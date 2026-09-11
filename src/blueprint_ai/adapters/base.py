from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from blueprint_ai.core import Finding, ToolStatus
from blueprint_ai.core.models import FileRange


class CommandResult:
    def __init__(
        self, command: list[str], returncode: int, stdout: str, stderr: str, timed_out: bool = False
    ):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


class ToolAdapter(ABC):
    name: str
    blueprint: str

    @abstractmethod
    def status(self) -> ToolStatus: ...

    @abstractmethod
    def command(self, root: Path) -> list[str]: ...

    @abstractmethod
    def parse(self, result: CommandResult, root: Path) -> list[Finding]: ...

    def run(self, root: Path, timeout: int = 120) -> tuple[ToolStatus, list[Finding], str | None]:
        status = self.status()
        if not status.available:
            return status, [], None
        command = self.command(root)
        env = {**os.environ, "NO_COLOR": "1", "CI": "1"}
        try:
            process = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=env,
            )
            result = CommandResult(command, process.returncode, process.stdout, process.stderr)
            findings = self.parse(result, root)
            error = (
                None if process.returncode in self.expected_codes else f"exit {process.returncode}"
            )
            return status, findings, error
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(command, 124, exc.stdout or "", exc.stderr or "", True)
            return status, self.parse(result, root), f"timed out after {timeout}s"
        except OSError as exc:
            return status, [], str(exc)


Parser = Callable[[CommandResult, Path, "ExternalToolAdapter"], list[Finding]]


class ExternalToolAdapter(ToolAdapter):
    def __init__(
        self,
        name: str,
        blueprint: str,
        args: list[str],
        parser: Parser,
        *,
        expected_codes: set[int] | None = None,
        executable: str | None = None,
    ):
        self.name = name
        self.blueprint = blueprint
        self.args = args
        self.parser = parser
        self.expected_codes = expected_codes or {0, 1}
        self.executable = executable or name

    def status(self) -> ToolStatus:
        path = shutil.which(self.executable)
        if not path:
            return ToolStatus(
                name=self.name, available=False, detail=f"install {self.name} to enable this check"
            )
        version = None
        for flag in (["--version"], ["version"]):
            try:
                output = subprocess.run(
                    [path, *flag],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                if output.returncode == 0:
                    version = (output.stdout or output.stderr).strip().splitlines()[0][:160]
                    break
            except (OSError, subprocess.TimeoutExpired, IndexError):
                pass
        return ToolStatus(name=self.name, available=True, version=version)

    def command(self, root: Path) -> list[str]:
        return [self.executable, *self.args]

    def parse(self, result: CommandResult, root: Path) -> list[Finding]:
        return self.parser(result, root, self)


def _priority(severity: str) -> str:
    return {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3", "info": "P3"}.get(
        severity, "P2"
    )


def finding(
    adapter: ExternalToolAdapter,
    *,
    message: str,
    severity: str = "medium",
    file: str | None = None,
    line: int | None = None,
    evidence: list[str] | None = None,
    recommendation: str = "Review and address the reported issue.",
    category: str = "tool-finding",
) -> Finding:
    normalized = severity.lower()
    if normalized not in {"critical", "high", "medium", "low", "info"}:
        normalized = "medium"
    return Finding(
        blueprint=adapter.blueprint,
        category=category,
        source=adapter.name,
        severity=normalized,
        priority=_priority(normalized),
        file=file,
        range=FileRange(start_line=line) if line else None,
        evidence=evidence or [],
        message=message[:1000],
        recommendation=recommendation,
        verification=f"rerun {adapter.name}",
    )


def parse_json_list(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return parse_lines(result, root, adapter)
    if isinstance(rows, dict):
        rows = rows.get("results", rows.get("Results", []))
    findings = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("messages"), list):
            for message_row in row["messages"]:
                findings.append(
                    finding(
                        adapter,
                        category="lint",
                        message=(
                            f"{message_row.get('ruleId') or adapter.name}: "
                            f"{message_row.get('message', '')}"
                        ),
                        severity="high" if message_row.get("severity") == 2 else "medium",
                        file=row.get("filePath"),
                        line=_int(message_row.get("line")),
                        recommendation="Fix the lint violation.",
                    )
                )
            continue
        location = row.get("location", {}) or {}
        code = row.get("code", row.get("check_id", row.get("RuleID", "")))
        message = row.get("message", row.get("Description", row.get("Match", str(code))))
        filename = row.get("filename", row.get("path", row.get("File", location.get("path"))))
        line = row.get("line", row.get("StartLine", location.get("row")))
        severity = row.get("severity", "high" if adapter.blueprint == "security" else "medium")
        findings.append(
            finding(
                adapter,
                category="secret" if adapter.name == "gitleaks" else "tool-finding",
                message=f"{code}: {message}".strip(": "),
                severity=str(severity),
                file=filename,
                line=_int(line),
            )
        )
    if not findings and result.returncode != 0 and result.stdout.strip():
        return parse_lines(result, root, adapter)
    return findings


def parse_json_lines(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    rows = []
    try:
        for line in result.stdout.splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except ValueError:
        return parse_lines(result, root, adapter)
    synthetic = CommandResult(result.command, result.returncode, json.dumps(rows), result.stderr)
    return parse_json_list(synthetic, root, adapter)


def parse_lines(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    output = (result.stdout + "\n" + result.stderr).strip()
    if not output or (result.returncode == 0 and not result.timed_out):
        return []
    findings = []
    pattern = re.compile(r"^(.*?):(\d+)(?::\d+)?:\s*(.*)$")
    for line in output.splitlines()[:200]:
        match = pattern.match(line.strip())
        if match:
            findings.append(
                finding(
                    adapter, file=match.group(1), line=int(match.group(2)), message=match.group(3)
                )
            )
    if not findings:
        findings.append(
            finding(
                adapter, message=output[:1000], severity="high" if result.timed_out else "medium"
            )
        )
    return findings


def parse_ruff(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return parse_lines(result, root, adapter)
    findings = []
    for row in rows:
        location = row.get("location", {})
        findings.append(
            finding(
                adapter,
                category="lint",
                file=row.get("filename"),
                line=_int(location.get("row")),
                message=f"{row.get('code', 'ruff')}: {row.get('message', '')}",
                recommendation=(row.get("fix") or {}).get("message", "Fix the lint violation."),
            )
        )
    return findings


def parse_osv(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    findings = []
    for result_row in data.get("results", []):
        source = result_row.get("source", {})
        for package in result_row.get("packages", []):
            package_info = package.get("package", {})
            for vulnerability in package.get("vulnerabilities", []):
                vuln_id = vulnerability.get("id", "vulnerability")
                findings.append(
                    finding(
                        adapter,
                        category="dependency-vulnerability",
                        severity="high",
                        file=source.get("path"),
                        message=f"{vuln_id} affects {package_info.get('name', 'dependency')}",
                        evidence=[alias for alias in vulnerability.get("aliases", [])[:5]],
                        recommendation=(
                            "Upgrade to a non-vulnerable version described by the advisory."
                        ),
                    )
                )
    return findings


def parse_terraform(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    findings = []
    for diagnostic in data.get("diagnostics", []):
        source = diagnostic.get("range", {})
        start = source.get("start", {})
        findings.append(
            finding(
                adapter,
                category="validation",
                severity="high" if diagnostic.get("severity") == "error" else "medium",
                file=source.get("filename"),
                line=_int(start.get("line")),
                message=(
                    f"{diagnostic.get('summary', 'Terraform validation')}: "
                    f"{diagnostic.get('detail', '')}"
                ),
                recommendation="Correct the Terraform configuration and validate again.",
            )
        )
    return findings


def parse_trivy(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    findings = []
    severity_map = {
        "UNKNOWN": "medium",
        "LOW": "low",
        "MEDIUM": "medium",
        "HIGH": "high",
        "CRITICAL": "critical",
    }
    for scan in data.get("Results", []):
        target = scan.get("Target")
        groups = (
            ("Vulnerabilities", "dependency-vulnerability"),
            ("Misconfigurations", "misconfiguration"),
            ("Secrets", "secret"),
        )
        for key, category in groups:
            for row in scan.get(key, []) or []:
                identifier = row.get("VulnerabilityID", row.get("ID", row.get("RuleID", "trivy")))
                message = row.get("Title", row.get("Message", row.get("Description", identifier)))
                findings.append(
                    finding(
                        adapter,
                        category=category,
                        severity=severity_map.get(
                            str(row.get("Severity", "MEDIUM")).upper(), "medium"
                        ),
                        file=target,
                        line=_int(row.get("StartLine")),
                        message=f"{identifier}: {message}",
                        recommendation=row.get(
                            "Resolution", "Review the Trivy guidance and remediate."
                        ),
                    )
                )
    return findings


def parse_checkov(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        documents = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    if not isinstance(documents, list):
        documents = [documents]
    findings = []
    for document in documents:
        for row in document.get("results", {}).get("failed_checks", []):
            location = row.get("file_line_range") or [None]
            findings.append(
                finding(
                    adapter,
                    category="misconfiguration",
                    severity="high",
                    file=str(row.get("file_path", "")).lstrip("/"),
                    line=_int(location[0] if location else None),
                    message=f"{row.get('check_id', 'checkov')}: {row.get('check_name', '')}",
                    recommendation=row.get("guideline") or "Apply the referenced policy guidance.",
                )
            )
    return findings


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
