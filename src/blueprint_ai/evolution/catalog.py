"""Canonical evolution catalog and the deliberately small executable recipe set."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from blueprint_ai.config import load_yaml_mapping
from blueprint_ai.core import ProjectFacts
from blueprint_ai.safety import MAX_MANIFEST_BYTES, read_text_bounded

from .models import Mechanism, TransformationSpec, VerificationRequirement

Detect = Callable[[Path, ProjectFacts], list[str]]
Transform = Callable[[Path, list[str]], dict[str, str]]
Command = Callable[[Path, list[str]], list[str]]


@dataclass(frozen=True)
class RuntimeTransformation:
    spec: TransformationSpec
    detect: Detect
    transform: Transform | None = None
    preview_command: Command | None = None
    apply_command: Command | None = None
    verify_command: Command | None = None


def _verification(
    recipe: str,
    kind: str,
    detail: str,
    *,
    command: list[str] | None = None,
    trust: bool = False,
) -> VerificationRequirement:
    return VerificationRequirement(
        id=f"{recipe}/{kind}",
        kind=kind,
        command=command or [],
        requires_project_trust=trust,
        detail=detail,
    )


def _source_files(suffix: str, *, scopes: set[str]) -> Detect:
    def detect(_root: Path, facts: ProjectFacts) -> list[str]:
        return sorted(
            path
            for path, scope in facts.graph.file_scopes.items()
            if path.endswith(suffix) and scope in scopes
        )

    return detect


def _terraform_files(_root: Path, facts: ProjectFacts) -> list[str]:
    return sorted(
        path
        for path, scope in facts.graph.file_scopes.items()
        if path.endswith((".tf", ".tfvars", ".tftest.hcl"))
        and scope not in {"vendor", "remote-module", "generated", "fixture"}
    )


def _go_packages(paths: list[str]) -> list[str]:
    """Return only package directories represented by the graph-selected Go files."""
    directories = {Path(path).parent.as_posix() for path in paths}
    return ["." if path == "." else f"./{path}" for path in sorted(directories)]


def _ruff_target(root: Path) -> str | None:
    try:
        project = tomllib.loads(
            read_text_bounded(root / "pyproject.toml", MAX_MANIFEST_BYTES, root=root)
        ).get("project", {})
        supported = SpecifierSet(project["requires-python"])
    except (AttributeError, KeyError, OSError, InvalidVersion, TypeError, ValueError):
        return None
    # Ruff 0.13+ accepts these Python target levels. Patch-only lower bounds still
    # select their language minor; excluded entire minors remain excluded.
    if any(Version(f"3.{minor}.999") in supported for minor in range(0, 9)):
        return None
    for minor in range(9, 16):
        if any(Version(f"3.{minor}.{patch}") in supported for patch in (0, 999)):
            return f"py3{minor}"
    return None


def _ruff_command(root: Path, paths: list[str], *arguments: str) -> list[str]:
    target = _ruff_target(root)
    if target is None:
        raise ValueError("project.requires-python has no supported Python 3.9-3.15 target")
    return [
        "ruff",
        "check",
        "--isolated",
        "--target-version",
        target,
        "--select",
        "UP",
        *arguments,
        "--no-cache",
        "--",
        *paths,
    ]


def _terraform_paths(paths: list[str]) -> list[str]:
    """Keep repository filenames from being interpreted as Terraform CLI options."""
    return [path if path.startswith("./") else f"./{path}" for path in paths]


_MAINTAINER = re.compile(r"^(?P<indent>\s*)MAINTAINER[ \t]+(?P<value>\S.*?)[ \t]*$", re.I)


def _dockerfiles(root: Path, facts: ProjectFacts) -> list[str]:
    matches = []
    for relative in facts.containers:
        if "dockerfile" not in Path(relative).name.lower():
            continue
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        if any(_MAINTAINER.match(line) for line in text.splitlines()):
            matches.append(relative)
    return sorted(matches)


def _modernize_dockerfiles(root: Path, paths: list[str]) -> dict[str, str]:
    changed = {}
    for relative in paths:
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        lines = text.splitlines(keepends=True)
        if re.search(r"(?mi)^\s*LABEL\s+[^\n]*org\.opencontainers\.image\.authors\s*=", text):
            raise ValueError(
                f"{relative}: existing org.opencontainers.image.authors label conflicts with "
                "MAINTAINER conversion"
            )
        output = []
        for line in lines:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            body = line[: -len(ending)] if ending else line
            match = _MAINTAINER.match(body)
            if match:
                value = match.group("value")
                if value.endswith("\\"):
                    raise ValueError(f"{relative}: multiline MAINTAINER is ambiguous")
                body = (
                    f"{match.group('indent')}LABEL org.opencontainers.image.authors="
                    + json.dumps(value, ensure_ascii=False)
                )
            output.append(body + ending)
        rendered = "".join(output)
        if rendered != text:
            changed[relative] = rendered
    return changed


# Full SHAs were resolved from the official repositories' major tags on 2026-09-12.
# The transform preserves the selected major; it does not choose a new compatibility target.
OFFICIAL_ACTION_SHAS = {
    "actions/checkout@v2": "0717577d45739eb3c851188b29f50ed6c0b2194e",
    "actions/checkout@v4": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/checkout@v5": "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
    "actions/checkout@v6": "d23441a48e516b6c34aea4fa41551a30e30af803",
    "actions/checkout@v7": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python@v4": "7f4fc3e22c37d6ff65e88745f38bd3157c663f7c",
    "actions/setup-python@v5": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-python@v6": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/setup-python@v7": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/setup-node@v2": "7c12f8017d5436eb855f1ed4399f037a36fbd9e8",
    "actions/setup-node@v3": "3235b876344d2a9aa001b8d1453c930bba69e610",
    "actions/setup-node@v4": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/setup-node@v5": "a0853c24544627f65ddf259abe73b1d18a591444",
    "actions/setup-node@v6": "249970729cb0ef3589644e2896645e5dc5ba9c38",
    "actions/upload-artifact@v3": "ff15f0306b3f739f7b6fd43fb5d26cd321bd4de5",
    "actions/upload-artifact@v4": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/upload-artifact@v5": "330a01c490aca151604b8cf639adc76d48f6c5d4",
    "actions/download-artifact@v3": "9bc31d5ccc31df68ecc42ccf4149144866c47d8a",
    "actions/download-artifact@v4": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/download-artifact@v5": "634f93cb2916e3fdff6788551b99b062d0335ce0",
}
_USES = re.compile(
    r"^(?P<prefix>\s*(?:-\s*)?uses:\s*)(?P<reference>actions/[a-z0-9_-]+@v\d+)"
    r"(?P<suffix>\s*(?:#.*)?)$",
    re.I,
)


def _workflow_files(root: Path, facts: ProjectFacts) -> list[str]:
    matches = []
    for relative in sorted(facts.graph.file_scopes):
        if not relative.startswith(".github/workflows/") or not relative.endswith(
            (".yml", ".yaml")
        ):
            continue
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        if any(
            (match := _USES.match(line))
            and match.group("reference").lower() in OFFICIAL_ACTION_SHAS
            for line in text.splitlines()
        ):
            matches.append(relative)
    return matches


def _pin_workflows(root: Path, paths: list[str]) -> dict[str, str]:
    changed = {}
    for relative in paths:
        # Strict YAML parsing catches malformed and duplicate-key workflows before mutation.
        load_yaml_mapping(root / relative, root, label=relative)
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        output = []
        for line in text.splitlines(keepends=True):
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            body = line[: -len(ending)] if ending else line
            match = _USES.match(body)
            if match and (key := match.group("reference").lower()) in OFFICIAL_ACTION_SHAS:
                major = key.rsplit("@", 1)[1]
                suffix = match.group("suffix")
                if not suffix.strip():
                    suffix = f" # {major}"
                original_name = match.group("reference").split("@", 1)[0]
                body = (
                    match.group("prefix") + original_name + "@" + OFFICIAL_ACTION_SHAS[key] + suffix
                )
            output.append(body + ending)
        rendered = "".join(output)
        if rendered != text:
            changed[relative] = rendered
    return changed


def _manual(predicate: Callable[[ProjectFacts], bool]) -> Detect:
    def detect(_root: Path, facts: ProjectFacts) -> list[str]:
        return ["."] if predicate(facts) else []

    return detect


def _spec(**values) -> TransformationSpec:
    return TransformationSpec.model_validate(values)


def _runtime_catalog() -> list[RuntimeTransformation]:
    python_scopes = {"runtime", "development", "test"}
    go_scopes = {"runtime", "development", "test"}
    return [
        RuntimeTransformation(
            spec=_spec(
                id="python/ruff-pyupgrade",
                category="language-modernization",
                current_state="Python syntax with fixes covered by stable Ruff UP rules",
                desired_state="Syntax modernized to the project's declared minimum Python version",
                applicability=["Python source", "pyproject.toml with project.requires-python"],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider="Ruff",
                tool="ruff",
                tool_version=">=0.13,<1",
                recipe_version="1",
                authoritative_source="https://docs.astral.sh/ruff/rules/#pyupgrade-up",
                license="MIT",
                sandbox_requirements="explicit trusted writable sandbox or trusted host",
                preconditions=[
                    "Git repository is clean or dirty state is explicitly accepted",
                    "pyproject.toml declares project.requires-python",
                    "compatible Ruff executable/image is explicitly available",
                ],
                conflicts=[
                    "generated, fixture, vendor, benchmark, and migration scopes are excluded"
                ],
                expected_paths=["**/*.py", "**/*.pyi"],
                dry_run=True,
                reversible=True,
                verification=[
                    _verification(
                        "python/ruff-pyupgrade",
                        "native-idempotency",
                        "Ruff UP dry-run produces no remaining automatic changes",
                        command=["ruff", "check", "--select", "UP", "--diff"],
                        trust=True,
                    ),
                    _verification(
                        "python/ruff-pyupgrade",
                        "python-parse",
                        "Changed Python files parse successfully",
                    ),
                ],
                maturity="supported",
                mechanism="official-native",
                implementation="command",
                decision="wrap",
                limitations=["Does not change declared Python or dependency versions"],
                reviewed="2026-09-12",
            ),
            detect=lambda root, facts: (
                _source_files(".py", scopes=python_scopes)(root, facts)
                + _source_files(".pyi", scopes=python_scopes)(root, facts)
                if _has_python_target(root)
                else []
            ),
            preview_command=lambda root, paths: _ruff_command(root, paths, "--diff"),
            apply_command=lambda root, paths: _ruff_command(root, paths, "--fix-only"),
            verify_command=lambda root, paths: _ruff_command(root, paths, "--diff"),
        ),
        RuntimeTransformation(
            spec=_spec(
                id="go/native-fix",
                category="language-modernization",
                current_state="Go source eligible for the toolchain's safe modernizers",
                desired_state="Current declared-toolchain fixes applied with go fix",
                applicability=["go.mod", "owned Go source"],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider="Go toolchain",
                tool="go",
                tool_version=">=1.26,<2",
                recipe_version="1",
                authoritative_source="https://go.dev/blog/gofix",
                license="BSD-3-Clause",
                sandbox_requirements=(
                    "explicit trusted writable sandbox or trusted host; no network by default"
                ),
                preconditions=[
                    "Git repository is clean or dirty state is explicitly accepted",
                    "Go 1.26 or newer is available",
                    "module is dependency-free or dependencies are vendored for the empty cache",
                ],
                conflicts=["unexpected writes outside owned Go source abort the transaction"],
                expected_paths=["**/*.go"],
                dry_run=True,
                reversible=True,
                verification=[
                    _verification(
                        "go/native-fix",
                        "native-idempotency",
                        "go fix -diff reports no remaining changes",
                        command=["go", "fix", "-diff", "./..."],
                        trust=True,
                    ),
                    _verification(
                        "go/native-fix",
                        "unit",
                        "The Go package test suite passes",
                        command=["go", "test", "./..."],
                        trust=True,
                    ),
                ],
                maturity="supported",
                mechanism="official-native",
                implementation="command",
                decision="wrap",
                limitations=[
                    "Build-tagged configurations need separate runs; the isolated module cache "
                    "starts empty"
                ],
                reviewed="2026-09-12",
            ),
            detect=lambda root, facts: (
                _source_files(".go", scopes=go_scopes)(root, facts)
                if (root / "go.mod").is_file()
                else []
            ),
            preview_command=lambda _root, paths: ["go", "fix", "-diff", *_go_packages(paths)],
            apply_command=lambda _root, paths: ["go", "fix", *_go_packages(paths)],
            verify_command=lambda _root, paths: ["go", "fix", "-diff", *_go_packages(paths)],
        ),
        RuntimeTransformation(
            spec=_spec(
                id="terraform/native-format",
                category="configuration-modernization",
                current_state="Non-canonical Terraform native-syntax configuration",
                desired_state="Configuration formatted by the selected Terraform CLI",
                applicability=["owned .tf, .tfvars, or .tftest.hcl files"],
                lifecycle=["infrastructure-module", "unknown"],
                provider="Terraform CLI",
                tool="terraform",
                tool_version=">=1.6,<2",
                recipe_version="1",
                authoritative_source="https://developer.hashicorp.com/terraform/cli/commands/fmt",
                license="BUSL-1.1",
                sandbox_requirements=(
                    "explicit trusted writable sandbox or trusted host; network disabled"
                ),
                preconditions=["Compatible Terraform CLI is explicitly available"],
                conflicts=["remote modules, generated code, and JSON configuration are excluded"],
                expected_paths=["**/*.tf", "**/*.tfvars", "**/*.tftest.hcl"],
                dry_run=True,
                reversible=True,
                verification=[
                    _verification(
                        "terraform/native-format",
                        "native-idempotency",
                        "terraform fmt -check succeeds for every selected file",
                        command=["terraform", "fmt", "-check", "-diff"],
                        trust=True,
                    ),
                    _verification(
                        "terraform/native-format", "hcl-parse", "Changed HCL parses successfully"
                    ),
                ],
                maturity="supported",
                mechanism="official-native",
                implementation="command",
                decision="wrap",
                limitations=["Formatting is not a provider, module, backend, or state migration"],
                reviewed="2026-09-12",
            ),
            detect=_terraform_files,
            preview_command=lambda _root, paths: [
                "terraform",
                "fmt",
                "-check",
                "-diff",
                "-no-color",
                *_terraform_paths(paths),
            ],
            apply_command=lambda _root, paths: [
                "terraform",
                "fmt",
                "-no-color",
                *_terraform_paths(paths),
            ],
            verify_command=lambda _root, paths: [
                "terraform",
                "fmt",
                "-check",
                "-diff",
                "-no-color",
                *_terraform_paths(paths),
            ],
        ),
        RuntimeTransformation(
            spec=_spec(
                id="container/maintainer-to-oci-label",
                category="container-modernization",
                current_state="Dockerfile uses the deprecated MAINTAINER instruction",
                desired_state="Author metadata uses org.opencontainers.image.authors",
                applicability=["owned Dockerfile containing a single-line MAINTAINER instruction"],
                lifecycle=["active-application", "unknown"],
                provider="Blueprint AI structural transform",
                recipe_version="1",
                authoritative_source="https://docs.docker.com/reference/build-checks/maintainer-deprecated/",
                license="MIT",
                sandbox_requirements="built-in parser; no command, network, or project execution",
                preconditions=["No existing OCI authors label", "MAINTAINER is single-line"],
                conflicts=["existing org.opencontainers.image.authors label"],
                expected_paths=["**/Dockerfile", "**/*.Dockerfile"],
                dry_run=True,
                reversible=True,
                verification=[
                    _verification(
                        "container/maintainer-to-oci-label",
                        "dockerfile-structure",
                        "Deprecated instruction is absent and OCI authors label is present",
                    )
                ],
                maturity="supported",
                mechanism="builtin-structural",
                implementation="builtin",
                decision="native",
                limitations=["Multiline instructions are rejected rather than guessed"],
                reviewed="2026-09-12",
            ),
            detect=_dockerfiles,
            transform=_modernize_dockerfiles,
        ),
        RuntimeTransformation(
            spec=_spec(
                id="github-actions/pin-official-actions",
                category="ci-modernization",
                current_state="Selected official actions use mutable major tags",
                desired_state="The same major tags are pinned to reviewed immutable full SHAs",
                applicability=["strictly parsed GitHub workflow", "known actions/* major tag"],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider="Blueprint AI schema-aware YAML scalar transform",
                recipe_version="2026.09.12",
                authoritative_source="https://docs.github.com/en/actions/reference/security/secure-use",
                license="MIT",
                sandbox_requirements="built-in parser; no command, network, or project execution",
                preconditions=["Exact official repository/tag mapping is in the reviewed registry"],
                conflicts=[
                    "expressions, branches, unknown actions, quoted scalars, and reusable "
                    "workflows are unchanged"
                ],
                expected_paths=[".github/workflows/*.yml", ".github/workflows/*.yaml"],
                dry_run=True,
                reversible=True,
                verification=[
                    _verification(
                        "github-actions/pin-official-actions",
                        "yaml-parse",
                        "Workflow parses without duplicate keys",
                    ),
                    _verification(
                        "github-actions/pin-official-actions",
                        "immutable-reference",
                        "Every eligible mapped reference is a full SHA",
                    ),
                ],
                maturity="supported",
                mechanism="builtin-structural",
                implementation="builtin",
                decision="native",
                limitations=[
                    "Preserves major compatibility target and never resolves unknown or "
                    "third-party tags"
                ],
                reviewed="2026-09-12",
            ),
            detect=_workflow_files,
            transform=_pin_workflows,
        ),
        *_manual_catalog(),
    ]


def _has_python_target(root: Path) -> bool:
    return _ruff_target(root) is not None


def _manual_catalog() -> list[RuntimeTransformation]:
    rows = [
        (
            "python/requirements-to-uv-project",
            "packaging-modernization",
            "pip requirements workflow",
            "PEP 621 pyproject.toml and uv project workflow",
            "uv",
            "https://docs.astral.sh/uv/guides/migration/pip-to-project/",
            "MIT OR Apache-2.0",
            "partial",
            "adapt",
            lambda f: "requirements.txt" in f.manifests and "pyproject.toml" not in f.manifests,
            [
                "Requirement classification, indexes, editable sources, and build metadata "
                "need operator intent"
            ],
        ),
        (
            "java/openrewrite-lts",
            "runtime-modernization",
            "older Java/JDK source level",
            "requested Java LTS with applicable Apache-licensed recipes",
            "OpenRewrite Maven/Gradle plugin",
            "https://docs.openrewrite.org/running-recipes/getting-started",
            "Apache-2.0 core; recipe-specific",
            "partial",
            "wrap",
            lambda f: "Java" in f.languages,
            [
                "Recipe artifacts, licenses, build plugins, and target JDK must be selected "
                "explicitly"
            ],
        ),
        (
            "java/spring-openrewrite",
            "framework-modernization",
            "older Spring/Spring Boot",
            "requested supported Spring generation",
            "OpenRewrite/Moderne recipes",
            "https://docs.openrewrite.org/licensing/openrewrite-licensing",
            "recipe-specific; some MSA/proprietary",
            "deferred",
            "defer",
            lambda f: any("spring" in value.lower() for value in f.frameworks),
            [
                "No source-available or proprietary recipe is bundled or exposed as "
                "Blueprint-managed execution"
            ],
        ),
        (
            "react/create-react-app-foundation",
            "framework-modernization",
            "deprecated Create React App application or template",
            "explicitly chosen React framework, Vite, Parcel, or Rsbuild foundation",
            "React migration guidance plus chosen foundation tooling",
            "https://react.dev/blog/2025/02/14/sunsetting-create-react-app",
            "tool-specific",
            "partial",
            "defer",
            lambda f: any(
                component.name == "create-react-app"
                or any(dependency.name == "react-scripts" for dependency in component.dependencies)
                for component in f.graph.components
            ),
            [
                "The destination depends on routing, rendering, data, deployment, and project "
                "intent; Blueprint AI will not guess Vite versus a framework"
            ],
        ),
        (
            "react/19-official-codemods",
            "framework-modernization",
            "React before 19",
            "React 19 API patterns",
            "React-recommended Codemod registry recipe",
            "https://react.dev/blog/2024/04/25/react-19-upgrade-guide",
            "recipe-specific",
            "partial",
            "wrap",
            lambda f: any(value.lower() == "react" for value in f.frameworks),
            [
                "Dependency compatibility and runtime behavior require an explicit version "
                "target and project tests"
            ],
        ),
        (
            "next/official-upgrade-codemod",
            "framework-modernization",
            "older Next.js release",
            "explicit requested Next.js release",
            "@next/codemod",
            "https://nextjs.org/docs/app/guides/upgrading/codemods",
            "MIT",
            "partial",
            "wrap",
            lambda f: any(value.lower() == "next.js" for value in f.frameworks),
            [
                "Interactive dependency and semantic upgrade choices are not yet "
                "transactionally adapted"
            ],
        ),
        (
            "rust/edition",
            "language-modernization",
            "older Rust edition",
            "next explicitly requested Rust edition",
            "cargo fix --edition",
            "https://doc.rust-lang.org/cargo/commands/cargo-fix.html",
            "MIT OR Apache-2.0",
            "partial",
            "adapt",
            lambda f: "Rust" in f.languages,
            ["Cargo does not update Cargo.toml and inactive cfg/features can retain manual work"],
        ),
        (
            "dotnet/modernization-agent",
            "runtime-modernization",
            "older .NET or .NET Framework",
            "supported .NET target",
            "GitHub Copilot modernization agent",
            "https://learn.microsoft.com/en-us/dotnet/core/porting/upgrade-assistant-overview",
            "service-specific",
            "deferred",
            "agent-assist",
            lambda f: "C#" in f.languages,
            [
                "Upgrade Assistant is deprecated; the successor requires an authorized "
                "external agent workflow"
            ],
        ),
        (
            "kubernetes/api-convert",
            "configuration-modernization",
            "deprecated Kubernetes API version",
            "explicit supported API version",
            "kubectl convert plugin",
            "https://kubernetes.io/docs/reference/using-api/deprecation-guide/",
            "Apache-2.0",
            "partial",
            "wrap",
            lambda f: bool(f.kubernetes),
            [
                "Conversion can choose non-ideal defaults and must be checked against the "
                "target cluster version"
            ],
        ),
        (
            "iac/terraform-to-opentofu",
            "platform-migration",
            "Terraform configuration and state",
            "OpenTofu after state/dependency-safe migration",
            "OpenTofu CLI and migration guide",
            "https://opentofu.org/docs/intro/migration/",
            "MPL-2.0",
            "deferred",
            "defer",
            lambda f: "terraform" in f.iac,
            [
                "State, backend, provider, remote-state dependency order, and rollback are "
                "not textual changes"
            ],
        ),
        (
            "generic/ast-grep",
            "structural-codemod",
            "explicit old syntax pattern",
            "explicit replacement syntax",
            "ast-grep",
            "https://ast-grep.github.io/guide/rewrite-code.html",
            "MIT",
            "experimental",
            "adapt",
            lambda f: bool({"JavaScript", "TypeScript"} & set(f.languages)),
            [
                "No universal recipe is inferred; a reviewed project-specific structural "
                "rule is required"
            ],
        ),
    ]
    mechanisms: dict[str, Mechanism] = {
        "python/requirements-to-uv-project": "official-native",
        "java/openrewrite-lts": "established-codemod",
        "java/spring-openrewrite": "established-codemod",
        "react/19-official-codemods": "established-codemod",
        "next/official-upgrade-codemod": "official-native",
        "rust/edition": "official-native",
        "dotnet/modernization-agent": "agent-implementation",
        "kubernetes/api-convert": "official-native",
        "iac/terraform-to-opentofu": "official-native",
        "generic/ast-grep": "established-codemod",
    }
    networked = {
        "python/requirements-to-uv-project",
        "java/openrewrite-lts",
        "java/spring-openrewrite",
        "react/create-react-app-foundation",
        "react/19-official-codemods",
        "next/official-upgrade-codemod",
        "dotnet/modernization-agent",
        "iac/terraform-to-opentofu",
    }
    model_planning = {
        "java/openrewrite-lts",
        "react/create-react-app-foundation",
        "dotnet/modernization-agent",
        "iac/terraform-to-opentofu",
    }
    transformations = []
    for (
        recipe_id,
        category,
        current,
        desired,
        provider,
        source,
        license_name,
        maturity,
        decision,
        predicate,
        limitations,
    ) in rows:
        transformations.append(
            RuntimeTransformation(
                spec=_spec(
                    id=recipe_id,
                    category=category,
                    current_state=current,
                    desired_state=desired,
                    applicability=["detected ecosystem; exact applicability remains unresolved"],
                    provider=provider,
                    recipe_version="research-2026.09.12",
                    authoritative_source=source,
                    license=license_name,
                    network_required=recipe_id in networked,
                    sandbox_requirements=(
                        "not executable in 0.7.0; explicit future trust/network policy required"
                    ),
                    preconditions=[
                        "Exact target, tool/recipe version, and verification contract selected"
                    ],
                    conflicts=["Semantic or stateful boundaries must be resolved before mutation"],
                    expected_paths=[],
                    dry_run=False,
                    reversible=False,
                    irreversible_boundary="No mutation is implemented in 0.7.0",
                    verification=[],
                    model_allowed=recipe_id in model_planning,
                    agent_allowed=decision == "agent-assist",
                    maturity=maturity,
                    mechanism=mechanisms.get(recipe_id, "manual"),
                    implementation="manual",
                    decision=decision,
                    limitations=limitations,
                    reviewed="2026-09-12",
                ),
                detect=_manual(predicate),
            )
        )
    return transformations


TRANSFORMATIONS = {item.spec.id: item for item in _runtime_catalog()}


def catalog_data() -> dict:
    return {
        "schema_version": "1.0.0",
        "transformations": {
            key: value.spec.model_dump(mode="json") for key, value in TRANSFORMATIONS.items()
        },
    }
