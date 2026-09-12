"""Shared, evidence-bearing project semantics for review and composition.

Paths are repository relative. Unknown roles remain unknown; declarations never grant
execution authority. The graph is an inventory, not a compiler or deployment plan.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Scope = Literal[
    "runtime",
    "development",
    "test",
    "fixture",
    "example",
    "generated",
    "vendor",
    "migration",
    "benchmark",
    "fuzz",
    "documentation",
    "build-image",
    "test-image",
    "runtime-image",
    "reusable-module",
    "remote-module",
]
NON_RUNTIME = {"fixture", "example", "generated", "vendor", "migration", "benchmark", "fuzz"}


class Evidence(BaseModel):
    file: str
    kind: str
    detail: str


class Dependency(BaseModel):
    name: str
    ecosystem: str
    scope: Scope = "runtime"
    source: str
    requirement: str = ""


class Component(BaseModel):
    id: str
    root: str
    name: str | None = None
    roles: list[str] = Field(default_factory=list)
    scope: Scope = "runtime"
    lifecycle: str = "unknown"
    languages: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_manager: str | None = None
    workspace_patterns: list[str] = Field(default_factory=list)
    scripts: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class Relationship(BaseModel):
    source: str
    target: str
    kind: str
    evidence: Evidence
    external: bool = False


class VerificationNode(BaseModel):
    component: str
    kind: str
    evidence: list[str]
    command: list[str] = Field(default_factory=list)
    requires_project_trust: bool = True


class ProjectGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    components: list[Component] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    verification: list[VerificationNode] = Field(default_factory=list)
    file_scopes: dict[str, Scope] = Field(default_factory=dict)
    lifecycle: str = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @cached_property
    def by_root(self) -> dict[str, Component]:
        return {component.root: component for component in self.components}

    def owner(self, path: str) -> Component | None:
        # Walk ancestors instead of scanning all components for every source file.
        by_root = self.by_root
        for parent in PurePosixPath(path).parents:
            if component := by_root.get(parent.as_posix()):
                return component
        return by_root.get(".")

    def scope(self, path: str) -> Scope:
        return self.file_scopes.get(path, path_scope(path))

    def source_files(self, component: Component | None = None) -> list[str]:
        return [
            path
            for path, scope in self.file_scopes.items()
            if scope not in NON_RUNTIME and (component is None or self.owner(path) == component)
        ]


def path_scope(path: str) -> Scope:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    name = PurePosixPath(path).name.lower()
    if ".terraform" in parts:
        return "remote-module"
    if parts & {"vendor", "node_modules", "third_party", "third-party", ".venv"}:
        return "vendor"
    if parts & {"fixtures", "fixture", "corpus", "testdata", "snapshots", "__snapshots__"}:
        return "fixture"
    if parts & {"generated", "__generated__"} or name.endswith(("_pb2.py", ".pb.go", ".g.cs")):
        return "generated"
    if parts & {"examples", "example", "tutorials", "tutorial"}:
        return "example"
    if parts & {"migrations", "alembic"}:
        return "migration"
    if parts & {"fuzz", "fuzz_targets", "fuzzers"}:
        return "fuzz"
    if parts & {"benchmarks", "benches", "benchmark"}:
        return "benchmark"
    if (
        parts & {"tests", "test", "spec", "__tests__"}
        or name.startswith("test_")
        or any(marker in name for marker in (".test.", ".spec.", "_test.", ".tftest."))
    ):
        return "test"
    if parts & {"docs", "doc", "documentation"} or name.endswith((".md", ".rst")):
        return "documentation"
    if parts & {"scripts", ".github", ".devcontainer"}:
        return "development"
    return "runtime"
