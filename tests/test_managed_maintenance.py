import json
import subprocess
from pathlib import Path

import pytest

from blueprint_ai.evolution import maintenance, template_update


def commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Synthetic"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "synthetic@example.test"], check=True)
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
    assert (project / "renovate.json").is_file(), "a later template-authored file is never deleted by the old capability receipt"

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
