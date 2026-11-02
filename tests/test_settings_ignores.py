from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_cli import run_cli

from blueprint_ai.config import load_settings
from blueprint_ai.settings import read_settings, write_settings


@pytest.mark.parametrize("git_project", [False, True])
def test_cli_ignore_default_changes_real_inspection_and_reset(tmp_path: Path, git_project: bool):
    if git_project:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "keep.py").write_text("keep = True\n")
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "generated.py").write_text("generated = True\n")

    def python_count():
        result = run_cli("inspect", str(tmp_path), "--json")
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["languages"]["Python"]

    assert python_count() == 2
    result = run_cli("settings", "set", "ignores", '["scratch/"]', str(tmp_path), "--json")
    assert result.returncode == 0, result.stderr
    setting = next(row for row in json.loads(result.stdout)["settings"] if row["id"] == "ignores")
    assert setting["effective"] == ["scratch/"]
    assert setting["default"] == [] and setting["source"] == "project"
    assert setting["scope"] == "project" and setting["constraints"]["type"] == "array"
    assert python_count() == 1
    reset = run_cli("settings", "reset", "ignores", str(tmp_path), "--json")
    assert reset.returncode == 0, reset.stderr
    assert python_count() == 2


def test_list_edit_preserves_unrelated_yaml_and_existing_precedence(tmp_path: Path):
    path = tmp_path / ".blueprint-ai.yml"
    path.write_text(
        "# Keep\nmodel_mode: off # no inference\nignores:\n  - old/\n# Next limit\nmax_workers: 3\n"
    )
    saved = write_settings(
        tmp_path, {"ignores": ["scratch/", "*.log"]}, [], read_settings(tmp_path)["revision"]
    )
    assert path.read_text().startswith("# Keep\nmodel_mode: off # no inference\n")
    assert path.read_text().endswith("# Next limit\nmax_workers: 3\n")
    assert load_settings(tmp_path).ignores == ["scratch/", "*.log"]
    write_settings(tmp_path, {}, ["ignores"], saved["revision"])
    assert "ignores:" not in path.read_text()
    (tmp_path / "keep.py").write_text("")
    (tmp_path / "skip.py").write_text("")
    (tmp_path / ".gitignore").write_text("skip.py\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("")
    write_settings(
        tmp_path, {"ignores": ["!skip.py", "!.venv/"]}, [], read_settings(tmp_path)["revision"]
    )
    result = run_cli("inspect", str(tmp_path), "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["languages"]["Python"] == 1


@pytest.mark.parametrize("value", ["scratch/", [False], ["bad\x00"], ["x" * 1001], ["x"] * 1001])
def test_invalid_ignore_edits_do_not_create_a_document(tmp_path: Path, value):
    with pytest.raises(ValueError):
        write_settings(tmp_path, {"ignores": value}, [], "missing")
    assert read_settings(tmp_path)["revision"] == "missing"
