from __future__ import annotations

import fnmatch
import itertools
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from blueprint_ai.blueprints.catalog import Blueprint
from blueprint_ai.config import load_yaml_mapping
from blueprint_ai.core import Finding, ProjectFacts
from blueprint_ai.core.models import Priority, Severity
from blueprint_ai.discovery import iter_project_files
from blueprint_ai.safety import read_text_bounded


class ApplicabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages_any: list[str] = Field(default_factory=list, max_length=32)
    project_types_any: list[str] = Field(default_factory=list, max_length=32)
    files_any: list[str] = Field(default_factory=list, max_length=64)


class RuleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    message: str = Field(min_length=1, max_length=500)
    recommendation: str = Field(min_length=1, max_length=500)


class RequiredFilesRule(RuleBase):
    kind: Literal["required-files"]
    paths_any: list[str] = Field(min_length=1, max_length=64)

    @field_validator("paths_any")
    @classmethod
    def safe_paths(cls, value: list[str]) -> list[str]:
        _validate_patterns(value)
        return value


class ForbiddenTextRule(RuleBase):
    kind: Literal["forbidden-text"]
    files: list[str] = Field(min_length=1, max_length=64)
    literal: str = Field(min_length=1, max_length=200)

    @field_validator("files")
    @classmethod
    def safe_paths(cls, value: list[str]) -> list[str]:
        _validate_patterns(value)
        return value


Rule = Annotated[RequiredFilesRule | ForbiddenTextRule, Field(discriminator="kind")]


class CustomBlueprintSpec(BaseModel):
    """Safe project-local extension contract. It has no executable or import hook."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^local-[a-z0-9][a-z0-9-]{0,55}$")
    description: str = Field(min_length=1, max_length=300)
    applicability: ApplicabilitySpec = Field(default_factory=ApplicabilitySpec)
    rules: list[Rule] = Field(min_length=1, max_length=128)

    @field_validator("rules")
    @classmethod
    def unique_rule_ids(cls, rules: list[Rule]) -> list[Rule]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("custom blueprint rule ids must be unique")
        return rules

    @field_validator("applicability")
    @classmethod
    def safe_applicability_paths(cls, value: ApplicabilitySpec) -> ApplicabilitySpec:
        _validate_patterns(value.files_any)
        return value


def _validate_patterns(patterns: list[str]) -> None:
    for pattern in patterns:
        path = Path(pattern)
        if path.is_absolute() or ".." in path.parts or "\x00" in pattern:
            raise ValueError("custom blueprint paths must stay within the project")


def _matches_any(rels: list[str], patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for rel in rels for pattern in patterns)


def _to_blueprint(spec: CustomBlueprintSpec, root: Path, by_rel: dict[str, Path]) -> Blueprint:
    rels = list(by_rel)

    def applicable(facts: ProjectFacts) -> bool:
        checks = []
        if spec.applicability.languages_any:
            checks.append(bool(set(spec.applicability.languages_any) & set(facts.languages)))
        if spec.applicability.project_types_any:
            checks.append(
                bool(set(spec.applicability.project_types_any) & set(facts.project_types))
            )
        if spec.applicability.files_any:
            checks.append(_matches_any(rels, spec.applicability.files_any))
        return all(checks) if checks else True

    def check(_root: Path, _facts: ProjectFacts) -> list[Finding]:
        findings: list[Finding] = []
        for rule in spec.rules:
            if isinstance(rule, RequiredFilesRule):
                _validate_patterns(rule.paths_any)
                if not _matches_any(rels, rule.paths_any):
                    findings.append(_custom_finding(spec.name, rule))
                continue
            _validate_patterns(rule.files)
            for rel, path in by_rel.items():
                if not any(fnmatch.fnmatch(rel, pattern) for pattern in rule.files):
                    continue
                try:
                    text = read_text_bounded(path, 512_000, root=root, errors="ignore")
                except (OSError, ValueError):
                    continue
                if rule.literal in text:
                    finding = _custom_finding(spec.name, rule)
                    finding.file = rel
                    findings.append(finding)
        return findings

    return Blueprint(
        name=spec.name,
        description=spec.description,
        applicability=applicable,
        check=check,
        model_review=False,
        not_applicable_reason="custom declarative applicability did not match",
    )


def _custom_finding(name: str, rule: RuleBase) -> Finding:
    priorities: dict[Severity, Priority] = {
        "critical": "P0",
        "high": "P1",
        "medium": "P2",
        "low": "P3",
        "info": "P3",
    }
    return Finding(
        blueprint=name,
        category=rule.id,
        rule_id=f"{name}/{rule.id}",
        source="blueprint-ai:declarative",
        sources=["blueprint-ai:declarative"],
        severity=rule.severity,
        priority=priorities[rule.severity],
        message=rule.message,
        recommendation=rule.recommendation,
    )


def load_custom_blueprints(root: Path) -> dict[str, Blueprint]:
    directory = root / ".blueprint-ai" / "blueprints"
    if not directory.is_dir() or directory.is_symlink():
        return {}
    loaded: dict[str, Blueprint] = {}
    paths = list(
        itertools.islice(itertools.chain(directory.glob("*.yml"), directory.glob("*.yaml")), 129)
    )
    if len(paths) > 128:
        raise ValueError("at most 128 project-local blueprints may be loaded")
    files, _ = iter_project_files(root)
    by_rel = {path.relative_to(root).as_posix(): path for path in files}
    for path in sorted(paths):
        payload = load_yaml_mapping(path, root, label=path.relative_to(root).as_posix())
        spec = CustomBlueprintSpec.model_validate(payload)
        if spec.name in loaded:
            raise ValueError(f"duplicate custom blueprint: {spec.name}")
        loaded[spec.name] = _to_blueprint(spec, root, by_rel)
    return loaded


def custom_blueprint_schema() -> dict:
    return CustomBlueprintSpec.model_json_schema()
