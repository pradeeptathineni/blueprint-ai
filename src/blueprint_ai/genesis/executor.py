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
from blueprint_ai.genesis.assets import json_file, strengthen
from blueprint_ai.genesis.models import GenesisPlan, GenesisResult, Operation, OperationResult
from blueprint_ai.genesis.plan import PROVIDERS, plan_project
from blueprint_ai.safety import controlled_env, run_process


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


def _probe(executable: str, requirement: str | None) -> tuple[str | None, str | None, str]:
    resolved = _resolve_executable(executable)
    if not resolved:
        return None, None, f"{executable} is not installed"
    result = run_process([resolved, "--version"], Path(tempfile.gettempdir()), 5)
    match = re.search(r"(?:^|\s|v)(\d+\.\d+\.\d+(?:[a-z0-9.+-]*))", result.stdout)
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


def _run(operation: Operation, stage: Path, cache: Path, allow_network: bool) -> OperationResult:
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


def _inventory(stage: Path) -> dict[str, str]:
    # Native generator output is not trusted to be portable just because the command succeeded.
    for directory, dirs, files in os.walk(stage):
        dirs[:] = [name for name in dirs if name not in {"node_modules", ".venv", ".git"}]
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"provider generated a symbolic link: {path.relative_to(stage)}")
    source_files, _ = iter_project_files(stage)
    return {
        p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_files
    }


def create_project(
    path: Path, plan: GenesisPlan, *, allow_network: bool = False, trust_providers: bool = False
) -> GenesisResult:
    started = time.monotonic()
    destination = _destination(path)
    # Serialized plans are descriptive data; commands are always recomputed from validated intent.
    authoritative = plan_project(plan.intent)
    if authoritative.digest() != plan.digest():
        raise ValueError("plan differs from the trusted resolver; regenerate it before execution")
    result = GenesisResult(status="failed", destination=str(destination), plan_sha256=plan.digest())
    result.operations.extend(
        preflight(plan, allow_network=allow_network, trust_providers=trust_providers)
    )
    if result.operations:
        result.detail = "preflight failed; destination unchanged"
        return result
    with tempfile.TemporaryDirectory(
        prefix=".blueprint-genesis-", dir=destination.parent
    ) as temporary:
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
                    if operation.id == "verify:openapi":
                        from openapi_spec_validator import validate

                        for spec in stage.rglob("openapi.json"):
                            if "node_modules" not in spec.parts and ".venv" not in spec.parts:
                                validate(json.loads(spec.read_text()))
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
                receipt = _run(operation, stage, cache, allow_network)
                result.operations.append(receipt)
                if receipt.status != "passed":
                    if operation.action == "initialize" or receipt.status == "failed":
                        result.detail = f"{operation.id} failed; staged output rolled back"
                        result.duration_ms = round((time.monotonic() - started) * 1000)
                        return result
                    blocked.add(operation.component)
            _inventory(stage)
            facts = discover_project(stage)
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
            context, _ = make_context(stage, model_mode="off", trust_project_executables=False)
            context.config["offline"] = not allow_network
            report = review(context)
            report.facts.path = str(destination)
            result.review_summary = {r.blueprint: r.status for r in report.results}
            result.files = _inventory(stage)
            owners = {
                rel: (owner.id if (owner := facts.graph.owner(rel)) else ".")
                for rel in result.files
            }
            # Publish only source/lock/metadata, never staging environments with absolute shebangs.
            publish = work / "publish"
            publish.mkdir()
            for rel in result.files:
                target = publish / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / rel, target)
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
                    "isolation": "staged filesystem and controlled environment; no OS sandbox",
                    "plan": plan.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                    "analyzer": analyzer_identity(),
                    "graph": facts.graph.model_dump(mode="json"),
                },
            )
            json_file(publish, ".blueprint-ai/review.json", report.model_dump(mode="json"))
            # Recheck immediately before rename: a concurrent new destination is a conflict.
            _destination(destination)
            _publish_fresh(publish, destination)
        except Exception as exc:
            result.status = "failed"
            result.detail = f"{type(exc).__name__}: {exc}; destination unchanged"
            result.duration_ms = round((time.monotonic() - started) * 1000)
    return result
