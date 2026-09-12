"""Release red-team regressions and opt-in, controlled live OCI policy checks."""

from __future__ import annotations

import json
import os
import socket
import time
import tomllib
from pathlib import Path

import pytest

from blueprint_ai import safety, sandbox
from blueprint_ai.safety import ProcessResult


@pytest.mark.parametrize(
    ("network", "cargo_offline", "uv_offline"),
    [("none", "true", "1"), ("unrestricted", "false", "0")],
)
def test_package_managers_follow_network_policy(tmp_path, network, cargo_offline, uv_offline):
    policy = sandbox.SandboxPolicy(
        network=network,
        trusted=network == "unrestricted",
        authorize_network=network == "unrestricted",
    )
    argv = sandbox.container_command(
        ["docker"], "docker", "test", "controlled-test", tmp_path, ".", ["cargo", "test"], policy
    )
    assert f"CARGO_NET_OFFLINE={cargo_offline}" in argv
    assert f"UV_OFFLINE={uv_offline}" in argv
    assert argv[argv.index("--network") + 1] == ("bridge" if network == "unrestricted" else "none")


@pytest.mark.parametrize(
    "inspection",
    [
        ProcessResult(1, "", "engine unavailable", False, False),
        ProcessResult(0, "[]", "", False, False),
        ProcessResult(0, "malformed", "", False, False),
        ProcessResult(0, '{"StartedAt":"0001-01-01T00:00:00Z"}', "", False, False),
    ],
)
def test_success_requires_confirmed_container_lifecycle(tmp_path, monkeypatch, inspection):
    calls = []
    monkeypatch.setattr(sandbox, "resolve_backend", lambda _: "docker")
    monkeypatch.setattr(sandbox, "runtime_info", lambda _: {})
    monkeypatch.setattr(sandbox, "image_identity", lambda *a: "sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox, "_local_runtime", lambda _: (["docker"], {}))

    def run(argv, *args, **kwargs):
        calls.append(argv)
        if argv[1] == "inspect":
            return inspection
        return ProcessResult(0, "null" if argv[1] == "image" else "", "", False, False)

    monkeypatch.setattr(sandbox, "run_process", run)
    with pytest.raises(sandbox.SandboxUnavailable, match="lifecycle|startup"):
        sandbox.execute(["scanner"], tmp_path, sandbox.SandboxPolicy(image="test"))
    assert calls[-1][1:4] == ["rm", "--force", "--volumes"]
    assert not any(argv[0] == "scanner" for argv in calls)


def test_failed_teardown_is_reported_even_after_execution_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "resolve_backend", lambda _: "docker")
    monkeypatch.setattr(sandbox, "runtime_info", lambda _: {})
    monkeypatch.setattr(sandbox, "image_identity", lambda *a: "sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox, "_local_runtime", lambda _: (["docker"], {}))

    def run(argv, *args, **kwargs):
        if argv[1] == "run":
            raise OSError("client connection failed")
        return ProcessResult(1 if argv[1] == "rm" else 0, "null", "", False, False)

    monkeypatch.setattr(sandbox, "run_process", run)
    with pytest.raises(sandbox.SandboxUnavailable, match="teardown failed"):
        sandbox.execute(["scanner"], tmp_path, sandbox.SandboxPolicy(image="test"))


def test_optional_backends_reject_missing_runtime_guarantees(monkeypatch):
    monkeypatch.setattr(sandbox, "_local_runtime", lambda _: (["runtime"], {}))
    metadata: dict[str, object] = {"host": {"cgroupVersion": "v2", "cgroupControllers": None}}
    monkeypatch.setattr(
        sandbox,
        "run_process",
        lambda *a, **k: ProcessResult(0, json.dumps(metadata), "", False, False),
    )
    with pytest.raises(sandbox.SandboxUnavailable, match="delegated cgroup"):
        sandbox.runtime_info("podman")
    metadata = dict.fromkeys(["MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit"], True)
    metadata["Runtimes"] = None
    with pytest.raises(sandbox.SandboxUnavailable, match="runsc is not configured"):
        sandbox.runtime_info("gvisor")


@pytest.mark.skipif(os.name != "posix", reason="native Podman adapter requires Linux")
def test_podman_ignores_implicit_host_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTAINERS_CONF", "/host/unsafe-config")
    monkeypatch.setenv("CONTAINERS_CONF_OVERRIDE", "/host/unsafe-override")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", "/host/credentials")
    env = sandbox._runtime_environment("podman")
    assert "CONTAINERS_CONF_OVERRIDE" not in env
    assert env["CONTAINERS_CONF"] != os.environ["CONTAINERS_CONF"]
    config = tomllib.loads(Path(env["CONTAINERS_CONF"]).read_text())
    assert config["containers"] == {"env_host": False, "http_proxy": False}
    assert config["engine"]["hooks_dir"] == []
    assert json.loads(Path(env["REGISTRY_AUTH_FILE"]).read_text())["auths"] == {}
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: "/usr/bin/podman")
    monkeypatch.setattr(sandbox.os, "uname", lambda: type("Uname", (), {"sysname": "Linux"})())
    prefix, _ = sandbox._local_runtime("podman")
    assert Path(prefix[prefix.index("--default-mounts-file") + 1]).read_text() == ""
    assert not list(Path(prefix[prefix.index("--hooks-dir") + 1]).iterdir())
    argv = sandbox.container_command(
        prefix,
        "podman",
        "test",
        "controlled-test",
        tmp_path,
        ".",
        ["true"],
        sandbox.SandboxPolicy(),
    )
    assert "bind-nonrecursive" in argv[argv.index("--mount") + 1]
    assert "--read-only-tmpfs=false" in argv
    assert "--env-host=false" in argv and "--http-proxy=false" in argv
    assert "--hosts-file=none" in argv


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX no-follow descriptors")
@pytest.mark.parametrize("replacement", ["symlink", "fifo", "parent-symlink"])
def test_bounded_reads_reject_paths_replaced_after_preflight(tmp_path, monkeypatch, replacement):
    target = tmp_path / "target"
    target.mkdir()
    child = target / "child"
    child.mkdir()
    path = child / "data.txt"
    path.write_text("authorized")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("outside canary")
    original = safety.safe_regular_file

    def preflight(*args, **kwargs):
        okay = original(*args, **kwargs)
        if replacement == "parent-symlink":
            child.rename(target / "original")
            child.symlink_to(outside, target_is_directory=True)
        else:
            path.unlink()
            if replacement == "symlink":
                path.symlink_to(outside / "data.txt")
            else:
                os.mkfifo(path)
        return okay

    monkeypatch.setattr(safety, "safe_regular_file", preflight)
    with pytest.raises(OSError):
        safety.read_bytes_bounded(path, 100, root=target)


LIVE = pytest.mark.skipif(
    not os.environ.get("BLUEPRINT_SANDBOX_TEST_IMAGE"),
    reason="set BLUEPRINT_SANDBOX_TEST_IMAGE for live OCI policy checks",
)


def _record(label: str, execution: sandbox.Execution) -> dict:
    evidence = {
        "evidence": execution.evidence.model_dump(mode="json"),
        "stdout": execution.result.stdout,
        "stderr": execution.result.stderr,
    }
    if directory := os.environ.get("BLUEPRINT_SANDBOX_EVIDENCE_DIR"):
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{label}.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def _run(label: str, target: Path, script: str, **options):
    execution = sandbox.execute(
        ["python", "-c", script],
        target,
        sandbox.SandboxPolicy(
            backend="docker", image=os.environ["BLUEPRINT_SANDBOX_TEST_IMAGE"], **options
        ),
    )
    evidence = _record(label, execution)
    assert execution.result.returncode == 0, evidence
    assert execution.evidence.isolated and execution.evidence.teardown
    return execution


@LIVE
def test_live_writable_stage_and_cache_isolation(tmp_path):
    canary = tmp_path / "operator-owned.txt"
    canary.write_text("preserve")
    writable = _run(
        "writable-stage",
        tmp_path,
        "from pathlib import Path; Path('/workspace/created.txt').write_text('created'); "
        "Path('/tmp/cache').mkdir(); Path('/tmp/cache/poison').write_text('discard')",
        trusted=True,
        writable=True,
    )
    assert writable.evidence.workspace_writable_limit_mb is None
    assert not writable.evidence.workspace_writable_limit_enforced
    assert "no portable" in writable.evidence.workspace_writable_limit_detail
    assert canary.read_text() == "preserve"
    assert (tmp_path / "created.txt").read_text() == "created"
    clean = _run(
        "cache-isolation",
        tmp_path,
        "from pathlib import Path; assert not Path('/tmp/cache/poison').exists(); "
        "assert Path('/workspace/created.txt').read_text() == 'created'; print('fresh cache')",
    )
    assert clean.evidence.target_read_only
    assert clean.evidence.workspace_writable_limit_mb == 0
    assert clean.evidence.workspace_writable_limit_enforced


@LIVE
def test_live_private_loopback_and_explicit_network(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        _run(
            "network-none",
            tmp_path,
            "import socket\n"
            f"try: socket.create_connection(('127.0.0.1', {port}), timeout=.2)\n"
            "except OSError: pass\n"
            "else: raise AssertionError('host loopback accessible')\n"
            "server=socket.socket(); server.bind(('127.0.0.1',0)); server.listen(); "
            "client=socket.create_connection(server.getsockname(),timeout=1); "
            "accepted,_=server.accept(); client.close(); accepted.close(); server.close(); "
            "print('private loopback only')",
            network="none",
        )
    online = _run(
        "network-explicit",
        tmp_path,
        "import urllib.request; "
        "response=urllib.request.urlopen('https://pypi.org/simple/packaging/',timeout=10); "
        "assert response.status == 200; print(response.status); response.close()",
        network="unrestricted",
        trusted=True,
        authorize_network=True,
    )
    assert not online.evidence.network_enforced


@LIVE
def test_live_cpu_quota_and_scratch_capacity(tmp_path):
    _run(
        "cpu-and-scratch",
        tmp_path,
        "import json, os, pathlib, time\n"
        "quota,period=map(int,pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split())\n"
        "assert quota/period == .25\n"
        "start=time.monotonic(); cpu=time.process_time()\n"
        "while time.monotonic()-start < 3: pass\n"
        "used=time.process_time()-cpu; elapsed=time.monotonic()-start\n"
        "assert used/elapsed < .5, (used,elapsed)\n"
        "fs=os.statvfs('/tmp'); capacity=fs.f_frsize*fs.f_blocks\n"
        "assert capacity == 16*1024*1024\n"
        "print(json.dumps({'cpu_seconds':used,'wall_seconds':elapsed,'scratch_bytes':capacity}))",
        cpus=0.25,
        scratch_mb=16,
    )


@LIVE
def test_live_detached_children_cannot_persist_after_teardown(tmp_path):
    _run(
        "detached-child",
        tmp_path,
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c',"
        '"import time,pathlib; time.sleep(2); '
        "pathlib.Path('/workspace/escaped').write_text('bad')\""
        "],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
        trusted=True,
        writable=True,
    )
    time.sleep(3)
    assert not (tmp_path / "escaped").exists()


@LIVE
def test_live_blocked_fifo_obeys_timeout(tmp_path):
    fifo = tmp_path / "blocked"
    os.mkfifo(fifo)
    execution = sandbox.execute(
        ["python", "-c", "open('/workspace/blocked').read()"],
        tmp_path,
        sandbox.SandboxPolicy(
            backend="docker",
            image=os.environ["BLUEPRINT_SANDBOX_TEST_IMAGE"],
            timeout=1,
        ),
    )
    _record("blocked-fifo", execution)
    assert execution.result.timed_out and execution.evidence.teardown
    assert fifo.is_fifo()


@pytest.mark.skipif(
    not os.environ.get("BLUEPRINT_SANDBOX_NODE_IMAGE"),
    reason="set BLUEPRINT_SANDBOX_NODE_IMAGE for the live npm lifecycle check",
)
def test_live_project_npm_config_cannot_enable_lifecycle_scripts(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "controlled-lifecycle-fixture",
                "version": "1.0.0",
                "private": True,
                "scripts": {
                    "postinstall": "node -e \"require('fs').writeFileSync('script-ran','bad')\""
                },
            }
        )
    )
    (tmp_path / ".npmrc").write_text("ignore-scripts=false\ncache=/workspace/persistent-cache\n")
    execution = sandbox.execute(
        ["npm", "install", "--offline", "--no-progress"],
        tmp_path,
        sandbox.SandboxPolicy(
            backend="docker",
            image=os.environ["BLUEPRINT_SANDBOX_NODE_IMAGE"],
            writable=True,
            trusted=True,
        ),
    )
    evidence = _record("npm-lifecycle-suppression", execution)
    assert execution.result.returncode == 0, evidence
    assert execution.evidence.isolated and execution.evidence.teardown
    assert (tmp_path / "package-lock.json").is_file()
    assert not (tmp_path / "script-ran").exists()
    assert not (tmp_path / "persistent-cache").exists()
