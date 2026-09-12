from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from blueprint_ai.core import Finding, ToolStatus
from blueprint_ai.core.models import FileRange, Priority, Severity
from blueprint_ai.safety import MAX_TOOL_OUTPUT_BYTES, controlled_env, run_process, sanitize_label
from blueprint_ai.sandbox import SandboxPolicy, SandboxUnavailable, execute, resolve_backend
from blueprint_ai.support import TOOLS
from blueprint_ai.tooling import cached_image

_TRIVY_LOCK = threading.Lock()
_TRIVY_CACHE: tempfile.TemporaryDirectory[str] | None = None


class IncompleteToolOutput(ValueError):
    """A native report explicitly says some requested analysis did not run."""


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
    if Path(command[0]).name == "trivy":
        # Reuse the database within a run without repeated downloads or concurrent DB writers.
        # Created lazily so importing providers/planning never writes to the filesystem.
        global _TRIVY_CACHE
        with _TRIVY_LOCK:
            if _TRIVY_CACHE is None:
                _TRIVY_CACHE = tempfile.TemporaryDirectory(prefix="blueprint-trivy-")
            return _run_bounded_command(command, root, timeout, output_limit, _TRIVY_CACHE.name)
    return _run_bounded_command(command, root, timeout, output_limit)


def _run_bounded_command(
    command: list[str],
    root: Path,
    timeout: float,
    output_limit: int,
    trivy_cache: str | None = None,
) -> CommandResult:
    with tempfile.TemporaryDirectory(prefix="blueprint-tool-") as temporary:
        cache = Path(temporary)
        result = run_process(
            command,
            root,
            timeout,
            output_limit=output_limit,
            env=controlled_env(
                {
                    "HOME": str(cache),
                    "XDG_CACHE_HOME": str(cache),
                    "XDG_CONFIG_HOME": str(cache),
                    "RUFF_CACHE_DIR": str(cache / "ruff"),
                    "MYPY_CACHE_DIR": str(cache / "mypy"),
                    "GOCACHE": str(cache / "go-build"),
                    "GOMODCACHE": str(cache / "go-mod"),
                    "CARGO_TARGET_DIR": str(cache / "cargo"),
                    "TRIVY_CACHE_DIR": trivy_cache or str(cache / "trivy"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "-p no:cacheprovider",
                    "GOTOOLCHAIN": "local",
                    "CHECKPOINT_DISABLE": "1",
                }
            ),
        )
    return CommandResult(
        command,
        result.returncode,
        result.stdout,
        result.stderr,
        result.timed_out,
        result.output_truncated,
    )


def _failure_detail(result: CommandResult) -> str:
    output = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
    detail = f"exit {result.returncode}"
    return f"{detail}: {sanitize_label(output, 500)}" if output else detail


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
        repository_root = root
        root = root / getattr(self, "working_directory", ".")
        policy = getattr(self, "sandbox", SandboxPolicy())
        sandbox_command = None
        try:
            backend = resolve_backend(policy)
            if backend == "host" or getattr(self, "disabled_reason", None):
                status = self.status()
            else:
                tool_id = self.name.split(":", 1)[0]
                image = policy.image or cached_image(tool_id, backend)
                if not image:
                    raise SandboxUnavailable(
                        f"{tool_id}: sandbox image unavailable; see tools plan {tool_id}"
                    )
                spec = TOOLS.get(tool_id)
                executable = getattr(self, "executable", self.name)
                if Path(executable).is_absolute():
                    try:
                        executable = (
                            "/workspace/" + Path(executable).relative_to(repository_root).as_posix()
                        )
                    except ValueError:
                        executable = Path(executable).name
                if not policy.image and spec and spec.container_executable:
                    executable = spec.container_executable
                sandbox_command = [executable, *getattr(self, "args", [])]
                policy = SandboxPolicy.model_validate(
                    {**policy.model_dump(), "backend": backend, "image": image}
                )
                status = ToolStatus(
                    name=self.name,
                    available=True,
                    network_required=getattr(self, "network_required", False),
                    requires_project_trust=getattr(self, "executes_project_code", False),
                    recommended_version=getattr(self, "recommended_version", None),
                )
        except (SandboxUnavailable, OSError) as exc:
            status = ToolStatus(
                name=self.name,
                available=False,
                outcome="unsupported",
                analysis_state="sandbox_unavailable",
                detail=str(exc),
                sandbox={
                    "backend": policy.backend,
                    "policy": policy.model_dump(),
                    "isolated": False,
                    "executed": False,
                },
            )
        status.working_directory = str(root)
        spec = TOOLS.get(self.name.split(":", 1)[0])
        if spec:
            status.provenance = spec.model_dump(mode="json")
        if not status.available:
            if status.outcome != "unsupported":
                status.outcome = "tool_missing"
            return status, [], None
        command = sandbox_command or self.command(root)
        status.command = command
        started = time.monotonic()
        try:
            if sandbox_command:
                policy = SandboxPolicy.model_validate(
                    {
                        **policy.model_dump(),
                        "timeout": timeout,
                        "output_bytes": getattr(self, "max_output_bytes", MAX_TOOL_OUTPUT_BYTES),
                    }
                )
                execution = execute(
                    command,
                    repository_root,
                    policy,
                    cwd=getattr(self, "working_directory", "."),
                    tool=self.name,
                )
                output = execution.result
                status.sandbox = execution.evidence.model_dump(mode="json")
                status.version = execution.evidence.image_id
                result = CommandResult(
                    command,
                    output.returncode,
                    output.stdout.replace("/workspace/", str(repository_root) + "/"),
                    output.stderr.replace("/workspace/", str(repository_root) + "/"),
                    output.timed_out,
                    output.output_truncated,
                )
            else:
                result = run_bounded_command(
                    command, root, timeout, getattr(self, "max_output_bytes", MAX_TOOL_OUTPUT_BYTES)
                )
                status.sandbox = {
                    "backend": "host",
                    "isolated": False,
                    "policy": policy.model_dump(),
                    "detail": "explicitly trusted host; no filesystem/network/resource isolation",
                }
            status.exit_code = result.returncode
            status.output_truncated = result.output_truncated
            if result.output_truncated:
                status.outcome = "tool_error"
                status.analysis_state = "truncated"
                status.detail = "tool output exceeded capture limit; analysis is incomplete"
                status.duration_ms = round((time.monotonic() - started) * 1000)
                return status, [], status.detail
            self.output_evidence: dict[str, Any] = {}
            findings = [] if result.timed_out else self.parse(result, root)
            status.output_evidence = getattr(self, "output_evidence", {})
            if root != repository_root:
                for item in findings:
                    if item.file:
                        item.file = (root.relative_to(repository_root) / item.file).as_posix()
            incomplete = next(
                (
                    item
                    for item in findings
                    if item.tool_metadata.get("incomplete_analysis") is True
                ),
                None,
            )
            error = (
                f"timed out after {timeout}s"
                if result.timed_out
                else f"analysis prerequisite missing: {incomplete.message}"
                if incomplete
                else "nonzero exit without structured diagnostics"
                if result.returncode != 0 and not findings
                else None
                if result.returncode in self.expected_codes
                else _failure_detail(result)
            )
            status.exit_code = result.returncode
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.output_truncated = result.output_truncated
            status.outcome = "tool_error" if error else "finding" if findings else "passed"
            status.analysis_state = (
                "prerequisite_failed" if incomplete else "failed" if error else "complete"
            )
            if error:
                status.detail = error
                findings = []
            return status, findings, error
        except subprocess.TimeoutExpired:
            result = CommandResult(command, 124, "", "", True)
            status.exit_code = 124
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            status.detail = f"timed out after {timeout}s"
            return status, self.parse(result, root), f"timed out after {timeout}s"
        except (OSError, SandboxUnavailable) as exc:
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            status.detail = sanitize_label(str(exc), 500)
            return status, [], status.detail
        except IncompleteToolOutput as exc:
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            status.analysis_state = "incomplete"
            status.detail = sanitize_label(str(exc), 500)
            return status, [], status.detail
        except Exception as exc:
            status.duration_ms = round((time.monotonic() - started) * 1000)
            status.outcome = "tool_error"
            status.analysis_state = "malformed"
            status.detail = (
                f"malformed tool output: {type(exc).__name__}: {sanitize_label(str(exc), 300)}"
            )
            return status, [], status.detail


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
        default_timeout: float | None = None,
    ):
        self.output_evidence: dict[str, Any] = {}
        self.sandbox = SandboxPolicy()
        self.working_directory = "."
        self.name = name
        self.blueprint = blueprint
        self.args = args
        self.parser = parser
        self.expected_codes = expected_codes or {0, 1}
        self.executable = executable or name
        spec = TOOLS.get(name.split(":", 1)[0])
        self.install = install or (
            (f"blueprint-ai tools plan {spec.id}; " if spec.image else "")
            + spec.acquisition
            + "; "
            + spec.source
            if spec
            else f"install {name} with its official package or release"
        )
        self.network_required = network_required
        self.recommended_version = recommended_version or (spec.versions if spec else None)
        self.disabled_reason: str | None = None
        self.executes_project_code = executes_project_code
        self.default_timeout = default_timeout
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
                output = run_bounded_command([path, *flag], Path(tempfile.gettempdir()), 3, 16_384)
                if output.returncode == 0:
                    lines = (output.stdout or output.stderr).strip().splitlines()
                    version = next(
                        (line for line in lines if re.search(r"\d+\.\d+", line)), lines[0]
                    )[:160]
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
    normalized = {"error": "high", "warning": "medium", "note": "low", "style": "low"}.get(
        severity.lower(), severity.lower()
    )
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


def _object_report(result: CommandResult, key: str, *, nullable: bool = False) -> dict:
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or key not in data:
        raise ValueError(f"missing {key} in structured tool output")
    value = data[key]
    if not isinstance(value, list) and not (nullable and value is None):
        raise ValueError(f"expected {key} diagnostic array")
    if value is not None and any(not isinstance(row, dict) for row in value):
        raise ValueError(f"invalid {key} diagnostic")
    return data


def _report_errors(data: dict, key: str, tool: str) -> None:
    errors = data.get(key)
    if errors:
        raise IncompleteToolOutput(f"{tool} reported incomplete analysis ({key}): {errors!s}")


def parse_json_list(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    try:
        rows = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if isinstance(rows, dict):
        if not ("results" in rows or "Results" in rows):
            raise ValueError("unrecognized structured tool output")
        rows = rows.get("results", rows.get("Results"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("expected a list of structured diagnostics")
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
        filename = row.get(
            "filename",
            row.get(
                "filepath",
                row.get("file", row.get("path", row.get("File", location.get("path")))),
            ),
        )
        line = row.get("line", row.get("StartLine", location.get("row")))
        severity = row.get(
            "severity",
            row.get("level", "high" if adapter.blueprint == "security" else "medium"),
        )
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
    if adapter.name == "gitleaks":
        specific_locations = {
            (item.file, item.range.start_line if item.range else None)
            for item in findings
            if item.rule_id != "generic-api-key"
        }
        findings = [
            item
            for item in findings
            if item.rule_id != "generic-api-key"
            or (item.file, item.range.start_line if item.range else None) not in specific_locations
        ]
    return findings


def parse_cfn_lint(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("CloudFormation lint must emit a diagnostic array")
    findings = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("Message"), str):
            raise ValueError("invalid CloudFormation lint diagnostic")
        rule = row.get("Rule") or {}
        start = (row.get("Location") or {}).get("Start") or {}
        findings.append(
            finding(
                adapter,
                root=root,
                category="schema-validation",
                rule_id=str(rule.get("Id") or "cfn-lint"),
                message=row["Message"],
                severity=str(row.get("Level") or "warning"),
                file=row.get("Filename"),
                line=_int(start.get("LineNumber")),
                recommendation=rule.get("Source") or "Correct the CloudFormation template.",
            )
        )
    return findings


def parse_spectral(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("Spectral must emit a diagnostic array")
    findings = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("message"), str):
            raise ValueError("invalid Spectral diagnostic")
        start = (row.get("range") or {}).get("start") or {}
        line = _int(start.get("line"))
        item = finding(
            adapter,
            root=root,
            category="contract-lint",
            rule_id=str(row.get("code") or "spectral"),
            message=row["message"],
            file=row.get("source"),
            severity={"0": "high", "1": "medium", "2": "low", "3": "info"}.get(
                str(row.get("severity")), "medium"
            ),
            line=line + 1 if line is not None else None,
            recommendation=row.get("documentationUrl") or "Correct the API contract violation.",
        )
        item.tool_metadata["document_path"] = json.dumps(row.get("path") or [])
        findings.append(item)
    return findings


def parse_tflint(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = _object_report(result, "issues")
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    severity_map = {"error": "high", "warning": "medium", "notice": "low"}
    _report_errors(data, "errors", "tflint")
    findings = []
    for row in data.get("issues", []) if isinstance(data, dict) else []:
        rule = row.get("rule", {}) or {}
        source = row.get("range", {}) or {}
        start = source.get("start", {}) or {}
        rule_name = str(rule.get("name") or "tflint")
        item = finding(
            adapter,
            root=root,
            category="lint",
            rule_id=f"tflint/{rule_name}",
            severity=severity_map.get(str(rule.get("severity", "warning")).lower(), "medium"),
            file=source.get("filename"),
            line=_int(start.get("line")),
            message=f"{rule_name}: {row.get('message', '')}",
            recommendation="Correct the Terraform lint violation and rerun TFLint.",
        )
        if rule.get("link"):
            item.evidence.append(str(rule["link"]))
        findings.append(item)
    return findings


def parse_shellcheck(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    findings = parse_json_list(result, root, adapter)
    grouped: dict[tuple[str | None, str | None], Finding] = {}
    for item in findings:
        key = (item.file, item.rule_id)
        if existing := grouped.get(key):
            occurrences = int(existing.tool_metadata.get("occurrences") or 1) + 1
            existing.tool_metadata["occurrences"] = occurrences
            line = item.range.start_line if item.range else None
            if line and len(existing.evidence) < 20:
                existing.evidence.append(f"also at line {line}")
            continue
        item.tool_metadata["occurrences"] = 1
        grouped[key] = item
    return list(grouped.values())


def parse_markdownlint(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    output = (result.stdout + "\n" + result.stderr).strip()
    if not output or (result.returncode == 0 and not result.timed_out):
        return []
    pattern = re.compile(
        r"^(.*?):(\d+)(?::\d+)?\s+\w+\s+(MD\d+)(?:/\S+)?\s+(.*)$",
        re.IGNORECASE,
    )
    grouped: dict[tuple[str, str], Finding] = {}
    for raw_line in output.splitlines()[:2_000]:
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        filename, line_text, rule_id, message = match.groups()
        line = int(line_text)
        key = (filename, rule_id.upper())
        if existing := grouped.get(key):
            occurrences = int(existing.tool_metadata.get("occurrences") or 1) + 1
            existing.tool_metadata["occurrences"] = occurrences
            if len(existing.evidence) < 20:
                existing.evidence.append(f"also at line {line}")
            continue
        item = finding(
            adapter,
            root=root,
            category="documentation-style",
            rule_id=f"markdownlint/{rule_id.upper()}",
            severity="low",
            file=filename,
            line=line,
            message=f"{rule_id.upper()}: {message}",
            recommendation=(
                "Fix the Markdown convention or configure the rule when the repository "
                "intentionally uses a different style."
            ),
        )
        item.tool_metadata["occurrences"] = 1
        grouped[key] = item
    return list(grouped.values()) or parse_lines(result, root, adapter)


def parse_json_lines(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    rows = []
    try:
        for line in result.stdout.splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    synthetic = CommandResult(result.command, result.returncode, json.dumps(rows), result.stderr)
    return parse_json_list(synthetic, root, adapter)


def parse_lines(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    output = (result.stdout + "\n" + result.stderr).strip()
    if not output or (result.returncode == 0 and not result.timed_out):
        return []
    tool_id = adapter.name.split(":", 1)[0]
    lowered = output.lower()
    if tool_id in {"cargo-fmt", "cargo-clippy", "cargo-test"} and (
        (
            ("rustup" in lowered or "syncing channel updates" in lowered)
            and any(
                marker in lowered
                for marker in (
                    "read-only file system",
                    "could not download",
                    "failed to download",
                    "not installed",
                    "no release found",
                    "no default toolchain",
                )
            )
        )
        or re.search(r"(?:toolchain|component) .+ is not installed", lowered)
        or "no such command: `clippy`" in lowered
        or "no such command: `fmt`" in lowered
    ):
        raise IncompleteToolOutput("Rust toolchain prerequisite unavailable: " + output)
    if (
        tool_id in {"cargo-clippy", "cargo-test"}
        and "offline mode" in lowered
        and "--offline" in lowered
        and re.search(r"^error: no matching package named .+ found$", lowered, re.MULTILINE)
    ):
        raise IncompleteToolOutput("Rust dependency prerequisite unavailable offline: " + output)
    if tool_id in {"go-vet", "go-test"} and re.search(
        r"requires go >= .+running go.+gotoolchain=local", lowered
    ):
        raise IncompleteToolOutput("Go toolchain prerequisite unavailable: " + output)
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
        rows = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Ruff must emit a JSON diagnostic array")
    findings = []
    for row in rows:
        if not isinstance(row.get("message"), str) or not isinstance(row.get("location"), dict):
            raise ValueError("invalid Ruff diagnostic")
        location = row["location"]
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
        data = _object_report(result, "results")
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    findings = []
    for result_row in data.get("results", []):
        source = result_row.get("source", {})
        for package in result_row.get("packages", []):
            package_info = package.get("package", {})
            for vulnerability in package.get("vulnerabilities", []):
                vuln_id = str(vulnerability.get("id", "vulnerability"))
                aliases = sorted({vuln_id, *map(str, vulnerability.get("aliases", []))})
                canonical_id = next(
                    (alias for alias in aliases if alias.startswith("CVE-")), aliases[0]
                )
                fixed = sorted(
                    {
                        str(event["fixed"])
                        for affected in vulnerability.get("affected", [])
                        for range_row in affected.get("ranges", [])
                        for event in range_row.get("events", [])
                        if isinstance(event, dict) and event.get("fixed")
                    }
                )
                database_severity = str(
                    vulnerability.get("database_specific", {}).get("severity", "medium")
                ).lower()
                if database_severity == "moderate":
                    database_severity = "medium"
                elif database_severity not in {"critical", "high", "medium", "low", "info"}:
                    database_severity = "medium"
                installed = package_info.get("version")
                name = package_info.get("name", "dependency")
                evidence = [vuln_id]
                evidence.extend(str(alias) for alias in vulnerability.get("aliases", [])[:5])
                if summary := vulnerability.get("summary"):
                    evidence.append(str(summary))
                if installed:
                    evidence.append(f"installed version: {installed}")
                item = finding(
                    adapter,
                    root=root,
                    category="dependency-vulnerability",
                    rule_id=canonical_id,
                    severity=database_severity,
                    file=source.get("path"),
                    message=f"{vuln_id} affects {name}{f' {installed}' if installed else ''}",
                    evidence=evidence,
                    recommendation=(
                        f"Upgrade to {', '.join(fixed[:5])} or another non-vulnerable version."
                        if fixed
                        else "Upgrade to a non-vulnerable version described by the advisory."
                    ),
                )
                item.advisory_aliases = aliases
                item.tool_metadata.update(
                    {
                        "package": str(name),
                        "installed_version": str(installed) if installed else None,
                        "ecosystem": str(package_info.get("ecosystem") or "") or None,
                        "advisory_id": vuln_id,
                    }
                )
                findings.append(item)
    return findings


def parse_semgrep(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = _object_report(result, "results")
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    levels = {"ERROR": "high", "WARNING": "medium", "INFO": "low", "INVENTORY": "info"}
    _report_errors(data, "errors", "semgrep")
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
        data = _object_report(result, "matches")
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
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
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if isinstance(data, dict):
        data = _object_report(result, "resources", nullable=True)
        if (data.get("summary") or {}).get("skipped", 0):
            raise IncompleteToolOutput(
                "kubeconform skipped resources; schema coverage is incomplete"
            )
        rows = data["resources"] or []
    else:
        rows = data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid kubeconform resource report")
    findings = []
    for row in rows if isinstance(rows, list) else []:
        if row.get("status") in {"statusError", "error", "statusSkipped", "skipped"}:
            raise IncompleteToolOutput("kubeconform could not validate a resource")
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


def parse_kube_linter(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    data = _object_report(result, "Reports", nullable=True)
    summary = data.get("Summary") or {}
    if summary.get("ChecksStatus") not in {"Passed", "Failed"}:
        raise ValueError("invalid KubeLinter summary")
    if summary["ChecksStatus"] == "Failed" and not data["Reports"]:
        raise IncompleteToolOutput("KubeLinter failed without diagnostics")
    findings = []
    for row in data["Reports"] or []:
        diagnostic = row.get("Diagnostic") or {}
        if not isinstance(row.get("Check"), str) or not isinstance(diagnostic.get("Message"), str):
            raise ValueError("invalid KubeLinter diagnostic")
        metadata = (row.get("Object") or {}).get("Metadata") or {}
        findings.append(
            finding(
                adapter,
                root=root,
                category="workload-policy",
                rule_id="kube-linter/" + row["Check"],
                message=diagnostic["Message"],
                file=metadata.get("FilePath"),
                recommendation=row.get("Remediation") or "Correct the Kubernetes workload policy.",
            )
        )
    return findings


def parse_terraform(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    try:
        data = _object_report(result, "diagnostics")
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if not isinstance(data.get("valid"), bool):
        raise ValueError("Terraform validation report must declare validity")
    if data["valid"] is False and not data["diagnostics"]:
        raise IncompleteToolOutput("Terraform validation failed without diagnostics")
    findings = []
    for diagnostic in data.get("diagnostics", []):
        source = diagnostic.get("range", {})
        start = source.get("start", {})
        item = finding(
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
        summary = str(diagnostic.get("summary") or "")
        detail = str(diagnostic.get("detail") or "")
        if summary in {"Module not installed", "Missing required provider"} or (
            "cached in .terraform/providers" in detail
        ):
            item.tool_metadata["incomplete_analysis"] = True
        findings.append(item)
    return findings


def parse_trivy(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid Trivy report")
    if "Results" not in data:
        if not isinstance(data.get("SchemaVersion"), int) or "ArtifactName" not in data:
            raise ValueError("unrecognized Trivy report envelope")
        data["Results"] = []
    if not isinstance(data["Results"], list):
        raise ValueError("invalid Trivy result array")
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
                cause = row.get("CauseMetadata", {}) or {}
                installed = row.get("InstalledVersion")
                fixed = row.get("FixedVersion")
                evidence = []
                if installed:
                    evidence.append(f"installed version: {installed}")
                if fixed:
                    evidence.append(f"fixed version: {fixed}")
                if primary_url := row.get("PrimaryURL"):
                    evidence.append(str(primary_url))
                item = finding(
                    adapter,
                    root=root,
                    category=category,
                    rule_id=str(identifier),
                    severity=severity_map.get(str(row.get("Severity", "MEDIUM")).upper(), "medium"),
                    file=target,
                    line=_int(row.get("StartLine", cause.get("StartLine"))),
                    message=f"{identifier}: {message}",
                    evidence=evidence,
                    recommendation=row.get(
                        "Resolution",
                        f"Upgrade to {fixed}."
                        if fixed
                        else "Review the Trivy guidance and remediate.",
                    ),
                )
                item.tool_metadata.update(
                    {
                        "package": str(row.get("PkgName") or ""),
                        "ecosystem": str(scan.get("Type") or ""),
                        "installed_version": str(installed) if installed else None,
                        "fixed_version": str(fixed) if fixed else None,
                    }
                )
                findings.append(item)
    return findings


def parse_actionlint(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    try:
        decoded = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    rows = [item for batch in decoded for item in (batch if isinstance(batch, list) else [batch])]
    findings = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("message"), str):
            raise ValueError("invalid actionlint diagnostic")
        message = row["message"]
        injection = "potentially untrusted" in message.lower()
        item = finding(
            adapter,
            root=root,
            category="workflow-security" if injection else "workflow-validation",
            rule_id=(
                "github-actions/template-injection"
                if injection
                else f"actionlint/{row.get('kind') or 'validation'}"
            ),
            severity="high" if injection else "medium",
            file=row.get("filepath"),
            line=_int(row.get("line")),
            message=message,
            evidence=[str(row["snippet"])] if row.get("snippet") else [],
            recommendation=(
                "Move attacker-controlled expressions into an environment variable before "
                "using them in a script."
                if injection
                else "Correct the workflow syntax or expression and rerun actionlint."
            ),
        )
        item.tool_metadata["kind"] = str(row.get("kind") or "") or None
        findings.append(item)
    return findings


def parse_lychee(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if not isinstance(data, dict) or not isinstance(data.get("error_map"), dict):
        raise ValueError("invalid lychee report envelope")
    findings: dict[tuple[str, str, str], Finding] = {}
    for filename, rows in (data.get("error_map", {}) or {}).items():
        for row in rows if isinstance(rows, list) else []:
            status = row.get("status", {}) or {}
            detail = status.get("text") or status.get("details") or "link check failed"
            code = _int(status.get("code"))
            lowered = str(detail).lower()
            inconclusive = code in {401, 403, 429} or any(
                marker in lowered
                for marker in ("cached", "timed out", "timeout", "connection", "network", "dns")
            )
            category = "link-check-inconclusive" if inconclusive else "broken-link"
            url = str(row.get("url", "unknown"))
            line = _int((row.get("span") or {}).get("line"))
            key = (str(filename), url, category)
            if existing := findings.get(key):
                occurrences = int(existing.tool_metadata.get("occurrences") or 1) + 1
                existing.tool_metadata["occurrences"] = occurrences
                if line:
                    existing.evidence = sorted(set(existing.evidence + [f"also at line {line}"]))
                continue
            item = finding(
                adapter,
                root=root,
                category=category,
                rule_id=f"lychee/{category}",
                severity="info" if inconclusive else "low",
                file=filename,
                line=line,
                message=(
                    f"Link could not be verified: {url} ({detail})"
                    if inconclusive
                    else f"Broken link: {url} ({detail})"
                ),
                evidence=[str(status.get("details"))] if status.get("details") else [],
                recommendation=(
                    "Verify the link manually; the remote service may block automated clients."
                    if inconclusive
                    else "Correct or remove the link, then rerun lychee."
                ),
            )
            item.confidence = 0.5 if inconclusive else 1.0
            item.tool_metadata["http_status"] = code
            item.tool_metadata["occurrences"] = 1
            findings[key] = item
    return list(findings.values())


def _checkov_security_group_reference(code_block: list) -> bool:
    """Confirm the known upstream false positive from HCL syntax, never raw comments."""
    import hcl2

    try:
        source = "\n".join(row[1] for row in code_block if isinstance(row, list) and len(row) == 2)
        resources = hcl2.loads(source).get("resource", [])
        if len(resources) != 1:
            return False
        resource = {key.strip('"'): value for key, value in resources[0].items()}
        if set(resource) != {"aws_vpc_security_group_ingress_rule"}:
            return False
        instances = resource["aws_vpc_security_group_ingress_rule"]
        if len(instances) != 1:
            return False
        attributes = next(iter(instances.values()))
        return bool(attributes.get("referenced_security_group_id")) and not any(
            key in attributes
            for key in ("cidr_ipv4", "cidr_ipv6", "cidr_blocks", "ipv6_cidr_blocks")
        )
    except Exception:
        # Incomplete/unknown evidence cannot justify discarding a native finding.
        return False


def parse_checkov(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    try:
        documents = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    if not isinstance(documents, list):
        documents = [documents]
    findings = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("invalid Checkov report")
        summary = document.get("summary") or {}
        if "results" not in document:
            if not isinstance(summary.get("passed"), int) or not isinstance(
                summary.get("failed"), int
            ):
                raise ValueError("unrecognized Checkov report envelope")
            if summary["failed"]:
                raise IncompleteToolOutput("Checkov reported failures without diagnostics")
        results = document.get("results", {})
        if not isinstance(results, dict):
            raise ValueError("invalid Checkov results")
        _report_errors(results, "parsing_errors", "Checkov")
        if summary.get("parsing_errors"):
            raise IncompleteToolOutput("Checkov reported configuration parsing errors")
        if "results" in document and not isinstance(results.get("failed_checks"), list):
            raise ValueError("Checkov report lacks failed_checks array")
        for row in results.get("failed_checks", []):
            code_block = row.get("code_block") or []
            if row.get("check_id") == "CKV_AWS_260" and _checkov_security_group_reference(
                code_block
            ):
                continue
            location = row.get("file_line_range") or [None]
            findings.append(
                finding(
                    adapter,
                    root=root,
                    category="misconfiguration",
                    rule_id=str(row.get("check_id") or "checkov"),
                    severity=str(row.get("severity") or "unknown"),
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
        data = _object_report(result, "runs")
    except ValueError as exc:
        raise ValueError("expected structured tool output") from exc
    findings = []
    levels = {"error": "high", "warning": "medium", "note": "low", "none": "info"}
    for run in data["runs"]:
        for invocation in run.get("invocations", []):
            if invocation.get("executionSuccessful") is False:
                raise IncompleteToolOutput("SARIF invocation did not complete successfully")
        for row in run.get("results", []):
            location = (row.get("locations") or [{}])[0].get("physicalLocation", {})
            artifact = location.get("artifactLocation", {}).get("uri")
            if (
                adapter.name == "zizmor"
                and artifact
                and "/" not in artifact
                and (root / ".github" / "workflows" / artifact).is_file()
            ):
                artifact = f".github/workflows/{artifact}"
            region = location.get("region", {})
            message = row.get("message", {})
            original_rule = str(row.get("ruleId") or adapter.name)
            if adapter.name == "zizmor" and original_rule.endswith("/template-injection"):
                normalized_rule = "github-actions/template-injection"
                category = "workflow-security"
            elif adapter.name == "zizmor" and original_rule.endswith("/unpinned-uses"):
                normalized_rule = "blueprint-ai/ci-cd/unpinned-action"
                category = "unpinned-action"
            else:
                normalized_rule = original_rule
                category = "workflow-security" if adapter.name == "zizmor" else "sarif"
            item = finding(
                adapter,
                root=root,
                category=category,
                rule_id=normalized_rule,
                severity=levels.get(str(row.get("level", "warning")).lower(), "medium"),
                file=artifact,
                line=_int(region.get("startLine")),
                message=str(message.get("text") or message.get("markdown") or row.get("ruleId")),
            )
            item.tool_metadata["original_rule_id"] = original_rule
            findings.append(item)
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


def parse_conftest(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("Conftest must emit a JSON result array")
    findings = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("successes", 0), int):
            raise ValueError("invalid Conftest result envelope")
        for level in ("failures", "warnings"):
            diagnostics = row.get(level, []) or []
            if not isinstance(diagnostics, list):
                raise ValueError("invalid Conftest diagnostic array")
            for diagnostic in diagnostics:
                if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("msg"), str):
                    raise ValueError("invalid Conftest diagnostic")
                metadata = diagnostic.get("metadata") or {}
                item = finding(
                    adapter,
                    root=root,
                    message=diagnostic["msg"],
                    file=row.get("filename"),
                    severity="high" if level == "failures" else "medium",
                    rule_id=str(metadata.get("rule", row.get("namespace", "policy"))),
                )
                item.tool_metadata = {
                    "namespace": row.get("namespace"),
                    "metadata": json.dumps(metadata, sort_keys=True),
                }
                findings.append(item)
    return findings


def parse_ast_grep(
    result: CommandResult, root: Path, adapter: ExternalToolAdapter
) -> list[Finding]:
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("ast-grep must emit a JSON diagnostic array")
    findings = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("ruleId"), str)
            or not isinstance(row.get("range"), dict)
        ):
            raise ValueError("invalid ast-grep diagnostic")
        findings.append(
            finding(
                adapter,
                root=root,
                message=str(row.get("message") or row["ruleId"]),
                file=row.get("file"),
                line=int(row["range"]["start"]["line"]) + 1,
                rule_id=row["ruleId"],
                severity={"error": "high", "warning": "medium", "info": "low", "hint": "info"}.get(
                    str(row.get("severity", "warning")).lower(), "medium"
                ),
            )
        )
    return findings


def parse_buf(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    findings = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("message"), str):
            raise ValueError("invalid Buf diagnostic")
        findings.append(
            finding(
                adapter,
                root=root,
                message=row["message"],
                file=row.get("path"),
                line=row.get("start_line"),
                rule_id=row.get("type", "protobuf-lint"),
            )
        )
    return findings


def parse_syft(result: CommandResult, root: Path, adapter: ExternalToolAdapter) -> list[Finding]:
    import hashlib

    payload = json.loads(result.stdout)
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("artifacts"), list)
        or not isinstance(payload.get("descriptor"), dict)
    ):
        raise ValueError("unrecognized Syft SBOM envelope")
    if any(not isinstance(p, dict) or not p.get("name") for p in payload["artifacts"]):
        raise ValueError("malformed Syft package inventory")
    adapter.output_evidence = {
        "format": "syft-json",
        "packages": len(payload["artifacts"]),
        "sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
    }
    return []
