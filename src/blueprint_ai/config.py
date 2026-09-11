from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    enabled_blueprints: list[str] = Field(default_factory=list)
    disabled_blueprints: list[str] = Field(default_factory=list)
    profiles: dict[str, list[str]] = Field(default_factory=dict)
    tool_overrides: dict[str, str] = Field(default_factory=dict)
    ignores: list[str] = Field(default_factory=list)
    minimum_severity: Literal["critical", "high", "medium", "low", "info"] = "info"
    maximum_priority: Literal["P0", "P1", "P2", "P3"] = "P3"
    model_mode: Literal["auto", "off", "on"] = "auto"
    model_budget: int = Field(default=12_000, ge=512)
    output_path: str = ".blueprint-ai"

    @field_validator("output_path")
    @classmethod
    def output_stays_in_project(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("output_path must stay within the project")
        return value


def load_settings(root: Path) -> Settings:
    path = root / ".blueprint-ai.yml"
    if not path.is_file():
        return Settings()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(".blueprint-ai.yml must contain a mapping")
    return Settings.model_validate(raw)


def merge_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    values = deepcopy(base.model_dump())
    for key, value in overrides.items():
        if value is not None:
            values[key] = value
    return Settings.model_validate(values)
