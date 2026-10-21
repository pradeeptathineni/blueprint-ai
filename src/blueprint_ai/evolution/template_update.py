"""Pinned local Git templates with previewed, reversible three-way updates."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

from blueprint_ai import __version__

from .engine import _operation_lock, _operations_root, _safe_path

RECORD = ".blueprint-ai/template.json"
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
_KEY = re.compile(rb"\{\{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*\}\}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: object) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def _git(directory: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(directory), *args],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"Git {args[0]} failed: {result.stderr.decode(errors='replace')[:400]}")
    return result.stdout


def _commit(source: Path, ref: str) -> str:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("template source must be a local Git directory")
    if not re.fullmatch(r"[a-zA-Z0-9_./-]{1,120}", ref) or ref.startswith("-"):
        raise ValueError("invalid template ref")
    commit = _git(source, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("template ref did not resolve to a commit")
    return commit


def _snapshot(source: Path, commit: str, answers: dict[str, str]) -> dict[str, tuple[bytes, int]]:
    raw = _git(source, "ls-tree", "-rz", "--full-tree", commit)
    result: dict[str, tuple[bytes, int]] = {}
    total = 0
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, path_bytes = item.split(b"\t", 1)
        mode, kind, _object = header.decode().split()
        path = path_bytes.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"template contains unsupported entry: {path}")
        if path == RECORD or path.startswith((".git/", ".blueprint-ai/")):
            raise ValueError(f"template uses reserved path: {path}")
        _safe_path(Path("/tmp"), path)
        content = _git(source, "show", f"{commit}:{path}")
        if len(content) > MAX_FILE:
            raise ValueError(f"template file exceeds 4 MiB: {path}")

        def replace(match: re.Match[bytes]) -> bytes:
            key = match.group(1).decode()
            if key not in answers:
                raise ValueError(f"missing template answer: {key}")
            return answers[key].encode()

        content = _KEY.sub(replace, content)
        if b"{{" in content and re.search(rb"\{\{[^{}]*\}\}", content):
            raise ValueError(f"unsupported template placeholder: {path}")
        if len(content) > MAX_FILE:
            raise ValueError(f"rendered template file exceeds 4 MiB: {path}")
        total += len(content)
        if total > MAX_TOTAL:
            raise ValueError("rendered template exceeds 32 MiB")
        result[path] = (content, int(mode, 8) & 0o777)
    if not result:
        raise ValueError("template has no files")
    return result


def _read(root: Path, path: str) -> tuple[bytes, int] | None:
    target = _safe_path(root, path)
    if not target.exists():
        return None
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
        raise ValueError(f"unsupported or oversized project file: {path}")
    return target.read_bytes(), stat.S_IMODE(info.st_mode)


def _merge(ours: bytes, base: bytes, theirs: bytes) -> tuple[bytes, bool]:
    if b"\0" in ours + base + theirs:
        return ours, True
    with tempfile.TemporaryDirectory(prefix="blueprint-template-merge-") as temporary:
        files = [Path(temporary) / name for name in ("ours", "base", "theirs")]
        for path, content in zip(files, (ours, base, theirs), strict=True):
            path.write_bytes(content)
        result = subprocess.run(
            [
                "git",
                "merge-file",
                "-p",
                "-L",
                "project",
                "-L",
                "template base",
                "-L",
                "template update",
                *(str(p) for p in files),
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    if result.returncode not in {0, 1}:
        raise ValueError(f"Git merge-file failed: {result.stderr.decode(errors='replace')[:400]}")
    return result.stdout, result.returncode == 1


def _record(root: Path) -> dict:
    current = _read(root, RECORD)
    if current is None:
        raise ValueError("project has no Blueprint template record; run template init")
    try:
        record = json.loads(current[0])
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid template record") from exc
    if record.get("schema") != 1 or not isinstance(record.get("answers"), dict):
        raise ValueError("invalid template record")
    return record


def initialize(root: Path, source: Path, ref: str, answers: dict[str, str]) -> dict:
    root, source = root.resolve(), source.resolve()
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in answers.items()):
        raise ValueError("template answers must be string keys and values")
    if root.exists() and any(root.iterdir()):
        raise ValueError("template destination must be empty")
    commit = _commit(source, ref)
    files = _snapshot(source, commit, answers)
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    record = {"schema": 1, "source": str(source), "commit": commit, "answers": answers}
    try:
        for path, (content, mode) in files.items():
            target = _safe_path(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(mode)
        target = _safe_path(root, RECORD)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_json(record))
    except Exception:
        for path in [*files, RECORD]:
            _safe_path(root, path).unlink(missing_ok=True)
        raise
    return {"status": "initialized", "commit": commit, "files": sorted(files), "record": RECORD}


def check(root: Path, ref: str) -> dict:
    record = _record(root.resolve())
    target = _commit(Path(record["source"]), ref)
    return {
        "status": "current" if target == record["commit"] else "behind",
        "current": record["commit"],
        "target": target,
    }


def plan(root: Path, ref: str, resolutions: dict[str, str] | None = None) -> dict:
    root = root.resolve()
    record = _record(root)
    source = Path(record["source"])
    old = _snapshot(source, record["commit"], record["answers"])
    target_commit = _commit(source, ref)
    new = _snapshot(source, target_commit, record["answers"])
    resolutions = resolutions or {}
    if any(path not in old and path not in new for path in resolutions):
        raise ValueError("resolution names a path outside the template")
    rows = []
    for path in sorted(old.keys() | new.keys()):
        base = old.get(path)
        theirs = new.get(path)
        ours = _read(root, path)
        proposed: tuple[bytes, int] | None
        conflict = False
        if base == theirs:
            proposed = ours
        elif ours == base:
            proposed = theirs
        elif theirs == ours:
            proposed = ours
        elif base and theirs and ours:
            merged, conflict = _merge(ours[0], base[0], theirs[0])
            proposed = (merged, ours[1])
        else:
            conflict = True
            proposed = ours
        resolution = resolutions.get(path)
        if resolution and not conflict:
            raise ValueError(f"resolution is unnecessary: {path}")
        if conflict and resolution:
            if resolution == "project":
                proposed = ours
            elif resolution == "template":
                proposed = theirs
            else:
                resolved = Path(resolution).resolve()
                if (
                    not resolved.is_file()
                    or resolved.is_symlink()
                    or resolved.stat().st_size > MAX_FILE
                ):
                    raise ValueError(f"invalid resolution file: {resolution}")
                proposed = (resolved.read_bytes(), ours[1] if ours else 0o644)
            conflict = False
        status = (
            "conflict"
            if conflict
            else "noop"
            if ours == proposed
            else "delete"
            if proposed is None
            else "create"
            if ours is None
            else "change"
        )
        diff = ""
        if status != "noop" and all(x is None or b"\0" not in x[0] for x in (ours, proposed)):
            diff = "".join(
                difflib.unified_diff(
                    (ours[0] if ours else b"").decode(errors="replace").splitlines(keepends=True),
                    (proposed[0] if proposed else b"")
                    .decode(errors="replace")
                    .splitlines(keepends=True),
                    fromfile=f"project/{path}",
                    tofile=f"updated/{path}",
                )
            )
        rows.append(
            {
                "path": path,
                "status": status,
                "before": _sha(ours[0]) if ours else None,
                "before_mode": ours[1] if ours else None,
                "after": _sha(proposed[0]) if proposed else None,
                "after_mode": proposed[1] if proposed else None,
                "content": base64.b64encode(proposed[0]).decode() if proposed else None,
                "diff": diff,
            }
        )
    current_record = _read(root, RECORD)
    next_record = {**record, "commit": target_commit}
    if target_commit != record["commit"]:
        assert current_record is not None
        rows.append(
            {
                "path": RECORD,
                "status": "change",
                "before": _sha(current_record[0]),
                "before_mode": current_record[1],
                "after": _sha(_json(next_record)),
                "after_mode": current_record[1],
                "content": base64.b64encode(_json(next_record)).decode(),
                "diff": "",
            }
        )
    outcome = (
        "conflict"
        if any(row["status"] == "conflict" for row in rows)
        else "noop"
        if all(row["status"] == "noop" for row in rows)
        else "ready"
    )
    result = {
        "schema": 1,
        "status": outcome,
        "root": str(root),
        "source": str(source),
        "from_commit": record["commit"],
        "to_commit": target_commit,
        "rows": rows,
    }
    result["plan_sha256"] = _sha(_json(result))
    return result


def apply(root: Path, saved: dict) -> dict:
    root = root.resolve()
    expected = saved.get("plan_sha256")
    if (
        not isinstance(expected, str)
        or _sha(_json({k: v for k, v in saved.items() if k != "plan_sha256"})) != expected
    ):
        raise ValueError("template plan checksum mismatch")
    if saved.get("root") != str(root) or saved.get("status") == "conflict":
        raise ValueError("plan belongs to another project or has unresolved conflicts")
    with _operation_lock(root):
        record = _record(root)
        if record["commit"] != saved.get("from_commit") or record["source"] != saved.get("source"):
            raise ValueError("template provenance changed after preview")
        if _commit(Path(record["source"]), saved["to_commit"]) != saved["to_commit"]:
            raise ValueError("target template commit is unavailable")
        rows = saved["rows"]
        if (
            not isinstance(rows, list)
            or len(rows) > 2000
            or len({r["path"] for r in rows}) != len(rows)
        ):
            raise ValueError("invalid template plan paths")
        changes = [row for row in rows if row["status"] not in {"noop", "conflict"}]
        before: dict[str, tuple[bytes, int] | None] = {}
        for row in rows:
            path = row["path"]
            if path != RECORD and (path.startswith(".blueprint-ai/") or path.startswith(".git/")):
                raise ValueError("reserved template target")
            current = _read(root, path)
            if (_sha(current[0]) if current else None) != row["before"] or (
                current[1] if current else None
            ) != row["before_mode"]:
                raise ValueError(f"stale template plan: {path} changed after preview")
            before[path] = current
            proposed = (
                base64.b64decode(row["content"], validate=True)
                if row["content"] is not None
                else None
            )
            if (_sha(proposed) if proposed is not None else None) != row["after"]:
                raise ValueError(f"invalid planned content: {path}")
        if saved.get("status") == "noop":
            return {"status": "noop", "changes": [], "plan_sha256": expected}
        operation = uuid.uuid4().hex
        state = _operations_root(root, create=True) / operation
        state.mkdir(mode=0o700)
        written = []
        try:
            backups = state / "before"
            for row in changes:
                path = row["path"]
                original = before[path]
                if original:
                    backup = backups / path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(original[0])
                    backup.chmod(original[1])
            for row in changes:
                path = row["path"]
                target = _safe_path(root, path)
                if row["content"] is None:
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                        temporary.write(base64.b64decode(row["content"], validate=True))
                        staged = Path(temporary.name)
                    try:
                        staged.chmod(row["after_mode"])
                        os.replace(staged, target)
                    finally:
                        staged.unlink(missing_ok=True)
                written.append(path)
                observed = _read(root, path)
                if (_sha(observed[0]) if observed else None) != row["after"] or (
                    observed[1] if observed else None
                ) != row["after_mode"]:
                    raise OSError(f"post-write verification failed: {path}")
            manifest = {
                "version": 4,
                "kind": "template-update",
                "blueprint_ai_version": __version__,
                "operation_id": operation,
                "plan_sha256": expected,
                "from_commit": saved["from_commit"],
                "to_commit": saved["to_commit"],
                "changes": [
                    {
                        k: row[k]
                        for k in ("path", "status", "before", "before_mode", "after", "after_mode")
                    }
                    for row in changes
                ],
            }
            receipt = _json(manifest)
            (state / "receipt.sha256").write_text(_sha(receipt))
            public = _safe_path(root, f".blueprint-ai/operations/{operation}.json")
            public.parent.mkdir(parents=True, exist_ok=True)
            public.write_bytes(receipt)
            return {
                "status": "verified",
                "operation_id": operation,
                "changes": manifest["changes"],
                "manifest_path": f".blueprint-ai/operations/{operation}.json",
                "plan_sha256": expected,
                "rollback_command": ["blueprint-ai", "rollback", operation, str(root)],
            }
        except Exception:
            receipt_path = root / ".blueprint-ai" / "operations" / f"{operation}.json"
            receipt_path.unlink(missing_ok=True)
            for path in reversed(written):
                target = _safe_path(root, path)
                original = before[path]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(original[0])
                    target.chmod(original[1])
            shutil.rmtree(state, ignore_errors=True)
            raise


def rollback(root: Path, operation: str) -> dict:
    root = root.resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise ValueError("invalid template operation id")
    with _operation_lock(root):
        public = _safe_path(root, f".blueprint-ai/operations/{operation}.json", missing=False)
        state = _operations_root(root) / operation
        receipt = public.read_bytes()
        if not (state / "receipt.sha256").is_file() or (
            state / "receipt.sha256"
        ).read_text() != _sha(receipt):
            raise ValueError("template update receipt is not sealed by Git-private state")
        manifest = json.loads(receipt)
        if manifest.get("kind") != "template-update" or manifest.get("operation_id") != operation:
            raise ValueError("invalid template operation receipt")
        changes = manifest["changes"]
        if len({row["path"] for row in changes}) != len(changes):
            raise ValueError("duplicate rollback targets")
        for row in changes:
            current = _read(root, row["path"])
            if (_sha(current[0]) if current else None) != row["after"] or (
                current[1] if current else None
            ) != row["after_mode"]:
                raise ValueError(f"rollback refused: {row['path']} changed after update")
            if row["before"] is not None:
                backup = state / "before" / row["path"]
                if (
                    not backup.is_file()
                    or backup.is_symlink()
                    or _sha(backup.read_bytes()) != row["before"]
                ):
                    raise ValueError(f"rollback backup is unavailable or changed: {row['path']}")
        for row in reversed(changes):
            target = _safe_path(root, row["path"])
            if row["before"] is None:
                target.unlink()
            else:
                backup = state / "before" / row["path"]
                shutil.copy2(backup, target, follow_symlinks=False)
            restored = _read(root, row["path"])
            if (_sha(restored[0]) if restored else None) != row["before"] or (
                restored[1] if restored else None
            ) != row["before_mode"]:
                raise OSError(f"rollback verification failed: {row['path']}")
        public.unlink()
        shutil.rmtree(state)
        return {"status": "rolled_back", "operation_id": operation, "changes": changes}
