"""Read-only adapter for the pinned Context static-diagnostics producer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from blueprint_ai.config import load_settings
from blueprint_ai.core import Finding, ProjectFacts
from blueprint_ai.core.models import FileRange
from blueprint_ai.discovery import discover_project, project_source_snapshot
from blueprint_ai.safety import read_bytes_bounded

_PROFILE_ENV = {
    "source": "BLUEPRINT_CONTEXT_SOURCE",
    "revision": "BLUEPRINT_CONTEXT_REF",
    "python": "BLUEPRINT_CONTEXT_PYTHON",
    "node": "BLUEPRINT_CONTEXT_NODE",
    "typescript": "BLUEPRINT_CONTEXT_TYPESCRIPT",
}
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SEVERITY = {"error": "high", "warning": "medium", "suggestion": "low", "info": "info"}
_MAX_RESPONSE = 4_000_000
_MAX_DIAGNOSTICS = 20_000


def _profile() -> dict[str, str]:
    values = {key: os.environ.get(env, "") for key, env in _PROFILE_ENV.items()}
    if not all(values.values()):
        raise ValueError(
            "Context static diagnostics are not configured; no syntax diagnostics ran."
        )
    source = Path(values["source"])
    if (
        not source.is_absolute()
        or source.is_symlink()
        or not source.is_dir()
        or source.resolve(strict=True) != source
    ):
        raise ValueError("Configured Context source is unavailable; no syntax diagnostics ran.")
    if not re.fullmatch(r"[a-f0-9]{40}", values["revision"]):
        raise ValueError(
            "Configured Context source revision is invalid; no syntax diagnostics ran."
        )
    for key in ("python", "node", "typescript"):
        path = Path(values[key])
        if not path.is_absolute() or not path.is_file():
            raise ValueError(
                "A configured Context runtime is unavailable; no syntax diagnostics ran."
            )
        if key != "python" and path.resolve(strict=True) != path:
            raise ValueError(
                "Configured Context runtime is not an exact resolved file; no diagnostics ran."
            )
        if key == "python" and not path.resolve(strict=True).is_file():
            raise ValueError(
                "A configured Context Python runtime is unavailable; no syntax diagnostics ran."
            )
        if key in {"python", "node"} and not os.access(path, os.X_OK):
            raise ValueError(
                "A configured Context runtime is not executable; no syntax diagnostics ran."
            )
    try:
        head = subprocess.run(
            ["git", "-C", values["source"], "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", values["source"], "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise ValueError(
            "Configured Context source could not be verified; no syntax diagnostics ran."
        ) from None
    if head != values["revision"] or status:
        raise ValueError(
            "Context source is not clean at the accepted revision; no diagnostics ran."
        )
    return values


def _relative(path: Any) -> bool:
    return (
        isinstance(path, str)
        and 0 < len(path) <= 500
        and "\\" not in path
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in path.split("/"))
        and PurePosixPath(path).as_posix() == path
    )


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def _diagnostic_id(diagnostic: dict[str, Any], path: str, file_sha: str) -> str:
    material = {
        "path": path,
        "fileSha256": file_sha,
        "code": diagnostic["code"],
        "severity": diagnostic["severity"],
        "range": diagnostic["range"],
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _line_evidence(root: Path, path: str, file_sha: str, line: int) -> str:
    try:
        data = read_bytes_bounded(root / path, 8_000_000, root=root)
        if hashlib.sha256(data).hexdigest() != file_sha:
            return "Source excerpt omitted because the file changed after hashing."
        lines = data.decode("utf-8", errors="replace").splitlines()
        if not 1 <= line <= len(lines):
            return "Source excerpt unavailable at the reported line."
        excerpt = lines[line - 1]
        if len(excerpt) > 1000:
            excerpt = excerpt[:1000] + "…"
        return f"Line {line}: {excerpt}"
    except (OSError, ValueError):
        return "Source excerpt unavailable; the diagnostic location and file hash are retained."


def _validate_packet(packet: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if (
        not isinstance(packet, dict)
        or packet.get("schema") != "context.static-diagnostics.v1"
        or packet.get("schema_version") != 1
    ):
        raise ValueError("Context returned an unsupported static-diagnostics packet.")
    if packet.get("stable") is not True or packet.get("status") not in {"complete", "partial"}:
        raise ValueError("Context could not confirm a stable static-diagnostics snapshot.")
    snapshot = packet.get("snapshot")
    if (
        not isinstance(snapshot, dict)
        or not _digest(snapshot.get("id"))
        or not isinstance(snapshot.get("files"), list)
        or snapshot.get("stable") is not True
        or not isinstance(snapshot.get("dirty"), bool)
        or (
            snapshot.get("head") is not None
            and not re.fullmatch(r"[a-f0-9]{40,64}", snapshot.get("head", ""))
        )
    ):
        raise ValueError("Context returned an invalid source snapshot.")
    if not _digest(packet.get("source_digest")):
        raise ValueError("Context returned an invalid source digest.")
    if packet.get("schema_version") != 1:
        raise ValueError("Context returned an unsupported static-diagnostics version.")
    coverage = packet.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("Context returned invalid coverage status.")
    if coverage.get("column_units") != "UTF-16 code units; 1-based, end-exclusive":
        raise ValueError("Context returned an unsupported source-column convention.")
    files = packet.get("files")
    if not isinstance(files, list) or len(files) > 50_000:
        raise ValueError("Context returned an invalid file inventory.")
    seen: set[str] = set()
    source_hashes: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    coverage_notes: list[str] = []
    unknown = coverage.get("unknown_reasons", [])
    if not isinstance(unknown, list) or any(
        not isinstance(item, str) or len(item) > 1000 for item in unknown
    ):
        raise ValueError("Context returned invalid coverage qualifications.")
    coverage_notes.extend(unknown[:100])
    coverage_counts = (
        "tracked_source_candidates",
        "analyzed_files",
        "unsupported_files",
        "omitted_files",
    )
    if any(type(coverage.get(key)) is not int or coverage[key] < 0 for key in coverage_counts):
        raise ValueError("Context returned invalid coverage counts.")
    coverage_diagnostics = coverage.get("diagnostics")
    if not isinstance(coverage_diagnostics, dict) or any(
        type(coverage_diagnostics.get(key)) is not int or coverage_diagnostics[key] < 0
        for key in ("total", "returned", "omitted")
    ):
        raise ValueError("Context returned invalid diagnostic coverage counts.")
    if (
        coverage_diagnostics["returned"] > coverage_diagnostics["total"]
        or coverage_diagnostics["omitted"]
        != coverage_diagnostics["total"] - coverage_diagnostics["returned"]
    ):
        raise ValueError("Context diagnostic coverage counts are inconsistent.")
    if coverage_diagnostics["omitted"]:
        coverage_notes.append(
            f"Context omitted {coverage_diagnostics['omitted']} diagnostics at its response bound."
        )
    for item in files:
        if not isinstance(item, dict) or not _relative(item.get("path")) or item["path"] in seen:
            raise ValueError("Context returned an unsafe or duplicate source path.")
        seen.add(item["path"])
        state = item.get("status")
        if state not in {"analyzed", "unsupported", "omitted"}:
            raise ValueError("Context returned an unknown file analysis status.")
        file_sha = item.get("sha256")
        if file_sha is not None and not _digest(file_sha):
            raise ValueError("Context returned an invalid file hash.")
        if state == "analyzed" and file_sha is None:
            raise ValueError("Context omitted the hash for an analyzed file.")
        if file_sha is not None:
            source_hashes[item["path"]] = file_sha
        language = item.get("language")
        if not isinstance(language, str) or not language or len(language) > 100:
            raise ValueError("Context returned an invalid source language.")
        total = item.get("diagnostic_total")
        if type(total) is not int or total < 0:
            raise ValueError("Context returned an invalid diagnostic count.")
        if state != "analyzed":
            reason = item.get("reason") or f"Context marked {item['path']} {state}."
            if not isinstance(reason, str) or len(reason) > 1000:
                reason = f"Context marked {item['path']} {state}; reason unavailable."
            coverage_notes.append(f"{item['path']}: {reason}")
        file_unknown = item.get("unknown_reasons", [])
        if not isinstance(file_unknown, list) or any(
            not isinstance(value, str) or len(value) > 1000 for value in file_unknown
        ):
            raise ValueError("Context returned invalid per-file coverage qualifications.")
        coverage_notes.extend(file_unknown[:100])
        rows = item.get("diagnostics")
        if not isinstance(rows, list) or len(rows) > _MAX_DIAGNOSTICS:
            raise ValueError("Context returned an invalid diagnostics list.")
        if state != "analyzed" and rows:
            raise ValueError("Context attached diagnostics to a file it did not analyze.")
        if len(rows) > total:
            raise ValueError("Context returned more diagnostics than its reported total.")
        if len(rows) < total:
            coverage_notes.append(
                f"{item['path']}: per-file count exceeds returned rows; missing findings unknown."
            )
        for diagnostic in rows:
            if not isinstance(diagnostic, dict):
                raise ValueError("Context returned an invalid diagnostic.")
            code, message, severity = (
                diagnostic.get("code"),
                diagnostic.get("message"),
                diagnostic.get("severity"),
            )
            location = diagnostic.get("range")
            if (
                not isinstance(code, (str, int))
                or len(str(code)) > 200
                or not isinstance(message, str)
                or len(message) > 2000
                or severity not in _SEVERITY
            ):
                raise ValueError("Context returned malformed diagnostic fields.")
            if not isinstance(location, dict) or set(location) != {"start", "end"}:
                raise ValueError("Context returned an invalid diagnostic range.")
            start, end = location["start"], location["end"]
            if (
                not isinstance(start, dict)
                or not isinstance(end, dict)
                or not all(
                    type(v) is int and v > 0
                    for v in (
                        start.get("line"),
                        start.get("column"),
                        end.get("line"),
                        end.get("column"),
                    )
                )
                or end["line"] < start["line"]
                or (end["line"] == start["line"] and end["column"] < start["column"])
            ):
                raise ValueError("Context returned an invalid diagnostic range.")
            if diagnostic.get("diagnosticId") != _diagnostic_id(diagnostic, item["path"], file_sha):
                raise ValueError("Context diagnostic identity did not match its source evidence.")
            diagnostics.append({**diagnostic, "path": item["path"], "fileSha256": file_sha})
    if len(diagnostics) > _MAX_DIAGNOSTICS:
        raise ValueError("Context diagnostics exceed the bounded report limit.")
    snapshot_hashes: dict[str, str] = {}
    for item in snapshot["files"]:
        if (
            not isinstance(item, dict)
            or not _relative(item.get("path"))
            or not _digest(item.get("sha256"))
            or item["path"] in snapshot_hashes
        ):
            raise ValueError("Context returned an invalid snapshot file row.")
        snapshot_hashes[item["path"]] = item["sha256"]
    if snapshot_hashes != source_hashes:
        raise ValueError("Context snapshot rows did not match its analyzed file inventory.")
    if coverage["analyzed_files"] != sum(item["status"] == "analyzed" for item in files):
        raise ValueError("Context analyzed-file coverage counts are inconsistent.")
    if coverage["unsupported_files"] != sum(item["status"] == "unsupported" for item in files):
        raise ValueError("Context unsupported-file coverage counts are inconsistent.")
    if coverage["omitted_files"] < sum(item["status"] == "omitted" for item in files):
        raise ValueError("Context omitted-file coverage counts are inconsistent.")
    if coverage_diagnostics["returned"] != sum(len(item.get("diagnostics", [])) for item in files):
        raise ValueError("Context returned-diagnostic coverage counts are inconsistent.")
    if coverage_diagnostics["total"] != sum(item.get("diagnostic_total", 0) for item in files):
        # Global producer totals bound returned rows; per-file parser counters may
        # include diagnostics that could not be represented as selectable rows.
        coverage_notes.append(
            "Context's per-file and global diagnostic counts differ; missing rows are unknown."
        )
    digest = hashlib.sha256()
    for path in sorted(source_hashes, key=lambda value: value.encode("utf-8")):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(source_hashes[path]))
    if digest.hexdigest() != packet["source_digest"]:
        raise ValueError("Context source digest did not match its file hashes.")
    if packet.get("status") == "partial":
        coverage_notes.append("Context reported partial language coverage.")
    qualifications = packet.get("qualifications")
    if (
        not isinstance(qualifications, list)
        or not qualifications
        or any(not isinstance(item, str) or len(item) > 2000 for item in qualifications)
    ):
        raise ValueError("Context omitted its analysis qualifications.")
    coverage_notes.extend(qualifications[:20])
    return diagnostics, list(dict.fromkeys(coverage_notes))[:100]


def context_diagnostics(root: Path, facts: ProjectFacts) -> list[Finding]:
    """Run Context's pinned read-only producer and admit only source-matched findings."""
    profile = _profile()
    root = root.expanduser().resolve(strict=True)
    if not (root / ".git").exists():
        raise ValueError(
            "Context static diagnostics require a configured Git root; none ran."
        )
    before = project_source_snapshot(root, facts, evidence_paths=facts.graph.file_scopes.keys())
    if not before["complete"]:
        raise ValueError(
            "Blueprint source inventory is incomplete; Context findings were not admitted."
        )
    script = Path(profile["source"]) / "scripts" / "static_diagnostics.py"
    git_executable = shutil.which("git")
    if not git_executable:
        raise ValueError(
            "Git is unavailable to Context static diagnostics; no findings were admitted."
        )
    command = [
        profile["python"],
        str(script),
        "--project",
        str(root),
        "--node",
        profile["node"],
        "--typescript",
        profile["typescript"],
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=profile["source"],
            capture_output=True,
            timeout=90,
            env={
                "PATH": os.pathsep.join(
                    dict.fromkeys(
                        (
                            str(Path(profile["python"]).parent),
                            str(Path(profile["node"]).parent),
                            str(Path(git_executable).resolve().parent),
                        )
                    )
                ),
                "PYTHONPATH": profile["source"],
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError(
            "Context static diagnostics were unavailable; no findings were admitted."
        ) from None
    if completed.returncode != 0 or len(completed.stdout) > _MAX_RESPONSE:
        raise ValueError("Context static diagnostics could not confirm a bounded result.")
    try:
        packet = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Context returned an invalid static-diagnostics response.") from None
    diagnostics, coverage_notes = _validate_packet(packet)
    engine = packet.get("engine")
    if not isinstance(engine, dict):
        raise ValueError("Context omitted its analyzer identity.")
    for name, profile_key in (("python", "python"), ("node", "node"), ("typescript", "typescript")):
        identity = engine.get(name)
        expected_path = str(Path(profile[profile_key]).resolve(strict=True))
        if not isinstance(identity, dict) or identity.get("path") != expected_path:
            raise ValueError("Context analyzer identity did not match the trusted runtime profile.")
    context_files = packet["files"]
    paths = {item["path"] for item in context_files}
    if not paths.issubset(facts.graph.file_scopes):
        raise ValueError(
            "Context reported files outside the Blueprint inventory; findings were not admitted."
        )
    after_facts = discover_project(
        root, load_settings(root, trust_project_executables=False).ignores
    )
    after = project_source_snapshot(root, after_facts, evidence_paths=paths)
    if (
        not after["complete"]
        or before["source_digest"] != after["source_digest"]
        or set(before["files"]) != set(after_facts.graph.file_scopes)
        or set(after_facts.graph.file_scopes) != set(facts.graph.file_scopes)
    ):
        raise ValueError(
            "Sources changed or became incomplete; Context findings were not admitted."
        )
    admitted: list[Finding] = []
    for item in context_files:
        if item["sha256"] is not None and (
            before["files"].get(item["path"]) != item["sha256"]
            or after["files"].get(item["path"]) != item["sha256"]
        ):
            raise ValueError(
                "A Context file hash mismatched Blueprint before/after; findings were not admitted."
            )
    for diagnostic in diagnostics:
        path = diagnostic["path"]
        location = diagnostic["range"]
        snippet = _line_evidence(root, path, diagnostic["fileSha256"], location["start"]["line"])
        source_range = FileRange(
            start_line=location["start"]["line"],
            start_column=location["start"]["column"],
            end_line=location["end"]["line"],
            end_column=location["end"]["column"],
        )
        raw_severity = diagnostic["severity"]
        admitted.append(
            Finding(
                blueprint="context-diagnostics",
                category=str(diagnostic["code"]),
                rule_id=f"context-ai/{diagnostic['diagnosticId']}",
                source="context-ai/static-diagnostics",
                sources=["context-ai"],
                severity=_SEVERITY[raw_severity],
                priority=None,
                file=path,
                range=source_range,
                evidence=[f"Context {raw_severity}: {diagnostic['message']}", snippet],
                message=diagnostic["message"],
                recommendation=(
                    "Review this syntax diagnostic and source range; no changes were applied."
                ),
                verification="rerun Context static diagnostics",
                tool_metadata={
                    "diagnostic_id": diagnostic["diagnosticId"],
                    "producer_severity": raw_severity,
                    "file_sha256": diagnostic["fileSha256"],
                    "column_encoding": "1-based UTF-16 code units; end-exclusive",
                    "scopeable": True,
                },
            )
        )
    if coverage_notes:
        admitted.append(
            Finding(
                blueprint="context-diagnostics",
                category="coverage-unknown",
                rule_id="context-ai/static-diagnostics/coverage-unknown",
                source="context-ai/static-diagnostics",
                sources=["context-ai"],
                severity="info",
                priority=None,
                file=None,
                evidence=coverage_notes,
                message=(
                    "Context syntax coverage is partial; unsupported, omitted, and unknown paths "
                    "were not assessed."
                ),
                recommendation=(
                    "Treat listed coverage limits as unknown; do not infer the project is clean."
                ),
                verification="rerun Context static diagnostics after resolving coverage limits",
                tool_metadata={"scopeable": False, "coverage_status": "partial"},
            )
        )
    return admitted
