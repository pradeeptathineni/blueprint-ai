from pathlib import Path

from blueprint_ai.blueprints import BLUEPRINTS
from blueprint_ai.discovery import discover_project


def test_phase_two_taxonomy_is_complete() -> None:
    assert set(BLUEPRINTS) == {
        "api-data-config",
        "containers",
        "code-design",
        "completeness",
        "identity",
        "repository",
        "code-quality",
        "security",
        "supply-chain",
        "testing",
        "iac",
        "kubernetes",
        "ci-cd",
        "reliability",
        "operations",
        "documentation",
        "architecture",
        "ai-context",
    }


def test_repository_and_testing_offer_safe_remediation(python_project: Path) -> None:
    facts = discover_project(python_project)
    repository = BLUEPRINTS["repository"].check(python_project, facts)
    testing = BLUEPRINTS["testing"].check(python_project, facts)
    assert {finding.remediation.target for finding in repository if finding.remediation} == {
        ".editorconfig",
        ".pre-commit-config.yaml",
    }
    unit = next(finding for finding in testing if finding.category == "missing-unit-tests")
    smoke = next(finding for finding in testing if finding.category == "missing-smoke-tests")
    assert unit.remediation is None
    assert smoke.remediation is not None
    assert smoke.remediation.target == "tests/test_smoke.py"


def test_ci_pin_check(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("steps:\n  - uses: actions/checkout@v4\n")
    facts = discover_project(tmp_path)
    findings = BLUEPRINTS["ci-cd"].check(tmp_path, facts)
    assert findings[0].category == "unpinned-action"
    assert findings[0].priority == "P1"


def test_security_does_not_flag_nonsecret_environment_defaults(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("EXAMPLE=value\n")
    facts = discover_project(tmp_path)
    findings = BLUEPRINTS["security"].check(tmp_path, facts)
    assert not findings


def test_ai_context_detects_duplicate_and_oversized_instructions(tmp_path: Path) -> None:
    content = "instruction\n" * 3_000
    (tmp_path / "AGENTS.md").write_text(content)
    (tmp_path / "CLAUDE.md").write_text(content)
    facts = discover_project(tmp_path)
    findings = BLUEPRINTS["ai-context"].check(tmp_path, facts)
    assert {finding.category for finding in findings} == {
        "duplicate-context",
        "oversized-context",
    }


def test_terraform_config_is_not_treated_as_application_environment_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "stack.tfvars").write_text('region = "us-east-1"\n')
    (tmp_path / "main.tf").write_text('terraform { required_version = ">= 1.0" }\n')
    facts = discover_project(tmp_path)
    assert BLUEPRINTS["api-data-config"].assess(facts).state == "not_applicable"
    assert BLUEPRINTS["api-data-config"].check(tmp_path, facts) == []


def test_iac_without_native_tests_has_contextual_medium_finding(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('terraform { required_version = ">= 1.0" }\n')
    facts = discover_project(tmp_path)
    finding = BLUEPRINTS["testing"].check(tmp_path, facts)[0]
    assert finding.severity == "medium"
    assert "Terraform module behavior" in finding.message
    assert "native Terraform tests" in finding.recommendation


def test_iac_operations_use_existing_operational_guidance(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('terraform { required_version = ">= 1.0" }\n')
    (tmp_path / "README.md").write_text(
        "# Infrastructure\n\nVerify health, destroy for rollback, protect state for recovery, "
        "and monitor cost.\n"
    )
    facts = discover_project(tmp_path)
    assert BLUEPRINTS["operations"].assess(facts).state == "applicable"
    assert BLUEPRINTS["operations"].check(tmp_path, facts) == []
    assert BLUEPRINTS["reliability"].assess(facts).state == "partial"


def test_shell_only_code_design_is_partial_not_passable_by_empty_checks(tmp_path: Path) -> None:
    (tmp_path / "script.sh").write_text("#!/bin/sh\nexit 0\n")
    facts = discover_project(tmp_path)
    assert BLUEPRINTS["code-design"].assess(facts).state == "partial"


def test_explicit_provider_placeholder_does_not_hide_a_realistic_secret(tmp_path: Path) -> None:
    credential = "realistic-" + "credential-value-12345"
    (tmp_path / "config.py").write_text(
        f'token = "ghp_exampleabcdefghijklmnopqrstuv"\napi_key = "{credential}"\n'
    )
    findings = BLUEPRINTS["security"].check(tmp_path, discover_project(tmp_path))
    assert len(findings) == 1
    assert "api_key" in findings[0].message
