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
    assert testing[0].remediation is not None
    assert testing[0].remediation.target == "tests/test_smoke.py"


def test_ci_pin_check(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("steps:\n  - uses: actions/checkout@v4\n")
    facts = discover_project(tmp_path)
    findings = BLUEPRINTS["ci-cd"].check(tmp_path, facts)
    assert findings[0].category == "unpinned-action"
    assert findings[0].priority == "P1"


def test_security_flags_visible_environment_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("EXAMPLE=value\n")
    facts = discover_project(tmp_path)
    findings = BLUEPRINTS["security"].check(tmp_path, facts)
    assert findings[0].category == "sensitive-file"
    assert findings[0].severity == "high"


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
