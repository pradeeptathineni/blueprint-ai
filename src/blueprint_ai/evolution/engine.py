"""Plan, execute, verify, and exactly roll back project evolution transactions."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import hcl2
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from blueprint_ai import __version__
from blueprint_ai.config import load_yaml_mapping
from blueprint_ai.core import ProjectFacts
from blueprint_ai.discovery import discover_project
from blueprint_ai.safety import (
    MAX_MANIFEST_BYTES,
    atomic_write_text,
    controlled_env,
    read_text_bounded,
    run_process,
    run_process_bytes,
    safe_regular_file,
)
from blueprint_ai.sandbox import SandboxPolicy, SandboxUnavailable, execute
from blueprint_ai.support import backend_executable
from blueprint_ai.tooling import cached_image

from .catalog import (
    _MAINTAINER,
    _USES,
    OFFICIAL_ACTION_SHAS,
    TRANSFORMATIONS,
    RuntimePipelineStage,
    _dotnet_target_frameworks,
    _javascript_code_mask,
    _javascript_migration_syntax_mask,
    _next_experimental_ppr_residual,
    _next_font_residual,
    _next_unstable_cache_residual,
    _rust_editions,
)
from .models import (
    EvolutionChange,
    EvolutionPlan,
    EvolutionReport,
    EvolutionStep,
    EvolutionVerification,
    Precondition,
    ProjectState,
    ReviewDelta,
)

MAX_PLAN_FILES = 2_000
MAX_COMMAND_BYTES = 120_000
MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024
MAX_CHECKPOINT_FILE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class FileRecord:
    kind: str
    sha256: str
    size: int
    mode: int


def _safe_path(root: Path, relative: str, *, missing: bool = True) -> Path:
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe evolution path: {relative!r}")
    current = root.resolve()
    for part in path.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"evolution path crosses a symbolic link: {relative}")
    target = current / path.name
    if target.is_symlink():
        raise ValueError(f"evolution target is a symbolic link: {relative}")
    if not missing and not target.is_file():
        raise ValueError(f"evolution target is not a regular file: {relative}")
    return target


def _git_paths(root: Path) -> list[str]:
    result = run_process_bytes(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(root),
            "ls-files",
            "-co",
            "--exclude-standard",
            "-z",
        ],
        root,
        10,
        output_limit=64_000_000,
    )
    if result.returncode or result.timed_out or result.output_truncated:
        raise ValueError("complete Git project inventory is unavailable")
    values = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        value = raw.decode("utf-8", errors="strict")
        if value.startswith((".blueprint-ai/", ".blueprint-state/")):
            continue
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe evolution path: {value!r}")
        current = root.resolve()
        for part in path.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError(f"evolution path crosses a symbolic link: {value}")
        values.append(value)
    if len(values) > 250_000:
        raise ValueError("project exceeds the 250000-file evolution limit")
    return sorted(set(values))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _inventory(root: Path) -> dict[str, FileRecord]:
    records = {}
    for relative in _git_paths(root):
        target = root / relative
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            link = os.readlink(target).encode("utf-8", errors="surrogateescape")
            records[relative] = FileRecord(
                "symlink",
                hashlib.sha256(link).hexdigest(),
                len(link),
                stat.S_IMODE(info.st_mode),
            )
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"evolution inventory rejects non-regular project path: {relative}")
        digest, size = _hash_file(target)
        records[relative] = FileRecord("file", digest, size, stat.S_IMODE(info.st_mode))
    return records


def _current_record(root: Path, relative: str) -> FileRecord | None:
    """Read one bounded publication target without rehashing the entire repository."""
    target = _safe_path(root, relative)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"evolution inventory rejects non-regular project path: {relative}")
    digest, size = _hash_file(target)
    return FileRecord("file", digest, size, stat.S_IMODE(info.st_mode))


def _inventory_with_paths(
    root: Path,
    paths: list[str] | set[str],
    *,
    base: dict[str, FileRecord] | None = None,
) -> dict[str, FileRecord]:
    """Overlay explicit transaction paths, including Git-ignored paths, on an inventory."""
    records = dict(base) if base is not None else _inventory(root)
    for relative in sorted(set(paths)):
        record = _current_record(root, relative)
        if record is None:
            records.pop(relative, None)
        else:
            records[relative] = record
    return records


def _stage_inventory(root: Path) -> dict[str, FileRecord]:
    """Inventory every staged file, including tool-created normally ignored paths."""
    records: dict[str, FileRecord] = {}
    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        names.sort()
        files.sort()
        parent = Path(directory)
        for name in names:
            if (parent / name).is_symlink():
                raise ValueError(f"staged transformation created a directory symlink: {name}")
        for name in files:
            target = parent / name
            relative = target.relative_to(root).as_posix()
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"staged transformation created a non-regular path: {relative}")
            if info.st_size > MAX_CHECKPOINT_FILE_BYTES:
                raise ValueError(f"staged transformation created an oversized file: {relative}")
            total += info.st_size
            if total > MAX_CHECKPOINT_BYTES or len(records) >= 250_000:
                raise ValueError("staged transformation exceeded project size limits")
            digest, size = _hash_file(target)
            records[relative] = FileRecord("file", digest, size, stat.S_IMODE(info.st_mode))
    return records


def _fingerprint(records: dict[str, FileRecord]) -> str:
    digest = hashlib.sha256()
    for relative, record in sorted(records.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(record.kind.encode())
        digest.update(b"\0")
        digest.update(record.sha256.encode())
        digest.update(b"\0")
        digest.update(str(record.mode).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def project_fingerprint(root: Path) -> str:
    return _fingerprint(_inventory(root.resolve()))


def _git_head(root: Path) -> str | None:
    result = run_process(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        root,
        5,
        output_limit=4096,
    )
    value = result.stdout.strip()
    return value if not result.returncode and re.fullmatch(r"[a-f0-9]{40,64}", value) else None


def _git_branch(root: Path) -> str | None:
    result = run_process(
        ["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        root,
        5,
        output_limit=4096,
    )
    value = result.stdout.strip()
    return value if not result.returncode and value else None


def _git_index_sha256(root: Path) -> str:
    result = run_process_bytes(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files", "--stage", "-z"],
        root,
        10,
        output_limit=64_000_000,
    )
    if result.returncode or result.timed_out or result.output_truncated:
        raise ValueError("complete Git index identity is unavailable")
    return hashlib.sha256(result.stdout).hexdigest()


def _detected_versions(root: Path, facts: ProjectFacts) -> dict[str, str]:
    versions = {}
    for component in facts.graph.components:
        for dependency in component.dependencies:
            if dependency.requirement and dependency.name.lower() in {
                "react",
                "react-dom",
                "next",
                "django",
                "fastapi",
                "org.springframework.boot:spring-boot-starter",
            }:
                key = f"{dependency.ecosystem}:{dependency.name}"
                if component.root != ".":
                    key += f"@{component.root}"
                versions[key] = dependency.requirement
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            project = tomllib.loads(
                read_text_bounded(pyproject, MAX_MANIFEST_BYTES, root=root)
            ).get("project", {})
            if isinstance(project.get("requires-python"), str):
                versions["python"] = project["requires-python"]
        except (OSError, ValueError):
            pass
    go_mod = root / "go.mod"
    if go_mod.is_file():
        try:
            text = read_text_bounded(go_mod, MAX_MANIFEST_BYTES, root=root)
            if match := re.search(r"(?m)^go\s+([^\s]+)", text):
                versions["go"] = match.group(1)
        except (OSError, ValueError):
            pass
    rust_editions = _rust_editions(root, facts)
    if len(rust_editions) == 1:
        versions["rust-edition"] = rust_editions[0]
    elif rust_editions:
        versions["rust-editions"] = ";".join(rust_editions)
    dotnet_frameworks = _dotnet_target_frameworks(root, facts)
    if len(dotnet_frameworks) == 1:
        versions["dotnet-target-framework"] = dotnet_frameworks[0]
    elif dotnet_frameworks:
        versions["dotnet-target-frameworks"] = ";".join(dotnet_frameworks)
    return dict(sorted(versions.items()))


def _project_state(root: Path, facts: ProjectFacts, fingerprint: str) -> ProjectState:
    return ProjectState(
        project=facts.name,
        lifecycle=facts.graph.lifecycle,
        git_branch=facts.git_branch,
        git_head=_git_head(root),
        git_index_sha256=_git_index_sha256(root),
        git_clean=not facts.git_dirty,
        languages=sorted(facts.languages),
        frameworks=facts.frameworks,
        package_managers=facts.package_managers,
        versions=_detected_versions(root, facts),
        project_fingerprint=fingerprint,
    )


def _requested_target_versions(
    requested_targets: list[str] | None,
    target_versions: dict[str, str] | None,
) -> tuple[list[str], dict[str, str]]:
    targets: list[str] = []
    versions = dict(target_versions or {})
    for raw in requested_targets or []:
        recipe_id, separator, version = raw.partition("=")
        if not recipe_id or (separator and not version):
            raise ValueError(f"invalid evolution target request: {raw}")
        if separator:
            previous = versions.get(recipe_id)
            if previous is not None and previous != version:
                raise ValueError(f"conflicting requested targets for {recipe_id}")
            versions[recipe_id] = version
        targets.append(recipe_id)
    targets = list(dict.fromkeys(targets))
    if set(versions) - set(targets):
        raise ValueError("target version provided for an unrequested transformation")
    return targets, dict(sorted(versions.items()))


def _step_preconditions(
    root: Path,
    facts: ProjectFacts,
    paths: list[str],
    *,
    tool: str | None,
) -> list[Precondition]:
    preconditions = [
        Precondition(
            id="git-repository",
            status="passed",
            detail="Git repository inventory is available",
        ),
        Precondition(
            id="clean-worktree",
            status="passed" if not facts.git_dirty else "failed",
            detail=(
                "working tree is clean"
                if not facts.git_dirty
                else "working tree is dirty; apply requires --allow-dirty and the same fingerprint"
            ),
        ),
        Precondition(
            id="non-main-branch",
            status="failed" if facts.git_branch == "main" else "passed",
            detail=(
                "apply on main requires --allow-main"
                if facts.git_branch == "main"
                else f"isolated branch {facts.git_branch or '<detached>'}"
            ),
        ),
        Precondition(
            id="applicable",
            status="passed" if paths else "failed",
            detail=(
                f"{len(paths)} owned path(s) matched"
                if paths
                else "the requested current state was not detected"
            ),
        ),
    ]
    if tool:
        available = shutil.which(tool) is not None or any(
            (root / directory / tool).is_file() for directory in (".venv/bin", ".venv/Scripts")
        )
        preconditions.append(
            Precondition(
                id="tool-availability",
                status="passed" if available else "unknown",
                detail=(
                    f"{tool} executable discovered"
                    if available
                    else f"{tool} must be explicitly available on apply or in the selected image"
                ),
            )
        )
    return preconditions


def _receipt_is_authoritative(
    root: Path,
    operation_id: str,
    content: str,
    *,
    require_after_inventory: bool = False,
) -> bool:
    """Require the public receipt to match a seal held in Git-private operation state."""
    operation_state = _operations_root(root) / operation_id
    seal = operation_state / "receipt.sha256"
    inventory = operation_state / "after-inventory.json"
    if not safe_regular_file(seal, operation_state) or (
        require_after_inventory and not safe_regular_file(inventory, operation_state)
    ):
        return False
    try:
        expected = read_text_bounded(seal, 256, root=operation_state).strip()
    except (OSError, ValueError, UnicodeError):
        return False
    return (
        bool(re.fullmatch(r"[a-f0-9]{64}", expected))
        and expected == hashlib.sha256(content.encode()).hexdigest()
    )


def _seal_operation_receipt(
    root: Path, operation_id: str, content: str, *, overwrite: bool = True
) -> None:
    operation_state = _operations_root(root) / operation_id
    if not operation_state.is_dir() or operation_state.is_symlink():
        raise OSError("Git-private evolution operation state is unavailable")
    atomic_write_text(
        operation_state / "receipt.sha256",
        hashlib.sha256(content.encode()).hexdigest() + "\n",
        overwrite=overwrite,
        root=operation_state,
    )


def _accepted_manual_target(
    root: Path,
    records: dict[str, FileRecord],
    recipe_id: str,
    desired_target: str,
) -> bool:
    directory = _safe_path(root, ".blueprint-ai/operations")
    if not directory.is_dir() or directory.is_symlink():
        return False
    try:
        receipts = sorted(directory.glob("*.json"))
    except OSError:
        return False
    if len(receipts) > 200:
        return False
    for receipt in reversed(receipts):
        if receipt.is_symlink() or not receipt.is_file():
            continue
        try:
            content = read_text_bounded(receipt, MAX_MANIFEST_BYTES, root=root)
            data = json.loads(content)
        except (OSError, ValueError, UnicodeError):
            continue
        targets = data.get("target_versions", {}) if isinstance(data, dict) else {}
        operation_id = data.get("operation_id") if isinstance(data, dict) else None
        acceptance = data.get("manual_acceptance") if isinstance(data, dict) else None
        allowed = data.get("manual_completion_paths") if isinstance(data, dict) else None
        raw_changes = data.get("changes") if isinstance(data, dict) else None
        if (
            not isinstance(operation_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", operation_id)
            or receipt.stem != operation_id
            or not _receipt_is_authoritative(
                root, operation_id, content, require_after_inventory=True
            )
            or not isinstance(acceptance, dict)
            or not isinstance(acceptance.get("accepted_at"), str)
            or not isinstance(acceptance.get("evidence"), list)
            or not acceptance["evidence"]
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 500
                for item in acceptance["evidence"]
            )
            or not isinstance(allowed, list)
            or any(not isinstance(path, str) for path in allowed)
            or len(set(allowed)) != len(allowed)
            or not isinstance(raw_changes, list)
        ):
            continue
        try:
            changes = [EvolutionChange.model_validate(item) for item in raw_changes]
            if len({change.target for change in changes}) != len(changes):
                continue
            current = _inventory_with_paths(
                root,
                [change.target for change in changes],
                base=records,
            )
        except (OSError, TypeError, ValueError):
            continue
        if (
            isinstance(targets, dict)
            and data.get("version") == 3
            and data.get("kind") == "evolution"
            and data.get("status") == "accepted"
            and data.get("after_fingerprint") == _fingerprint(current)
            and targets.get(recipe_id) == desired_target
        ):
            return True
    return False


def plan_evolution(
    root: Path,
    requested_targets: list[str] | None = None,
    target_versions: dict[str, str] | None = None,
) -> EvolutionPlan:
    started = time.monotonic()
    root = root.expanduser().resolve()
    facts = discover_project(root)
    if not facts.is_git:
        raise ValueError("evolution planning requires a Git repository")
    records = _inventory(root)
    fingerprint = _fingerprint(records)
    detected = {
        recipe_id: runtime.detect(root, facts) for recipe_id, runtime in TRANSFORMATIONS.items()
    }
    candidates = [recipe_id for recipe_id, paths in detected.items() if paths]
    targets, resolved_targets = _requested_target_versions(requested_targets, target_versions)
    unknown = [target for target in targets if target not in TRANSFORMATIONS]
    if unknown:
        raise ValueError("unknown evolution target: " + ", ".join(unknown))
    steps: list[EvolutionStep] = []
    desired: list[str] = []
    risks: list[str] = []
    manual: list[str] = []

    def sealed_images(tool: str | None) -> dict[str, str]:
        if not tool:
            return {}
        return {
            backend: identity
            for backend in ("docker", "podman")
            if (identity := cached_image(tool, backend)) is not None
        }

    for recipe_id, runtime in TRANSFORMATIONS.items():
        if recipe_id not in targets:
            continue
        spec = runtime.spec
        paths = detected[recipe_id]
        if len(paths) > MAX_PLAN_FILES or sum(len(path) + 1 for path in paths) > MAX_COMMAND_BYTES:
            raise ValueError(f"{recipe_id}: selected scope exceeds the bounded plan size")
        desired_target = resolved_targets.get(recipe_id)
        if desired_target is not None and desired_target not in spec.allowed_targets:
            raise ValueError(
                f"{recipe_id}: unsupported target {desired_target}; choose one of "
                + ", ".join(spec.allowed_targets)
            )
        preconditions = _step_preconditions(root, facts, paths, tool=spec.tool)
        if (
            runtime.pipeline is not None
            and spec.maturity == "partial"
            and desired_target is not None
            and _accepted_manual_target(root, records, recipe_id, desired_target)
        ):
            steps.append(
                EvolutionStep(
                    id=f"step-{len(steps) + 1:02d}",
                    recipe_id=recipe_id,
                    operation="manual-acceptance-recorded",
                    kind="postcondition",
                    desired_target=desired_target,
                    sequence=len(steps) + 1,
                    mechanism=spec.mechanism,
                    provider=spec.provider,
                    tool=spec.tool,
                    tool_version=spec.tool_version,
                    recipe_version=spec.recipe_version,
                    authoritative_source=spec.authoritative_source,
                    files=[],
                    preconditions=preconditions,
                    verification=[],
                    tool_contract=spec.tool_contract,
                    image_identities=sealed_images(spec.tool),
                    reversible=True,
                    mutates=False,
                    status="noop",
                )
            )
            desired.append(f"{spec.desired_state}: {desired_target}")
            risks.extend(f"{recipe_id}: {item}" for item in spec.conflicts)
            continue
        if runtime.pipeline is not None and desired_target is not None and not paths:
            current_target = (
                runtime.current_target(root, facts) if runtime.current_target is not None else None
            )
            reached = current_target == desired_target
            requires_manual_acceptance = reached and spec.maturity == "partial"
            detail = (
                "target metadata and semantic scans are current; project build/type/test "
                "acceptance evidence remains required"
                if requires_manual_acceptance
                else f"authoritative metadata already proves target {desired_target}"
                if reached
                else (
                    f"authoritative metadata proves {current_target}, not requested "
                    f"{desired_target}"
                    if current_target is not None
                    else "authoritative current-version evidence or safe applicability is absent"
                )
            )
            step_id = f"step-{len(steps) + 1:02d}"
            steps.append(
                EvolutionStep(
                    id=step_id,
                    recipe_id=recipe_id,
                    operation=(
                        "project-verification"
                        if requires_manual_acceptance
                        else "already-target"
                        if reached
                        else "target-precondition"
                    ),
                    kind=(
                        "manual-boundary"
                        if requires_manual_acceptance or not reached
                        else "postcondition"
                    ),
                    desired_target=desired_target,
                    sequence=len(steps) + 1,
                    mechanism=(
                        "manual" if requires_manual_acceptance or not reached else spec.mechanism
                    ),
                    provider=spec.provider,
                    tool=spec.tool,
                    tool_version=spec.tool_version,
                    recipe_version=spec.recipe_version,
                    authoritative_source=spec.authoritative_source,
                    files=[],
                    preconditions=preconditions,
                    verification=[],
                    tool_contract=spec.tool_contract,
                    image_identities=sealed_images(spec.tool),
                    reversible=True,
                    mutates=False,
                    limitations=[] if reached and not requires_manual_acceptance else [detail],
                    status=(
                        "manual"
                        if requires_manual_acceptance
                        else "noop"
                        if reached
                        else "failed-precondition"
                    ),
                )
            )
            if not reached or requires_manual_acceptance:
                manual.append(f"{recipe_id}: {detail}")
            desired.append(f"{spec.desired_state}: {desired_target}")
            risks.extend(f"{recipe_id}: {item}" for item in spec.conflicts)
            continue
        if runtime.pipeline is not None and spec.target_required and desired_target is None:
            step_id = f"step-{len(steps) + 1:02d}"
            steps.append(
                EvolutionStep(
                    id=step_id,
                    recipe_id=recipe_id,
                    operation="target-decision",
                    kind="manual-boundary",
                    desired_target=None,
                    sequence=len(steps) + 1,
                    mechanism="manual",
                    provider=spec.provider,
                    tool=spec.tool,
                    tool_version=spec.tool_version,
                    recipe_version=spec.recipe_version,
                    authoritative_source=spec.authoritative_source,
                    files=[] if paths == ["."] else paths,
                    preconditions=preconditions,
                    verification=[],
                    postconditions=spec.postconditions,
                    tool_contract=spec.tool_contract,
                    image_identities=sealed_images(spec.tool),
                    reversible=True,
                    mutates=False,
                    limitations=spec.limitations,
                    status="manual" if paths else "noop",
                )
            )
            if paths:
                manual.append(
                    f"{recipe_id}: explicit target required; allowed: "
                    + ", ".join(spec.allowed_targets)
                )
            desired.append(spec.desired_state)
            risks.extend(f"{recipe_id}: {item}" for item in spec.conflicts)
            continue
        if (
            runtime.pipeline is not None
            and (desired_target is not None or not spec.target_required)
            and paths
        ):
            stages = runtime.pipeline(root, facts, paths, desired_target or "")
            if not stages:
                stage_id = f"step-{len(steps) + 1:02d}"
                steps.append(
                    EvolutionStep(
                        id=stage_id,
                        recipe_id=recipe_id,
                        operation="already-target",
                        kind="postcondition",
                        desired_target=desired_target,
                        sequence=len(steps) + 1,
                        mechanism=spec.mechanism,
                        provider=spec.provider,
                        tool=spec.tool,
                        tool_version=spec.tool_version,
                        recipe_version=spec.recipe_version,
                        authoritative_source=spec.authoritative_source,
                        files=[] if paths == ["."] else paths,
                        preconditions=preconditions,
                        verification=[],
                        tool_contract=spec.tool_contract,
                        image_identities=sealed_images(spec.tool),
                        reversible=True,
                        mutates=False,
                        status="noop",
                    )
                )
            else:
                local_ids: dict[str, str] = {}
                for stage in stages:
                    step_id = f"step-{len(steps) + 1:02d}"
                    local_ids[stage.id] = step_id
                    dependencies = [local_ids[item] for item in stage.depends_on if item]
                    status = stage.status
                    if status == "ready" and any(
                        next(item for item in steps if item.id == dependency).status
                        in {"manual", "blocked", "deferred", "failed-precondition"}
                        for dependency in dependencies
                    ):
                        status = "blocked"
                    steps.append(
                        EvolutionStep(
                            id=step_id,
                            recipe_id=recipe_id,
                            operation=stage.operation,
                            operation_target=stage.operation_target,
                            kind=stage.kind,
                            desired_target=desired_target,
                            sequence=len(steps) + 1,
                            depends_on=dependencies,
                            mechanism=(
                                "manual"
                                if stage.kind in {"manual-boundary", "residual-boundary"}
                                else spec.mechanism
                            ),
                            provider=spec.provider,
                            tool=stage.tool,
                            tool_version=spec.tool_version if stage.tool else None,
                            preview_command=stage.preview_command,
                            apply_command=stage.apply_command,
                            recipe_version=spec.recipe_version,
                            authoritative_source=spec.authoritative_source,
                            files=stage.files,
                            preconditions=preconditions,
                            verification=stage.verification,
                            postconditions=stage.postconditions,
                            tool_contract=spec.tool_contract,
                            execution_network=stage.execution_network,
                            image_identities=sealed_images(stage.tool),
                            ephemeral_paths=stage.ephemeral_paths,
                            manual_completion_paths=stage.manual_completion_paths,
                            rediscover=stage.rediscover,
                            mutates=stage.mutates,
                            reversible=spec.reversible,
                            limitations=sorted(set(spec.limitations + stage.limitations)),
                            status=status,
                        )
                    )
                    if status in {"manual", "deferred", "blocked", "failed-precondition"}:
                        manual.extend(f"{recipe_id}: {item}" for item in stage.limitations)
            desired.append(
                f"{spec.desired_state}: {desired_target}"
                if desired_target is not None
                else spec.desired_state
            )
            risks.extend(f"{recipe_id}: {item}" for item in spec.conflicts)
            continue
        if spec.implementation == "manual":
            step_status: Literal["ready", "blocked", "noop", "manual"] = "manual"
            manual.extend(f"{recipe_id}: {item}" for item in spec.limitations)
        elif not paths:
            step_status = "noop"
        else:
            step_status = "ready"
        step_id = f"step-{len(steps) + 1:02d}"
        steps.append(
            EvolutionStep(
                id=step_id,
                recipe_id=recipe_id,
                kind=(
                    "manual-boundary"
                    if spec.implementation == "manual"
                    else "builtin-edit"
                    if spec.implementation == "builtin"
                    else "native-command"
                ),
                sequence=len(steps) + 1,
                mechanism=spec.mechanism,
                provider=spec.provider,
                tool=spec.tool,
                tool_version=spec.tool_version,
                recipe_version=spec.recipe_version,
                authoritative_source=spec.authoritative_source,
                files=[] if paths == ["."] else paths,
                preconditions=preconditions,
                verification=spec.verification,
                postconditions=spec.postconditions,
                tool_contract=spec.tool_contract,
                image_identities=sealed_images(spec.tool),
                reversible=spec.reversible,
                limitations=spec.limitations,
                model_responsibility=(
                    "bounded planning only; never repository mutation"
                    if spec.model_allowed
                    else "none"
                ),
                agent_responsibility=(
                    "explicit residual implementation; unavailable in this release"
                    if spec.agent_allowed
                    else "none"
                ),
                status=step_status,
            )
        )
        desired.append(spec.desired_state)
        risks.extend(f"{recipe_id}: {item}" for item in spec.conflicts)
    if not targets:
        plan_status: Literal["inspection", "ready", "blocked", "noop"] = "inspection"
    elif any(step.status == "ready" for step in steps):
        plan_status = "ready"
    elif any(
        step.status in {"manual", "blocked", "deferred", "failed-precondition"} for step in steps
    ):
        plan_status = "blocked"
    else:
        plan_status = "noop"
    return EvolutionPlan(
        blueprint_ai_version=__version__,
        requested_targets=targets,
        target_versions=resolved_targets,
        current_state=_project_state(root, facts, fingerprint),
        desired_state=desired,
        candidates=candidates,
        steps=steps,
        risks=sorted(set(risks)),
        manual_boundaries=sorted(set(manual)),
        status=plan_status,
        metrics={
            "planning_duration_ms": round((time.monotonic() - started) * 1000),
            "files_in_inventory": len(records),
        },
    ).seal()


def load_plan(path: Path) -> EvolutionPlan:
    resolved = path.expanduser().resolve()
    try:
        payload = json.loads(read_text_bounded(resolved, MAX_MANIFEST_BYTES))
        plan = EvolutionPlan.model_validate(payload)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"invalid evolution plan: {exc}") from exc
    plan.validate_digest()
    return plan


def _changed(
    before: dict[str, FileRecord], after: dict[str, FileRecord]
) -> dict[str, tuple[FileRecord | None, FileRecord | None]]:
    return {
        path: (before.get(path), after.get(path))
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    }


def _checkpoint(root: Path, records: dict[str, FileRecord], destination: Path) -> None:
    total = sum(record.size for record in records.values())
    if total > MAX_CHECKPOINT_BYTES or any(
        record.size > MAX_CHECKPOINT_FILE_BYTES for record in records.values()
    ):
        raise ValueError(
            "project exceeds the 512 MiB total or 64 MiB per-file evolution checkpoint limit"
        )
    for relative, record in records.items():
        if record.kind != "file":
            raise ValueError(f"unsupported checkpoint entry: {relative}")
        source = _safe_path(root, relative, missing=False)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def _restore_published(
    root: Path,
    checkpoint: Path,
    before: dict[str, FileRecord],
    published_after: dict[str, FileRecord],
    published: set[str],
) -> None:
    """Restore only Blueprint-published paths and preserve concurrent unrelated edits."""
    conflicts = []
    restore = []
    for relative in sorted(published):
        observed = _current_record(root, relative)
        old = before.get(relative)
        new = published_after.get(relative)
        if observed == old:
            continue
        if observed != new:
            conflicts.append(relative)
            continue
        restore.append((relative, old))
    for relative, old in restore:
        target = _safe_path(root, relative)
        if old is None:
            target.unlink()
        else:
            shutil.copy2(checkpoint / relative, target, follow_symlinks=False)
            os.chmod(target, old.mode)
    failed = [relative for relative, old in restore if _current_record(root, relative) != old]
    if conflicts or failed:
        raise OSError(
            "automatic rollback preserved concurrent edits on: "
            + ", ".join(sorted(set(conflicts + failed)))
        )


@contextmanager
def _operation_lock(root: Path):
    lock_root = _git_state_root(root)
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(lock_root, 0o700)
    lock = lock_root / "evolution.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("another Blueprint AI evolution transaction is active") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _git_state_root(root: Path) -> Path:
    result = run_process(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-dir"],
        root,
        5,
        output_limit=16_384,
    )
    if result.returncode or result.timed_out or result.output_truncated:
        raise ValueError("Git private state directory is unavailable")
    git_directory = Path(result.stdout.strip()).resolve()
    if not git_directory.is_dir():
        raise ValueError("Git private state directory is invalid")
    state = git_directory / "blueprint-ai"
    for parent in (git_directory, *git_directory.parents):
        if parent.is_symlink():
            raise ValueError("Git private state crosses a symbolic link")
    if os.path.lexists(state) and (state.is_symlink() or not state.is_dir()):
        raise ValueError("Git private Blueprint AI state is not a regular directory")
    return state


def _operations_root(root: Path, *, create: bool = False) -> Path:
    operations = _git_state_root(root) / "operations"
    if os.path.lexists(operations) and (operations.is_symlink() or not operations.is_dir()):
        raise ValueError("Git private operation state is not a regular directory")
    if create:
        operations.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(operations, 0o700)
    return operations


def _version_value(text: str) -> str | None:
    match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)", text)
    return match.group(1) if match else None


def _tool_command(
    root: Path,
    tool: str,
    policy: SandboxPolicy,
    *,
    requested: str | None = None,
) -> str:
    executable_name = requested or tool
    if policy.backend != "host" and not (
        policy.backend == "auto" and policy.trusted and not policy.image
    ):
        return backend_executable(tool, executable_name, policy.backend)
    if executable := shutil.which(executable_name):
        return executable
    if executable_name == "python" and Path(sys.executable).is_file():
        return sys.executable
    for directory in (".venv/bin", ".venv/Scripts"):
        candidate = root / directory / executable_name
        if safe_regular_file(candidate, root) and os.access(candidate, os.X_OK):
            return str(candidate)
    raise ValueError(f"{executable_name} executable is unavailable")


def _execution_env(directory: Path) -> dict[str, str]:
    caches = directory / "caches"
    caches.mkdir()
    return controlled_env(
        {
            "HOME": str(directory),
            "XDG_CACHE_HOME": str(caches),
            "RUFF_CACHE_DIR": str(caches / "ruff"),
            "GOPATH": str(caches / "go"),
            "GOCACHE": str(caches / "go-build"),
            "GOMODCACHE": str(caches / "go" / "pkg" / "mod"),
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
            "CARGO_HOME": str(caches / "cargo-home"),
            "CARGO_TARGET_DIR": str(caches / "cargo-target"),
            "CARGO_NET_OFFLINE": "true",
            "DOTNET_CLI_HOME": str(caches / "dotnet"),
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            "NUGET_PACKAGES": str(caches / "nuget"),
            "npm_config_cache": str(caches / "npm"),
            "npm_config_ignore_scripts": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "NEXT_TELEMETRY_DISABLED": "1",
            "CHECKPOINT_DISABLE": "1",
            "TF_IN_AUTOMATION": "1",
        }
    )


def _run(
    root: Path,
    command: list[str],
    policy: SandboxPolicy,
    environment: dict[str, str],
    *,
    tool: str,
    tool_root: Path | None = None,
    success_exit_codes: set[int] | None = None,
) -> tuple[EvolutionVerification, str]:
    execution_command = list(command)
    execution_command[0] = _tool_command(
        tool_root or root, tool, policy, requested=execution_command[0]
    )
    run_environment = dict(environment)
    if policy.network == "unrestricted":
        if tool == "cargo":
            run_environment["CARGO_NET_OFFLINE"] = "false"
        elif tool == "go":
            run_environment["GOPROXY"] = "https://proxy.golang.org"
            run_environment["GOSUMDB"] = "sum.golang.org"
    started = time.monotonic()
    try:
        execution = execute(execution_command, root, policy, tool=tool, host_env=run_environment)
    except (OSError, ValueError, SandboxUnavailable) as exc:
        return (
            EvolutionVerification(
                id=f"{tool}/execution",
                status="tool_error",
                command=execution_command,
                detail=str(exc)[:1000],
                duration_ms=round((time.monotonic() - started) * 1000),
            ),
            "",
        )
    result = execution.result
    output = (result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr)[
        :1_000_000
    ]
    # External tools often echo checkout and isolated-cache paths. They are not useful
    # migration evidence and can disclose operator-specific filesystem details.
    output = output.replace(str(root), ".")
    for key in (
        "HOME",
        "XDG_CACHE_HOME",
        "RUFF_CACHE_DIR",
        "GOPATH",
        "GOCACHE",
        "GOMODCACHE",
        "CARGO_HOME",
        "CARGO_TARGET_DIR",
        "DOTNET_CLI_HOME",
        "NUGET_PACKAGES",
        "npm_config_cache",
    ):
        if value := environment.get(key):
            output = output.replace(value, "<isolated-cache>")
    accepted = success_exit_codes or {0}
    anomalies = []
    if execution.evidence.oom_killed:
        anomalies.append("sandbox was OOM-killed")
    if result.timed_out:
        anomalies.append("execution timed out")
    if result.output_truncated:
        anomalies.append("bounded output was truncated")
    okay = result.returncode in accepted and not anomalies
    detail = output[-1000:] or f"exit {result.returncode}"
    if anomalies:
        detail = "; ".join(anomalies) + "; " + detail
    return (
        EvolutionVerification(
            id=f"{tool}/execution",
            status="passed" if okay else "failed",
            command=execution_command,
            detail=detail,
            duration_ms=round((time.monotonic() - started) * 1000),
            sandbox=execution.evidence.model_dump(mode="json"),
        ),
        output,
    )


def _verify_version(
    root: Path,
    tool: str,
    supported: str,
    policy: SandboxPolicy,
    environment: dict[str, str],
    *,
    tool_root: Path | None = None,
    executable: str | None = None,
) -> EvolutionVerification:
    requested = executable or tool
    flag = "version" if requested == "go" else "--version"
    verification, output = _run(
        root, [requested, flag], policy, environment, tool=tool, tool_root=tool_root
    )
    verification.id = f"{tool}/version"
    if verification.status != "passed":
        return verification
    raw = _version_value(output)
    try:
        valid = bool(raw and Version(raw) in SpecifierSet(supported))
    except (InvalidVersion, ValueError):
        valid = False
    verification.status = "passed" if valid else "failed"
    verification.detail = (
        f"observed {raw}; required {supported}"
        if raw
        else f"could not parse a version; required {supported}"
    )
    return verification


def _step_policy(step: EvolutionStep, policy: SandboxPolicy, *, writable: bool) -> SandboxPolicy:
    """Apply the stricter operator/contract resource limit to one stage."""
    updates: dict[str, object] = {"writable": writable}
    if step.execution_network == "required" and (
        step.tool_contract is None
        or step.tool_contract.network not in {"acquisition-only", "required"}
    ):
        raise ValueError("stage requests network outside its authoritative tool contract")
    if step.tool_contract is not None:
        updates.update(
            {
                "timeout": step.tool_contract.timeout_seconds,
                "memory_mb": min(policy.memory_mb, step.tool_contract.memory_mb),
                "cpus": min(policy.cpus, step.tool_contract.cpus),
                "pids": min(policy.pids, step.tool_contract.pids),
                "scratch_mb": min(policy.scratch_mb, step.tool_contract.scratch_mb),
                "file_size_mb": min(policy.file_size_mb, step.tool_contract.file_size_mb),
                "output_bytes": min(policy.output_bytes, step.tool_contract.output_bytes),
            }
        )
        updates["timeout"] = min(policy.timeout, step.tool_contract.timeout_seconds)
        if step.execution_network != "required" and step.tool_contract.network in {
            "none",
            "acquisition-only",
        }:
            updates["network"] = "none"
            updates["authorize_network"] = False
        if policy.backend != "host" and step.tool_contract.image:
            updates["image"] = step.tool_contract.image
    return policy.model_copy(update=updates)


def _within_authoritative_write_scope(path: str, scopes: list[str]) -> bool:
    """Match a planned path against the contract's portable POSIX glob vocabulary."""
    candidate = PurePosixPath(path)
    return any(
        candidate.match(scope)
        or (scope.startswith("**/") and candidate.match(scope.removeprefix("**/")))
        for scope in scopes
    )


def _step_provenance(step: EvolutionStep, reports: list[EvolutionVerification]) -> dict[str, str]:
    """Flatten the sealed supply-chain contract into durable report evidence."""
    contract = step.tool_contract
    values = {
        "recipe_id": step.recipe_id,
        "operation": step.operation,
        "target": step.desired_target or "",
        "provider": step.provider,
        "mechanism": step.mechanism,
        "recipe_version": step.recipe_version,
        "tool_version": step.tool_version or "built-in",
        "postconditions": ",".join(item.id for item in step.postconditions),
    }
    if contract is not None:
        observed_image = next(
            (
                str((item.sandbox or {}).get("image_id", ""))
                for item in reports
                if item.id == f"{step.tool}/version"
            ),
            "",
        )
        values.update(
            {
                "tool_id": contract.tool_id,
                "runner": contract.runner_package or "",
                "runner_version": contract.runner_version or "",
                "runner_integrity": contract.runner_integrity or "",
                "migration_recipe": contract.recipe,
                "migration_recipe_version": contract.recipe_version,
                "tool_license": contract.tool_license,
                "recipe_license": contract.recipe_license,
                "distribution": contract.distribution,
                "redistribution_permitted": str(contract.redistribution_permitted).lower(),
                "image": contract.image or "",
                "image_digest": contract.image_digest or "",
                "sealed_image_ids": ",".join(
                    f"{backend}={identity}"
                    for backend, identity in sorted(step.image_identities.items())
                ),
                "resolved_image_id": observed_image,
                "network": contract.network,
                "allowed_destinations": ",".join(contract.allowed_destinations),
            }
        )
    return values


def _diff(relative: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def _staged_diff(checkpoint: Path, workspace: Path, paths: list[str]) -> str:
    return "".join(
        _diff(
            relative,
            (
                read_text_bounded(checkpoint / relative, MAX_CHECKPOINT_FILE_BYTES, root=checkpoint)
                if (checkpoint / relative).is_file()
                else ""
            ),
            (
                read_text_bounded(workspace / relative, MAX_CHECKPOINT_FILE_BYTES, root=workspace)
                if (workspace / relative).is_file()
                else ""
            ),
        )
        for relative in paths
    )


def _builtin_preview(root: Path, step: EvolutionStep) -> tuple[dict[str, str], dict[str, str]]:
    runtime = TRANSFORMATIONS[step.recipe_id]
    if step.operation == "transform":
        if runtime.transform is None:
            raise ValueError(f"{step.recipe_id}: built-in transform is unavailable")
        rendered = runtime.transform(root, step.files)
    else:
        transform = runtime.operations.get(step.operation)
        if transform is None:
            raise ValueError(f"{step.recipe_id}: canonical operation is unavailable")
        rendered = transform(root, step.files, step.operation_target or step.desired_target)
    diffs = {
        path: _diff(
            path,
            (
                read_text_bounded(root / path, MAX_MANIFEST_BYTES, root=root)
                if (root / path).is_file()
                else ""
            ),
            content,
        )
        for path, content in rendered.items()
    }
    return rendered, diffs


def _selected_value(text: str, selector: str) -> str | None:
    format_name, separator, expression = selector.partition(":")
    if not separator or not expression:
        return None
    if format_name == "toml":
        value: object = tomllib.loads(text)
        for part in expression.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value if isinstance(value, str) else None
    if format_name == "json":
        value = json.loads(text)
        for part in expression.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value if isinstance(value, str) else None
    if format_name == "xml":
        document = ET.fromstring(text)
        values = [
            (element.text or "").strip()
            for element in document.iter()
            if element.tag.rsplit("}", 1)[-1] == expression
        ]
        return values[0] if len(values) == 1 else None
    return None


def _evaluate_postcondition(root: Path, condition) -> EvolutionVerification:
    try:
        if condition.kind in {"path-present", "path-absent"}:
            present = {
                relative: _safe_path(root, relative).is_file() for relative in condition.paths
            }
            failures = [
                path
                for path, exists in present.items()
                if exists != (condition.kind == "path-present")
            ]
            if failures:
                expectation = "present" if condition.kind == "path-present" else "absent"
                raise ValueError(f"migration paths are not {expectation}: " + ", ".join(failures))
            return EvolutionVerification(id=condition.id, status="passed", detail=condition.detail)
        texts: dict[str, str] = {}
        absence_satisfies = condition.kind in {"forbidden-pattern", "no-manual-markers"}
        for relative in condition.paths:
            target = _safe_path(root, relative)
            if absence_satisfies and not target.is_file():
                continue
            texts[relative] = read_text_bounded(
                _safe_path(root, relative, missing=False), MAX_MANIFEST_BYTES, root=root
            )
        searchable = (
            {path: _javascript_code_mask(text) for path, text in texts.items()}
            if condition.selector == "javascript-code"
            else {path: _javascript_migration_syntax_mask(text) for path, text in texts.items()}
            if condition.selector == "javascript-migration-syntax"
            else {
                path: json.dumps(document.get("scripts", {}) if isinstance(document, dict) else {})
                for path, text in texts.items()
                for document in [json.loads(text)]
            }
            if condition.selector == "json-scripts"
            else {
                path: json.dumps(
                    {
                        section: document.get(section, {})
                        for section in ("dependencies", "devDependencies")
                    }
                    if isinstance(document, dict)
                    else {}
                )
                for path, text in texts.items()
                for document in [json.loads(text)]
            }
            if condition.selector == "json-dependencies"
            else {
                path: (
                    "removed-next-experimental-ppr" if _next_experimental_ppr_residual(text) else ""
                )
                for path, text in texts.items()
            }
            if condition.selector == "next-experimental-ppr"
            else {
                path: "removed-next-font" if _next_font_residual(text) else ""
                for path, text in texts.items()
            }
            if condition.selector == "next-font"
            else {
                path: "removed-next-cache-api" if _next_unstable_cache_residual(text) else ""
                for path, text in texts.items()
            }
            if condition.selector == "next-unstable-cache"
            else texts
        )
        if condition.kind == "forbidden-pattern":
            if not condition.pattern:
                raise ValueError("forbidden-pattern postcondition has no pattern")
            failures = [
                path for path, text in searchable.items() if re.search(condition.pattern, text)
            ]
            if failures:
                raise ValueError("forbidden migration surface remains in: " + ", ".join(failures))
        elif condition.kind == "required-pattern":
            if not condition.pattern:
                raise ValueError("required-pattern postcondition has no pattern")
            failures = [
                path for path, text in searchable.items() if not re.search(condition.pattern, text)
            ]
            if failures:
                raise ValueError(
                    "required migration surface is absent from: " + ", ".join(failures)
                )
        elif condition.kind == "exact-value":
            if not condition.selector or condition.expected is None:
                raise ValueError("exact-value postcondition is incomplete")
            failures = [
                path
                for path, text in texts.items()
                if _selected_value(text, condition.selector) != condition.expected
            ]
            if failures:
                raise ValueError(
                    "requested manifest value was not reached in: " + ", ".join(failures)
                )
        elif condition.kind == "no-manual-markers":
            pattern = condition.pattern or r"(?m)\b(?:TODO|FIXME)\b"
            failures = [path for path, text in texts.items() if re.search(pattern, text)]
            if failures:
                raise ValueError("migration manual marker remains in: " + ", ".join(failures))
        elif condition.kind in {"command", "no-new-findings"}:
            raise ValueError(f"{condition.kind} is enforced by its dedicated verification gate")
        else:
            raise ValueError(f"unknown postcondition kind: {condition.kind}")
    except (
        ET.ParseError,
        json.JSONDecodeError,
        OSError,
        ValueError,
        tomllib.TOMLDecodeError,
    ) as exc:
        return EvolutionVerification(id=condition.id, status="failed", detail=str(exc)[:1000])
    return EvolutionVerification(id=condition.id, status="passed", detail=condition.detail)


def _builtin_verify(root: Path, step: EvolutionStep) -> list[EvolutionVerification]:
    checks = []
    try:
        if step.recipe_id == "container/maintainer-to-oci-label":
            for relative in step.files:
                text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
                if any(_MAINTAINER.match(line) for line in text.splitlines()):
                    raise ValueError(f"{relative}: deprecated MAINTAINER remains")
                if "org.opencontainers.image.authors=" not in text:
                    raise ValueError(f"{relative}: OCI authors label is missing")
        elif step.recipe_id == "github-actions/pin-official-actions":
            for relative in step.files:
                load_yaml_mapping(root / relative, root, label=relative)
                text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
                if any(
                    (match := _USES.match(line))
                    and match.group("reference").lower() in OFFICIAL_ACTION_SHAS
                    for line in text.splitlines()
                ):
                    raise ValueError(f"{relative}: eligible mutable action reference remains")
        elif step.recipe_id == "python/ruff-pyupgrade":
            for relative in step.files:
                tree = ast.parse(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
                if Path(relative).name != "__init__.py":
                    continue
                used = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module in {
                        "typing",
                        "typing_extensions",
                    }:
                        aliases = node.names
                    elif isinstance(node, ast.Import):
                        aliases = [
                            alias
                            for alias in node.names
                            if alias.name in {"typing", "typing_extensions"}
                        ]
                    else:
                        continue
                    if any(
                        alias.name != "*" and alias.asname is None and alias.name not in used
                        for alias in aliases
                    ):
                        raise ValueError(
                            f"{relative}: typing name is neither used nor an explicit export"
                        )
        elif step.recipe_id == "terraform/native-format":
            for relative in step.files:
                hcl2.loads(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
    except (OSError, ValueError, SyntaxError) as exc:
        checks.append(
            EvolutionVerification(
                id=f"{step.recipe_id}/builtin",
                status="failed",
                detail=str(exc)[:1000],
            )
        )
    else:
        checks.append(
            EvolutionVerification(
                id=f"{step.recipe_id}/builtin",
                status="passed",
                detail="bounded structural verification passed",
            )
        )
    return checks


def _review(root: Path) -> tuple[set[str], str | None, list[str]]:
    try:
        from blueprint_ai.engine import make_context, review

        context, _settings = make_context(root, model_mode="off", trust_project_executables=False)
        report = review(context)
        incomplete = sorted(
            f"{result.blueprint}:{result.status}"
            for result in report.results
            if result.status not in {"passed", "not_applicable"}
        )
        return {item.fingerprint for item in report.active_findings}, None, incomplete
    except Exception as exc:  # review failure is reportable evidence, never hidden success
        return set(), f"{type(exc).__name__}: {str(exc)[:500]}", []


def _validate_fresh_plan(
    root: Path, plan: EvolutionPlan
) -> tuple[ProjectFacts, dict[str, FileRecord]]:
    if plan.blueprint_ai_version != __version__:
        raise ValueError("evolution plan was produced by a different Blueprint AI version")
    plan.validate_digest()
    records = _inventory(root)
    current = _fingerprint(records)
    if current != plan.current_state.project_fingerprint:
        raise ValueError("stale evolution plan: project fingerprint changed after planning")
    fresh = plan_evolution(root, plan.requested_targets, plan.target_versions)
    planned = plan.model_dump(mode="json", exclude={"created_at", "metrics", "plan_sha256"})
    resolved = fresh.model_dump(mode="json", exclude={"created_at", "metrics", "plan_sha256"})
    if planned != resolved:
        raise ValueError("evolution plan no longer resolves to the canonical catalog")
    return discover_project(root), records


def _assert_git_identity(root: Path, state: ProjectState, context: str) -> None:
    """Fail if branch, HEAD, or index identity moved during a sealed transaction."""
    if _git_branch(root) != state.git_branch:
        raise RuntimeError(f"Git branch changed {context}")
    if _git_head(root) != state.git_head:
        raise RuntimeError(f"Git HEAD changed {context}")
    if _git_index_sha256(root) != state.git_index_sha256:
        raise RuntimeError(f"Git index changed {context}")


def _stage_signature(stage: EvolutionStep | RuntimePipelineStage) -> tuple[object, ...]:
    """Comparable execution authority for planned and regenerated pipeline stages."""
    return (
        stage.operation,
        stage.operation_target,
        stage.kind,
        tuple(sorted(stage.files)),
        None if stage.kind == "builtin-edit" else stage.tool,
        tuple(stage.apply_command),
    )


def _revalidate_downstream(
    workspace: Path,
    plan: EvolutionPlan,
    ready: list[EvolutionStep],
    position: int,
    discovered: ProjectFacts,
) -> int:
    """Regenerate the remaining mutable suffix from rediscovered authoritative state."""
    step = ready[position]
    runtime = TRANSFORMATIONS[step.recipe_id]
    if runtime.pipeline is None or step.desired_target is None:
        return len(discovered.graph.components)
    original_scope = sorted(
        {
            relative
            for item in plan.steps
            if item.recipe_id == step.recipe_id
            for relative in item.files
            if relative != "."
        }
    )
    detected = runtime.detect(workspace, discovered)
    outside = set(detected) - set(original_scope)
    if outside:
        raise RuntimeError(
            "rediscovery expanded the sealed migration scope: " + ", ".join(sorted(outside)[:20])
        )
    regenerated = runtime.pipeline(
        workspace,
        discovered,
        detected or original_scope,
        step.desired_target,
    )
    current = next(
        (
            index
            for index, stage in enumerate(regenerated)
            if stage.operation == step.operation and stage.operation_target == step.operation_target
        ),
        None,
    )
    if current is not None:
        regenerated = regenerated[current + 1 :]
    actual = [
        _stage_signature(stage)
        for stage in regenerated
        if stage.status == "ready" and stage.mutates
    ]
    expected = [
        _stage_signature(item)
        for item in ready[position + 1 :]
        if item.recipe_id == step.recipe_id and item.mutates
    ]
    if actual != expected:
        raise RuntimeError("rediscovery invalidated the sealed downstream pipeline")
    return len(discovered.graph.components)


def _verify_pipeline_idempotency(
    root: Path,
    workspace: Path,
    temporary_path: Path,
    ready: list[EvolutionStep],
    policy: SandboxPolicy,
    environment: dict[str, str],
    reports: list[EvolutionVerification],
) -> None:
    """Replay every mutable pipeline stage on the final state and require an exact no-op."""
    replay = temporary_path / "idempotency"
    shutil.copytree(workspace, replay)
    for step in ready:
        runtime = TRANSFORMATIONS[step.recipe_id]
        if runtime.pipeline is None or not step.mutates:
            continue
        spec = runtime.spec
        if (
            step.operation_target is not None
            and step.operation_target in spec.allowed_targets
            and step.operation_target != step.desired_target
        ):
            continue
        verification = EvolutionVerification(
            id=f"{step.recipe_id}/{step.operation}/idempotency",
            status="passed",
            detail="final-state replay produced no relevant changes",
        )
        if step.kind == "builtin-edit":
            rendered, _diffs = _builtin_preview(replay, step)
            if rendered:
                verification.status = "failed"
                verification.detail = (
                    "deterministic final-state replay still proposed changes: "
                    + ", ".join(sorted(rendered)[:20])
                )
        elif step.kind in {"native-command", "established-codemod"}:
            if not step.tool or not step.apply_command:
                raise RuntimeError(f"{step.recipe_id}: idempotency command is incomplete")
            before_replay = _stage_inventory(replay)
            verification, _output = _run(
                replay,
                step.apply_command,
                _step_policy(step, policy, writable=True),
                environment,
                tool=step.tool,
                tool_root=root,
                success_exit_codes=set(step.tool_contract.success_exit_codes)
                if step.tool_contract is not None
                else None,
            )
            verification.id = f"{step.recipe_id}/{step.operation}/idempotency"
            if verification.status == "passed":
                for ephemeral in step.ephemeral_paths:
                    target = _safe_path(replay, ephemeral)
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    elif target.is_file():
                        target.unlink()
                changed = _changed(before_replay, _stage_inventory(replay))
                if changed:
                    verification.status = "failed"
                    verification.detail = (
                        "authoritative final-state replay still changed: "
                        + ", ".join(sorted(changed)[:20])
                    )
                else:
                    verification.detail = "authoritative final-state replay was an exact no-op"
        else:
            raise RuntimeError(f"{step.recipe_id}: unsupported mutable pipeline stage")
        reports.append(verification)
        if verification.status != "passed":
            raise RuntimeError(verification.detail)


def apply_evolution(
    root: Path,
    plan: EvolutionPlan,
    *,
    dry_run: bool = False,
    allow_dirty: bool = False,
    allow_main: bool = False,
    policy: SandboxPolicy | None = None,
    run_blueprint_review: bool = True,
    report_failures: bool = False,
) -> EvolutionReport:
    root = root.expanduser().resolve()
    started = time.monotonic()
    operation_id = uuid.uuid4().hex
    manifest_relative = f".blueprint-ai/operations/{operation_id}.json"
    default_policy = SandboxPolicy()
    execution_policy = policy or default_policy
    with _operation_lock(root):
        facts, before = _validate_fresh_plan(root, plan)
        ready = [step for step in plan.steps if step.status == "ready"]
        unresolved = [
            step
            for step in plan.steps
            if step.status in {"manual", "blocked", "deferred", "failed-precondition"}
        ]
        limitations = sorted({item for step in plan.steps for item in step.limitations})
        if (
            unresolved
            and not ready
            and not any(
                step.status == "manual" and step.manual_completion_paths for step in unresolved
            )
        ):
            raise ValueError("plan contains manual/deferred steps and cannot be applied")
        if not ready and not unresolved:
            return EvolutionReport(
                plan_sha256=plan.plan_sha256,
                status="noop",
                before=plan.current_state,
                plan=plan,
                after=plan.current_state,
                limitations=limitations,
                metrics={"duration_ms": round((time.monotonic() - started) * 1000)},
            )
        if not dry_run and facts.git_dirty and not allow_dirty:
            raise ValueError(
                "working tree is dirty; pass --allow-dirty only after reviewing its state"
            )
        if not dry_run and facts.git_branch == "main" and not allow_main:
            raise ValueError("refusing to mutate main; use a branch/worktree or pass --allow-main")
        if any(step.tool for step in ready):
            if not execution_policy.trusted or (not dry_run and not execution_policy.writable):
                raise ValueError(
                    "tool-driven evolution requires explicit trusted execution"
                    + (" with a writable target" if not dry_run else "")
                )
        contracts = [
            step.tool_contract for step in ready if step.tool and step.tool_contract is not None
        ]
        if any(
            step.execution_network == "required"
            or step.tool_contract is not None
            and step.tool_contract.network == "required"
            for step in ready
            if step.tool
        ):
            if execution_policy.network != "unrestricted" or not execution_policy.authorize_network:
                raise ValueError("this migration tool requires explicit network authorization")
        images = {contract.image for contract in contracts if contract.image}
        if execution_policy.backend != "host" and images:
            if execution_policy.image is not None and (
                len(images) != 1 or execution_policy.image not in images
            ):
                raise ValueError(
                    "an operator-selected OCI image cannot override the sealed per-step "
                    "authoritative tool images"
                )
        before_findings: set[str] = set()
        before_review_error = None
        before_incomplete: list[str] = []
        if run_blueprint_review and not dry_run:
            before_findings, before_review_error, before_incomplete = _review(root)
        reports: list[EvolutionVerification] = []
        diffs: dict[str, str] = {}
        recipe_by_path: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="blueprint-evolution-") as temporary:
            temporary_path = Path(temporary).resolve()
            checkpoint = temporary_path / "before"
            checkpoint.mkdir()
            _checkpoint(root, before, checkpoint)
            workspace = temporary_path / "workspace"
            workspace.mkdir()
            _checkpoint(root, before, workspace)
            environment = _execution_env(temporary_path)
            transaction_ephemeral = sorted(
                {path for step in ready for path in step.ephemeral_paths}
            )
            published: set[str] = set()
            published_after: dict[str, FileRecord] = {}
            try:
                checked_tools: set[tuple[str, str]] = set()
                completed_steps: set[str] = set()
                for position, step in enumerate(ready):
                    if any(dependency not in completed_steps for dependency in step.depends_on):
                        raise RuntimeError(
                            f"{step.recipe_id}: dependent stage was reached before its predecessor"
                        )
                    runtime = TRANSFORMATIONS[step.recipe_id]
                    spec = runtime.spec
                    if step.mutates and step.tool_contract is not None:
                        outside_contract = [
                            path
                            for path in step.files
                            if not _within_authoritative_write_scope(
                                path, step.tool_contract.expected_write_scopes
                            )
                        ]
                        if outside_contract:
                            raise RuntimeError(
                                f"{step.recipe_id}: planned files exceed the authoritative "
                                "write contract: " + ", ".join(sorted(outside_contract)[:20])
                            )
                    stage_before = _stage_inventory(workspace)
                    if step.tool and step.kind != "builtin-edit":
                        tool_key = (step.tool, step.tool_version or "")
                        if tool_key not in checked_tools:
                            version_policy = _step_policy(step, execution_policy, writable=False)
                            if (
                                step.execution_network == "required"
                                and step.tool_contract is not None
                                and step.tool_contract.network == "acquisition-only"
                            ):
                                version_policy = version_policy.model_copy(
                                    update={"network": "none", "authorize_network": False}
                                )
                            version_check = _verify_version(
                                workspace,
                                step.tool,
                                step.tool_version or "",
                                version_policy,
                                environment,
                                tool_root=root,
                                executable=(
                                    step.tool_contract.host_executable
                                    if step.tool_contract is not None
                                    else None
                                ),
                            )
                            reports.append(version_check)
                            if version_check.status != "passed":
                                raise RuntimeError(version_check.detail)
                            if (
                                execution_policy.backend != "host"
                                and step.tool_contract is not None
                            ):
                                image = step.tool_contract.image or ""
                                sandbox = version_check.sandbox or {}
                                observed = str(sandbox.get("image_id", ""))
                                digest = step.tool_contract.image_digest
                                if digest and f"@{digest}" not in image and observed != digest:
                                    raise RuntimeError(
                                        f"{step.tool}: image identity {observed or '<missing>'} "
                                        f"does not match sealed digest {digest}"
                                    )
                                if image and not digest:
                                    backend = str(sandbox.get("backend", ""))
                                    receipt_backend = "docker" if backend == "gvisor" else backend
                                    sealed = step.image_identities.get(receipt_backend)
                                    acquired = cached_image(step.tool, receipt_backend)
                                    if not sealed:
                                        raise RuntimeError(
                                            f"{step.tool}: managed image identity was not sealed "
                                            "into the plan; acquire the tool and replan"
                                        )
                                    if acquired != sealed or observed != sealed:
                                        raise RuntimeError(
                                            f"{step.tool}: managed image does not match the "
                                            "identity sealed into the plan"
                                        )
                            checked_tools.add(tool_key)
                    if step.kind == "builtin-edit":
                        rendered, step_diffs = _builtin_preview(workspace, step)
                        for path, content in step_diffs.items():
                            diffs[path] = content
                        reports.append(
                            EvolutionVerification(
                                id=f"{step.recipe_id}/{step.operation}/preview",
                                status="passed",
                                detail=f"{len(rendered)} file(s) would change",
                            )
                        )
                        if not dry_run or runtime.pipeline is not None:
                            for relative, content in rendered.items():
                                atomic_write_text(
                                    _safe_path(workspace, relative),
                                    content,
                                    root=workspace,
                                )
                                if relative in before:
                                    os.chmod(workspace / relative, before[relative].mode)
                                recipe_by_path[relative] = step.recipe_id
                    elif step.kind in {
                        "dependency-acquisition",
                        "native-command",
                        "established-codemod",
                    }:
                        if not step.tool:
                            raise ValueError(f"{step.recipe_id}: command stage has no tool")
                        if runtime.pipeline is not None:
                            if not step.apply_command:
                                raise ValueError(
                                    f"{step.recipe_id}: canonical command stage is incomplete"
                                )
                            applied, output = _run(
                                workspace,
                                step.apply_command,
                                _step_policy(step, execution_policy, writable=True),
                                environment,
                                tool=step.tool,
                                tool_root=root,
                                success_exit_codes=set(step.tool_contract.success_exit_codes)
                                if step.tool_contract is not None
                                else None,
                            )
                            applied.id = f"{step.recipe_id}/{step.operation}"
                            reports.append(applied)
                            if applied.status != "passed":
                                raise RuntimeError(applied.detail)
                            if output:
                                diffs[f"{step.recipe_id}/{step.operation}/output"] = output
                            diffs[f"{step.recipe_id}/{step.operation}"] = _staged_diff(
                                checkpoint, workspace, step.files
                            )
                            for relative in step.files:
                                if (workspace / relative).is_file():
                                    recipe_by_path[relative] = step.recipe_id
                        elif runtime.staged_preview:
                            assert (
                                runtime.preflight_command
                                and runtime.cleanup_command
                                and runtime.transform
                                and runtime.apply_command
                            )
                            preflight, _output = _run(
                                workspace,
                                runtime.preflight_command(workspace, step.files),
                                execution_policy.model_copy(update={"writable": False}),
                                environment,
                                tool=step.tool,
                                tool_root=root,
                            )
                            preflight.id = f"{step.recipe_id}/preflight"
                            reports.append(preflight)
                            if preflight.status != "passed":
                                raise RuntimeError(
                                    f"{step.recipe_id}: pre-existing import cleanup findings make "
                                    "the composed change ambiguous"
                                )
                            applied, _output = _run(
                                workspace,
                                runtime.apply_command(workspace, step.files),
                                execution_policy.model_copy(update={"writable": True}),
                                environment,
                                tool=step.tool,
                                tool_root=root,
                            )
                            applied.id = f"{step.recipe_id}/pyupgrade"
                            reports.append(applied)
                            if applied.status != "passed":
                                raise RuntimeError(applied.detail)
                            rendered = runtime.transform(workspace, step.files)
                            for relative, content in rendered.items():
                                atomic_write_text(
                                    _safe_path(workspace, relative, missing=False),
                                    content,
                                    root=workspace,
                                )
                            cleanup, _output = _run(
                                workspace,
                                runtime.cleanup_command(workspace, step.files),
                                execution_policy.model_copy(update={"writable": True}),
                                environment,
                                tool=step.tool,
                                tool_root=root,
                            )
                            cleanup.id = f"{step.recipe_id}/unused-import-cleanup"
                            reports.append(cleanup)
                            if cleanup.status != "passed":
                                raise RuntimeError(cleanup.detail)
                            diffs[step.recipe_id] = _staged_diff(checkpoint, workspace, step.files)
                            for relative in step.files:
                                recipe_by_path[relative] = step.recipe_id
                        else:
                            assert runtime.preview_command and runtime.apply_command
                            preview, output = _run(
                                workspace,
                                runtime.preview_command(workspace, step.files),
                                execution_policy.model_copy(update={"writable": False}),
                                environment,
                                tool=step.tool,
                                tool_root=root,
                            )
                            preview.id = f"{step.recipe_id}/preview"
                            has_diff = bool(re.search(r"(?m)^(?:--- |diff |@@ )", output))
                            evidence = preview.sandbox or {}
                            if (
                                preview.status == "failed"
                                and has_diff
                                and evidence.get("exit_code")
                                in {"ruff": {1}, "go": {1}, "terraform": {3}}.get(step.tool, set())
                                and not evidence.get("timed_out", False)
                                and not evidence.get("output_truncated", False)
                                and not evidence.get("oom_killed", False)
                            ):
                                preview.status = "passed"
                            reports.append(preview)
                            if preview.status != "passed":
                                raise RuntimeError(preview.detail)
                            if output:
                                diffs[step.recipe_id] = output
                            if not dry_run:
                                applied, _output = _run(
                                    workspace,
                                    runtime.apply_command(workspace, step.files),
                                    execution_policy,
                                    environment,
                                    tool=step.tool,
                                    tool_root=root,
                                )
                                applied.id = f"{step.recipe_id}/apply"
                                reports.append(applied)
                                if applied.status != "passed":
                                    raise RuntimeError(applied.detail)
                                for relative in step.files:
                                    recipe_by_path[relative] = step.recipe_id
                    elif step.kind != "postcondition":
                        raise ValueError(f"unsupported ready pipeline step kind: {step.kind}")
                    current = _stage_inventory(workspace)
                    unexpected = {
                        path
                        for path in set(_changed(stage_before, current)) - set(step.files)
                        if not any(
                            path == ephemeral or path.startswith(ephemeral.rstrip("/") + "/")
                            for ephemeral in transaction_ephemeral
                        )
                    }
                    if unexpected:
                        raise RuntimeError(
                            "transformation crossed its planned scope: "
                            + ", ".join(sorted(unexpected)[:20])
                        )
                    if dry_run and runtime.pipeline is None:
                        if _inventory(root) != before:
                            raise RuntimeError("dry-run mutated project files")
                        continue
                    if runtime.pipeline is None and spec.implementation == "command":
                        assert spec.tool and runtime.verify_command
                        verified, _output = _run(
                            workspace,
                            runtime.verify_command(workspace, step.files),
                            execution_policy.model_copy(update={"writable": False}),
                            environment,
                            tool=spec.tool,
                            tool_root=root,
                        )
                        verified.id = f"{step.recipe_id}/idempotency"
                        reports.append(verified)
                        if verified.status != "passed":
                            raise RuntimeError(verified.detail)
                    if runtime.pipeline is None:
                        reports.extend(_builtin_verify(workspace, step))
                        if any(item.status != "passed" for item in reports[-1:]):
                            raise RuntimeError(reports[-1].detail)
                    requirements = (
                        step.verification if runtime.pipeline is not None else spec.verification
                    )
                    for requirement in requirements:
                        if not requirement.command or requirement.kind == "native-idempotency":
                            continue
                        verification_root = workspace
                        verification_policy = _step_policy(step, execution_policy, writable=False)
                        if requirement.isolated_copy:
                            verification_root = temporary_path / (
                                "verification-" + uuid.uuid4().hex
                            )
                            shutil.copytree(workspace, verification_root)
                            verification_policy = _step_policy(
                                step, execution_policy, writable=True
                            )
                        verification, _output = _run(
                            verification_root,
                            requirement.command,
                            verification_policy,
                            environment,
                            tool=step.tool or requirement.command[0],
                            tool_root=root,
                        )
                        verification.id = requirement.id
                        reports.append(verification)
                        if requirement.required and verification.status != "passed":
                            raise RuntimeError(verification.detail)
                    for condition in step.postconditions:
                        if condition.kind in {"command", "no-new-findings"}:
                            postcondition, _output = _run(
                                workspace,
                                condition.command,
                                _step_policy(step, execution_policy, writable=False),
                                environment,
                                tool=step.tool or condition.command[0],
                                tool_root=root,
                            )
                            postcondition.id = condition.id
                            if postcondition.status == "passed":
                                postcondition.detail = condition.detail
                        else:
                            postcondition = _evaluate_postcondition(workspace, condition)
                        reports.append(postcondition)
                        if condition.required and postcondition.status != "passed":
                            raise RuntimeError(postcondition.detail)
                    if step.rediscover:
                        discovered = discover_project(workspace)
                        component_count = _revalidate_downstream(
                            workspace, plan, ready, position, discovered
                        )
                        reports.append(
                            EvolutionVerification(
                                id=f"{step.recipe_id}/{step.operation}/rediscovery",
                                status="passed",
                                detail=(
                                    f"rediscovered {component_count} component(s) and resealed "
                                    "the remaining mutable pipeline"
                                ),
                            )
                        )
                    if _stage_inventory(workspace) != current:
                        raise RuntimeError("verification mutated staged project files")
                    if _inventory(root) != before:
                        raise RuntimeError(
                            "project changed concurrently while the sealed pipeline was staged"
                        )
                    _assert_git_identity(
                        root, plan.current_state, "while the sealed pipeline was staged"
                    )
                    completed_steps.add(step.id)
                _verify_pipeline_idempotency(
                    root,
                    workspace,
                    temporary_path,
                    ready,
                    execution_policy,
                    environment,
                    reports,
                )
                for ephemeral in transaction_ephemeral:
                    target = _safe_path(workspace, ephemeral)
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    elif target.is_file():
                        target.unlink()
                if dry_run:
                    staged_after = _stage_inventory(workspace)
                    for relative in _changed(before, staged_after):
                        diffs[relative] = _staged_diff(checkpoint, workspace, [relative])
                    return EvolutionReport(
                        plan_sha256=plan.plan_sha256,
                        status="dry_run",
                        before=plan.current_state,
                        plan=plan,
                        after=plan.current_state,
                        verifications=reports,
                        diffs=diffs,
                        provenance=[_step_provenance(step, reports) for step in ready],
                        metrics={
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "model_calls": 0,
                            "agent_calls": 0,
                        },
                        limitations=limitations,
                    )
                after = _stage_inventory(workspace)
                for relative in _changed(before, after):
                    diffs[relative] = _staged_diff(checkpoint, workspace, [relative])
                published_after = after
                actual = _changed(before, after)
                if not actual and not unresolved:
                    return EvolutionReport(
                        plan_sha256=plan.plan_sha256,
                        status="partial" if unresolved else "noop",
                        before=plan.current_state,
                        plan=plan,
                        after=plan.current_state,
                        verifications=reports,
                        diffs=diffs,
                        metrics={"duration_ms": round((time.monotonic() - started) * 1000)},
                        limitations=limitations,
                    )
                if _inventory(root) != before:
                    raise RuntimeError("project changed concurrently before staged publication")
                _assert_git_identity(root, plan.current_state, "before staged publication")
                for relative, (old, new) in actual.items():
                    if _current_record(root, relative) != old:
                        raise RuntimeError(f"project changed concurrently at {relative}")
                    target = _safe_path(root, relative)
                    published.add(relative)
                    if new is None:
                        target.unlink()
                        continue
                    content = read_text_bounded(
                        _safe_path(workspace, relative, missing=False),
                        MAX_CHECKPOINT_FILE_BYTES,
                        root=workspace,
                    )
                    atomic_write_text(target, content, root=root)
                    os.chmod(target, new.mode)
                if _inventory(root) != after:
                    raise RuntimeError("verified staged result could not be published exactly")
                _assert_git_identity(root, plan.current_state, "during staged publication")
                after_findings: set[str] = set()
                after_review_error = None
                after_incomplete: list[str] = []
                if run_blueprint_review:
                    after_findings, after_review_error, after_incomplete = _review(root)
                final = _inventory(root)
                unexpected = set(_changed(after, final))
                if unexpected:
                    raise RuntimeError(
                        "verification mutated project files: " + ", ".join(sorted(unexpected)[:20])
                    )
                _assert_git_identity(
                    root, plan.current_state, "during final migration verification"
                )
                review_delta = ReviewDelta()
                if run_blueprint_review:
                    if before_review_error or after_review_error:
                        review_delta = ReviewDelta(
                            status="failed",
                            detail=before_review_error or after_review_error or "review failed",
                        )
                        raise RuntimeError(review_delta.detail)
                    introduced = sorted(after_findings - before_findings)
                    resolved = sorted(before_findings - after_findings)
                    review_delta = ReviewDelta(
                        before_findings=len(before_findings),
                        after_findings=len(after_findings),
                        introduced_fingerprints=introduced,
                        resolved_fingerprints=resolved,
                        before_incomplete=before_incomplete,
                        after_incomplete=after_incomplete,
                        status=(
                            "regressed"
                            if introduced
                            else "partial"
                            if before_incomplete or after_incomplete
                            else "passed"
                        ),
                        detail=(
                            "deterministic review introduced no new findings; optional analysis "
                            "remained incomplete"
                            if not introduced and (before_incomplete or after_incomplete)
                            else "deterministic Blueprint AI review introduced no new findings"
                            if not introduced
                            else f"{len(introduced)} new finding(s) require review"
                        ),
                    )
                    if introduced:
                        raise RuntimeError(review_delta.detail)
                operation_state = _operations_root(root, create=True) / operation_id
                if os.path.lexists(operation_state):
                    raise ValueError("new evolution operation state unexpectedly already exists")
                state = operation_state / "before"
                state.mkdir(parents=True, mode=0o700)
                manual_completion_paths = sorted(
                    {path for step in unresolved for path in step.manual_completion_paths}
                )
                checkpoint_paths = set(actual) | {
                    path for path in manual_completion_paths if path in before
                }
                for relative in sorted(checkpoint_paths):
                    old = before.get(relative)
                    if old is not None:
                        source = checkpoint / relative
                        target = state / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target, follow_symlinks=False)
                if unresolved:
                    after_inventory = {
                        relative: {
                            "kind": record.kind,
                            "sha256": record.sha256,
                            "size": record.size,
                            "mode": record.mode,
                        }
                        for relative, record in after.items()
                    }
                    atomic_write_text(
                        operation_state / "after-inventory.json",
                        json.dumps(after_inventory, sort_keys=True, separators=(",", ":")) + "\n",
                        overwrite=False,
                        root=operation_state,
                    )
                changes = [
                    EvolutionChange(
                        target=relative,
                        recipe_id=recipe_by_path.get(relative, "evolution"),
                        status="created"
                        if old is None
                        else "deleted"
                        if new is None
                        else "changed",
                        before_sha256=old.sha256 if old else None,
                        after_sha256=new.sha256 if new else None,
                        detail="verified evolution transaction",
                    )
                    for relative, (old, new) in actual.items()
                ]
                manifest = {
                    "version": 3,
                    "kind": "evolution",
                    "operation_id": operation_id,
                    "plan_sha256": plan.plan_sha256,
                    "requested_targets": plan.requested_targets,
                    "target_versions": plan.target_versions,
                    "manual_completion_paths": manual_completion_paths,
                    "before_fingerprint": _fingerprint(before),
                    "after_fingerprint": _fingerprint(after),
                    "git_branch": plan.current_state.git_branch,
                    "git_head": plan.current_state.git_head,
                    "git_index_sha256": plan.current_state.git_index_sha256,
                    "state": f"git-private:blueprint-ai/operations/{operation_id}/before",
                    "status": "partial" if unresolved else "verified",
                    "changes": [item.model_dump(mode="json") for item in changes],
                }
                manifest_content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
                atomic_write_text(
                    _safe_path(root, manifest_relative),
                    manifest_content,
                    overwrite=False,
                    root=root,
                )
                _seal_operation_receipt(
                    root,
                    operation_id,
                    manifest_content,
                    overwrite=False,
                )
                return EvolutionReport(
                    plan_sha256=plan.plan_sha256,
                    status="partial" if unresolved else "verified",
                    before=plan.current_state,
                    plan=plan,
                    after=_project_state(
                        root,
                        discover_project(root),
                        _fingerprint(final),
                    ),
                    operation_id=operation_id,
                    manifest_path=manifest_relative,
                    changes=changes,
                    verifications=reports,
                    diffs=diffs,
                    review=review_delta,
                    provenance=[_step_provenance(step, reports) for step in ready],
                    metrics={
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "files_changed": len(changes),
                        "model_calls": 0,
                        "agent_calls": 0,
                    },
                    rollback_command=["blueprint-ai", "rollback", operation_id, "."],
                    limitations=limitations,
                )
            except Exception as failure:
                recovery_error: Exception | None = None
                try:
                    _restore_published(root, checkpoint, before, published_after, published)
                except Exception as exc:
                    recovery_error = exc
                try:
                    receipt = _safe_path(root, manifest_relative)
                    if safe_regular_file(receipt, root):
                        receipt.unlink()
                        for parent in (receipt.parent, receipt.parent.parent):
                            try:
                                parent.rmdir()
                            except OSError:
                                break
                except Exception as exc:
                    recovery_error = recovery_error or exc
                try:
                    state_parent = _operations_root(root) / operation_id
                    if state_parent.is_dir() and not state_parent.is_symlink():
                        shutil.rmtree(state_parent, ignore_errors=True)
                except Exception as exc:
                    recovery_error = recovery_error or exc
                if recovery_error:
                    raise recovery_error from failure
                if report_failures:
                    detail = str(failure)[:1000] or type(failure).__name__
                    reports.append(
                        EvolutionVerification(
                            id="evolution/failure",
                            status="failed",
                            detail=detail,
                        )
                    )
                    restored = _inventory_with_paths(root, published)
                    reports.append(
                        EvolutionVerification(
                            id="evolution/recovery",
                            status="passed" if restored == before else "failed",
                            detail=(
                                "staged/published migration changes were recovered exactly"
                                if restored == before
                                else "the project contains concurrent state outside recovery"
                            ),
                        )
                    )
                    return EvolutionReport(
                        plan_sha256=plan.plan_sha256,
                        status="failed",
                        before=plan.current_state,
                        plan=plan,
                        after=_project_state(
                            root,
                            discover_project(root),
                            _fingerprint(restored),
                        ),
                        verifications=reports,
                        diffs=diffs,
                        provenance=[_step_provenance(step, reports) for step in ready],
                        metrics={
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "model_calls": 0,
                            "agent_calls": 0,
                        },
                        limitations=sorted(set([*limitations, detail])),
                    )
                raise


def accept_evolution(root: Path, operation_id: str, evidence: list[str]) -> EvolutionReport:
    """Record explicit operator evidence that completes a partial manual boundary."""
    root = root.expanduser().resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("invalid operation id")
    cleaned = [item.strip() for item in evidence if item.strip()]
    if not cleaned or len(cleaned) > 20 or any(len(item) > 500 for item in cleaned):
        raise ValueError("manual acceptance requires 1-20 bounded evidence statements")
    with _operation_lock(root):
        manifest_relative = f".blueprint-ai/operations/{operation_id}.json"
        manifest_path = _safe_path(root, manifest_relative, missing=False)
        try:
            manifest_content = read_text_bounded(manifest_path, MAX_MANIFEST_BYTES, root=root)
            data = json.loads(manifest_content)
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(f"invalid evolution operation manifest: {exc}") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != 3
            or data.get("kind") != "evolution"
            or data.get("operation_id") != operation_id
            or data.get("status") != "partial"
            or not re.fullmatch(r"[a-f0-9]{64}", str(data.get("after_fingerprint", "")))
        ):
            raise ValueError("only an unchanged partial evolution operation can be accepted")
        if not _receipt_is_authoritative(
            root, operation_id, manifest_content, require_after_inventory=True
        ):
            raise ValueError("evolution operation receipt is not sealed by Git-private state")
        operation_state = _operations_root(root) / operation_id
        inventory_path = operation_state / "after-inventory.json"
        try:
            raw_inventory = json.loads(
                read_text_bounded(
                    inventory_path,
                    MAX_CHECKPOINT_BYTES,
                    root=operation_state,
                )
            )
            if not isinstance(raw_inventory, dict):
                raise ValueError("invalid sealed post-prefix inventory")
            sealed_after = {
                relative: FileRecord(**value)
                for relative, value in raw_inventory.items()
                if isinstance(relative, str) and isinstance(value, dict)
            }
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(f"manual acceptance inventory is unavailable: {exc}") from exc
        if (
            len(sealed_after) != len(raw_inventory)
            or _fingerprint(sealed_after) != data["after_fingerprint"]
        ):
            raise ValueError("manual acceptance inventory does not match the sealed prefix")
        allowed = data.get("manual_completion_paths")
        if (
            not isinstance(allowed, list)
            or any(not isinstance(path, str) for path in allowed)
            or len(set(allowed)) != len(allowed)
        ):
            raise ValueError("manual acceptance paths are not sealed in the operation")
        for path in allowed:
            _safe_path(root, path)
        records = _inventory_with_paths(root, allowed)
        manual_changes = _changed(sealed_after, records)
        target_versions = data.get("target_versions")
        is_next = (
            isinstance(target_versions, dict) and "next/official-upgrade-codemod" in target_versions
        )
        next_lockfiles = {
            "package-lock.json",
            "npm-shrinkwrap.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
        }
        unsafe = [
            path
            for path, (old, _new) in manual_changes.items()
            if path not in allowed or old is not None and not (is_next and path in next_lockfiles)
        ]
        if unsafe:
            raise ValueError(
                "manual acceptance crossed its sealed completion paths; changed: "
                + ", ".join(sorted(unsafe)[:20])
            )
        if is_next:
            completed = {path for path in next_lockfiles if path in records}
            if len(completed) != 1:
                raise ValueError("Next.js manual acceptance requires exactly one root lockfile")
            package_changed = any(
                isinstance(change, dict) and change.get("target") == "package.json"
                for change in data.get("changes", [])
            )
            lockfile = next(iter(completed))
            if package_changed and lockfile not in manual_changes:
                raise ValueError(
                    "Next.js dependency changes require the selected root lockfile to be updated"
                )
        facts = discover_project(root)
        if (
            facts.git_branch != data.get("git_branch")
            or _git_head(root) != data.get("git_head")
            or _git_index_sha256(root) != data.get("git_index_sha256")
        ):
            raise ValueError("manual acceptance refused because Git identity changed")
        data["status"] = "accepted"
        data["after_fingerprint"] = _fingerprint(records)
        existing_changes = data.get("changes", [])
        if not isinstance(existing_changes, list):
            raise ValueError("manual acceptance operation changes are malformed")
        for relative, (old, new) in manual_changes.items():
            if new is None:
                continue
            existing_changes.append(
                EvolutionChange(
                    target=relative,
                    recipe_id="manual-acceptance",
                    status="created" if old is None else "changed",
                    before_sha256=old.sha256 if old is not None else None,
                    after_sha256=new.sha256,
                    detail=(
                        "lockfile generated during manual acceptance"
                        if old is None
                        else "lockfile updated during manual acceptance"
                    ),
                ).model_dump(mode="json")
            )
        data["changes"] = existing_changes
        data["manual_acceptance"] = {
            "accepted_at": datetime.now(UTC).isoformat(),
            "evidence": cleaned,
        }
        accepted_content = json.dumps(data, indent=2, sort_keys=True) + "\n"
        try:
            atomic_write_text(manifest_path, accepted_content, root=root)
            _seal_operation_receipt(root, operation_id, accepted_content)
        except Exception:
            atomic_write_text(manifest_path, manifest_content, root=root)
            _seal_operation_receipt(root, operation_id, manifest_content)
            raise
        state = _project_state(root, facts, data["after_fingerprint"])
        return EvolutionReport(
            plan_sha256=str(data.get("plan_sha256", "")),
            status="accepted",
            operation_id=operation_id,
            after=state,
            manifest_path=manifest_relative,
            verifications=[
                EvolutionVerification(
                    id="evolution/manual-acceptance",
                    status="passed",
                    detail="; ".join(cleaned),
                )
            ],
            rollback_command=["blueprint-ai", "rollback", operation_id, "."],
        )


def rollback_evolution(root: Path, operation_id: str) -> EvolutionReport:
    root = root.expanduser().resolve()
    with _operation_lock(root):
        return _rollback_evolution_locked(root, operation_id)


def _rollback_evolution_locked(root: Path, operation_id: str) -> EvolutionReport:
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("invalid operation id")
    manifest_relative = f".blueprint-ai/operations/{operation_id}.json"
    manifest_path = _safe_path(root, manifest_relative, missing=False)
    try:
        manifest_content = read_text_bounded(manifest_path, MAX_MANIFEST_BYTES, root=root)
        data = json.loads(manifest_content)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"invalid evolution operation manifest: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 3
        or data.get("kind") != "evolution"
        or data.get("operation_id") != operation_id
        or not isinstance(data.get("changes"), list)
        or not re.fullmatch(r"[a-f0-9]{64}", str(data.get("before_fingerprint", "")))
        or not re.fullmatch(r"[a-f0-9]{64}", str(data.get("after_fingerprint", "")))
        or not re.fullmatch(r"[a-f0-9]{64}", str(data.get("git_index_sha256", "")))
        or (
            data.get("git_head") is not None
            and not re.fullmatch(r"[a-f0-9]{40,64}", str(data.get("git_head")))
        )
        or (data.get("git_branch") is not None and not isinstance(data.get("git_branch"), str))
    ):
        raise ValueError("operation is not a valid evolution transaction")
    if not _receipt_is_authoritative(root, operation_id, manifest_content):
        raise ValueError("evolution operation receipt is not sealed by Git-private state")
    changes = [EvolutionChange.model_validate(item) for item in data["changes"]]
    if len({change.target for change in changes}) != len(changes):
        raise ValueError("operation contains duplicate evolution targets")
    for change in changes:
        _safe_path(root, change.target)
    state = _operations_root(root) / operation_id / "before"
    current = _inventory_with_paths(root, [change.target for change in changes])
    conflicts = []
    facts = discover_project(root)
    if facts.git_branch != data.get("git_branch"):
        conflicts.append("Git branch")
    if _git_head(root) != data.get("git_head"):
        conflicts.append("Git HEAD")
    if _git_index_sha256(root) != data["git_index_sha256"]:
        conflicts.append("Git index")
    if _fingerprint(current) != data["after_fingerprint"]:
        conflicts.append("project fingerprint")
    projected = dict(current)
    for change in changes:
        record = current.get(change.target)
        observed = record.sha256 if record else None
        if observed != change.after_sha256:
            conflicts.append(change.target)
        if change.before_sha256 is not None:
            backup = state / change.target
            if not safe_regular_file(backup, state):
                conflicts.append(change.target + " (backup unavailable)")
            elif hashlib.sha256(backup.read_bytes()).hexdigest() != change.before_sha256:
                conflicts.append(change.target + " (backup checksum mismatch)")
            else:
                info = backup.stat()
                projected[change.target] = FileRecord(
                    "file", change.before_sha256, info.st_size, stat.S_IMODE(info.st_mode)
                )
        else:
            projected.pop(change.target, None)
    if _fingerprint(projected) != data["before_fingerprint"]:
        conflicts.append("checkpoint fingerprint")
    if conflicts:
        raise ValueError(
            "rollback refused because post-apply files or backups changed: "
            + ", ".join(sorted(set(conflicts)))
        )
    for change in reversed(changes):
        target = _safe_path(root, change.target)
        if change.before_sha256 is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(state / change.target, target, follow_symlinks=False)
        change.status = "rolled_back"
        change.detail = "exact pre-evolution content restored"
    restored = _fingerprint(_inventory_with_paths(root, [change.target for change in changes]))
    if restored != data.get("before_fingerprint"):
        raise OSError("rollback bytes were restored but the project fingerprint does not match")
    manifest_path.unlink()
    shutil.rmtree(state.parent, ignore_errors=True)
    return EvolutionReport(
        plan_sha256=str(data.get("plan_sha256", "")),
        status="rolled_back",
        operation_id=operation_id,
        changes=changes,
        verifications=[
            EvolutionVerification(
                id="rollback/exact-restoration",
                status="passed",
                detail="project fingerprint matches the pre-evolution checkpoint",
            )
        ],
    )
