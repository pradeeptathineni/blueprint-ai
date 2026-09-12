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
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
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

from .catalog import _MAINTAINER, _USES, OFFICIAL_ACTION_SHAS, TRANSFORMATIONS
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
        _safe_path(root, value)
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
        target = _safe_path(root, relative)
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"evolution inventory rejects non-regular project path: {relative}")
        digest, size = _hash_file(target)
        records[relative] = FileRecord("file", digest, size, stat.S_IMODE(info.st_mode))
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
                versions[f"{dependency.ecosystem}:{dependency.name}"] = dependency.requirement
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
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        try:
            import tomllib

            package = tomllib.loads(read_text_bounded(cargo, MAX_MANIFEST_BYTES, root=root)).get(
                "package", {}
            )
            if package.get("edition"):
                versions["rust-edition"] = str(package["edition"])
        except (OSError, ValueError):
            pass
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


def plan_evolution(root: Path, requested_targets: list[str] | None = None) -> EvolutionPlan:
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
    targets = list(dict.fromkeys(requested_targets or []))
    unknown = [target for target in targets if target not in TRANSFORMATIONS]
    if unknown:
        raise ValueError("unknown evolution target: " + ", ".join(unknown))
    steps: list[EvolutionStep] = []
    desired: list[str] = []
    risks: list[str] = []
    manual: list[str] = []
    previous: str | None = None
    for recipe_id, runtime in TRANSFORMATIONS.items():
        if recipe_id not in targets:
            continue
        spec = runtime.spec
        paths = detected[recipe_id]
        if len(paths) > MAX_PLAN_FILES or sum(len(path) + 1 for path in paths) > MAX_COMMAND_BYTES:
            raise ValueError(f"{recipe_id}: selected scope exceeds the bounded plan size")
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
                    else "working tree is dirty; apply requires --allow-dirty and the same "
                    "fingerprint"
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
        if spec.tool:
            available = shutil.which(spec.tool) is not None or any(
                (root / directory / spec.tool).is_file()
                for directory in (".venv/bin", ".venv/Scripts")
            )
            preconditions.append(
                Precondition(
                    id="tool-availability",
                    status="passed" if available else "unknown",
                    detail=(
                        f"{spec.tool} executable discovered"
                        if available
                        else f"{spec.tool} must be explicitly available on apply or in the "
                        "selected image"
                    ),
                )
            )
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
                sequence=len(steps) + 1,
                depends_on=[previous] if previous else [],
                mechanism=spec.mechanism,
                provider=spec.provider,
                tool=spec.tool,
                tool_version=spec.tool_version,
                recipe_version=spec.recipe_version,
                authoritative_source=spec.authoritative_source,
                files=[] if paths == ["."] else paths,
                preconditions=preconditions,
                verification=spec.verification,
                reversible=spec.reversible,
                limitations=spec.limitations,
                model_responsibility=(
                    "bounded planning only; never repository mutation"
                    if spec.model_allowed
                    else "none"
                ),
                agent_responsibility=(
                    "explicit residual implementation; unavailable in 0.7.0"
                    if spec.agent_allowed
                    else "none"
                ),
                status=step_status,
            )
        )
        previous = step_id
        desired.append(spec.desired_state)
        risks.extend(f"{recipe_id}: {item}" for item in spec.conflicts)
    if not targets:
        plan_status: Literal["inspection", "ready", "blocked", "noop"] = "inspection"
    elif any(step.status == "manual" for step in steps):
        plan_status = "blocked"
    elif any(step.status == "ready" for step in steps):
        plan_status = "ready"
    else:
        plan_status = "noop"
    return EvolutionPlan(
        blueprint_ai_version=__version__,
        requested_targets=targets,
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
    current = _inventory(root)
    conflicts = []
    restore = []
    for relative in sorted(published):
        observed = current.get(relative)
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
    verified = _inventory(root)
    failed = [relative for relative, old in restore if verified.get(relative) != old]
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


def _tool_command(root: Path, tool: str, policy: SandboxPolicy) -> str:
    if policy.backend != "host" and not (
        policy.backend == "auto" and policy.trusted and not policy.image
    ):
        return tool
    if executable := shutil.which(tool):
        return executable
    for directory in (".venv/bin", ".venv/Scripts"):
        candidate = root / directory / tool
        if safe_regular_file(candidate, root) and os.access(candidate, os.X_OK):
            return str(candidate)
    raise ValueError(f"{tool} executable is unavailable")


def _execution_env(directory: Path) -> dict[str, str]:
    caches = directory / "caches"
    caches.mkdir()
    return controlled_env(
        {
            "HOME": str(directory),
            "XDG_CACHE_HOME": str(caches),
            "RUFF_CACHE_DIR": str(caches / "ruff"),
            "GOCACHE": str(caches / "go-build"),
            "GOMODCACHE": str(caches / "go-mod"),
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
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
) -> tuple[EvolutionVerification, str]:
    reported_command = list(command)
    execution_command = list(command)
    execution_command[0] = _tool_command(tool_root or root, execution_command[0], policy)
    started = time.monotonic()
    try:
        execution = execute(execution_command, root, policy, tool=tool, host_env=environment)
    except (OSError, ValueError, SandboxUnavailable) as exc:
        return (
            EvolutionVerification(
                id=f"{tool}/execution",
                status="tool_error",
                command=reported_command,
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
    for key in ("HOME", "XDG_CACHE_HOME", "RUFF_CACHE_DIR", "GOCACHE", "GOMODCACHE"):
        if value := environment.get(key):
            output = output.replace(value, "<isolated-cache>")
    okay = result.returncode == 0 and not result.timed_out and not result.output_truncated
    return (
        EvolutionVerification(
            id=f"{tool}/execution",
            status="passed" if okay else "failed",
            command=reported_command,
            detail=(output[-1000:] or f"exit {result.returncode}"),
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
) -> EvolutionVerification:
    flag = "version" if tool == "go" else "--version"
    verification, output = _run(
        root, [tool, flag], policy, environment, tool=tool, tool_root=tool_root
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


def _diff(relative: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def _builtin_preview(root: Path, step: EvolutionStep) -> tuple[dict[str, str], dict[str, str]]:
    runtime = TRANSFORMATIONS[step.recipe_id]
    if runtime.transform is None:
        raise ValueError(f"{step.recipe_id}: built-in transform is unavailable")
    rendered = runtime.transform(root, step.files)
    diffs = {
        path: _diff(
            path,
            read_text_bounded(root / path, MAX_MANIFEST_BYTES, root=root),
            content,
        )
        for path, content in rendered.items()
    }
    return rendered, diffs


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
                ast.parse(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
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
    fresh = plan_evolution(root, plan.requested_targets)
    planned = plan.model_dump(mode="json", exclude={"created_at", "metrics", "plan_sha256"})
    resolved = fresh.model_dump(mode="json", exclude={"created_at", "metrics", "plan_sha256"})
    if planned != resolved:
        raise ValueError("evolution plan no longer resolves to the canonical catalog")
    return discover_project(root), records


def apply_evolution(
    root: Path,
    plan: EvolutionPlan,
    *,
    dry_run: bool = False,
    allow_dirty: bool = False,
    allow_main: bool = False,
    policy: SandboxPolicy | None = None,
    run_blueprint_review: bool = True,
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
        limitations = sorted({item for step in plan.steps for item in step.limitations})
        if any(step.status == "manual" for step in plan.steps):
            raise ValueError("plan contains manual/deferred steps and cannot be applied")
        if not ready:
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
        if any(TRANSFORMATIONS[step.recipe_id].spec.tool for step in ready):
            if not execution_policy.trusted or (not dry_run and not execution_policy.writable):
                raise ValueError(
                    "tool-driven evolution requires explicit trusted execution"
                    + (" with a writable target" if not dry_run else "")
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
            allowed: set[str] = set()
            published: set[str] = set()
            published_after: dict[str, FileRecord] = {}
            try:
                for step in ready:
                    runtime = TRANSFORMATIONS[step.recipe_id]
                    spec = runtime.spec
                    allowed.update(step.files)
                    if spec.implementation == "builtin":
                        rendered, step_diffs = _builtin_preview(workspace, step)
                        diffs.update(step_diffs)
                        reports.append(
                            EvolutionVerification(
                                id=f"{step.recipe_id}/preview",
                                status="passed",
                                detail=f"{len(rendered)} file(s) would change",
                            )
                        )
                        if not dry_run:
                            for relative, content in rendered.items():
                                atomic_write_text(
                                    _safe_path(workspace, relative, missing=False),
                                    content,
                                    root=workspace,
                                )
                                os.chmod(workspace / relative, before[relative].mode)
                                recipe_by_path[relative] = step.recipe_id
                    else:
                        assert spec.tool and runtime.preview_command and runtime.apply_command
                        version_check = _verify_version(
                            workspace,
                            spec.tool,
                            spec.tool_version or "",
                            execution_policy.model_copy(update={"writable": False}),
                            environment,
                            tool_root=root,
                        )
                        reports.append(version_check)
                        if version_check.status != "passed":
                            raise RuntimeError(version_check.detail)
                        preview, output = _run(
                            workspace,
                            runtime.preview_command(workspace, step.files),
                            execution_policy.model_copy(update={"writable": False}),
                            environment,
                            tool=spec.tool,
                            tool_root=root,
                        )
                        preview.id = f"{step.recipe_id}/preview"
                        has_diff = bool(re.search(r"(?m)^(?:--- |diff |@@ )", output))
                        evidence = preview.sandbox or {}
                        if (
                            preview.status == "failed"
                            and has_diff
                            and evidence.get("exit_code")
                            in {"ruff": {1}, "go": {1}, "terraform": {3}}.get(spec.tool, set())
                            and not evidence.get("timed_out", False)
                            and not evidence.get("output_truncated", False)
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
                                tool=spec.tool,
                                tool_root=root,
                            )
                            applied.id = f"{step.recipe_id}/apply"
                            reports.append(applied)
                            if applied.status != "passed":
                                raise RuntimeError(applied.detail)
                            for relative in step.files:
                                recipe_by_path[relative] = step.recipe_id
                    current = _stage_inventory(workspace)
                    unexpected = set(_changed(before, current)) - allowed
                    if unexpected:
                        raise RuntimeError(
                            "transformation crossed its planned scope: "
                            + ", ".join(sorted(unexpected)[:20])
                        )
                    if dry_run:
                        if current != before:
                            raise RuntimeError("dry-run mutated project files")
                        continue
                    if spec.implementation == "command":
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
                    reports.extend(_builtin_verify(workspace, step))
                    if any(item.status != "passed" for item in reports[-1:]):
                        raise RuntimeError(reports[-1].detail)
                    for requirement in spec.verification:
                        if not requirement.command or requirement.kind == "native-idempotency":
                            continue
                        verification, _output = _run(
                            workspace,
                            requirement.command,
                            execution_policy.model_copy(update={"writable": False}),
                            environment,
                            tool=requirement.command[0],
                            tool_root=root,
                        )
                        verification.id = requirement.id
                        reports.append(verification)
                        if requirement.required and verification.status != "passed":
                            raise RuntimeError(verification.detail)
                    if _stage_inventory(workspace) != current:
                        raise RuntimeError("verification mutated staged project files")
                if dry_run:
                    return EvolutionReport(
                        plan_sha256=plan.plan_sha256,
                        status="dry_run",
                        before=plan.current_state,
                        plan=plan,
                        after=plan.current_state,
                        verifications=reports,
                        diffs=diffs,
                        provenance=[
                            {
                                "recipe_id": step.recipe_id,
                                "provider": step.provider,
                                "mechanism": step.mechanism,
                                "recipe_version": step.recipe_version,
                                "tool_version": step.tool_version or "built-in",
                            }
                            for step in ready
                        ],
                        metrics={
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "model_calls": 0,
                            "agent_calls": 0,
                        },
                        limitations=limitations,
                    )
                after = _stage_inventory(workspace)
                published_after = after
                actual = _changed(before, after)
                if not actual:
                    return EvolutionReport(
                        plan_sha256=plan.plan_sha256,
                        status="noop",
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
                for relative, (old, new) in actual.items():
                    if _inventory(root).get(relative) != old:
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
                for relative, (old, _new) in actual.items():
                    if old is not None:
                        source = checkpoint / relative
                        target = state / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target, follow_symlinks=False)
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
                    "before_fingerprint": _fingerprint(before),
                    "after_fingerprint": _fingerprint(after),
                    "git_branch": plan.current_state.git_branch,
                    "git_head": plan.current_state.git_head,
                    "git_index_sha256": plan.current_state.git_index_sha256,
                    "state": f"git-private:blueprint-ai/operations/{operation_id}/before",
                    "changes": [item.model_dump(mode="json") for item in changes],
                }
                atomic_write_text(
                    _safe_path(root, manifest_relative),
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    overwrite=False,
                    root=root,
                )
                return EvolutionReport(
                    plan_sha256=plan.plan_sha256,
                    status="verified",
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
                    provenance=[
                        {
                            "recipe_id": step.recipe_id,
                            "provider": step.provider,
                            "mechanism": step.mechanism,
                            "recipe_version": step.recipe_version,
                            "tool_version": step.tool_version or "built-in",
                        }
                        for step in ready
                    ],
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
                raise


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
        data = json.loads(read_text_bounded(manifest_path, MAX_MANIFEST_BYTES, root=root))
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
    changes = [EvolutionChange.model_validate(item) for item in data["changes"]]
    if len({change.target for change in changes}) != len(changes):
        raise ValueError("operation contains duplicate evolution targets")
    for change in changes:
        _safe_path(root, change.target)
    state = _operations_root(root) / operation_id / "before"
    current = _inventory(root)
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
    restored = _fingerprint(_inventory(root))
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
