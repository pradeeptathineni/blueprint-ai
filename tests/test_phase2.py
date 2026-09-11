from __future__ import annotations

import json
import stat
import subprocess
import time
from pathlib import Path

import pytest

import blueprint_ai.engine as engine_module
from blueprint_ai.adapters.base import (
    CommandResult,
    ExternalToolAdapter,
    parse_grype,
    parse_junit,
    parse_kubeconform,
    parse_sarif,
    parse_semgrep,
)
from blueprint_ai.adapters.languages import language_adapter_registry
from blueprint_ai.adapters.registry import applicable_adapters
from blueprint_ai.blueprints import BLUEPRINTS, PROFILES
from blueprint_ai.config import Settings
from blueprint_ai.core import Finding, ToolStatus
from blueprint_ai.discovery import discover_project
from blueprint_ai.engine import make_context, review, write_baseline
from blueprint_ai.output import report_markdown, report_sarif
from blueprint_ai.remediation import apply_findings, apply_kit, rollback_operation


def test_all_required_profiles_compose(python_project: Path) -> None:
    required = {
        "library",
        "cli",
        "api",
        "backend-service",
        "frontend",
        "web-app",
        "full-stack",
        "iac",
        "terraform",
        "container",
        "kubernetes",
        "data-pipeline",
        "ai-app",
        "ai-agent",
        "open-source",
        "portfolio",
        "production",
    }
    assert required <= set(PROFILES)
    context, _ = make_context(python_project, profile="cli,production", model_mode="off")
    assert context.profiles == ["cli", "production"]
    assert context.selected_blueprints == list(
        dict.fromkeys(PROFILES["cli"] + PROFILES["production"])
    )


def test_applicability_reports_state_and_reason(python_project: Path) -> None:
    context, _ = make_context(
        python_project,
        profile="api",
        blueprints=["api-data-config", "iac"],
        model_mode="off",
    )
    results = review(context).results
    assert results[0].applicability.state == "partial"
    assert "machine-readable API contract" in results[0].applicability.reason
    assert results[1].status == "not_applicable"
    assert results[1].applicability.reason


def test_language_registry_has_first_class_native_routes() -> None:
    registry = language_adapter_registry()
    assert set(registry) == {"Python", "JavaScript", "TypeScript", "Go", "Rust", "Java", "Shell"}
    assert "go-vet" in registry["Go"].quality
    assert "cargo-clippy" in registry["Rust"].quality
    assert "tsc" in registry["TypeScript"].quality


def test_mixed_platform_discovery_and_deterministic_findings(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('resource "x" "y" {}\n')
    (tmp_path / "Dockerfile").write_text("FROM demo:latest\nRUN echo x\n")
    (tmp_path / "deploy.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n"
        "      containers:\n        - image: demo:latest\n          securityContext:\n"
        "            privileged: true\n"
    )
    (tmp_path / "openapi.yaml").write_text("openapi: 3.1.0\ninfo:\n  title: Demo\npaths: {}\n")
    facts = discover_project(tmp_path)
    assert facts.iac == ["terraform"]
    assert facts.containers == ["Dockerfile"]
    assert facts.api_specs == ["openapi.yaml"]
    assert facts.kubernetes == ["deploy.yaml"]
    assert {item.category for item in BLUEPRINTS["containers"].check(tmp_path, facts)} == {
        "floating-base-image",
        "root-runtime",
    }
    assert {item.category for item in BLUEPRINTS["kubernetes"].check(tmp_path, facts)} == {
        "floating-image",
        "missing-resource-policy",
        "privileged-workload",
    }
    assert (
        BLUEPRINTS["api-data-config"].check(tmp_path, facts)[0].category == "unversioned-contract"
    )


def test_baseline_and_reasoned_suppression_lifecycle(python_project: Path) -> None:
    context, settings = make_context(python_project, blueprints=["repository"], model_mode="off")
    first = review(context)
    baseline_path = python_project / settings.baseline_path
    write_baseline(first, baseline_path)
    second = review(context)
    assert second.active_findings == []
    assert len(second.baseline_findings) == len(first.findings)

    baseline_path.unlink()
    (python_project / ".blueprint-ai.yml").write_text(
        "suppressions:\n"
        "  - blueprint: repository\n"
        "    category: missing-baseline\n"
        "    reason: owned by platform migration\n"
    )
    suppressed_context, _ = make_context(
        python_project, blueprints=["repository"], model_mode="off"
    )
    suppressed = review(suppressed_context)
    assert suppressed.suppressed_findings
    assert suppressed.suppressed_findings[0].suppression.reason == "owned by platform migration"


def test_markdown_and_sarif_preserve_lifecycle_contract(python_project: Path) -> None:
    context, _ = make_context(python_project, blueprints=["repository"], model_mode="off")
    report = review(context)
    markdown = report_markdown(report)
    sarif = report_sarif(report)
    assert "# Blueprint AI review" in markdown
    assert "| Priority | Disposition |" in markdown
    assert sarif["version"] == "2.1.0"
    result = sarif["runs"][0]["results"][0]
    assert result["partialFingerprints"]["blueprintAiFingerprint"]
    assert result["properties"]["priority"].startswith("P")


def test_capability_kit_apply_idempotency_and_safe_rollback(tmp_path: Path) -> None:
    facts = discover_project(tmp_path)
    first = apply_kit(tmp_path, facts, "observability")
    assert len(first.changed) == 1
    assert first.manifest_path
    second = apply_kit(tmp_path, facts, "observability")
    assert second.changed == []
    rolled_back = rollback_operation(tmp_path, first.operation_id)
    assert rolled_back.changes[0].status == "rolled_back"
    assert not (tmp_path / "docs" / "observability.md").exists()


def test_rollback_preserves_post_apply_user_edits(tmp_path: Path) -> None:
    result = apply_kit(tmp_path, discover_project(tmp_path), "container")
    target = tmp_path / ".dockerignore"
    target.write_text(target.read_text() + "custom\n")
    rolled_back = rollback_operation(tmp_path, result.operation_id)
    assert rolled_back.changes[0].status == "conflicted"
    assert target.is_file()


@pytest.mark.parametrize(
    ("parser", "payload", "category"),
    [
        (
            parse_semgrep,
            {
                "results": [
                    {
                        "check_id": "py.x",
                        "path": "a.py",
                        "start": {"line": 2},
                        "extra": {"message": "bad", "severity": "ERROR"},
                    }
                ]
            },
            "sast",
        ),
        (
            parse_grype,
            {
                "matches": [
                    {
                        "vulnerability": {
                            "id": "CVE-2026-1",
                            "severity": "High",
                            "fix": {"versions": ["2"]},
                        },
                        "artifact": {"name": "demo", "version": "1"},
                    }
                ]
            },
            "dependency-vulnerability",
        ),
        (
            parse_kubeconform,
            {
                "resources": [
                    {"filename": "deploy.yaml", "status": "statusInvalid", "msg": "bad schema"}
                ]
            },
            "schema-validation",
        ),
    ],
)
def test_phase_two_structured_parser_goldens(tmp_path, parser, payload, category) -> None:
    adapter = ExternalToolAdapter("tool", "security", [], parser)
    finding = parser(CommandResult([], 1, json.dumps(payload), ""), tmp_path, adapter)[0]
    assert finding.category == category
    assert finding.rule_id


def test_sarif_and_junit_parser_goldens(tmp_path: Path) -> None:
    sarif_adapter = ExternalToolAdapter("sarif-tool", "ci-cd", [], parse_sarif)
    payload = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "SEC1",
                        "level": "error",
                        "message": {"text": "unsafe"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "ci.yml"},
                                    "region": {"startLine": 4},
                                }
                            }
                        ],
                    }
                ]
            }
        ]
    }
    sarif_finding = parse_sarif(
        CommandResult([], 1, json.dumps(payload), ""), tmp_path, sarif_adapter
    )[0]
    assert sarif_finding.file == "ci.yml"
    assert sarif_finding.range.start_line == 4

    junit_adapter = ExternalToolAdapter("tests", "testing", [], parse_junit)
    junit = (
        '<testsuite><testcase classname="a" name="b">'
        '<failure message="boom"/></testcase></testsuite>'
    )
    junit_finding = parse_junit(CommandResult([], 1, junit, ""), tmp_path, junit_adapter)[0]
    assert junit_finding.category == "test-failure"
    assert "boom" in junit_finding.message


def test_tool_timeout_is_a_tool_error(tmp_path: Path) -> None:
    executable = tmp_path / "slow"
    executable.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "slow 1"; exit 0; fi\nsleep 1\n'
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    adapter = ExternalToolAdapter(
        "slow", "testing", [], parse_junit, executable=str(executable), expected_codes={0}
    )
    status, findings, error = adapter.run(tmp_path, timeout=0.05)
    assert status.outcome == "tool_error"
    assert status.exit_code == 124
    assert error.startswith("timed out")
    assert findings == []


def test_generated_test_failure_remains_a_product_finding(python_project: Path) -> None:
    fake_pytest = python_project / "fake-pytest"
    fake_pytest.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "pytest 99"; exit 0; fi\n'
        'echo "tests/test_smoke.py:1: product assertion failed"\nexit 1\n'
    )
    fake_pytest.chmod(fake_pytest.stat().st_mode | stat.S_IXUSR)
    (python_project / ".blueprint-ai.yml").write_text(f"tool_overrides:\n  pytest: {fake_pytest}\n")
    context, _ = make_context(python_project, blueprints=["testing"], model_mode="off")
    before = review(context)
    applied = apply_findings(python_project, before.facts, before.active_findings)
    assert applied.changed[0].target == "tests/test_smoke.py"
    assert "Generated by Blueprint AI" in (python_project / "tests/test_smoke.py").read_text()
    after = review(context).results[0]
    assert after.status == "findings"
    assert after.findings[0].message == "product assertion failed"


def test_independent_tools_run_with_bounded_concurrency(
    python_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowAdapter:
        def __init__(self, name: str):
            self.name = name

        def run(self, root, timeout):
            time.sleep(0.15)
            return ToolStatus(name=self.name, available=True, outcome="passed"), [], None

    monkeypatch.setattr(
        engine_module,
        "applicable_adapters",
        lambda *args, **kwargs: [SlowAdapter("one"), SlowAdapter("two")],
    )
    context, _ = make_context(python_project, blueprints=["code-quality"], model_mode="off")
    started = time.monotonic()
    report = review(context)
    elapsed = time.monotonic() - started
    assert elapsed < 0.27
    assert [tool.outcome for tool in report.results[0].tools] == ["passed", "passed"]


def test_changed_file_discovery(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    tracked.write_text("value = 2\n")
    (tmp_path / "new.py").write_text("new = True\n")
    facts = discover_project(tmp_path, changed_only=True)
    assert facts.changed_files == ["new.py", "tracked.py"]


def test_malformed_config_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="extra"):
        Settings.model_validate({"tool_overide": {"ruff": "unsafe; command"}})


def test_dast_requires_explicit_valid_scope_and_safe_command(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTP"):
        Settings(authorized_target="ftp://example.test")
    with pytest.raises(ValueError, match="credentials"):
        Settings(authorized_target="https://user:secret@example.test")
    (tmp_path / "app.py").write_text("value = 1\n")
    facts = discover_project(tmp_path)
    without_scope = applicable_adapters(facts, "security")
    assert all(adapter.name != "zap-baseline" for adapter in without_scope)
    scoped = applicable_adapters(facts, "security", authorized_target="http://127.0.0.1:8080")
    zap = next(adapter for adapter in scoped if adapter.name == "zap-baseline")
    assert zap.command(tmp_path) == [
        "zap-baseline.py",
        "-t",
        "http://127.0.0.1:8080",
        "-m",
        "2",
        "-I",
    ]


def test_fingerprint_property_ignores_message_wording() -> None:
    messages = ["Secret found", "A differently worded warning", "message changed in tool v2"]
    fingerprints = {
        Finding(
            blueprint="security",
            category="secret",
            rule_id="secret/generic-api-key",
            source="scanner",
            file="app.py",
            message=message,
            recommendation="remove",
        ).fingerprint
        for message in messages
    }
    assert len(fingerprints) == 1


def test_ai_eval_dataset_is_versioned_and_well_formed() -> None:
    path = Path(__file__).parent / "evals" / "review_cases.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert {case["blueprint"] for case in payload["cases"]} >= {
        "architecture",
        "security",
        "testing",
    }
    assert all(case["expect"] for case in payload["cases"])


def test_comment_review_does_not_match_string_literals(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text('MARKER = "TODO should not be a finding"\n')
    facts = discover_project(tmp_path)
    assert BLUEPRINTS["code-design"].check(tmp_path, facts) == []


def test_reliability_detects_unbounded_network_call(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "service"\nversion = "1"\ndependencies = ["fastapi"]\n'
    )
    (tmp_path / "service.py").write_text(
        "import requests\n\ndef fetch():\n    return requests.get('https://example.test')\n"
    )
    facts = discover_project(tmp_path)
    findings = BLUEPRINTS["reliability"].check(tmp_path, facts)
    assert {finding.category for finding in findings} == {
        "missing-timeout",
        "missing-observability",
    }
