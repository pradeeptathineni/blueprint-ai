import json
import subprocess
from pathlib import Path

import pytest

from blueprint_ai.evolution import apply_evolution, maintenance, plan_evolution, template_update


def commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Synthetic"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "synthetic@example.test"], check=True
    )
    subprocess.run(["git", "-C", str(root), "add", "--all"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def test_capability_template_evolution_cold_history_and_scoped_rollback(tmp_path: Path):
    source = tmp_path / "template"
    source.mkdir()
    (source / "README.md").write_text("# Service\n\nKeep this project readable.\n")
    first = commit(source, "template v1")

    project = tmp_path / "project"
    template_update.initialize(project, source, first, {})
    (project / "local-notes.txt").write_text("author-owned\n")

    proposal = maintenance.plan_capability(project, "dependency-updates", source)
    assert proposal["status"] == "ready"
    assert proposal["template_binding"]["commit"] == first
    row = next(item for item in proposal["files"] if item["path"] == "renovate.json")
    assert row["before_sha256"] is None
    assert json.loads(row["content"]) == {"extends": ["config:recommended"]}

    with pytest.raises(ValueError, match="project or capability changed"):
        maintenance.apply_capability(project, "dependency-updates", "0" * 64, source)
    applied = maintenance.apply_capability(
        project, "dependency-updates", proposal["plan_sha256"], source
    )
    assert applied["status"] == "verified"
    capability_operation = applied["operation_id"]
    assert (project / "renovate.json").read_text() == row["content"]
    history = maintenance.history(project)
    assert any(item["operation_id"] == capability_operation for item in history["operations"])

    (source / "README.md").write_text("# Service v2\n\nKeep this project readable.\n")
    (source / "renovate.json").write_text('{"extends": ["config:base"]}\n')
    second = commit(source, "template v2")
    blocked = template_update.plan(project, second)
    assert blocked["status"] == "conflict"
    assert (project / "renovate.json").read_text() == row["content"]
    assert (project / "local-notes.txt").read_text() == "author-owned\n"

    resolved = template_update.plan(project, second, {"renovate.json": "template"})
    assert resolved["status"] == "ready"
    update = template_update.apply(project, resolved)
    assert update["status"] == "verified"
    template_operation = update["operation_id"]
    assert json.loads((project / "renovate.json").read_text()) == {"extends": ["config:base"]}
    assert (project / "local-notes.txt").read_text() == "author-owned\n"

    capability_rollback = maintenance.rollback(project, capability_operation)
    assert any(change["status"] == "conflicted" for change in capability_rollback["changes"])
    assert (project / "renovate.json").is_file(), (
        "a later template-authored file is never deleted by the old capability receipt"
    )

    template_rollback = maintenance.rollback(project, template_operation)
    assert template_rollback["status"] == "rolled_back"
    assert (project / "renovate.json").read_text() == row["content"]
    capability_rollback = maintenance.rollback(project, capability_operation)
    assert all(change["status"] != "conflicted" for change in capability_rollback["changes"])
    assert not (project / "renovate.json").exists()
    assert (project / "local-notes.txt").read_text() == "author-owned\n"

    cold_source = source.with_name("template-unavailable")
    source.rename(cold_source)
    reopened = maintenance.history(project)
    assert reopened["template"]["commit"] == first
    assert reopened["template"]["source_available"] is False
    assert reopened["operations"] == []


def test_conflict_and_source_binding_refuse_without_project_writes(tmp_path: Path):
    source = tmp_path / "template"
    source.mkdir()
    (source / "README.md").write_text("# Existing\n")
    commit_id = commit(source, "template")
    project = tmp_path / "project"
    template_update.initialize(project, source, commit_id, {})
    (project / "renovate.json").write_text('{"custom": true}\n')
    before = (project / "renovate.json").read_bytes()
    with pytest.raises(ValueError, match="configured template source"):
        maintenance.plan_capability(project, "dependency-updates", tmp_path)
    plan = maintenance.plan_capability(project, "dependency-updates", source)
    assert plan["status"] == "conflict"
    with pytest.raises(ValueError, match="conflict"):
        maintenance.apply_capability(project, "dependency-updates", plan["plan_sha256"], source)
    assert (project / "renovate.json").read_bytes() == before
    assert maintenance.history(project)["operations"] == []


def test_cold_history_marks_unqualified_receipts_instead_of_reporting_empty(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    operations = project / ".blueprint-ai" / "operations"
    operations.mkdir(parents=True)
    (operations / ("a" * 32 + ".json")).write_text("{truncated")

    retained = maintenance.history(project)

    assert retained["operations"] == []
    assert retained["history_status"] == "partial"
    assert retained["unreadable_receipts"] == [
        {"receipt_id": "a" * 32, "reason": "unreadable-or-invalid"}
    ]


def test_supported_configuration_modernization_is_previewed_retained_and_reversible(tmp_path: Path):
    source = tmp_path / "template"
    source.mkdir()
    (source / "README.md").write_text("# Service\n")
    first = commit(source, "template")
    project = tmp_path / "project"
    template_update.initialize(project, source, first, {})
    workflow = project / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    original = (
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  check:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: third-party/example@v4\n"
    )
    workflow.write_text(original)
    unrelated = project / "author-notes.txt"
    unrelated.write_text("preserve local notes\n")
    commit(project, "existing project")
    from subprocess import run

    run(["git", "-C", str(project), "checkout", "-qb", "config-modernization"], check=True)

    plan = plan_evolution(project, ["github-actions/pin-official-actions"])
    assert plan.status == "ready"
    assert plan.steps[0].files == [".github/workflows/ci.yml"]
    preview = apply_evolution(
        project, plan, dry_run=True, run_blueprint_review=False, report_failures=True
    )
    assert preview.status == "dry_run"
    assert "actions/checkout@v4" in preview.diffs[".github/workflows/ci.yml"]
    assert workflow.read_text() == original

    applied = apply_evolution(project, plan, run_blueprint_review=False, report_failures=True)
    assert applied.status == "verified"
    assert applied.operation_id
    modernized = workflow.read_text()
    assert "actions/checkout@v4" not in modernized
    assert "actions/checkout@" in modernized
    assert "third-party/example@v4" in modernized
    assert unrelated.read_text() == "preserve local notes\n"

    retained = maintenance.history(project)
    row = next(
        item for item in retained["operations"] if item["operation_id"] == applied.operation_id
    )
    assert row["kind"] == "evolution"
    assert row["transformations"] == ["github-actions/pin-official-actions"]
    assert row["changes"][0]["target"] == ".github/workflows/ci.yml"

    rolled_back = maintenance.rollback(project, applied.operation_id)
    assert rolled_back["status"] == "rolled_back"
    assert workflow.read_text() == original
    assert unrelated.read_text() == "preserve local notes\n"
