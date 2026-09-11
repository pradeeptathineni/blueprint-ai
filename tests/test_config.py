from pathlib import Path

import pytest

from blueprint_ai.config import Settings, load_settings, merge_settings


def test_defaults_require_no_config(tmp_path: Path) -> None:
    assert load_settings(tmp_path) == Settings()


def test_config_rejects_output_path_escape(tmp_path: Path) -> None:
    (tmp_path / ".blueprint-ai.yml").write_text("output_path: ../outside\n")
    with pytest.raises(ValueError, match="within the project"):
        load_settings(tmp_path)


def test_settings_merge_keeps_unspecified_values() -> None:
    settings = Settings(disabled_blueprints=["iac"])
    merged = merge_settings(settings, {"model_mode": "off", "model_budget": None})
    assert merged.disabled_blueprints == ["iac"]
    assert merged.model_mode == "off"
    assert merged.model_budget == 12_000
