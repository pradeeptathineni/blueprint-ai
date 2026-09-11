from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

Severity = Literal["critical", "high", "medium", "low", "info"]
Priority = Literal["P0", "P1", "P2", "P3"]
Provenance = Literal["deterministic", "model"]
ResultStatus = Literal["passed", "findings", "not_applicable", "tool_missing", "partial", "error"]


class FileRange(BaseModel):
    start_line: int | None = None
    start_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


class Remediation(BaseModel):
    kind: str
    target: str
    safe: bool = False
    description: str


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint: str
    category: str
    source: str
    provenance: Provenance = "deterministic"
    severity: Severity = "medium"
    priority: Priority | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    file: str | None = None
    range: FileRange | None = None
    evidence: list[str] = Field(default_factory=list)
    message: str
    recommendation: str
    remediation: Remediation | None = None
    verification: str = "rerun blueprint"

    @computed_field
    @property
    def fingerprint(self) -> str:
        material = {
            "blueprint": self.blueprint,
            "category": self.category,
            "file": self.file,
            "message": " ".join(self.message.lower().split()),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


class ProjectFacts(BaseModel):
    path: str
    name: str
    is_git: bool = False
    git_branch: str | None = None
    git_dirty: bool = False
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    ci: list[str] = Field(default_factory=list)
    iac: list[str] = Field(default_factory=list)
    containers: list[str] = Field(default_factory=list)
    cloud_hints: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    ai_context_files: list[str] = Field(default_factory=list)
    project_types: list[str] = Field(default_factory=list)
    file_count: int = 0
    ignored_count: int = 0


class ToolStatus(BaseModel):
    name: str
    available: bool
    version: str | None = None
    detail: str | None = None


class BlueprintResult(BaseModel):
    blueprint: str
    status: ResultStatus
    findings: list[Finding] = Field(default_factory=list)
    tools: list[ToolStatus] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RunContext(BaseModel):
    root: Path
    profile: str = "default"
    selected_blueprints: list[str] = Field(default_factory=list)
    model_mode: Literal["auto", "off", "on"] = "auto"
    model_budget: int = Field(default=12_000, ge=512)
    output_mode: Literal["human", "json"] = "human"
    config: dict[str, Any] = Field(default_factory=dict)
    cache_dir: Path | None = None


class RunReport(BaseModel):
    facts: ProjectFacts
    profile: str
    results: list[BlueprintResult]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def findings(self) -> list[Finding]:
        return [finding for result in self.results for finding in result.findings]
