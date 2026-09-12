from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from pathspec import GitIgnoreSpec

from blueprint_ai.core import ProjectFacts
from blueprint_ai.discovery.graph import build_graph, graph_project_types
from blueprint_ai.safety import (
    MAX_CONFIG_BYTES,
    RawProcessResult,
    read_text_bounded,
    run_process_bytes,
)

SKIP_DIRS = {
    ".blueprint-state",
    ".next",
    ".git",
    ".blueprint-ai",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    ".terraform",
    "vendor",
}
GENERATED_SUFFIXES = {".min.js", ".min.css", ".map", ".lockb"}
MAX_PROJECT_FILES = 250_000
LANGUAGE_SUFFIXES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".h": "C/C++",
    ".cpp": "C/C++",
    ".swift": "Swift",
    ".sh": "Shell",
    ".tf": "HCL",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".md": "Markdown",
}


def _git_run(root: Path, *arguments: str, timeout: float = 5):
    try:
        return run_process_bytes(
            _git_command(root, *arguments), root, timeout, output_limit=8_000_000
        )
    except FileNotFoundError:
        # Filesystem discovery and built-in genesis do not require an installed Git client.
        return RawProcessResult(127, b"", b"Git client unavailable", False, False)


def _ignore_spec(root: Path, extra: Iterable[str]) -> GitIgnoreSpec:
    patterns = list(extra)
    ignore = root / ".gitignore"
    if ignore.is_file():
        try:
            patterns.extend(
                read_text_bounded(ignore, MAX_CONFIG_BYTES, root=root, errors="ignore").splitlines()
            )
        except (OSError, ValueError):
            pass
    patterns = [pattern for pattern in patterns[:10_000] if len(pattern) <= 1_000]
    return GitIgnoreSpec.from_lines(patterns)


def iter_project_files(root: Path, extra_ignores: Iterable[str] = ()) -> tuple[list[Path], int]:
    spec = _ignore_spec(root, extra_ignores)
    git_files = _git_files(root)
    if git_files is not None:
        if len(git_files) > MAX_PROJECT_FILES:
            raise ValueError(f"repository exceeds the {MAX_PROJECT_FILES}-file discovery limit")
        git_found = []
        ignored = 0
        for rel in git_files:
            relative = Path(rel)
            if relative.is_absolute() or ".." in relative.parts:
                ignored += 1
                continue
            path = root / rel
            if any(part in SKIP_DIRS for part in Path(rel).parts) or spec.match_file(rel):
                ignored += 1
                continue
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_size <= 2_000_000
                    and not any(path.name.endswith(s) for s in GENERATED_SUFFIXES)
                ):
                    git_found.append(path)
                else:
                    ignored += 1
            except OSError:
                ignored += 1
                continue
        ignored_process = _git_run(root, "ls-files", "-oi", "--exclude-standard", "-z")
        ignored += len([item for item in ignored_process.stdout.split(b"\0") if item])
        return sorted(set(git_found)), ignored
    found: list[Path] = []
    ignored = 0
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        kept_dirs = []
        for directory in dirs:
            rel = (rel_dir / directory).as_posix()
            if directory in SKIP_DIRS or spec.match_file(f"{rel}/"):
                ignored += 1
            else:
                kept_dirs.append(directory)
        dirs[:] = kept_dirs
        for filename in files:
            path = current_path / filename
            rel = path.relative_to(root).as_posix()
            if spec.match_file(rel) or any(filename.endswith(s) for s in GENERATED_SUFFIXES):
                ignored += 1
                continue
            try:
                if path.is_symlink() or path.stat().st_size > 2_000_000:
                    ignored += 1
                    continue
            except OSError:
                ignored += 1
                continue
            found.append(path)
            if len(found) > MAX_PROJECT_FILES:
                raise ValueError(f"repository exceeds the {MAX_PROJECT_FILES}-file discovery limit")
    return sorted(found), ignored


def _git_files(root: Path) -> list[str] | None:
    probe = _git_run(root, "rev-parse", "--show-toplevel", timeout=3)
    if (
        probe.returncode != 0
        or probe.output_truncated
        or Path(probe.stdout.decode(errors="replace").strip()).resolve() != root.resolve()
    ):
        return None
    listed = _git_run(root, "ls-files", "-co", "--exclude-standard", "-z")
    if listed.returncode != 0 or listed.output_truncated:
        return None
    return [item.decode(errors="replace") for item in listed.stdout.split(b"\0") if item]


def _git_facts(root: Path) -> tuple[bool, str | None, bool]:
    probe = _git_run(root, "rev-parse", "--show-toplevel", timeout=3)
    if (
        probe.returncode != 0
        or probe.output_truncated
        or Path(probe.stdout.decode(errors="replace").strip()).resolve() != root.resolve()
    ):
        return False, None, False
    branch_result = _git_run(root, "branch", "--show-current", timeout=3)
    branch = branch_result.stdout.decode(errors="replace").strip() or None
    dirty = bool(
        _git_run(
            root, "status", "--porcelain", "--untracked-files=normal", timeout=3
        ).stdout.strip()
    )
    return True, branch, dirty


def _git_changed_files(root: Path, base_ref: str | None = None) -> list[str]:
    changed: set[str] = set()
    compare = base_ref or "HEAD"
    commands = [
        [
            "diff",
            "--no-ext-diff",
            "--name-only",
            "--diff-filter=ACMR",
            "--end-of-options",
            compare,
            "--",
        ],
        ["status", "--porcelain", "-z", "--untracked-files=normal"],
    ]
    for index, command in enumerate(commands):
        try:
            result = _git_run(root, *command)
        except OSError:
            continue
        if result.returncode != 0 or result.output_truncated:
            continue
        if index == 1:
            for item in result.stdout.split(b"\0"):
                if len(item) > 3:
                    changed.add(item[3:].decode(errors="replace"))
        else:
            changed.update(
                item.decode(errors="replace") for item in result.stdout.splitlines() if item
            )
    return sorted(changed)


def _git_command(root: Path, *arguments: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.external=",
        "-C",
        str(root),
        *arguments,
    ]


def _discover_kubernetes(files: list[Path], rels: list[str]) -> list[str]:
    kubernetes = []
    for path, rel in zip(files, rels, strict=True):
        lower = rel.lower()
        if Path(rel).name.lower() in {"chart.yaml", "kustomization.yaml", "kustomization.yml"}:
            kubernetes.append(rel)
            continue
        if path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:16_000]
        except OSError:
            continue
        if re.search(r"(?m)^apiVersion:\s*[^\s]+\s*$", head) and re.search(
            r"(?m)^kind:\s*[A-Za-z]+\s*$", head
        ):
            kubernetes.append(rel)
        elif any(marker in lower for marker in ("k8s", "kubernetes", "helm/")):
            kubernetes.append(rel)
    return sorted(set(kubernetes))


def _discover_iac(files: list[Path], rels: list[str]) -> list[str]:
    detected = {"terraform" for rel in rels if rel.endswith(".tf")}
    if any(Path(rel).name == "Pulumi.yaml" for rel in rels):
        detected.add("pulumi")
    for path, rel in zip(files, rels, strict=True):
        if "cloudformation" in rel.lower() or "sam-template" in rel.lower():
            detected.add("cloudformation")
            continue
        if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:16_000]
        except OSError:
            continue
        if "AWSTemplateFormatVersion" in head or "AWS::Serverless-2016-10-31" in head:
            detected.add("cloudformation")
    return sorted(detected)


def _discover_test_capabilities(root: Path, tests: list[str], rels: list[str]) -> list[str]:
    capabilities = set()
    javascript_cli_targets: set[str] = set()
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
            binaries = package.get("bin", {}) if isinstance(package, dict) else {}
            if isinstance(binaries, str):
                javascript_cli_targets.add(binaries.removeprefix("./"))
            elif isinstance(binaries, dict):
                javascript_cli_targets.update(str(name) for name in binaries)
                javascript_cli_targets.update(
                    str(target).removeprefix("./") for target in binaries.values()
                )
        except (OSError, ValueError):
            pass
    markers = {
        "unit": ("unit",),
        "integration": ("integration",),
        "contract": ("contract", "pact"),
        "regression": ("regression",),
        "smoke": ("smoke",),
        "end-to-end": ("e2e", "end_to_end", "playwright", "cypress"),
        "property-based": ("property", "hypothesis"),
        "fuzz": ("fuzz",),
        "benchmark": ("benchmark", "bench"),
        "performance": ("performance", "perf"),
        "load": ("load", "k6", "locust"),
        "stress": ("stress",),
        "soak": ("soak",),
        "resilience": ("resilience", "chaos"),
        "failure": ("failure", "fault"),
        "dast": ("dast", "penetration", "zap"),
    }
    for rel in tests:
        lowered = rel.lower()
        try:
            test_text = (root / rel).read_text(encoding="utf-8", errors="ignore")[:100_000]
        except OSError:
            test_text = ""
        matched = {
            capability
            for capability, aliases in markers.items()
            if any(alias in lowered for alias in aliases)
        }
        capabilities.update(matched)
        if not matched:
            generated_smoke = "Generated by Blueprint AI" in test_text
            capabilities.add("smoke" if generated_smoke else "unit")
        if "subprocess.run" in test_text and (
            "blueprint_ai.cli" in test_text or "blueprint-ai" in test_text
        ):
            capabilities.add("smoke")
        if (
            javascript_cli_targets
            and ("node:child_process" in test_text or "child_process" in test_text)
            and any(
                call in test_text for call in ("spawn(", "spawnSync(", "execFile(", "execFileSync(")
            )
            and any(target in test_text for target in javascript_cli_targets)
        ):
            capabilities.add("smoke")
    browser_configs = {
        "playwright.config.ts",
        "playwright.config.js",
        "cypress.config.ts",
        "cypress.config.js",
    }
    if any(Path(rel).name in browser_configs for rel in rels):
        capabilities.add("end-to-end")
    if any(
        Path(rel).name in {"locustfile.py", "k6.js"} or rel.endswith(".bench.js") for rel in rels
    ):
        capabilities.update({"performance", "load"})
    if any(Path(rel).name in {".coveragerc", "coverage.xml", "jacoco.xml"} for rel in rels):
        capabilities.add("coverage")
    return sorted(capabilities)


def discover_project(
    root: Path,
    extra_ignores: Iterable[str] = (),
    *,
    changed_only: bool = False,
    base_ref: str | None = None,
) -> ProjectFacts:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    files, ignored = iter_project_files(root, extra_ignores)
    rels = [path.relative_to(root).as_posix() for path in files]
    names = {path.name for path in files}
    languages = Counter(
        LANGUAGE_SUFFIXES[path.suffix.lower()]
        for path in files
        if path.suffix.lower() in LANGUAGE_SUFFIXES
    )
    manifest_names = {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "requirements.txt",
        "poetry.lock",
        "uv.lock",
        "go.mod",
        "go.sum",
        "Cargo.toml",
        "Cargo.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Gemfile",
        "Gemfile.lock",
        "composer.json",
        "composer.lock",
    }
    manifests = sorted(
        rel for rel in rels if Path(rel).name in manifest_names or rel.endswith(".csproj")
    )
    managers = []
    for marker, manager in (
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("requirements.txt", "pip"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("go.mod", "go"),
        ("Cargo.toml", "cargo"),
    ):
        if marker in names:
            managers.append(manager)
    graph = build_graph(root, files, LANGUAGE_SUFFIXES)
    frameworks = sorted(
        {
            name
            for c in graph.components
            if c.scope not in {"fixture", "example", "test", "generated"}
            for name in c.frameworks
        }
    )
    project_types = graph_project_types(graph)
    iac = _discover_iac(files, rels)
    containers = [
        rel
        for rel in rels
        if Path(rel).name.lower()
        in {
            "dockerfile",
            "compose.yml",
            "compose.yaml",
            "docker-compose.yml",
            "docker-compose.yaml",
        }
    ]
    ci = sorted(
        {"github-actions" for rel in rels if rel.startswith(".github/workflows/")}
        | {"gitlab-ci" for rel in rels if rel == ".gitlab-ci.yml"}
    )
    tests = [
        rel
        for rel in rels
        if rel.startswith(("tests/", "test/", "spec/", "__tests__/"))
        or Path(rel).name.startswith("test_")
        or rel.endswith(".tftest.hcl")
        or ".test." in rel
        or ".spec." in rel
    ]
    docs = [rel for rel in rels if rel.lower().endswith(".md") or rel.startswith("docs/")]
    ai_names = {"AGENTS.md", "CLAUDE.md", ".cursorrules", "copilot-instructions.md"}
    ai_context = [
        rel
        for rel in rels
        if Path(rel).name in ai_names
        or rel == "docs/ai-context.md"
        or rel.startswith((".cursor/rules/", ".github/instructions/", "prompts/"))
    ]
    api_specs = [
        rel
        for rel in rels
        if Path(rel).name.lower()
        in {
            "openapi.json",
            "openapi.yaml",
            "openapi.yml",
            "swagger.json",
            "swagger.yaml",
            "swagger.yml",
            "asyncapi.json",
            "asyncapi.yaml",
            "asyncapi.yml",
            "schema.graphql",
        }
        or rel.lower().endswith((".graphql", ".gql", ".proto"))
    ]
    kubernetes = _discover_kubernetes(files, rels)
    migrations = [
        rel
        for rel in rels
        if any(
            part.lower() in {"migrations", "alembic", "flyway", "liquibase"}
            for part in Path(rel).parts
        )
        or Path(rel).name.lower() in {"alembic.ini", "liquibase.properties"}
    ]
    config_files = [
        rel
        for rel in rels
        if Path(rel).name.lower()
        in {".env.example", ".env.template", "config.yaml", "config.yml", "settings.json"}
        or rel.startswith(("config/", "configs/"))
    ]
    observability_markers = {
        "opentelemetry": "tracing",
        "prometheus": "metrics",
        "structlog": "structured-logging",
        "logback": "logging",
        "sentry": "error-monitoring",
    }
    dependencies = {
        dep.name
        for c in graph.components
        if c.scope not in {"fixture", "example", "test", "generated"}
        for dep in c.dependencies
    }
    observability = sorted(
        {
            capability
            for marker, capability in observability_markers.items()
            if any(
                dep == marker or dep.startswith(marker + "-") or dep.startswith("@" + marker + "/")
                for dep in dependencies
            )
        }
    )
    tests = [rel for rel in tests if graph.scope(rel) not in {"fixture", "generated", "vendor"}]
    test_capabilities = sorted(
        set(_discover_test_capabilities(root, tests, rels))
        | {
            node.kind
            for node in graph.verification
            if node.kind not in {"build", "typecheck", "external-workflow"}
        }
    )
    cloud = sorted(
        {
            cloud
            for c in graph.components
            for evidence in c.evidence
            for prefix, cloud in (("aws_", "aws"), ("azurerm_", "azure"), ("google_", "gcp"))
            if evidence.kind == "resource" and evidence.detail.startswith(prefix)
        }
        | {
            cloud
            for dep in dependencies
            for token, cloud in (
                ("hashicorp/aws", "aws"),
                ("hashicorp/azurerm", "azure"),
                ("hashicorp/google", "gcp"),
                ("@pulumi/aws", "aws"),
                ("@pulumi/azure-native", "azure"),
                ("@pulumi/gcp", "gcp"),
            )
            if dep == token
        }
    )
    if iac:
        project_types.append("iac")
    if "terraform" in iac:
        project_types.append("terraform")
    if containers:
        project_types.append("container")
    if kubernetes:
        project_types.append("kubernetes")
    if ai_context:
        project_types.append("ai-context")
    license_present = any(
        Path(rel).name.lower() in {"license", "license.txt", "license.md", "copying"}
        for rel in rels
    )
    license_present = license_present and not any(
        "No license to redistribute is granted"
        in read_text_bounded(root / rel, 2_000_000, root=root)
        for rel in rels
        if Path(rel).name.lower() in {"license", "license.txt", "license.md", "copying"}
        and Path(rel).parent == Path(".")
    )
    if license_present and (
        (root / ".github").is_dir()
        or any(Path(rel).name.lower() == "contributing.md" for rel in rels)
    ):
        project_types.append("open-source")
    project_types = sorted(set(project_types))
    is_git, branch, dirty = _git_facts(root)
    changed_files = _git_changed_files(root, base_ref) if changed_only and is_git else []
    suggested_profiles = sorted(set(project_types)) or ["default"]
    return ProjectFacts(
        graph=graph,
        path=str(root),
        name=root.name,
        is_git=is_git,
        git_branch=branch,
        git_dirty=dirty,
        languages=dict(languages.most_common()),
        frameworks=sorted(set(frameworks)),
        package_managers=sorted(set(managers)),
        manifests=manifests,
        ci=ci,
        iac=iac,
        containers=containers,
        cloud_hints=cloud,
        tests=tests,
        docs=docs,
        ai_context_files=ai_context,
        api_specs=sorted(api_specs),
        kubernetes=sorted(set(kubernetes)),
        migrations=sorted(migrations),
        config_files=sorted(config_files),
        observability=observability,
        test_capabilities=test_capabilities,
        changed_files=changed_files,
        suggested_profiles=suggested_profiles,
        project_types=project_types,
        file_count=len(files),
        ignored_count=ignored,
    )
