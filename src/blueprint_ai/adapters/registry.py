from __future__ import annotations

from pathlib import Path

from blueprint_ai.core import ProjectFacts

from .base import (
    ExternalToolAdapter,
    parse_checkov,
    parse_grype,
    parse_json_lines,
    parse_json_list,
    parse_kubeconform,
    parse_lines,
    parse_osv,
    parse_output_paths,
    parse_ruff,
    parse_sarif,
    parse_semgrep,
    parse_terraform,
    parse_trivy,
)


def known_tools() -> list[ExternalToolAdapter]:
    """Return replaceable tool definitions; selection happens from discovered evidence."""
    return [
        ExternalToolAdapter(
            "ruff", "code-quality", ["check", ".", "--output-format", "json"], parse_ruff
        ),
        ExternalToolAdapter(
            "mypy", "code-quality", [".", "--show-error-codes", "--no-error-summary"], parse_lines
        ),
        ExternalToolAdapter(
            "biome", "code-quality", ["check", ".", "--reporter=json"], parse_json_list
        ),
        ExternalToolAdapter("eslint", "code-quality", [".", "--format", "json"], parse_json_list),
        ExternalToolAdapter("tsc", "code-quality", ["--noEmit", "--pretty", "false"], parse_lines),
        ExternalToolAdapter(
            "go-vet", "code-quality", ["vet", "./..."], parse_lines, executable="go"
        ),
        ExternalToolAdapter(
            "cargo-fmt", "code-quality", ["fmt", "--", "--check"], parse_lines, executable="cargo"
        ),
        ExternalToolAdapter(
            "cargo-clippy",
            "code-quality",
            ["clippy", "--message-format=short", "--", "-D", "warnings"],
            parse_lines,
            executable="cargo",
        ),
        ExternalToolAdapter("shellcheck", "code-quality", ["--format=json"], parse_json_list),
        ExternalToolAdapter(
            "semgrep",
            "security",
            ["scan", "--config", ".semgrep.yml", "--json", "--metrics=off", "."],
            parse_semgrep,
        ),
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
            "osv-scanner", "supply-chain", ["scan", "--format", "json", "-r", "."], parse_osv
        ),
        ExternalToolAdapter(
            "trivy",
            "supply-chain",
            ["fs", "--format", "json", "--scanners", "vuln,misconfig,secret", "."],
            parse_trivy,
        ),
        ExternalToolAdapter(
            "syft",
            "supply-chain",
            ["scan", "dir:.", "-o", "json"],
            parse_json_list,
            expected_codes={0},
        ),
        ExternalToolAdapter("grype", "supply-chain", ["dir:.", "-o", "json"], parse_grype),
        ExternalToolAdapter("terraform", "iac", ["validate", "-json"], parse_terraform),
        ExternalToolAdapter(
            "terraform-fmt",
            "iac",
            ["fmt", "-check", "-recursive", "-diff"],
            parse_lines,
            executable="terraform",
        ),
        ExternalToolAdapter("tflint", "iac", ["--format", "json"], parse_json_list),
        ExternalToolAdapter("checkov", "iac", ["-d", ".", "-o", "json", "--quiet"], parse_checkov),
        ExternalToolAdapter("cfn-lint", "iac", ["--format", "json"], parse_json_list),
        ExternalToolAdapter("hadolint", "containers", ["--format", "json"], parse_json_list),
        ExternalToolAdapter(
            "kubeconform", "kubernetes", ["-output", "json", "-summary"], parse_kubeconform
        ),
        ExternalToolAdapter(
            "kube-linter", "kubernetes", ["lint", ".", "--format", "json"], parse_json_list
        ),
        ExternalToolAdapter(
            "kubescape",
            "kubernetes",
            ["scan", ".", "--format", "json", "--no-submit"],
            parse_json_list,
        ),
        ExternalToolAdapter("helm", "kubernetes", ["lint"], parse_lines),
        ExternalToolAdapter("kustomize", "kubernetes", ["build"], parse_lines),
        ExternalToolAdapter("actionlint", "ci-cd", ["-format", "{{json .}}"], parse_json_lines),
        ExternalToolAdapter(
            "zizmor", "ci-cd", ["--format", "sarif", ".github/workflows"], parse_sarif
        ),
        ExternalToolAdapter(
            "spectral", "api-data-config", ["lint", "--format", "json"], parse_json_list
        ),
        ExternalToolAdapter(
            "markdownlint-cli2", "documentation", ["**/*.md", "#node_modules"], parse_lines
        ),
        ExternalToolAdapter(
            "lychee", "documentation", ["--format", "json", "**/*.md"], parse_json_list
        ),
        ExternalToolAdapter("pytest", "testing", ["-q"], parse_lines, executable="pytest"),
        ExternalToolAdapter(
            "npm-test", "testing", ["test", "--", "--run"], parse_lines, executable="npm"
        ),
        ExternalToolAdapter("go-test", "testing", ["test", "./..."], parse_lines, executable="go"),
        ExternalToolAdapter(
            "cargo-test", "testing", ["test", "--quiet"], parse_lines, executable="cargo"
        ),
        ExternalToolAdapter("maven-test", "testing", ["test", "-q"], parse_lines, executable="mvn"),
        ExternalToolAdapter(
            "gradle-test", "testing", ["test", "--console=plain"], parse_lines, executable="gradle"
        ),
    ]


def _configured(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).exists() for name in names)


def _quality_route(
    facts: ProjectFacts, root: Path, tools: dict[str, ExternalToolAdapter]
) -> list[str]:
    languages = set(facts.languages)
    names: list[str] = []
    if "Python" in languages:
        names.append("ruff")
        configured_mypy = _configured(root, ("mypy.ini", ".mypy.ini"))
        if (root / "pyproject.toml").is_file():
            configured_mypy |= "mypy" in (root / "pyproject.toml").read_text(errors="ignore")
        if configured_mypy:
            names.append("mypy")
    if languages & {"JavaScript", "TypeScript"}:
        eslint_config = any(root.glob("eslint.config.*")) or _configured(
            root, (".eslintrc", ".eslintrc.json", ".eslintrc.js")
        )
        if eslint_config:
            names.append("eslint")
        if _configured(root, ("biome.json", "biome.jsonc")):
            names.append("biome")
    if "TypeScript" in languages and (root / "tsconfig.json").is_file():
        names.append("tsc")
    if "Go" in languages:
        names.append("go-vet")
        go_files = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.go")
            if ".git" not in path.parts
        ]
        if go_files:
            tools["gofmt"] = ExternalToolAdapter(
                "gofmt", "code-quality", ["-l", *go_files], parse_output_paths, expected_codes={0}
            )
            names.append("gofmt")
    if "Rust" in languages:
        names.extend(["cargo-fmt", "cargo-clippy"])
    if "Java" in languages:
        if (root / "pom.xml").is_file():
            maven = str(root / "mvnw") if (root / "mvnw").is_file() else "mvn"
            tools["maven-check"] = ExternalToolAdapter(
                "maven-check",
                "code-quality",
                ["verify", "-DskipTests", "-q"],
                parse_lines,
                executable=maven,
            )
            names.append("maven-check")
        if _configured(root, ("build.gradle", "build.gradle.kts")):
            gradle = str(root / "gradlew") if (root / "gradlew").is_file() else "gradle"
            tools["gradle-check"] = ExternalToolAdapter(
                "gradle-check",
                "code-quality",
                ["check", "-x", "test", "--console=plain"],
                parse_lines,
                executable=gradle,
            )
            names.append("gradle-check")
    if "Shell" in languages:
        shell_files = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.sh")
            if not set(path.parts) & {".git", ".venv", "node_modules", "vendor"}
        ]
        tools["shellcheck"].args.extend(shell_files)
        names.append("shellcheck")
    return names


def _testing_route(
    facts: ProjectFacts, root: Path, tools: dict[str, ExternalToolAdapter]
) -> list[str]:
    if not facts.tests:
        return []
    languages = set(facts.languages)
    names = []
    if "Python" in languages:
        names.append("pytest")
    if languages & {"JavaScript", "TypeScript"}:
        tools["npm-test"].args = (
            ["test", "--", "--run"]
            if "vitest" in facts.frameworks
            else ["test", "--", "--runInBand"]
            if "jest" in facts.frameworks
            else ["test"]
        )
        names.append("npm-test")
    if "Go" in languages:
        names.append("go-test")
    if "Rust" in languages:
        names.append("cargo-test")
    if "Java" in languages:
        if (root / "pom.xml").is_file():
            if (root / "mvnw").is_file():
                tools["maven-test"].executable = str(root / "mvnw")
            names.append("maven-test")
        else:
            if (root / "gradlew").is_file():
                tools["gradle-test"].executable = str(root / "gradlew")
            names.append("gradle-test")
    return names


def _ecosystem_route(
    facts: ProjectFacts,
    blueprint: str,
    root: Path,
    tools: dict[str, ExternalToolAdapter],
) -> list[str]:
    names: list[str] = []
    if blueprint == "security":
        names.append("gitleaks")
        if _configured(root, (".semgrep.yml", ".semgrep.yaml")):
            if (root / ".semgrep.yaml").is_file():
                tools["semgrep"].args[2] = ".semgrep.yaml"
            names.append("semgrep")
    elif blueprint == "supply-chain":
        names.extend(["osv-scanner", "trivy"])
        if (root / ".syft.yaml").is_file():
            names.append("syft")
        if (root / ".grype.yaml").is_file():
            names.append("grype")
    elif blueprint == "iac":
        if "terraform" in facts.iac:
            names.extend(["terraform-fmt", "terraform", "tflint", "checkov"])
        if "cloudformation" in facts.iac:
            cloudformation = [
                path.relative_to(root).as_posix()
                for path in root.rglob("*.y*ml")
                if "AWSTemplateFormatVersion" in path.read_text(encoding="utf-8", errors="ignore")
                or "AWS::Serverless-2016-10-31" in path.read_text(encoding="utf-8", errors="ignore")
            ]
            tools["cfn-lint"].args.extend(cloudformation)
            names.extend(["cfn-lint", "checkov"])
    elif blueprint == "containers":
        dockerfiles = [rel for rel in facts.containers if Path(rel).name.lower() == "dockerfile"]
        if dockerfiles:
            tools["hadolint"].args.extend(dockerfiles)
            names.append("hadolint")
    elif blueprint == "kubernetes":
        special = {"chart.yaml", "kustomization.yaml", "kustomization.yml"}
        manifests = [
            rel
            for rel in facts.kubernetes
            if Path(rel).suffix in {".yaml", ".yml"} and Path(rel).name.lower() not in special
        ]
        if manifests:
            tools["kubeconform"].args.extend(manifests)
        names.extend(["kubeconform", "kube-linter", "kubescape"])
        chart_dirs = sorted(
            {
                str((root / rel).parent.relative_to(root))
                for rel in facts.kubernetes
                if Path(rel).name.lower() == "chart.yaml"
            }
        )
        if chart_dirs:
            tools["helm"].args.extend(chart_dirs)
            names.append("helm")
        kustomize_dirs = sorted(
            {
                str((root / rel).parent.relative_to(root))
                for rel in facts.kubernetes
                if Path(rel).name.lower().startswith("kustomization")
            }
        )
        if kustomize_dirs:
            tools["kustomize"].args.extend(kustomize_dirs)
            names.append("kustomize")
    elif blueprint == "ci-cd" and "github-actions" in facts.ci:
        names.extend(["actionlint", "zizmor"])
    elif blueprint == "api-data-config" and facts.api_specs:
        tools["spectral"].args.extend(facts.api_specs)
        names.append("spectral")
    elif blueprint == "documentation" and facts.docs:
        names.extend(["markdownlint-cli2", "lychee"])
    return names


def applicable_adapters(
    facts: ProjectFacts,
    blueprint: str,
    overrides: dict[str, str] | None = None,
    *,
    authorized_target: str | None = None,
) -> list[ExternalToolAdapter]:
    root = Path(facts.path)
    tools = {adapter.name: adapter for adapter in known_tools()}
    if blueprint == "code-quality":
        names = _quality_route(facts, root, tools)
    elif blueprint == "testing":
        names = _testing_route(facts, root, tools)
    else:
        names = _ecosystem_route(facts, blueprint, root, tools)

    if blueprint == "security" and authorized_target:
        tools["zap-baseline"] = ExternalToolAdapter(
            "zap-baseline",
            "security",
            ["-t", authorized_target, "-m", "2", "-I"],
            parse_lines,
            expected_codes={0, 1, 2},
            executable="zap-baseline.py",
            install=(
                "install OWASP ZAP's official baseline script or use its pinned container image"
            ),
        )
        names.append("zap-baseline")

    selected = [tools[name] for name in dict.fromkeys(names)]
    for adapter in selected:
        local = root / "node_modules" / ".bin" / Path(adapter.executable).name
        if local.is_file() and adapter.name not in {"npm-test"}:
            adapter.executable = str(local)
        if overrides and adapter.name in overrides:
            adapter.executable = overrides[adapter.name]
    return selected
