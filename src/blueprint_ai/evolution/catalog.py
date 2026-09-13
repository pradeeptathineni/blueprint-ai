"""Canonical evolution catalog and the deliberately small executable recipe set."""

from __future__ import annotations

import ast
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from blueprint_ai.config import load_yaml_mapping
from blueprint_ai.core import ProjectFacts
from blueprint_ai.safety import MAX_MANIFEST_BYTES, read_text_bounded

from .models import (
    AuthoritativeToolContract,
    Mechanism,
    PipelineStepKind,
    PipelineStepStatus,
    TransformationSpec,
    TypedPostcondition,
    VerificationRequirement,
)

Detect = Callable[[Path, ProjectFacts], list[str]]
Transform = Callable[[Path, list[str]], dict[str, str]]
Command = Callable[[Path, list[str]], list[str]]
TargetTransform = Callable[[Path, list[str], str | None], dict[str, str]]
PipelineBuilder = Callable[[Path, ProjectFacts, list[str], str], list["RuntimePipelineStage"]]
CurrentTarget = Callable[[Path, ProjectFacts], str | None]

RUST_EDITION_TARGET = "2024"
DOTNET_TFM_TARGET = "net10.0"
REACT_19_TARGET = "19.3.0"
_NON_PROJECT_SCOPES = {"fixture", "vendor", "remote-module", "example", "test", "generated"}


@dataclass(frozen=True)
class RuntimeTransformation:
    spec: TransformationSpec
    detect: Detect
    transform: Transform | None = None
    preview_command: Command | None = None
    apply_command: Command | None = None
    verify_command: Command | None = None
    preflight_command: Command | None = None
    cleanup_command: Command | None = None
    staged_preview: bool = False
    pipeline: PipelineBuilder | None = None
    current_target: CurrentTarget | None = None
    operations: dict[str, TargetTransform] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimePipelineStage:
    id: str
    kind: PipelineStepKind
    files: list[str]
    operation: str = "command"
    operation_target: str | None = None
    tool: str | None = None
    preview_command: list[str] = field(default_factory=list)
    apply_command: list[str] = field(default_factory=list)
    verification: list[VerificationRequirement] = field(default_factory=list)
    postconditions: list[TypedPostcondition] = field(default_factory=list)
    ephemeral_paths: list[str] = field(default_factory=list)
    manual_completion_paths: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    rediscover: bool = False
    mutates: bool = True
    status: PipelineStepStatus = "ready"
    limitations: list[str] = field(default_factory=list)


def _verification(
    recipe: str,
    kind: str,
    detail: str,
    *,
    command: list[str] | None = None,
    trust: bool = False,
    isolated: bool = False,
) -> VerificationRequirement:
    return VerificationRequirement(
        id=f"{recipe}/{kind}",
        kind=kind,
        command=command or [],
        requires_project_trust=trust,
        isolated_copy=isolated,
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


def _ruff_command(root: Path, paths: list[str], rules: str, *arguments: str) -> list[str]:
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
        rules,
        *arguments,
        "--no-cache",
        "--",
        *paths,
    ]


def _preserve_init_typing_exports(root: Path, paths: list[str]) -> dict[str, str]:
    """Mark typing names made unused by UP fixes as intentional package exports."""
    rendered: dict[str, str] = {}
    for relative in paths:
        if Path(relative).name != "__init__.py":
            continue
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        tree = ast.parse(text)
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        line_offsets = [0]
        for line in text.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1] + len(line))
        inserts: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            aliases: list[ast.alias]
            if isinstance(node, ast.ImportFrom) and node.module in {"typing", "typing_extensions"}:
                aliases = node.names
            elif isinstance(node, ast.Import):
                aliases = [
                    alias for alias in node.names if alias.name in {"typing", "typing_extensions"}
                ]
            else:
                continue
            for alias in aliases:
                if alias.asname is not None or alias.name == "*" or alias.name in used:
                    continue
                if alias.end_lineno is None or alias.end_col_offset is None:
                    raise ValueError(f"{relative}: typing import position is unavailable")
                offset = line_offsets[alias.end_lineno - 1] + alias.end_col_offset
                inserts.append((offset, f" as {alias.name}"))
        if inserts:
            output = text
            for offset, value in sorted(inserts, reverse=True):
                output = output[:offset] + value + output[offset:]
            rendered[relative] = output
    return rendered


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
    "actions/setup-java@v2": "215b7e5f829ab9cac114c149667e57dc1b70b4bc",
    "actions/setup-java@v3": "e9fbacdec3bb3b6036605a3e6f7995d66773a8c6",
    "actions/setup-java@v4": "cf277c60eb25467037889841efdb72551f06f6c3",
    "actions/setup-java@v5": "b6effb05e454b25005698d916606bdc6ffcbf961",
    "actions/setup-java@v6": "de7274f081f381c8f8158605e0321c36c376e2e6",
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


def _owned_manifests(facts: ProjectFacts, predicate: Callable[[str], bool]) -> list[str]:
    return sorted(
        relative
        for relative, scope in facts.graph.file_scopes.items()
        if scope not in _NON_PROJECT_SCOPES and predicate(relative)
    )


def _rust_editions(root: Path, facts: ProjectFacts) -> list[str]:
    editions: set[str] = set()
    workspace_editions: set[str] = set()
    documents: list[dict] = []
    try:
        for relative in _owned_manifests(facts, lambda path: Path(path).name == "Cargo.toml"):
            document = tomllib.loads(
                read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
            )
            if not isinstance(document, dict):
                return []
            documents.append(document)
            workspace = document.get("workspace")
            if isinstance(workspace, dict) and isinstance(workspace.get("package"), dict):
                value = workspace["package"].get("edition")
                if isinstance(value, str):
                    workspace_editions.add(value)
        for document in documents:
            package = document.get("package")
            if not isinstance(package, dict):
                continue
            value = package.get("edition", "2015")
            if isinstance(value, dict) and value.get("workspace") is True:
                value = next(iter(workspace_editions)) if len(workspace_editions) == 1 else None
            if not isinstance(value, str) or value not in {"2015", "2018", "2021", "2024"}:
                return []
            editions.add(value)
    except (OSError, ValueError):
        return []
    return sorted(editions)


def _dotnet_target_frameworks(root: Path, facts: ProjectFacts) -> list[str]:
    frameworks: set[str] = set()
    try:
        for relative in _owned_manifests(facts, lambda path: path.endswith(".csproj")):
            document = ET.fromstring(
                read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
            )
            for element in document.iter():
                name = element.tag.rsplit("}", 1)[-1]
                if name not in {"TargetFramework", "TargetFrameworks"}:
                    continue
                value = (element.text or "").strip()
                values = [item.strip().lower() for item in value.split(";") if item.strip()]
                if not values or any(
                    not re.fullmatch(r"[a-z][a-z0-9.+-]*", item) for item in values
                ):
                    return []
                frameworks.update(values)
    except (ET.ParseError, OSError, ValueError):
        return []

    def sort_key(value: str) -> tuple[int, int, str]:
        generation = _dotnet_tfm_generation(value)
        if generation is None:
            return (1, 0, value)
        return (0, generation, value)

    return sorted(frameworks, key=sort_key)


def _rust_before_target(root: Path, facts: ProjectFacts) -> list[str]:
    editions = _rust_editions(root, facts)
    return ["."] if len(editions) == 1 and int(editions[0]) < int(RUST_EDITION_TARGET) else []


def _dotnet_tfm_generation(value: str) -> int | None:
    if match := re.fullmatch(r"net(\d+)\.\d+(?:-[a-z0-9.-]+)?", value):
        return int(match.group(1))
    if re.fullmatch(r"net\d{2,3}(?:-[a-z0-9.-]+)?", value):
        return 4  # .NET Framework target monikers such as net48 and net472.
    if match := re.fullmatch(r"netcoreapp(\d+)\.\d+(?:-[a-z0-9.-]+)?", value):
        return int(match.group(1))
    return None


def _dotnet_before_target(root: Path, facts: ProjectFacts) -> list[str]:
    frameworks = _dotnet_target_frameworks(root, facts)
    generation = _dotnet_tfm_generation(frameworks[0]) if len(frameworks) == 1 else None
    return ["."] if generation is not None and generation < 10 else []


def _postcondition(
    identifier: str,
    kind: str,
    detail: str,
    *,
    paths: list[str] | None = None,
    pattern: str | None = None,
    selector: str | None = None,
    expected: str | None = None,
) -> TypedPostcondition:
    return TypedPostcondition.model_validate(
        {
            "id": identifier,
            "kind": kind,
            "detail": detail,
            "paths": paths or [],
            "pattern": pattern,
            "selector": selector,
            "expected": expected,
        }
    )


def _authoritative_tool(**values) -> AuthoritativeToolContract:
    return AuthoritativeToolContract.model_validate(values)


def _rust_owned_paths(root: Path, facts: ProjectFacts) -> list[str]:
    manifests = _owned_manifests(facts, lambda path: Path(path).name == "Cargo.toml")
    # A root Cargo invocation cannot verify an unrelated nested package. A workspace-aware
    # multi-package lane needs explicit member resolution and per-package command evidence.
    if len(manifests) != 1 or manifests[0] != "Cargo.toml":
        return []
    explicit_editions: set[str] = set()
    try:
        for relative in manifests:
            document = tomllib.loads(
                read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
            )
            package = document.get("package")
            edition = package.get("edition") if isinstance(package, dict) else None
            # Workspace-inherited editions need a separate, section-aware workspace lane.
            # The supported lane only owns literal package.edition fields.
            if not isinstance(edition, str) or edition not in {"2018", "2021"}:
                return []
            explicit_editions.add(edition)
    except (OSError, ValueError):
        return []
    if len(explicit_editions) != 1 or not manifests or not (root / "Cargo.lock").is_file():
        return []
    return sorted(
        relative
        for relative, scope in facts.graph.file_scopes.items()
        if scope not in {"fixture", "vendor", "remote-module", "generated"}
        and (relative.endswith(".rs") or Path(relative).name in {"Cargo.toml", "Cargo.lock"})
    )


def _rust_current_target(root: Path, facts: ProjectFacts) -> str | None:
    manifests = _owned_manifests(facts, lambda path: Path(path).name == "Cargo.toml")
    if len(manifests) != 1 or manifests[0] != "Cargo.toml":
        return None
    editions: set[str] = set()
    try:
        for relative in manifests:
            document = tomllib.loads(
                read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
            )
            package = document.get("package")
            edition = package.get("edition") if isinstance(package, dict) else None
            if not isinstance(edition, str) or edition not in {"2018", "2021", "2024"}:
                return None
            editions.add(edition)
    except (OSError, ValueError):
        return None
    return next(iter(editions)) if manifests and len(editions) == 1 else None


def _replace_rust_edition(root: Path, paths: list[str], target: str | None) -> dict[str, str]:
    if target not in {"2021", "2024"}:
        raise ValueError("Rust edition stage has an unsupported target")
    changed: dict[str, str] = {}
    header = re.compile(r"(?m)^\s*\[\s*([^]\r\n]+?)\s*\]\s*(?:#.*)?$")
    explicit = re.compile(
        r'(?m)^(?P<prefix>\s*edition\s*=\s*)["\'](?:2018|2021|2024)["\']'
        r"(?P<suffix>\s*(?:#.*)?)$"
    )
    matched = False
    for relative in paths:
        if Path(relative).name != "Cargo.toml":
            continue
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        headers = list(header.finditer(text))
        packages = [index for index, match in enumerate(headers) if match.group(1) == "package"]
        if len(packages) != 1:
            raise ValueError(f"{relative}: expected one literal [package] table")
        index = packages[0]
        start = headers[index].end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        section, count = explicit.subn(rf'\g<prefix>"{target}"\g<suffix>', text[start:end])
        if count != 1:
            raise ValueError(f"{relative}: expected one explicit package edition field")
        matched = True
        rendered = text[:start] + section + text[end:]
        if rendered != text:
            changed[relative] = rendered
    if not matched:
        raise ValueError("no explicit owned Cargo edition field was available to update")
    return changed


RUST_TOOL = _authoritative_tool(
    tool_id="cargo",
    provider="Rust toolchain",
    host_executable="cargo",
    container_executable="cargo",
    tool_version=">=1.85,<2",
    recipe="cargo fix --edition",
    recipe_version="1",
    authoritative_source=(
        "https://doc.rust-lang.org/stable/edition-guide/editions/"
        "transitioning-an-existing-project-to-a-new-edition.html"
    ),
    tool_license="MIT OR Apache-2.0",
    recipe_license="MIT OR Apache-2.0",
    image="blueprint-tools/rust:1.98.1",
    network="none",
    preview_supported=False,
    expected_write_scopes=["Cargo.toml", "Cargo.lock", "**/*.rs"],
    timeout_seconds=600,
    memory_mb=2048,
)


def _rust_pipeline(
    root: Path, facts: ProjectFacts, paths: list[str], target: str
) -> list[RuntimePipelineStage]:
    editions = _rust_editions(root, facts)
    if len(editions) != 1:
        return []
    current = editions[0]
    current_values = []
    # The exact current edition is sealed in the plan's ProjectState. The stage ladder is
    # deterministic and never skips the 2021 boundary.
    for edition in ("2021", "2024"):
        if int(edition) <= int(current) or int(edition) > int(target):
            continue
        current_values.append(edition)
    manifests = [path for path in paths if Path(path).name == "Cargo.toml"]
    stages: list[RuntimePipelineStage] = []
    previous: str | None = None
    for edition in current_values:
        fix_id = f"cargo-fix-{edition}"
        stages.append(
            RuntimePipelineStage(
                id=fix_id,
                kind="native-command",
                operation=fix_id,
                operation_target=edition,
                files=paths,
                tool="cargo",
                apply_command=[
                    "cargo",
                    "fix",
                    "--edition",
                    "--locked",
                    "--offline",
                    "--allow-dirty",
                    "--allow-no-vcs",
                    "--all-targets",
                    "--all-features",
                ],
                depends_on=[previous] if previous else [],
                rediscover=True,
            )
        )
        manifest_id = f"manifest-{edition}"
        stages.append(
            RuntimePipelineStage(
                id=manifest_id,
                kind="builtin-edit",
                operation="rust-edition",
                operation_target=edition,
                files=manifests,
                depends_on=[fix_id],
                rediscover=True,
                postconditions=[
                    _postcondition(
                        f"rust/edition-{edition}",
                        "exact-value",
                        f"Every owned explicit Cargo edition is {edition}",
                        paths=manifests,
                        selector="toml:package.edition",
                        expected=edition,
                    )
                ],
            )
        )
        verify_id = f"verify-{edition}"
        stages.append(
            RuntimePipelineStage(
                id=verify_id,
                kind="postcondition",
                operation=verify_id,
                files=paths,
                tool="cargo",
                mutates=False,
                depends_on=[manifest_id],
                verification=[
                    _verification(
                        f"rust/edition-{edition}",
                        "format",
                        "rustfmt accepts all workspace targets",
                        command=["cargo", "fmt", "--all", "--", "--check"],
                        trust=True,
                    ),
                    _verification(
                        f"rust/edition-{edition}",
                        "compiler",
                        "Cargo checks all features and targets offline",
                        command=[
                            "cargo",
                            "check",
                            "--locked",
                            "--offline",
                            "--all-features",
                            "--all-targets",
                        ],
                        trust=True,
                    ),
                    _verification(
                        f"rust/edition-{edition}",
                        "test",
                        "Cargo tests all features and targets offline",
                        command=[
                            "cargo",
                            "test",
                            "--locked",
                            "--offline",
                            "--all-features",
                            "--all-targets",
                        ],
                        trust=True,
                    ),
                    _verification(
                        f"rust/edition-{edition}",
                        "doctest",
                        "Cargo doctests pass for all features offline",
                        command=[
                            "cargo",
                            "test",
                            "--doc",
                            "--locked",
                            "--offline",
                            "--all-features",
                        ],
                        trust=True,
                    ),
                ],
                postconditions=[],
            )
        )
        previous = verify_id
    return stages


def _simple_dotnet_project(root: Path, facts: ProjectFacts) -> tuple[str, str] | None:
    projects = _owned_manifests(facts, lambda path: path.endswith(".csproj"))
    if len(projects) != 1:
        return None
    relative = projects[0]
    try:
        document = ET.fromstring(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
    except (ET.ParseError, OSError, ValueError):
        return None
    if document.attrib.get("Sdk") != "Microsoft.NET.Sdk":
        return None
    tfms = [
        element
        for element in document.iter()
        if element.tag.rsplit("}", 1)[-1] in {"TargetFramework", "TargetFrameworks"}
    ]
    if (
        len(tfms) != 1
        or tfms[0].tag.rsplit("}", 1)[-1] != "TargetFramework"
        or not isinstance(tfms[0].text, str)
        or bool(tfms[0].attrib)
        or "$" in tfms[0].text
        or any(
            element.attrib
            and any(child.tag.rsplit("}", 1)[-1] == "TargetFramework" for child in element)
            for element in document.iter()
            if element.tag.rsplit("}", 1)[-1] == "PropertyGroup"
        )
        or any(element.tag.rsplit("}", 1)[-1] == "PackageReference" for element in document.iter())
    ):
        return None
    current = tfms[0].text.strip().lower()
    return (relative, current) if re.fullmatch(r"net\d+\.0", current) else None


def _simple_dotnet_paths(root: Path, facts: ProjectFacts) -> list[str]:
    project = _simple_dotnet_project(root, facts)
    generation = _dotnet_tfm_generation(project[1]) if project is not None else None
    if project is None or generation is None or generation >= 10:
        return []
    return [project[0]]


def _dotnet_current_target(root: Path, facts: ProjectFacts) -> str | None:
    frameworks = _dotnet_target_frameworks(root, facts)
    return frameworks[0] if len(frameworks) == 1 else None


def _replace_dotnet_tfm(root: Path, paths: list[str], target: str | None) -> dict[str, str]:
    if target != "net10.0" or len(paths) != 1:
        raise ValueError("the simple .NET lane supports only an explicit net10.0 target")
    relative = paths[0]
    text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
    pattern = re.compile(
        r"(<TargetFramework>)(?P<leading>\s*)(net\d+\.0)(?P<trailing>\s*)"
        r"(</TargetFramework>)"
    )
    rendered, count = pattern.subn(rf"\g<1>\g<leading>{target}\g<trailing>\g<5>", text)
    if count != 1:
        raise ValueError("the SDK project does not contain one literal TargetFramework")
    return {relative: rendered} if rendered != text else {}


DOTNET_TOOL = _authoritative_tool(
    tool_id="dotnet",
    provider="Microsoft .NET SDK",
    host_executable="dotnet",
    container_executable="dotnet",
    tool_version=">=10,<11",
    recipe="SDK target framework verification",
    recipe_version="1",
    authoritative_source="https://learn.microsoft.com/en-us/dotnet/core/porting/",
    tool_license="MIT",
    recipe_license="MIT",
    image=(
        "mcr.microsoft.com/dotnet/sdk@"
        "sha256:2fa828c68761b1b8c23d7662dc134421b9d3b59fe1425fdbc80804e390cdb24d"
    ),
    image_digest="sha256:2fa828c68761b1b8c23d7662dc134421b9d3b59fe1425fdbc80804e390cdb24d",
    network="none",
    preview_supported=True,
    expected_write_scopes=["*.csproj"],
    timeout_seconds=600,
    memory_mb=2048,
)


def _dotnet_pipeline(
    _root: Path, _facts: ProjectFacts, paths: list[str], target: str
) -> list[RuntimePipelineStage]:
    project = paths[0]
    return [
        RuntimePipelineStage(
            id="target-framework",
            kind="builtin-edit",
            operation="dotnet-tfm",
            files=paths,
            rediscover=True,
            postconditions=[
                _postcondition(
                    "dotnet/target-framework",
                    "exact-value",
                    "The SDK project reached the explicitly requested target framework",
                    paths=paths,
                    selector="xml:TargetFramework",
                    expected=target,
                )
            ],
        ),
        RuntimePipelineStage(
            id="restore-build",
            kind="postcondition",
            files=paths,
            tool="dotnet",
            mutates=False,
            depends_on=["target-framework"],
            verification=[
                _verification(
                    "dotnet/sdk-target",
                    "restore-build",
                    "The selected SDK restores offline and builds the requested target",
                    command=[
                        "dotnet",
                        "build",
                        project,
                        "-p:RestoreSources=/tmp/blueprint-empty-nuget",
                        "-p:RestoreIgnoreFailedSources=true",
                        "-p:BaseIntermediateOutputPath=/tmp/blueprint-dotnet/obj/",
                        "-p:OutputPath=/tmp/blueprint-dotnet/bin/",
                        "-p:NuGetAudit=false",
                    ],
                    trust=True,
                ),
            ],
        ),
    ]


def _python_legacy_build_paths(root: Path, facts: ProjectFacts) -> list[str]:
    owned = set(
        _owned_manifests(
            facts, lambda path: Path(path).name in {"setup.py", "setup.cfg", "pyproject.toml"}
        )
    )
    if not ({"setup.py", "setup.cfg"} & owned):
        return []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            document = tomllib.loads(read_text_bounded(pyproject, MAX_MANIFEST_BYTES, root=root))
        except (OSError, ValueError):
            return []
        tools = document.get("tool", {})
        if "build-system" in document or (
            isinstance(tools, dict)
            and any(key in tools for key in ("poetry", "hatch", "flit", "pdm"))
        ):
            return []
    setup_text = ""
    for relative in ("setup.py", "setup.cfg"):
        if (root / relative).is_file():
            setup_text += read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
    if "setuptools" not in setup_text and "[metadata]" not in setup_text:
        return []
    return sorted(owned | {"pyproject.toml"})


def _python_current_build_backend(root: Path, facts: ProjectFacts) -> str | None:
    if "pyproject.toml" not in facts.graph.file_scopes:
        return None
    try:
        document = tomllib.loads(
            read_text_bounded(root / "pyproject.toml", MAX_MANIFEST_BYTES, root=root)
        )
    except (OSError, ValueError):
        return None
    build_system = document.get("build-system")
    backend = build_system.get("build-backend") if isinstance(build_system, dict) else None
    requirements = build_system.get("requires") if isinstance(build_system, dict) else None
    return (
        backend
        if isinstance(backend, str)
        and isinstance(requirements, list)
        and requirements.count("setuptools>=77") == 1
        else None
    )


def _add_setuptools_build_system(
    root: Path, paths: list[str], target: str | None
) -> dict[str, str]:
    if target != "setuptools.build_meta":
        raise ValueError(
            "the legacy Python lane requires the explicit setuptools.build_meta backend"
        )
    relative = "pyproject.toml"
    path = root / relative
    text = read_text_bounded(path, MAX_MANIFEST_BYTES, root=root) if path.is_file() else ""
    if text:
        document = tomllib.loads(text)
        if "build-system" in document:
            return {}
        separator = "" if text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
        rendered = text + separator
    else:
        rendered = ""
    rendered += (
        '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n'
    )
    return {relative: rendered}


PYTHON_BUILD_TOOL = _authoritative_tool(
    tool_id="python-build",
    provider="PyPA build interface",
    host_executable="python",
    container_executable="python",
    tool_version=">=3.12,<4",
    recipe="PEP 517 setuptools build-system declaration",
    recipe_version="1",
    authoritative_source="https://packaging.python.org/en/latest/guides/modernize-setup-py-project/",
    tool_license="PSF-2.0; setuptools MIT; wheel MIT",
    recipe_license="MIT",
    image="blueprint-tools/python-build:3.12-setuptools84",
    network="none",
    preview_supported=True,
    expected_write_scopes=["pyproject.toml"],
    timeout_seconds=300,
)


def _python_build_pipeline(
    _root: Path, _facts: ProjectFacts, paths: list[str], target: str
) -> list[RuntimePipelineStage]:
    return [
        RuntimePipelineStage(
            id="build-system",
            kind="builtin-edit",
            operation="python-build-system",
            files=["pyproject.toml"],
            rediscover=True,
            postconditions=[
                _postcondition(
                    "python/build-backend",
                    "exact-value",
                    "pyproject.toml declares the selected backend",
                    paths=["pyproject.toml"],
                    selector="toml:build-system.build-backend",
                    expected=target,
                ),
                _postcondition(
                    "python/build-requires-setuptools",
                    "required-pattern",
                    "The build requirements explicitly contain setuptools",
                    paths=["pyproject.toml"],
                    pattern=r'(?m)^requires\s*=\s*\[[^\]]*"setuptools>=77"',
                ),
            ],
        ),
        RuntimePipelineStage(
            id="package-build",
            kind="postcondition",
            files=paths,
            tool="python-build",
            mutates=False,
            depends_on=["build-system"],
            verification=[
                _verification(
                    "python/pep517-build-system",
                    "build",
                    "pip can build a wheel through the declared PEP 517 backend",
                    command=[
                        "python",
                        "-m",
                        "pip",
                        "wheel",
                        ".",
                        "--no-deps",
                        "--no-build-isolation",
                        "--wheel-dir",
                        "/tmp/blueprint-python-dist",
                    ],
                    trust=True,
                    isolated=True,
                )
            ],
        ),
    ]


def _root_package_json(root: Path, facts: ProjectFacts) -> tuple[str, dict] | None:
    if "package.json" not in facts.graph.file_scopes:
        return None
    try:
        document = json.loads(
            read_text_bounded(root / "package.json", MAX_MANIFEST_BYTES, root=root)
        )
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return ("package.json", document) if isinstance(document, dict) else None


def _exact_npm_dependencies(root: Path, facts: ProjectFacts) -> dict[str, str]:
    package = _root_package_json(root, facts)
    if package is None:
        return {}
    versions: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        values = package[1].get(section, {})
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            if (
                isinstance(name, str)
                and isinstance(value, str)
                and re.fullmatch(r"\d+\.\d+\.\d+", value)
            ):
                versions[name.lower()] = value
    return versions


def _root_owned_js_paths(facts: ProjectFacts) -> list[str]:
    root_component = facts.graph.by_root.get(".")
    return sorted(
        path
        for path, scope in facts.graph.file_scopes.items()
        if scope not in _NON_PROJECT_SCOPES
        and root_component is not None
        and facts.graph.owner(path) == root_component
        and path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"))
    )


def _react_migration_paths(root: Path, facts: ProjectFacts) -> list[str]:
    versions = _exact_npm_dependencies(root, facts)
    react = versions.get("react")
    react_dom = versions.get("react-dom")
    package = _root_package_json(root, facts)
    dependencies = package[1].get("dependencies", {}) if package is not None else {}
    development = package[1].get("devDependencies", {}) if package is not None else {}
    if (
        not react
        or not react_dom
        or not isinstance(dependencies, dict)
        or not isinstance(development, dict)
        or dependencies.get("react") != react
        or dependencies.get("react-dom") != react_dom
        or "react" in development
        or "react-dom" in development
        or any(name in dependencies for name in ("@types/react", "@types/react-dom"))
        or any(
            name in development and name not in versions
            for name in ("@types/react", "@types/react-dom")
        )
        or Version(react) > Version(REACT_19_TARGET)
        or Version(react_dom) > Version(REACT_19_TARGET)
        or "next" in facts.frameworks
        or not (root / "src").is_dir()
        or any(
            (root / name).is_file()
            for name in (
                "package-lock.json",
                "npm-shrinkwrap.json",
                "yarn.lock",
                "pnpm-lock.yaml",
                "bun.lock",
                "bun.lockb",
            )
        )
    ):
        return []
    owned = _root_owned_js_paths(facts)
    if react == REACT_19_TARGET and react_dom == REACT_19_TARGET:
        return ["package.json", *owned] if _react_known_residuals(root, owned) else []
    return ["package.json", *owned]


def _react_current_target(root: Path, facts: ProjectFacts) -> str | None:
    versions = _exact_npm_dependencies(root, facts)
    package = _root_package_json(root, facts)
    dependencies = package[1].get("dependencies", {}) if package is not None else {}
    development = package[1].get("devDependencies", {}) if package is not None else {}
    react = versions.get("react")
    react_dom = versions.get("react-dom")
    if (
        not react
        or react != react_dom
        or not isinstance(dependencies, dict)
        or not isinstance(development, dict)
        or dependencies.get("react") != react
        or dependencies.get("react-dom") != react
        or "react" in development
        or "react-dom" in development
        or any(name in dependencies for name in ("@types/react", "@types/react-dom"))
        or any(
            name in development and versions.get(name) != react
            for name in ("@types/react", "@types/react-dom")
        )
    ):
        return None
    return react if not _react_known_residuals(root, _root_owned_js_paths(facts)) else None


def _replace_package_versions(root: Path, paths: list[str], target: str | None) -> dict[str, str]:
    if not target or not re.fullmatch(r"\d+\.\d+\.\d+", target):
        raise ValueError("npm dependency stage requires an exact semantic version")
    relative = "package.json"
    document = json.loads(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
    changed = False
    for section in ("dependencies", "devDependencies"):
        values = document.get(section, {})
        if not isinstance(values, dict):
            continue
        for name in ("react", "react-dom"):
            if name in values and values[name] != target:
                values[name] = target
                changed = True
    if not changed:
        return {}
    return {relative: json.dumps(document, indent=2, ensure_ascii=False) + "\n"}


def _javascript_code_mask(text: str) -> str:
    """Preserve executable-code offsets while blanking comments, regexes, and literals."""
    masked = list(text)
    state = "code"
    escaped = False
    regex_class = False
    previous_code = ""
    template_depths: list[int] = []
    template_count = 0
    index = 0
    while index < len(text):
        character = text[index]
        pair = text[index : index + 2]
        if state == "code":
            if pair in {"//", "/*"}:
                state = "line-comment" if pair == "//" else "block-comment"
                masked[index] = masked[index + 1] = " "
                index += 2
                continue
            if character in {"'", '"'}:
                state = character
                escaped = False
                masked[index] = " "
            elif character == "`":
                state = "template"
                template_count += 1
                escaped = False
                masked[index] = " "
            elif character == "/" and (not previous_code or previous_code in "([{=,:;!?&|+-*%^~"):
                state = "regex"
                regex_class = False
                escaped = False
                masked[index] = " "
            elif character == "{" and template_depths:
                template_depths[-1] += 1
                previous_code = character
            elif character == "}" and template_depths:
                template_depths[-1] -= 1
                if template_depths[-1] == 0:
                    template_depths.pop()
                    state = "template"
                    masked[index] = " "
                else:
                    previous_code = character
            elif not character.isspace():
                previous_code = character
        elif state == "line-comment":
            if character == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block-comment":
            masked[index] = " "
            if pair == "*/":
                masked[index + 1] = " "
                state = "code"
                index += 2
                continue
        elif state == "regex":
            masked[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "[":
                regex_class = True
            elif character == "]":
                regex_class = False
            elif character == "/" and not regex_class:
                state = "code"
                previous_code = "x"
        elif state == "template":
            masked[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif pair == "${":
                masked[index + 1] = " "
                template_depths.append(1)
                state = "code"
                previous_code = "{"
                index += 2
                continue
            elif character == "`":
                template_count -= 1
                state = "code" if template_depths or template_count == 0 else "template"
                previous_code = "x"
        else:
            masked[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == state:
                state = "code"
                previous_code = "x"
        index += 1
    return "".join(masked)


def _javascript_migration_syntax_mask(text: str) -> str:
    """Expose only migration-relevant string literals outside comments/other strings."""
    masked = list(_javascript_code_mask(text))
    code = "".join(masked)
    context = re.compile(
        r"(?:\bfrom|\brequire\s*\(|\bimport\s*\(|\bref\s*=\s*\{?|\bruntime\s*=|"
        r"\bthis\s*\[|[\[,{])\s*$"
    )
    literal_assignment = re.compile(r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*$")
    index = 0
    while index < len(text):
        quote = text[index]
        prefix_start = max(0, index - 128)
        prefix = code[prefix_start:index]
        raw_prefix = text[prefix_start:index]
        contextual_literal = context.search(prefix) and context.search(raw_prefix)
        targeted_assignment = (
            literal_assignment.search(prefix)
            and literal_assignment.search(raw_prefix)
            and (
                text.startswith(quote + "@next/font", index)
                or text.startswith(quote + "experimental_ppr" + quote, index)
            )
        )
        if (
            quote not in {'"', "'"}
            or masked[index] != " "
            or not (contextual_literal or targeted_assignment)
        ):
            index += 1
            continue
        end = index + 1
        escaped = False
        while end < len(text):
            character = text[end]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                end += 1
                break
            elif character in "\r\n":
                break
            end += 1
        masked[index:end] = text[index:end]
        index = end
    return "".join(masked)


def _react_known_residuals(root: Path, paths: list[str]) -> bool:
    for relative in paths:
        try:
            text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        except (OSError, ValueError):
            return True
        code = _javascript_code_mask(text)
        migration_syntax = _javascript_migration_syntax_mask(text)
        if (
            re.search(
                r"\bthis(?:\.refs|\[\s*[\"']refs[\"']\s*\])(?:\.|\[)",
                migration_syntax,
            )
            or re.search(
                r"\bReactDOM\.(?:render|hydrate|findDOMNode|unmountComponentAtNode)\b", code
            )
            or re.search(r"\bref\s*=\s*(?:\{\s*)?[\"']", migration_syntax)
        ):
            return True
    return False


def _next_unstable_cache_residual(text: str) -> bool:
    """Detect only the two next/cache names stabilized by the pinned Next 16 recipe."""
    syntax = _javascript_migration_syntax_mask(text)
    names = r"(?:unstable_cacheTag|unstable_cacheLife)"
    direct = [
        rf"(?m)^\s*(?:import|export)\s*\{{[^}}\r\n]*\b{names}\b[^}}\r\n]*\}}"
        rf"\s*from\s*[\"']next/cache[\"']",
        rf"\{{[^}}\r\n]*\b{names}\b[^}}\r\n]*\}}\s*=\s*"
        rf"require\s*\(\s*[\"']next/cache[\"']\s*\)",
        rf"\{{[^}}\r\n]*\b{names}\b[^}}\r\n]*\}}\s*=\s*(?:await\s*)?"
        rf"import\s*\(\s*[\"']next/cache[\"']\s*\)",
        rf"require\s*\(\s*[\"']next/cache[\"']\s*\)\s*(?:\.\s*{names}\b|"
        rf"\[\s*[\"']{names}[\"']\s*\])",
        rf"import\s*\(\s*[\"']next/cache[\"']\s*\)\s*\.\s*then\s*\("
        rf"[^\r\n]*\{{[^}}\r\n]*\b{names}\b[^}}\r\n]*\}}",
    ]
    if any(re.search(pattern, syntax) for pattern in direct):
        return True
    binding_patterns = [
        r"(?m)^\s*import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s*"
        r"[\"']next/cache[\"']",
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*"
        r"[\"']next/cache[\"']\s*\)",
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s*)?"
        r"import\s*\(\s*[\"']next/cache[\"']\s*\)",
    ]
    for pattern in binding_patterns:
        for match in re.finditer(pattern, syntax):
            binding = re.escape(match.group(1))
            if (
                re.search(
                    rf"\b{binding}\s*(?:(?:\?\.|\.)\s*{names}\b|"
                    rf"(?:\?\.)?\s*\[\s*[\"']{names}[\"']\s*\])",
                    syntax,
                )
                or re.search(
                    rf"\{{[^}}\r\n]*\b{names}\b[^}}\r\n]*\}}\s*=\s*\b{binding}\b",
                    syntax,
                )
                or re.search(
                    rf"\b{binding}\s*\.\s*then\s*\([\s\S]{{0,4096}}?"
                    rf"\{{[^}}\r\n]*\b{names}\b[^}}\r\n]*\}}",
                    syntax,
                )
            ):
                return True
    return False


def _next_experimental_ppr_residual(text: str) -> bool:
    """Detect static and simply aliased experimental_ppr configuration keys."""
    code = _javascript_code_mask(text)
    syntax = _javascript_migration_syntax_mask(text)
    if (
        re.search(r"(?:\bexport\s+)?\b(?:const|let|var)\s+experimental_ppr\s*=", code)
        or re.search(r"\bexperimental_ppr\b\s*:", code)
        or re.search(
            r"(?:[\"']experimental_ppr[\"']|\[\s*[\"']experimental_ppr[\"']\s*\])\s*:",
            syntax,
        )
    ):
        return True
    for match in re.finditer(
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[\"']experimental_ppr[\"']",
        syntax,
    ):
        if re.search(rf"\[\s*{re.escape(match.group(1))}\s*\]\s*:", code):
            return True
    return False


def _next_font_residual(text: str) -> bool:
    """Detect literal and simply aliased dynamic references to the removed @next/font package."""
    syntax = _javascript_migration_syntax_mask(text)
    module = r"[\"']@next/font(?:/[^\"'\r\n]+)?[\"']"
    if re.search(rf"(?:\bfrom\s+|\brequire\s*\(|\bimport\s*\()\s*{module}", syntax):
        return True
    code = _javascript_code_mask(text)
    for match in re.finditer(rf"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*{module}", syntax):
        if re.search(rf"\b(?:require|import)\s*\(\s*{re.escape(match.group(1))}\s*\)", code):
            return True
    return False


def _next_known_residuals(root: Path, facts: ProjectFacts, target: str) -> bool:
    package = _root_package_json(root, facts)
    scripts = package[1].get("scripts", {}) if package is not None else None
    if package is None or not isinstance(scripts, dict):
        return True
    if any(
        isinstance(command, str) and re.search(r"\bnext\s+lint\b", command)
        for command in scripts.values()
    ):
        return True
    dependencies = package[1].get("dependencies", {})
    development = package[1].get("devDependencies", {})
    if not isinstance(dependencies, dict) or not isinstance(development, dict):
        return True
    if "@next/font" in dependencies or "@next/font" in development:
        return True
    for relative in _root_owned_js_paths(facts):
        if Version(target).major >= 16 and Path(relative).name in {
            "middleware.js",
            "middleware.ts",
        }:
            return True
        try:
            text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        except (OSError, ValueError):
            return True
        if re.search(r"@next-codemod-error\b", text):
            return True
        if _next_font_residual(text):
            return True
        if Version(target).major >= 16:
            if _next_experimental_ppr_residual(text):
                return True
            if _next_unstable_cache_residual(text):
                return True
    return False


def _repair_react_this_refs(root: Path, paths: list[str], _target: str | None) -> dict[str, str]:
    callback = re.compile(
        r"ref\s*=\s*\{\s*\(?\s*(?P<arg>[A-Za-z_$][\w$]*)\s*\)?\s*=>\s*\{\s*"
        r"this\.refs\.(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P=arg)\s*;?\s*\}\s*\}"
    )
    changed: dict[str, str] = {}
    for relative in paths:
        if not relative.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")):
            continue
        text = read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root)
        mask = _javascript_code_mask(text)
        names = {match.group("name") for match in callback.finditer(mask)}
        if not names:
            continue
        pattern = re.compile(
            r"\bthis\.refs\.("
            + "|".join(sorted(map(re.escape, names), key=len, reverse=True))
            + r")\b"
        )
        output: list[str] = []
        cursor = 0
        for match in pattern.finditer(mask):
            output.extend((text[cursor : match.start()], "this." + match.group(1)))
            cursor = match.end()
        output.append(text[cursor:])
        rendered = "".join(output)
        if rendered != text:
            changed[relative] = rendered
    return changed


REACT_CODEMOD_TOOL = _authoritative_tool(
    tool_id="react-codemod",
    provider="Codemod CLI and React codemods",
    host_executable="codemod",
    container_executable="codemod",
    tool_version=">=1.18.3,<2",
    runner_package="codemod",
    runner_version="1.18.3",
    runner_integrity=(
        "sha512-cG3RcyPSWF0/mByubQ9/TdO21X1uiVV/V7kBpqCJjlb48hm0RdDTCZSj/"
        "lvw5xKk5wVddGb3uhEetBVPeYNkRw=="
    ),
    recipe="react/19/migration-recipe",
    recipe_version="0.1.5",
    authoritative_source="https://react.dev/blog/2024/04/25/react-19-upgrade-guide",
    tool_license="Apache-2.0",
    recipe_license="MIT (react-codemod source); registry artifact invoked, not redistributed",
    image="blueprint-tools/react-codemod:1.18.3",
    network="required",
    allowed_destinations=["registry.npmjs.org", "app.codemod.com"],
    mounts=["cache", "config", "ca"],
    noninteractive_flags=["--no-interactive", "--disable-analytics"],
    preview_supported=True,
    expected_write_scopes=[
        "package.json",
        "src/**/*.js",
        "src/**/*.jsx",
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.mjs",
        "src/**/*.mts",
        "src/**/*.cjs",
        "src/**/*.cts",
    ],
    timeout_seconds=900,
    memory_mb=2048,
    scratch_mb=2048,
    file_size_mb=512,
)


def _react_pipeline(
    root: Path, facts: ProjectFacts, paths: list[str], target: str
) -> list[RuntimePipelineStage]:
    versions = _exact_npm_dependencies(root, facts)
    current_react = versions.get("react")
    current_dom = versions.get("react-dom")
    source_paths = [path for path in paths if path != "package.json"]
    residual = _react_known_residuals(root, source_paths)
    limitations = [
        "The pinned Codemod runner resolves the separately versioned registry recipe only at "
        "runtime; Blueprint AI cannot verify that recipe before it sees a writable source tree, "
        "so automatic execution is refused"
    ]
    if (current_react, current_dom) not in {("18.3.1", "18.3.1"), (target, target)}:
        limitations.append(
            "React recommends running the 18.3 warning release before the React 19 migration"
        )
    if residual:
        limitations.append(
            "Removed React APIs remain; the deterministic this.refs repair stays available only "
            "as a tested completion primitive after an independently trusted codemod result"
        )
    return [
        RuntimePipelineStage(
            id="verified-recipe-acquisition",
            kind="manual-boundary",
            operation="verified-recipe-acquisition",
            files=paths,
            mutates=False,
            status="failed-precondition",
            limitations=limitations,
        )
    ]


def _next_migration_paths(root: Path, facts: ProjectFacts) -> list[str]:
    versions = _exact_npm_dependencies(root, facts)
    current = versions.get("next")
    react = versions.get("react")
    react_dom = versions.get("react-dom")
    package = _root_package_json(root, facts)
    dependencies = package[1].get("dependencies", {}) if package is not None else {}
    development = package[1].get("devDependencies", {}) if package is not None else {}
    if (
        not current
        or not react
        or not react_dom
        or not isinstance(dependencies, dict)
        or not isinstance(development, dict)
        or dependencies.get("next") != current
        or dependencies.get("react") != react
        or dependencies.get("react-dom") != react_dom
        or "next" in development
        or "react" in development
        or "react-dom" in development
        or "@next/font" in development
        or any(name in dependencies for name in ("@types/react", "@types/react-dom"))
        or "eslint-config-next" in dependencies
        or any(
            name in development and name not in versions
            for name in ("@types/react", "@types/react-dom")
        )
        or ("eslint-config-next" in development and "eslint-config-next" not in versions)
        or Version(current).major not in {14, 15, 16}
        or _owned_manifests(facts, lambda path: Path(path).name == "package.json")
        != ["package.json"]
        or not ((root / "app").is_dir() or (root / "src/app").is_dir())
        or any(
            (root / name).is_file()
            for name in (
                "package-lock.json",
                "npm-shrinkwrap.json",
                "yarn.lock",
                "pnpm-lock.yaml",
                "bun.lock",
                "bun.lockb",
            )
        )
    ):
        return []
    return sorted(
        {
            "package.json",
            "package-lock.json",
            "eslint.config.mjs",
            "eslint.config.mts",
            "eslint.config.cts",
            *_root_owned_js_paths(facts),
        }
    )


def _next_current_target(root: Path, facts: ProjectFacts) -> str | None:
    versions = _exact_npm_dependencies(root, facts)
    package = _root_package_json(root, facts)
    dependencies = package[1].get("dependencies", {}) if package is not None else {}
    development = package[1].get("devDependencies", {}) if package is not None else {}
    current = versions.get("next")
    react = versions.get("react")
    react_dom = versions.get("react-dom")
    if (
        not current
        or _owned_manifests(facts, lambda path: Path(path).name == "package.json")
        != ["package.json"]
        or not isinstance(dependencies, dict)
        or not isinstance(development, dict)
        or dependencies.get("next") != current
        or dependencies.get("react") != REACT_19_TARGET
        or dependencies.get("react-dom") != REACT_19_TARGET
        or react != REACT_19_TARGET
        or react_dom != REACT_19_TARGET
        or "next" in development
        or "@next/font" in dependencies
        or "@next/font" in development
        or ("eslint-config-next" in development and versions.get("eslint-config-next") != current)
        or any(
            name in development and versions.get(name) != REACT_19_TARGET
            for name in ("@types/react", "@types/react-dom")
        )
    ):
        return None
    return current if not _next_known_residuals(root, facts, current) else None


def _next_proxy_scope(paths: list[str]) -> list[str]:
    scoped = set(paths)
    for path in paths:
        name = Path(path).name
        if name in {"middleware.js", "middleware.ts"}:
            scoped.add(str(Path(path).with_name("proxy" + Path(path).suffix)))
        elif name in {"proxy.js", "proxy.ts"}:
            scoped.add(str(Path(path).with_name("middleware" + Path(path).suffix)))
    return sorted(scoped)


def _replace_next_version(root: Path, _paths: list[str], target: str | None) -> dict[str, str]:
    if target not in {"15.5.25", "16.3.5"}:
        raise ValueError("Next.js dependency stage has an unsupported target")
    relative = "package.json"
    document = json.loads(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
    before = json.dumps(document, sort_keys=True, separators=(",", ":"))
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, dict) or any(
        name not in dependencies for name in ("next", "react", "react-dom")
    ):
        raise ValueError("root package.json has no owned Next.js and React dependency set")
    dependencies["next"] = target
    dependencies["react"] = REACT_19_TARGET
    dependencies["react-dom"] = REACT_19_TARGET
    dependencies.pop("@next/font", None)
    dev_dependencies = document.get("devDependencies")
    if "eslint-config-next" in dependencies:
        raise ValueError("eslint-config-next must be an owned development dependency")
    if isinstance(dev_dependencies, dict) and (
        "next" in dev_dependencies or "@next/font" in dev_dependencies
    ):
        raise ValueError("ambiguous Next.js dependencies crossed manifest sections")
    if isinstance(dev_dependencies, dict) and "eslint-config-next" in dev_dependencies:
        dev_dependencies["eslint-config-next"] = target
    if json.dumps(document, sort_keys=True, separators=(",", ":")) == before:
        return {}
    return {relative: json.dumps(document, indent=2, ensure_ascii=False) + "\n"}


def _replace_react_type_versions(
    root: Path, _paths: list[str], target: str | None
) -> dict[str, str]:
    if target != REACT_19_TARGET:
        raise ValueError("React type dependency stage has an unsupported target")
    relative = "package.json"
    document = json.loads(read_text_bounded(root / relative, MAX_MANIFEST_BYTES, root=root))
    dependencies = document.get("dependencies", {})
    development = document.get("devDependencies", {})
    if not isinstance(dependencies, dict) or not isinstance(development, dict):
        raise ValueError("package dependency sections are malformed")
    if any(name in dependencies for name in ("@types/react", "@types/react-dom")):
        raise ValueError("React type packages must be owned development dependencies")
    changed = False
    for name in ("@types/react", "@types/react-dom"):
        if name in development:
            if not isinstance(development[name], str) or not re.fullmatch(
                r"\d+\.\d+\.\d+", development[name]
            ):
                raise ValueError("React type packages require exact owned versions")
            changed |= development[name] != target
            development[name] = target
    return {relative: json.dumps(document, indent=2, ensure_ascii=False) + "\n"} if changed else {}


NEXT_CODEMOD_TOOL = _authoritative_tool(
    tool_id="next-codemod",
    provider="Vercel Next.js codemod",
    host_executable="codemod",
    container_executable="codemod",
    tool_version=">=16.3.5,<17",
    runner_package="@next/codemod",
    runner_version="16.3.5",
    runner_integrity=(
        "sha512-uUzPcoYjSPv2YXX1k3BOh9rzDZ+AOGrqIzzZPyVVruUal9a6kRZoPFuYGaNKmvO"
        "aiWrNifEuvDfHukDZ/UgKyw=="
    ),
    recipe="reviewed major-boundary transforms",
    recipe_version="16.3.5",
    authoritative_source="https://nextjs.org/docs/app/guides/upgrading/codemods",
    tool_license="MIT",
    recipe_license="MIT",
    image="blueprint-tools/next-codemod:16.3.5",
    network="acquisition-only",
    allowed_destinations=["registry.npmjs.org"],
    mounts=["cache", "config", "ca"],
    noninteractive_flags=["CI=1", "--force"],
    preview_supported=True,
    expected_write_scopes=[
        "package.json",
        "package-lock.json",
        "eslint.config.mjs",
        "eslint.config.mts",
        "eslint.config.cts",
        "**/*.js",
        "**/*.jsx",
        "**/*.ts",
        "**/*.tsx",
        "**/*.mjs",
        "**/*.mts",
        "**/*.cjs",
        "**/*.cts",
    ],
    timeout_seconds=900,
    memory_mb=2048,
)


def _next_pipeline(
    root: Path, facts: ProjectFacts, paths: list[str], target: str
) -> list[RuntimePipelineStage]:
    current = _exact_npm_dependencies(root, facts).get("next")
    if current is None:
        return []
    current_version = Version(current)
    target_version = Version(target)
    if current_version > target_version:
        raise ValueError("Next.js downgrades are outside the authoritative migration lane")
    package = _root_package_json(root, facts)
    dependencies = package[1].get("dependencies", {}) if package is not None else {}
    development = package[1].get("devDependencies", {}) if package is not None else {}
    type_names = [
        name
        for name in ("@types/react", "@types/react-dom")
        if isinstance(development, dict) and name in development
    ]
    needs_type_update = any(
        isinstance(development, dict) and development.get(name) != REACT_19_TARGET
        for name in type_names
    )
    if target_version.major >= 16 and any(
        (root / name).is_file()
        for name in (
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yaml",
            ".eslintrc.yml",
        )
    ):
        return [
            RuntimePipelineStage(
                id="legacy-eslint-config",
                kind="manual-boundary",
                operation="legacy-eslint-config",
                files=paths,
                mutates=False,
                status="failed-precondition",
                limitations=[
                    "Next 16's lint codemod delegates legacy ESLint conversion to an unpinned "
                    "networked package; migrate to a flat ESLint config first"
                ],
            )
        ]
    edge_middleware = []
    if target_version.major >= 16:
        for path in paths:
            if Path(path).name not in {"middleware.js", "middleware.ts"}:
                continue
            text = read_text_bounded(root / path, MAX_MANIFEST_BYTES, root=root)
            if re.search(
                r"\bexport\s+const\s+runtime\s*=\s*[\"'](?:experimental-)?edge[\"']",
                _javascript_migration_syntax_mask(text),
            ):
                edge_middleware.append(path)
    if edge_middleware:
        return [
            RuntimePipelineStage(
                id="edge-middleware-runtime",
                kind="manual-boundary",
                operation="edge-middleware-runtime",
                files=paths,
                mutates=False,
                status="failed-precondition",
                limitations=[
                    "Next 16 Proxy does not support the Edge runtime; retain middleware or "
                    "choose a runtime explicitly before migration: " + ", ".join(edge_middleware)
                ],
            )
        ]
    conventions = [
        path
        for path in paths
        if Path(path).name in {"middleware.js", "middleware.ts", "proxy.js", "proxy.ts"}
    ]
    middleware_paths = [path for path in conventions if Path(path).name.startswith("middleware.")]
    proxy_paths = [path for path in conventions if Path(path).name.startswith("proxy.")]
    app_roots = [name for name in ("app", "src/app") if (root / name).is_dir()]
    expected_parent = "src" if app_roots == ["src/app"] else "."
    misplaced = [path for path in conventions if Path(path).parent.as_posix() != expected_parent]
    ambiguous_conventions = (
        sorted(set([*conventions, *app_roots]))
        if len(app_roots) != 1
        or len(middleware_paths) > 1
        or bool(middleware_paths and proxy_paths)
        or bool(misplaced)
        else []
    )
    if target_version.major >= 16 and ambiguous_conventions:
        return [
            RuntimePipelineStage(
                id="proxy-path-collision",
                kind="manual-boundary",
                operation="proxy-path-collision",
                files=paths,
                mutates=False,
                status="failed-precondition",
                limitations=[
                    "Next 16 middleware-to-proxy has ambiguous sibling convention files: "
                    + ", ".join(sorted(ambiguous_conventions))
                ],
            )
        ]
    fully_current = (
        current_version == target_version
        and isinstance(dependencies, dict)
        and dependencies.get("react") == REACT_19_TARGET
        and dependencies.get("react-dom") == REACT_19_TARGET
        and "@next/font" not in dependencies
        and isinstance(development, dict)
        and "@next/font" not in development
        and (
            "eslint-config-next" not in development
            or development.get("eslint-config-next") == target
        )
        and all(
            isinstance(development, dict) and development.get(name) == REACT_19_TARGET
            for name in type_names
        )
        and not _next_known_residuals(root, facts, target)
    )
    if fully_current:
        return [
            RuntimePipelineStage(
                id="project-verification",
                kind="manual-boundary",
                operation="project-verification",
                files=paths,
                mutates=False,
                manual_completion_paths=[
                    "package-lock.json",
                    "npm-shrinkwrap.json",
                    "pnpm-lock.yaml",
                    "yarn.lock",
                    "bun.lock",
                    "bun.lockb",
                ],
                status="manual",
                limitations=[
                    "A lockfile-backed install and project build/type/lint/test evidence are "
                    "required before this target can be accepted"
                ],
            )
        ]
    boundaries: list[str] = []
    if current_version.major == 14 and target_version.major >= 15:
        boundaries.append("15.5.25")
    elif current_version.major == 15 and target_version.major == 15:
        boundaries.append(target)
    elif current_version == target_version and target_version.major < 16:
        boundaries.append(target)
    if target_version.major >= 16:
        boundaries.append("16.3.5")
    stages: list[RuntimePipelineStage] = []
    previous: str | None = None
    needs_v16_transforms = current_version.major < 16 or _next_known_residuals(root, facts, target)
    if type_names and needs_type_update:
        type_identifier = "react-type-dependencies"
        stages.append(
            RuntimePipelineStage(
                id=type_identifier,
                kind="builtin-edit",
                operation="react-type-dependencies",
                operation_target=REACT_19_TARGET,
                files=["package.json"],
                rediscover=True,
                postconditions=[
                    _postcondition(
                        "next/types/" + name.replace("@types/", ""),
                        "exact-value",
                        f"{name} reached the reviewed React 19 type release",
                        paths=["package.json"],
                        selector=f"json:devDependencies.{name}",
                        expected=REACT_19_TARGET,
                    )
                    for name in type_names
                ],
            )
        )
        previous = type_identifier
    for boundary in boundaries:
        boundary_paths = _next_proxy_scope(paths) if Version(boundary).major == 16 else paths
        transforms = (
            ["built-in-next-font", "next-async-request-api"]
            if Version(boundary).major == 15 and current_version.major == 14
            else [
                "next-async-request-api",
                "next-lint-to-eslint-cli",
                "middleware-to-proxy",
                "remove-experimental-ppr",
                "remove-unstable-prefix",
            ]
            if Version(boundary).major == 16 and needs_v16_transforms
            else []
        )
        for transform_name in transforms:
            identifier = f"{transform_name}-{boundary.replace('.', '-')}"
            command = ["codemod", transform_name, ".", "--force"]
            if transform_name == "built-in-next-font":
                command = [
                    "node",
                    (
                        "/usr/local/lib/node_modules/@next/codemod/node_modules/"
                        "jscodeshift/bin/jscodeshift.js"
                    ),
                    "--parser=tsx",
                    "--ignore-pattern=**/node_modules/**",
                    "--ignore-pattern=**/.next/**",
                    "--extensions=tsx,ts,jsx,js",
                    "--transform",
                    ("/usr/local/lib/node_modules/@next/codemod/transforms/built-in-next-font.js"),
                    ".",
                ]
            elif transform_name == "next-lint-to-eslint-cli":
                command = [
                    "node",
                    "-e",
                    (
                        "const t=require('/usr/local/lib/node_modules/@next/codemod/"
                        "transforms/next-lint-to-eslint-cli.js').default;"
                        "Promise.resolve(t(['.'],{skipInstall:true}))"
                        ".catch(e=>{console.error(e);process.exit(1)})"
                    ),
                ]
            stages.append(
                RuntimePipelineStage(
                    id=identifier,
                    kind="established-codemod",
                    operation=identifier,
                    operation_target=boundary,
                    files=boundary_paths,
                    tool="next-codemod",
                    apply_command=command,
                    depends_on=[previous] if previous else [],
                    ephemeral_paths=["node_modules"],
                    rediscover=True,
                )
            )
            previous = identifier
        identifier = "dependencies-" + boundary.replace(".", "-")
        source_paths = [
            path
            for path in boundary_paths
            if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"))
        ]
        stage_postconditions = [
            _postcondition(
                f"next/version-{boundary}",
                "exact-value",
                f"Next.js reached the explicit boundary {boundary}",
                paths=["package.json"],
                selector="json:dependencies.next",
                expected=boundary,
            ),
            _postcondition(
                f"next/react-version-{boundary}",
                "exact-value",
                "React reached the reviewed Next.js compatibility target",
                paths=["package.json"],
                selector="json:dependencies.react",
                expected=REACT_19_TARGET,
            ),
            _postcondition(
                f"next/react-dom-version-{boundary}",
                "exact-value",
                "ReactDOM reached the reviewed Next.js compatibility target",
                paths=["package.json"],
                selector="json:dependencies.react-dom",
                expected=REACT_19_TARGET,
            ),
            _postcondition(
                f"next/no-codemod-error-{boundary}",
                "no-manual-markers",
                "The authoritative codemod emitted no unresolved manual-error markers",
                paths=source_paths,
                pattern=r"@next-codemod-error\b",
            ),
        ]
        if Version(boundary).major >= 16 and (
            needs_v16_transforms
            or isinstance(development, dict)
            and "eslint-config-next" in development
        ):
            stage_postconditions.append(
                _postcondition(
                    f"next/eslint-config-version-{boundary}",
                    "exact-value",
                    "eslint-config-next matches the selected Next.js boundary",
                    paths=["package.json"],
                    selector="json:devDependencies.eslint-config-next",
                    expected=boundary,
                )
            )
        if Version(boundary).major >= 16:
            stage_postconditions.extend(
                [
                    _postcondition(
                        "next/no-experimental-ppr",
                        "forbidden-pattern",
                        "Removed experimental_ppr configuration is absent",
                        paths=[
                            path
                            for path in source_paths
                            if Path(path).name.startswith("next.config.")
                        ]
                        or ["next.config.js"],
                        pattern=r"removed-next-experimental-ppr",
                        selector="next-experimental-ppr",
                    ),
                    _postcondition(
                        "next/no-unstable-prefix",
                        "forbidden-pattern",
                        "Next 16 cache APIs handled by the pinned recipe use stable names",
                        paths=source_paths,
                        pattern=r"removed-next-cache-api",
                        selector="next-unstable-cache",
                    ),
                ]
            )
            middleware_paths = [
                path for path in paths if Path(path).name in {"middleware.js", "middleware.ts"}
            ]
            if middleware_paths:
                stage_postconditions.extend(
                    [
                        _postcondition(
                            "next/middleware-path-removed",
                            "path-absent",
                            "Owned middleware entry point was renamed",
                            paths=middleware_paths,
                        ),
                        _postcondition(
                            "next/proxy-path-created",
                            "path-present",
                            "The corresponding Next.js Proxy entry point exists",
                            paths=[
                                str(Path(path).with_name("proxy" + Path(path).suffix))
                                for path in middleware_paths
                            ],
                        ),
                    ]
                )
        if Version(boundary).major >= 15:
            stage_postconditions.append(
                _postcondition(
                    f"next/no-next-font-{boundary}",
                    "forbidden-pattern",
                    "Deprecated @next/font imports are absent",
                    paths=[
                        path
                        for path in boundary_paths
                        if path.endswith(
                            (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
                        )
                    ],
                    pattern=r"removed-next-font",
                    selector="next-font",
                )
            )
            stage_postconditions.append(
                _postcondition(
                    f"next/no-next-font-dependency-{boundary}",
                    "forbidden-pattern",
                    "Deprecated @next/font dependency entries are absent",
                    paths=["package.json"],
                    pattern=r"@next/font",
                    selector="json-dependencies",
                )
            )
        if Version(boundary).major >= 16:
            stage_postconditions.append(
                _postcondition(
                    "next/no-next-lint",
                    "forbidden-pattern",
                    "Removed next lint script is absent",
                    paths=["package.json"],
                    pattern=r"\bnext\s+lint\b",
                    selector="json-scripts",
                )
            )
        needs_dependency_update = (
            current != boundary
            or not isinstance(dependencies, dict)
            or dependencies.get("react") != REACT_19_TARGET
            or dependencies.get("react-dom") != REACT_19_TARGET
            or "@next/font" in dependencies
            or not isinstance(development, dict)
            or "@next/font" in development
            or (
                "eslint-config-next" in development
                and development.get("eslint-config-next") != boundary
            )
            or needs_v16_transforms
        )
        stages.append(
            RuntimePipelineStage(
                id=identifier,
                kind="builtin-edit" if needs_dependency_update else "postcondition",
                operation="next-dependencies",
                operation_target=boundary,
                files=["package.json"],
                depends_on=[previous] if previous else [],
                rediscover=True,
                mutates=needs_dependency_update,
                postconditions=stage_postconditions,
            )
        )
        previous = identifier
    stages.append(
        RuntimePipelineStage(
            id="project-verification",
            kind="manual-boundary",
            operation="project-verification",
            files=paths,
            mutates=False,
            manual_completion_paths=[
                "package-lock.json",
                "npm-shrinkwrap.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "bun.lock",
                "bun.lockb",
            ],
            depends_on=[previous] if previous else [],
            status="manual",
            limitations=[
                "Install the resulting lockfile and run Next build/type/lint/test before acceptance"
            ],
        )
    )
    return stages


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
                provider="Ruff with Blueprint AI bounded export preservation",
                tool="ruff",
                tool_version=">=0.13,<1",
                recipe_version="2",
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
                        "Ruff UP and bounded import cleanup produce no remaining safe changes",
                        command=["ruff", "check", "--select", "UP,F401,I001", "--diff"],
                        trust=True,
                    ),
                    _verification(
                        "python/ruff-pyupgrade",
                        "python-parse",
                        "Changed Python files parse successfully",
                    ),
                    _verification(
                        "python/ruff-pyupgrade",
                        "package-exports",
                        "Newly unused package initializer typing names remain explicit exports",
                    ),
                ],
                maturity="supported",
                mechanism="official-native",
                implementation="command",
                decision="wrap",
                limitations=[
                    "Does not change declared Python or dependency versions",
                    "Package initializer typing imports are retained as explicit public exports",
                ],
                reviewed="2026-09-12",
            ),
            detect=lambda root, facts: (
                _source_files(".py", scopes=python_scopes)(root, facts)
                + _source_files(".pyi", scopes=python_scopes)(root, facts)
                if _has_python_target(root)
                else []
            ),
            preview_command=lambda root, paths: _ruff_command(root, paths, "UP", "--diff"),
            apply_command=lambda root, paths: _ruff_command(root, paths, "UP", "--fix-only"),
            verify_command=lambda root, paths: _ruff_command(root, paths, "UP,F401,I001", "--diff"),
            preflight_command=lambda root, paths: _ruff_command(root, paths, "F401,I001"),
            cleanup_command=lambda root, paths: _ruff_command(
                root, paths, "F401,I001", "--fix-only"
            ),
            transform=_preserve_init_typing_exports,
            staged_preview=True,
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
        RuntimeTransformation(
            spec=_spec(
                id="rust/edition",
                category="language-modernization",
                current_state="one exact owned Rust 2018 or 2021 edition",
                desired_state="explicit requested Rust edition reached one boundary at a time",
                applicability=["one root Cargo package with one exact explicit edition"],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider="Rust toolchain plus Blueprint AI manifest edit",
                tool="cargo",
                tool_version=">=1.85,<2",
                allowed_targets=["2021", "2024"],
                target_required=True,
                tool_contract=RUST_TOOL,
                recipe_version="2",
                authoritative_source=RUST_TOOL.authoritative_source,
                license="MIT OR Apache-2.0",
                sandbox_requirements=(
                    "trusted writable host or pinned Rust OCI image; network denied"
                ),
                preconditions=[
                    "exact current edition and explicit later target",
                    "Cargo.lock is present for locked offline execution",
                    "dependencies are cached, vendored, or absent",
                ],
                conflicts=[
                    "nested packages, workspaces, mixed editions, inherited editions, and "
                    "inactive cfg states are rejected"
                ],
                expected_paths=["Cargo.toml", "Cargo.lock", "**/*.rs"],
                dry_run=True,
                reversible=True,
                verification=[],
                postconditions=RUST_TOOL.postconditions,
                maturity="supported",
                mechanism="official-native",
                implementation="command",
                decision="adapt",
                limitations=[
                    "Only a single root package is supported; workspaces and independent nested "
                    "packages require component-aware command evidence",
                    "Inactive cfg combinations outside all features/targets may need separate "
                    "checks",
                ],
                reviewed="2026-09-12",
            ),
            detect=_rust_owned_paths,
            pipeline=_rust_pipeline,
            current_target=_rust_current_target,
            operations={"rust-edition": _replace_rust_edition},
        ),
        RuntimeTransformation(
            spec=_spec(
                id="dotnet/sdk-target",
                category="runtime-modernization",
                current_state="one dependency-free Microsoft.NET.Sdk project below net10.0",
                desired_state="explicit net10.0 target restored and built with .NET SDK 10",
                applicability=[
                    "one exact owned Microsoft.NET.Sdk .csproj",
                    "one literal TargetFramework and no PackageReference",
                ],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider="Blueprint AI XML scalar edit plus Microsoft .NET SDK",
                tool="dotnet",
                tool_version=">=10,<11",
                allowed_targets=["net10.0"],
                target_required=True,
                tool_contract=DOTNET_TOOL,
                recipe_version="1",
                authoritative_source="https://learn.microsoft.com/en-us/dotnet/core/porting/",
                license="MIT",
                sandbox_requirements=(
                    "trusted writable host or pinned SDK OCI image; network denied"
                ),
                preconditions=["explicit net10.0 target", "simple SDK-style project evidence"],
                conflicts=[
                    "multi-target, inherited, conditional, package-dependent, Web SDK, "
                    "and .NET Framework projects"
                ],
                expected_paths=["*.csproj"],
                dry_run=True,
                reversible=True,
                verification=[],
                maturity="supported",
                mechanism="builtin-structural",
                implementation="command",
                decision="adapt",
                limitations=[
                    "Dependency/API modernization and complex application models remain "
                    "explicit residuals"
                ],
                reviewed="2026-09-12",
            ),
            detect=_simple_dotnet_paths,
            pipeline=_dotnet_pipeline,
            current_target=_dotnet_current_target,
            operations={"dotnet-tfm": _replace_dotnet_tfm},
        ),
        RuntimeTransformation(
            spec=_spec(
                id="python/pep517-build-system",
                category="packaging-modernization",
                current_state=(
                    "setuptools setup.py/setup.cfg packaging without build-system metadata"
                ),
                desired_state=(
                    "explicit setuptools PEP 517 build-system metadata with legacy setup preserved"
                ),
                applicability=["owned legacy setuptools project without a conflicting backend"],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider="Blueprint AI structural TOML edit plus PyPA build interface",
                tool="python-build",
                tool_version=">=3.12,<4",
                allowed_targets=["setuptools.build_meta"],
                target_required=True,
                tool_contract=PYTHON_BUILD_TOOL,
                recipe_version="1",
                authoritative_source=PYTHON_BUILD_TOOL.authoritative_source,
                license="MIT",
                sandbox_requirements=(
                    "trusted writable host or pinned Python OCI image; network denied"
                ),
                preconditions=[
                    "explicit setuptools.build_meta target",
                    "setuptools is evidenced by setup.py/setup.cfg",
                ],
                conflicts=[
                    "existing or conflicting build backend and ambiguous non-setuptools setup"
                ],
                expected_paths=["pyproject.toml"],
                dry_run=True,
                reversible=True,
                verification=[],
                maturity="supported",
                mechanism="builtin-structural",
                implementation="command",
                decision="native",
                limitations=[
                    "Preserves setup.py/setup.cfg and does not infer PEP 621 metadata or "
                    "uv workflow semantics"
                ],
                reviewed="2026-09-12",
            ),
            detect=_python_legacy_build_paths,
            pipeline=_python_build_pipeline,
            current_target=_python_current_build_backend,
            operations={"python-build-system": _add_setuptools_build_system},
        ),
        RuntimeTransformation(
            spec=_spec(
                id="react/19-official-codemods",
                category="framework-modernization",
                current_state="exact React/ReactDOM release before 19 outside Next.js",
                desired_state="explicit React 19 dependencies and verified React 19 API patterns",
                applicability=[
                    "root package.json with exact React and ReactDOM versions",
                    "source-root JavaScript/TypeScript project without a lockfile",
                ],
                lifecycle=["active-application", "library-framework", "unknown"],
                provider=(
                    "React-recommended Codemod registry acquisition boundary plus bounded repair"
                ),
                tool="react-codemod",
                tool_version=">=1.18.3,<2",
                allowed_targets=["19.3.0"],
                target_required=True,
                tool_contract=REACT_CODEMOD_TOOL,
                recipe_version="3",
                authoritative_source=REACT_CODEMOD_TOOL.authoritative_source,
                license="Apache-2.0 runner; MIT React recipe",
                network_required=True,
                sandbox_requirements=(
                    "automatic execution refused because the separately resolved registry recipe "
                    "cannot be integrity-verified before source mutation"
                ),
                preconditions=[
                    "React and ReactDOM are exactly 18.3.1",
                    "explicit React 19.3.0 target",
                    "dependency compatibility has been assessed",
                ],
                conflicts=[
                    "Next.js owns its React upgrade path",
                    "lockfile updates are not inferred",
                ],
                expected_paths=["package.json", "src/**/*.{js,jsx,ts,tsx}"],
                dry_run=False,
                reversible=False,
                verification=[],
                maturity="partial",
                mechanism="established-codemod",
                implementation="command",
                decision="adapt",
                limitations=[
                    "React before 18.3 stops at the official transitional prerequisite",
                    "Automatic recipe execution is refused until the registry artifact can be "
                    "verified before mutation",
                    "The this.refs repair is a tested completion primitive, not automatic support",
                ],
                reviewed="2026-09-12",
            ),
            detect=_react_migration_paths,
            pipeline=_react_pipeline,
            current_target=_react_current_target,
            operations={
                "react-dependencies": _replace_package_versions,
                "react-type-dependencies": _replace_react_type_versions,
                "react-this-refs": _repair_react_this_refs,
            },
        ),
        RuntimeTransformation(
            spec=_spec(
                id="next/official-upgrade-codemod",
                category="framework-modernization",
                current_state="exact Next.js 14 or 15 App Router project without a lockfile",
                desired_state="explicit supported Next.js target reached major by major",
                applicability=[
                    "root package.json with an exact Next.js version",
                    "one package component with owned App Router source and no lockfile",
                ],
                lifecycle=["active-application", "unknown"],
                provider="Vercel @next/codemod",
                tool="next-codemod",
                tool_version=">=16.3.5,<17",
                allowed_targets=["15.5.25", "16.3.5"],
                target_required=True,
                tool_contract=NEXT_CODEMOD_TOOL,
                recipe_version="16.3.5",
                authoritative_source=NEXT_CODEMOD_TOOL.authoritative_source,
                license="MIT",
                network_required=False,
                sandbox_requirements="trusted writable pinned @next/codemod OCI sandbox",
                preconditions=[
                    "exact current and target versions",
                    "managed offline codemod image",
                ],
                conflicts=[
                    "nested or workspace package components",
                    "lockfile/package-manager ambiguity",
                    "project-specific cache semantics",
                ],
                expected_paths=[
                    "package.json",
                    "package-lock.json",
                    "eslint.config.mjs",
                    "eslint.config.mts",
                    "eslint.config.cts",
                    "app/**/*",
                    "src/app/**/*",
                    "next.config.*",
                ],
                dry_run=True,
                reversible=True,
                verification=[],
                maturity="partial",
                mechanism="established-codemod",
                implementation="command",
                decision="adapt",
                limitations=[
                    "Build/type/lint/test acceptance remains manual after the codemod prefix",
                    "Nested and workspace package components are rejected because the pinned "
                    "codemod command targets the repository root",
                    "Only reviewed 14→15 and 15→16 boundaries are represented",
                ],
                reviewed="2026-09-12",
            ),
            detect=_next_migration_paths,
            pipeline=_next_pipeline,
            current_target=_next_current_target,
            operations={
                "next-dependencies": _replace_next_version,
                "react-type-dependencies": _replace_react_type_versions,
            },
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
            "dotnet/complex-modernization-agent",
            "runtime-modernization",
            f".NET target framework before {DOTNET_TFM_TARGET}",
            "supported .NET target",
            "GitHub Copilot modernization agent",
            "https://learn.microsoft.com/en-us/dotnet/core/porting/upgrade-assistant-overview",
            "service-specific",
            "deferred",
            "agent-assist",
            lambda f: False,
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
        "dotnet/complex-modernization-agent": "agent-implementation",
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
        "dotnet/complex-modernization-agent",
        "iac/terraform-to-opentofu",
    }
    model_planning = {
        "java/openrewrite-lts",
        "react/create-react-app-foundation",
        "dotnet/complex-modernization-agent",
        "iac/terraform-to-opentofu",
    }
    transformations = []
    versioned_detection: dict[str, Detect] = {}
    versioned_applicability: dict[str, list[str]] = {}
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
        detect = versioned_detection.get(recipe_id)
        if detect is None:
            assert predicate is not None
            detect = _manual(predicate)
        transformations.append(
            RuntimeTransformation(
                spec=_spec(
                    id=recipe_id,
                    category=category,
                    current_state=current,
                    desired_state=desired,
                    applicability=versioned_applicability.get(
                        recipe_id,
                        ["detected ecosystem; exact applicability remains unresolved"],
                    ),
                    provider=provider,
                    recipe_version="research-2026.09.12",
                    authoritative_source=source,
                    license=license_name,
                    network_required=recipe_id in networked,
                    sandbox_requirements=(
                        "not executable in this release; explicit future trust/network policy "
                        "required"
                    ),
                    preconditions=[
                        "Exact target, tool/recipe version, and verification contract selected"
                    ],
                    conflicts=["Semantic or stateful boundaries must be resolved before mutation"],
                    expected_paths=[],
                    dry_run=False,
                    reversible=False,
                    irreversible_boundary="No mutation is implemented in this release",
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
                detect=detect,
            )
        )
    return transformations


TRANSFORMATIONS = {item.spec.id: item for item in _runtime_catalog()}


def catalog_data() -> dict:
    return {
        "schema_version": "2.0.0",
        "transformations": {
            key: value.spec.model_dump(mode="json") for key, value in TRANSFORMATIONS.items()
        },
    }
