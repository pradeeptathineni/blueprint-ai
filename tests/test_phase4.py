from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import blueprint_ai.engine as engine_module
from blueprint_ai.adapters.base import (
    CommandResult,
    ExternalToolAdapter,
    parse_actionlint,
    parse_checkov,
    parse_json_list,
    parse_lychee,
    parse_osv,
    parse_sarif,
    parse_trivy,
)
from blueprint_ai.adapters.registry import applicable_adapters, known_tools
from blueprint_ai.cli import app
from blueprint_ai.core import ToolStatus
from blueprint_ai.core.priority import deduplicate
from blueprint_ai.discovery import discover_project
from blueprint_ai.engine import make_context, review
from blueprint_ai.output import report_markdown
from blueprint_ai.remediation import apply_kit


def _adapter(name: str, parser) -> ExternalToolAdapter:
    return ExternalToolAdapter(name, "ci-cd", [], parser)


def test_priority_adapter_commands_match_current_upstream_contracts() -> None:
    tools = {tool.name: tool for tool in known_tools()}
    assert tools["gitleaks"].args[0] == "dir"
    assert "--redact" in tools["gitleaks"].args
    assert tools["osv-scanner"].args[:2] == ["scan", "source"]
    assert tools["trivy"].args[4] == "vuln,misconfig"
    assert "--offline" in tools["zizmor"].args
    assert tools["lychee"].expected_codes == {0, 2}
    assert tools["lychee"].default_timeout == 30
    assert "--exclude-all-private" in tools["lychee"].args


def test_trivy_scanner_scope_follows_blueprint_applicability(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('resource "aws_vpc" "main" {}\n')
    facts = discover_project(tmp_path)
    iac_trivy = next(tool for tool in applicable_adapters(facts, "iac") if tool.name == "trivy")
    assert iac_trivy.blueprint == "iac"
    assert iac_trivy.args[4] == "misconfig"

    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}\n')
    facts = discover_project(tmp_path)
    supply_trivy = next(
        tool for tool in applicable_adapters(facts, "supply-chain") if tool.name == "trivy"
    )
    assert supply_trivy.blueprint == "supply-chain"
    assert supply_trivy.args[4] == "vuln"

    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    facts = discover_project(tmp_path)
    container_trivy = next(
        tool for tool in applicable_adapters(facts, "containers") if tool.name == "trivy"
    )
    assert container_trivy.blueprint == "containers"
    assert container_trivy.args[4] == "misconfig"


def test_terraform_tools_cover_real_roots_and_tflint_recurses(tmp_path: Path) -> None:
    for directory in (tmp_path / "terraform", tmp_path / "bootstrap"):
        directory.mkdir()
        (directory / "main.tf").write_text('terraform { required_version = ">= 1.0" }\n')
        (directory / ".terraform.lock.hcl").write_text("# lock\n")
    facts = discover_project(tmp_path)
    adapters = applicable_adapters(facts, "iac", allow_project_executables=True)
    terraform = [
        tool for tool in adapters if tool.executable == "terraform" and "validate" in tool.args
    ]
    assert [tool.args[0] for tool in terraform] == ["-chdir=terraform", "-chdir=bootstrap"]
    tflint = next(tool for tool in adapters if tool.name == "tflint")
    assert tflint.args[:3] == ["--recursive", "--format", "json"]
    assert tflint.expected_codes == {0, 2}


def test_terraform_tests_are_routed_to_the_configuration_root(tmp_path: Path) -> None:
    terraform = tmp_path / "terraform"
    tests = terraform / "tests"
    tests.mkdir(parents=True)
    (terraform / "main.tf").write_text('terraform { required_version = ">= 1.0" }\n')
    (tests / "architecture.tftest.hcl").write_text('run "plan" { command = plan }\n')
    facts = discover_project(tmp_path)
    adapter = next(
        tool
        for tool in applicable_adapters(facts, "testing", allow_project_executables=True)
        if tool.name == "terraform-test"
    )
    assert adapter.args == ["-chdir=terraform", "test", "-no-color"]


def test_lockfile_only_project_is_supply_chain_applicable(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {}}')
    facts = discover_project(tmp_path)
    assert facts.manifests == ["package-lock.json"]
    assert [tool.name for tool in applicable_adapters(facts, "supply-chain")] == [
        "osv-scanner",
        "trivy",
    ]


def test_git_repository_uses_history_mode_for_gitleaks(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    facts = discover_project(tmp_path)
    facts.is_git = True
    adapter = applicable_adapters(facts, "security")[0]
    assert adapter.name == "gitleaks"
    assert adapter.args[0] == "git"


def test_actionlint_receives_explicit_workflow_paths(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\non: push\njobs: {}\n")
    facts = discover_project(tmp_path)
    adapter = next(
        tool for tool in applicable_adapters(facts, "ci-cd") if tool.name == "actionlint"
    )
    assert adapter.args[-1] == ".github/workflows/ci.yml"


def test_documentation_tools_receive_only_discovered_markdown(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n")
    ignored = tmp_path / ".venv" / "dependency.md"
    ignored.parent.mkdir()
    ignored.write_text("not project documentation\n")
    facts = discover_project(tmp_path)
    adapters = {tool.name: tool for tool in applicable_adapters(facts, "documentation")}
    assert adapters["markdownlint-cli2"].args == ["README.md"]
    assert adapters["lychee"].args[-1] == "README.md"


def test_gitleaks_discards_generic_duplicate_at_same_location(tmp_path: Path) -> None:
    payload = [
        {
            "RuleID": "generic-api-key",
            "Description": "generic",
            "File": "secret.txt",
            "StartLine": 1,
        },
        {
            "RuleID": "github-pat",
            "Description": "specific",
            "File": "secret.txt",
            "StartLine": 1,
        },
    ]
    findings = ExternalToolAdapter("gitleaks", "security", [], parse_json_list).parse(
        CommandResult([], 1, json.dumps(payload), ""), tmp_path
    )
    assert [item.rule_id for item in findings] == ["github-pat"]


def test_actionlint_and_zizmor_duplicate_preserves_both_sources(tmp_path: Path) -> None:
    actionlint_payload = [
        {
            "message": '"github.event.issue.title" is potentially untrusted',
            "filepath": ".github/workflows/unsafe.yml",
            "line": 10,
            "column": 24,
            "kind": "expression",
            "snippet": "- run: echo ${{ github.event.issue.title }}",
        }
    ]
    actionlint = _adapter("actionlint", parse_actionlint).parse(
        CommandResult([], 1, json.dumps(actionlint_payload), ""), tmp_path
    )
    sarif_payload = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "zizmor/template-injection",
                        "level": "error",
                        "message": {"text": "code injection via template expansion"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": ".github/workflows/unsafe.yml"},
                                    "region": {"startLine": 10},
                                }
                            }
                        ],
                    }
                ]
            }
        ]
    }
    zizmor = _adapter("zizmor", parse_sarif).parse(
        CommandResult([], 0, json.dumps(sarif_payload), ""), tmp_path
    )
    combined = deduplicate(actionlint + zizmor, ["production"])
    assert len(combined) == 1
    assert combined[0].sources == ["actionlint", "zizmor"]
    assert combined[0].priority == "P1"
    assert combined[0].file == ".github/workflows/unsafe.yml"


def test_zizmor_basename_location_is_normalized_to_workflow_path(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\n")
    payload = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "zizmor/unpinned-uses",
                        "level": "error",
                        "message": {"text": "unpinned action reference"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "ci.yml"},
                                    "region": {"startLine": 2},
                                }
                            }
                        ],
                    }
                ]
            }
        ]
    }
    finding = _adapter("zizmor", parse_sarif).parse(
        CommandResult([], 0, json.dumps(payload), ""), tmp_path
    )[0]
    assert finding.file == ".github/workflows/ci.yml"


def test_equivalent_iac_scanner_rules_are_aggregated(tmp_path: Path) -> None:
    checkov = ExternalToolAdapter("checkov", "iac", [], parse_checkov)
    trivy = ExternalToolAdapter("trivy", "iac", [], parse_trivy)
    checkov_finding = checkov.parse(
        CommandResult(
            [],
            1,
            json.dumps(
                {
                    "results": {
                        "failed_checks": [
                            {
                                "check_id": "CKV_AWS_131",
                                "check_name": "ALB should drop invalid headers",
                                "file_path": "/main.tf",
                                "file_line_range": [4, 10],
                            }
                        ]
                    }
                }
            ),
            "",
        ),
        tmp_path,
    )[0]
    trivy_finding = trivy.parse(
        CommandResult(
            [],
            0,
            json.dumps(
                {
                    "Results": [
                        {
                            "Target": "main.tf",
                            "Misconfigurations": [
                                {
                                    "ID": "AWS-0052",
                                    "Title": "Load balancers should drop invalid headers",
                                    "Severity": "HIGH",
                                    "CauseMetadata": {"StartLine": 9},
                                }
                            ],
                        }
                    ]
                }
            ),
            "",
        ),
        tmp_path,
    )[0]
    combined = deduplicate([checkov_finding, trivy_finding], ["iac"])
    assert len(combined) == 1
    assert combined[0].rule_id == "iac/aws/alb-drop-invalid-headers"
    assert combined[0].sources == ["checkov", "trivy"]


def test_lychee_json_error_map_preserves_location_and_url(tmp_path: Path) -> None:
    payload = {
        "errors": 1,
        "error_map": {
            "README.md": [
                {
                    "url": "file:///project/missing.md",
                    "status": {"text": "File not found", "details": "path is missing"},
                    "span": {"line": 7, "column": 1},
                }
            ]
        },
    }
    findings = _adapter("lychee", parse_lychee).parse(
        CommandResult([], 2, json.dumps(payload), ""), tmp_path
    )
    assert len(findings) == 1
    assert findings[0].file == "README.md"
    assert findings[0].range and findings[0].range.start_line == 7
    assert "missing.md" in findings[0].message


def test_lychee_collapses_repeated_blocked_urls_as_inconclusive(tmp_path: Path) -> None:
    payload = {
        "errors": 2,
        "error_map": {
            "README.md": [
                {
                    "url": "https://example.com/blocked",
                    "status": {"text": "Rejected status code: 403 Forbidden", "code": 403},
                    "span": {"line": 7, "column": 1},
                },
                {
                    "url": "https://example.com/blocked",
                    "status": {"text": "Error (cached)"},
                    "span": {"line": 12, "column": 1},
                },
            ]
        },
    }
    findings = _adapter("lychee", parse_lychee).parse(
        CommandResult([], 2, json.dumps(payload), ""), tmp_path
    )
    assert len(findings) == 1
    assert findings[0].category == "link-check-inconclusive"
    assert findings[0].confidence == 0.5
    assert findings[0].tool_metadata["occurrences"] == 2
    assert findings[0].range and findings[0].range.start_line == 7
    assert "also at line 12" in findings[0].evidence


def test_osv_parser_uses_advisory_severity_version_and_fix(tmp_path: Path) -> None:
    payload = {
        "results": [
            {
                "source": {"path": str(tmp_path / "package-lock.json")},
                "packages": [
                    {
                        "package": {"name": "lodash", "version": "4.17.20", "ecosystem": "npm"},
                        "groups": [
                            {
                                "ids": ["GHSA-demo"],
                                "aliases": ["CVE-2021-23337", "GHSA-demo"],
                            }
                        ],
                        "vulnerabilities": [
                            {
                                "id": "GHSA-demo",
                                "aliases": ["CVE-2021-23337"],
                                "summary": "command injection",
                                "database_specific": {"severity": "HIGH"},
                                "affected": [
                                    {
                                        "ranges": [
                                            {"events": [{"introduced": "0"}, {"fixed": "4.17.21"}]}
                                        ]
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    finding = _adapter("osv-scanner", parse_osv).parse(
        CommandResult([], 1, json.dumps(payload), ""), tmp_path
    )[0]
    assert finding.severity == "high"
    assert finding.rule_id == "CVE-2021-23337"
    assert finding.file == "package-lock.json"
    assert finding.tool_metadata["installed_version"] == "4.17.20"
    assert "4.17.21" in finding.recommendation


def test_trivy_parser_reads_nested_cause_location_and_fix(tmp_path: Path) -> None:
    payload = {
        "Results": [
            {
                "Target": "main.tf",
                "Misconfigurations": [
                    {
                        "ID": "AWS-0107",
                        "Title": "Unrestricted SSH",
                        "Severity": "HIGH",
                        "Resolution": "Restrict the CIDR",
                        "CauseMetadata": {"StartLine": 6},
                    }
                ],
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2026-1",
                        "Title": "Vulnerable package",
                        "Severity": "CRITICAL",
                        "InstalledVersion": "1.0",
                        "FixedVersion": "1.1",
                    }
                ],
            }
        ]
    }
    findings = _adapter("trivy", parse_trivy).parse(
        CommandResult([], 0, json.dumps(payload), ""), tmp_path
    )
    misconfiguration = next(item for item in findings if item.category == "misconfiguration")
    vulnerability = next(item for item in findings if item.category == "dependency-vulnerability")
    assert misconfiguration.range and misconfiguration.range.start_line == 6
    assert vulnerability.tool_metadata["fixed_version"] == "1.1"
    assert "fixed version: 1.1" in vulnerability.evidence


def test_disabled_or_missing_tool_makes_result_partial(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("# Demo\n\nInstall it, then see the usage example.\n")
    monkeypatch.setattr(
        engine_module,
        "_run_tools",
        lambda *_args, **_kwargs: [
            (
                ToolStatus(
                    name="project-tool",
                    available=False,
                    outcome="unsupported",
                    detail="disabled for an untrusted repository",
                ),
                [],
                None,
            )
        ],
    )
    context, _ = make_context(tmp_path, blueprints=["documentation"], model_mode="off")
    result = review(context).results[0]
    assert result.status == "partial"
    markdown = report_markdown(review(context))
    assert "No findings from completed checks; analysis is incomplete." in markdown
    assert "Incomplete tools" in markdown


def test_model_only_blueprint_is_partial_when_model_is_disabled(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 1\n")
    context, _ = make_context(tmp_path, blueprints=["architecture"], model_mode="off")
    result = review(context).results[0]
    assert result.status == "partial"
    assert result.notes == [
        "model review disabled; no deterministic implementation covers this blueprint"
    ]


def test_generated_smoke_does_not_claim_unit_test_coverage(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "type": "module", "dependencies": {"react": "latest"}})
    )
    (tmp_path / "app.js").write_text("export const value = 1;\n")
    apply_kit(tmp_path, discover_project(tmp_path), "testing-javascript")
    facts = discover_project(tmp_path)
    assert facts.test_capabilities == ["smoke"]

    findings = review(make_context(tmp_path, blueprints=["testing"], model_mode="off")[0]).findings
    unit = next(finding for finding in findings if finding.category == "missing-unit-tests")
    assert unit.remediation is None


def test_doctor_explains_missing_model_credential(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["model"]["detail"] == (
        "OPENAI_API_KEY is not set; deterministic review remains available"
    )
