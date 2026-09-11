from __future__ import annotations

import json
import os
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


def discover_project(root: Path, extra_ignores: Iterable[str] = ()) -> ProjectFacts:
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
    iac = sorted(
        {"terraform" for rel in rels if rel.endswith(".tf")}
        | {
            "cloudformation"
            for rel in rels
            if "cloudformation" in rel.lower() or "sam-template" in rel.lower()
        }
    )
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
        if Path(rel).name in ai_names or rel.startswith((".cursor/rules/", ".github/instructions/"))
    ]
    cloud = sorted(
        {
            provider
            for rel in rels
            for marker, provider in (("aws", "aws"), ("azure", "azure"), ("gcp", "gcp"))
            if marker in rel.lower()
        }
    )
    if iac:
        project_types.append("iac")
    if not project_types and manifests:
        project_types.append("library")
    is_git, branch, dirty = _git_facts(root)
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
        project_types=sorted(set(project_types)),
        file_count=len(files),
        ignored_count=ignored,
    )
