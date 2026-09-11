from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from blueprint_ai.core import Finding, ToolStatus
from blueprint_ai.core.models import FileRange, Priority, Severity
from blueprint_ai.safety import MAX_TOOL_OUTPUT_BYTES, run_process, sanitize_label


class CommandResult:
    def __init__(
        self,
        command: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
        timed_out: bool = False,
        output_truncated: bool = False,
    ):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.output_truncated = output_truncated


def run_bounded_command(
    command: list[str], root: Path, timeout: float, output_limit: int = MAX_TOOL_OUTPUT_BYTES
) -> CommandResult:
    """Run without a shell while draining and bounding both output streams."""
    result = run_process(command, root, timeout, output_limit=output_limit)
    return CommandResult(
        command,
        result.returncode,
        result.stdout,
        result.stderr,
        result.timed_out,
        result.output_truncated,
    )


def _resolve_executable(executable: str) -> str | None:
    if os.sep in executable or (os.altsep and os.altsep in executable):
        path = Path(executable)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(executable)
    if found:
        return found
    sibling = Path(sys.executable).parent / executable
    return str(sibling) if sibling.is_file() and os.access(sibling, os.X_OK) else None


class ToolAdapter(ABC):
    name: str
    blueprint: str
    expected_codes: set[int]

    @abstractmethod
    def status(self) -> ToolStatus: ...

    @abstractmethod
    def command(self, root: Path) -> list[str]: ...

    @abstractmethod
    def parse(self, result: CommandResult, root: Path) -> list[Finding]: ...

    def run(self, root: Path, timeout: float = 120) -> tuple[ToolStatus, list[Finding], str | None]:
        status = self.status()
        if not status.available:
            if status.outcome != "unsupported":
                status.outcome = "tool_missing"
            return status, [], None
        command = self.command(root)
        status.command = command
        started = time.monotonic()
        try:
            result = run_bounded_command(
                command, root, timeout, getattr(self, "max_output_bytes", MAX_TOOL_OUTPUT_BYTES)
            )
            findings = [] if result.timed_out else self.parse(result, root)
            error = (
                f"timed out after {timeout}s"
                if result.timed_out
                else None
                if result.returncode in self.expected_codes
                else f"exit {result.returncode}"
            )
            status.exit_code = result.returncode
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.output_truncated = result.output_truncated
            status.outcome = "tool_error" if error else "finding" if findings else "passed"
            if error:
                findings = []
            return status, findings, error
        except subprocess.TimeoutExpired:
            result = CommandResult(command, 124, "", "", True)
            status.exit_code = 124
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            return status, self.parse(result, root), f"timed out after {timeout}s"
        except OSError as exc:
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            return status, [], str(exc)
        except Exception as exc:
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            return (
                status,
                [],
                (f"malformed tool output: {type(exc).__name__}: {sanitize_label(str(exc), 300)}"),
            )


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
        install: str | None = None,
        network_required: bool = False,
        recommended_version: str | None = None,
        executes_project_code: bool = False,
    ):
        self.name = name
        self.blueprint = blueprint
        self.args = args
        self.parser = parser
        self.expected_codes = expected_codes or {0, 1}
        self.executable = executable or name
        self.install = install or f"install {name} with its official package or release"
        self.network_required = network_required
        self.recommended_version = recommended_version
        self.disabled_reason: str | None = None
        self.executes_project_code = executes_project_code
        self.max_output_bytes = MAX_TOOL_OUTPUT_BYTES

    def status(self) -> ToolStatus:
        if self.disabled_reason:
            return ToolStatus(
                name=self.name,
                available=False,
                detail=self.disabled_reason,
                outcome="unsupported",
                network_required=self.network_required,
                recommended_version=self.recommended_version,
                requires_project_trust=self.executes_project_code,
            )
        path = _resolve_executable(self.executable)
        if not path:
            return ToolStatus(
                name=self.name,
                available=False,
                detail=self.install,
                outcome="tool_missing",
                network_required=self.network_required,
                recommended_version=self.recommended_version,
                requires_project_trust=self.executes_project_code,
            )
        self._resolved_executable = path
        version = None
        for flag in (["--version"], ["version"]):
            try:
                output = run_bounded_command([path, *flag], Path.cwd(), 3, 16_384)
                if output.returncode == 0:
                    version = (output.stdout or output.stderr).strip().splitlines()[0][:160]
                    break
            except (OSError, subprocess.TimeoutExpired, IndexError):
                pass
        return ToolStatus(
            name=self.name,
            available=True,
            version=version,
            network_required=self.network_required,
            recommended_version=self.recommended_version,
            requires_project_trust=self.executes_project_code,
        )

    def command(self, root: Path) -> list[str]:
        return [getattr(self, "_resolved_executable", self.executable), *self.args]

    def parse(self, result: CommandResult, root: Path) -> list[Finding]:
        return self.parser(result, root, self)


def _priority(severity: str) -> Priority:
    return cast(
        Priority,
        {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3", "info": "P3"}.get(
            severity, "P2"
        ),
    )


def finding(
    adapter: ExternalToolAdapter,
    *,
    root: Path | None = None,
    message: str,
    severity: str = "medium",
    file: str | None = None,
    line: int | None = None,
    evidence: list[str] | None = None,
    recommendation: str = "Review and address the reported issue.",
    category: str = "tool-finding",
    rule_id: str | None = None,
) -> Finding:
    normalized = severity.lower()
    if normalized not in {"critical", "high", "medium", "low", "info"}:
        normalized = "medium"
    normalized_file = None
    if file:
        candidate = Path(str(file))
        try:
            normalized_file = (
                candidate.resolve().relative_to(root.resolve()).as_posix()
                if root
                else candidate.as_posix()
            )
        except (OSError, ValueError):
            normalized_file = str(file).removeprefix("./")
    return Finding(
        blueprint=adapter.blueprint,
        category=category,
        rule_id=rule_id or f"{adapter.name}/{category}",
        source=adapter.name,
        sources=[adapter.name],
        severity=cast(Severity, normalized),
        priority=_priority(normalized),
        file=normalized_file,
        range=FileRange(start_line=line) if line else None,
        evidence=evidence or [],
        tool_metadata={"adapter": adapter.name, "original_severity": severity},
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
                        root=root,
                        category="lint",
                        rule_id=str(message_row.get("ruleId") or adapter.name),
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
                root=root,
                category="secret" if adapter.name == "gitleaks" else "tool-finding",
                rule_id=str(code or adapter.name),
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
                    adapter,
                    root=root,
                    file=match.group(1),
                    line=int(match.group(2)),
                    message=match.group(3),
                )
            )
    if not findings:
        findings.append(
            finding(
                adapter,
                root=root,
                message=output[:1000],
                severity="high" if result.timed_out else "medium",
            )
        )
    return findings


def parse_output_paths(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    """Parse tools whose successful stdout lists files needing formatting."""
    return [
        finding(
            adapter,
            root=root,
            category="format",
            rule_id=f"{adapter.name}/format",
            file=line.strip(),
            message=f"{line.strip()} is not formatted with {adapter.name}.",
            recommendation=f"Run {adapter.name} in write mode, then rerun the check.",
        )
        for line in result.stdout.splitlines()[:500]
        if line.strip()
    ]


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
                root=root,
                category="lint",
                rule_id=str(row.get("code") or "ruff"),
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
                        root=root,
                        category="dependency-vulnerability",
                        rule_id=vuln_id,
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


def parse_semgrep(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    levels = {"ERROR": "high", "WARNING": "medium", "INFO": "low", "INVENTORY": "info"}
    findings = []
    for row in data.get("results", []):
        extra = row.get("extra", {})
        findings.append(
            finding(
                adapter,
                root=root,
                category="sast",
                rule_id=str(row.get("check_id") or "semgrep"),
                severity=levels.get(str(extra.get("severity", "WARNING")).upper(), "medium"),
                file=row.get("path"),
                line=_int(row.get("start", {}).get("line")),
                message=f"{row.get('check_id', 'semgrep')}: {extra.get('message', '')}",
                recommendation=extra.get("metadata", {}).get("fix")
                or "Apply the rule guidance and add a regression test.",
            )
        )
    return findings


def parse_grype(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    findings = []
    for row in data.get("matches", []):
        vulnerability = row.get("vulnerability", {})
        artifact = row.get("artifact", {})
        identifier = str(vulnerability.get("id") or "vulnerability")
        findings.append(
            finding(
                adapter,
                root=root,
                category="dependency-vulnerability",
                rule_id=identifier,
                severity=str(vulnerability.get("severity", "medium")).lower(),
                message=(
                    f"{identifier} affects {artifact.get('name', 'dependency')} "
                    f"{artifact.get('version', '')}".rstrip()
                ),
                evidence=[
                    str(value) for value in vulnerability.get("fix", {}).get("versions", [])[:5]
                ],
                recommendation="Upgrade to a fixed version and rerun the dependency scan.",
            )
        )
    return findings


def parse_kubeconform(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    rows = data.get("resources", []) if isinstance(data, dict) else data
    findings = []
    for row in rows if isinstance(rows, list) else []:
        if row.get("status") in {"statusValid", "valid"}:
            continue
        findings.append(
            finding(
                adapter,
                root=root,
                category="schema-validation",
                rule_id="kubeconform/schema",
                severity="high",
                file=row.get("filename"),
                message=str(row.get("msg") or row.get("message") or "invalid Kubernetes resource"),
                recommendation="Correct the manifest against the selected Kubernetes/CRD schemas.",
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
                root=root,
                category="validation",
                rule_id=str(diagnostic.get("summary") or "terraform-validation"),
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
                        root=root,
                        category=category,
                        rule_id=str(identifier),
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
                    root=root,
                    category="misconfiguration",
                    rule_id=str(row.get("check_id") or "checkov"),
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


def parse_sarif(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return parse_lines(result, root, adapter)
    findings = []
    levels = {"error": "high", "warning": "medium", "note": "low", "none": "info"}
    for run in data.get("runs", []):
        for row in run.get("results", []):
            location = (row.get("locations") or [{}])[0].get("physicalLocation", {})
            artifact = location.get("artifactLocation", {}).get("uri")
            region = location.get("region", {})
            message = row.get("message", {})
            findings.append(
                finding(
                    adapter,
                    root=root,
                    category="sarif",
                    rule_id=str(row.get("ruleId") or adapter.name),
                    severity=levels.get(str(row.get("level", "warning")).lower(), "medium"),
                    file=artifact,
                    line=_int(region.get("startLine")),
                    message=str(
                        message.get("text") or message.get("markdown") or row.get("ruleId")
                    ),
                )
            )
    return findings


def parse_junit(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    payload = result.stdout.strip()
    if not payload.startswith("<"):
        return parse_lines(result, root, adapter)
    try:
        document = ET.fromstring(payload)
    except ET.ParseError:
        return parse_lines(result, root, adapter)
    findings = []
    for case in document.iter("testcase"):
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is None:
            continue
        test_id = ".".join(filter(None, [case.get("classname"), case.get("name")]))
        findings.append(
            finding(
                adapter,
                root=root,
                category="test-failure",
                rule_id=test_id or "test-failure",
                severity="high",
                file=case.get("file"),
                line=_int(case.get("line")),
                message=(
                    f"{test_id or 'test'}: {failure.get('message') or (failure.text or '').strip()}"
                ),
                recommendation=(
                    "Fix the product failure, or repair the test only if the test is incorrect."
                ),
            )
        )
    return findings
