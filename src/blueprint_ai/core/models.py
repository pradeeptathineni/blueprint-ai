from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from blueprint_ai.core.project import ProjectGraph, Scope

Severity = Literal["critical", "high", "medium", "low", "info"]
Priority = Literal["P0", "P1", "P2", "P3"]
Provenance = Literal["deterministic", "model"]
ResultStatus = Literal["passed", "findings", "not_applicable", "tool_missing", "partial", "error"]
ApplicabilityState = Literal["applicable", "partial", "not_applicable"]
ToolOutcome = Literal["available", "passed", "finding", "tool_error", "tool_missing", "unsupported"]
Disposition = Literal["new", "baseline", "suppressed"]


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


class Suppression(BaseModel):
    reason: str
    expires: datetime | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint: str
    category: str
    rule_id: str | None = None
    source: str
    sources: list[str] = Field(default_factory=list)
    provenance: Provenance = "deterministic"
    severity: Severity = "medium"
    priority: Priority | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    file: str | None = None
    range: FileRange | None = None
    evidence: list[str] = Field(default_factory=list)
    tool_metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    message: str
    recommendation: str
    remediation: Remediation | None = None
    verification: str = "rerun blueprint"
    disposition: Disposition = "new"
    suppression: Suppression | None = None
    component: str | None = None
    scope: Scope = "runtime"
    advisory_aliases: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def fingerprint(self) -> str:
        issue = None
        for token in re.findall(
            r"\b(?:CVE-\d{4}-\d+|GHSA-[a-z0-9-]+|OSV-[a-z0-9-]+|CKV_[a-z0-9_-]+|[A-Z]\d{3,4})\b",
            self.message,
            re.IGNORECASE,
        ):
            issue = token.upper()
            break
        material = {
            "blueprint": self.blueprint,
            "category": self.category,
            "rule_id": self.rule_id or issue or self.category,
            "file": self.file,
            "line": self.range.start_line if self.range else None,
        }
        if self.category == "dependency-vulnerability":
            material.update(
                {
                    "component": self.component,
                    "scope": self.scope,
                    "package": self.tool_metadata.get("package"),
                    "ecosystem": self.tool_metadata.get("ecosystem"),
                    "installed_version": self.tool_metadata.get("installed_version"),
                }
            )
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


class ProjectFacts(BaseModel):
    graph: ProjectGraph = Field(default_factory=ProjectGraph)
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
    api_specs: list[str] = Field(default_factory=list)
    kubernetes: list[str] = Field(default_factory=list)
    migrations: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    observability: list[str] = Field(default_factory=list)
    test_capabilities: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    suggested_profiles: list[str] = Field(default_factory=list)
    project_types: list[str] = Field(default_factory=list)
    file_count: int = 0
    ignored_count: int = 0


class ToolStatus(BaseModel):
    name: str
    available: bool
    version: str | None = None
    detail: str | None = None
    outcome: ToolOutcome = "available"
    command: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    duration_ms: int | None = None
    output_truncated: bool = False
    network_required: bool = False
    recommended_version: str | None = None
    requires_project_trust: bool = False
    working_directory: str | None = None
    analysis_state: str | None = None


class Applicability(BaseModel):
    state: ApplicabilityState
    reason: str


class BlueprintResult(BaseModel):
    blueprint: str
    status: ResultStatus
    findings: list[Finding] = Field(default_factory=list)
    tools: list[ToolStatus] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    applicability: Applicability | None = None
    model_metrics: dict[str, int | float | str | None] = Field(default_factory=dict)
    assessment: str = "checks_only"
    checks_passed: list[str] = Field(default_factory=list)


class RunContext(BaseModel):
    root: Path
    profile: str = "default"
    profiles: list[str] = Field(default_factory=list)
    selected_blueprints: list[str] = Field(default_factory=list)
    model_mode: Literal["auto", "off", "on"] = "auto"
    model_budget: int = Field(default=12_000, ge=512)
    output_mode: Literal["human", "json", "markdown", "sarif", "junit"] = "human"
    changed_only: bool = False
    base_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    cache_dir: Path | None = None
    trust_project_executables: bool = False


class RunMetadata(BaseModel):
    schema_version: str = "1.0.0"
    blueprint_ai_version: str
    config_sha256: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    offline: bool = False
    model_mode: str
    model_provider: str | None = None
    model_name: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    tool_versions: dict[str, str | None] = Field(default_factory=dict)
    analyzer_commit: str | None = None
    analyzer_source_sha256: str | None = None


class RunReport(BaseModel):
    schema_version: str = "1.0.0"
    facts: ProjectFacts
    profile: str
    results: list[BlueprintResult]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: RunMetadata | None = None

    @property
    def findings(self) -> list[Finding]:
        return [finding for result in self.results for finding in result.findings]

    @property
    def active_findings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.disposition == "new"]

    @property
    def baseline_findings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.disposition == "baseline"]

    @property
    def suppressed_findings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.disposition == "suppressed"]
