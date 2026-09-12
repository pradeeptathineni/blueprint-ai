from __future__ import annotations

from pathlib import Path

from blueprint_ai.core import ProjectFacts
from blueprint_ai.core.project import NON_RUNTIME
from blueprint_ai.discovery import iter_project_files
from blueprint_ai.discovery.project import SKIP_DIRS

from .base import (
    ExternalToolAdapter,
    parse_actionlint,
    parse_checkov,
    parse_grype,
    parse_json_list,
    parse_kubeconform,
    parse_lines,
    parse_lychee,
    parse_markdownlint,
    parse_osv,
    parse_output_paths,
    parse_ruff,
    parse_sarif,
    parse_semgrep,
    parse_shellcheck,
    parse_terraform,
    parse_tflint,
    parse_trivy,
)


def known_tools() -> list[ExternalToolAdapter]:
    """Return replaceable tool definitions; selection happens from discovered evidence."""
    tools = [
        ExternalToolAdapter(
            "ruff",
            "code-quality",
            ["check", ".", "--output-format", "json", "--no-cache"],
            parse_ruff,
        ),
        ExternalToolAdapter(
            "mypy",
            "code-quality",
            [".", "--show-error-codes", "--no-error-summary"],
            parse_lines,
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "biome", "code-quality", ["check", ".", "--reporter=json"], parse_json_list
        ),
        ExternalToolAdapter(
            "eslint",
            "code-quality",
            [".", "--format", "json"],
            parse_json_list,
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "tsc",
            "code-quality",
            ["--noEmit", "--pretty", "false"],
            parse_lines,
            executes_project_code=True,
        ),
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
            executes_project_code=True,
        ),
        ExternalToolAdapter("shellcheck", "code-quality", ["--format=json"], parse_shellcheck),
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
                "dir",
                "--report-format",
                "json",
                "--report-path",
                "-",
                "--no-banner",
                "--redact",
                ".",
            ],
            parse_json_list,
        ),
        ExternalToolAdapter(
            "osv-scanner",
            "supply-chain",
            ["scan", "source", "--format", "json", "-r", "."],
            parse_osv,
            network_required=True,
        ),
        ExternalToolAdapter(
            "trivy",
            "supply-chain",
            [
                "fs",
                "--format",
                "json",
                "--scanners",
                "vuln,misconfig",
                "--no-progress",
                "--config",
                "/dev/null",
                "--tf-exclude-downloaded-modules",
                *[
                    argument
                    for directory in sorted(SKIP_DIRS)
                    for argument in ("--skip-dirs", directory)
                ],
                ".",
            ],
            parse_trivy,
            network_required=True,
        ),
        ExternalToolAdapter(
            "syft",
            "supply-chain",
            ["scan", "dir:.", "-o", "json"],
            parse_json_list,
            expected_codes={0},
        ),
        ExternalToolAdapter(
            "grype", "supply-chain", ["dir:.", "-o", "json"], parse_grype, network_required=True
        ),
        ExternalToolAdapter(
            "terraform",
            "iac",
            ["validate", "-json"],
            parse_terraform,
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "terraform-fmt",
            "iac",
            ["fmt", "-check", "-recursive", "-diff"],
            parse_lines,
            executable="terraform",
        ),
        ExternalToolAdapter(
            "terraform-test",
            "testing",
            ["test", "-no-color"],
            parse_lines,
            executable="terraform",
            expected_codes={0, 1},
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "tflint",
            "iac",
            ["--recursive", "--format", "json"],
            parse_tflint,
            expected_codes={0, 2},
            executes_project_code=True,
        ),
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
        ExternalToolAdapter("actionlint", "ci-cd", ["-format", "{{json .}}"], parse_actionlint),
        ExternalToolAdapter(
            "zizmor",
            "ci-cd",
            ["--offline", "--format", "sarif", ".github/workflows"],
            parse_sarif,
        ),
        ExternalToolAdapter(
            "spectral",
            "api-data-config",
            ["lint", "--format", "json"],
            parse_json_list,
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "markdownlint-cli2",
            "documentation",
            [],
            parse_markdownlint,
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "lychee",
            "documentation",
            [
                "--format",
                "json",
                "--no-progress",
                "--timeout",
                "10",
                "--max-retries",
                "1",
                "--config",
                "/dev/null",
                "--exclude-all-private",
                "--scheme",
                "https",
                "--scheme",
                "http",
            ],
            parse_lychee,
            expected_codes={0, 2},
            network_required=True,
            default_timeout=30,
        ),
        ExternalToolAdapter(
            "pytest",
            "testing",
            ["-q"],
            parse_lines,
            executable="pytest",
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "npm-test",
            "testing",
            ["test", "--", "--run"],
            parse_lines,
            executable="npm",
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "go-test",
            "testing",
            ["test", "./..."],
            parse_lines,
            executable="go",
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "cargo-test",
            "testing",
            ["test", "--quiet"],
            parse_lines,
            executable="cargo",
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "maven-test",
            "testing",
            ["test", "-q"],
            parse_lines,
            executable="mvn",
            executes_project_code=True,
        ),
        ExternalToolAdapter(
            "gradle-test",
            "testing",
            ["test", "--console=plain"],
            parse_lines,
            executable="gradle",
            executes_project_code=True,
        ),
    ]
    recommended = {
        "ruff": ">=0.13,<1",
        "pytest": ">=9.0.3,<10",
        "semgrep": ">=1,<2",
        "gitleaks": ">=8,<9",
        "osv-scanner": ">=2,<3",
        "trivy": ">=0.60,<1",
        "syft": ">=1,<2",
        "grype": ">=0.90,<1",
    }
    for tool in tools:
        tool.recommended_version = recommended.get(tool.name)
    return tools


def _configured(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).exists() for name in names)


def _terraform_roots(root: Path) -> list[str]:
    directories = {
        path.parent.relative_to(root)
        for path in iter_project_files(root)[0]
        if path.suffix == ".tf"
    }
    locked = {
        directory
        for directory in directories
        if (root / directory / ".terraform.lock.hcl").is_file()
    }
    selected = locked or {
        directory
        for directory in directories
        if not any(parent in directories for parent in directory.parents if parent != directory)
    }
    return sorted(
        (directory.as_posix() for directory in selected),
        key=lambda rel: (rel != "terraform", rel != ".", rel),
    )


def _quality_route(
    facts: ProjectFacts,
    root: Path,
    tools: dict[str, ExternalToolAdapter],
    allow_project_executables: bool = False,
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
            for path in iter_project_files(root)[0]
            if path.suffix == ".go"
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
            maven = (
                str(root / "mvnw")
                if allow_project_executables and (root / "mvnw").is_file()
                else "mvn"
            )
            tools["maven-check"] = ExternalToolAdapter(
                "maven-check",
                "code-quality",
                ["verify", "-DskipTests", "-q"],
                parse_lines,
                executable=maven,
                executes_project_code=True,
            )
            names.append("maven-check")
        if _configured(root, ("build.gradle", "build.gradle.kts")):
            gradle = (
                str(root / "gradlew")
                if allow_project_executables and (root / "gradlew").is_file()
                else "gradle"
            )
            tools["gradle-check"] = ExternalToolAdapter(
                "gradle-check",
                "code-quality",
                ["check", "-x", "test", "--console=plain"],
                parse_lines,
                executable=gradle,
                executes_project_code=True,
            )
            names.append("gradle-check")
    if "Shell" in languages:
        shell_files = [
            path.relative_to(root).as_posix()
            for path in iter_project_files(root)[0]
            if path.suffix == ".sh"
        ]
        tools["shellcheck"].args.extend(shell_files)
        names.append("shellcheck")
    return names


def _testing_route(
    facts: ProjectFacts,
    root: Path,
    tools: dict[str, ExternalToolAdapter],
    allow_project_executables: bool = False,
) -> list[str]:
    if not facts.tests:
        return []
    languages = set(facts.languages)
    names = []
    terraform_test_roots = sorted(
        {
            root_dir
            for rel in facts.tests
            if rel.endswith(".tftest.hcl")
            for root_dir in [
                next(
                    (
                        parent.as_posix()
                        for parent in Path(rel).parents
                        if any((root / parent).glob("*.tf"))
                    ),
                    ".",
                )
            ]
        },
        key=lambda rel: (rel != "terraform", rel != ".", rel),
    )
    for index, rel in enumerate(terraform_test_roots):
        name = "terraform-test" if index == 0 else f"terraform-test:{rel}"
        args = ["test", "-no-color"] if rel == "." else [f"-chdir={rel}", "test", "-no-color"]
        tools[name] = ExternalToolAdapter(
            name,
            "testing",
            args,
            parse_lines,
            executable="terraform",
            expected_codes={0, 1},
            executes_project_code=True,
        )
        names.append(name)
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
            if allow_project_executables and (root / "mvnw").is_file():
                tools["maven-test"].executable = str(root / "mvnw")
            names.append("maven-test")
        else:
            if allow_project_executables and (root / "gradlew").is_file():
                tools["gradle-test"].executable = str(root / "gradlew")
            names.append("gradle-test")
    return names


def _iac_route(facts: ProjectFacts, root: Path, tools: dict[str, ExternalToolAdapter]) -> list[str]:
    names: list[str] = []
    if "terraform" in facts.iac:
        tools["trivy"].blueprint = "iac"
        tools["trivy"].args[4] = "misconfig"
        names.append("terraform-fmt")
        tools.pop("terraform")
        for index, rel in enumerate(_terraform_roots(root)):
            name = "terraform" if index == 0 else f"terraform:{rel}"
            args = ["validate", "-json"] if rel == "." else [f"-chdir={rel}", "validate", "-json"]
            tools[name] = ExternalToolAdapter(
                name,
                "iac",
                args,
                parse_terraform,
                executable="terraform",
                executes_project_code=True,
            )
            names.append(name)
        names.extend(["tflint", "checkov", "trivy"])
    if "cloudformation" in facts.iac:
        tools["trivy"].blueprint = "iac"
        tools["trivy"].args[4] = "misconfig"
        cloudformation = []
        for path in iter_project_files(root)[0]:
            if path.suffix not in {".yaml", ".yml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")[:100_000]
            if "AWSTemplateFormatVersion" in text or "AWS::Serverless-2016-10-31" in text:
                cloudformation.append(path.relative_to(root).as_posix())
        tools["cfn-lint"].args.extend(cloudformation)
        names.extend(["cfn-lint", "checkov", "trivy"])
    return names


def _ecosystem_route(
    facts: ProjectFacts,
    blueprint: str,
    root: Path,
    tools: dict[str, ExternalToolAdapter],
) -> list[str]:
    names: list[str] = []
    if blueprint == "security":
        if facts.is_git:
            tools["gitleaks"].args[0] = "git"
        names.append("gitleaks")
        if _configured(root, (".semgrep.yml", ".semgrep.yaml")):
            if (root / ".semgrep.yaml").is_file():
                tools["semgrep"].args[2] = ".semgrep.yaml"
            names.append("semgrep")
    elif blueprint == "supply-chain":
        tools["trivy"].args[4] = "vuln"
        names.extend(["osv-scanner", "trivy"])
        if (root / ".syft.yaml").is_file():
            names.append("syft")
        if (root / ".grype.yaml").is_file():
            names.append("grype")
    elif blueprint == "iac":
        names.extend(_iac_route(facts, root, tools))
    elif blueprint == "containers":
        dockerfiles = [rel for rel in facts.containers if Path(rel).name.lower() == "dockerfile"]
        if dockerfiles:
            tools["hadolint"].args.extend(dockerfiles)
            tools["trivy"].blueprint = "containers"
            tools["trivy"].args[4] = "misconfig"
            names.extend(["hadolint", "trivy"])
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
        workflow_files = [
            path.relative_to(root).as_posix()
            for path in iter_project_files(root)[0]
            if path.relative_to(root).as_posix().startswith(".github/workflows/")
            and path.suffix.lower() in {".yaml", ".yml"}
        ]
        tools["actionlint"].args.extend(workflow_files)
        names.extend(["actionlint", "zizmor"])
    elif blueprint == "api-data-config" and facts.api_specs:
        tools["spectral"].args.extend(facts.api_specs)
        names.append("spectral")
    elif blueprint == "documentation" and facts.docs:
        markdown_files = [rel for rel in facts.docs if rel.lower().endswith(".md")]
        if markdown_files:
            tools["markdownlint-cli2"].args.extend(markdown_files)
            tools["lychee"].args.extend(markdown_files)
            names.extend(["markdownlint-cli2", "lychee"])
    return names


def applicable_adapters(
    facts: ProjectFacts,
    blueprint: str,
    overrides: dict[str, str] | None = None,
    *,
    authorized_target: str | None = None,
    offline: bool = False,
    allow_project_executables: bool = False,
) -> list[ExternalToolAdapter]:
    root = Path(facts.path)
    tools = {adapter.name: adapter for adapter in known_tools()}
    if blueprint == "code-quality":
        names = _quality_route(facts, root, tools, allow_project_executables)
    elif blueprint == "testing":
        names = _testing_route(facts, root, tools, allow_project_executables)
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
            network_required=True,
        )
        names.append("zap-baseline")

    selected = [tools[name] for name in dict.fromkeys(names)]
    if facts.graph.components and blueprint in {"code-quality", "testing"}:
        selected = [a for a in selected if a.name.startswith("terraform-test")] + _module_adapters(
            facts, blueprint, allow_project_executables
        )
    if blueprint == "supply-chain" and facts.graph.components:
        # Scope source scans explicitly. Dependency resolution remains the native scanner's job.
        excluded = sorted(
            {
                str(Path(rel).parent)
                for rel in facts.manifests
                if facts.graph.scope(rel) in NON_RUNTIME | {"documentation", "test"}
            }
        )
        for adapter in selected:
            if adapter.name == "trivy":
                adapter.args[-1:-1] = [arg for rel in excluded for arg in ("--skip-dirs", rel)]
            elif adapter.name == "osv-scanner":
                locks = [
                    rel
                    for rel in facts.manifests
                    if Path(rel).name
                    in {
                        "uv.lock",
                        "poetry.lock",
                        "requirements.txt",
                        "package-lock.json",
                        "pnpm-lock.yaml",
                        "yarn.lock",
                        "Cargo.lock",
                        "go.mod",
                        "pom.xml",
                    }
                    and facts.graph.scope(rel) not in NON_RUNTIME | {"documentation", "test"}
                ]
                if locks:
                    adapter.args = [
                        "scan",
                        "source",
                        "--format",
                        "json",
                        *[arg for rel in locks for arg in ("--lockfile", "./" + rel)],
                    ]
                else:
                    adapter.disabled_reason = (
                        "no supported owned dependency lockfiles; dependency inventory only"
                    )

    for adapter in selected:
        if adapter.name == "trivy" and any(
            edge.kind == "module-call" and edge.external for edge in facts.graph.relationships
        ):
            adapter.disabled_reason = (
                "remote Terraform module resolution has no bounded network authorization; "
                "local HCL inventory and other applicable checks remain available"
            )
        if offline and adapter.network_required:
            adapter.disabled_reason = "disabled by offline mode because the adapter may use network"
            continue
        if adapter.executes_project_code and not allow_project_executables:
            adapter.disabled_reason = (
                "disabled for an untrusted repository; pass --trust-project-executables "
                "after reviewing the target"
            )
            continue
        local = (
            root
            / adapter.working_directory
            / "node_modules"
            / ".bin"
            / Path(adapter.executable).name
        )
        if not local.is_file():
            local = root / "node_modules" / ".bin" / Path(adapter.executable).name
        if adapter.name.split(":", 1)[0] == "tsc" and not local.is_file():
            adapter.disabled_reason = (
                "project TypeScript compiler/dependencies unavailable; "
                "global compiler is not authoritative"
            )
            continue
        if allow_project_executables and local.is_file() and adapter.name not in {"npm-test"}:
            adapter.executable = str(local)
        override_name = adapter.name.split(":", 1)[0]
        if allow_project_executables and overrides and override_name in overrides:
            override = Path(overrides[override_name])
            adapter.executable = str(override if override.is_absolute() else root / override)
    return selected


def _module_adapters(
    facts: ProjectFacts, blueprint: str, trusted: bool
) -> list[ExternalToolAdapter]:
    root = Path(facts.path)
    selected = []
    workspace_members = {
        edge.target for edge in facts.graph.relationships if edge.kind == "workspace-member"
    }
    for component in facts.graph.components:
        if component.scope in NON_RUNTIME | {"test", "documentation"}:
            continue
        component_root = root / component.root
        owned = facts.graph.source_files(component)
        local_facts = facts.model_copy(
            update={
                "path": str(component_root),
                "languages": {language: 1 for language in component.languages},
                "frameworks": component.frameworks,
                "tests": [
                    str((root / rel).relative_to(component_root))
                    for rel in facts.tests
                    if rel in owned
                ],
            }
        )
        tools = {tool.name: tool for tool in known_tools()}
        names = (
            _quality_route(local_facts, component_root, tools, trusted)
            if blueprint == "code-quality"
            else _testing_route(local_facts, component_root, tools, trusted)
        )
        for name in names:
            adapter = tools[name]
            if name.startswith("terraform-test"):
                continue
            # Workspace-native Rust tooling owns its members in a single invocation.
            if name.startswith("cargo-") and component.id in workspace_members:
                continue
            if name in {"go-vet", "go-test"} and not (component_root / "go.mod").is_file():
                continue
            if name.startswith("cargo-") and not (component_root / "Cargo.toml").is_file():
                continue
            if name == "ruff":
                python_files = [
                    "./" + str((root / rel).relative_to(component_root))
                    for rel in owned
                    if Path(rel).suffix in {".py", ".pyi"}
                ]
                if not python_files:
                    continue
                # Bounded argv; Ruff's native excludes still apply to explicit file arguments.
                if sum(map(len, python_files)) > 80_000:
                    adapter.disabled_reason = (
                        "source argument set exceeds safe command size; split the component"
                    )
                adapter.args = [
                    "check",
                    "--output-format",
                    "json",
                    "--no-cache",
                    "--force-exclude",
                    *python_files,
                ]
            if name == "pytest":
                adapter.args.extend(["-p", "no:cacheprovider"])
            adapter.working_directory = component.root
            if component.root != ".":
                adapter.name += ":" + component.root
            selected.append(adapter)
    return selected
