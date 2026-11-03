from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_cli import run_cli

from blueprint_ai.config import Settings, load_settings
from blueprint_ai.engine import make_context, review
from blueprint_ai.settings import (
    CONFIG_FILE,
    SettingsConflict,
    preview_settings,
    read_settings,
    write_settings,
)


def entries(result: dict) -> dict:
    return {item["id"]: item for item in result["settings"]}


def test_defaults_preview_persistence_and_reset(tmp_path: Path) -> None:
    before = read_settings(tmp_path)
    assert before["schema"] == "blueprint.settings.v1"
    assert entries(before)["max_workers"]["effective"] == Settings().max_workers
    assert entries(before)["max_workers"]["source"] == "default"
    draft = preview_settings(tmp_path, {"max_workers": 8}, [], before["revision"])
    assert draft["saved"] is False
    assert not (tmp_path / CONFIG_FILE).exists()
    saved = write_settings(tmp_path, {"max_workers": 8}, [], before["revision"])
    assert saved["saved"] is True
    assert load_settings(tmp_path).max_workers == 8
    assert entries(read_settings(tmp_path))["max_workers"]["source"] == "project"
    assert os.stat(tmp_path / CONFIG_FILE).st_mode & 0o777 == 0o600
    reset = write_settings(tmp_path, {}, ["max_workers"], saved["revision"])
    assert entries(reset)["max_workers"]["effective"] == Settings().max_workers
    assert entries(reset)["max_workers"]["source"] == "default"
    assert load_settings(tmp_path) == Settings()
    assert reset["revision"] == "missing"
    assert not (tmp_path / CONFIG_FILE).exists()


def test_reset_missing_override_writes_nothing(tmp_path: Path) -> None:
    result = write_settings(tmp_path, {}, ["tool_timeout"], "missing")
    assert result["revision"] == "missing"
    assert not (tmp_path / CONFIG_FILE).exists()


def test_existing_coercible_values_keep_native_meaning_and_bytes(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_FILE
    path.write_text('max_workers: "3"\nmodel_budget: "4096"\n')
    before = read_settings(tmp_path)
    assert entries(before)["max_workers"]["effective"] == load_settings(tmp_path).max_workers == 3
    result = write_settings(tmp_path, {"tool_timeout": 30}, [], before["revision"])
    assert path.read_text().startswith('max_workers: "3"\nmodel_budget: "4096"\n')
    assert entries(result)["max_workers"]["effective"] == 3


def test_unrelated_bytes_and_privileged_values_stay_private(tmp_path: Path) -> None:
    private = (
        "# Operator-owned settings\n"
        "tool_overrides: {ruff: /private/tool}\n"
        "authorized_target: https://private.example.test\n"
        "model_mode: off # keep offline fixture mode\n"
        "max_workers: 3 # local machine\n"
    )
    path = tmp_path / CONFIG_FILE
    path.write_text(private)
    path.chmod(0o640)
    before = read_settings(tmp_path)
    result = write_settings(tmp_path, {"max_workers": 6}, [], before["revision"])
    assert path.read_text() == private.replace("max_workers: 3", "max_workers: 6")
    assert path.stat().st_mode & 0o777 == 0o640
    assert "/private/tool" not in json.dumps(result)
    assert "private.example.test" not in json.dumps(result)
    with pytest.raises(ValueError, match="privileged"):
        load_settings(tmp_path)
    reset = write_settings(tmp_path, {}, ["max_workers"], result["revision"])
    assert "tool_overrides: {ruff: /private/tool}" in path.read_text()
    assert "# local machine" in path.read_text()
    assert entries(reset)["max_workers"]["source"] == "default"


@pytest.mark.parametrize(
    "updates",
    [
        {"max_workers": 0},
        {"max_workers": 33},
        {"max_workers": "8"},
        {"max_workers": True},
        {"max_workers": 1.5},
        {"tool_timeout": 3601},
        {"minimum_severity": "unknown"},
        {"model_mode": "on"},
        {"authorized_target": "https://example.test"},
        {"tool_overrides": {}},
        {"max_tool_output_bytes": 100},
        {"fail_on_priority": "P4"},
    ],
)
def test_invalid_or_privileged_updates_preserve_document(tmp_path: Path, updates: dict) -> None:
    path = tmp_path / CONFIG_FILE
    path.write_text("# keep\nmax_workers: 4\n")
    data = path.read_bytes()
    revision = read_settings(tmp_path)["revision"]
    with pytest.raises(ValueError):
        write_settings(tmp_path, updates, [], revision)
    assert path.read_bytes() == data
    assert not (tmp_path / ".blueprint-ai.settings.lock").exists()


def test_stale_edits_and_busy_writer_are_refused(tmp_path: Path) -> None:
    revision = read_settings(tmp_path)["revision"]
    write_settings(tmp_path, {"tool_timeout": 60}, [], revision)
    with pytest.raises(SettingsConflict, match="changed"):
        write_settings(tmp_path, {"max_workers": 8}, [], revision)
    lock = tmp_path / ".blueprint-ai.settings.lock"
    lock.mkdir()
    with pytest.raises(SettingsConflict, match="progress"):
        write_settings(tmp_path, {"max_workers": 8}, [], read_settings(tmp_path)["revision"])
    assert lock.is_dir()
    assert load_settings(tmp_path).tool_timeout == 60


@pytest.mark.parametrize(
    "document",
    [
        "max_workers: 2\nmax_workers: 3\n",
        "max_workers: &value 2\ntool_timeout: *value\n",
        "[1, 2]\n",
        "max_workers: invalid-secret-value\n",
        "model_budget: private-invalid\n",
    ],
)
def test_invalid_existing_documents_are_not_repaired_silently(
    tmp_path: Path,
    document: str,
) -> None:
    path = tmp_path / CONFIG_FILE
    path.write_text(document)
    with pytest.raises(ValueError) as error:
        read_settings(tmp_path)
    assert "invalid-secret-value" not in str(error.value)
    assert "private-invalid" not in str(error.value)
    assert path.read_text() == document


def test_symlink_and_nonblock_layout_remain_untouched(tmp_path: Path) -> None:
    outside = tmp_path / "original.yml"
    outside.write_text("max_workers: 3\n")
    path = tmp_path / CONFIG_FILE
    path.symlink_to(outside)
    with pytest.raises(OSError):
        read_settings(tmp_path)
    assert outside.read_text() == "max_workers: 3\n"
    path.unlink()
    path.write_text("{max_workers: 3}\n")
    with pytest.raises(ValueError, match="block"):
        write_settings(tmp_path, {"max_workers": 8}, [], read_settings(tmp_path)["revision"])
    assert path.read_text() == "{max_workers: 3}\n"


def test_saved_filters_reach_real_review_and_cli_override(python_project: Path) -> None:
    base, _ = make_context(python_project, blueprints=["repository"], model_mode="off")
    assert review(base).active_findings
    revision = read_settings(python_project)["revision"]
    saved = write_settings(
        python_project, {"maximum_priority": "P0", "tool_timeout": 30}, [], revision
    )
    current, settings = make_context(python_project, blueprints=["repository"], model_mode="off")
    assert settings.tool_timeout == 30
    assert current.config["tool_timeout"] == 30
    assert not review(current).active_findings
    write_settings(
        python_project, {"fail_on_priority": "P3"}, ["maximum_priority"], saved["revision"]
    )
    result = run_cli(
        "review", str(python_project), "--blueprint", "repository", "--no-model", "--json"
    )
    assert result.returncode == 1, result.stderr
    override = run_cli(
        "review",
        str(python_project),
        "--blueprint",
        "repository",
        "--no-model",
        "--fail-on",
        "P0",
        "--json",
    )
    assert override.returncode == 0, override.stderr


def test_cli_discovery_preview_save_filter_and_reset(tmp_path: Path) -> None:
    show = run_cli("settings", "show", str(tmp_path), "--json")
    assert show.returncode == 0, show.stderr
    assert json.loads(show.stdout)["owner"] == "blueprint-ai"
    preview = run_cli("settings", "set", "max_workers", "8", str(tmp_path), "--dry-run", "--json")
    assert preview.returncode == 0, preview.stderr
    assert not (tmp_path / CONFIG_FILE).exists()
    save = run_cli(
        "settings", "set", "max_workers", "8", str(tmp_path), "--expect", "missing", "--json"
    )
    assert save.returncode == 0, save.stderr
    stale = run_cli("settings", "set", "tool_timeout", "30", str(tmp_path), "--expect", "missing")
    assert stale.returncode != 0
    changed = run_cli(
        "settings", "show", str(tmp_path), "--changed", "--query", "workers", "--json"
    )
    assert [row["id"] for row in json.loads(changed.stdout)["settings"]] == ["max_workers"]
    reset = run_cli("settings", "reset", "max_workers", str(tmp_path), "--json")
    assert reset.returncode == 0, reset.stderr
    assert entries(json.loads(reset.stdout))["max_workers"]["source"] == "default"
