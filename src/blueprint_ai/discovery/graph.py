"""Deterministic project inventory. Never import or execute target code."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import tomllib
import warnings
from pathlib import Path, PurePosixPath
from typing import Any, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from blueprint_ai.config import load_yaml_mapping
from blueprint_ai.core.project import (
    NON_RUNTIME,
    Component,
    Dependency,
    Evidence,
    ProjectGraph,
    Relationship,
    Scope,
    VerificationNode,
    path_scope,
)
from blueprint_ai.safety import read_text_bounded

MANIFEST_LANGUAGES = {
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "package.json": "JavaScript",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "build.gradle.kts": "Kotlin",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
}
FRAMEWORKS = {
    "react",
    "next",
    "vue",
    "svelte",
    "express",
    "fastify",
    "fastapi",
    "flask",
    "django",
    "typer",
    "pytest",
    "vitest",
    "jest",
}
AI = {
    "openai",
    "anthropic",
    "langchain",
    "langchain-core",
    "llama-index",
    "litellm",
    "pydantic-ai",
    "transformers",
    "ollama",
}
AGENTS = {"openai-agents", "autogen-agentchat", "pyautogen", "crewai", "langgraph"}
DATA = {"apache-airflow", "dagster", "prefect", "pyspark"}


def _read(root: Path, rel: str, limit: int = 200_000) -> str:
    return read_text_bounded(root / rel, limit, root=root, errors="replace")


def _scripts(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(command, str) for key, command in value.items()
    ):
        raise ValueError("scripts must map names to command strings")
    return value


def _members(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(member, str) for member in value):
        raise ValueError("workspace members must be a list of strings")
    return value


def _requirements(component: Component, values: Any, rel: str, scope: str) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            req = Requirement(value.split(" #", 1)[0].rstrip().removesuffix("\\").rstrip())
        except InvalidRequirement:
            continue
        component.dependencies.append(
            Dependency(
                name=canonicalize_name(req.name),
                ecosystem="PyPI",
                source=rel,
                scope=cast(Scope, scope),
                requirement=str(req.specifier),
            )
        )


def _manifest(root: Path, rel: str, c: Component) -> None:
    name = Path(rel).name
    text = _read(root, rel, 2_000_000)
    if name == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project", {})
        c.name = project.get("name") or c.name
        c.scripts.update(_scripts(project.get("scripts", {})))
        if project.get("scripts"):
            c.roles.append("cli")
        _requirements(c, project.get("dependencies", []), rel, "runtime")
        for values in project.get("optional-dependencies", {}).values():
            _requirements(c, values, rel, "development")
        for values in data.get("dependency-groups", {}).values():
            _requirements(c, values, rel, "development")
        tool = data.get("tool", {})
        poetry = tool.get("poetry", {})
        c.name = c.name or poetry.get("name")
        for dep, requirement in poetry.get("dependencies", {}).items():
            if dep != "python":
                c.dependencies.append(
                    Dependency(
                        name=canonicalize_name(dep),
                        ecosystem="PyPI",
                        source=rel,
                        requirement=str(requirement),
                    )
                )
        c.workspace_patterns.extend(
            _members(tool.get("uv", {}).get("workspace", {}).get("members", []))
        )
        if "build-system" in data:
            c.lifecycle = "library-framework"
            c.roles.append("library")
        c.evidence.append(Evidence(file=rel, kind="manifest", detail="Python project metadata"))
    elif name == "requirements.txt":
        _requirements(c, text.splitlines(), rel, "runtime")
    elif name == "package.json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("package manifest must be an object")
        c.name = data.get("name") or c.name
        c.scripts.update(_scripts(data.get("scripts", {})))
        for field, scope in (
            ("dependencies", "runtime"),
            ("devDependencies", "development"),
            ("peerDependencies", "runtime"),
            ("optionalDependencies", "runtime"),
        ):
            for dep, version in data.get(field, {}).items():
                c.dependencies.append(
                    Dependency(
                        name=dep,
                        ecosystem="npm",
                        source=rel,
                        scope=cast(Scope, scope),
                        requirement=str(version),
                    )
                )
        if data.get("bin"):
            c.roles.append("cli")
        if data.get("exports") or data.get("main"):
            c.roles.append("library")
        workspaces = data.get("workspaces", [])
        c.workspace_patterns.extend(
            _members(workspaces.get("packages", []) if isinstance(workspaces, dict) else workspaces)
        )
        if manager := data.get("packageManager"):
            c.package_manager = str(manager).split("@", 1)[0]
        c.evidence.append(Evidence(file=rel, kind="manifest", detail="npm package metadata"))
    elif name == "Cargo.toml":
        data = tomllib.loads(text)
        c.name = data.get("package", {}).get("name") or c.name
        c.workspace_patterns.extend(_members(data.get("workspace", {}).get("members", [])))
        for field, scope in (
            ("dependencies", "runtime"),
            ("dev-dependencies", "development"),
            ("build-dependencies", "development"),
        ):
            for dep, version in data.get(field, {}).items():
                c.dependencies.append(
                    Dependency(
                        name=dep,
                        ecosystem="crates.io",
                        source=rel,
                        scope=cast(Scope, scope),
                        requirement=str(version),
                    )
                )
        if data.get("lib") or (root / c.root / "src/lib.rs").is_file():
            c.roles.append("library")
        if data.get("bin") or (root / c.root / "src/main.rs").is_file():
            c.roles.append("cli")
        if data.get("bench"):
            c.evidence.append(Evidence(file=rel, kind="benchmark", detail="Cargo bench targets"))
        if data.get("package", {}).get("metadata", {}).get("cargo-fuzz"):
            c.evidence.append(Evidence(file=rel, kind="fuzz", detail="cargo-fuzz targets"))
    elif name == "go.mod":
        match = re.search(r"(?m)^module\s+(\S+)", text)
        c.name = match.group(1) if match else None
        for dep, version in re.findall(
            r"(?m)^\s*(?:require\s+)?([\w.-]+/[^\s]+)\s+(v[^\s]+)", text
        ):
            c.dependencies.append(
                Dependency(name=dep, ecosystem="Go", source=rel, requirement=version)
            )
    elif name == "composer.json":
        data = json.loads(text)
        c.name = data.get("name")
    # JVM/Ruby configuration is recorded, never evaluated as executable build scripts.


def _hcl(root: Path, rel: str, c: Component, graph: ProjectGraph) -> None:
    import hcl2

    data = hcl2.loads(
        _read(root, rel),
        serialization_options=hcl2.utils.SerializationOptions(strip_string_quotes=True),
    )
    for block in data.get("module", []):
        for label, attrs in block.items():
            if label.startswith("__") or not isinstance(attrs, dict):
                continue
            source = str(attrs.get("source", ""))
            external = not source.startswith(("./", "../"))
            c.dependencies.append(
                Dependency(
                    name=source,
                    ecosystem="Terraform",
                    source=rel,
                    requirement=str(attrs.get("version", "")),
                )
            )
            target = source if external else (PurePosixPath(c.root) / source).as_posix()
            graph.relationships.append(
                Relationship(
                    source=c.id,
                    target=target,
                    kind="module-call",
                    external=external,
                    evidence=Evidence(file=rel, kind="hcl", detail=f"module {label}"),
                )
            )
    for block in data.get("terraform", []):
        if block.get("backend") or block.get("cloud"):
            c.roles.append("deployed-iac")
        for provider_block in block.get("required_providers", []):
            for provider, attrs in provider_block.items():
                if provider.startswith("__"):
                    continue
                source = attrs.get("source", provider) if isinstance(attrs, dict) else provider
                c.dependencies.append(
                    Dependency(
                        name=str(source),
                        ecosystem="Terraform",
                        source=rel,
                        requirement=str(attrs.get("version", ""))
                        if isinstance(attrs, dict)
                        else "",
                    )
                )
    if data.get("variable"):
        c.roles.append("reusable-iac-module")
    for block in data.get("resource", []):
        for resource, instances in block.items():
            if resource.startswith("__") or not isinstance(instances, dict):
                continue
            c.evidence.append(Evidence(file=rel, kind="resource", detail=resource))
            for label, attrs in instances.items():
                if isinstance(attrs, dict) and any(key in attrs for key in ("count", "for_each")):
                    c.evidence.append(
                        Evidence(
                            file=rel, kind="conditional-resource", detail=f"{resource}.{label}"
                        )
                    )


def _roles(c: Component) -> None:
    # Development-only framework dependencies are evidence for tooling, not a service.
    runtime = {d.name for d in c.dependencies if d.scope == "runtime"}
    all_deps = {d.name for d in c.dependencies}
    c.frameworks = sorted(all_deps & FRAMEWORKS)
    if runtime & {"react", "next", "vue", "svelte"} or (
        "svelte" in all_deps and "@sveltejs/kit" in all_deps
    ):
        c.roles.append("frontend")
    if runtime & {"fastapi", "flask", "django", "express", "fastify"}:
        c.roles.extend(["backend", "api", "service"])
    if runtime & AI:
        c.roles.append("ai-subsystem")
    if runtime & AGENTS:
        c.roles.extend(["ai-agent", "ai-subsystem"])
    if runtime & DATA:
        c.roles.append("data-pipeline")
    if c.workspace_patterns:
        c.roles.append("workspace")
    if c.scope == "documentation":
        c.roles.append("documentation")
    elif c.scope in {"fixture", "test", "example", "generated"}:
        c.roles.append(c.scope)
    if not c.roles and c.name:
        c.roles.append("library")
    if "deployed-iac" in c.roles:
        c.lifecycle = "active-application"
    elif "reusable-iac-module" in c.roles:
        c.lifecycle = "reusable-infrastructure-module"
        if c.scope == "runtime":
            c.scope = "reusable-module"
    elif set(c.roles) & {"frontend", "backend", "service"}:
        c.lifecycle = "active-application"
    elif "library" in c.roles:
        c.lifecycle = "library-framework"
    c.roles = sorted(set(c.roles))


def _source_evidence(root: Path, rel: str, c: Component, graph: ProjectGraph) -> None:
    scope = graph.scope(rel)
    if scope in {"fixture", "vendor", "remote-module"}:
        return
    suffix = Path(rel).suffix
    if suffix not in {".py", ".rs", ".go", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml"}:
        return
    text = _read(root, rel, 2_000_000)[:80_000]
    if re.search(
        r"(?mi)^(?://|#|/\*)\s*(?:code generated .*do not edit|auto-generated|generated by)",
        text[:600],
    ):
        if scope == "runtime":
            graph.file_scopes[rel] = "generated"
        graph.relationships.append(
            Relationship(
                source=c.id,
                target=rel,
                kind="generated-output",
                evidence=Evidence(file=rel, kind="header", detail="Explicit generated-code header"),
            )
        )
    kinds: set[str] = set()
    if suffix == ".rs":
        # Attribute syntax is stronger than a filename or an incidental prose marker.
        if re.search(r"(?m)^\s*#\[(?:tokio::|async_std::)?test\]", text):
            kinds.add("unit")
        if re.search(r"(?m)^\s*(?:#!\[no_main\]|fuzz_target!\s*\()", text):
            kinds.add("fuzz")
    if suffix == ".go" and scope == "test":
        for pattern, kind in (
            (r"func Test\w+\(", "unit"),
            (r"func Fuzz\w+\(", "fuzz"),
            (r"func Benchmark\w+\(", "benchmark"),
        ):
            if re.search(pattern, text):
                kinds.add(kind)
    if suffix == ".py" and scope in {"runtime", "test"}:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except (SyntaxError, RecursionError):
            tree = None
        if tree:
            imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            if imports & {"fastapi", "flask"}:
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                if any(
                    isinstance(n.func, ast.Name) and n.func.id in {"FastAPI", "Flask"}
                    for n in calls
                ):
                    graph.relationships.append(
                        Relationship(
                            source=c.id,
                            target=rel,
                            kind="code-defined-api",
                            evidence=Evidence(
                                file=rel,
                                kind="python-ast",
                                detail="Framework application constructor",
                            ),
                        )
                    )
            if scope == "test" and imports & {"hypothesis"}:
                kinds.add("property-based")
            if scope == "test" and imports & {
                "fastapi.testclient",
                "starlette.testclient",
                "flask.testing",
            }:
                kinds.add("integration")
            if scope == "test" and imports & {"vcr", "pytest_recording"}:
                kinds.add("regression")
    if scope == "test" and not kinds:
        kinds.add("smoke" if "Generated by Blueprint AI" in text else "unit")
    if "vcr_cassettes" in Path(rel).parts or "cassettes" in Path(rel).parts:
        kinds.add("regression")
    for kind in sorted(kinds):
        graph.verification.append(VerificationNode(component=c.id, kind=kind, evidence=[rel]))


def build_graph(root: Path, files: list[Path], languages: dict[str, str]) -> ProjectGraph:
    rels = [p.relative_to(root).as_posix() for p in files]
    graph = ProjectGraph(file_scopes={rel: path_scope(rel) for rel in rels})
    # The word "truth" alone is never enough to exclude ordinary application source.
    corpus_roots = []
    for rel in rels:
        if Path(rel).name.lower() == "readme.md":
            text = _read(root, rel, 2_000_000)[:80_000]
            if "<CURSOR>" in text and "truth data" in text and "./truth" in text:
                corpus_roots.append(str(Path(rel).parent / "truth") + "/")
                graph.evidence.append(
                    Evidence(
                        file=rel,
                        kind="fixture",
                        detail="Documented cursor-marked evaluation corpus",
                    )
                )
    for rel in rels:
        if any(rel.startswith(prefix) for prefix in corpus_roots):
            graph.file_scopes[rel] = "fixture"
    components: dict[str, Component] = {".": Component(id=".", root=".")}
    manifests = [rel for rel in rels if Path(rel).name in MANIFEST_LANGUAGES or rel.endswith(".tf")]
    for rel in manifests:
        directory = Path(rel).parent.as_posix()
        c = components.setdefault(
            directory, Component(id=directory, root=directory, scope=graph.scope(rel))
        )
        c.manifests.append(rel)
        if lang := MANIFEST_LANGUAGES.get(Path(rel).name):
            c.languages.append(lang)
        try:
            if rel.endswith(".tf"):
                c.languages.append("HCL")
                _hcl(root, rel, c, graph)
            else:
                _manifest(root, rel, c)
        except Exception as exc:
            # Malformed/unsupported syntax is explicit incomplete inventory, not a guessed role.
            graph.diagnostics.append(f"{rel}: {type(exc).__name__}: manifest inventory incomplete")
    graph.components = list(components.values())
    owners: dict[str, Component] = {}
    for rel in rels:
        owners[rel] = next(
            components[parent.as_posix()]
            for parent in PurePosixPath(rel).parents
            if parent.as_posix() in components
        )
        c = owners[rel]
        scope = graph.scope(rel)
        if "resources" in Path(rel).parts and any(e.kind == "benchmark" for e in c.evidence):
            graph.file_scopes[rel] = "benchmark"
            scope = "benchmark"
        if scope not in NON_RUNTIME and (language := languages.get(Path(rel).suffix)):
            c.languages.append(language)
        name = Path(rel).name.lower()
        if name in {"pnpm-workspace.yaml", "pnpm-workspace.yml"}:
            try:
                c.workspace_patterns.extend(
                    _members(load_yaml_mapping(root / rel, root, label=rel).get("packages", []))
                )
                c.package_manager = "pnpm"
            except ValueError:
                graph.diagnostics.append(f"{rel}: invalid workspace metadata")
        if name in {"copier.yml", "copier.yaml", "cookiecutter.json", "template.json"}:
            c.roles.append("generator-template")
            graph.evidence.append(Evidence(file=rel, kind="template", detail="Template manifest"))
        if name in {
            "uv.lock",
            "poetry.lock",
            "pnpm-lock.yaml",
            "yarn.lock",
            "package-lock.json",
            "cargo.lock",
            "go.mod",
        }:
            c.package_manager = {
                "uv.lock": "uv",
                "poetry.lock": "poetry",
                "pnpm-lock.yaml": "pnpm",
                "yarn.lock": "yarn",
                "package-lock.json": "npm",
                "cargo.lock": "cargo",
                "go.mod": "go",
            }[name]
        if name.startswith("dockerfile") or name.endswith(".dockerfile"):
            text = _read(root, rel, 80_000)
            stages = re.split(r"(?mi)^FROM\s+", text)[1:]
            final = stages[-1] if stages else ""
            stage_name = final.splitlines()[0].lower() if final else ""
            role = (
                "test-image"
                if re.search(r"\bas\s+(?:test|tests)\b", stage_name)
                or re.search(r"(?mi)^(?:CMD|ENTRYPOINT).*\b(?:pytest|test|vitest)\b", final)
                else "build-image"
                if (
                    re.search(r"\bas\s+(?:build|builder|generate|generator)\b", stage_name)
                    or not re.search(r"(?mi)^(?:CMD|ENTRYPOINT)\b", final)
                )
                else "runtime-image"
            )
            graph.file_scopes[rel] = cast(Scope, role)
            if ".devcontainer" in Path(rel).parts:
                graph.file_scopes[rel] = "development"
            c.roles.append("container")
            c.evidence.append(Evidence(file=rel, kind=role, detail="Final Docker stage"))
        if rel.startswith(".github/workflows/") and Path(rel).suffix in {".yaml", ".yml"}:
            try:
                workflow = load_yaml_mapping(root / rel, root, label=rel)
                for job in workflow.get("jobs", {}).values():
                    if not isinstance(job, dict):
                        continue
                    if reference := job.get("uses"):
                        graph.relationships.append(
                            Relationship(
                                source=c.id,
                                target=str(reference),
                                kind="reusable-workflow",
                                external=not str(reference).startswith(("./", "$/")),
                                evidence=Evidence(
                                    file=rel, kind="ci", detail="Reusable workflow job"
                                ),
                            )
                        )
                        graph.verification.append(
                            VerificationNode(
                                component=c.id,
                                kind="external-workflow",
                                evidence=[rel, str(reference)],
                            )
                        )
            except (ValueError, AttributeError):
                graph.diagnostics.append(f"{rel}: invalid workflow inventory")
        # Bounded source reads; excluded compiler fixtures never enter the parser.
        try:
            _source_evidence(root, rel, c, graph)
        except (OSError, ValueError, RecursionError):
            graph.diagnostics.append(f"{rel}: source inventory unavailable or exceeds read limit")
    for c in components.values():
        _roles(c)
        c.languages = sorted(set(c.languages))
        for kind in ("benchmark", "fuzz"):
            for evidence in c.evidence:
                if evidence.kind == kind:
                    graph.verification.append(
                        VerificationNode(component=c.id, kind=kind, evidence=[evidence.file])
                    )
        for script, command in c.scripts.items():
            script_kind = (
                "end-to-end"
                if re.search(r"\b(playwright|cypress)\b", command)
                else (
                    "unit"
                    if script in {"test", "test:unit"}
                    else "build"
                    if script == "build"
                    else "typecheck"
                    if script in {"typecheck", "check-types"}
                    else None
                )
            )
            if script_kind:
                graph.verification.append(
                    VerificationNode(
                        component=c.id,
                        kind=script_kind,
                        evidence=c.manifests[:1],
                        command=[c.package_manager or "npm", "run", script],
                    )
                )
        for other in components.values():
            if other is c:
                continue
            for pattern in c.workspace_patterns:
                prefix = "" if c.root == "." else c.root + "/"
                if fnmatch.fnmatchcase(other.root, prefix + pattern.rstrip("/")):
                    graph.relationships.append(
                        Relationship(
                            source=c.id,
                            target=other.id,
                            kind="workspace-member",
                            evidence=Evidence(
                                file=c.manifests[0] if c.manifests else c.root,
                                kind="workspace",
                                detail=pattern,
                            ),
                        )
                    )
    # Root lifecycle must be backed by direct statements/metadata, not incidental words.
    for rel in rels:
        if Path(rel).name.lower().startswith("readme") and Path(rel).parent == Path("."):
            text = _read(root, rel, 2_000_000)[:100_000]
            if re.search(
                r"(?im)^(?:>\s*)?(?:#+\s*|\*\*)?(?:this (?:project|repository|package|tool) "
                r"(?:is|has been) |create react app (?:is|has been) )?deprecated\b",
                text[:6000],
            ):
                graph.lifecycle = "deprecated-maintenance"
                graph.evidence.append(
                    Evidence(file=rel, kind="lifecycle", detail="Explicit deprecation notice")
                )
            if re.search(
                r"(?im)^#+[^\n]*(?:project |application |full.stack )?template\b", text[:2000]
            ):
                components["."].roles.append("generator-template")
                graph.evidence.append(Evidence(file=rel, kind="lifecycle", detail="Template title"))
    root_component = components["."]
    contracts = [
        rel
        for rel in rels
        if Path(rel).name.lower()
        in {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml"}
    ]
    for rel in contracts:
        owner = owners[rel]
        owner.roles.append("api-contract")
        graph.relationships.append(
            Relationship(
                source=owner.id,
                target=rel,
                kind="api-contract",
                evidence=Evidence(file=rel, kind="schema", detail="Machine-readable API contract"),
            )
        )

    if any(c.name == "create-react-app" for c in components.values()):
        root_component.roles.append("generator-template")
    source = [
        rel
        for rel in rels
        if Path(rel).suffix in languages
        and languages[Path(rel).suffix] not in {"Markdown", "YAML", "HCL"}
        and graph.scope(rel) == "runtime"
    ]
    if contracts and not source and not any(rel.endswith(".tf") for rel in rels):
        graph.lifecycle = "api-contract-project"
    if (
        not source
        and not any(rel.endswith(".tf") for rel in rels)
        and any(rel.lower().endswith((".md", ".rst")) for rel in rels)
        and not any("generator-template" in c.roles for c in components.values())
    ):
        root_component.roles.append("documentation")
        root_component.lifecycle = "documentation-project"
        if graph.lifecycle == "unknown":
            graph.lifecycle = "documentation-project"
    elif "generator-template" in root_component.roles and graph.lifecycle == "unknown":
        graph.lifecycle = "template-generator"
    if graph.lifecycle == "unknown":
        graph.lifecycle = root_component.lifecycle
        if graph.lifecycle == "unknown" and any(
            c.lifecycle == "active-application" and c.scope == "runtime"
            for c in components.values()
        ):
            graph.lifecycle = "active-application"
    _generation_evidence(root, rels, graph)
    return graph


def graph_project_types(graph: ProjectGraph) -> list[str]:
    roles = {
        role
        for c in graph.components
        if c.scope not in NON_RUNTIME | {"test", "documentation"}
        for role in c.roles
    }
    types = roles & {"cli", "library", "data-pipeline", "container", "api-contract"}
    if "frontend" in roles:
        types.add("web-app")
        types.add("frontend")
    if "api" in roles:
        types.update({"api", "backend-service"})
    if {"frontend", "api"} <= roles:
        types.add("full-stack")
    if "ai-subsystem" in roles:
        types.add("ai-app")
    if "ai-agent" in roles:
        types.add("ai-agent")
    if graph.lifecycle == "documentation-project":
        types = {"documentation-project"}
    if graph.lifecycle in {"deprecated-maintenance", "template-generator"}:
        types.add("template-generator")
    return sorted(types)


def _generation_evidence(root: Path, rels: list[str], graph: ProjectGraph) -> None:
    for rel in rels:
        name = Path(rel).name
        if name in {"openapi-ts.config.ts", "openapitools.json", "orval.config.ts"}:
            graph.relationships.append(
                Relationship(
                    source=rel,
                    target=str(Path(rel).parent),
                    kind="client-generator",
                    evidence=Evidence(
                        file=rel,
                        kind="generator-config",
                        detail="Recognized OpenAPI generator configuration; not executed",
                    ),
                )
            )
        if name in {"post_gen_project.py", "pre_gen_project.py", "post_gen_project.sh"}:
            graph.relationships.append(
                Relationship(
                    source=str(Path(rel).parent.parent),
                    target=rel,
                    kind="generation-hook",
                    evidence=Evidence(
                        file=rel,
                        kind="template-hook",
                        detail="Template hook inventory only; no execution authorization",
                    ),
                )
            )
        if (
            name in {"compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"}
            or name.startswith(("docker-compose.", "compose."))
            and name.endswith((".yaml", ".yml"))
        ):
            try:
                compose = load_yaml_mapping(root / rel, root, label=rel)
                services = compose.get("services", {})
                if not isinstance(services, dict):
                    raise ValueError("Compose services must be a mapping")
            except ValueError:
                graph.diagnostics.append(f"{rel}: invalid Compose inventory")
                continue
            for service, spec in services.items():
                if not isinstance(spec, dict):
                    graph.diagnostics.append(f"{rel}: invalid Compose service inventory")
                    continue
                build = spec.get("build")
                context = build.get("context") if isinstance(build, dict) else build
                if isinstance(context, str):
                    graph.relationships.append(
                        Relationship(
                            source=rel,
                            target=context,
                            kind="build-context",
                            evidence=Evidence(file=rel, kind="compose", detail=str(service)),
                        )
                    )
                dependencies = spec.get("depends_on", [])
                if isinstance(dependencies, (list, dict)):
                    for dependency in dependencies:
                        graph.relationships.append(
                            Relationship(
                                source=f"{rel}#{service}",
                                target=f"{rel}#{dependency}",
                                kind="service-dependency",
                                evidence=Evidence(file=rel, kind="compose", detail="depends_on"),
                            )
                        )
    receipt = root / ".blueprint-ai/genesis.json"
    if not receipt.is_file():
        return
    try:
        data = json.loads(read_text_bounded(receipt, 2_000_000, root=root))
        if data.get("schema_version") != "1.0":
            raise ValueError("unsupported genesis provenance")
        hashes = data.get("result", {}).get("files", {})
        graph.evidence.append(
            Evidence(
                file=".blueprint-ai/genesis.json",
                kind="genesis-provenance",
                detail="Creation receipt; does not grant trust or override discovered roles",
            )
        )
        for row in data.get("graph", {}).get("relationships", [])[:1000]:
            edge = Relationship.model_validate(row)
            if (
                edge.kind != "generated-client"
                or edge.source not in hashes
                or edge.target not in hashes
            ):
                continue
            if any(
                hashlib.sha256(
                    read_text_bounded(root / rel, 2_000_000, root=root).encode()
                ).hexdigest()
                != hashes[rel]
                for rel in (edge.source, edge.target)
            ):
                graph.diagnostics.append(
                    "generated-client provenance drift: source or output changed"
                )
                continue
            graph.relationships.append(edge)
    except (OSError, ValueError, TypeError, AttributeError):
        graph.diagnostics.append("invalid or unavailable genesis provenance")
