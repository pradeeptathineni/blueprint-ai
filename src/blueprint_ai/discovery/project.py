from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from pathspec import PathSpec

from blueprint_ai.core import ProjectFacts

SKIP_DIRS = {
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
    "vendor",
}
GENERATED_SUFFIXES = {".min.js", ".min.css", ".map", ".lockb"}
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


def _ignore_spec(root: Path, extra: Iterable[str]) -> PathSpec:
    patterns = list(extra)
    ignore = root / ".gitignore"
    if ignore.is_file():
        patterns.extend(ignore.read_text(encoding="utf-8", errors="ignore").splitlines())
    return PathSpec.from_lines("gitwildmatch", patterns)


def iter_project_files(root: Path, extra_ignores: Iterable[str] = ()) -> tuple[list[Path], int]:
    spec = _ignore_spec(root, extra_ignores)
    git_files = _git_files(root)
    if git_files is not None:
        found = []
        for rel in git_files:
            path = root / rel
            if any(part in SKIP_DIRS for part in Path(rel).parts) or spec.match_file(rel):
                continue
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_size <= 2_000_000
                    and not any(path.name.endswith(s) for s in GENERATED_SUFFIXES)
                ):
                    found.append(path)
            except OSError:
                continue
        ignored_process = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-oi", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        ignored = len([item for item in ignored_process.stdout.split(b"\0") if item])
        return sorted(set(found)), ignored
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
    return sorted(found), ignored


def _git_files(root: Path) -> list[str] | None:
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != root.resolve():
        return None
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if listed.returncode != 0:
        return None
    return [item.decode(errors="replace") for item in listed.stdout.split(b"\0") if item]


def _git_facts(root: Path) -> tuple[bool, str | None, bool]:
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != root.resolve():
        return False, None, False
    branch = (
        subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        ).stdout.strip()
        or None
    )
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        ).stdout.strip()
    )
    return True, branch, dirty


def _git_changed_files(root: Path, base_ref: str | None = None) -> list[str]:
    changed: set[str] = set()
    compare = base_ref or "HEAD"
    commands = [
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            compare,
            "--",
        ],
        ["git", "-C", str(root), "status", "--porcelain", "-z"],
    ]
    for index, command in enumerate(commands):
        try:
            result = subprocess.run(command, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
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


def _package_json_hints(path: Path) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")[:200_000])
    except (OSError, ValueError):
        return [], []
    deps = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
    frameworks = [
        name
        for name in ("react", "next", "vue", "svelte", "express", "fastify", "vitest", "jest")
        if name in deps
    ]
    types = ["cli"] if data.get("bin") else []
    if {"react", "next", "vue", "svelte"} & deps:
        types.append("web-app")
    if {"express", "fastify"} & deps:
        types.append("api")
    return frameworks, types


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
            capabilities.add("unit")
        if "subprocess.run" in test_text and (
            "blueprint_ai.cli" in test_text or "blueprint-ai" in test_text
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


def _augment_project_types(
    root: Path,
    project_types: list[str],
    observation_text: str,
    iac: list[str],
    containers: list[str],
    kubernetes: list[str],
    manifests: list[str],
) -> list[str]:
    if iac:
        project_types.append("iac")
    if "terraform" in iac:
        project_types.append("terraform")
    if containers:
        project_types.append("container")
    if kubernetes:
        project_types.append("kubernetes")
    if any(
        marker in observation_text for marker in ("openai", "anthropic", "langchain", "llamaindex")
    ):
        project_types.append("ai-app")
    if any(
        marker in observation_text for marker in ("agents sdk", "autogen", "crewai", "langgraph")
    ):
        project_types.append("ai-agent")
    if "api" in project_types:
        project_types.append("backend-service")
    if "web-app" in project_types and "api" in project_types:
        project_types.append("full-stack")
    elif "web-app" in project_types:
        project_types.append("frontend")
    if any(marker in observation_text for marker in ("airflow", "dagster", "prefect", "spark")):
        project_types.append("data-pipeline")
    public_candidate = bool((root / "CONTRIBUTING.md").is_file() or (root / ".github").is_dir())
    if (root / "LICENSE").is_file() and public_candidate:
        project_types.append("open-source")
    if not project_types and manifests:
        project_types.append("library")
    return sorted(set(project_types))


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
    manifests = [
        name
        for name in (
            "pyproject.toml",
            "package.json",
            "go.mod",
            "Cargo.toml",
            "pom.xml",
            "build.gradle",
            "Gemfile",
            "composer.json",
        )
        if name in names
    ]
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
    frameworks: list[str] = []
    project_types: list[str] = []
    if "pyproject.toml" in names:
        text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")[:200_000]
        frameworks += [
            name
            for name in ("django", "fastapi", "flask", "pytest", "typer")
            if name in text.lower()
        ]
        if "[project.scripts]" in text:
            project_types.append("cli")
        if "fastapi" in frameworks or "flask" in frameworks or "django" in frameworks:
            project_types.append("api")
    if "package.json" in names:
        js_frameworks, js_types = _package_json_hints(root / "package.json")
        frameworks += js_frameworks
        project_types += js_types
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
        or rel.lower().endswith((".graphql", ".gql"))
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
    observation_text = " ".join(rels + frameworks).lower()
    for manifest in manifests:
        try:
            observation_text += (
                " "
                + (root / manifest).read_text(encoding="utf-8", errors="ignore")[:200_000].lower()
            )
        except OSError:
            pass
    observability = sorted(
        {
            capability
            for marker, capability in observability_markers.items()
            if marker in observation_text
        }
    )
    test_capabilities = _discover_test_capabilities(root, tests, rels)
    cloud = sorted(
        {
            provider
            for rel in rels
            for marker, provider in (("aws", "aws"), ("azure", "azure"), ("gcp", "gcp"))
            if marker in rel.lower()
        }
    )
    project_types = _augment_project_types(
        root,
        project_types,
        observation_text,
        iac,
        containers,
        kubernetes,
        manifests,
    )
    is_git, branch, dirty = _git_facts(root)
    changed_files = _git_changed_files(root, base_ref) if changed_only and is_git else []
    suggested_profiles = sorted(set(project_types)) or ["default"]
    return ProjectFacts(
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
