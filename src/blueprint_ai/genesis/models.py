from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blueprint_ai.core.project import Component
from blueprint_ai.naming import IdentityMap

Kind = Literal[
    "repository",
    "python-library",
    "python-cli",
    "python-api",
    "typescript-library",
    "typescript-cli",
    "node-api",
    "react",
    "full-stack",
    "openapi",
]


class IntentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=200)
    kind: Kind = "repository"
    backend: Literal["python", "node"] = "python"
    maturity: Literal["starter", "team"] = "starter"
    container: bool = False
    ci: bool = False
    devcontainer: bool = False
    api_client: bool = False
    license: Literal["MIT", "UNLICENSED"] = "UNLICENSED"

    @model_validator(mode="after")
    def compatible(self) -> IntentSpec:
        if self.container and self.kind not in {"node-api", "python-api", "full-stack"}:
            raise ValueError("container requires a Python or Node API component")
        if self.api_client and self.kind != "full-stack":
            raise ValueError("generated API client requires full-stack composition")
        if self.backend != "python" and self.kind != "full-stack":
            raise ValueError("backend is only configurable for full-stack intent")
        return self


class Capability(BaseModel):
    id: str
    requires: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    provider: str
    description: str


class Provider(BaseModel):
    id: str
    executable: str | None = None
    supported_versions: str | None = None
    package: str | None = None
    version: str | None = None
    registry_integrity: str | None = None
    source: str
    license: str
    network: bool = False
    executes_code: bool = False
    reviewed: str = "2026-09-12"


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    provider: str
    component: str = "."
    action: Literal["initialize", "strengthen", "verify"]
    command: list[str] = Field(default_factory=list)
    network: bool = False
    requires_execution: bool = False
    expected_files: list[str] = Field(default_factory=list)


class ArtifactClaim(BaseModel):
    path: str
    owner: str
    mode: Literal["create-only", "contribute", "native-cli"] = "create-only"
    locator: str | None = None


class GenesisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    intent: IntentSpec
    identity: IdentityMap
    capabilities: list[str]
    components: list[Component]
    providers: list[Provider]
    operations: list[Operation]
    claims: list[ArtifactClaim]
    decisions: list[str]

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


class OperationResult(BaseModel):
    id: str
    status: Literal["passed", "failed", "unavailable", "unauthorized", "partial"]
    command: list[str] = Field(default_factory=list)
    component: str = "."
    version: str | None = None
    executable_sha256: str | None = None
    evidence_sha256: str | None = None
    artifact_id: str | None = None
    duration_ms: int = 0
    detail: str = ""


class GenesisResult(BaseModel):
    status: Literal["created", "verified", "partial", "failed"]
    destination: str
    plan_sha256: str
    operations: list[OperationResult] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    review_summary: dict[str, str] = Field(default_factory=dict)
    model_calls: int = 0
    duration_ms: int = 0
    detail: str = ""
