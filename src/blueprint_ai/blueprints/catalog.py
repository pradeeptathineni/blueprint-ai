from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import tokenize
import tomllib
import warnings
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from blueprint_ai.config import load_yaml_mapping
from blueprint_ai.core import Applicability, Finding, ProjectFacts
from blueprint_ai.core.models import FileRange, Priority, Remediation, Severity
from blueprint_ai.core.project import NON_RUNTIME
from blueprint_ai.discovery import iter_project_files
from blueprint_ai.naming import validate_name
from blueprint_ai.safety import read_bytes_bounded, read_text_bounded, safe_regular_file

Check = Callable[[Path, ProjectFacts], list[Finding]]
Predicate = Callable[[ProjectFacts], bool]


@dataclass(frozen=True)
class Blueprint:
    name: str
    description: str
    applicability: Predicate
    check: Check
    model_review: bool = False
    not_applicable_reason: str = "project evidence does not match this capability"
    partial: Predicate | None = None
    partial_reason: str = "only generic deterministic coverage is available"

    def assess(self, facts: ProjectFacts) -> Applicability:
        if not self.applicability(facts):
            return Applicability(state="not_applicable", reason=self.not_applicable_reason)
        if self.partial and self.partial(facts):
            return Applicability(state="partial", reason=self.partial_reason)
        return Applicability(state="applicable", reason="matching project evidence was discovered")


def _finding(
    blueprint: str,
    category: str,
    message: str,
    recommendation: str,
    *,
    severity: Severity = "medium",
    priority: Priority | None = None,
    file: str | None = None,
    line: int | None = None,
    evidence: list[str] | None = None,
    remediation: Remediation | None = None,
    verification: str = "rerun blueprint",
    rule_id: str | None = None,
) -> Finding:
    derived: dict[Severity, Priority] = {
        "critical": "P0",
        "high": "P1",
        "medium": "P2",
        "low": "P3",
        "info": "P3",
    }
    return Finding(
        blueprint=blueprint,
        category=category,
        rule_id=rule_id or f"blueprint-ai/{blueprint}/{category}",
        source="blueprint-ai",
        sources=["blueprint-ai"],
        severity=severity,
        priority=priority or derived[severity],
        file=file,
        range=FileRange(start_line=line) if line else None,
        evidence=evidence or [],
        message=message,
        recommendation=recommendation,
        remediation=remediation,
        verification=verification,
    )


def _manifest_identity(root: Path) -> tuple[str | None, list[str]]:
    for filename, section in (
        ("pyproject.toml", "project"),
        ("package.json", None),
        ("Cargo.toml", "package"),
    ):
        path = root / filename
        if not safe_regular_file(path, root):
            continue
        raw = read_text_bounded(path, 2_000_000, root=root)
        data = json.loads(raw) if section is None else tomllib.loads(raw).get(section, {})
        if not isinstance(data, dict):
            raise ValueError(f"{filename} identity must be a mapping")
        name = data.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"{filename} name must be a string")
        commands = data.get("bin", {}) if section is None else data.get("scripts", {})
        if isinstance(commands, str) and section is None:
            commands = [commands]
        elif isinstance(commands, dict):
            commands = list(commands)
        else:
            raise ValueError(f"{filename} commands must be a mapping or package bin string")
        return name, commands
    go_mod = root / "go.mod"
    if safe_regular_file(go_mod, root):
        match = re.search(r"(?m)^module\s+(\S+)", read_text_bounded(go_mod, 2_000_000, root=root))
        return (match.group(1).rsplit("/", 1)[-1], []) if match else (None, [])
    return None, []


def identity_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    name, commands = _manifest_identity(root)
    if not name and (
        facts.graph.lifecycle in {"documentation-project", "template-generator"}
        or any(c.name for c in facts.graph.components)
    ):
        return []
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
    findings = []
    namespace = (
        "python"
        if (root / "pyproject.toml").is_file()
        else "npm"
        if (root / "package.json").is_file()
        else "repository"
    )
    valid = validate_name(name, namespace).syntax_valid
    if not valid:
        findings.append(
            _finding(
                "identity",
                "invalid-package-name",
                f"Package name '{name}' is not portable.",
                "Use the ecosystem's lowercase portable package naming convention.",
                severity="high",
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
    if facts.graph.lifecycle == "documentation-project":
        baseline = {key: value for key, value in baseline.items() if key == "LICENSE"}
    for filename, (recommendation, remediation) in baseline.items():
        if not _conventional_file(root, filename):
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
    if (
        not facts.ci
        and facts.graph.lifecycle != "documentation-project"
        and not (root / ".pre-commit-config.yaml").is_file()
    ):
        findings.append(
            _finding(
                "repository",
                "missing-quality-hooks",
                "No pre-commit quality baseline was found.",
                "Add fast whitespace, syntax, quality, and secret checks.",
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
    if "open-source" in facts.project_types:
        for filename in ("SECURITY.md", "CONTRIBUTING.md"):
            if not _conventional_file(root, filename):
                findings.append(
                    _finding(
                        "repository",
                        "oss-readiness",
                        f"{filename} is missing from this open-source project.",
                        f"Add a concise {filename} appropriate to the project.",
                        severity="low",
                        file=filename,
                    )
                )
    files, _ = iter_project_files(root)
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 1_000_000:
            findings.append(
                _finding(
                    "repository",
                    "large-file",
                    "Large file may not belong in source control.",
                    "Confirm the file is intentional or store it as an artifact.",
                    file=rel,
                    evidence=[f"{size} bytes"],
                )
            )
        if path.suffix.lower() in {".bak", ".old", ".orig", ".rej"}:
            findings.append(
                _finding(
                    "repository",
                    "stale-artifact",
                    "A likely backup or merge artifact is tracked.",
                    "Remove it if obsolete or document why it is source material.",
                    severity="low",
                    file=rel,
                )
            )
    return findings


def code_design_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    files, _ = iter_project_files(root)
    imports: dict[str, set[str]] = defaultdict(set)
    for path in files:
        if (
            path.suffix != ".py"
            or facts.graph.scope(path.relative_to(root).as_posix()) in NON_RUNTIME
        ):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            text = read_text_bounded(path, 2_000_000, root=root, errors="ignore")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        module = rel.removesuffix(".py").replace("/", ".").removeprefix("src.")
        module = module.removesuffix(".__init__")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                complexity = sum(
                    isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.Match))
                    for child in ast.walk(node)
                )
                if end - node.lineno + 1 > 100 and complexity > 10:
                    findings.append(
                        _finding(
                            "code-design",
                            "large-function",
                            f"Function '{node.name}' spans {end - node.lineno + 1} lines.",
                            "Extract cohesive responsibilities and keep the public contract "
                            "stable.",
                            severity="low",
                            file=rel,
                            line=node.lineno,
                        )
                    )
            elif isinstance(node, ast.Import):
                imports[module].update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports[module].add(node.module)
        try:
            comments = [
                token
                for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT
            ]
        except (IndentationError, tokenize.TokenError):
            comments = []
        for token in comments:
            if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", token.string, re.IGNORECASE):
                findings.append(
                    _finding(
                        "code-design",
                        "work-marker",
                        "Unresolved work marker should be tracked or resolved.",
                        "Link it to an issue with intent/expiry, or remove stale commentary.",
                        severity="info",
                        file=rel,
                        line=token.start[0],
                        evidence=[token.string.strip()[:200]],
                    )
                )
    local = set(imports)
    for module, dependencies in imports.items():
        for dependency in dependencies:
            if dependency in local and module in imports.get(dependency, set()):
                pair = sorted([module, dependency])
                findings.append(
                    _finding(
                        "code-design",
                        "dependency-cycle",
                        f"Possible Python import cycle between {' and '.join(pair)}.",
                        "Move the shared contract to a lower-level module and verify imports.",
                        evidence=pair,
                    )
                )
    return findings


def security_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    files, _ = iter_project_files(root)
    secret_pattern = re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]"
    )
    code_suffixes = {".py", ".js", ".ts", ".java", ".go", ".rs", ".yaml", ".yml", ".json"}
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.name == ".env" or (
            path.name.startswith(".env.") and path.name not in {".env.example", ".env.template"}
        ):
            text = read_text_bounded(path, 300_000, root=root, errors="ignore")
            values = [
                value.strip().strip("\"'")
                for key, value in re.findall(r"(?m)^([A-Za-z_][\w]*)\s*=\s*([^\n#]*)", text)
                if re.search(r"(?i)(?:secret|password|token|api_key)", key)
            ]
            if any(not _placeholder(value) for value in values):
                findings.append(
                    _finding(
                        "security",
                        "sensitive-file",
                        f"Potential credentials in environment file '{rel}'.",
                        "Rotate if real and keep credentials outside source control.",
                        severity="high",
                        file=rel,
                    )
                )
        if path.suffix.lower() not in code_suffixes:
            continue
        try:
            text = read_text_bounded(path, 2_000_000, root=root, errors="ignore")[:300_000]
        except OSError:
            continue
        for match in secret_pattern.finditer(text):
            value = match.group(2)
            if _placeholder(value):
                continue
            if _public_search_key(text, match.start(), rel):
                continue
            findings.append(
                _finding(
                    "security",
                    "hardcoded-secret",
                    f"Potential hardcoded {match.group(1)} was found.",
                    "Rotate if real and load the value from an approved secret store.",
                    severity="high",
                    file=rel,
                    line=text.count("\n", 0, match.start()) + 1,
                    evidence=["value redacted"],
                )
            )
    return findings


def testing_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    present = set(facts.test_capabilities)
    types = set(facts.project_types)
    application_languages = set(facts.languages) - {"HCL", "Markdown", "Shell", "YAML"}
    iac_focused = bool(facts.iac) and (
        not application_languages
        or all(
            not set(component.roles) & {"frontend", "backend", "api", "service", "library", "cli"}
            for component in facts.graph.components
        )
    )
    required: list[tuple[str, Severity, str]] = [
        (
            "unit",
            "medium" if iac_focused else "high",
            "Terraform module behavior"
            if iac_focused and "terraform" in facts.iac
            else "infrastructure behavior"
            if iac_focused
            else "core behavior",
        )
    ]
    if types & {"api", "backend-service", "full-stack"}:
        required.append(("integration", "medium", "service boundaries"))
    if facts.api_specs:
        required.append(("contract", "medium", "API schema compatibility"))
    if types & {"web-app", "frontend", "full-stack"}:
        required.append(("end-to-end", "medium", "critical user journeys"))
    if "cli" in types:
        required.append(("smoke", "medium", "installed command execution"))
    if "production" in types and types & {
        "api",
        "backend-service",
        "web-app",
        "full-stack",
        "data-pipeline",
        "container",
        "kubernetes",
    }:
        required.append(("performance", "low", "capacity assumptions"))
    findings = []
    for capability, severity, scope in required:
        if capability in present:
            continue
        language = (
            "python"
            if "Python" in facts.languages
            else "javascript"
            if set(facts.languages) & {"JavaScript", "TypeScript"}
            else None
        )
        remediation = None
        if capability == "smoke" and language:
            target = "tests/test_smoke.py" if language == "python" else "tests/smoke.test.js"
            remediation = Remediation(
                kind="test-scaffold",
                target=target,
                safe=True,
                description=f"add generated {capability} test",
            )
        findings.append(
            _finding(
                "testing",
                f"missing-{capability}-tests",
                f"No {capability} test capability was discovered for {scope}.",
                (
                    "Add focused native Terraform tests for important plan-time invariants."
                    if iac_focused
                    else (
                        f"Use the existing test stack to cover {scope}; "
                        "mark generated-test provenance."
                    )
                ),
                severity=severity,
                remediation=remediation,
                verification="execute the generated or existing test suite",
            )
        )
    return findings


def _application_config_files(facts: ProjectFacts) -> list[str]:
    return [
        rel for rel in facts.config_files if Path(rel).suffix.lower() not in {".tfvars", ".hcl"}
    ]


def api_data_config_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    for rel in facts.api_specs:
        path = root / rel
        if path.suffix.lower() in {".graphql", ".gql", ".proto"}:
            continue  # Native GraphQL/Protobuf compilers own these grammars.
        try:
            raw = read_text_bounded(path, 2_000_000, root=root)
            data = (
                json.loads(raw)
                if path.suffix == ".json"
                else load_yaml_mapping(path, root, label=rel)
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                _finding(
                    "api-data-config",
                    "invalid-schema",
                    f"API/schema document cannot be parsed: {exc}",
                    "Fix syntax, then validate with the native schema validator or Spectral.",
                    severity="high",
                    file=rel,
                )
            )
            continue
        if isinstance(data, dict) and any(
            key in data for key in ("openapi", "swagger", "asyncapi")
        ):
            if not data.get("info", {}).get("version"):
                findings.append(
                    _finding(
                        "api-data-config",
                        "unversioned-contract",
                        "API contract has no info.version.",
                        "Record a contract version and compare it with a baseline in CI.",
                        file=rel,
                    )
                )
    application_config = _application_config_files(facts)
    env_example = any(
        Path(rel).name in {".env.example", ".env.template"} for rel in application_config
    )
    if application_config and not env_example:
        findings.append(
            _finding(
                "api-data-config",
                "undocumented-environment",
                "Configuration exists without an environment-variable example file.",
                "Document required variables, safe defaults, validation, and secret-store "
                "ownership.",
                severity="low",
            )
        )
    has_rollback = any(
        "down" in Path(rel).stem.lower() or "rollback" in rel.lower() for rel in facts.migrations
    )
    if facts.migrations and not has_rollback:
        findings.append(
            _finding(
                "api-data-config",
                "migration-recovery",
                "Migrations were found without an obvious rollback/recovery artifact.",
                "Document roll-forward/rollback strategy and test migration compatibility.",
                evidence=facts.migrations[:10],
            )
        )
    return findings


def supply_chain_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    lock_markers = {
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "packages.lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "go.sum",
        "Cargo.lock",
    }
    go_dependencies = any(
        d.ecosystem == "Go" for c in facts.graph.components for d in c.dependencies
    )
    needs_lock = any(Path(rel).name != "go.mod" or go_dependencies for rel in facts.manifests)
    if needs_lock and not any(Path(rel).name in lock_markers for rel in facts.manifests):
        findings.append(
            _finding(
                "supply-chain",
                "missing-lockfile",
                "No dependency lock or checksum file was discovered.",
                "Use the ecosystem-native lock/checksum mechanism for reproducible resolution.",
            )
        )
    if set(facts.project_types) & {"open-source", "production"}:
        known_sbom = any(
            (root / name).exists() for name in (".syft.yaml", ".github/workflows/sbom.yml")
        )
        workflow_dir = root / ".github" / "workflows"
        if workflow_dir.is_dir():
            known_sbom = known_sbom or any(
                "sbom-action" in read_text_bounded(path, 2_000_000, root=root, errors="ignore")
                or "syft" in read_text_bounded(path, 2_000_000, root=root, errors="ignore")
                for path in workflow_dir.glob("*.y*ml")
            )
        known_sbom = known_sbom or any(
            Path(rel).name.lower().startswith(("sbom", "bom.")) for rel in facts.docs
        )
        if not known_sbom:
            findings.append(
                _finding(
                    "supply-chain",
                    "missing-sbom-process",
                    "No SBOM generation configuration or artifact was discovered.",
                    "Generate CycloneDX or SPDX with Syft/native build attestations in release CI.",
                    severity="low",
                )
            )
    return findings


def iac_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    if "terraform" in facts.iac:
        tf_files = [path for path in iter_project_files(root)[0] if path.suffix == ".tf"]
        combined = "\n".join(
            read_text_bounded(path, 2_000_000, root=root, errors="ignore")[:100_000]
            for path in tf_files
        )
        if "required_version" not in combined:
            findings.append(
                _finding(
                    "iac",
                    "terraform-version",
                    "Terraform required_version is not constrained.",
                    "Declare a compatible version range and verify with terraform validate.",
                )
            )
        for path in tf_files:
            if path.name.endswith(".tfstate"):
                findings.append(
                    _finding(
                        "iac",
                        "tracked-state",
                        "Terraform state must not be kept in source control.",
                        "Remove it from Git history, rotate exposed secrets, and use a protected "
                        "backend.",
                        severity="critical",
                        file=path.relative_to(root).as_posix(),
                    )
                )
    return findings


def container_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    for rel in facts.containers:
        path = root / rel
        if path.name.lower() != "dockerfile":
            continue
        text = read_text_bounded(path, 2_000_000, root=root, errors="ignore")
        if re.search(r"(?mi)^FROM\s+\S+:latest(?:\s|$)", text):
            findings.append(
                _finding(
                    "containers",
                    "floating-base-image",
                    "Docker base image uses the floating latest tag.",
                    "Pin an immutable digest and automate reviewed updates.",
                    severity="high",
                    file=rel,
                )
            )
        final_stage = re.split(r"(?mi)^FROM\s+", text)[-1]
        runtime = facts.graph.scope(rel) == "runtime-image" if facts.graph.components else True
        if runtime and not re.search(r"(?mi)^USER\s+(?!root\b|0\b)\S+", final_stage):
            findings.append(
                _finding(
                    "containers",
                    "root-runtime",
                    "Dockerfile does not set a non-root runtime user.",
                    "Create and switch to a least-privileged user in the final stage.",
                    severity="high",
                    file=rel,
                )
            )
        if (
            runtime
            and "production" in facts.project_types
            and "HEALTHCHECK" not in final_stage.upper()
            and "api" in facts.project_types
        ):
            findings.append(
                _finding(
                    "containers",
                    "missing-healthcheck",
                    "Production container has no Docker health check.",
                    "Add a cheap health check or document platform-owned probes.",
                    file=rel,
                )
            )
    return findings


def kubernetes_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    rules: tuple[tuple[str, str, str, str, Severity], ...] = (
        (
            "privileged-workload",
            r"(?m)^\s*privileged:\s*true\s*$",
            "Kubernetes workload enables privileged mode.",
            "Remove privileged mode or document and isolate the narrow requirement.",
            "critical",
        ),
        (
            "floating-image",
            r"(?m)^\s*-?\s*image:\s*\S+:latest\s*$",
            "Kubernetes workload uses a floating image tag.",
            "Pin an immutable digest and use controlled rollout automation.",
            "high",
        ),
    )
    for rel in facts.kubernetes:
        path = root / rel
        if path.name.lower() in {"chart.yaml", "kustomization.yaml", "kustomization.yml"}:
            continue
        text = read_text_bounded(path, 2_000_000, root=root, errors="ignore")
        for category, pattern, message, recommendation, severity in rules:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                findings.append(
                    _finding(
                        "kubernetes",
                        category,
                        message,
                        recommendation,
                        severity=severity,
                        file=rel,
                        line=text.count("\n", 0, match.start()) + 1,
                    )
                )
        workload = re.search(r"(?m)^kind:\s*(Deployment|StatefulSet)\s*$", text)
        if workload and "resources:" not in text:
            findings.append(
                _finding(
                    "kubernetes",
                    "missing-resource-policy",
                    "Workload has no resource requests/limits block.",
                    "Set workload-informed requests and limits, then load test scheduling "
                    "behavior.",
                    file=rel,
                )
            )
    return findings


def ci_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    sha = re.compile(r"^[0-9a-f]{40}$")
    workflow_dir = root / ".github" / "workflows"
    workflow_texts = []
    for workflow in workflow_dir.glob("*.y*ml") if workflow_dir.is_dir() else []:
        try:
            text = read_text_bounded(workflow, 2_000_000, root=root, errors="ignore")
        except (OSError, ValueError):
            continue
        workflow_texts.append(text)
        try:
            loaded = load_yaml_mapping(workflow, root, label=workflow.relative_to(root).as_posix())
            if not isinstance(loaded, dict):
                raise ValueError("workflow root is not a mapping")
        except (ValueError, yaml.YAMLError) as exc:
            findings.append(
                _finding(
                    "ci-cd",
                    "invalid-workflow-yaml",
                    f"Workflow YAML cannot be parsed: {exc}",
                    "Correct workflow syntax and validate with actionlint.",
                    severity="high",
                    file=workflow.relative_to(root).as_posix(),
                )
            )
            continue
        for number, line in enumerate(text.splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if not match or match.group(1).startswith(("./", "$/", "docker://")):
                continue
            reference = match.group(1).rsplit("@", 1)[-1]
            if not sha.fullmatch(reference):
                findings.append(
                    _finding(
                        "ci-cd",
                        "unpinned-action",
                        f"GitHub Action is not pinned to a full commit SHA: {match.group(1)}",
                        "Pin third-party actions to a reviewed SHA and retain the version in a "
                        "comment.",
                        severity="high",
                        file=workflow.relative_to(root).as_posix(),
                        line=number,
                        rule_id="blueprint-ai/ci-cd/unpinned-action",
                    )
                )
        checks_out = re.search(r"(?m)^\s*-?\s*uses:\s*actions/checkout", text)
        if (
            "pull_request_target" in text
            and checks_out
            and re.search(r"github\.event\.pull_request\.head", text)
        ):
            findings.append(
                _finding(
                    "ci-cd",
                    "dangerous-pr-workflow",
                    "pull_request_target workflow checks out code and may expose a privileged "
                    "token.",
                    "Avoid executing untrusted PR code with write permissions or secrets.",
                    severity="critical",
                    file=workflow.relative_to(root).as_posix(),
                )
            )
    if "open-source" in facts.project_types:
        dependency_updates = (root / ".github" / "dependabot.yml").is_file() or any(
            (root / name).is_file() for name in ("renovate.json", "renovate.json5")
        )
        if (
            facts.manifests
            and facts.graph.lifecycle != "documentation-project"
            and not dependency_updates
        ):
            findings.append(
                _finding(
                    "ci-cd",
                    "missing-dependency-updates",
                    "No dependency-update automation configuration was discovered.",
                    "Configure Dependabot or Renovate for the detected package managers and CI.",
                    severity="low",
                )
            )
        if facts.graph.lifecycle != "documentation-project" and not _conventional_file(
            root, "CHANGELOG.md"
        ):
            findings.append(
                _finding(
                    "ci-cd",
                    "missing-changelog",
                    "Open-source release history is not documented.",
                    "Keep a concise changelog or document the generated release-note authority.",
                    severity="low",
                )
            )
    if (
        facts.package_managers
        and workflow_texts
        and not any("cache" in text.lower() for text in workflow_texts)
    ):
        findings.append(
            _finding(
                "ci-cd",
                "missing-build-cache",
                "CI does not contain an obvious dependency/build cache.",
                "Use the package-manager-native cache with lockfile-derived keys.",
                severity="low",
            )
        )
    return findings


def reliability_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    types = set(facts.project_types)
    runtime_types = {"api", "backend-service", "data-pipeline", "container", "kubernetes"}
    if types & runtime_types and not facts.observability:
        findings.append(
            _finding(
                "reliability",
                "missing-observability",
                "No metrics, tracing, structured logging, or error-monitoring integration was "
                "discovered.",
                "Add the smallest applicable signals with correlation IDs and ownership.",
            )
        )
    files, _ = iter_project_files(root)
    for path in files:
        if path.suffix != ".py" or facts.graph.scope(
            path.relative_to(root).as_posix()
        ) in NON_RUNTIME | {"test"}:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(read_text_bounded(path, 2_000_000, root=root))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted_name(node.func)
            network_call = name.startswith(("requests.", "httpx.", "urllib3.")) and name.rsplit(
                ".", 1
            )[-1] in {"get", "post", "put", "patch", "delete", "request"}
            bounded_process = name in {"subprocess.run", "subprocess.call", "subprocess.check_call"}
            if (network_call or bounded_process) and not any(
                keyword.arg == "timeout" for keyword in node.keywords
            ):
                findings.append(
                    _finding(
                        "reliability",
                        "missing-timeout",
                        f"{name} call has no explicit timeout.",
                        "Set an operation-appropriate timeout and test timeout/failure behavior.",
                        severity="medium",
                        file=path.relative_to(root).as_posix(),
                        line=node.lineno,
                    )
                )
    return findings


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def operations_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    operational_text = "\n".join(
        read_text_bounded(root / rel, 2_000_000, root=root, errors="ignore")[:100_000].lower()
        for rel in facts.docs
        if rel.lower().endswith(".md") and (root / rel).is_file()
    )
    evidence = {
        "health/verification": ("health", "verify", "verification"),
        "rollback/teardown": ("rollback", "teardown", "destroy"),
        "state/recovery": ("backup", "recovery", "state"),
        "cost ownership": ("cost", "charges", "billing"),
    }
    missing = [
        category
        for category, markers in evidence.items()
        if not any(marker in operational_text for marker in markers)
    ]
    if not missing:
        return []
    return [
        _finding(
            "operations",
            "missing-runbook",
            f"Operational guidance is missing: {', '.join(missing)}.",
            "Document the project-appropriate health, recovery, state, and cost procedures.",
            severity="low",
        )
    ]


def documentation_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    readme = next(
        (
            root / name
            for name in ("README.md", "README.rst", "README")
            if safe_regular_file(root / name, root)
        ),
        None,
    )
    if not readme:
        return [
            _finding(
                "documentation",
                "missing-readme",
                "README is missing.",
                "Add a concise overview, setup, usage, and troubleshooting path.",
                file="README.md",
                remediation=Remediation(
                    kind="template",
                    target="README.md",
                    safe=True,
                    description="add README baseline",
                ),
            )
        ]
    text = read_text_bounded(readme, 2_000_000, root=root, errors="ignore")[:100_000].lower()
    if facts.graph.lifecycle == "documentation-project":
        return []
    concepts = {
        "install": (
            "install",
            "setup",
            "getting started",
            "how to use",
            'module "',
            "terraform-docs",
        ),
        "usage": ("usage", "commands", "example", "how to use", 'module "', "terraform-docs"),
    }
    missing = [
        name for name, aliases in concepts.items() if not any(alias in text for alias in aliases)
    ]
    if not missing:
        return []
    return [
        _finding(
            "documentation",
            "readme-incomplete",
            f"README does not mention: {', '.join(missing)}.",
            "Add the missing reader-oriented sections.",
            severity="low",
            file=readme.name,
        )
    ]


def ai_context_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    findings = []
    hashes: dict[str, list[str]] = {}
    for rel in facts.ai_context_files:
        path = root / rel
        try:
            content = read_bytes_bounded(path, 2_000_000, root=root)
        except OSError:
            continue
        if len(content) > 32_000:
            findings.append(
                _finding(
                    "ai-context",
                    "oversized-context",
                    f"AI context file is {len(content)} bytes.",
                    "Split or compress instructions so agents receive only relevant context.",
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
    if set(facts.project_types) & {"ai-app", "ai-agent"}:
        files, _ = iter_project_files(root)
        rels = [path.relative_to(root).as_posix().lower() for path in files]
        if not any("eval" in rel for rel in rels) and "regression" not in facts.test_capabilities:
            findings.append(
                _finding(
                    "ai-context",
                    "missing-evals",
                    "AI project has no discovered regression eval dataset or harness.",
                    "Add versioned representative and adversarial eval cases with recorded "
                    "settings.",
                )
            )
        if not facts.ai_context_files:
            findings.append(
                _finding(
                    "ai-context",
                    "missing-ai-instructions",
                    "AI project has no discovered agent/context instruction boundary.",
                    "Document tool permissions, untrusted-input boundaries, models, and budgets.",
                )
            )
    return findings


def completeness_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    security_workflow = bool(list(root.glob(".github/workflows/*security*")))
    families = {
        "testing": bool(facts.tests)
        or bool(
            set(facts.test_capabilities)
            & {"unit", "integration", "smoke", "end-to-end", "contract"}
        ),
        "security": _conventional_file(root, "SECURITY.md") or security_workflow,
        "ci-cd": bool(facts.ci),
        "documentation": bool(facts.docs),
        "reliability": bool(facts.observability),
    }
    types = set(facts.project_types)
    applicable = {"testing", "security", "documentation"}
    if facts.is_git:
        applicable.add("ci-cd")
    if types & {"api", "backend-service", "data-pipeline", "container", "kubernetes"}:
        applicable.add("reliability")
    findings = []
    for family in sorted(applicable):
        if families[family]:
            continue
        if family == "security":
            message = "No repository-managed security policy or check workflow was discovered."
            recommendation = (
                "Add a project-appropriate secret or infrastructure-security check in CI, "
                "or document where that control is owned."
            )
        else:
            message = f"Applicable '{family}' capability has no supporting project evidence."
            recommendation = (
                f"Enable the {family} blueprint and add the smallest project-appropriate baseline."
            )
        findings.append(
            _finding(
                "completeness",
                "missing-family-evidence",
                message,
                recommendation,
                severity="medium" if family in {"testing", "security"} else "low",
                evidence=[f"profiles: {', '.join(facts.project_types) or 'default'}"],
                rule_id=f"blueprint-ai/completeness/missing/{family}",
            )
        )
    return findings


def no_builtin_checks(root: Path, facts: ProjectFacts) -> list[Finding]:
    return []


def _has_code(facts: ProjectFacts) -> bool:
    if facts.graph.lifecycle == "documentation-project":
        return False
    return bool(set(facts.languages) - {"Markdown", "YAML", "HCL"})


BLUEPRINTS: dict[str, Blueprint] = {
    "identity": Blueprint(
        "identity",
        "Project, package, and CLI naming consistency",
        lambda f: bool(f.manifests),
        identity_checks,
        False,
        "no native package manifest was discovered",
    ),
    "repository": Blueprint(
        "repository",
        "Repository structure, hygiene, OSS readiness, and maintenance",
        lambda f: True,
        repository_checks,
    ),
    "code-quality": Blueprint(
        "code-quality",
        "Native formatting, linting, static correctness, and types",
        _has_code,
        no_builtin_checks,
        False,
        "no source-language files were discovered",
    ),
    "code-design": Blueprint(
        "code-design",
        "Complexity, duplication, dead code, comments, and resource patterns",
        _has_code,
        code_design_checks,
        True,
        "no source-language files were discovered",
        lambda f: "Python" not in f.languages,
        "deterministic code-design checks currently cover Python source only",
    ),
    "architecture": Blueprint(
        "architecture",
        "Boundaries, coupling, cycles, and design-pattern review",
        _has_code,
        no_builtin_checks,
        True,
        "no source-language files were discovered",
    ),
    "testing": Blueprint(
        "testing",
        "Applicable test capabilities, execution, generation, and completeness",
        lambda f: _has_code(f) or bool(f.iac),
        testing_checks,
        True,
        "no testable source-language or infrastructure files were discovered",
    ),
    "api-data-config": Blueprint(
        "api-data-config",
        "API contracts, schemas, migrations, configuration, and secrets",
        lambda f: bool(
            f.api_specs
            or f.migrations
            or _application_config_files(f)
            or set(f.project_types) & {"api", "data-pipeline"}
        ),
        api_data_config_checks,
        True,
        "no API, schema, migration, or application configuration evidence was discovered",
        lambda f: (
            not bool(f.api_specs)
            and bool(
                _application_config_files(f) or set(f.project_types) & {"api", "data-pipeline"}
            )
        ),
        "API/config review is partial because no machine-readable API contract was discovered",
    ),
    "security": Blueprint(
        "security",
        "SAST, secret scanning, and repository security posture",
        lambda f: f.file_count > 0,
        security_checks,
        True,
    ),
    "supply-chain": Blueprint(
        "supply-chain",
        "Dependencies, SCA, SBOM, licenses, signing, and provenance",
        lambda f: bool(f.manifests or f.containers or f.iac),
        supply_chain_checks,
        True,
        "no dependencies or build artifacts were discovered",
    ),
    "iac": Blueprint(
        "iac",
        "Terraform/OpenTofu and CloudFormation validation and policy",
        lambda f: bool(f.iac),
        iac_checks,
        True,
        "no supported infrastructure-as-code was discovered",
    ),
    "containers": Blueprint(
        "containers",
        "Dockerfile, image, and container runtime posture",
        lambda f: bool(f.containers),
        container_checks,
        True,
        "no container build or compose file was discovered",
    ),
    "kubernetes": Blueprint(
        "kubernetes",
        "Kubernetes, Helm, and Kustomize validation and security",
        lambda f: bool(f.kubernetes),
        kubernetes_checks,
        True,
        "no Kubernetes, Helm, or Kustomize evidence was discovered",
    ),
    "ci-cd": Blueprint(
        "ci-cd",
        "CI correctness, workflow security, builds, deployments, and releases",
        lambda f: bool(f.ci),
        ci_checks,
        True,
        "no supported CI configuration was discovered",
    ),
    "reliability": Blueprint(
        "reliability",
        "Timeouts, retries, idempotency, shutdown, health, and scaling",
        lambda f: (
            bool(
                set(f.project_types)
                & {"api", "backend-service", "data-pipeline", "container", "kubernetes"}
            )
            or bool(f.iac)
        ),
        reliability_checks,
        True,
        "no long-running or production-like runtime was discovered",
        lambda f: (
            bool(f.iac)
            and not bool(
                set(f.project_types)
                & {"api", "backend-service", "data-pipeline", "container", "kubernetes"}
            )
        ),
        "IaC reliability review requires model judgment beyond current deterministic checks",
    ),
    "operations": Blueprint(
        "operations",
        "Observability, runbooks, alerts, backup/recovery, and cost evidence",
        lambda f: (
            bool(
                set(f.project_types)
                & {"api", "backend-service", "data-pipeline", "container", "kubernetes"}
            )
            or bool(f.iac)
        ),
        operations_checks,
        True,
        "no operational service or platform evidence was discovered",
    ),
    "documentation": Blueprint(
        "documentation",
        "README, architecture docs, links, drift, and presentation",
        lambda f: True,
        documentation_checks,
        True,
    ),
    "ai-context": Blueprint(
        "ai-context",
        "AI instructions, providers, prompts, permissions, evals, and budgets",
        lambda f: bool(f.ai_context_files or set(f.project_types) & {"ai-app", "ai-agent"}),
        ai_context_checks,
        True,
        "no AI project or instruction context was discovered",
    ),
    "completeness": Blueprint(
        "completeness",
        "Applicable best-practice families missing from the project",
        lambda f: True,
        completeness_checks,
    ),
}

CORE = [
    "identity",
    "repository",
    "code-quality",
    "code-design",
    "security",
    "supply-chain",
    "testing",
    "documentation",
    "completeness",
]
SERVICE = ["architecture", "api-data-config", "ci-cd", "reliability", "operations"]
PROFILES = {
    "api-contract": ["repository", "security", "api-data-config", "documentation"],
    "documentation-project": ["repository", "security", "ci-cd", "documentation"],
    "template-generator": CORE + ["architecture", "ci-cd", "api-data-config"],
    "ai-context": ["ai-context"],
    "default": CORE,
    "library": CORE + ["architecture", "ci-cd"],
    "cli": CORE + ["architecture", "ci-cd", "reliability"],
    "api": CORE + SERVICE,
    "backend-service": CORE + SERVICE,
    "frontend": CORE + ["architecture", "ci-cd", "reliability"],
    "web-app": CORE + ["architecture", "api-data-config", "ci-cd", "reliability"],
    "full-stack": CORE + SERVICE,
    "iac": [
        "repository",
        "security",
        "supply-chain",
        "testing",
        "iac",
        "reliability",
        "operations",
        "ci-cd",
        "documentation",
        "completeness",
    ],
    "terraform": [
        "repository",
        "security",
        "supply-chain",
        "testing",
        "iac",
        "reliability",
        "operations",
        "ci-cd",
        "documentation",
        "completeness",
    ],
    "container": CORE + ["containers", "ci-cd", "reliability"],
    "kubernetes": CORE + ["containers", "kubernetes", "ci-cd", "reliability", "operations"],
    "data-pipeline": CORE
    + ["architecture", "api-data-config", "ci-cd", "reliability", "operations"],
    "ai-app": CORE + ["architecture", "api-data-config", "ai-context", "reliability"],
    "ai-agent": CORE + ["architecture", "api-data-config", "ai-context", "reliability"],
    "open-source": CORE + ["architecture", "ci-cd"],
    "portfolio": ["identity", "repository", "documentation", "security", "completeness"],
    "production": list(BLUEPRINTS),
}


def get_blueprint(name: str) -> Blueprint:
    try:
        return BLUEPRINTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown blueprint: {name}") from exc


def _conventional_file(root: Path, name: str) -> bool:
    aliases = {
        "LICENSE": {"license", "license.txt", "license.md", "copying", "copying.txt"},
        "CHANGELOG.md": {
            "changelog.md",
            "changes.md",
            "changes.rst",
            "changelog.rst",
            "release-notes.md",
        },
    }.get(name, {name.lower()})
    return any(
        path.name.lower() in aliases
        for directory in (root, root / ".github", root / "docs")
        if directory.is_dir()
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
    )


def _placeholder(value: str) -> bool:
    lower = value.strip().lower()
    return (
        not lower
        or lower
        in {
            "changethis",
            "changeme",
            "change-me",
            "change_this",
            "change-this",
            "your-secret-key",
            "your_api_key",
            "password",
            "secret",
            "null",
            "none",
        }
        or lower.startswith(("${", "{{", "<", "your-", "your_"))
        or bool(re.fullmatch(r"(?:x+|0+|example[-_\w]*|placeholder[-_\w]*)", lower))
        or bool(re.fullmatch(r"gh[pousr]_example[a-z0-9_]*", lower))
    )


def _public_search_key(text: str, offset: int, rel: str) -> bool:
    # Search-only configuration has a narrow public-key contract. Generic apiKey stays flagged.
    return "docusaurus.config." in Path(rel).name and bool(
        re.search(r"algolia\s*:\s*\{[^}]*$", text[max(0, offset - 1500) : offset])
    )
