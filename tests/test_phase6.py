from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from blueprint_ai.adapters.base import (
    CommandResult,
    ExternalToolAdapter,
    parse_ast_grep,
    parse_buf,
    parse_conftest,
    parse_lines,
    parse_markdownlint,
)
from blueprint_ai.adapters.registry import applicable_adapters, known_tools
from blueprint_ai.capabilities import add_capability, plan_add
from blueprint_ai.cli import app
from blueprint_ai.discovery import discover_project
from blueprint_ai.genesis import IntentSpec, plan_project
from blueprint_ai.genesis.archive import unpack_source
from blueprint_ai.remediation import KITS, CapabilityKit, rollback_operation
from blueprint_ai.sandbox import SandboxPolicy, SandboxUnavailable, container_command, execute
from blueprint_ai.support import FAMILIES, TOOLS, tool_classification
from blueprint_ai.support_view import support_markdown
from blueprint_ai.tooling import cached_image, tool_plan


def files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


@pytest.mark.parametrize(
    "options",
    [
        {"backend": "host"},
        {"network": "unrestricted"},
        {"network": "unrestricted", "trusted": True},
        {"network": "restricted"},
        {"network": "allowlist"},
        {"writable": True},
        {"pids": 0},
        {"cpus": 0},
        {"memory_mb": 0},
        {"timeout": 0},
        {"backend": "shell"},
        {"host_mounts": ["/"]},
    ],
)
def test_sandbox_policy_rejects_unenforceable_or_unauthorized_modes(options: dict) -> None:
    with pytest.raises(ValidationError):
        SandboxPolicy.model_validate(options)


def test_sandbox_cannot_fall_back_to_host(monkeypatch, tmp_path: Path) -> None:
    import blueprint_ai.sandbox as sandbox

    calls = []

    def unavailable(_backend):
        raise SandboxUnavailable("not installed")

    monkeypatch.setattr(sandbox, "runtime_info", unavailable)
    monkeypatch.setattr(sandbox, "run_process", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(SandboxUnavailable, match="no local OCI sandbox"):
        execute(["malicious"], tmp_path, SandboxPolicy())
    assert not calls
    adapter = ExternalToolAdapter("unknown", "testing", [], parse_lines)
    status, findings, error = adapter.run(tmp_path)
    assert not status.available and not findings and not calls
    assert status.analysis_state == "sandbox_unavailable"


def test_copied_policy_cannot_bypass_validation(tmp_path: Path) -> None:
    policy = SandboxPolicy().model_copy(update={"backend": "host"})
    with pytest.raises(ValidationError):
        execute(["should-never-run"], tmp_path, policy)


@pytest.mark.parametrize("cwd", ["../escape", "/tmp", "child/../../escape"])
def test_working_directory_boundary(tmp_path: Path, cwd: str) -> None:
    with pytest.raises(SandboxUnavailable, match="within the target"):
        execute(["never"], tmp_path, SandboxPolicy(), cwd=cwd)


def test_symlink_directory_boundary(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxUnavailable, match="symlink"):
        execute(["never"], tmp_path, SandboxPolicy(), cwd="link")


def test_oci_policy_is_common_to_docker_podman_runsc(tmp_path: Path) -> None:
    for backend in ("docker", "podman", "gvisor"):
        for network in ("none",):
            policy = SandboxPolicy(network=network)
            argv = container_command(
                ["runtime"],
                backend,
                "sha256:" + "a" * 64,
                "example",
                tmp_path,
                ".",
                ["python", "-c", "print(1)"],
                policy,
            )
            assert argv[argv.index("--network") + 1] == "none"
            assert "--read-only" in argv and "--pull=never" in argv
            assert "--cap-drop=ALL" in argv and "--security-opt=no-new-privileges" in argv
            assert argv[argv.index("--mount") + 1].endswith(",readonly")
            assert "--pids-limit" in argv and "--memory-swap" in argv
            assert "--privileged" not in argv and "--volume" not in argv
            assert "--entrypoint" in argv
            assert not any(
                "SSH_AUTH_SOCK" in a or "OPENAI_API_KEY" in a or "docker.sock" in a for a in argv
            )
            assert ("--runtime=runsc" in argv) == (backend == "gvisor")


@pytest.mark.parametrize(
    ("legacy", "canonical"), [("loopback", "none"), ("normal", "unrestricted")]
)
def test_legacy_network_names_are_normalized(legacy: str, canonical: str) -> None:
    options: dict[str, object] = {"network": legacy}
    if canonical == "unrestricted":
        options.update(trusted=True, authorize_network=True)
    assert SandboxPolicy.model_validate(options).network == canonical


def test_runtime_requires_confirmed_resource_support(monkeypatch) -> None:
    import blueprint_ai.sandbox as sandbox
    from blueprint_ai.safety import ProcessResult

    metadata = dict.fromkeys(["MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit"], True)
    monkeypatch.setattr(sandbox, "_local_runtime", lambda _: (["docker"], {}))
    monkeypatch.setattr(
        sandbox,
        "run_process",
        lambda *a, **k: ProcessResult(0, json.dumps(metadata), "", False, False),
    )
    assert sandbox.runtime_info("docker") == metadata
    for capability in list(metadata):
        metadata[capability] = False
        with pytest.raises(SandboxUnavailable, match="resource-limit support"):
            sandbox.runtime_info("docker")
        metadata[capability] = True


def test_runtime_client_does_not_load_host_credentials_or_proxies(monkeypatch) -> None:
    import blueprint_ai.sandbox as sandbox

    monkeypatch.setenv("DOCKER_CONFIG", "/synthetic/host-credentials")
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic-private-proxy:9999")
    env = sandbox._runtime_environment()
    assert "HTTP_PROXY" not in env
    assert env["DOCKER_CONFIG"] != os.environ["DOCKER_CONFIG"]
    assert json.loads((Path(env["DOCKER_CONFIG"]) / "config.json").read_text()) == {
        "auths": {},
        "proxies": {},
    }


def test_rejected_container_does_not_claim_isolation(tmp_path: Path, monkeypatch) -> None:
    import blueprint_ai.sandbox as sandbox
    from blueprint_ai.safety import ProcessResult

    monkeypatch.setattr(sandbox, "resolve_backend", lambda _: "docker")
    monkeypatch.setattr(sandbox, "runtime_info", lambda _: {})
    monkeypatch.setattr(sandbox, "image_identity", lambda *a: "sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox, "_local_runtime", lambda _: (["docker"], {}))

    def process(argv, *args, **kwargs):
        if argv[1] == "run":
            return ProcessResult(125, "", "container creation rejected", False, False)
        output = '{"StartedAt":"0001-01-01T00:00:00Z"}' if argv[1] == "inspect" else "null"
        return ProcessResult(0, output, "", False, False)

    monkeypatch.setattr(sandbox, "run_process", process)
    result = execute(["never"], tmp_path, SandboxPolicy(backend="docker", image="test"))
    assert result.result.returncode == 125 and result.evidence.teardown
    assert not result.evidence.isolated
    assert not result.evidence.network_enforced and not result.evidence.limits_enforced
    assert not result.evidence.workspace_writable_limit_enforced


def test_tool_registry_covers_all_registered_adapters() -> None:
    assert len(TOOLS) == 56
    assert {tool.name for tool in known_tools()} <= TOOLS.keys()
    for spec in TOOLS.values():
        assert (
            spec.source.startswith("https://") and spec.license and spec.acquisition and spec.update
        )
        assert tool_classification(spec) in {
            "managed-oci",
            "safely-acquirable",
            "platform-constrained",
            "experimental",
            "deferred",
            "superseded-rejected",
        }
    assert tool_plan("ruff")["command"] == ["docker", "pull", TOOLS["ruff"].image]
    assert not tool_plan("tsc")["command"]
    with pytest.raises(ValueError):
        tool_plan("curl | sh")


def test_support_document_cannot_drift() -> None:
    root = Path(__file__).parents[1]
    assert (root / "docs/support.md").read_text() == support_markdown()


def test_tool_receipt_identity_and_symlink_safety(tmp_path: Path) -> None:
    receipt = {
        "tool": "ruff",
        "image": TOOLS["ruff"].image,
        "backend": "docker",
        "image_id": "sha256:" + "a" * 64,
    }
    path = tmp_path / "ruff-docker.json"
    path.write_text(json.dumps(receipt))
    assert cached_image("ruff", "docker", cache=tmp_path) == receipt["image_id"]
    receipt["image"] = "attacker/tool:latest"
    path.write_text(json.dumps(receipt))
    assert cached_image("ruff", "docker", cache=tmp_path) is None
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    assert cached_image("ruff", "docker", cache=tmp_path) is None


def test_built_tool_receipt_is_bound_to_the_current_integrity_checked_recipe(
    tmp_path: Path,
) -> None:
    plan = tool_plan("react-codemod")
    base_digest = TOOLS["react-codemod"].base_image
    assert base_digest and "integrity mismatch" in " ".join(plan["image_recipe"])
    recipe = "FROM " + base_digest + "\n" + "\n".join(plan["image_recipe"]) + "\n"
    receipt = {
        **plan,
        "base_digest": base_digest,
        "recipe_sha256": hashlib.sha256(recipe.encode()).hexdigest(),
        "image_id": "sha256:" + "b" * 64,
    }
    path = tmp_path / "react-codemod-docker.json"
    path.write_text(json.dumps(receipt))
    assert cached_image("react-codemod", "docker", cache=tmp_path) == receipt["image_id"]
    receipt["image_recipe"] = ["RUN npm install attacker@latest"]
    path.write_text(json.dumps(receipt))
    assert cached_image("react-codemod", "docker", cache=tmp_path) is None


@pytest.mark.parametrize("kind", [f.id for f in FAMILIES.values() if f.initialize])
def test_every_supported_family_has_pure_valid_plan(kind: str, tmp_path: Path) -> None:
    before = files(tmp_path)
    plan = plan_project(IntentSpec.model_validate({"name": "Example Service", "kind": kind}))
    assert plan.digest() == plan_project(plan.intent).digest()
    assert plan.capabilities and all(
        op.provider in {p.id for p in plan.providers} for op in plan.operations
    )
    assert not any(
        verb in {"apply", "destroy", "deploy", "up"}
        for op in plan.operations
        for verb in op.command[1:]
    )
    assert files(tmp_path) == before


def test_iac_cloud_coverage_has_no_resources_or_state(tmp_path: Path) -> None:
    from blueprint_ai.genesis.assets import strengthen

    for tool in ("terraform", "opentofu"):
        for cloud in ("aws", "azure", "gcp"):
            stage = tmp_path / f"{tool}-{cloud}"
            stage.mkdir()
            plan = plan_project(IntentSpec(name="Cloud Baseline", kind=tool, cloud=cloud))
            strengthen(stage, plan)
            text = (stage / "main.tf").read_text()
            assert (
                "required_providers" in text
                and 'resource "' not in text
                and 'backend "' not in text
            )
            assert any("-backend=false" in op.command for op in plan.operations)


def test_add_plan_conflict_apply_and_rollback(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Existing\n")
    before = files(tmp_path)
    plan = plan_add(tmp_path, "security-policy")
    assert plan["files"][0]["status"] == "create" and files(tmp_path) == before
    result = add_capability(tmp_path, discover_project(tmp_path), "security-policy")
    assert result.changed and result.operation_id
    assert plan_add(tmp_path, "security-policy")["files"][0]["status"] == "already-present"
    rolled = rollback_operation(tmp_path, result.operation_id)
    assert rolled.changes[0].status == "rolled_back"
    assert {
        p: b for p, b in files(tmp_path).items() if not p.startswith(".blueprint-ai/")
    } == before
    (tmp_path / "SECURITY.md").write_text("owned by user")
    with pytest.raises(ValueError, match="conflicts"):
        add_capability(tmp_path, discover_project(tmp_path), "security-policy")
    assert (tmp_path / "SECURITY.md").read_text() == "owned by user"


@pytest.mark.parametrize("name", list(KITS))
def test_all_capabilities_restore_the_exact_tree(name: str, tmp_path: Path, monkeypatch) -> None:
    import blueprint_ai.sandbox as sandbox

    def unavailable(_policy):
        raise SandboxUnavailable("test environment has no engine")

    monkeypatch.setattr(sandbox, "resolve_backend", unavailable)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "existing"\nversion = "1"\n')
    (tmp_path / "package.json").write_text('{"name":"existing","version":"1"}\n')
    (tmp_path / "main.py").write_text("value = 1\n")
    (tmp_path / "index.js").write_text("export const value = 1;\n")
    (tmp_path / "README.md").write_text("# Existing\n")
    before = files(tmp_path)
    directories = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_dir()}
    result = add_capability(tmp_path, discover_project(tmp_path), name)
    assert result.changed and result.operation_id
    assert result.verifications[0].status == "passed"
    repeat = add_capability(tmp_path, discover_project(tmp_path), name)
    assert not repeat.changed
    rollback_operation(tmp_path, result.operation_id)
    assert files(tmp_path) == before
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_dir()} == directories


def archive_bytes(entries: list[tuple[str, bytes, int]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, content)
    return stream.getvalue()


@pytest.mark.parametrize(
    "name,content,mode",
    [
        ("../escape", b"bad", stat.S_IFREG),
        ("/absolute", b"bad", stat.S_IFREG),
        ("nested\\escape", b"bad", stat.S_IFREG),
        (".git/hooks/post-checkout", b"bad", stat.S_IFREG),
        ("linked", b"/etc/passwd", stat.S_IFLNK),
        ("fifo", b"", stat.S_IFIFO),
        ("invalid.txt", b"\xff", stat.S_IFREG),
    ],
)
def test_archive_rejects_hostile_entries_before_writes(
    name: str, content: bytes, mode: int, tmp_path: Path
) -> None:
    data = archive_bytes([("safe.txt", b"safe", stat.S_IFREG), (name, content, mode)])
    with pytest.raises((ValueError, UnicodeError)):
        unpack_source(data, tmp_path)
    assert not list(tmp_path.iterdir())


def test_archive_bounds_normalization_and_valid_source(tmp_path: Path) -> None:
    for entries in [
        [(f"{i}.txt", b"x", stat.S_IFREG) for i in range(257)],
        [("a//b", b"one", stat.S_IFREG), ("a/b", b"two", stat.S_IFREG)],
        [("a", b"one", stat.S_IFREG), ("a/b", b"two", stat.S_IFREG)],
        [("large.txt", b"x" * 2_000_001, stat.S_IFREG)],
    ]:
        with pytest.raises(ValueError):
            unpack_source(archive_bytes(entries), tmp_path)
        assert not list(tmp_path.iterdir())
    unpack_source(
        archive_bytes([("src/Example.java", b"class Example {}\n", stat.S_IFREG)]), tmp_path
    )
    assert (tmp_path / "src/Example.java").read_text() == "class Example {}\n"


def test_pulumi_is_infrastructure_without_inventing_application_roles(tmp_path: Path) -> None:
    (tmp_path / "Pulumi.yaml").write_text("name: cloud\nruntime: nodejs\n")
    (tmp_path / "package.json").write_text(
        '{"name":"cloud","main":"index.ts",'
        '"dependencies":{"@pulumi/pulumi":"^3","@pulumi/aws":"^7"}}'
    )
    (tmp_path / "index.ts").write_text('export const cloud = "aws";\n')
    facts = discover_project(tmp_path)
    assert facts.iac == ["pulumi"] and facts.cloud_hints == ["aws"]
    assert "infrastructure-module" in facts.graph.components[0].roles
    assert "library" not in facts.graph.components[0].roles


def test_maven_declared_runtime_and_test_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("""<project xmlns="http://maven.apache.org/POM/4.0.0">
<artifactId>service</artifactId><dependencies>
<dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-webmvc</artifactId></dependency>
<dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>
<scope>test</scope><version>6.0.0</version></dependency>
</dependencies></project>""")
    (tmp_path / "App.java").write_text("class App {}\n")
    facts = discover_project(tmp_path)
    component = facts.graph.components[0]
    assert "api" in component.roles and "spring-boot" in component.frameworks
    assert next(d for d in component.dependencies if "junit" in d.name).scope == "test"
    (tmp_path / "pom.xml").write_text('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x/>')
    assert discover_project(tmp_path).graph.diagnostics


def test_sandbox_cli_emits_structured_evidence(tmp_path: Path, monkeypatch) -> None:
    import blueprint_ai.sandbox as sandbox
    from blueprint_ai.safety import ProcessResult
    from blueprint_ai.sandbox import Execution, SandboxEvidence

    monkeypatch.setattr(
        sandbox,
        "execute",
        lambda *a, **k: Execution(
            ProcessResult(0, "ready\n", "", False, False),
            SandboxEvidence(backend="docker", tool="python", policy=SandboxPolicy()),
        ),
    )
    result = CliRunner().invoke(app, ["sandbox", str(tmp_path), "test-image", "python"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["evidence"]["backend"] == "docker"


def test_proto_routes_to_buf_without_yaml_parser_or_spectral(tmp_path: Path) -> None:
    from blueprint_ai.blueprints.catalog import api_data_config_checks

    (tmp_path / "buf.yaml").write_text("version: v2\n")
    (tmp_path / "contract.proto").write_text('syntax = "proto3";\npackage api.v1;\n')
    facts = discover_project(tmp_path)
    names = {a.name for a in applicable_adapters(facts, "api-data-config", sandboxed=True)}
    assert "buf" in names and "spectral" not in names
    assert not [f for f in api_data_config_checks(tmp_path, facts) if f.rule_id == "invalid-schema"]


def test_failed_capability_verification_rolls_back(tmp_path: Path, monkeypatch) -> None:
    import blueprint_ai.sandbox as sandbox
    from blueprint_ai.safety import ProcessResult
    from blueprint_ai.sandbox import Execution, SandboxEvidence

    policy = SandboxPolicy(backend="docker", image="test")
    monkeypatch.setitem(
        KITS,
        "bad-check",
        CapabilityKit(name="bad-check", files={"generated.txt": "hi"}, verification=[["false"]]),
    )
    monkeypatch.setattr(
        sandbox,
        "execute",
        lambda *a, **k: Execution(
            ProcessResult(1, "", "failure", False, False),
            SandboxEvidence(backend="docker", tool="false", policy=policy),
        ),
    )
    result = add_capability(tmp_path, discover_project(tmp_path), "bad-check", policy=policy)
    assert not result.changed and not (tmp_path / "generated.txt").exists()
    assert result.changes[0].status == "rolled_back"


def test_configured_policy_and_ast_routes(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text("terraform {}\n")
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy/check.rego").write_text("package main\n")
    assert "conftest" in {a.name for a in applicable_adapters(discover_project(tmp_path), "iac")}
    (tmp_path / "main.py").write_text("print(1)\n")
    (tmp_path / "sgconfig.yml").write_text("ruleDirs: [rules]\n")
    assert "ast-grep" in {
        a.name
        for a in applicable_adapters(discover_project(tmp_path), "code-quality", sandboxed=True)
    }
    (tmp_path / "buf.yaml").write_text("version: v2\n")
    assert "buf" in {
        a.name
        for a in applicable_adapters(discover_project(tmp_path), "api-data-config", sandboxed=True)
    }


@pytest.mark.parametrize(
    "name,parser,clean,bad",
    [
        (
            "conftest",
            parse_conftest,
            '[{"filename":"main.tf","successes":1}]',
            (
                '[{"filename":"main.tf","successes":0,"failures":[{"msg":"den'
                'ied","metadata":{"rule":"deny-public"}}]}]'
            ),
        ),
        (
            "ast-grep",
            parse_ast_grep,
            "[]",
            (
                '[{"ruleId":"unsafe-eval","message":"avoid eval","file":"main'
                '.py","range":{"start":{"line":1}}}]'
            ),
        ),
        (
            "buf",
            parse_buf,
            "",
            (
                '{"path":"api.proto","start_line":1,"type":"PACKAGE_DEFINED",'
                '"message":"package missing"}'
            ),
        ),
    ],
)
def test_native_parsers_clean_mutated_repaired_and_malformed(
    tmp_path: Path, name, parser, clean, bad
) -> None:
    adapter = ExternalToolAdapter(name, "security", [], parser)
    assert not parser(CommandResult([], 0, clean, ""), tmp_path, adapter)
    findings = parser(CommandResult([], 1, bad, ""), tmp_path, adapter)
    assert len(findings) == 1 and findings[0].rule_id and findings[0].file
    assert not parser(CommandResult([], 0, clean, ""), tmp_path, adapter)
    with pytest.raises((ValueError, TypeError, KeyError)):
        parser(CommandResult([], 0, '{"bogus": true}', ""), tmp_path, adapter)


@pytest.mark.parametrize("position", ["205", "205:1"])
def test_markdown_diagnostics_with_optional_column(position: str, tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("markdownlint-cli2", "documentation", [], parse_markdownlint)
    result = CommandResult(
        [], 1, "", f"docs/research.md:{position} error MD012/no-multiple-blanks Extra blank line"
    )
    findings = parse_markdownlint(result, tmp_path, adapter)
    assert len(findings) == 1 and findings[0].rule_id == "markdownlint/MD012"
    assert findings[0].range and findings[0].range.start_line == 205
    assert findings[0].severity == "low"


def test_dotnet_and_php_manifest_evidence(tmp_path: Path) -> None:
    (tmp_path / "service.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup><PackageRefe'
        'rence Include="Example" Version="1.0.0" /></ItemGroup></Proj'
        "ect>"
    )
    facts = discover_project(tmp_path)
    component = facts.graph.components[0]
    assert "C#" in component.languages and "api" in component.roles
    assert component.dependencies[0].ecosystem == "NuGet"
    assert "dotnet-build" in {
        a.name for a in applicable_adapters(facts, "code-quality", sandboxed=True)
    }


def test_discovery_works_without_a_git_installation(tmp_path: Path, monkeypatch) -> None:
    import blueprint_ai.discovery.project as project

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(project, "run_process_bytes", missing)
    (tmp_path / "main.py").write_text("answer = 42\n")
    facts = discover_project(tmp_path)
    assert facts.file_count == 1 and "Python" in facts.languages
    assert not facts.is_git and facts.git_branch is None


def test_cli_dry_run_and_generated_docs(tmp_path: Path) -> None:
    runner = CliRunner()
    for argv in (
        ["tools", "install", "ruff", "--dry-run"],
        ["add", "security-policy", str(tmp_path)],
        ["init", str(tmp_path / "new"), "--kind", "go-api", "--dry-run"],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, result.output
        json.loads(result.output)
    assert not files(tmp_path)
    result = runner.invoke(app, ["support", "--markdown"])
    assert result.exit_code == 0 and result.output == support_markdown()


@pytest.mark.skipif(
    not os.environ.get("BLUEPRINT_SANDBOX_TEST_IMAGE"),
    reason="set BLUEPRINT_SANDBOX_TEST_IMAGE for live OCI policy tests",
)
def test_live_hostile_process_filesystem_network_limits_and_teardown(
    tmp_path: Path, monkeypatch
) -> None:
    image = os.environ["BLUEPRINT_SANDBOX_TEST_IMAGE"]
    monkeypatch.setenv("BLUEPRINT_TEST_SECRET", "synthetic-environment-canary")
    # pytest temp directories are outside Documents and shareable by Docker Desktop.
    target = tmp_path / "target"
    target.mkdir()
    secret = tmp_path / "host-canary"
    secret.write_text("synthetic-host-canary")
    (target / "outside").symlink_to(secret)
    script = f"""import os, pathlib, socket, resource
assert os.getuid() != 0
assert not pathlib.Path({str(secret)!r}).exists()
assert not pathlib.Path('/workspace/outside').exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
assert 'BLUEPRINT_TEST_SECRET' not in os.environ
try:
    pathlib.Path('/workspace/owned').write_text('escape')
except OSError: pass
else: raise AssertionError('writable target')
try:
    socket.create_connection(('127.0.0.1', 9), timeout=0.2)
except OSError: pass
else: raise AssertionError('unexpected host listener')
assert len(pathlib.Path('/proc/net/route').read_text().splitlines()) == 1
for address in ('169.254.169.254', '10.0.0.1', '1.1.1.1'):
    try:
        socket.create_connection((address, 80), timeout=0.2)
    except OSError: pass
    else: raise AssertionError('unexpected egress')
assert resource.getrlimit(resource.RLIMIT_FSIZE)[0] == 1048576
try:
    pathlib.Path('/tmp/large').write_bytes(b'x' * 2000000)
except OSError: pass
else: raise AssertionError('file limit not enforced')
pathlib.Path('/tmp/scratch').write_text('allowed')
print('policy passed')
"""
    (target / "hostile.py").write_text(script)
    before = files(target)
    policy = SandboxPolicy(backend="docker", image=image, timeout=10, file_size_mb=1)
    result = execute(["python", "hostile.py"], target, policy)
    assert result.result.returncode == 0, result.result
    assert result.evidence.isolated and result.evidence.target_read_only
    assert result.evidence.network_enforced and result.evidence.teardown
    assert files(target) == before and secret.read_text() == "synthetic-host-canary"
    hanging = execute(
        ["python", "-c", "import time; time.sleep(60)"],
        target,
        policy.model_copy(update={"timeout": 1}),
    )
    assert hanging.result.timed_out and hanging.evidence.teardown
    noisy = execute(
        ["python", "-c", 'print("x"*20000)'],
        target,
        policy.model_copy(update={"output_bytes": 1024}),
    )
    assert (
        noisy.result.output_truncated
        and len(noisy.result.stdout) < 1200
        and noisy.evidence.teardown
    )
    memory = execute(
        ["python", "-c", "a=[bytearray(4*1024*1024) for _ in range(40)]"],
        target,
        policy.model_copy(update={"memory_mb": 64}),
    )
    assert memory.result.returncode != 0 and memory.evidence.oom_killed and memory.evidence.teardown
    processes = execute(
        [
            "python",
            "-c",
            (
                "import subprocess\np=[]\ntry:\n for _ in range(32): p.append(su"
                'bprocess.Popen(["sleep","10"]))\nexcept OSError: print("bound'
                'ed")\nfinally:\n for c in p: c.terminate()'
            ),
        ],
        target,
        policy.model_copy(update={"pids": 16}),
    )
    assert (
        processes.result.returncode == 0
        and "bounded" in processes.result.stdout
        and processes.evidence.teardown
    )
