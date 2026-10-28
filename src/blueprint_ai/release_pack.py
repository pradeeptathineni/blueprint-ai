"""Deterministic, local release packs for explicitly selected tracked files.

Packs are unsigned review artifacts. This module never builds, publishes, installs,
or executes project code. Receipt and capability metadata are read through the
existing managed-maintenance APIs and remain qualified as owner-reported evidence.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from blueprint_ai.discovery import discover_project
from blueprint_ai.discovery.snapshot import project_source_snapshot
from blueprint_ai.evolution import maintenance
from blueprint_ai.safety import read_bytes_bounded

SCHEMA = "blueprint-local-release-pack/1"
MAX_FILES = 100
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_PACK_BYTES = 28 * 1024 * 1024
MAX_PREVIEW_CHARS = 4_000
_RECEIPT = re.compile(r"^[a-f0-9]{32}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_PLANNING_REVISION = re.compile(r"^[a-f0-9]{7,64}$")
_PACKAGE_MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}
_CAPABILITIES = {row["id"] for row in maintenance.capability_catalog()["capabilities"]}


def _json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root(value: Path) -> Path:
    root = Path(value)
    if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
        raise ValueError("configured project must be an ordinary exact directory")
    return root


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise ValueError("configured project Git identity is unavailable")
    return result.stdout


def _safe_paths(root: Path, paths: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES:
        raise ValueError("select between 1 and 100 exact project files")
    tracked = set(_git(root, "ls-files", "-z").decode("utf-8", errors="strict").split("\0"))
    admitted = set(discover_project(root).graph.file_scopes)
    rows: list[dict[str, Any]] = []
    omissions: list[dict[str, str]] = []
    seen: set[str] = set()
    total = 0
    for name in paths:
        if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name:
            raise ValueError("selected paths must be bounded relative POSIX paths")
        rel = PurePosixPath(name)
        if (
            rel.is_absolute()
            or any(part in {"", ".", ".."} for part in rel.parts)
            or rel.as_posix() != name
        ):
            raise ValueError("selected paths must be canonical relative POSIX paths")
        if name in seen:
            raise ValueError("selected paths must be unique")
        seen.add(name)
        path = root.joinpath(*rel.parts)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path:
                omissions.append({"path": name, "reason": "not-an-exact-regular-file"})
                continue
            if name not in tracked or name not in admitted:
                omissions.append(
                    {"path": name, "reason": "not-in-blueprint-safe-project-inventory"}
                )
                continue
            if info.st_size > MAX_FILE_BYTES:
                omissions.append({"path": name, "reason": "file-size-limit"})
                continue
            data = read_bytes_bounded(path, MAX_FILE_BYTES, root=root)
        except (OSError, ValueError):
            omissions.append({"path": name, "reason": "unavailable-or-unsafe"})
            continue
        if b"\x00" in data:
            omissions.append({"path": name, "reason": "binary-file-unsupported"})
            continue
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            omissions.append({"path": name, "reason": "non-utf8-binary-or-unsupported-text"})
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            omissions.append({"path": name, "reason": "pack-size-limit"})
            continue
        total += len(data)
        rows.append(
            {
                "path": name,
                "sha256": _sha(data),
                "bytes": len(data),
                "mode": stat.S_IMODE(info.st_mode) & 0o777,
            }
        )
    return rows, omissions


def _selected_packages(
    facts: Any, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    components = {component.id: component for component in facts.graph.components}
    grouped: dict[str, dict[str, Any]] = {}
    unmapped: list[str] = []
    for row in rows:
        owner = facts.graph.owner(row["path"])
        if owner is None or owner.id not in components:
            unmapped.append(row["path"])
            continue
        manifests = sorted(
            manifest for manifest in owner.manifests if Path(manifest).name in _PACKAGE_MANIFESTS
        )
        if not manifests:
            unmapped.append(row["path"])
            continue
        package = grouped.setdefault(
            owner.id,
            {
                "component_id": owner.id,
                "name": owner.name,
                "root": owner.root,
                "manifests": manifests,
                "files": [],
            },
        )
        package["files"].append(row["path"])
    packages = sorted(grouped.values(), key=lambda row: (row["root"], row["component_id"]))
    for package in packages:
        package["files"].sort()
    return packages, sorted(unmapped)


def _planning_readiness(
    value: Any, *, target_id: str | None, source_digest: str
) -> dict[str, Any] | None:
    if value is None:
        return None
    fields = {
        "schema",
        "owner",
        "owner_revision",
        "assessment",
        "specification",
        "project_binding",
        "currentness",
        "qualification",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Planning readiness projection has unsupported fields")
    if value["schema"] != "deep.planning-release-readiness.v1" or value["owner"] != "planning-ai":
        raise ValueError("Planning readiness owner evidence is unsupported")
    if not isinstance(value["owner_revision"], str) or not _PLANNING_REVISION.fullmatch(
        value["owner_revision"]
    ):
        raise ValueError("Planning readiness owner revision is invalid")
    if value["currentness"] not in {"current", "unknown"}:
        raise ValueError("Planning readiness currentness is invalid")
    if (
        not isinstance(value["qualification"], list)
        or len(value["qualification"]) > 12
        or any(
            not isinstance(item, str) or not item or len(item) > 500
            for item in value["qualification"]
        )
    ):
        raise ValueError("Planning readiness qualifications are invalid")
    assessment = value["assessment"]
    if not isinstance(assessment, dict) or set(assessment) != {
        "id",
        "revision",
        "sha256",
        "readiness",
        "requirement_count",
        "blocker_count",
    }:
        raise ValueError("Planning readiness assessment identity is invalid")
    if (
        not isinstance(assessment["id"], str)
        or not re.fullmatch(
            r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", assessment["id"]
        )
        or type(assessment["revision"]) is not int
        or assessment["revision"] < 1
        or assessment["readiness"] not in {"ready_for_human_review", "not_ready", "unknown"}
        or not (
            assessment["sha256"] is None
            or isinstance(assessment["sha256"], str)
            and _SHA.fullmatch(assessment["sha256"])
        )
        or not (
            assessment["requirement_count"] is None
            or type(assessment["requirement_count"]) is int
            and 0 <= assessment["requirement_count"] <= 64
        )
        or not (
            assessment["blocker_count"] is None
            or type(assessment["blocker_count"]) is int
            and 0 <= assessment["blocker_count"] <= 64
        )
    ):
        raise ValueError("Planning readiness assessment values are invalid")
    specification = value["specification"]
    if specification is not None and (
        not isinstance(specification, dict)
        or set(specification) != {"id", "revision", "sha256"}
        or not isinstance(specification["id"], str)
        or not specification["id"]
        or len(specification["id"]) > 200
        or type(specification["revision"]) is not int
        or specification["revision"] < 1
        or not isinstance(specification["sha256"], str)
        or not _SHA.fullmatch(specification["sha256"])
    ):
        raise ValueError("Planning readiness specification binding is invalid")
    binding = value["project_binding"]
    if binding is not None and (
        not isinstance(binding, dict)
        or set(binding) != {"target_id", "report_id", "source_digest", "currentness"}
        or not isinstance(binding["target_id"], str)
        or not binding["target_id"]
        or len(binding["target_id"]) > 64
        or not isinstance(binding["report_id"], str)
        or not _SHA.fullmatch(binding["report_id"])
        or not isinstance(binding["source_digest"], str)
        or not _SHA.fullmatch(binding["source_digest"])
        or binding["currentness"] not in {"current", "unknown"}
    ):
        raise ValueError("Planning readiness project binding is invalid")
    if value["currentness"] == "current":
        if (
            assessment["sha256"] is None
            or assessment["readiness"] == "unknown"
            or specification is None
            or binding is None
            or binding["currentness"] != "current"
            or target_id is None
            or binding["target_id"] != target_id
            or binding["source_digest"] != source_digest
        ):
            raise ValueError("Current Planning readiness does not bind this exact project source")
    return value


def _verify_package_provenance(manifest: dict[str, Any], selected_paths: set[str]) -> None:
    has_packages = "selected_packages" in manifest
    has_unmapped = "unmapped_selected_files" in manifest
    if not has_packages and not has_unmapped:
        return  # Older schema-1 packs predate package-component provenance.
    if not has_packages or not has_unmapped:
        raise ValueError("pack package provenance is incomplete")
    packages = manifest["selected_packages"]
    unmapped = manifest["unmapped_selected_files"]
    if not isinstance(packages, list) or len(packages) > MAX_FILES:
        raise ValueError("pack package provenance exceeds its bounds")
    if (
        not isinstance(unmapped, list)
        or len(unmapped) > MAX_FILES
        or any(not isinstance(path, str) or path not in selected_paths for path in unmapped)
    ):
        raise ValueError("pack unmapped selected-file provenance is invalid")
    assigned = list(unmapped)
    package_ids: set[str] = set()
    for package in packages:
        if not isinstance(package, dict) or set(package) != {
            "component_id",
            "name",
            "root",
            "manifests",
            "files",
        }:
            raise ValueError("pack selected package record is invalid")
        component_id = package["component_id"]
        root = package["root"]
        if (
            not isinstance(component_id, str)
            or not component_id
            or len(component_id) > 500
            or component_id in package_ids
            or not isinstance(package["name"], (str, type(None)))
            or isinstance(package["name"], str)
            and len(package["name"]) > 500
            or not isinstance(root, str)
            or not root
            or len(root) > 1000
            or root != "."
            and (
                PurePosixPath(root).is_absolute()
                or PurePosixPath(root).as_posix() != root
                or any(part in {"", ".", ".."} for part in PurePosixPath(root).parts)
            )
        ):
            raise ValueError("pack selected package identity is invalid")
        package_ids.add(component_id)
        manifests = package["manifests"]
        files = package["files"]
        if (
            not isinstance(manifests, list)
            or not manifests
            or any(
                not isinstance(path, str)
                or not path
                or PurePosixPath(path).is_absolute()
                or PurePosixPath(path).as_posix() != path
                or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
                for path in manifests
            )
            or len(set(manifests)) != len(manifests)
            or not isinstance(files, list)
            or not files
            or any(not isinstance(path, str) or path not in selected_paths for path in files)
            or len(set(files)) != len(files)
        ):
            raise ValueError("pack selected package files or manifests are invalid")
        assigned.extend(files)
    if len(assigned) != len(set(assigned)) or set(assigned) != selected_paths:
        raise ValueError("pack selected package files do not cover the exact selected file set")


def preview(
    root: Path,
    paths: list[str],
    *,
    capability_ids: list[str] | None = None,
    receipt_ids: list[str] | None = None,
    target_id: str | None = None,
    planning_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview only explicitly selected files and existing qualified owner metadata."""
    root = _root(root)
    if target_id is not None and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", target_id):
        raise ValueError("configured target identity is invalid")
    rows, omissions = _safe_paths(root, paths)
    capabilities = capability_ids or []
    receipts = receipt_ids or []
    if len(capabilities) > 32 or any(item not in _CAPABILITIES for item in capabilities):
        raise ValueError("select only registered Blueprint capabilities")
    if len(receipts) > 100 or any(
        not isinstance(item, str) or not _RECEIPT.fullmatch(item) for item in receipts
    ):
        raise ValueError("select exact retained maintenance receipt IDs")
    history = maintenance.history(root)
    receipt_by_id = {row.get("operation_id"): row for row in history["operations"]}
    selected_receipts = []
    for receipt_id in receipts:
        record = receipt_by_id.get(receipt_id)
        if record is None:
            raise ValueError("selected maintenance receipt is missing or unreadable")
        selected_receipts.append(record)
    catalog = maintenance.capability_catalog()
    registered = [row for row in catalog["capabilities"] if row["id"] in capabilities]
    available_receipts = history["operations"][:100]
    notice_paths = [
        row["path"]
        for row in rows
        if Path(row["path"]).name.lower()
        in {"license", "license.md", "copying", "notice", "notice.md"}
    ]
    captured_notices = []
    selected_text_evidence = []
    for row in rows:
        name = row["path"]
        content = read_bytes_bounded(root / name, MAX_FILE_BYTES, root=root).decode(
            "utf-8", errors="replace"
        )
        excerpt = {
            "path": name,
            "sha256": row["sha256"],
            "text_excerpt": content[:MAX_PREVIEW_CHARS],
            "truncated": len(content) > MAX_PREVIEW_CHARS,
            "qualification": "Selected text excerpt; remaining bytes are included in the pack.",
        }
        selected_text_evidence.append(excerpt)
        if name in notice_paths:
            captured_notices.append(
                {
                    **excerpt,
                    "qualification": "Captured selected notice text; not a legal assessment.",
                }
            )
    facts = discover_project(root)
    source = project_source_snapshot(root, facts, evidence_paths=[row["path"] for row in rows])
    selected_packages, unmapped_selected_files = _selected_packages(facts, rows)
    planning = _planning_readiness(
        planning_readiness,
        target_id=target_id,
        source_digest=source["source_digest"],
    )
    commit = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=all"))
    manifest = {
        "schema": SCHEMA,
        "target": {"name": root.name, **({"id": target_id} if target_id else {})},
        "source": {
            "base_commit": commit,
            "working_tree_changed": dirty,
            "source_digest": source["source_digest"],
            "snapshot_complete": source["complete"],
        },
        "files": rows,
        "omissions": omissions,
        "selected_packages": selected_packages,
        "unmapped_selected_files": unmapped_selected_files,
        "capabilities": registered,
        "registered_capabilities": catalog["capabilities"],
        "receipts": selected_receipts,
        "available_receipts": available_receipts,
        "available_receipts_count": len(history["operations"]),
        "available_receipts_truncated": len(history["operations"]) > len(available_receipts),
        "maintenance_history": {
            "status": history["history_status"],
            "unreadable_receipts": history["unreadable_receipts"],
        },
        "license_notice_evidence": captured_notices,
        "selected_text_evidence": selected_text_evidence,
        "license_notice_evidence_status": "selected-evidence-only"
        if notice_paths
        else "not-selected-or-unavailable",
        "qualification": [
            "This deterministic local pack is unsigned and is not an attestation or publication "
            "authorization.",
            "Capability and receipt entries are owner metadata; they do not establish "
            "license compatibility or legal permission.",
            "Only explicitly selected tracked regular text files are included; omissions remain "
            "listed.",
            "Dirty working-tree bytes are captured explicitly and are not represented as the base "
            "commit contents.",
            "Selected package groups follow native graph ownership; omitted package files and "
            "dependencies are not inferred.",
        ],
    }
    if planning is not None:
        manifest["planning_readiness"] = planning
        manifest["qualification"].append(
            "Planning readiness is a human-review qualification for this captured source "
            "snapshot, not release or publication approval."
        )
    return manifest


def build(
    root: Path,
    paths: list[str],
    *,
    capability_ids: list[str] | None = None,
    receipt_ids: list[str] | None = None,
    target_id: str | None = None,
    planning_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic ZIP bytes only after an exact before/after source check."""
    root = _root(root)
    before = preview(
        root,
        paths,
        capability_ids=capability_ids,
        receipt_ids=receipt_ids,
        target_id=target_id,
        planning_readiness=planning_readiness,
    )
    if before["omissions"]:
        raise ValueError("resolve every selected-file omission before building the pack")
    if not before["source"]["snapshot_complete"]:
        raise ValueError("source snapshot is incomplete; pack creation is refused")
    payloads: dict[str, bytes] = {}
    for row in before["files"]:
        data = read_bytes_bounded(root / row["path"], MAX_FILE_BYTES, root=root)
        if _sha(data) != row["sha256"]:
            raise ValueError("selected source changed during pack creation")
        payloads[row["path"]] = data
    after = preview(
        root,
        paths,
        capability_ids=capability_ids,
        receipt_ids=receipt_ids,
        target_id=target_id,
        planning_readiness=planning_readiness,
    )
    if _json(before) != _json(after):
        raise ValueError("project source or selected provenance changed during pack creation")
    manifest = before
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payloads[name])
        info = zipfile.ZipInfo("PROVENANCE.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, _json(manifest))
    data = output.getvalue()
    if len(data) > MAX_PACK_BYTES:
        raise ValueError("portable pack exceeds its size bound")
    return {
        "pack_id": _sha(data),
        "pack_sha256": _sha(data),
        "bytes": len(data),
        "zip_base64": __import__("base64").b64encode(data).decode("ascii"),
        "manifest": before,
    }


def verify_pack(
    data: bytes,
    expected_sha256: str,
    *,
    expected_target_id: str | None = None,
    allow_legacy_scoped_target: bool = False,
) -> dict[str, Any]:
    """Verify and reopen a portable pack without extracting it to a project."""
    if (
        len(data) > MAX_PACK_BYTES
        or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
        or _sha(data) != expected_sha256
    ):
        raise ValueError("pack checksum does not match the exact bytes")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if (
            not names
            or names[-1] != "PROVENANCE.json"
            or len(names) > MAX_FILES + 1
            or len(set(names)) != len(names)
        ):
            raise ValueError("pack layout is not a supported Blueprint release pack")
        manifest = json.loads(archive.read("PROVENANCE.json"))
        if manifest.get("schema") != SCHEMA or manifest.get("omissions"):
            raise ValueError("pack manifest is unsupported or incomplete")
        target = manifest.get("target")
        if not isinstance(target, dict):
            raise ValueError("pack target metadata is unsupported")
        has_target_id = isinstance(target.get("id"), str)
        if expected_target_id is not None and (
            (has_target_id and target.get("id") != expected_target_id)
            or (not has_target_id and not allow_legacy_scoped_target)
        ):
            raise ValueError("pack target identity does not match its configured artifact scope")
        if "planning_readiness" in manifest:
            _planning_readiness(
                manifest["planning_readiness"],
                target_id=target.get("id") if has_target_id else None,
                source_digest=manifest.get("source", {}).get("source_digest", ""),
            )
        rows = manifest.get("files")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
            raise ValueError("pack manifest exceeds the selected-file bound")
        selected: set[str] = set()
        total = 0
        for row in rows:
            name = row.get("path") if isinstance(row, dict) else None
            rel = PurePosixPath(name) if isinstance(name, str) else None
            if (
                not name
                or rel is None
                or rel.is_absolute()
                or rel.as_posix() != name
                or any(part in {"", ".", ".."} for part in rel.parts)
            ):
                raise ValueError("pack manifest contains an unsafe path")
            if (
                name in selected
                or not isinstance(row.get("bytes"), int)
                or not 0 <= row["bytes"] <= MAX_FILE_BYTES
            ):
                raise ValueError("pack manifest contains a duplicate path or invalid size")
            selected.add(name)
            total += row["bytes"]
            if total > MAX_TOTAL_BYTES or archive.getinfo(name).file_size != row["bytes"]:
                raise ValueError("portable content exceeds its manifest bounds")
        if set(names[:-1]) != selected:
            raise ValueError("pack file list does not match its manifest")
        _verify_package_provenance(manifest, selected)
        for row in rows:
            content = archive.read(row["path"])
            if _sha(content) != row["sha256"] or len(content) != row["bytes"]:
                raise ValueError("portable file content does not match its manifest")
    return {
        "pack_id": expected_sha256,
        "pack_sha256": expected_sha256,
        "bytes": len(data),
        "verified": True,
        "manifest": manifest,
        "target_binding": "manifest-target-id" if has_target_id else "legacy-private-journal-scope",
        "qualification": None
        if has_target_id
        else (
            "Legacy pack predates embedded target IDs. Its target scope is established by "
            "the exact private target-scoped retained journal; the immutable ZIP bytes and "
            "manifest remain unchanged."
        ),
    }
