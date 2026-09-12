"""Compose canonical family and kit metadata with shared capability resolution."""

from __future__ import annotations

from blueprint_ai.genesis.models import Capability
from blueprint_ai.remediation import KITS
from blueprint_ai.support import FAMILIES

CAPABILITIES = {
    "repository": Capability(
        id="repository", provider="builtin", description="Repository baseline"
    ),
    "python": Capability(
        id="python", provider="uv", requires=["repository"], description="Python package"
    ),
    "typescript": Capability(
        id="typescript", provider="npm", requires=["repository"], description="TypeScript package"
    ),
    "react": Capability(
        id="react", provider="vite", requires=["repository"], description="React TypeScript SPA"
    ),
    "api": Capability(
        id="api", provider="builtin", requires=["repository"], description="Health API and contract"
    ),
    "api-client": Capability(
        id="api-client",
        provider="openapi-typescript",
        requires=["api", "react"],
        description="Generated OpenAPI types",
    ),
    "container": Capability(
        id="container", provider="builtin", requires=["api"], description="Service container"
    ),
    "ci": Capability(
        id="ci",
        provider="builtin",
        requires=["repository"],
        description="GitHub verification workflow",
    ),
    "devcontainer": Capability(
        id="devcontainer",
        provider="builtin",
        requires=["repository"],
        description="Development container configuration",
    ),
}
# Existing kits remain the authority for reusable augmentation assets.
for _name, _kit in KITS.items():
    CAPABILITIES["kit:" + _name] = Capability(
        id="kit:" + _name,
        provider="builtin",
        requires=["repository"],
        description=f"Existing {_name} capability kit v{_kit.version}",
    )

for _family in FAMILIES.values():
    if _family.initialize:
        CAPABILITIES["family:" + _family.id] = Capability(
            id="family:" + _family.id,
            provider=_family.provider,
            requires=["repository"],
            description=_family.language + " " + _family.id,
        )


def resolve_capabilities(
    requested: list[str], catalog: dict[str, Capability] | None = None
) -> list[str]:
    catalog = catalog or CAPABILITIES
    ordered: list[str] = []
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in ordered:
            return
        if name not in catalog:
            raise ValueError(f"unknown capability: {name}")
        if name in visiting:
            raise ValueError("capability cycle: " + " -> ".join([*visiting, name]))
        visiting.append(name)
        for requirement in catalog[name].requires:
            visit(requirement)
        visiting.pop()
        ordered.append(name)

    for name in requested:
        visit(name)
    for name in ordered:
        conflicts = set(catalog[name].conflicts) & set(ordered)
        if conflicts:
            raise ValueError(f"{name} conflicts with {', '.join(sorted(conflicts))}")
    return ordered
