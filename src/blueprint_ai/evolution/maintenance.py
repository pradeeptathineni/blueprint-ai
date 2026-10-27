"""Compose existing declarative capability and template operations for one project.

This module only plans/records existing Blueprint operations. It adds no executor or
template renderer, and does not run target project code.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any

from blueprint_ai.core import ProjectFacts
from blueprint_ai.capabilities import add_capability, plan_add
from blueprint_ai.remediation import KITS, rollback_operation
from blueprint_ai.safety import MAX_MANIFEST_BYTES, read_bytes_bounded
from blueprint_ai.sandbox import SandboxPolicy
from blueprint_ai.evolution import template_update

PLAN_SCHEMA = "blueprint-managed-capability-plan/1"
MAX_OPERATION_FILES = 1000
MAX_OPERATION_BYTES = 32_000_000
MAX_TARGET_RECORD = 2_000_000


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(value: Any) -> str:
    return _sha(_json(value))


def _root(path: Path) -> Path:
    requested = Path(path)
    if requested.is_symlink() or not requested.is_dir() or requested.resolve() != requested:
        raise ValueError("configured project must be an ordinary exact directory")
    return requested


def _enabled_kit(name: str):
    kit = KITS.get(name)
    if kit is None:
        raise ValueError("choose a registered Blueprint capability")
    if kit.verification:
        raise ValueError("this maintenance flow only offers declarative capabilities without project verifiers")
    if not kit.files or len(kit.files) > 32:
        raise ValueError("registered capability file set exceeds the maintenance bound")
    return kit


def capability_catalog() -> dict[str, Any]:
    rows = []
    for name, kit in sorted(KITS.items()):
        if not kit.verification and kit.files and len(kit.files) <= 32:
            rows.append({
                "id": name,
                "label": name.replace("-", " ").title(),
                "version": kit.version,
                "paths": sorted(kit.files),
                "mode": "create-only",
                "verification": "built-in asset parsing only; no project command",
            })
    return {"schema": "blueprint-managed-capability-catalog/1", "capabilities": rows}


def _template_binding(root: Path, source: Path) -> dict[str, Any]:
    source = Path(source)
    if source.is_symlink() or not source.is_dir() or source.resolve() != source:
        raise ValueError("configured template source must be an ordinary exact directory")
    inspection = template_update.inspect_target(root)
    recipe = inspection["recipe"]
    if recipe.get("source") != str(source):
        raise ValueError("project template record does not match the configured template source")
    return {
        "format": recipe.get("format"),
        "commit": recipe.get("commit"),
        "record_sha256": inspection["record"].get("sha256"),
        "files": [{key: row.get(key) for key in
                   ("path", "status", "expected_sha256", "current_sha256", "expected_mode", "current_mode")}
                  for row in inspection["files"]],
    }


def plan_capability(root: Path, capability: str, template_source: Path) -> dict[str, Any]:
    root = _root(root)
    template = _template_binding(root, template_source)
    kit = _enabled_kit(capability)
    native = plan_add(root, capability)
    rows = []
    for row in native["files"]:
        content = row["content"].encode()
        path = root / row["path"]
        before = None
        if path.exists() or path.is_symlink():
            try:
                before = _sha(read_bytes_bounded(path, MAX_MANIFEST_BYTES, root=root))
            except (OSError, ValueError):
                before = "unreadable"
        rows.append({
            "path": row["path"],
            "status": row["status"],
            "before_sha256": before,
            "after_sha256": _sha(content),
            "after_mode": 0o644,
            "content": row["content"],
        })
    body = {
        "schema": PLAN_SCHEMA,
        "target": str(root),
        "capability": capability,
        "capability_version": kit.version,
        "template_binding": template,
        "status": "conflict" if native["conflicts"] else
                  "noop" if rows and all(row["status"] == "already-present" for row in rows) else "ready",
        "files": rows,
        "conflicts": list(native["conflicts"]),
        "verification": "built-in asset parsing only; no project code or verifier command runs",
    }
    return {**body, "plan_sha256": _hash(body)}


def apply_capability(root: Path, capability: str, plan_sha256: str, template_source: Path) -> dict[str, Any]:
    root = _root(root)
    if not re.fullmatch(r"[a-f0-9]{64}", plan_sha256 or ""):
        raise ValueError("invalid capability plan digest")
    fresh = plan_capability(root, capability, template_source)
    if fresh["plan_sha256"] != plan_sha256:
        raise ValueError("project or capability changed after preview; make a fresh proposal")
    if fresh["status"] == "conflict":
        raise ValueError("existing configuration conflict; project files were preserved")
    if fresh["status"] == "noop":
        return {"status": "noop", "operation_id": None, "plan_sha256": plan_sha256,
                "changes": [], "verification": fresh["verification"]}
    # These no-verifier kits do not inspect language-specific project facts. Avoid a broad
    # project scan: the native planner already bound the exact target paths and contents.
    facts = ProjectFacts(path=str(root), name=root.name)
    result = add_capability(
        root,
        facts,
        capability,
        policy=SandboxPolicy(backend="auto", network="none", trusted=False, writable=False),
    )
    if any(row.status in {"conflicted", "unsupported"} for row in result.changes):
        raise ValueError("native capability apply encountered a conflict; inspect the operation receipt")
    return {
        "status": "verified" if result.changed else "noop",
        "operation_id": result.operation_id,
        "manifest_path": result.manifest_path,
        "plan_sha256": plan_sha256,
        "changes": [row.model_dump(mode="json") for row in result.changes],
        "verifications": [row.model_dump(mode="json") for row in result.verifications],
        "verification": fresh["verification"],
    }


def _template_record(root: Path) -> dict[str, Any] | None:
    path = root / template_update.RECORD
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TARGET_RECORD or path.resolve() != path:
        raise ValueError("project template provenance is unsafe or oversized")
    raw = read_bytes_bounded(path, MAX_TARGET_RECORD, root=root)
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError("project template provenance is invalid")
    return {
        "schema": record.get("schema"),
        "format": record.get("format", "static"),
        "commit": record.get("commit"),
        "sha256": _sha(raw),
        "source_available": isinstance(record.get("source"), str) and Path(record["source"]).is_dir(),
    }


def history(root: Path) -> dict[str, Any]:
    """Cold-read retained project receipts without re-rendering a template or replanning."""
    root = _root(root)
    operations = root / ".blueprint-ai" / "operations"
    rows: list[dict[str, Any]] = []
    if operations.exists():
        info = operations.lstat()
        if not stat.S_ISDIR(info.st_mode) or operations.is_symlink() or operations.resolve() != operations:
            raise ValueError("project operation history path is unsafe")
        paths = sorted(operations.glob("*.json"))
        if len(paths) > MAX_OPERATION_FILES:
            raise ValueError("project operation history exceeds its bounded reader")
        total = 0
        for path in paths:
            if path.is_symlink() or not path.is_file() or path.resolve() != path:
                raise ValueError("project operation history contains an unsafe entry")
            total += path.stat().st_size
            if path.stat().st_size > MAX_MANIFEST_BYTES or total > MAX_OPERATION_BYTES:
                raise ValueError("project operation history exceeds its byte bound")
            try:
                record = json.loads(read_bytes_bounded(path, MAX_MANIFEST_BYTES, root=root))
            except (OSError, ValueError, UnicodeError):
                continue
            if not isinstance(record, dict) or record.get("operation_id") != path.stem:
                continue
            if record.get("kind") == "template-update":
                rows.append({
                    "kind": "template-update", "operation_id": path.stem,
                    "status": record.get("status"),
                    "plan_sha256": record.get("plan_sha256"),
                    "from_commit": record.get("from_commit"), "to_commit": record.get("to_commit"),
                    "changes": record.get("changes", []),
                    "receipt_sha256": _sha(path.read_bytes()),
                })
            elif record.get("version") in {1, 2} and isinstance(record.get("changes"), list):
                kits = sorted({str(change.get("blueprint", "")).removeprefix("kit:")
                               for change in record["changes"] if isinstance(change, dict)
                               and str(change.get("blueprint", "")).startswith("kit:")})
                if kits and all(name in KITS and not KITS[name].verification for name in kits):
                    rows.append({
                        "kind": "capability", "operation_id": path.stem,
                        "status": record.get("status"),
                        "capabilities": kits, "created_at": record.get("created_at"),
                        "changes": [{key: change.get(key) for key in ("target", "sha256", "status")}
                                    for change in record["changes"] if isinstance(change, dict)
                                    and str(change.get("blueprint", "")).startswith("kit:")],
                        "receipt_sha256": _sha(path.read_bytes()),
                    })
    rows.sort(key=lambda row: (str(row.get("from_commit", "")), row["operation_id"]))
    return {
        "schema": "blueprint-managed-maintenance-history/1",
        "template": _template_record(root),
        "operations": rows,
        "qualification": "Read from existing project template and operation receipts; no missing record is regenerated.",
    }


def rollback(root: Path, operation_id: str) -> dict[str, Any]:
    root = _root(root)
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id or ""):
        raise ValueError("invalid operation receipt ID")
    path = root / ".blueprint-ai" / "operations" / f"{operation_id}.json"
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise ValueError("operation receipt is missing or unsafe")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("operation receipt exceeds the rollback bound")
    record = json.loads(read_bytes_bounded(path, MAX_MANIFEST_BYTES, root=root))
    if not isinstance(record, dict) or record.get("operation_id") != operation_id:
        raise ValueError("operation receipt identity mismatch")
    if record.get("kind") == "template-update" and record.get("version") == 4:
        result = template_update.rollback(root, operation_id)
        return {**result, "kind": "template-update"}
    changes = record.get("changes")
    if record.get("version") not in {1, 2} or not isinstance(changes, list):
        raise ValueError("operation is outside managed maintenance rollback scope")
    kits = {str(change.get("blueprint", "")).removeprefix("kit:") for change in changes
            if isinstance(change, dict) and str(change.get("blueprint", "")).startswith("kit:")}
    if not kits or any(name not in KITS or KITS[name].verification for name in kits):
        raise ValueError("only retained no-verifier capability receipts can be rolled back here")
    result = rollback_operation(root, operation_id)
    return {**result.model_dump(mode="json"), "kind": "capability"}
