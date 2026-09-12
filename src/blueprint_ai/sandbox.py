"""Operator-owned execution policy. No untrusted host fallback and no implicit image pull."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blueprint_ai.safety import ProcessResult, controlled_env, run_process

Backend = Literal["auto", "docker", "podman", "gvisor", "host"]
Network = Literal["none", "loopback", "allowlist", "normal"]
_CONFIG_LOCK = threading.Lock()
_CLIENT_CONFIG: tempfile.TemporaryDirectory[str] | None = None


def _runtime_environment() -> dict[str, str]:
    # Docker otherwise discovers ~/.docker/config.json even when HOME is absent, and can
    # inject its configured proxies into a container. Public tool acquisition needs no credentials.
    global _CLIENT_CONFIG
    with _CONFIG_LOCK:
        if _CLIENT_CONFIG is None:
            _CLIENT_CONFIG = tempfile.TemporaryDirectory(prefix="blueprint-runtime-config-")
            (Path(_CLIENT_CONFIG.name) / "config.json").write_text('{"auths":{},"proxies":{}}')
        return controlled_env({"DOCKER_CONFIG": _CLIENT_CONFIG.name})


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: Backend = "auto"
    network: Network = "none"
    trusted: bool = False
    authorize_network: bool = False
    writable: bool = False
    image: str | None = None
    timeout: float = Field(default=120, gt=0, le=3600)
    memory_mb: int = Field(default=1024, ge=64, le=16384)
    cpus: float = Field(default=2, gt=0, le=16)
    pids: int = Field(default=128, ge=16, le=1024)
    scratch_mb: int = Field(default=256, ge=16, le=8192)
    file_size_mb: int = Field(default=128, ge=1, le=1024)
    output_bytes: int = Field(default=4_000_000, ge=1024, le=16_000_000)

    @model_validator(mode="after")
    def authorization(self) -> SandboxPolicy:
        if self.backend == "host" and not self.trusted:
            raise ValueError("host execution requires explicit project/provider trust")
        if self.network == "allowlist":
            raise ValueError("allowlisted egress is unavailable; select none or loopback")
        if self.network == "normal" and not (self.authorize_network and self.trusted):
            raise ValueError("normal network requires explicit network authorization and trust")
        if self.writable and not self.trusted:
            raise ValueError("writable target execution requires explicit trust")
        return self


class SandboxEvidence(BaseModel):
    backend: str
    tool: str
    policy: SandboxPolicy
    isolated: bool = False
    image_id: str | None = None
    runtime_version: str | None = None
    rootless: bool | None = None
    target_read_only: bool = False
    network_enforced: bool = False
    limits_enforced: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    output_truncated: bool = False
    oom_killed: bool = False
    teardown: bool = False
    duration_ms: int = 0
    detail: str = ""


@dataclass
class Execution:
    result: ProcessResult
    evidence: SandboxEvidence


class SandboxUnavailable(RuntimeError):
    """The requested boundary cannot be enforced. Never retry on the host."""


def _local_runtime(name: str) -> tuple[list[str], dict[str, str]]:
    executable = shutil.which(name)
    if not executable:
        raise SandboxUnavailable(f"{name} executable unavailable")
    # Only local engines: remote contexts can resolve bind paths on an unrelated host.
    if name == "docker":
        sockets = [Path("/var/run/docker.sock"), Path.home() / ".docker/run/docker.sock"]
    else:
        sockets = (
            [Path(f"/run/user/{os.getuid()}/podman/podman.sock")] if hasattr(os, "getuid") else []
        )
        # Native Linux Podman does not need a daemon/socket.
        if os.name == "posix" and os.uname().sysname == "Linux":
            return [executable, "--remote=false"], _runtime_environment()
    for socket in sockets:
        if socket.exists() and socket.is_socket():
            flag = "--host" if name == "docker" else "--url"
            return [executable, flag, "unix://" + str(socket)], _runtime_environment()
    raise SandboxUnavailable(f"local {name} engine unavailable; remote contexts are unsupported")


def runtime_info(backend: str) -> dict:
    prefix, env = _local_runtime("docker" if backend == "gvisor" else backend)
    probe = run_process(
        [*prefix, "info", "--format", "json" if backend == "podman" else "{{json .}}"],
        Path(tempfile.gettempdir()),
        10,
        env=env,
        output_limit=256_000,
    )
    if probe.returncode or probe.output_truncated:
        raise SandboxUnavailable(f"{backend} engine unavailable: {probe.stderr[-300:]}")
    try:
        info = json.loads(probe.stdout)
        if not isinstance(info, dict):
            raise ValueError("invalid runtime info")
    except ValueError as exc:
        raise SandboxUnavailable(f"{backend} returned malformed engine metadata") from exc
    if backend == "podman":
        host = info.get("host", {})
        if host.get("cgroupVersion") != "v2" or not {"cpu", "memory", "pids"}.issubset(
            host.get("cgroupControllers", [])
        ):
            raise SandboxUnavailable("Podman needs delegated cgroup v2 CPU, memory and PID limits")
    elif not all(
        info.get(capability) is True
        for capability in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit")
    ):
        raise SandboxUnavailable("Docker engine cannot confirm required resource-limit support")
    if backend == "gvisor" and "runsc" not in info.get("Runtimes", {}):
        raise SandboxUnavailable("gVisor/runsc is not configured in the local Docker engine")
    return info


def resolve_backend(policy: SandboxPolicy) -> str:
    if policy.backend == "host":
        return "host"
    if policy.backend != "auto":
        runtime_info(policy.backend)
        return policy.backend
    # An explicit trust request preserves the existing native-tool workflow.
    if policy.trusted and not policy.image:
        return "host"
    for backend in ("podman", "gvisor", "docker"):
        try:
            runtime_info(backend)
            return backend
        except (OSError, SandboxUnavailable):
            continue
    raise SandboxUnavailable("no local OCI sandbox available; untrusted execution was not run")


def image_identity(backend: str, image: str) -> str:
    if not image or image.startswith("-") or any(c.isspace() for c in image):
        raise SandboxUnavailable("invalid image reference")
    prefix, env = _local_runtime("docker" if backend == "gvisor" else backend)
    probe = run_process(
        [*prefix, "image", "inspect", image, "--format", "{{.Id}}"],
        Path(tempfile.gettempdir()),
        10,
        env=env,
        output_limit=16_384,
    )
    digest = probe.stdout.strip()
    if probe.returncode or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        detail = "inspection timed out" if probe.timed_out else probe.stderr.strip()[-300:]
        raise SandboxUnavailable(
            f"local image {image} unavailable; acquire it explicitly first"
            + (f" ({detail})" if detail else "")
        )
    return digest


def _target(root: Path, cwd: str) -> Path:
    absolute = root.absolute()
    # macOS's system aliases /tmp and /var are canonicalized, never target-defined aliases.
    for parent in (absolute, *absolute.parents):
        if parent.is_symlink() and parent not in {Path("/tmp"), Path("/var")}:
            raise SandboxUnavailable("target crosses a symlink boundary")
    root = absolute.resolve(strict=True)
    relative = Path(cwd)
    if relative.is_absolute() or ".." in relative.parts:
        raise SandboxUnavailable("working directory must remain within the target")
    candidate = root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise SandboxUnavailable("working directory crosses a symlink boundary")
    if not candidate.is_dir() or not root.is_dir():
        raise SandboxUnavailable("target and working directory must be directories")
    if any(c in str(root) for c in (",", "\n", "\r", "\0")):
        raise SandboxUnavailable("target contains unsupported mount-delimiter characters")
    return root


def container_command(
    prefix: list[str],
    backend: str,
    image: str,
    name: str,
    root: Path,
    cwd: str,
    command: list[str],
    policy: SandboxPolicy,
    identity_directory: Path | None = None,
) -> list[str]:
    if not command or not command[0] or "\0" in "".join(command):
        raise ValueError("an executable and valid argument array are required")
    uid = os.getuid() if hasattr(os, "getuid") else 10001
    gid = os.getgid() if hasattr(os, "getgid") else 10001
    uid = uid or 10001
    gid = gid or 10001
    mount = f"type=bind,src={root},dst=/workspace"
    if backend in {"docker", "gvisor"}:
        mount += ",bind-recursive=disabled"
    mount += "" if policy.writable else ",readonly"
    args = [
        *prefix,
        "run",
        "--name",
        name,
        "--pull=never",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        f"{uid}:{gid}",
        "--pids-limit",
        str(policy.pids),
        "--cpus",
        str(policy.cpus),
        "--memory",
        f"{policy.memory_mb}m",
        "--memory-swap",
        f"{policy.memory_mb}m",
        "--ulimit",
        "nofile=1024:1024",
        "--ulimit",
        f"fsize={policy.file_size_mb * 1024 * 1024}:{policy.file_size_mb * 1024 * 1024}",
        "--network",
        "bridge" if policy.network == "normal" else "none",
        "--ipc=private",
        "--shm-size=16m",
        "--log-driver=none",
        "--mount",
        mount,
        "--tmpfs",
        f"/tmp:rw,exec,nosuid,nodev,size={policy.scratch_mb}m,mode=1777",
        "--workdir",
        "/workspace" + ("/" + cwd if cwd != "." else ""),
    ]
    if backend == "podman":
        args.extend(["--userns=keep-id", "--image-volume=ignore"])
    if backend == "gvisor":
        args.extend(["--runtime=runsc"])
    if identity_directory is not None:
        # Synthetic identities only: never expose the host's account databases.
        for filename in ("passwd", "group"):
            args.extend(
                [
                    "--mount",
                    f"type=bind,src={identity_directory / filename},dst=/etc/{filename},readonly",
                ]
            )
    # No inherited host environment, credentials, SSH agent, Docker socket or arbitrary mounts.
    for key, value in {
        "HOME": "/tmp",
        "USER": "blueprint",
        "LOGNAME": "blueprint",
        "XDG_CACHE_HOME": "/tmp/cache",
        "XDG_CONFIG_HOME": "/tmp/config",
        "TMPDIR": "/tmp",
        "CI": "1",
        "NO_COLOR": "1",
        "NEXT_TELEMETRY_DISABLED": "1",
        "PULUMI_SKIP_UPDATE_CHECK": "true",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
        "RUFF_CACHE_DIR": "/tmp/ruff",
        "MYPY_CACHE_DIR": "/tmp/mypy",
        "UV_CACHE_DIR": "/tmp/uv",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1",
        "UV_OFFLINE": "0" if policy.network == "normal" else "1",
        "npm_config_cache": "/tmp/npm",
        "npm_config_ignore_scripts": "true",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "GOCACHE": "/tmp/go-build",
        "GOMODCACHE": "/tmp/go-mod",
        "GOTOOLCHAIN": "local",
        "CARGO_HOME": "/tmp/cargo-home",
        "CARGO_TARGET_DIR": "/tmp/cargo-target",
        "TRIVY_CACHE_DIR": "/tmp/trivy",
        "DOTNET_CLI_HOME": "/tmp/dotnet",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "NUGET_PACKAGES": "/workspace/.blueprint-state/nuget" if policy.writable else "/tmp/nuget",
        "CHECKPOINT_DISABLE": "1",
        "MAVEN_OPTS": "-Duser.home=/tmp",
    }.items():
        args.extend(["--env", f"{key}={value}"])
    args.extend(["--entrypoint", command[0], image, *command[1:]])
    return args


def execute(
    command: list[str],
    root: Path,
    policy: SandboxPolicy,
    *,
    cwd: str = ".",
    tool: str | None = None,
    host_env: dict[str, str] | None = None,
) -> Execution:
    policy = SandboxPolicy.model_validate(policy.model_dump())  # revalidate copied policies
    root = _target(root, cwd)
    backend = resolve_backend(policy)
    evidence = SandboxEvidence(backend=backend, tool=tool or command[0], policy=policy)
    started = time.monotonic()
    if backend == "host":
        if not policy.trusted:
            raise SandboxUnavailable("host execution requires explicit trust")
        result = run_process(
            command, root / cwd, policy.timeout, output_limit=policy.output_bytes, env=host_env
        )
        evidence.detail = (
            "Trusted host execution; filesystem, network and resource isolation are not enforced"
        )
        evidence.teardown = True
    else:
        if not policy.image:
            raise SandboxUnavailable(
                "sandbox tool image unavailable; use tools plan/install or --sandbox-image"
            )
        prefix, env = _local_runtime("docker" if backend == "gvisor" else backend)
        info = runtime_info(backend)
        identity = image_identity(backend, policy.image)
        # Image-declared anonymous volumes can otherwise introduce unbounded writable storage.
        inspected = run_process(
            [*prefix, "image", "inspect", identity, "--format", "{{json .Config.Volumes}}"],
            Path(tempfile.gettempdir()),
            10,
            env=env,
            output_limit=16_384,
        )
        if inspected.returncode or inspected.stdout.strip() not in {"null", "{}", ""}:
            raise SandboxUnavailable(
                "image-declared volumes are unsupported; use a volume-free tool image"
            )
        name = "blueprint-sandbox-" + uuid.uuid4().hex
        identity_files = tempfile.TemporaryDirectory(prefix="blueprint-identity-")
        identity_directory = Path(identity_files.name)
        uid = (os.getuid() or 10001) if hasattr(os, "getuid") else 10001
        gid = (os.getgid() or 10001) if hasattr(os, "getgid") else 10001
        (identity_directory / "passwd").write_text(
            f"blueprint:x:{uid}:{gid}:Sandbox:/tmp:/bin/false\n"
        )
        (identity_directory / "group").write_text(f"blueprint:x:{gid}:\n")
        invocation = container_command(
            prefix, backend, identity, name, root, cwd, command, policy, identity_directory
        )
        evidence.image_id = identity
        evidence.runtime_version = str(
            info.get("ServerVersion", info.get("version", {}).get("Version", "unknown"))
        )
        evidence.rootless = bool(
            info.get("host", {}).get("security", {}).get("rootless", False)
            or any("rootless" in str(value) for value in info.get("SecurityOptions", []))
        )
        try:
            result = run_process(
                invocation,
                Path(tempfile.gettempdir()),
                policy.timeout,
                output_limit=policy.output_bytes,
                env=env,
            )
            state = run_process(
                [*prefix, "inspect", name, "--format", "{{json .State}}"],
                Path(tempfile.gettempdir()),
                5,
                env=env,
                output_limit=16_384,
            )
            try:
                lifecycle = json.loads(state.stdout)
                started_at = lifecycle.get("StartedAt", "")
                # A rejected container creation is not evidence that a policy ran.
                evidence.isolated = bool(
                    state.returncode == 0
                    and isinstance(started_at, str)
                    and started_at
                    and not started_at.startswith("0001-")
                )
                evidence.oom_killed = bool(lifecycle.get("OOMKilled", False))
            except (ValueError, AttributeError):
                pass
            evidence.target_read_only = evidence.isolated and not policy.writable
            evidence.network_enforced = evidence.isolated and policy.network in {"none", "loopback"}
            evidence.limits_enforced = evidence.isolated
        finally:
            cleanup = run_process(
                [*prefix, "rm", "--force", "--volumes", name],
                Path(tempfile.gettempdir()),
                10,
                env=env,
                output_limit=16_384,
            )
            evidence.teardown = cleanup.returncode == 0
            identity_files.cleanup()
        evidence.detail = (
            "OCI isolation; shared Linux kernel"
            if backend != "gvisor"
            else "OCI isolation with configured runsc runtime"
        )
        if not evidence.teardown:
            raise SandboxUnavailable(
                f"sandbox teardown failed for {name}; inspect the local engine"
            )
    evidence.exit_code = result.returncode
    evidence.timed_out = result.timed_out
    evidence.output_truncated = result.output_truncated
    evidence.duration_ms = round((time.monotonic() - started) * 1000)
    return Execution(result, evidence)


def doctor() -> list[dict]:
    rows = []
    for backend in ("docker", "podman", "gvisor"):
        try:
            info = runtime_info(backend)
            rows.append(
                {
                    "backend": backend,
                    "available": True,
                    "version": info.get("ServerVersion", info.get("version")),
                    "detail": (
                        "local engine detected; tool image and policy execution still required"
                    ),
                }
            )
        except (OSError, SandboxUnavailable) as exc:
            rows.append({"backend": backend, "available": False, "detail": str(exc)})
    rows.extend(
        [
            {
                "backend": "host",
                "available": True,
                "isolated": False,
                "detail": "explicit trust required",
            },
            {
                "backend": "bubblewrap/nsjail",
                "available": False,
                "detail": (
                    "deferred: Linux deployment, seccomp and cgroup policy requir"
                    "e independent validation"
                ),
            },
        ]
    )
    return rows
