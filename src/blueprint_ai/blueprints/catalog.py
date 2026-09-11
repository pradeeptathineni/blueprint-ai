from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from blueprint_ai.core import Finding, ProjectFacts
from blueprint_ai.core.models import Remediation
from blueprint_ai.discovery import iter_project_files

Check = Callable[[Path, ProjectFacts], list[Finding]]
Applicability = Callable[[ProjectFacts], bool]


@dataclass(frozen=True)
class Blueprint:
    name: str
    description: str
    applicability: Applicability
    check: Check
    model_review: bool = False


def _finding(
    blueprint: str,
    category: str,
    message: str,
    recommendation: str,
    *,
    severity: str = "medium",
    priority: str | None = None,
    file: str | None = None,
    evidence: list[str] | None = None,
    remediation: Remediation | None = None,
) -> Finding:
    derived = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3", "info": "P3"}
    return Finding(
        blueprint=blueprint,
        category=category,
        source="blueprint-ai",
        severity=severity,
        priority=priority or derived[severity],
        file=file,
        evidence=evidence or [],
        message=message,
        recommendation=recommendation,
        remediation=remediation,
    )


def _manifest_identity(root: Path) -> tuple[str | None, list[str]]:
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
            return project.get("name"), list(project.get("scripts", {}))
        except (OSError, tomllib.TOMLDecodeError):
            pass
    package = root / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
            bins = data.get("bin", {})
            return data.get("name"), [bins] if isinstance(bins, str) else list(bins)
        except (OSError, ValueError):
            pass
    return None, []


def identity_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    name, commands = _manifest_identity(root)
    findings = []
    if not name:
        return [
            _finding(
                "identity",
                "missing-package-name",
                "No package identity was found.",
                "Declare a package name in the native manifest.",
                severity="low",
            )
        ]
    if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9][a-z0-9._-]*", name):
        findings.append(
            _finding(
                "identity",
                "invalid-package-name",
                f"Package name '{name}' is not portable.",
                "Use a lowercase package name containing letters, numbers, dots, "
                "underscores, or hyphens.",
                severity="high",
            )
        )
    normalized_dir = re.sub(r"[^a-z0-9]+", "-", facts.name.lower()).strip("-")
    normalized_name = re.sub(r"[^a-z0-9]+", "-", name.split("/")[-1].lower()).strip("-")
    if normalized_dir != normalized_name:
        findings.append(
            _finding(
                "identity",
                "name-mismatch",
                f"Directory '{facts.name}' and package '{name}' differ.",
                "Choose a consistent repository/package identity or document the distinction.",
                severity="low",
                evidence=[*commands],
            )
        )
    return findings


def repository_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    baseline = {
        ".gitignore": ("Add a project-local ignore file.", None),
        ".editorconfig": (
            "Add a small cross-editor formatting baseline.",
            Remediation(
                kind="template",
                target=".editorconfig",
                safe=True,
                description="add editor baseline",
            ),
        ),
        "LICENSE": ("Add or document the project's license.", None),
    }
    for filename, (recommendation, remediation) in baseline.items():
        if not (root / filename).is_file():
            findings.append(
                _finding(
                    "repository",
                    "missing-baseline",
                    f"{filename} is missing.",
                    recommendation,
                    severity="low",
                    file=filename,
                    remediation=remediation,
                )
            )
    if not (root / ".pre-commit-config.yaml").is_file():
        findings.append(
            _finding(
                "repository",
                "missing-quality-hooks",
                "No pre-commit quality baseline was found.",
                "Add minimal whitespace, YAML, and large-file checks.",
                severity="low",
                file=".pre-commit-config.yaml",
                remediation=Remediation(
                    kind="template",
                    target=".pre-commit-config.yaml",
                    safe=True,
                    description="add pre-commit baseline",
                ),
            )
        )
    files, _ = iter_project_files(root)
    for path in files:
        try:
            if path.stat().st_size > 1_000_000:
                findings.append(
                    _finding(
                        "repository",
                        "large-file",
                        "Large file may not belong in source control.",
                        "Confirm the file is intentional or store it as an artifact.",
                        severity="medium",
                        file=path.relative_to(root).as_posix(),
                        evidence=[f"{path.stat().st_size} bytes"],
                    )
                )
        except OSError:
            pass
    return findings


def security_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    files, _ = iter_project_files(root)
    findings = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if (
            path.name == ".env"
            or path.name.startswith(".env.")
            and path.name not in {".env.example", ".env.template"}
        ):
            findings.append(
                _finding(
                    "security",
                    "sensitive-file",
                    f"Potential environment secret file '{rel}' is visible to analysis.",
                    "Ensure the file is ignored and contains no committed credentials.",
                    severity="high",
                    file=rel,
                )
            )
    if facts.is_git and not (root / "SECURITY.md").is_file() and facts.file_count > 25:
        findings.append(
            _finding(
                "security",
                "missing-policy",
                "SECURITY.md is missing.",
                "Document supported versions and private vulnerability reporting.",
                severity="low",
                file="SECURITY.md",
            )
        )
    return findings


def testing_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    if facts.tests:
        return []
    language = (
        "Python"
        if "Python" in facts.languages
        else "JavaScript"
        if {"JavaScript", "TypeScript"} & set(facts.languages)
        else None
    )
    remediation = None
    if language:
        target = "tests/test_smoke.py" if language == "Python" else "tests/smoke.test.js"
        remediation = Remediation(
            kind="test-scaffold", target=target, safe=True, description=f"add {language} smoke test"
        )
    return [
        _finding(
            "testing",
            "missing-tests",
            "No tests were discovered.",
            "Add a small smoke test using the project's native test framework.",
            severity="high",
            remediation=remediation,
        )
    ]


def ci_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    sha = re.compile(r"^[0-9a-f]{40}$")
    for workflow in (
        (root / ".github" / "workflows").glob("*.y*ml")
        if (root / ".github" / "workflows").is_dir()
        else []
    ):
        for number, line in enumerate(
            workflow.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if not match or match.group(1).startswith(("./", "docker://")):
                continue
            reference = match.group(1).rsplit("@", 1)[-1]
            if not sha.fullmatch(reference):
                findings.append(
                    _finding(
                        "ci-cd",
                        "unpinned-action",
                        f"GitHub Action is not pinned to a full commit SHA: {match.group(1)}",
                        "Pin third-party actions to a reviewed full commit SHA and retain "
                        "the version in a comment.",
                        severity="high",
                        file=workflow.relative_to(root).as_posix(),
                        evidence=[f"line {number}"],
                    )
                )
    return findings


def documentation_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    readme = next(
        (root / name for name in ("README.md", "README.rst", "README") if (root / name).is_file()),
        None,
    )
    if not readme:
        return [
            _finding(
                "documentation",
                "missing-readme",
                "README is missing.",
                "Add a concise project overview, install instructions, and usage example.",
                severity="medium",
                file="README.md",
                remediation=Remediation(
                    kind="template",
                    target="README.md",
                    safe=True,
                    description="add README baseline",
                ),
            )
        ]
    text = readme.read_text(encoding="utf-8", errors="ignore")[:100_000].lower()
    concepts = {
        "install": ("install", "setup", "getting started"),
        "usage": ("usage", "commands", "example"),
    }
    missing = [
        name for name, aliases in concepts.items() if not any(alias in text for alias in aliases)
    ]
    return (
        [
            _finding(
                "documentation",
                "readme-incomplete",
                f"README does not mention: {', '.join(missing)}.",
                "Add the missing reader-oriented sections.",
                severity="low",
                file=readme.name,
            )
        ]
        if missing
        else []
    )


def ai_context_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    hashes: dict[str, list[str]] = {}
    for rel in facts.ai_context_files:
        path = root / rel
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if len(content) > 32_000:
            findings.append(
                _finding(
                    "ai-context",
                    "oversized-context",
                    f"AI context file is {len(content)} bytes.",
                    "Split or compress instructions so agents receive only relevant context.",
                    severity="medium",
                    file=rel,
                )
            )
        hashes.setdefault(hashlib.sha256(content).hexdigest(), []).append(rel)
    for duplicates in hashes.values():
        if len(duplicates) > 1:
            findings.append(
                _finding(
                    "ai-context",
                    "duplicate-context",
                    "Identical AI context exists at multiple paths.",
                    "Keep one authoritative instruction source or generate mirrors.",
                    severity="low",
                    evidence=duplicates,
                )
            )
    return findings


def no_builtin_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    return []


def _has_code(facts: ProjectFacts) -> bool:
    return bool(set(facts.languages) - {"Markdown", "YAML", "HCL"})


BLUEPRINTS: dict[str, Blueprint] = {
    "identity": Blueprint(
        "identity",
        "Project, package, and CLI naming consistency",
        lambda f: bool(f.manifests),
        identity_checks,
        True,
    ),
    "repository": Blueprint(
        "repository", "Repository structure and hygiene", lambda f: True, repository_checks
    ),
    "code-quality": Blueprint(
        "code-quality", "Native lint, formatting, and static analysis", _has_code, no_builtin_checks
    ),
    "security": Blueprint(
        "security",
        "Secrets, SAST, dependencies, and security posture",
        lambda f: f.file_count > 0,
        security_checks,
    ),
    "testing": Blueprint(
        "testing", "Test discovery, execution, and missing layers", _has_code, testing_checks, True
    ),
    "iac": Blueprint(
        "iac",
        "Infrastructure-as-code validation and policy",
        lambda f: bool(f.iac),
        no_builtin_checks,
        True,
    ),
    "ci-cd": Blueprint("ci-cd", "CI workflow syntax and security", lambda f: bool(f.ci), ci_checks),
    "documentation": Blueprint(
        "documentation",
        "Documentation structure, links, and clarity",
        lambda f: True,
        documentation_checks,
        True,
    ),
    "architecture": Blueprint(
        "architecture",
        "Dependencies, boundaries, coupling, and resilience",
        _has_code,
        no_builtin_checks,
        True,
    ),
    "ai-context": Blueprint(
        "ai-context",
        "AI instruction context size, duplication, and conflicts",
        lambda f: bool(f.ai_context_files),
        ai_context_checks,
        True,
    ),
}

PROFILES = {
    "default": [
        "identity",
        "repository",
        "code-quality",
        "security",
        "testing",
        "documentation",
        "ci-cd",
        "iac",
        "ai-context",
    ],
    "library": [
        "identity",
        "repository",
        "code-quality",
        "security",
        "testing",
        "documentation",
        "architecture",
    ],
    "cli": [
        "identity",
        "repository",
        "code-quality",
        "security",
        "testing",
        "documentation",
        "architecture",
        "ci-cd",
    ],
    "api": [
        "identity",
        "repository",
        "code-quality",
        "security",
        "testing",
        "documentation",
        "architecture",
        "ci-cd",
    ],
    "web-app": [
        "identity",
        "repository",
        "code-quality",
        "security",
        "testing",
        "documentation",
        "architecture",
        "ci-cd",
    ],
    "iac": ["repository", "security", "testing", "iac", "ci-cd", "documentation"],
    "ai-app": [
        "identity",
        "repository",
        "code-quality",
        "security",
        "testing",
        "documentation",
        "architecture",
        "ai-context",
    ],
    "production": list(BLUEPRINTS),
}


def get_blueprint(name: str) -> Blueprint:
    try:
        return BLUEPRINTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown blueprint: {name}") from exc
