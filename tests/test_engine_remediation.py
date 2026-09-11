import stat
from pathlib import Path

from blueprint_ai.core import Finding
from blueprint_ai.core.models import Remediation
from blueprint_ai.discovery import discover_project
from blueprint_ai.engine import make_context, review
from blueprint_ai.remediation import apply_findings


def test_explicit_blueprints_execute_and_nonapplicable_is_reported(python_project: Path) -> None:
    context, _ = make_context(python_project, blueprints=["identity", "iac"], model_mode="off")
    report = review(context)
    assert [result.blueprint for result in report.results] == ["identity", "iac"]
    assert report.results[1].status == "not_applicable"


def test_apply_is_idempotent_and_preserves_existing_files(python_project: Path) -> None:
    (python_project / "README.md").write_text("custom\n")
    context, _ = make_context(
        python_project, blueprints=["repository", "testing", "documentation"], model_mode="off"
    )
    report = review(context)
    first = apply_findings(python_project, report.facts, report.findings)
    assert {change.target for change in first.changed} == {
        ".editorconfig",
        ".pre-commit-config.yaml",
        "tests/test_smoke.py",
    }
    assert (python_project / "README.md").read_text() == "custom\n"
    after = review(context)
    second = apply_findings(python_project, after.facts, after.findings)
    assert second.changed == []


def test_tool_override_runs_fake_executable(python_project: Path) -> None:
    fake = python_project / "fake-ruff"
    fake.write_text(
        """#!/bin/sh
if [ "$1" = "--version" ]; then echo "ruff 99"; exit 0; fi
echo '[{"code":"F001","message":"fake issue","filename":"demo.py","location":{"row":3}}]'
exit 1
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    (python_project / ".blueprint-ai.yml").write_text(
        f"tool_overrides:\n  ruff: {fake}\nminimum_severity: medium\n"
    )
    context, _ = make_context(
        python_project,
        blueprints=["code-quality"],
        model_mode="off",
        trust_project_executables=True,
    )
    result = review(context).results[0]
    assert result.status == "findings"
    assert result.findings[0].source == "ruff"
    assert result.findings[0].message == "F001: fake issue"


def test_apply_rejects_paths_outside_project(python_project: Path) -> None:
    finding = Finding(
        blueprint="repository",
        category="unsafe",
        source="blueprint-ai",
        message="unsafe target",
        recommendation="none",
        remediation=Remediation(
            kind="template", target="../escape", safe=True, description="escape"
        ),
    )
    result = apply_findings(python_project, discover_project(python_project), [finding])
    assert result.changes[0].status == "conflicted"
    assert not (python_project.parent / "escape").exists()


def test_model_remediation_is_never_applied(python_project: Path) -> None:
    finding = Finding(
        blueprint="repository",
        category="model",
        source="fake:model",
        provenance="model",
        message="model change",
        recommendation="none",
        remediation=Remediation(
            kind="template", target="README.md", safe=True, description="model change"
        ),
    )
    result = apply_findings(python_project, discover_project(python_project), [finding])
    assert result.changes == []
    assert not (python_project / "README.md").exists()


def test_javascript_test_scaffold_uses_native_node_test(js_project: Path) -> None:
    context, _ = make_context(js_project, blueprints=["testing"], model_mode="off")
    report = review(context)
    result = apply_findings(js_project, report.facts, report.findings)
    assert [change.target for change in result.changed] == ["tests/smoke.test.js"]
    assert 'from "node:test"' in (js_project / "tests" / "smoke.test.js").read_text()
