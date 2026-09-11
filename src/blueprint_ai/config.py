from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SuppressionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str | None = None
    blueprint: str | None = None
    category: str | None = None
    file: str | None = None
    reason: str
    expires: date | None = None


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_blueprints: list[str] = Field(default_factory=list)
    disabled_blueprints: list[str] = Field(default_factory=list)
    profiles: dict[str, list[str]] = Field(default_factory=dict)
    tool_overrides: dict[str, str] = Field(default_factory=dict)
    ignores: list[str] = Field(default_factory=list)
    minimum_severity: Literal["critical", "high", "medium", "low", "info"] = "info"
    maximum_priority: Literal["P0", "P1", "P2", "P3"] = "P3"
    model_mode: Literal["auto", "off", "on"] = "auto"
    model_budget: int = Field(default=12_000, ge=512)
    blueprint_model_budgets: dict[str, int] = Field(default_factory=dict)
    tool_timeout: int = Field(default=120, ge=1, le=3600)
    max_workers: int = Field(default=4, ge=1, le=32)
    baseline_path: str | None = ".blueprint-ai/baseline.json"
    suppressions: list[SuppressionRule] = Field(default_factory=list)
    authorized_target: str | None = None
    output_path: str = ".blueprint-ai"

    @field_validator("output_path")
    @classmethod
    def output_stays_in_project(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("output_path must stay within the project")
        return value

    @field_validator("baseline_path")
    @classmethod
    def baseline_stays_in_project(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("baseline_path must stay within the project")
        return value

    @field_validator("blueprint_model_budgets")
    @classmethod
    def validate_blueprint_budgets(cls, value: dict[str, int]) -> dict[str, int]:
        if any(budget < 256 for budget in value.values()):
            raise ValueError("blueprint model budgets must be at least 256 tokens")
        return value

    @field_validator("authorized_target")
    @classmethod
    def validate_authorized_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("authorized_target must be an explicit HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("authorized_target must not embed credentials")
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
