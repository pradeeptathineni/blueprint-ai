from __future__ import annotations

from pathlib import Path

from blueprint_ai.core import ProjectFacts

from .base import (
    ExternalToolAdapter,
    parse_checkov,
    parse_json_lines,
    parse_json_list,
    parse_lines,
    parse_osv,
    parse_ruff,
    parse_terraform,
    parse_trivy,
)


def known_tools() -> list[ExternalToolAdapter]:
    return [
        ExternalToolAdapter(
            "ruff", "code-quality", ["check", ".", "--output-format", "json"], parse_ruff
        ),
        ExternalToolAdapter(
            "biome", "code-quality", ["check", ".", "--reporter=json"], parse_json_list
        ),
        ExternalToolAdapter("eslint", "code-quality", [".", "--format", "json"], parse_json_list),
        ExternalToolAdapter("shellcheck", "code-quality", ["--format=json", "."], parse_json_list),
        ExternalToolAdapter(
            "gitleaks",
            "security",
            [
                "detect",
                "--source",
                ".",
                "--report-format",
                "json",
                "--report-path",
                "-",
                "--no-banner",
            ],
            parse_json_list,
        ),
        ExternalToolAdapter(
            "osv-scanner", "security", ["scan", "--format", "json", "-r", "."], parse_osv
        ),
        ExternalToolAdapter(
            "trivy",
            "security",
            ["fs", "--format", "json", "--scanners", "vuln,misconfig,secret", "."],
            parse_trivy,
        ),
        ExternalToolAdapter("terraform", "iac", ["validate", "-json"], parse_terraform),
        ExternalToolAdapter("tflint", "iac", ["--format", "json"], parse_json_list),
        ExternalToolAdapter("checkov", "iac", ["-d", ".", "-o", "json", "--quiet"], parse_checkov),
        ExternalToolAdapter(
            "cfn-lint", "iac", ["--format", "json", "**/*.yaml", "**/*.yml"], parse_json_list
        ),
        ExternalToolAdapter("actionlint", "ci-cd", ["-format", "{{json .}}"], parse_json_lines),
        ExternalToolAdapter(
            "zizmor", "ci-cd", ["--format", "json", ".github/workflows"], parse_json_list
        ),
        ExternalToolAdapter(
            "markdownlint-cli2", "documentation", ["**/*.md", "#node_modules"], parse_lines
        ),
        ExternalToolAdapter(
            "lychee", "documentation", ["--format", "json", "**/*.md"], parse_json_list
        ),
    ]


def applicable_adapters(
    facts: ProjectFacts,
    blueprint: str,
    overrides: dict[str, str] | None = None,
) -> list[ExternalToolAdapter]:
    languages = set(facts.languages)
    root = Path(facts.path)
    selected = []
    for adapter in known_tools():
        if adapter.blueprint != blueprint:
            continue
        if adapter.name == "ruff" and "Python" not in languages:
            continue
        if adapter.name in {"biome", "eslint"} and not languages & {"JavaScript", "TypeScript"}:
            continue
        if adapter.name == "eslint" and not any(root.glob("eslint.config.*")):
            continue
        if adapter.name == "shellcheck":
            continue
        if adapter.name in {"terraform", "tflint"} and "terraform" not in facts.iac:
            continue
        if adapter.name == "cfn-lint" and "cloudformation" not in facts.iac:
            continue
        if adapter.name in {"actionlint", "zizmor"} and "github-actions" not in facts.ci:
            continue
        selected.append(adapter)
    if blueprint == "code-quality" and "Shell" in languages:
        shell_files = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.sh")
            if not any(part in {".git", ".venv", "node_modules", "vendor"} for part in path.parts)
        ]
        if shell_files:
            selected.append(
                ExternalToolAdapter(
                    "shellcheck", "code-quality", ["--format=json", *shell_files], parse_json_list
                )
            )
    if blueprint == "testing" and facts.tests:
        if "Python" in languages:
            selected.append(
                ExternalToolAdapter("pytest", "testing", ["-q"], parse_lines, executable="pytest")
            )
        if {"JavaScript", "TypeScript"} & languages:
            args = (
                ["test", "--", "--run"]
                if "vitest" in facts.frameworks
                else ["test", "--", "--runInBand"]
            )
            selected.append(
                ExternalToolAdapter("npm-test", "testing", args, parse_lines, executable="npm")
            )
    for adapter in selected:
        if overrides and adapter.name in overrides:
            adapter.executable = overrides[adapter.name]
    return selected
