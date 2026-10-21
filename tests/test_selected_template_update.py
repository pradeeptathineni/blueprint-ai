"""Owned template updates preserve authored work and exact rollback bytes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from blueprint_ai.evolution import template_update
from blueprint_ai.evolution.template_update import apply, check, initialize, plan, rollback


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def template_pair(tmp_path: Path) -> tuple[Path, Path, str, str]:
    source = tmp_path / "template"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "app.py").write_text(
        '"""Small API."""\n\n'
        'def route(path):\n    if path == "/":\n        return "{{project}}"\n'
        '    return "missing"\n\n\n'
        'def status():\n    return "ready"\n'
    )
    (source / "README.md").write_text("# {{project}}\n\nStatus: basic\n\nGenerated API.\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "v1")
    first = git(source, "rev-parse", "HEAD")
    (source / "app.py").write_text(
        '"""Small API."""\n\n'
        'def route(path):\n    if path == "/":\n        return "{{project}}"\n'
        '    return "missing"\n\n\n'
        'def status():\n    return "ready"\n\n\n'
        'def health():\n    return {"status": "ok"}\n'
    )
    (source / "README.md").write_text("# {{project}}\n\nStatus: healthy\n\nGenerated API.\n")
    (source / "test_health.py").write_text(
        'from app import health\n\ndef test_health():\n    assert health() == {"status": "ok"}\n'
    )
    git(source, "add", ".")
    git(source, "commit", "-qm", "v2")
    second = git(source, "rev-parse", "HEAD")
    return source, tmp_path / "project", first, second


def test_conflict_resolution_verify_replay_and_rollback(template_pair):
    source, project, first, second = template_pair
    initialize(project, source, first, {"project": "Sample API"})
    original_app = (project / "app.py").read_text()
    (project / "app.py").write_text(
        original_app.replace(
            '    return "missing"',
            '    if path == "/mine":\n        return "authored"\n    return "missing"',
        )
    )
    (project / "README.md").write_text(
        "# Sample API\n\nStatus: customized\n\nGenerated API.\n\nMy notes.\n"
    )
    before = {p: (project / p).read_bytes() for p in ("app.py", "README.md")}
    preview = plan(project, second)
    assert preview["status"] == "conflict"
    assert (
        next(row for row in preview["rows"] if row["path"] == "README.md")["status"] == "conflict"
    )
    with pytest.raises(ValueError, match="unresolved"):
        apply(project, preview)
    assert (project / "test_health.py").exists() is False
    merged = project.parent / "resolved-readme.md"
    merged.write_text(
        "# Sample API\n\nStatus: healthy and customized\n\nGenerated API.\n\nMy notes.\n"
    )
    ready = plan(project, second, {"README.md": str(merged)})
    assert ready["status"] == "ready"
    result = apply(project, ready)
    assert result["status"] == "verified"
    assert 'return "authored"' in (project / "app.py").read_text()
    assert "def health" in (project / "app.py").read_text()
    assert "My notes." in (project / "README.md").read_text()
    assert check(project, second)["status"] == "current"
    assert plan(project, second)["status"] == "noop"
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_health.py"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / "unrelated.txt").write_text("keep me\n")
    assert rollback(project, result["operation_id"])["status"] == "rolled_back"
    assert all((project / path).read_bytes() == content for path, content in before.items())
    assert not (project / "test_health.py").exists()
    assert (project / "unrelated.txt").read_text() == "keep me\n"
    assert check(project, second)["status"] == "behind"


def test_stale_plan_and_changed_output_refuse(template_pair):
    source, project, first, second = template_pair
    initialize(project, source, first, {"project": "Sample API"})
    ready = plan(project, second)
    (project / "README.md").write_text("foreign change\n")
    with pytest.raises(ValueError, match="stale"):
        apply(project, ready)
    assert not (project / "test_health.py").exists()
    (project / "README.md").write_text("# Sample API\n\nStatus: basic\n\nGenerated API.\n")
    result = apply(project, ready)
    (project / "app.py").write_text("foreign change\n")
    with pytest.raises(ValueError, match="rollback refused"):
        rollback(project, result["operation_id"])
    assert (project / "test_health.py").exists()


def test_reject_symlinks_and_changed_plan(template_pair):
    source, project, first, second = template_pair
    initialize(project, source, first, {"project": "Sample API"})
    ready = plan(project, second)
    changed = json.loads(json.dumps(ready))
    changed["rows"][0]["status"] = "delete"
    with pytest.raises(ValueError, match="checksum"):
        apply(project, changed)
    (project / "app.py").unlink()
    (project / "app.py").symlink_to(source / "app.py")
    with pytest.raises(ValueError, match="symbolic link"):
        plan(project, second)


def test_publication_failure_restores_exact_prior_bytes(template_pair, monkeypatch):
    source, project, first, second = template_pair
    initialize(project, source, first, {"project": "Sample API"})
    ready = plan(project, second)
    original = {
        path: (project / path).read_bytes()
        for path in ("app.py", "README.md", ".blueprint-ai/template.json")
    }
    actual_replace = template_update.os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication failure")
        return actual_replace(source, target)

    monkeypatch.setattr(template_update.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        apply(project, ready)
    assert all((project / path).read_bytes() == value for path, value in original.items())
    assert not (project / "test_health.py").exists()
    assert list((project / ".blueprint-ai/operations").glob("*.json")) == []
