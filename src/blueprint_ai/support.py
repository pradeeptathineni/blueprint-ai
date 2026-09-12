"""Canonical ecosystem and tool/provider metadata shared by planning, execution and docs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from blueprint_ai.core.provider import Provider

PROVIDERS = {
    "docker": Provider(
        id="docker",
        executable="docker",
        supported_versions=">=24",
        source="https://docs.docker.com/",
        license="Apache-2.0",
        network=True,
        executes_code=True,
    ),
    "builtin": Provider(id="builtin", source="blueprint-ai", license="MIT"),
    "uv": Provider(
        id="uv",
        executable="uv",
        supported_versions=">=0.8,<1",
        source="https://docs.astral.sh/uv/",
        license="MIT OR Apache-2.0",
        executes_code=True,
    ),
    "npm": Provider(
        id="npm",
        executable="npm",
        supported_versions=">=9,<12",
        source="https://docs.npmjs.com/cli/",
        license="Artistic-2.0",
        executes_code=True,
    ),
    "vite": Provider(
        id="vite",
        executable="npm",
        supported_versions=">=9,<12",
        package="create-vite",
        version="9.2.1",
        registry_integrity=(
            "sha512-nE710dPFzm9CavJw4PtpOLQpNBiOzFV+tGbwcmBdg1A3TWgFkqypD"
            "mjt44oV0jvvQEdGsvFAvQ5pUnKrdcwciw=="
        ),
        source="https://github.com/vitejs/vite",
        license="MIT",
        network=True,
        executes_code=True,
    ),
    "openapi-typescript": Provider(
        id="openapi-typescript",
        executable="npm",
        supported_versions=">=9,<12",
        package="openapi-typescript",
        version="7.13.0",
        registry_integrity=(
            "sha512-EFP392gcqXS7ntPvbhBzbF8TyBA+baIYEm791Hy5YkjDYKTnk/Tn5"
            "OQeKm5BIZvJihpp8Zzr4hzx0Irde1LNGQ=="
        ),
        source="https://github.com/openapi-ts/openapi-typescript",
        license="MIT",
        network=True,
        executes_code=True,
    ),
}


class ToolSpec(BaseModel):
    id: str
    source: str
    license: str
    versions: str = "upstream stable; observed version retained, compatibility not certified"
    platforms: list[str] = Field(default_factory=lambda: ["Linux", "macOS", "Windows"])
    image: str | None = None
    base_image: str | None = None
    image_recipe: list[str] = Field(default_factory=list)
    container_executable: str | None = None
    acquisition_tool: str | None = None
    acquisition: str = (
        "official release/package; pin and verify upstream checksums; explicit install only"
    )
    maturity: Literal["supported", "partial", "experimental", "deferred"] = "supported"
    integration: Literal["review", "genesis", "toolchain", "capability-kit", "deferred"] = "review"
    update: str = (
        "explicit upgrade; rerun native contract fixtures before changing the selected version"
    )
    reviewed: str = "2026-09-12"


def _tool(ids: str, source: str, license: str, **kwargs) -> dict[str, ToolSpec]:
    return {
        name: ToolSpec(id=name, source=source, license=license, **kwargs) for name in ids.split()
    }


TOOLS: dict[str, ToolSpec] = {
    **_tool(
        "ruff",
        "https://docs.astral.sh/ruff/",
        "MIT",
        versions=">=0.13,<1",
        image="ghcr.io/astral-sh/ruff:0.16.7",
        container_executable="/ruff",
    ),
    **_tool("mypy", "https://mypy.readthedocs.io/", "MIT", versions=">=1,<3"),
    **_tool("pytest", "https://docs.pytest.org/", "MIT", versions=">=9.0.3,<10"),
    **_tool("biome", "https://biomejs.dev/", "MIT OR Apache-2.0"),
    **_tool("eslint", "https://eslint.org/docs/latest/", "MIT"),
    **_tool(
        "tsc",
        "https://www.typescriptlang.org/docs/",
        "Apache-2.0",
        acquisition="use the project's locked TypeScript dependency",
    ),
    **_tool("go-vet go-test gofmt", "https://go.dev/doc/", "BSD-3-Clause", versions=">=1.24,<2"),
    **_tool(
        "cargo-fmt cargo-clippy cargo-test",
        "https://doc.rust-lang.org/cargo/",
        "MIT OR Apache-2.0",
        acquisition=(
            "official rustup toolchain plus rustfmt and clippy; no automatic host installation"
        ),
    ),
    **_tool("maven-check maven-test", "https://maven.apache.org/", "Apache-2.0"),
    **_tool("gradle-check gradle-test", "https://docs.gradle.org/", "Apache-2.0"),
    **_tool(
        "dotnet-build dotnet-test",
        "https://learn.microsoft.com/en-us/dotnet/core/tools/",
        "MIT",
        versions=">=10,<11",
    ),
    **_tool(
        "php-lint composer-validate", "https://getcomposer.org/doc/", "MIT", maturity="partial"
    ),
    **_tool("npm-test", "https://docs.npmjs.com/cli/", "Artistic-2.0"),
    **_tool("shellcheck", "https://github.com/koalaman/shellcheck", "GPL-3.0-only"),
    **_tool(
        "semgrep",
        "https://semgrep.dev/docs/",
        "LGPL-2.1-only",
        versions=">=1,<2",
        image="semgrep/semgrep:1.177.0",
        container_executable="semgrep",
    ),
    **_tool(
        "gitleaks",
        "https://github.com/gitleaks/gitleaks",
        "MIT",
        versions=">=8,<9",
        image="ghcr.io/gitleaks/gitleaks:v8.30.1",
        container_executable="gitleaks",
    ),
    **_tool(
        "osv-scanner", "https://google.github.io/osv-scanner/", "Apache-2.0", versions=">=2,<3"
    ),
    **_tool("trivy", "https://trivy.dev/", "Apache-2.0", versions=">=0.60,<1"),
    **_tool(
        "syft",
        "https://github.com/anchore/syft",
        "Apache-2.0",
        versions=">=1,<2",
        image="anchore/syft:v1.51.1",
        container_executable="/syft",
    ),
    **_tool(
        "grype",
        "https://github.com/anchore/grype",
        "Apache-2.0",
        versions=">=0.90,<1",
        maturity="partial",
        acquisition=(
            "alternate to OSV/Trivy; explicit configured use avoids duplicate default scanning"
        ),
    ),
    **_tool(
        "terraform terraform-fmt terraform-test",
        "https://developer.hashicorp.com/terraform/cli",
        "BUSL-1.1",
        versions=">=1.6,<2",
    ),
    **_tool("tofu", "https://opentofu.org/docs/", "MPL-2.0", versions=">=1.8,<2"),
    **_tool("tflint", "https://github.com/terraform-linters/tflint", "MPL-2.0"),
    **_tool("checkov", "https://www.checkov.io/", "Apache-2.0"),
    **_tool("cfn-lint", "https://github.com/aws-cloudformation/cfn-lint", "MIT"),
    **_tool(
        "hadolint",
        "https://github.com/hadolint/hadolint",
        "GPL-3.0-only",
        image="hadolint/hadolint:v2.15.1",
        container_executable="/bin/hadolint",
    ),
    **_tool("kubeconform", "https://github.com/yannh/kubeconform", "Apache-2.0"),
    **_tool("kube-linter", "https://github.com/stackrox/kube-linter", "Apache-2.0"),
    **_tool(
        "kubescape",
        "https://github.com/kubescape/kubescape",
        "Apache-2.0",
        maturity="partial",
        acquisition=(
            "explicit compliance use; disabled by default where kube-lint"
            "er already covers workload policy"
        ),
    ),
    **_tool("helm", "https://helm.sh/docs/", "Apache-2.0"),
    **_tool("kustomize", "https://kubectl.docs.kubernetes.io/references/kustomize/", "Apache-2.0"),
    **_tool("actionlint", "https://github.com/rhysd/actionlint", "MIT"),
    **_tool("zizmor", "https://docs.zizmor.sh/", "MIT"),
    **_tool("spectral", "https://github.com/stoplightio/spectral", "Apache-2.0"),
    **_tool("markdownlint-cli2", "https://github.com/DavidAnson/markdownlint-cli2", "MIT"),
    **_tool("lychee", "https://lychee.cli.rs/", "MIT OR Apache-2.0"),
    **_tool("conftest", "https://www.conftest.dev/", "Apache-2.0"),
    **_tool("ast-grep", "https://ast-grep.github.io/", "MIT"),
    **_tool("buf", "https://buf.build/docs/", "Apache-2.0"),
    **_tool(
        "zap-baseline",
        "https://www.zaproxy.org/docs/docker/baseline-scan/",
        "Apache-2.0",
        maturity="partial",
        acquisition=(
            "official ZAP image; exact authorized endpoint and explicit trusted network required"
        ),
    ),
}


class Family(BaseModel):
    id: str
    language: str
    provider: str
    roles: list[str] = Field(default_factory=list)
    recognize: bool = True
    initialize: bool = True
    verify: list[str] = Field(default_factory=list)
    maturity: Literal["supported", "experimental", "partial", "deferred"] = "supported"
    boundary: str = "native checks; optional scanner availability reported independently"


FAMILIES: dict[str, Family] = {}
for _id, _language, _provider, _roles, _verify in [
    ("repository", "config", "builtin", [], []),
    ("python-library", "Python", "uv", ["library"], ["ruff", "mypy", "pytest"]),
    ("python-cli", "Python", "uv", ["cli"], ["ruff", "mypy", "pytest"]),
    ("python-api", "Python", "uv", ["api", "service"], ["ruff", "mypy", "pytest"]),
    ("typescript-library", "TypeScript", "npm", ["library"], ["tsc", "npm-test"]),
    ("typescript-cli", "TypeScript", "npm", ["cli"], ["tsc", "npm-test"]),
    ("node-api", "TypeScript", "npm", ["api", "service"], ["tsc", "npm-test"]),
    ("react", "TypeScript", "vite", ["frontend"], ["tsc", "npm-test"]),
    (
        "full-stack",
        "TypeScript/Python",
        "vite",
        ["frontend", "backend"],
        ["tsc", "pytest", "npm-test"],
    ),
    ("openapi", "JSON Schema", "builtin", ["api"], []),
    ("go-library", "Go", "go", ["library"], ["go-vet", "go-test", "gofmt"]),
    ("go-cli", "Go", "go", ["cli"], ["go-vet", "go-test", "gofmt"]),
    ("go-api", "Go", "go", ["api", "service"], ["go-vet", "go-test", "gofmt"]),
    ("rust-library", "Rust", "cargo", ["library"], ["cargo-fmt", "cargo-clippy", "cargo-test"]),
    ("rust-cli", "Rust", "cargo", ["cli"], ["cargo-fmt", "cargo-clippy", "cargo-test"]),
    ("csharp-library", "C#", "dotnet", ["library"], ["dotnet-build", "dotnet-test"]),
    ("csharp-cli", "C#", "dotnet", ["cli"], ["dotnet-build", "dotnet-test"]),
    ("dotnet-api", "C#", "dotnet", ["api", "service"], ["dotnet-build", "dotnet-test"]),
    ("vue", "TypeScript", "vite", ["frontend"], ["tsc", "npm-test"]),
    ("svelte", "TypeScript", "vite", ["frontend"], ["tsc", "npm-test"]),
    ("django", "Python", "uv", ["api", "service"], ["ruff", "pytest"]),
    ("flask", "Python", "uv", ["api", "service"], ["ruff", "pytest"]),
    ("terraform", "HCL", "terraform", ["infrastructure-module"], ["terraform", "terraform-fmt"]),
    ("opentofu", "HCL", "tofu", ["infrastructure-module"], ["tofu"]),
    ("kubernetes", "YAML", "builtin", ["infrastructure-module"], ["kubeconform", "kube-linter"]),
    ("helm", "YAML", "helm", ["infrastructure-module"], ["helm"]),
    ("kustomize", "YAML", "kustomize", ["infrastructure-module"], ["kustomize"]),
]:
    FAMILIES[_id] = Family(
        id=_id, language=_language, provider=_provider, roles=_roles, verify=_verify
    )

for _id, _executable, _version, _source, _license in [
    ("go", "go", ">=1.24,<2", "https://go.dev/doc/", "BSD-3-Clause"),
    ("cargo", "cargo", ">=1.85,<2", "https://doc.rust-lang.org/cargo/", "MIT OR Apache-2.0"),
    (
        "dotnet",
        "dotnet",
        ">=10,<11",
        "https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-new",
        "MIT",
    ),
    (
        "terraform",
        "terraform",
        ">=1.6,<2",
        "https://developer.hashicorp.com/terraform/cli",
        "BUSL-1.1",
    ),
    ("tofu", "tofu", ">=1.8,<2", "https://opentofu.org/docs/", "MPL-2.0"),
    ("helm", "helm", ">=3.15,<5", "https://helm.sh/docs/", "Apache-2.0"),
    ("kustomize", "kustomize", ">=5,<6", "https://kubectl.docs.kubernetes.io/", "Apache-2.0"),
]:
    PROVIDERS[_id] = Provider(
        id=_id,
        executable=_executable,
        supported_versions=_version,
        source=_source,
        license=_license,
        executes_code=True,
    )

# Deliberate boundaries belong to the same registry, not a second hand-maintained matrix.
for _id, _lang, _source, _reason in [
    (
        "angular",
        "TypeScript",
        "https://angular.dev/tools/cli/new",
        (
            "CLI and Angular builder matrix deferred; existing project-na"
            "tive scripts remain authoritative"
        ),
    ),
    (
        "rust-api",
        "Rust",
        "https://docs.rs/axum/latest/axum/",
        "Axum is a mature option; async service contract and dependency matrix deferred",
    ),
    (
        "laravel-symfony",
        "PHP",
        "https://laravel.com/docs/installation",
        "Composer/plugin execution and database defaults require a dedicated tested recipe",
    ),
    (
        "aws-cdk-sam",
        "TypeScript/Python",
        "https://docs.aws.amazon.com/cdk/v2/guide/ref-cli-cmd-init.html",
        "native synth/init suitable; bootstrap, IAM and resource tests deferred; no provisioning",
    ),
    (
        "azure-bicep-azd",
        "Bicep",
        "https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/azd-init-workflow",
        "native build/init suitable; template identities and cloud tests deferred; no azd up",
    ),
    (
        "gcp-infrastructure-manager",
        "HCL",
        "https://cloud.google.com/infrastructure-manager/docs",
        (
            "Terraform generation covers local GCP configuration; managed"
            " deployment requires explicit account scope"
        ),
    ),
    (
        "crossplane",
        "YAML",
        "https://docs.crossplane.io/latest/",
        "provider CRDs and cluster policy are deployment-specific; do not guess schemas",
    ),
    (
        "nx-turbo-workspaces",
        "TypeScript",
        "https://nx.dev/",
        "workspace recognition and module review supported; task-engine generation deferred",
    ),
    (
        "graphql",
        "GraphQL",
        "https://graphql.org/learn/",
        "contract recognition; project compiler owns schema/code generation",
    ),
    (
        "asyncapi",
        "YAML",
        "https://www.asyncapi.com/docs/tools/cli",
        "configured Spectral governance only; broker/client generation deferred",
    ),
    (
        "grpc-protobuf",
        "Protobuf",
        "https://buf.build/docs/",
        "configured Buf lint; breaking checks require an explicit baseline; no guessed remote refs",
    ),
    (
        "pact-schemathesis",
        "API contracts",
        "https://docs.pact.io/",
        "consumer/service boundaries and authorized endpoints required",
    ),
    (
        "gitlab-ci",
        "YAML",
        "https://docs.gitlab.com/ci/",
        (
            "recognition and local security inventory; GitLab API lint an"
            "d template generation deferred"
        ),
    ),
]:
    FAMILIES[_id] = Family(
        id=_id,
        language=_lang,
        provider=_source,
        initialize=False,
        recognize=_id
        in {
            "angular",
            "rust-api",
            "laravel-symfony",
            "nx-turbo-workspaces",
            "graphql",
            "asyncapi",
            "grpc-protobuf",
            "gitlab-ci",
        },
        maturity="partial"
        if _id in {"graphql", "asyncapi", "grpc-protobuf", "gitlab-ci", "nx-turbo-workspaces"}
        else "deferred",
        boundary=_reason,
    )


PROVIDER_IMAGES = {
    "uv": "ghcr.io/astral-sh/uv:0.12.13-python3.12-trixie-slim",
    "npm": "node:24-bookworm-slim",
    "vite": "node:24-bookworm-slim",
    "openapi-typescript": "node:24-bookworm-slim",
    "go": "golang:1.26-bookworm",
    "cargo": "rust:1-slim-bookworm",
    "dotnet": "mcr.microsoft.com/dotnet/sdk:10.0",
    "terraform": "hashicorp/terraform:1.16.2",
    "tofu": "ghcr.io/opentofu/opentofu:1.12.6",
    "helm": "alpine/helm:3.19.0",
    "kustomize": "registry.k8s.io/kustomize/kustomize:v5.7.1",
}

for _name, _image in PROVIDER_IMAGES.items():
    PROVIDERS[_name].image = _image

TOOLS["conftest"].image = "openpolicyagent/conftest:v0.69.0"
TOOLS["conftest"].container_executable = "conftest"

PROVIDERS["maven"] = Provider(
    id="maven",
    executable="mvn",
    supported_versions=">=3.9,<4",
    source="https://maven.apache.org/archetypes/maven-archetype-quickstart/",
    license="Apache-2.0",
    executes_code=True,
    image="maven:3.9-eclipse-temurin-21",
)
PROVIDERS["spring"] = Provider(
    id="spring", source="https://start.spring.io", license="Apache-2.0", network=True
)
PROVIDER_IMAGES["maven"] = PROVIDERS["maven"].image or ""
FAMILIES["java-library"] = Family(
    id="java-library",
    language="Java",
    provider="maven",
    roles=["library"],
    verify=["maven-check", "maven-test"],
)
FAMILIES["spring-boot"] = Family(
    id="spring-boot",
    language="Java",
    provider="spring",
    roles=["api", "service"],
    verify=["maven-check", "maven-test"],
)

TOOLS["markdownlint-cli2"].image = "blueprint-tools/markdownlint-cli2:0.23.2"
TOOLS["markdownlint-cli2"].base_image = "node:24-bookworm-slim"
TOOLS["markdownlint-cli2"].image_recipe = [
    "RUN npm install --global --ignore-scripts --no-audit --no-fund markdownlint-cli2@0.23.2"
]
TOOLS["ast-grep"].image = "blueprint-tools/ast-grep:0.45.3"
TOOLS["ast-grep"].base_image = "python:3.12-slim-bookworm"
TOOLS["ast-grep"].image_recipe = [
    "RUN pip install --no-cache-dir --only-binary=:all: ast-grep-cli==0.45.3"
]
TOOLS["buf"].image = "bufbuild/buf:1.73.0"
TOOLS["buf"].container_executable = "buf"
TOOLS["cargo"] = ToolSpec(
    id="cargo",
    source=PROVIDERS["cargo"].source,
    license=PROVIDERS["cargo"].license,
    image="blueprint-tools/rust:1.98.1",
    base_image="rust:1.98.1-slim-bookworm",
    image_recipe=["RUN rustup component add rustfmt clippy"],
)
PROVIDERS["cargo"].image = TOOLS["cargo"].image
PROVIDER_IMAGES["cargo"] = TOOLS["cargo"].image or ""

PROVIDERS["next"] = Provider(
    id="next",
    executable="npm",
    supported_versions=">=9,<12",
    package="create-next-app",
    version="16.3.5",
    registry_integrity=(
        "sha512-0XXQn0soB+VkvguZR1Z+7Je4B6DDjW1bAfGPo+S+DpKiGSz/6WDy4"
        "AxUD6SQOmYkXoE2ik4jIrPlswiG5VP6hA=="
    ),
    source="https://nextjs.org/docs/app/api-reference/cli/create-next-app",
    license="MIT",
    executes_code=True,
    network=True,
    image="node:24-bookworm-slim",
)
PROVIDERS["pulumi"] = Provider(
    id="pulumi",
    executable="pulumi",
    supported_versions=">=3,<4",
    source="https://www.pulumi.com/docs/iac/cli/commands/pulumi_new/",
    license="Apache-2.0",
    executes_code=True,
    network=True,
    image="pulumi/pulumi-nodejs:3.262.0",
)
PROVIDER_IMAGES["next"] = PROVIDERS["next"].image or ""
PROVIDER_IMAGES["pulumi"] = PROVIDERS["pulumi"].image or ""
FAMILIES["nextjs"] = Family(
    id="nextjs",
    language="TypeScript",
    provider="next",
    roles=["frontend"],
    verify=["tsc", "npm-test", "eslint"],
)
FAMILIES["pulumi"] = Family(
    id="pulumi",
    language="TypeScript",
    provider="pulumi",
    roles=["infrastructure-module"],
    verify=["tsc"],
    boundary=(
        "generate-only native project, cloud dependency and type vali"
        "dation; no preview/state/deployment"
    ),
)

# Native adapters share provider toolchains; optional acquisition remains explicit.
for _ids, _provider in [
    ("go-vet go-test gofmt", "go"),
    ("cargo-fmt cargo-clippy cargo-test", "cargo"),
    ("dotnet-build dotnet-test", "dotnet"),
    ("maven-check maven-test", "maven"),
    ("terraform terraform-fmt terraform-test", "terraform"),
    ("tofu", "tofu"),
    ("helm", "helm"),
    ("kustomize", "kustomize"),
]:
    for _id in _ids.split():
        TOOLS[_id].image = PROVIDERS[_provider].image
        TOOLS[_id].container_executable = (
            "gofmt" if _id == "gofmt" else PROVIDERS[_provider].executable
        )
        TOOLS[_id].acquisition_tool = _provider
        if _provider in TOOLS:
            TOOLS[_id].base_image = TOOLS[_provider].base_image
            TOOLS[_id].image_recipe = TOOLS[_provider].image_recipe

TOOLS["pre-commit"] = ToolSpec(
    id="pre-commit",
    source="https://pre-commit.com/",
    license="MIT",
    acquisition="use a locked development dependency or explicit uv tool install pre-commit",
)
TOOLS["php-lint"].source = "https://www.php.net/manual/en/features.commandline.options.php"
TOOLS["php-lint"].license = "PHP-3.01"
TOOLS["php-lint"].maturity = "deferred"
TOOLS["php-lint"].integration = "deferred"
TOOLS["php-lint"].acquisition = (
    "PHP CLI recognition only; dedicated review invocation deferred; "
    "use project-native PHP checks with explicit trust"
)
TOOLS["kubescape"].maturity = "deferred"
TOOLS["kubescape"].integration = "deferred"
TOOLS["kubescape"].acquisition = (
    "alternate compliance scanner; no review selection/parser contract; "
    "use the official CLI separately with explicit scope/network authorization"
)
TOOLS[
    "tofu"
].acquisition += (
    "; Genesis provider verification only; existing HCL review currently uses Terraform adapters"
)
TOOLS["cargo"].acquisition = "managed Rust toolchain acquisition; cargo-fmt/clippy/test own review"
TOOLS["cargo"].integration = "toolchain"
TOOLS["tofu"].integration = "genesis"
TOOLS["pre-commit"].acquisition += "; capability-kit verification only, no review adapter"
TOOLS["pre-commit"].integration = "capability-kit"
FAMILIES["kubernetes"].maturity = "partial"
FAMILIES["kubernetes"].boundary = (
    "Namespace-only generation and built-in structural validation; "
    "no workload, live API-server, cluster or deployment verification"
)
FAMILIES["pulumi"].maturity = "partial"
FAMILIES["pulumi"].boundary = (
    "generate-only empty program and selected cloud SDK installation; "
    "no SDK resource API, preview, state or deployment verification"
)
