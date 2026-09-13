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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blueprint_ai.safety import ProcessResult, controlled_env, run_process

Backend = Literal["auto", "docker", "podman", "gvisor", "host"]
Network = Literal["none", "restricted", "allowlist", "unrestricted"]
_CONFIG_LOCK = threading.Lock()
_CLIENT_CONFIG: tempfile.TemporaryDirectory[str] | None = None


def _runtime_environment(backend: str = "docker") -> dict[str, str]:
    # Docker otherwise discovers ~/.docker/config.json even when HOME is absent, and can
    # inject its configured proxies into a container. Public tool acquisition needs no credentials.
    global _CLIENT_CONFIG
    with _CONFIG_LOCK:
        if _CLIENT_CONFIG is None:
            _CLIENT_CONFIG = tempfile.TemporaryDirectory(prefix="blueprint-runtime-config-")
            directory = Path(_CLIENT_CONFIG.name)
            (directory / "config.json").write_text('{"auths":{},"proxies":{}}')
            (directory / "containers.conf").write_text(
                "[containers]\nenv_host=false\nhttp_proxy=false\n[engine]\nhooks_dir=[]\n"
            )
            (directory / "mounts.conf").write_text("")
            (directory / "hooks").mkdir()
        env = {"DOCKER_CONFIG": _CLIENT_CONFIG.name}
        if backend == "podman":
            # Podman reads host config even without HOME. It can inject mounts, devices,
            # privileges and environment; registry auth also has independent defaults.
            env.update(
                {
                    "CONTAINERS_CONF": str(Path(_CLIENT_CONFIG.name) / "containers.conf"),
                    "REGISTRY_AUTH_FILE": str(Path(_CLIENT_CONFIG.name) / "config.json"),
                }
            )
        return controlled_env(env)


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
    scratch_mb: int = Field(default=1024, ge=16, le=8192)
    file_size_mb: int = Field(default=128, ge=1, le=1024)
    output_bytes: int = Field(default=4_000_000, ge=1024, le=16_000_000)

    @model_validator(mode="before")
    @classmethod
    def legacy_network_names(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if normalized.get("network") == "loopback":
            normalized["network"] = "none"
        elif normalized.get("network") == "normal":
            normalized["network"] = "unrestricted"
        return normalized

    @model_validator(mode="after")
    def authorization(self) -> SandboxPolicy:
        if self.backend == "host" and not self.trusted:
            raise ValueError("host execution requires explicit project/provider trust")
        if self.network in {"restricted", "allowlist"}:
            raise ValueError(
                f"{self.network} egress is unavailable because the local OCI adapters cannot "
                "guarantee destination filtering; select none or explicitly authorize unrestricted"
            )
        if self.network == "unrestricted" and not (self.authorize_network and self.trusted):
            raise ValueError(
                "unrestricted network requires explicit network authorization and trust"
            )
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
    scratch_limit_mb: int | None = None
    file_size_limit_mb: int | None = None
    workspace_writable_limit_mb: int | None = None
    workspace_writable_limit_enforced: bool = False
    workspace_writable_limit_detail: str = "not evaluated"
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
            env = _runtime_environment("podman")
            directory = Path(env["CONTAINERS_CONF"]).parent
            return [
                executable,
                "--remote=false",
                "--default-mounts-file",
                str(directory / "mounts.conf"),
                "--hooks-dir",
                str(directory / "hooks"),
            ], env
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
        if (
            not isinstance(host, dict)
            or host.get("cgroupVersion") != "v2"
            or not isinstance(host.get("cgroupControllers"), list)
            or not {"cpu", "memory", "pids"}.issubset(host["cgroupControllers"])
        ):
            raise SandboxUnavailable("Podman needs delegated cgroup v2 CPU, memory and PID limits")
    elif not all(
        info.get(capability) is True
        for capability in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit")
    ):
        raise SandboxUnavailable("Docker engine cannot confirm required resource-limit support")
    if backend == "gvisor":
        runtimes = info.get("Runtimes")
        if not isinstance(runtimes, dict) or "runsc" not in runtimes:
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
    elif backend == "podman":
        mount += ",bind-nonrecursive"
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
        "bridge" if policy.network == "unrestricted" else "none",
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
        args.extend(
            [
                "--userns=keep-id",
                "--image-volume=ignore",
                "--read-only-tmpfs=false",
                "--http-proxy=false",
                "--env-host=false",
                "--hosts-file=none",
            ]
        )
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
        "UV_OFFLINE": "0" if policy.network == "unrestricted" else "1",
        "npm_config_cache": "/tmp/npm",
        "npm_config_ignore_scripts": "true",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "GOPATH": "/tmp/go",
        "GOCACHE": "/tmp/go-build",
        "GOMODCACHE": "/tmp/go/pkg/mod",
        "GOTOOLCHAIN": "local",
        "CARGO_HOME": "/tmp/cargo-home",
        "CARGO_TARGET_DIR": "/tmp/cargo-target",
        "CARGO_NET_OFFLINE": "false" if policy.network == "unrestricted" else "true",
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


def _initial_evidence(backend: str, tool: str, policy: SandboxPolicy) -> SandboxEvidence:
    if backend == "host":
        workspace_limit = None
        workspace_detail = "trusted host execution has no workspace quota"
    elif policy.writable:
        workspace_limit = None
        workspace_detail = "writable bind mounts have no portable Docker/Podman total-size quota"
    else:
        workspace_limit = 0
        workspace_detail = "target is mounted read-only; writable workspace capacity is zero"
    return SandboxEvidence(
        backend=backend,
        tool=tool,
        policy=policy,
        scratch_limit_mb=None if backend == "host" else policy.scratch_mb,
        file_size_limit_mb=None if backend == "host" else policy.file_size_mb,
        workspace_writable_limit_mb=workspace_limit,
        workspace_writable_limit_detail=workspace_detail,
    )


def _identity_files() -> tempfile.TemporaryDirectory[str]:
    files = tempfile.TemporaryDirectory(prefix="blueprint-identity-")
    directory = Path(files.name)
    uid = (os.getuid() or 10001) if hasattr(os, "getuid") else 10001
    gid = (os.getgid() or 10001) if hasattr(os, "getgid") else 10001
    (directory / "passwd").write_text(f"blueprint:x:{uid}:{gid}:Sandbox:/tmp:/bin/false\n")
    (directory / "group").write_text(f"blueprint:x:{gid}:\n")
    return files


def _record_lifecycle(
    prefix: list[str],
    name: str,
    env: dict[str, str],
    policy: SandboxPolicy,
    result: ProcessResult,
    evidence: SandboxEvidence,
) -> None:
    state = run_process(
        [*prefix, "inspect", name, "--format", "{{json .State}}"],
        Path(tempfile.gettempdir()),
        5,
        env=env,
        output_limit=16_384,
    )
    if state.returncode or state.timed_out or state.output_truncated:
        raise SandboxUnavailable("sandbox lifecycle inspection failed; execution incomplete")
    try:
        lifecycle = json.loads(state.stdout)
        if not isinstance(lifecycle, dict):
            raise ValueError("invalid lifecycle metadata")
        started_at = lifecycle.get("StartedAt", "")
        # A rejected container creation is not evidence that a policy ran.
        evidence.isolated = bool(
            isinstance(started_at, str) and started_at and not started_at.startswith("0001-")
        )
        evidence.oom_killed = bool(lifecycle.get("OOMKilled", False))
    except (ValueError, AttributeError) as exc:
        raise SandboxUnavailable("sandbox returned malformed lifecycle metadata") from exc
    if result.returncode == 0 and not evidence.isolated:
        raise SandboxUnavailable("sandbox startup could not be confirmed; execution incomplete")
    evidence.target_read_only = evidence.isolated and not policy.writable
    evidence.network_enforced = evidence.isolated and policy.network == "none"
    evidence.limits_enforced = evidence.isolated
    evidence.workspace_writable_limit_enforced = evidence.isolated and not policy.writable


def _execute_oci(
    command: list[str],
    root: Path,
    cwd: str,
    backend: str,
    policy: SandboxPolicy,
    evidence: SandboxEvidence,
) -> ProcessResult:
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
    identity_files = _identity_files()
    invocation = container_command(
        prefix, backend, identity, name, root, cwd, command, policy, Path(identity_files.name)
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
        _record_lifecycle(prefix, name, env, policy, result, evidence)
    finally:
        try:
            cleanup = run_process(
                [*prefix, "rm", "--force", "--volumes", name],
                Path(tempfile.gettempdir()),
                10,
                env=env,
                output_limit=16_384,
            )
            evidence.teardown = cleanup.returncode == 0 and not cleanup.timed_out
        except OSError as exc:
            raise SandboxUnavailable(
                f"sandbox teardown failed for {name}; inspect the local engine"
            ) from exc
        finally:
            identity_files.cleanup()
        if not evidence.teardown:
            raise SandboxUnavailable(
                f"sandbox teardown failed for {name}; inspect the local engine"
            )
    evidence.detail = (
        "OCI isolation; shared Linux kernel"
        if backend != "gvisor"
        else "OCI isolation with configured runsc runtime"
    )
    return result


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
    evidence = _initial_evidence(backend, tool or command[0], policy)
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
        result = _execute_oci(command, root, cwd, backend, policy, evidence)
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
