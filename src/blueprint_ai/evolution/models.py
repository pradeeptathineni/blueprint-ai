"""Serializable contracts for deterministic-first project evolution."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
EvolutionStatus = Literal[
    "dry_run", "verified", "rolled_back", "partial", "accepted", "failed", "noop"
]
PipelineStepKind = Literal[
    "dependency-acquisition",
    "native-command",
    "established-codemod",
    "builtin-edit",
    "postcondition",
    "manual-boundary",
    "residual-boundary",
]
PipelineStepStatus = Literal[
    "ready", "blocked", "deferred", "noop", "manual", "failed-precondition"
]
PostconditionKind = Literal[
    "forbidden-pattern",
    "required-pattern",
    "exact-value",
    "command",
    "no-manual-markers",
    "no-new-findings",
    "path-present",
    "path-absent",
]


class VerificationRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    command: list[str] = Field(default_factory=list)
    component: str = "."
    required: bool = True
    requires_project_trust: bool = False
    isolated_copy: bool = False
    detail: str


class TypedPostcondition(BaseModel):
    """Small deterministic vocabulary for migration-specific acceptance checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9./_-]{2,119}$")
    kind: PostconditionKind
    paths: list[str] = Field(default_factory=list)
    pattern: str | None = None
    selector: str | None = None
    expected: str | None = None
    command: list[str] = Field(default_factory=list)
    component: str = "."
    required: bool = True
    detail: str

    @model_validator(mode="after")
    def kind_contract(self) -> TypedPostcondition:
        if self.kind in {"forbidden-pattern", "required-pattern"}:
            if not self.paths or not self.pattern:
                raise ValueError(f"{self.kind} requires paths and a pattern")
        elif self.kind == "exact-value":
            if not self.paths or not self.selector or self.expected is None:
                raise ValueError("exact-value requires paths, a selector, and an expected value")
        elif self.kind in {"command", "no-new-findings"} and not self.command:
            raise ValueError(f"{self.kind} requires a deterministic command")
        elif self.kind in {"path-present", "path-absent"} and not self.paths:
            raise ValueError(f"{self.kind} requires paths")
        return self


class AuthoritativeToolContract(BaseModel):
    """Bounded supply-chain and execution contract for an authoritative migration tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    provider: str
    host_executable: str
    container_executable: str
    tool_version: str
    runner_package: str | None = None
    runner_version: str | None = None
    runner_integrity: str | None = None
    recipe: str
    recipe_version: str
    authoritative_source: str
    tool_license: str
    recipe_license: str
    distribution: Literal["invoke", "redistribute"] = "invoke"
    redistribution_permitted: bool = False
    image: str | None = None
    image_digest: str | None = None
    network: Literal["none", "acquisition-only", "required"] = "none"
    allowed_destinations: list[str] = Field(default_factory=list)
    credentials: list[str] = Field(default_factory=list)
    mounts: list[Literal["cache", "config", "ca"]] = Field(default_factory=list)
    noninteractive_flags: list[str] = Field(default_factory=list)
    preview_supported: bool
    success_exit_codes: list[int] = Field(default_factory=lambda: [0])
    expected_write_scopes: list[str]
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    memory_mb: int = Field(default=1024, ge=64, le=16384)
    cpus: float = Field(default=2, gt=0, le=16)
    pids: int = Field(default=128, ge=16, le=1024)
    scratch_mb: int = Field(default=256, ge=16, le=8192)
    file_size_mb: int = Field(default=128, ge=1, le=1024)
    output_bytes: int = Field(default=4_000_000, ge=1024, le=16_000_000)
    postconditions: list[TypedPostcondition] = Field(default_factory=list)
    verification: list[VerificationRequirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded_contract(self) -> AuthoritativeToolContract:
        if not self.expected_write_scopes:
            raise ValueError("authoritative tools require a non-empty expected write scope")
        if self.image_digest and not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest):
            raise ValueError("tool image digest must be a SHA-256 identity")
        if self.network == "none" and self.allowed_destinations:
            raise ValueError("network-disabled tools cannot declare network destinations")
        if self.network == "required" and not self.allowed_destinations:
            raise ValueError("network-required tools must document expected destinations")
        if self.distribution == "redistribute" and not self.redistribution_permitted:
            raise ValueError("redistribution must be explicitly permitted")
        return self


class ResidualContract(BaseModel):
    """Sealed schema boundary for a future agent; no production backend consumes it."""

    model_config = ConfigDict(extra="forbid")

    desired_state: list[str] = Field(min_length=1)
    completed_steps: list[str]
    failures: list[str]
    permitted_paths: list[str] = Field(min_length=1)
    constraints: list[str]
    prohibited_scope: list[str]
    acceptance_commands: list[list[str]]
    postconditions: list[TypedPostcondition]
    network_destinations: list[str] = Field(default_factory=list)
    credential_requirements: list[str] = Field(default_factory=list)
    command_allowlist: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(ge=1, le=3600)
    step_limit: int = Field(ge=1, le=100)
    token_limit: int = Field(ge=1, le=1_000_000)
    rollback_checkpoint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


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
    allowed_targets: list[str] = Field(default_factory=list)
    target_required: bool = False
    tool_contract: AuthoritativeToolContract | None = None
    postconditions: list[TypedPostcondition] = Field(default_factory=list)
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
    operation: str = "transform"
    operation_target: str | None = None
    kind: PipelineStepKind = "builtin-edit"
    desired_target: str | None = None
    sequence: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)
    mechanism: Mechanism
    provider: str
    tool: str | None = None
    tool_version: str | None = None
    preview_command: list[str] = Field(default_factory=list)
    apply_command: list[str] = Field(default_factory=list)
    recipe_version: str
    authoritative_source: str
    files: list[str]
    preconditions: list[Precondition]
    verification: list[VerificationRequirement]
    postconditions: list[TypedPostcondition] = Field(default_factory=list)
    tool_contract: AuthoritativeToolContract | None = None
    execution_network: Literal["none", "required"] = "none"
    image_identities: dict[str, str] = Field(default_factory=dict)
    ephemeral_paths: list[str] = Field(default_factory=list)
    manual_completion_paths: list[str] = Field(default_factory=list)
    rediscover: bool = False
    mutates: bool = True
    reversible: bool
    limitations: list[str] = Field(default_factory=list)
    model_responsibility: str = "none"
    agent_responsibility: str = "none"
    status: PipelineStepStatus


class EvolutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0.0"
    blueprint_ai_version: str
    project_root: str = "."
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_targets: list[str]
    target_versions: dict[str, str] = Field(default_factory=dict)
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
