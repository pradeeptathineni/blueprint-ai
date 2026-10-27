"""Plan validation, bounded provider execution, and atomic fresh-project publication."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from blueprint_ai.adapters.base import _resolve_executable
from blueprint_ai.core.project import Evidence, Relationship
from blueprint_ai.discovery import discover_project, iter_project_files
from blueprint_ai.engine import analyzer_identity, make_context, review
from blueprint_ai.genesis.assets import strengthen
from blueprint_ai.genesis.files import json_file
from blueprint_ai.genesis.models import GenesisPlan, GenesisResult, Operation, OperationResult
from blueprint_ai.genesis.plan import PROVIDERS, plan_project
from blueprint_ai.safety import (
    MAX_MANIFEST_BYTES,
    controlled_env,
    read_bytes_bounded,
    read_text_bounded,
    run_process,
)
from blueprint_ai.sandbox import (
    SandboxPolicy,
    SandboxUnavailable,
    execute,
    image_identity,
    resolve_backend,
)
from blueprint_ai.support import PROVIDER_IMAGES


def _publish_fresh(source: Path, destination: Path) -> None:
    """Atomic no-replace publication, including a concurrent empty destination."""
    if sys.platform == "win32":
        os.rename(source, destination)  # Windows rename fails when the destination exists.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    elif hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory publication is unavailable")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _destination(path: Path) -> Path:
    path = path.expanduser().absolute()
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() and ancestor not in {Path("/tmp"), Path("/var")}:
            raise ValueError("destination and parents must not be symbolic links")
    if not path.parent.is_dir():
        raise ValueError("destination parent must already exist")
    if path.exists():
        raise ValueError("destination must not exist; fresh generation never overwrites a project")
    return path.parent.resolve() / path.name


def _read_json_record(root: Path, relative: str, limit: int = 4_000_000) -> dict:
    path = root / relative
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError("staged Blueprint receipt is unavailable")
    value = json.loads(read_text_bounded(path, limit, root=root))
    if not isinstance(value, dict):
        raise ValueError("staged Blueprint receipt is unsupported")
    return value


def _stage_metadata(stage: Path, plan: GenesisPlan, stage_id: str, result: GenesisResult) -> None:
    metadata = stage / ".blueprint-ai"
    if metadata.is_symlink():
        raise ValueError("staging metadata directory is unsafe")
    metadata.mkdir(mode=0o700, exist_ok=True)
    json_file(
        stage,
        ".blueprint-ai/staging.json",
        {
            "schema_version": "1.0",
            "stage_id": stage_id,
            "state": _stage_state(result),
            "plan_sha256": plan.digest(),
            "result": result.model_dump(mode="json"),
        },
    )


def _stage_state(result: GenesisResult) -> str:
    return (
        "validated"
        if result.status in {"verified", "partial"}
        and all(operation.status == "passed" for operation in result.operations)
        else "validation-failed"
    )


def _validate_stage(
    stage: Path,
    destination: Path,
    expected_plan_sha256: str,
    expected_stage_id: str,
) -> tuple[GenesisPlan, GenesisResult, dict[str, str]]:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_plan_sha256) or not re.fullmatch(
        r"[a-f0-9-]{36}", expected_stage_id
    ):
        raise ValueError("invalid retained stage identity")
    if (
        not stage.is_absolute()
        or stage.is_symlink()
        or not stage.is_dir()
        or stage.resolve(strict=True) != stage
    ):
        raise ValueError("exact retained staging directory is unavailable")
    staging_record = _read_json_record(stage, ".blueprint-ai/staging.json")
    if (
        staging_record.get("schema_version") != "1.0"
        or staging_record.get("stage_id") != expected_stage_id
        or staging_record.get("state") != "validated"
        or staging_record.get("plan_sha256") != expected_plan_sha256
    ):
        raise ValueError("retained staging receipt does not match this request")
    provenance = _read_json_record(stage, ".blueprint-ai/genesis.json")
    plan = GenesisPlan.model_validate(provenance.get("plan"))
    if plan.digest() != expected_plan_sha256:
        raise ValueError("retained plan changed; validate a fresh stage")
    if plan_project(plan.intent).digest() != plan.digest():
        raise ValueError("retained plan no longer matches the native resolver")
    result = GenesisResult.model_validate(provenance.get("result"))
    if (
        _stage_state(result) != "validated"
        or result.plan_sha256 != expected_plan_sha256
        or result.destination != str(destination)
    ):
        raise ValueError("retained stage did not pass native validation for this destination")
    files = _inventory(stage)
    if files != result.files:
        raise ValueError("staged files changed after native validation; stage retained")
    metadata = stage / ".blueprint-ai"
    if metadata.is_symlink() or not metadata.is_dir():
        raise ValueError("staged provenance directory is unsafe")
    names = sorted(entry.name for entry in metadata.iterdir())
    if names != ["genesis.json", "review.json", "staging.json"]:
        raise ValueError("staged provenance changed after native validation")
    review_record = _read_json_record(stage, ".blueprint-ai/review.json")
    facts = review_record.get("facts")
    if not isinstance(facts, dict) or facts.get("path") != str(destination):
        raise ValueError("staged review does not match the configured destination")
    summary = {
        row.get("blueprint"): row.get("status")
        for row in review_record.get("results", [])
        if isinstance(row, dict)
    }
    if summary != result.review_summary:
        raise ValueError("staged review summary changed after native validation")
    return plan, result, files


def inspect_staged_project(
    staging_path: Path,
    destination: Path,
    *,
    expected_plan_sha256: str,
    expected_stage_id: str,
) -> dict:
    destination = _destination(destination)
    stage = Path(staging_path)
    try:
        plan, result, files = _validate_stage(
            stage, destination, expected_plan_sha256, expected_stage_id
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        if stage.is_dir() and not stage.is_symlink():
            try:
                failed = _read_json_record(stage, ".blueprint-ai/staging.json")
                if (
                    failed.get("stage_id") == expected_stage_id
                    and failed.get("plan_sha256") == expected_plan_sha256
                    and failed.get("state") == "validation-failed"
                ):
                    return {
                        "state": "validation-failed",
                        "stage_id": expected_stage_id,
                        "plan_sha256": expected_plan_sha256,
                        "result": failed.get("result"),
                        "validation_error": str(error),
                    }
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return {
            "state": "unconfirmed",
            "stage_id": expected_stage_id,
            "plan_sha256": expected_plan_sha256,
            "validation_error": str(error),
        }
    return {
        "state": "validated",
        "stage_id": expected_stage_id,
        "plan_sha256": plan.digest(),
        "result": result.model_dump(mode="json"),
        "files": files,
    }


def publish_staged_project(
    staging_path: Path,
    destination: Path,
    *,
    expected_plan_sha256: str,
    expected_stage_id: str,
) -> dict:
    destination = _destination(destination)
    stage = Path(staging_path)
    _validate_stage(stage, destination, expected_plan_sha256, expected_stage_id)
    if stage.parent.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError("staging and destination must share a filesystem for atomic publication")
    _destination(destination)
    _publish_fresh(stage, destination)
    return {
        "state": "published",
        "stage_id": expected_stage_id,
        "plan_sha256": expected_plan_sha256,
        "destination": str(destination),
    }


def inspect_published_project(
    destination: Path,
    *,
    expected_plan_sha256: str,
    expected_stage_id: str,
) -> dict:
    path = Path(destination)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise ValueError("published Blueprint project is unavailable")
    provenance = _read_json_record(path, ".blueprint-ai/genesis.json")
    staging = _read_json_record(path, ".blueprint-ai/staging.json")
    result = GenesisResult.model_validate(provenance.get("result"))
    plan = GenesisPlan.model_validate(provenance.get("plan"))
    if (
        plan.digest() != expected_plan_sha256
        or plan_project(plan.intent).digest() != expected_plan_sha256
        or _stage_state(result) != "validated"
        or result.plan_sha256 != expected_plan_sha256
        or result.destination != str(path)
        or staging.get("stage_id") != expected_stage_id
        or staging.get("plan_sha256") != expected_plan_sha256
        or staging.get("state") != "validated"
    ):
        raise ValueError("published project does not match the exact validated stage")
    files = _inventory(path)
    if files != result.files:
        raise ValueError("published files changed after native validation")
    return {
        "state": "published",
        "stage_id": expected_stage_id,
        "plan_sha256": expected_plan_sha256,
        "destination": str(path),
        "result": result.model_dump(mode="json"),
        "files": files,
    }


def _probe(executable: str, requirement: str | None) -> tuple[str | None, str | None, str]:
    resolved = _resolve_executable(executable)
    if not resolved:
        return None, None, f"{executable} is not installed"
    flag = ["version"] if executable in {"go", "helm", "kustomize"} else ["--version"]
    result = run_process([resolved, *flag], Path(tempfile.gettempdir()), 5)
    match = re.search(r"(?:^|\s|v|go)(\d+\.\d+\.\d+(?:[a-z0-9.+-]*))", result.stdout)
    if result.returncode or not match:
        return None, None, f"cannot determine {executable} version"
    version = match.group(1)
    if requirement and Version(version) not in SpecifierSet(requirement):
        return None, version, f"{executable} {version} does not satisfy {requirement}"
    return resolved, version, ""


def preflight(
    plan: GenesisPlan, *, allow_network: bool, trust_providers: bool
) -> list[OperationResult]:
    failures = []
    for provider in plan.providers:
        if not provider.executable:
            continue
        if provider.executes_code and not trust_providers:
            failures.append(
                OperationResult(
                    id="preflight:" + provider.id,
                    status="unauthorized",
                    detail="provider execution requires --trust-providers",
                )
            )
            continue
        if provider.id == "docker":
            continue  # Optional daemon availability is a verification result, not initialization.
        if provider.network and not allow_network:
            failures.append(
                OperationResult(
                    id="preflight:" + provider.id,
                    status="unauthorized",
                    detail="fetched provider requires --allow-network",
                )
            )
            continue
        executable, version, detail = _probe(provider.executable, provider.supported_versions)
        if not executable:
            failures.append(
                OperationResult(
                    id="preflight:" + provider.id,
                    status="unavailable",
                    version=version,
                    detail=detail,
                )
            )
    if any("TypeScript" in c.languages for c in plan.components):
        executable, version, detail = _probe("node", ">=22.12")
        if not executable:
            failures.append(
                OperationResult(
                    id="preflight:node", status="unavailable", version=version, detail=detail
                )
            )
    return failures


def _run(
    operation: Operation,
    stage: Path,
    cache: Path,
    allow_network: bool,
    sandbox: SandboxPolicy | None = None,
) -> OperationResult:
    if sandbox is not None and resolve_backend(sandbox) != "host":
        return _run_sandboxed(operation, stage, allow_network, sandbox)
    if operation.network and not allow_network:
        return OperationResult(
            id=operation.id,
            component=operation.component,
            status="unauthorized",
            command=operation.command,
            detail="network disabled; verification incomplete",
        )
    provider = PROVIDERS[operation.provider]
    executable, version, detail = _probe(operation.command[0], provider.supported_versions)
    if not executable:
        return OperationResult(
            id=operation.id,
            component=operation.component,
            status="unavailable",
            command=operation.command,
            detail=detail,
            version=version,
        )
    command = [executable, *operation.command[1:]]
    if provider.registry_integrity:
        metadata = run_process(
            [
                executable,
                "view",
                f"{provider.package}@{provider.version}",
                "dist.integrity",
                "--json",
            ],
            cache,
            30,
            output_limit=16_384,
            env=controlled_env({"HOME": str(cache), "npm_config_cache": str(cache / "npm")}),
        )
        try:
            integrity = json.loads(metadata.stdout)
        except ValueError:
            integrity = None
        if metadata.returncode or integrity != provider.registry_integrity:
            return OperationResult(
                id=operation.id,
                component=operation.component,
                status="failed",
                command=operation.command,
                detail="Pinned provider registry integrity could not be verified",
            )
    docker_host = ""
    if operation.provider == "docker":
        socket = next(
            (
                path
                for path in (Path("/var/run/docker.sock"), Path.home() / ".docker/run/docker.sock")
                if path.exists()
            ),
            None,
        )
        if socket is None:
            return OperationResult(
                id=operation.id,
                component=operation.component,
                status="unavailable",
                command=operation.command,
                version=version,
                detail="Local Docker socket unavailable; image build was not verified",
            )
        docker_host = "unix://" + str(socket)
        daemon = run_process(
            [executable, "--host", docker_host, "info", "--format", "{{.ServerVersion}}"], stage, 10
        )
        if daemon.returncode != 0:
            return OperationResult(
                id=operation.id,
                component=operation.component,
                status="unavailable",
                command=operation.command,
                version=version,
                detail="Docker daemon unavailable; image build was not verified",
            )
    cwd = stage / operation.component
    cwd.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    output = run_process(
        command,
        cwd,
        240,
        output_limit=1_000_000,
        env=controlled_env(
            {
                "HOME": str(cache),
                "XDG_CACHE_HOME": str(cache),
                "DOCKER_CONFIG": str(cache / "docker"),
                "DOCKER_HOST": docker_host,
                "UV_CACHE_DIR": str(cache / "uv"),
                "npm_config_cache": str(cache / "npm"),
                "npm_config_ignore_scripts": "true",
                "npm_config_audit": "false",
                "npm_config_fund": "false",
                "UV_PYTHON": str(Path(sys.executable).resolve()),
                "UV_NO_CONFIG": "1",
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_OFFLINE": "0" if allow_network else "1",
                "npm_config_offline": "false" if allow_network else "true",
                "PYTHONDONTWRITEBYTECODE": "1",
                "GOCACHE": str(cache / "go-build"),
                "GOMODCACHE": str(cache / "go-mod"),
                "CARGO_TARGET_DIR": str(cache / "cargo-target"),
                "DOTNET_CLI_HOME": str(cache / "dotnet"),
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "NUGET_PACKAGES": str(cache / "nuget"),
                "RUFF_CACHE_DIR": str(cache / "ruff"),
                "MYPY_CACHE_DIR": str(cache / "mypy"),
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
            }
        ),
    )
    expected = all(
        (cwd / file).is_file() and not (cwd / file).is_symlink()
        for file in operation.expected_files
    )
    okay = (
        output.returncode == 0 and not output.output_truncated and not output.timed_out and expected
    )
    return OperationResult(
        id=operation.id,
        status="passed" if okay else "failed",
        component=operation.component,
        command=operation.command,
        version=version,
        executable_sha256=hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
        evidence_sha256=hashlib.sha256((output.stdout + "\0" + output.stderr).encode()).hexdigest(),
        artifact_id=output.stdout.strip()
        if operation.provider == "docker"
        and re.fullmatch(r"sha256:[a-f0-9]{64}", output.stdout.strip())
        else None,
        duration_ms=round((time.monotonic() - started) * 1000),
        detail="declared command and postconditions passed"
        if okay
        else (
            "provider timed out"
            if output.timed_out
            else "truncated provider evidence"
            if output.output_truncated
            else "expected output missing: " + (output.stderr or output.stdout)[-3000:]
            if not expected
            else (output.stderr or output.stdout)[-3000:]
        ),
    )


def _run_sandboxed(
    operation: Operation, stage: Path, allow_network: bool, policy: SandboxPolicy
) -> OperationResult:
    if operation.provider == "docker":
        return OperationResult(
            id=operation.id,
            status="unavailable",
            detail="nested image build is unsupported; no daemon socket enters the sandbox",
        )
    if operation.network and not allow_network:
        return OperationResult(
            id=operation.id,
            status="unauthorized",
            detail="network disabled; verification incomplete",
        )
    cwd = stage / operation.component
    cwd.mkdir(parents=True, exist_ok=True)
    chosen = SandboxPolicy.model_validate(
        {
            **policy.model_dump(),
            "writable": True,
            "trusted": True,
            "image": policy.image or PROVIDER_IMAGES.get(operation.provider),
            "network": "unrestricted" if operation.network else "none",
            "authorize_network": operation.network and allow_network,
            "timeout": 240,
            "scratch_mb": 1024,
            "memory_mb": 4096 if operation.provider in {"terraform", "tofu"} else 2048,
            "file_size_mb": 1024 if operation.provider in {"terraform", "tofu"} else 128,
        }
    )
    try:
        provider = PROVIDERS[operation.provider]
        if provider.registry_integrity:
            metadata = execute(
                [
                    operation.command[0],
                    "view",
                    f"{provider.package}@{provider.version}",
                    "dist.integrity",
                    "--json",
                ],
                stage,
                chosen,
                tool=provider.id,
            )
            if (
                metadata.result.returncode
                or json.loads(metadata.result.stdout) != provider.registry_integrity
            ):
                raise ValueError("pinned provider registry integrity could not be verified")
        execution = execute(
            operation.command, stage, chosen, cwd=operation.component, tool=provider.id
        )
        output = execution.result
        expected = all(
            (cwd / f).is_file() and not (cwd / f).is_symlink() for f in operation.expected_files
        )
        okay = (
            output.returncode == 0
            and not output.timed_out
            and not output.output_truncated
            and expected
        )
        return OperationResult(
            id=operation.id,
            component=operation.component,
            status="passed" if okay else "failed",
            command=operation.command,
            version=execution.evidence.image_id,
            evidence_sha256=hashlib.sha256(
                (output.stdout + "\0" + output.stderr).encode()
            ).hexdigest(),
            duration_ms=execution.evidence.duration_ms,
            sandbox=execution.evidence.model_dump(mode="json"),
            detail="declared command and postconditions passed"
            if okay
            else (output.stderr + output.stdout)[-3000:]
            or "provider failed/timed out or expected output missing",
        )
    except (SandboxUnavailable, OSError, ValueError) as exc:
        return OperationResult(
            id=operation.id,
            component=operation.component,
            status="unavailable" if isinstance(exc, SandboxUnavailable) else "failed",
            command=operation.command,
            detail=str(exc),
        )


def _inventory(stage: Path) -> dict[str, str]:
    # Native generator output is not trusted to be portable just because the command succeeded.
    for directory, dirs, files in os.walk(stage):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"provider generated a symbolic link: {path.relative_to(stage)}")
            if not path.is_file() and not path.is_dir():
                raise ValueError(f"provider generated a special file: {path.relative_to(stage)}")
        dirs[:] = [
            name
            for name in dirs
            if name not in {"node_modules", ".venv", ".git", ".blueprint-state"}
        ]
    source_files, _ = iter_project_files(stage, reject_oversized=True)
    return {
        p.relative_to(stage).as_posix(): hashlib.sha256(
            read_bytes_bounded(p, MAX_MANIFEST_BYTES, root=stage)
        ).hexdigest()
        for p in source_files
    }


def create_project(
    path: Path,
    plan: GenesisPlan,
    *,
    allow_network: bool = False,
    trust_providers: bool = False,
    sandbox: SandboxPolicy | None = None,
    staging_path: Path | None = None,
    stage_id: str | None = None,
) -> GenesisResult:
    started = time.monotonic()
    destination = _destination(path)
    retained_stage = _destination(staging_path) if staging_path is not None else None
    if retained_stage is not None:
        if (
            not isinstance(stage_id, str)
            or not re.fullmatch(r"[a-f0-9-]{36}", stage_id)
            or retained_stage == destination
            or retained_stage.parent.stat().st_dev != destination.parent.stat().st_dev
        ):
            raise ValueError("configure an exact stage identity and same-filesystem destination")
    elif stage_id is not None:
        raise ValueError("a stage identity requires a retained staging directory")

    def retain_failed_stage(stage: Path) -> None:
        if retained_stage is None or not stage.is_dir() or retained_stage.exists():
            return
        try:
            _stage_metadata(stage, plan, stage_id or "", result)
        except (OSError, ValueError):
            # Preserve the native output even when malformed provider output prevents
            # writing an owner record; inspection will report the missing receipt.
            pass
        _publish_fresh(stage, retained_stage)

    # Serialized plans are descriptive data; commands are always recomputed from validated intent.
    authoritative = plan_project(plan.intent)
    if authoritative.digest() != plan.digest():
        raise ValueError("plan differs from the trusted resolver; regenerate it before execution")
    result = GenesisResult(status="failed", destination=str(destination), plan_sha256=plan.digest())
    policy = sandbox or SandboxPolicy(
        backend="host" if trust_providers else "auto",
        trusted=trust_providers,
        writable=trust_providers,
    )
    backend = "host"
    if trust_providers and any(op.command for op in plan.operations):
        try:
            backend = resolve_backend(policy)
        except (OSError, SandboxUnavailable) as exc:
            result.operations.append(
                OperationResult(id="preflight:sandbox", status="unavailable", detail=str(exc))
            )
            result.detail = "sandbox unavailable; destination unchanged"
            return result
    if not trust_providers and any(op.command for op in plan.operations):
        result.operations.append(
            OperationResult(
                id="preflight:trust",
                status="unauthorized",
                detail="provider execution requires --trust-providers",
            )
        )
    elif any(op.command for op in plan.operations) and backend != "host":
        for provider in plan.providers:
            if provider.executable and provider.id != "docker":
                try:
                    image_identity(
                        backend,
                        policy.image or PROVIDER_IMAGES.get(provider.id, ""),
                    )
                except SandboxUnavailable as exc:
                    result.operations.append(
                        OperationResult(
                            id="preflight:" + provider.id, status="unavailable", detail=str(exc)
                        )
                    )
    else:
        result.operations.extend(
            preflight(plan, allow_network=allow_network, trust_providers=trust_providers)
        )
    if result.operations:
        result.detail = "preflight failed; destination unchanged"
        return result
    work_parent = retained_stage.parent if retained_stage is not None else destination.parent
    with tempfile.TemporaryDirectory(prefix=".blueprint-genesis-", dir=work_parent) as temporary:
        work = Path(temporary)
        stage = work / plan.identity.repository
        stage.mkdir()
        cache = work / "cache"
        cache.mkdir()
        blocked: set[str] = set()
        try:
            for operation in plan.operations:
                if operation.action == "strengthen" and operation.provider == "builtin":
                    strengthen(stage, plan)
                    result.operations.append(
                        OperationResult(
                            id=operation.id,
                            status="passed",
                            detail="capabilities applied in staging",
                        )
                    )
                    continue
                if not operation.command:
                    if operation.id == "initialize:spring":
                        if not allow_network:
                            result.operations.append(
                                OperationResult(
                                    id=operation.id,
                                    status="unauthorized",
                                    detail="Initializr requires explicit network",
                                )
                            )
                            result.detail = "initialization unauthorized; destination unchanged"
                            retain_failed_stage(stage)
                            return result
                        from blueprint_ai.genesis.archive import initialize_spring

                        archive_hash = initialize_spring(
                            stage, plan.identity.package, plan.identity.module
                        )
                        _inventory(stage)
                        result.operations.append(
                            OperationResult(
                                id=operation.id,
                                status="passed",
                                evidence_sha256=archive_hash,
                                detail=(
                                    "bounded official Initializr 4.1.1 archive; remote hooks not "
                                    "executed"
                                ),
                            )
                        )
                        continue
                    if operation.id == "verify:kubernetes":
                        import yaml

                        manifest = yaml.safe_load(
                            read_text_bounded(
                                stage / "namespace.yaml", MAX_MANIFEST_BYTES, root=stage
                            )
                        )
                        if (
                            manifest.get("kind") != "Namespace"
                            or manifest.get("apiVersion") != "v1"
                        ):
                            raise ValueError("invalid namespace manifest")
                    if operation.id == "verify:openapi":
                        from openapi_spec_validator import validate

                        for spec in stage.rglob("openapi.json"):
                            if "node_modules" not in spec.parts and ".venv" not in spec.parts:
                                validate(
                                    json.loads(
                                        read_text_bounded(spec, MAX_MANIFEST_BYTES, root=stage)
                                    )
                                )
                    result.operations.append(
                        OperationResult(
                            id=operation.id,
                            status="passed",
                            detail="native schema validation passed",
                        )
                    )
                    continue
                if operation.component in blocked and operation.id != "strengthen":
                    result.operations.append(
                        OperationResult(
                            id=operation.id,
                            status="partial",
                            component=operation.component,
                            command=operation.command,
                            detail="dependency installation prerequisite did not complete",
                        )
                    )
                    continue
                receipt = _run(operation, stage, cache, allow_network, sandbox=policy)
                result.operations.append(receipt)
                if receipt.status == "passed" and operation.action == "initialize":
                    _inventory(stage)
                if receipt.status != "passed":
                    if operation.action == "initialize" or receipt.status == "failed":
                        result.detail = f"{operation.id} failed; staged output rolled back"
                        result.duration_ms = round((time.monotonic() - started) * 1000)
                        retain_failed_stage(stage)
                        return result
                    blocked.add(operation.component)
            result.files = _inventory(stage)
            # Review exactly the source that will be published, excluding build caches/dependencies.
            publish = work / "publish"
            publish.mkdir()
            for rel in result.files:
                target = publish / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / rel, target)
            facts = discover_project(publish)
            if plan.intent.api_client:
                facts.graph.relationships.append(
                    Relationship(
                        source="backend/openapi.json",
                        target="frontend/src/generated/api.d.ts",
                        kind="generated-client",
                        evidence=Evidence(
                            file=".blueprint-ai/genesis.json",
                            kind="provider",
                            detail="openapi-typescript@7.13.0",
                        ),
                    )
                )
            context, _ = make_context(publish, model_mode="off", trust_project_executables=False)
            context.config["offline"] = not allow_network
            report = review(context)
            report.facts.path = str(destination)
            result.review_summary = {r.blueprint: r.status for r in report.results}
            owners = {
                rel: (owner.id if (owner := facts.graph.owner(rel)) else ".")
                for rel in result.files
            }
            result.status = "partial" if blocked else "verified"
            if any(f.priority in {"P0", "P1"} for f in report.active_findings):
                result.status = "partial"
            result.duration_ms = round((time.monotonic() - started) * 1000)
            result.detail = (
                "generated-project verifiers completed; review assurance is reported separately"
            )
            json_file(
                publish,
                ".blueprint-ai/genesis.json",
                {
                    "schema_version": "1.0",
                    "artifact_owners": owners,
                    "isolation": policy.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                    "analyzer": analyzer_identity(),
                    "graph": facts.graph.model_dump(mode="json"),
                },
            )
            json_file(publish, ".blueprint-ai/review.json", report.model_dump(mode="json"))
            # Recheck immediately before rename: a concurrent new destination is a conflict.
            _destination(destination)
            if retained_stage is not None:
                _stage_metadata(publish, plan, stage_id or "", result)
                _publish_fresh(publish, retained_stage)
            else:
                _publish_fresh(publish, destination)
        except Exception as exc:
            result.status = "failed"
            result.detail = f"{type(exc).__name__}: {exc}; destination unchanged"
            result.duration_ms = round((time.monotonic() - started) * 1000)
            retain_failed_stage(stage)
    return result
