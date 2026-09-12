"""Serializable contracts for deterministic-first project evolution."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mechanism = Literal[
    "official-native",
    "established-codemod",
    "builtin-structural",
    "model-planning",
    "agent-implementation",
    "manual",
]
Maturity = Literal["supported", "experimental", "partial", "deferred"]
PlanStatus = Literal["inspection", "ready", "blocked", "noop"]
EvolutionStatus = Literal["dry_run", "verified", "rolled_back", "partial", "failed", "noop"]


class VerificationRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    command: list[str] = Field(default_factory=list)
    component: str = "."
    required: bool = True
    requires_project_trust: bool = False
    detail: str


class TransformationSpec(BaseModel):
    """Canonical user-facing migration recipe metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9./-]{2,99}$")
    category: str
    current_state: str
    desired_state: str
    applicability: list[str]
    lifecycle: list[str] = Field(default_factory=list)
    provider: str
    tool: str | None = None
    tool_version: str | None = None
    recipe_version: str
    authoritative_source: str
    license: str
    network_required: bool = False
    sandbox_requirements: str
    preconditions: list[str]
    conflicts: list[str] = Field(default_factory=list)
    expected_paths: list[str]
    dry_run: bool
    reversible: bool
    irreversible_boundary: str | None = None
    verification: list[VerificationRequirement]
    model_allowed: bool = False
    agent_allowed: bool = False
    maturity: Maturity
    mechanism: Mechanism
    implementation: Literal["builtin", "command", "manual"]
    decision: Literal[
        "integrate",
        "wrap",
        "adapt",
        "source",
        "inspire",
        "native",
        "model-assist",
        "agent-assist",
        "defer",
        "reject",
    ]
    limitations: list[str] = Field(default_factory=list)
    reviewed: str


class Precondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["passed", "failed", "unknown"]
    detail: str


class ProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    lifecycle: str
    git_branch: str | None = None
    git_head: str | None = Field(default=None, pattern=r"^[a-f0-9]{40,64}$")
    git_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    git_clean: bool
    languages: list[str]
    frameworks: list[str]
    package_managers: list[str]
    versions: dict[str, str] = Field(default_factory=dict)
    project_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvolutionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    recipe_id: str
    sequence: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)
    mechanism: Mechanism
    provider: str
    tool: str | None = None
    tool_version: str | None = None
    recipe_version: str
    authoritative_source: str
    files: list[str]
    preconditions: list[Precondition]
    verification: list[VerificationRequirement]
    reversible: bool
    limitations: list[str] = Field(default_factory=list)
    model_responsibility: str = "none"
    agent_responsibility: str = "none"
    status: Literal["ready", "blocked", "noop", "manual"]


class EvolutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    blueprint_ai_version: str
    project_root: str = "."
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_targets: list[str]
    current_state: ProjectState
    desired_state: list[str]
    candidates: list[str]
    steps: list[EvolutionStep]
    risks: list[str] = Field(default_factory=list)
    manual_boundaries: list[str] = Field(default_factory=list)
    status: PlanStatus
    model_metrics: dict[str, int] = Field(
        default_factory=lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    )
    agent_metrics: dict[str, int] = Field(
        default_factory=lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    )
    metrics: dict[str, int | float] = Field(default_factory=dict)
    plan_sha256: str = ""

    def digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"plan_sha256"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def seal(self) -> EvolutionPlan:
        return self.model_copy(update={"plan_sha256": self.digest()})

    def validate_digest(self) -> None:
        if not self.plan_sha256 or self.plan_sha256 != self.digest():
            raise ValueError("evolution plan checksum does not match its contents")


class EvolutionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    recipe_id: str
    status: Literal["changed", "created", "deleted", "rolled_back", "conflicted"]
    before_sha256: str | None = None
    after_sha256: str | None = None
    detail: str


class EvolutionVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["passed", "failed", "skipped", "tool_missing", "tool_error"]
    command: list[str] = Field(default_factory=list)
    detail: str
    duration_ms: int = 0
    sandbox: dict | None = None


class ReviewDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_findings: int | None = None
    after_findings: int | None = None
    introduced_fingerprints: list[str] = Field(default_factory=list)
    resolved_fingerprints: list[str] = Field(default_factory=list)
    before_incomplete: list[str] = Field(default_factory=list)
    after_incomplete: list[str] = Field(default_factory=list)
    status: Literal["passed", "partial", "regressed", "skipped", "failed"] = "skipped"
    detail: str = "Blueprint review not run"


class EvolutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    operation_id: str | None = None
    status: EvolutionStatus
    plan_sha256: str
    before: ProjectState | None = None
    plan: EvolutionPlan | None = None
    changes: list[EvolutionChange] = Field(default_factory=list)
    diffs: dict[str, str] = Field(default_factory=dict)
    verifications: list[EvolutionVerification] = Field(default_factory=list)
    review: ReviewDelta = Field(default_factory=ReviewDelta)
    after: ProjectState | None = None
    manifest_path: str | None = None
    rollback_command: list[str] = Field(default_factory=list)
    provenance: list[dict[str, str]] = Field(default_factory=list)
    metrics: dict[str, int | float] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
