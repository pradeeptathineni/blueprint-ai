from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from blueprint_ai import cli, engine
from blueprint_ai.core import BlueprintResult, Finding, RunReport, ToolStatus
from blueprint_ai.core.models import Disposition, Remediation, ResultStatus
from blueprint_ai.discovery import discover_project
from blueprint_ai.output import report_human, report_markdown, report_plan
from blueprint_ai.remediation import apply_findings

runner = CliRunner()


def test_manual_finding_stays_visible_from_review_through_noop_apply(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print(1)\n")
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    (tmp_path / ".editorconfig").write_text("root = true\n")
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    for command in ("review", "plan", "apply"):
        result = runner.invoke(
            cli.app, [command, str(tmp_path), "--blueprint", "repository", "--model", "off"]
        )
        assert result.exit_code == 0, result.output
        assert "1 new, 0 baseline, 0 suppressed" in result.output
        assert "LICENSE is missing" in result.output
        assert "manual review" in result.output
        assert "No supported remediations were needed" not in result.output
        if command == "apply":
            assert "No automatic repair was applied" in result.output
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    result = runner.invoke(
        cli.app, ["apply", str(tmp_path), "--blueprint", "repository", "--model", "off", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"changes": [], "verification": None}


def test_conflicted_repair_keeps_finding_and_noop_summary(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print(1)\n")
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / ".editorconfig").mkdir()
    result = runner.invoke(
        cli.app, ["apply", str(tmp_path), "--blueprint", "repository", "--model", "off"]
    )
    assert result.exit_code == 0, result.output
    assert "No automatic repair was applied" in result.output
    assert "safe repair candidate" in result.output
    assert ".editorconfig is missing" in result.output
    assert "conflicted" in result.output
    assert (tmp_path / ".editorconfig").is_dir()


def test_changed_blueprint_verification_keeps_other_manual_work_visible(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("# TODO: select the next implementation task\nprint(1)\n")
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    args = [str(tmp_path), "--blueprint", "repository", "--blueprint", "code-design"]
    applied = runner.invoke(cli.app, ["apply", *args, "--model", "off"])
    assert applied.exit_code == 0, applied.output
    assert (tmp_path / ".editorconfig").is_file()
    assert "Verification of changed blueprints only" in applied.output
    assert "Other pre-apply results (not rerun)" in applied.output
    assert "Unresolved work marker" in applied.output
    assert "manual review" in applied.output
    verified = runner.invoke(cli.app, ["verify", *args])
    assert verified.exit_code == 0, verified.output
    assert "Unresolved work marker" in verified.output
    repeated = runner.invoke(cli.app, ["apply", *args, "--model", "off"])
    assert repeated.exit_code == 0, repeated.output
    assert "No automatic repair was applied" in repeated.output
    assert "Unresolved work marker" in repeated.output


def test_partial_coverage_survives_empty_plan_and_apply(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "app.py").write_text("print(1)\n")
    monkeypatch.setattr(
        engine,
        "_run_tools",
        lambda *_a, **_k: [
            (
                ToolStatus(
                    name="fixture-checker",
                    available=False,
                    outcome="unsupported",
                    detail="disabled for the synthetic untrusted project",
                ),
                [],
                None,
            )
        ],
    )
    for command in ("plan", "apply"):
        result = runner.invoke(
            cli.app, [command, str(tmp_path), "--blueprint", "code-quality", "--model", "off"]
        )
        assert result.exit_code == 0, result.output
        assert "code-quality: partial (0 findings)" in result.output
        assert "fixture-checker: unsupported" in result.output
        assert "disabled for the synthetic untrusted project" in result.output
        if command == "plan":
            assert "No active findings were returned" in result.output


@pytest.mark.parametrize("change", ["model", "foreign", "baseline", "suppressed", "unsafe"])
def test_candidate_label_and_native_mutation_share_eligibility(tmp_path: Path, change: str) -> None:
    (tmp_path / "app.py").write_text("print(1)\n")
    finding = Finding(
        blueprint="repository",
        category="missing-baseline",
        source="blueprint-ai",
        message=".editorconfig is missing.",
        recommendation="Add an editor baseline.",
        remediation=Remediation(
            kind="template", target=".editorconfig", safe=True, description="editor baseline"
        ),
    )
    if change == "model":
        finding.provenance = "model"
    if change == "foreign":
        finding.source = "fixture-import"
    if change in {"baseline", "suppressed"}:
        finding.disposition = cast(Disposition, change)
    if change == "unsafe":
        finding.remediation.safe = False  # type: ignore[union-attr]
    facts = discover_project(tmp_path)
    result = apply_findings(tmp_path, facts, [finding])
    assert not result.changed
    assert not (tmp_path / ".editorconfig").exists()
    report = RunReport(
        facts=facts,
        profile="default",
        results=[BlueprintResult(blueprint="repository", status="findings", findings=[finding])],
    )
    assert "manual review" in report_human(report)
    assert "safe repair candidate" not in report_human(report)


@pytest.mark.parametrize("status", ["partial", "error", "tool_missing"])
def test_incomplete_assessment_never_prints_unqualified_no_findings(
    tmp_path: Path, status: str
) -> None:
    facts = discover_project(tmp_path)
    report = RunReport(
        facts=facts,
        profile="default",
        results=[BlueprintResult(blueprint="fixture", status=cast(ResultStatus, status))],
    )
    assert "No findings from completed checks; analysis is incomplete." in report_markdown(report)
    assert f"fixture: {status} (0 findings)" in report_plan(report, [])
    assert f"fixture: {status} (0 findings)" in report_human(report)
