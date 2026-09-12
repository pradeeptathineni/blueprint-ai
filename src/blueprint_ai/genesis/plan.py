"""Small capability resolver and trusted provider plans, without permutation templates."""

from __future__ import annotations

from blueprint_ai.core.project import Component
from blueprint_ai.genesis.capabilities import CAPABILITIES as CAPABILITIES
from blueprint_ai.genesis.capabilities import resolve_capabilities
from blueprint_ai.genesis.models import (
    ArtifactClaim,
    GenesisPlan,
    IntentSpec,
    Operation,
)
from blueprint_ai.naming import resolve_identity
from blueprint_ai.support import FAMILIES, PROVIDERS


def validate_claims(claims: list[ArtifactClaim]) -> None:
    owned: dict[tuple[str, str | None], str] = {}
    for claim in claims:
        key = (claim.path, claim.locator)
        if key in owned and owned[key] != claim.owner:
            raise ValueError(f"artifact conflict at {claim.path}: {owned[key]} and {claim.owner}")
        # A full-file claim conflicts with all semantic contributions from another owner.
        for (path, locator), owner in owned.items():
            if (
                path == claim.path
                and owner != claim.owner
                and (locator is None or claim.locator is None)
            ):
                raise ValueError(f"whole-file/semantic conflict at {path}")
        owned[key] = claim.owner


def plan_project(intent: IntentSpec) -> GenesisPlan:
    from blueprint_ai.genesis.families import NATIVE_FAMILIES, native_plan

    if intent.kind not in FAMILIES or not FAMILIES[intent.kind].initialize:
        raise ValueError("unsupported initialization family")
    if intent.kind in NATIVE_FAMILIES:
        return native_plan(intent)
    python = (
        intent.kind.startswith("python")
        or intent.kind == "full-stack"
        and intent.backend == "python"
    )
    identity = resolve_identity(
        intent.name,
        "python"
        if python
        else "npm"
        if intent.kind != "repository" and intent.kind != "openapi"
        else "repository",
    )
    components: list[Component] = []
    requested = ["repository"]
    if python:
        requested.append("python")
        component_root = "backend" if intent.kind == "full-stack" else "."
        components.append(
            Component(
                id=component_root,
                root=component_root,
                name=identity.package,
                languages=["Python"],
                roles=["backend", "api", "service"]
                if intent.kind in {"python-api", "full-stack"}
                else ["cli"]
                if intent.kind == "python-cli"
                else ["library"],
                package_manager="uv",
            )
        )
    if (
        intent.kind in {"typescript-library", "typescript-cli", "node-api"}
        or intent.kind == "full-stack"
        and intent.backend == "node"
    ):
        requested.append("typescript")
        component_root = "backend" if intent.kind == "full-stack" else "."
        components.append(
            Component(
                id=component_root,
                root=component_root,
                name=identity.package,
                languages=["TypeScript"],
                roles=["backend", "api", "service"]
                if intent.kind in {"node-api", "full-stack"}
                else ["cli"]
                if intent.kind == "typescript-cli"
                else ["library"],
                package_manager="npm",
            )
        )
    if intent.kind in {"react", "full-stack"}:
        requested.append("react")
        component_root = "frontend" if intent.kind == "full-stack" else "."
        components.append(
            Component(
                id=component_root,
                root=component_root,
                name=identity.package,
                languages=["TypeScript"],
                roles=["frontend"],
                package_manager="npm",
            )
        )
    if intent.kind in {"node-api", "python-api", "full-stack", "openapi"}:
        requested.append("api")
    for enabled, capability in (
        (intent.container, "container"),
        (intent.ci or intent.maturity == "team", "ci"),
        (intent.devcontainer, "devcontainer"),
        (intent.api_client, "api-client"),
    ):
        if enabled:
            requested.append(capability)
    capabilities = resolve_capabilities(requested)
    operations: list[Operation] = []
    claims = [
        ArtifactClaim(path="README.md", owner="repository"),
        ArtifactClaim(path=".editorconfig", owner="repository"),
    ]
    for component in components:
        target = component.root
        prefix = "" if target == "." else target + "/"
        if "Python" in component.languages:
            provider = "uv"
            command = [
                "uv",
                "init",
                "--lib",
                "--name",
                identity.package,
                "--build-backend",
                "hatch",
                "--vcs",
                "none",
                "--python",
                "3.12",
                "--no-workspace",
                "--no-config",
                "--offline",
                ".",
            ]
            expected = ["pyproject.toml", "src/" + identity.module + "/__init__.py"]
        elif "frontend" in component.roles:
            provider = "vite"
            command = [
                "npm",
                "exec",
                "--yes",
                "--ignore-scripts",
                "--package=create-vite@9.2.1",
                "--",
                "create-vite",
                ".",
                "--template",
                "react-ts",
                "--no-interactive",
                "--no-install",
            ]
            expected = ["package.json", "src/App.tsx"]
        else:
            provider = "npm"
            command = ["npm", "init", "--yes", "--ignore-scripts"]
            expected = ["package.json"]
        operations.append(
            Operation(
                id=f"initialize:{target}",
                provider=provider,
                component=target,
                action="initialize",
                command=command,
                network=PROVIDERS[provider].network,
                requires_execution=True,
                expected_files=expected,
            )
        )
        claims.append(
            ArtifactClaim(path=prefix + expected[0], owner=f"component:{target}", mode="native-cli")
        )
    operations.append(Operation(id="strengthen", provider="builtin", action="strengthen"))
    for component in components:
        if "Python" in component.languages:
            commands = [
                (["uv", "sync"], True),
                (["uv", "run", "--no-sync", "ruff", "format", "."], False),
                (["uv", "run", "--no-sync", "ruff", "check", "--fix", "."], False),
                (["uv", "run", "--no-sync", "ruff", "check", "."], False),
                (["uv", "run", "--no-sync", "ruff", "format", "--check", "."], False),
                (["uv", "run", "--no-sync", "mypy", "src"], False),
                (["uv", "run", "--no-sync", "pytest", "-q"], False),
                (["uv", "build", "--no-sources"], True),
            ]
            provider = "uv"
        else:
            commands = [
                (
                    [
                        "npm",
                        "install",
                        "--include=optional",
                        "--ignore-scripts",
                        "--no-fund",
                        "--no-audit",
                    ],
                    True,
                ),
                (["npm", "run", "lint"], False),
                (["npm", "run", "build"], False),
                (["npm", "test"], False),
                (["npm", "pack", "--dry-run", "--ignore-scripts"], False),
            ]
            provider = "npm"
        for index, (command, network) in enumerate(commands):
            operations.append(
                Operation(
                    id=f"verify:{component.root}:{index}",
                    provider=provider,
                    component=component.root,
                    action="strengthen"
                    if "--fix" in command or ("format" in command and "--check" not in command)
                    else "verify",
                    command=command,
                    network=network,
                    requires_execution=True,
                )
            )
    if "api" in capabilities:
        operations.append(Operation(id="verify:openapi", provider="builtin", action="verify"))
    if intent.api_client:
        operations.insert(
            next(i for i, op in enumerate(operations) if op.action == "verify"),
            Operation(
                id="generate:api-client",
                provider="openapi-typescript",
                action="initialize",
                command=[
                    "npm",
                    "exec",
                    "--yes",
                    "--ignore-scripts",
                    "--package=openapi-typescript@7.13.0",
                    "--",
                    "openapi-typescript",
                    "backend/openapi.json",
                    "-o",
                    "frontend/src/generated/api.d.ts",
                ],
                network=True,
                requires_execution=True,
                expected_files=["frontend/src/generated/api.d.ts"],
            ),
        )
    if intent.container:
        service = next(c for c in components if "api" in c.roles)
        operations.append(
            Operation(
                id="verify:container",
                provider="docker",
                component=service.root,
                action="verify",
                command=["docker", "build", "--quiet", "."],
                network=True,
                requires_execution=True,
            )
        )
    if intent.devcontainer:
        operations.append(
            Operation(
                id="verify:devcontainer",
                provider="docker",
                component=".devcontainer",
                action="verify",
                command=["docker", "build", "--quiet", "."],
                network=True,
                requires_execution=True,
            )
        )
    validate_claims(claims)
    return GenesisPlan(
        intent=intent,
        identity=identity,
        capabilities=capabilities,
        components=components,
        providers=[PROVIDERS[name] for name in sorted({op.provider for op in operations})],
        operations=operations,
        claims=claims,
        decisions=[
            "Native initializers own scaffolding; capabilities contribute quality and contracts.",
            "Lifecycle scripts are disabled; verification executes staged generated code.",
            "No remote repository publication, template hooks, cloud provisioning, or model calls.",
            "Unavailable/unauthorized checks remain partial; source publication is atomic.",
        ],
    )
