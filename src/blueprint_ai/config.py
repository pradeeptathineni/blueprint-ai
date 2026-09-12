from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from yaml.events import AliasEvent

from blueprint_ai.safety import MAX_CONFIG_BYTES, read_text_bounded


class _NoAliasSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate YAML mapping keys are not accepted")
        return super().construct_mapping(node, deep=deep)

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise ValueError("YAML aliases are not accepted in project configuration")
        return super().compose_node(parent, index)


# PyYAML defaults to YAML 1.1, where ordinary values such as "on" and "off" become booleans.
# Configuration uses YAML 1.2 boolean behavior so enum strings retain their documented meaning.
_NoAliasSafeLoader.yaml_implicit_resolvers = {
    key: [item for item in resolvers if item[0] != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_NoAliasSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_yaml_mapping(path: Path, root: Path, *, label: str) -> dict[str, Any]:
    try:
        text = read_text_bounded(path, MAX_CONFIG_BYTES, root=root)
        raw = yaml.load(text, Loader=_NoAliasSafeLoader) or {}
    except (OSError, UnicodeError, ValueError, RecursionError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must contain a mapping")
    return raw


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

    enabled_blueprints: list[str] = Field(default_factory=list, max_length=256)
    disabled_blueprints: list[str] = Field(default_factory=list, max_length=256)
    profiles: dict[str, list[str]] = Field(default_factory=dict)
    tool_overrides: dict[str, str] = Field(default_factory=dict)
    ignores: list[str] = Field(default_factory=list, max_length=1_000)
    minimum_severity: Literal["critical", "high", "medium", "low", "info"] = "info"
    maximum_priority: Literal["P0", "P1", "P2", "P3"] = "P3"
    model_mode: Literal["auto", "off", "on"] = "auto"
    model_budget: int = Field(default=12_000, ge=512)
    model_max_calls: int = Field(default=6, ge=0, le=64)
    model_cache: Literal["read-write", "read-only", "refresh", "off"] = "read-write"
    blueprint_model_budgets: dict[str, int] = Field(default_factory=dict)
    tool_timeout: int = Field(default=120, ge=1, le=3600)
    max_workers: int = Field(default=4, ge=1, le=32)
    max_tool_output_bytes: int = Field(default=4_000_000, ge=16_384, le=32_000_000)
    offline: bool = False
    baseline_path: str | None = ".blueprint-ai/baseline.json"
    suppressions: list[SuppressionRule] = Field(default_factory=list, max_length=10_000)
    authorized_target: str | None = None
    output_path: str = ".blueprint-ai"
    fail_on_priority: Literal["P0", "P1", "P2", "P3"] | None = None

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

    @field_validator("tool_overrides")
    @classmethod
    def validate_tool_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not name or not executable or "\x00" in executable for name, executable in value.items()
        ):
            raise ValueError("tool overrides require non-empty names and executable paths")
        return value

    @field_validator("ignores")
    @classmethod
    def validate_ignores(cls, value: list[str]) -> list[str]:
        if any(len(pattern) > 1_000 or "\x00" in pattern for pattern in value):
            raise ValueError("ignore patterns must be at most 1000 characters and contain no NUL")
        return value


def load_settings(
    root: Path,
    *,
    trust_project_executables: bool = False,
    authorized_network_target: str | None = None,
) -> Settings:
    path = root / ".blueprint-ai.yml"
    if not path.is_file():
        return Settings()
    raw = load_yaml_mapping(path, root, label=".blueprint-ai.yml")
    if raw.get("tool_overrides") and not trust_project_executables:
        raise ValueError(
            "project tool_overrides are privileged; rerun with --trust-project-executables "
            "only after reviewing the repository configuration"
        )
    configured_target = raw.get("authorized_target")
    if configured_target and configured_target != authorized_network_target:
        raise ValueError(
            "authorized_target in project config is not operator authorization; pass the same "
            "URL with --authorize-target after confirming scope"
        )
    if authorized_network_target:
        raw["authorized_target"] = authorized_network_target
    return Settings.model_validate(raw)


def merge_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    values = deepcopy(base.model_dump())
    for key, value in overrides.items():
        if value is not None:
            values[key] = value
    return Settings.model_validate(values)
