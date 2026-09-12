"""Adversarial contracts: a scanner's inability to analyze must not become a clean report."""

import json
from pathlib import Path

import pytest

from blueprint_ai.adapters import base
from blueprint_ai.adapters.base import CommandResult, ExternalToolAdapter
from blueprint_ai.adapters.registry import applicable_adapters, known_tools
from blueprint_ai.core import ToolStatus
from blueprint_ai.discovery import discover_project
from blueprint_ai.sandbox import SandboxPolicy, SandboxUnavailable
from blueprint_ai.support import TOOLS
from blueprint_ai.tooling import cached_image, install_tool


@pytest.mark.parametrize(
    "name",
    [
        "ruff",
        "semgrep",
        "osv-scanner",
        "trivy",
        "grype",
        "terraform",
        "tflint",
        "checkov",
        "kubeconform",
        "zizmor",
        "lychee",
        "biome",
        "kube-linter",
    ],
)
def test_malformed_empty_object_never_passes(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = next(a for a in known_tools() if a.name == name)
    adapter.sandbox = SandboxPolicy(backend="host", trusted=True)
    monkeypatch.setattr(adapter, "status", lambda: ToolStatus(name=name, available=True))
    monkeypatch.setattr(base, "run_bounded_command", lambda *a: CommandResult([], 0, "{}", ""))
    status, findings, error = adapter.run(tmp_path)
    assert status.outcome == "tool_error"
    assert status.analysis_state == "malformed"
    assert findings == [] and error


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        (
            "semgrep",
            {"results": [], "errors": [{"type": "ParseError", "message": "invalid Python"}]},
        ),
        ("tflint", {"issues": [], "errors": [{"message": "plugin unavailable"}]}),
        ("checkov", {"results": {"failed_checks": [], "parsing_errors": ["main.tf"]}}),
        ("kubeconform", {"resources": [{"filename": "app.yaml", "status": "statusError"}]}),
        ("kubeconform", {"resources": [], "summary": {"skipped": 1}}),
        ("zizmor", {"runs": [{"results": [], "invocations": [{"executionSuccessful": False}]}]}),
    ],
)
def test_native_partial_analysis_never_becomes_clean(
    name: str, payload: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = next(a for a in known_tools() if a.name == name)
    adapter.sandbox = SandboxPolicy(backend="host", trusted=True)
    monkeypatch.setattr(adapter, "status", lambda: ToolStatus(name=name, available=True))
    monkeypatch.setattr(
        base, "run_bounded_command", lambda *a: CommandResult([], 0, json.dumps(payload), "")
    )
    status, findings, error = adapter.run(tmp_path)
    assert status.outcome == "tool_error" and status.analysis_state == "incomplete"
    assert findings == [] and error


def test_nonzero_empty_diagnostic_list_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = next(a for a in known_tools() if a.name == "gitleaks")
    adapter.sandbox = SandboxPolicy(backend="host", trusted=True)
    monkeypatch.setattr(adapter, "status", lambda: ToolStatus(name="gitleaks", available=True))
    monkeypatch.setattr(base, "run_bounded_command", lambda *a: CommandResult([], 1, "[]", ""))
    status, findings, error = adapter.run(tmp_path)
    assert status.outcome == "tool_error" and not findings and error


def test_native_kube_linter_envelope_clean_mutated_repaired(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "kube-linter")
    clean = {"Reports": None, "Summary": {"ChecksStatus": "Passed"}}
    bad = {
        "Reports": [
            {
                "Diagnostic": {"Message": 'container "app" is privileged'},
                "Check": "privileged-container",
                "Remediation": "Do not run your container as privileged unless it is required.",
                "Object": {"Metadata": {"FilePath": str(tmp_path / "deploy.yaml")}},
            }
        ],
        "Summary": {"ChecksStatus": "Failed"},
    }
    results = [
        adapter.parse(CommandResult([], code, json.dumps(payload), ""), tmp_path)
        for code, payload in [(0, clean), (1, bad), (0, clean)]
    ]
    assert [len(r) for r in results] == [0, 1, 0]
    assert results[1][0].rule_id == "kube-linter/privileged-container"
    assert results[1][0].file == "deploy.yaml"


def test_biome_uses_native_sarif_contract(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "biome")
    assert "--reporter=sarif" in adapter.args
    payload = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "lint/suspicious/noDebugger",
                        "level": "error",
                        "message": {"text": "This is an unexpected use of the debugger statement."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": str(tmp_path / "index.js")},
                                    "region": {"startLine": 1},
                                }
                            }
                        ],
                    }
                ]
            }
        ]
    }
    finding = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)[0]
    assert finding.rule_id == "lint/suspicious/noDebugger"
    assert finding.priority == "P1" and finding.file == "index.js"


def test_ast_grep_preserves_native_error_severity(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "ast-grep")
    payload = [
        {
            "ruleId": "no-eval",
            "message": "Avoid eval",
            "severity": "error",
            "file": "a.py",
            "range": {"start": {"line": 0}, "end": {"line": 0}},
        }
    ]
    finding = adapter.parse(CommandResult([], 0, json.dumps(payload), ""), tmp_path)[0]
    assert finding.priority == "P1"


@pytest.mark.parametrize("payload", [[], None, 42, "poisoned", {"image_id": []}])
def test_poisoned_receipt_is_a_cache_miss(tmp_path: Path, payload: object) -> None:
    (tmp_path / "ruff-docker.json").write_text(json.dumps(payload))
    assert cached_image("ruff", "docker", cache=tmp_path) is None


@pytest.mark.parametrize("payload", ["{}", "null", "[]", '["bad\\nFROM attacker"]'])
def test_image_acquisition_refuses_malformed_base_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    from blueprint_ai import tooling
    from blueprint_ai.safety import ProcessResult

    calls = []

    def run(command, *args, **kwargs):
        calls.append(command)
        return ProcessResult(0, payload if "inspect" in command else "", "", False, False)

    monkeypatch.setattr(tooling, "_local_runtime", lambda backend: ([backend], {}))
    monkeypatch.setattr(tooling, "run_process", run)
    with pytest.raises(SandboxUnavailable, match="cannot pin"):
        install_tool("ast-grep", cache=tmp_path)
    assert not any("build" in c for c in calls)
    assert not list(tmp_path.iterdir())


def test_dynamic_adapters_inherit_canonical_acquisition_and_versions() -> None:
    for name in ("gofmt", "maven-check", "gradle-check", "terraform:module"):
        adapter = ExternalToolAdapter(name, "code-quality", [], base.parse_lines)
        spec = TOOLS[name.split(":", 1)[0]]
        assert adapter.recommended_version == spec.versions
        assert spec.source in adapter.install
    assert TOOLS["gofmt"].container_executable == "gofmt"


def test_malicious_markdown_filename_cannot_become_a_cli_option(tmp_path: Path) -> None:
    (tmp_path / "--config=evil.md").write_text("# Document\n")
    facts = discover_project(tmp_path)
    adapters = applicable_adapters(facts, "documentation", sandboxed=True)
    assert adapters
    for adapter in adapters:
        assert "./--config=evil.md" in adapter.args
        assert "--config=evil.md" not in adapter.args


def test_kustomize_roots_get_separate_native_invocations(tmp_path: Path) -> None:
    for name in ("development", "production"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n"
        )
    adapters = applicable_adapters(discover_project(tmp_path), "kubernetes", sandboxed=True)
    selected = [a for a in adapters if a.name.startswith("kustomize")]
    assert len(selected) == 2
    assert {tuple(a.args) for a in selected} == {("build", "development"), ("build", "production")}


def test_cfn_lint_preserves_native_envelope_and_exit_bitmask(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "cfn-lint")
    payload = [
        {
            "Rule": {"Id": "E3002"},
            "Message": "Additional properties are not allowed",
            "Level": "Error",
            "Filename": "stack.yaml",
            "Location": {"Start": {"LineNumber": 4}},
        }
    ]
    item = adapter.parse(CommandResult([], 2, json.dumps(payload), ""), tmp_path)[0]
    assert item.rule_id == "E3002" and item.file == "stack.yaml"
    assert item.range and item.range.start_line == 4 and item.priority == "P1"
    assert adapter.expected_codes == {0, 2, 4, 6, 8, 10, 12, 14}


def test_spectral_path_is_a_document_location_not_a_filename(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "spectral")
    payload = [
        {
            "code": "info-contact",
            "message": "Missing contact",
            "severity": 0,
            "source": "openapi.yaml",
            "path": ["info", "contact"],
            "range": {"start": {"line": 3, "character": 2}},
        }
    ]
    item = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)[0]
    assert item.file == "openapi.yaml" and item.priority == "P1"
    assert item.range and item.range.start_line == 4
    assert json.loads(str(item.tool_metadata["document_path"])) == ["info", "contact"]


def test_terraform_formatting_exit_is_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = next(a for a in known_tools() if a.name == "terraform-fmt")
    adapter.sandbox = SandboxPolicy(backend="host", trusted=True)
    monkeypatch.setattr(adapter, "status", lambda: ToolStatus(name=adapter.name, available=True))
    monkeypatch.setattr(
        base, "run_bounded_command", lambda *a: CommandResult([], 3, "main.tf\n", "")
    )
    status, findings, error = adapter.run(tmp_path)
    assert status.outcome == "finding" and findings and error is None


def test_actionlint_native_workflow_diagnostic_is_not_suppressed(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "actionlint")
    payload = [
        {
            "kind": "workflow-call",
            "filepath": ".github/workflows/ci.yml",
            "line": 5,
            "message": 'invalid reusable workflow reference "$/.github/workflows/called.yml"',
        }
    ]
    phases = [[], payload, []]
    results = [
        adapter.parse(CommandResult([], 1 if p else 0, json.dumps(p), ""), tmp_path) for p in phases
    ]
    assert [len(r) for r in results] == [0, 1, 0]
    assert results[1][0].rule_id == "actionlint/workflow-call"


def test_checkov_comment_cannot_suppress_public_ingress(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "checkov")
    source = [
        [1, 'resource "aws_vpc_security_group_ingress_rule" "public" {'],
        [2, '# referenced_security_group_id = "sg-safe"'],
        [3, 'cidr_ipv4 = "0.0.0.0/0"'],
        [4, "from_port = 80"],
        [5, "to_port = 80"],
        [6, "}"],
    ]
    payload = {
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_260",
                    "check_name": "Public HTTP",
                    "file_path": "/main.tf",
                    "code_block": source,
                }
            ]
        }
    }
    result = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)
    assert len(result) == 1 and result[0].rule_id == "CKV_AWS_260"


def test_version_detection_skips_unversioned_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = next(a for a in known_tools() if a.name == "shellcheck")
    monkeypatch.setattr(base, "_resolve_executable", lambda name: "/trusted/shellcheck")
    monkeypatch.setattr(
        base,
        "run_bounded_command",
        lambda *a: CommandResult(
            [], 0, "ShellCheck - shell script analysis tool\nversion: 0.11.0\n", ""
        ),
    )
    assert adapter.status().version == "version: 0.11.0"


@pytest.mark.parametrize(
    ("name", "output"),
    [
        (
            "cargo-fmt",
            "info: syncing channel updates for 1.98.0-x86_64-unknown-linux-gnu\n"
            "error: could not create temp file /usr/local/rustup/tmp/probe: "
            "Read-only file system (os error 30)",
        ),
        ("cargo-clippy", "error: toolchain '1.98.0-x86_64-unknown-linux-gnu' is not installed"),
        ("cargo-test", "info: syncing channel updates for 1.98.0\nerror: could not download file"),
        ("go-test", "go: go.mod requires go >= 1.27 (running go 1.26; GOTOOLCHAIN=local)"),
    ],
)
def test_native_toolchain_failure_is_incomplete_not_a_project_finding(
    name: str, output: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = next(a for a in known_tools() if a.name == name)
    adapter.sandbox = SandboxPolicy(backend="host", trusted=True)
    monkeypatch.setattr(adapter, "status", lambda: ToolStatus(name=name, available=True))
    monkeypatch.setattr(base, "run_bounded_command", lambda *a: CommandResult([], 1, "", output))
    status, findings, error = adapter.run(tmp_path)
    assert status.outcome == "tool_error" and status.analysis_state == "incomplete"
    assert not findings and error and "toolchain prerequisite" in error


def test_native_compile_diagnostic_remains_a_finding(tmp_path: Path) -> None:
    adapter = next(a for a in known_tools() if a.name == "cargo-clippy")
    findings = adapter.parse(
        CommandResult([], 1, "src/lib.rs:2:1: error: undefined value", ""), tmp_path
    )
    assert len(findings) == 1 and findings[0].file == "src/lib.rs"


@pytest.mark.parametrize("name", ["cargo-clippy", "cargo-test"])
def test_cargo_offline_missing_dependency_is_incomplete(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = next(a for a in known_tools() if a.name == name)
    adapter.sandbox = SandboxPolicy(backend="host", trusted=True)
    monkeypatch.setattr(adapter, "status", lambda: ToolStatus(name=name, available=True))
    output = (
        "error: no matching package named `actix-web` found\n"
        "location searched: crates.io index\n"
        "required by package `shipping v0.0.0 (/workspace/src/shipping)`\n"
        "note: offline mode (via `--offline`) can sometimes cause surprising resolution failures\n"
        "help: if this error is too confusing you may wish to retry without `--offline`\n"
    )
    monkeypatch.setattr(base, "run_bounded_command", lambda *a: CommandResult([], 101, "", output))
    status, findings, error = adapter.run(tmp_path)
    assert status.outcome == "tool_error" and status.analysis_state == "incomplete"
    assert not findings and error and "dependency prerequisite unavailable offline" in error


def test_rust_test_ownership_does_not_schedule_python_resource_data(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname="native"\nversion="0.1.0"\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "native.rs").write_text("#[test]\nfn works() { assert!(true); }\n")
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "syntax_test.py").write_text("if:\n")
    (tmp_path / "resources" / "example.ts").write_text("export const value = 1;\n")
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    assert "cargo-test" in {a.name for a in selected}
    assert not any(a.name.startswith(("pytest", "npm-test")) for a in selected)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = resources\n")
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    assert "pytest" in {a.name for a in selected}


def test_explicit_python_maintainer_project_keeps_its_tests(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname="native"\nversion="0.1.0"\n')
    project = tmp_path / "tools"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="helper"\nversion="0.1.0"\n')
    (project / "test_helper.py").write_text("def test_helper():\n    assert True\n")
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    assert "pytest:tools" in {a.name for a in selected}


def test_manifestless_python_tests_remain_discoverable(tmp_path: Path) -> None:
    (tmp_path / "test_helper.py").write_text("def test_helper():\n    assert True\n")
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    assert "pytest" in {a.name for a in selected}


def test_cargo_workspace_collects_member_tests_once_at_owner(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers=["member"]\nresolver="2"\n')
    member = tmp_path / "member"
    (member / "src").mkdir(parents=True)
    (member / "Cargo.toml").write_text('[package]\nname="member"\nversion="0.1.0"\n')
    (member / "src" / "lib.rs").write_text("#[test]\nfn works() { assert!(true); }\n")
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    cargo = [a for a in selected if a.name.startswith("cargo-test")]
    assert len(cargo) == 1 and cargo[0].working_directory == "."
    assert cargo[0].args == ["test", "--workspace", "--quiet"]


@pytest.mark.parametrize("source_file", ["lib.rs", "main.rs"])
def test_inline_rust_tests_select_native_runner(tmp_path: Path, source_file: str) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname="native"\nversion="0.1.0"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / source_file).write_text("#[test]\nfn works() { assert!(true); }\n")
    facts = discover_project(tmp_path)
    assert any(n.kind == "unit" for n in facts.graph.verification)
    selected = applicable_adapters(facts, "testing", sandboxed=True)
    assert "cargo-test" in {a.name for a in selected}


def test_java_resource_data_requires_a_native_build_manifest(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname="native"\nversion="0.1.0"\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "native.rs").write_text("#[test]\nfn works() { assert!(true); }\n")
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "SyntaxTest.java").write_text("class SyntaxTest {}\n")
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    assert not any(a.name.startswith(("maven", "gradle")) for a in selected)


def test_explicit_csharp_test_project_is_selected_once(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Demo.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
    (tmp_path / "src" / "Value.cs").write_text("public class Value {}\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "Demo.Tests.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><IsTestProject>true</IsTestProject>'
        "</PropertyGroup></Project>"
    )
    (tmp_path / "tests" / "ValueTests.cs").write_text(
        "using Xunit;\npublic class ValueTests { [Fact] public void Works() {} }\n"
    )
    selected = applicable_adapters(discover_project(tmp_path), "testing", sandboxed=True)
    dotnet = [a for a in selected if a.name.startswith("dotnet-test")]
    assert len(dotnet) == 1 and dotnet[0].working_directory == "tests"
