"""Composable native family recipes. All operations use the common genesis executor."""

from __future__ import annotations

import json
from pathlib import Path

from blueprint_ai.core.project import Component
from blueprint_ai.genesis.files import json_file, write
from blueprint_ai.genesis.models import ArtifactClaim, GenesisPlan, IntentSpec, Operation
from blueprint_ai.naming import resolve_identity
from blueprint_ai.support import FAMILIES, PROVIDERS

NATIVE_FAMILIES = {
    "nextjs",
    "pulumi",
    "java-library",
    "spring-boot",
    "go-library",
    "go-cli",
    "go-api",
    "rust-library",
    "rust-cli",
    "csharp-library",
    "csharp-cli",
    "dotnet-api",
    "vue",
    "svelte",
    "django",
    "flask",
    "terraform",
    "opentofu",
    "kubernetes",
    "helm",
    "kustomize",
}


def _operations(
    kind: str, package: str, module: str
) -> tuple[list[list[str]], list[tuple[list[str], bool]], list[str]]:
    if kind == "nextjs":
        return (
            [
                [
                    "npm",
                    "exec",
                    "--yes",
                    "--ignore-scripts",
                    "--package=create-next-app@16.3.5",
                    "--",
                    "create-next-app",
                    package,
                    "--yes",
                    "--ts",
                    "--eslint",
                    "--app",
                    "--src-dir",
                    "--no-tailwind",
                    "--use-npm",
                    "--skip-install",
                    "--disable-git",
                    "--empty",
                ]
            ],
            [
                (
                    [
                        "npm",
                        "install",
                        "--ignore-scripts",
                        "--include=optional",
                        "--no-audit",
                        "--no-fund",
                    ],
                    True,
                ),
                (["npm", "run", "build"], False),
                (["npm", "run", "lint"], False),
                (["npm", "test"], False),
            ],
            ["package.json"],
        )
    if kind == "pulumi":
        return (
            [
                [
                    "pulumi",
                    "new",
                    "typescript",
                    "--generate-only",
                    "--yes",
                    "--name",
                    package,
                    "--description",
                    "Local infrastructure baseline",
                ]
            ],
            [
                (["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], True),
                (["npm", "exec", "--", "tsc", "--noEmit"], False),
            ],
            ["Pulumi.yaml", "package.json"],
        )
    if kind == "java-library":
        return (
            [
                [
                    "mvn",
                    "-B",
                    "org.apache.maven.plugins:maven-archetype-plugin:3.4.1:generate",
                    "-DarchetypeGroupId=org.apache.maven.archetypes",
                    "-DarchetypeArtifactId=maven-archetype-quickstart",
                    "-DarchetypeVersion=1.5",
                    "-DgroupId=com.example",
                    "-DartifactId=" + package,
                    "-Dpackage=com.example." + module,
                    "-DjavaCompilerVersion=21",
                    "-DinteractiveMode=false",
                ]
            ],
            [(["mvn", "-B", "-Dmaven.repo.local=.blueprint-state/m2", "verify"], True)],
            ["pom.xml"],
        )
    if kind == "spring-boot":
        return (
            [],
            [(["mvn", "-B", "-Dmaven.repo.local=.blueprint-state/m2", "verify"], True)],
            ["pom.xml"],
        )
    if kind.startswith("go-"):
        return (
            [["go", "mod", "init", f"example.com/{package}"]],
            [
                (["go", "fmt", "./..."], False),
                (["go", "vet", "./..."], False),
                (["go", "test", "./..."], False),
                (["go", "build", "./..."], False),
            ],
            ["go.mod"],
        )
    if kind.startswith("rust-"):
        return (
            [
                [
                    "cargo",
                    "init",
                    "--lib" if kind.endswith("library") else "--bin",
                    "--name",
                    package,
                    "--vcs",
                    "none",
                    ".",
                ]
            ],
            [
                (["cargo", "fmt"], False),
                (["cargo", "clippy", "--offline", "--", "-D", "warnings"], False),
                (["cargo", "test", "--offline"], False),
                (["cargo", "build", "--offline"], False),
            ],
            ["Cargo.toml"],
        )
    if kind.startswith("csharp-") or kind == "dotnet-api":
        template = (
            "classlib" if kind.endswith("library") else "web" if kind == "dotnet-api" else "console"
        )
        return (
            [
                ["dotnet", "new", template, "--name", module, "--output", "src", "--no-restore"],
                [
                    "dotnet",
                    "new",
                    "xunit",
                    "--name",
                    module + "Tests",
                    "--output",
                    "tests",
                    "--no-restore",
                ],
                ["dotnet", "add", "tests", "reference", f"src/{module}.csproj"],
            ],
            [
                (["dotnet", "restore", "tests", "--use-lock-file"], True),
                (["dotnet", "build", "tests", "--no-restore"], False),
                (["dotnet", "test", "tests", "--no-build", "--no-restore"], False),
            ],
            [f"src/{module}.csproj"],
        )
    if kind in {"vue", "svelte"}:
        return (
            [
                [
                    "npm",
                    "exec",
                    "--yes",
                    "--ignore-scripts",
                    "--package=create-vite@9.2.1",
                    "--",
                    "create-vite",
                    ".",
                    "--template",
                    kind + "-ts",
                    "--no-interactive",
                    "--no-install",
                ]
            ],
            [
                (
                    [
                        "npm",
                        "install",
                        "--ignore-scripts",
                        "--include=optional",
                        "--no-audit",
                        "--no-fund",
                    ],
                    True,
                ),
                (["npm", "run", "build"], False),
                (["npm", "test"], False),
            ],
            ["package.json"],
        )
    if kind in {"django", "flask"}:
        initializers = [
            [
                "uv",
                "init",
                "--lib",
                "--name",
                package,
                "--build-backend",
                "hatch",
                "--vcs",
                "none",
                "--python",
                "3.12",
                "--no-workspace",
                "--no-config",
                "--offline",
                ".",
            ]
        ]
        if kind == "django":
            initializers.append(
                [
                    "uv",
                    "tool",
                    "run",
                    "--from",
                    "Django>=5.2,<5.3",
                    "django-admin",
                    "startproject",
                    "project_config",
                    ".",
                ]
            )
        return (
            initializers,
            [
                (["uv", "sync"], True),
                (["uv", "run", "--no-sync", "ruff", "format", "."], False),
                (["uv", "run", "--no-sync", "ruff", "check", "."], False),
                (
                    ["uv", "run", "--no-sync", "python", "manage.py", "test"]
                    if kind == "django"
                    else ["uv", "run", "--no-sync", "pytest", "-q"],
                    False,
                ),
                (["uv", "build", "--no-sources"], True),
            ],
            ["pyproject.toml"],
        )
    if kind in {"terraform", "opentofu"}:
        exe = "tofu" if kind == "opentofu" else "terraform"
        return (
            [],
            [
                ([exe, "fmt"], False),
                ([exe, "init", "-backend=false", "-input=false"], True),
                ([exe, "validate", "-json"], False),
            ],
            ["main.tf"],
        )
    if kind == "helm":
        return (
            [["helm", "create", "chart"]],
            [
                (["helm", "lint", "chart", "--strict"], False),
                (["helm", "template", package, "chart"], False),
            ],
            ["chart/Chart.yaml"],
        )
    if kind == "kustomize":
        return [], [(["kustomize", "build", "."], False)], ["kustomization.yaml"]
    return [], [], ["namespace.yaml"]


def native_plan(intent: IntentSpec) -> GenesisPlan:
    family = FAMILIES[intent.kind]
    identity = resolve_identity(
        intent.name, "python" if family.language == "Python" else "repository"
    )
    if not identity.module.isidentifier():
        raise ValueError("native package/module names must start with a letter")
    initializers, checks, expected = _operations(intent.kind, identity.package, identity.module)
    component = Component(
        id=".",
        root=".",
        name=identity.package,
        languages=[family.language],
        roles=family.roles,
        package_manager=family.provider,
    )
    operations = []
    for i, command in enumerate(initializers):
        operations.append(
            Operation(
                id=f"initialize:.:{i}",
                provider=family.provider,
                action="initialize",
                command=command,
                network=family.provider in {"vite", "maven", "next", "pulumi"} or "tool" in command,
                requires_execution=True,
                expected_files=(
                    [identity.package + "/" + path for path in expected]
                    if intent.kind in {"java-library", "nextjs"}
                    else expected
                )
                if i == 0
                else [],
            )
        )
    if intent.kind == "spring-boot":
        operations.append(
            Operation(
                id="initialize:spring",
                provider="spring",
                action="initialize",
                network=True,
                expected_files=["pom.xml"],
            )
        )
    operations.append(Operation(id="strengthen", provider="builtin", action="strengthen"))
    for i, (command, network) in enumerate(checks):
        operations.append(
            Operation(
                id=f"verify:.:{i}",
                provider="npm"
                if family.provider in {"vite", "next", "pulumi"}
                else "maven"
                if family.provider == "spring"
                else family.provider,
                action="strengthen" if command[1] in {"fmt"} or "format" in command else "verify",
                command=command,
                network=network,
                requires_execution=True,
            )
        )
    if intent.kind == "kubernetes":
        operations.append(Operation(id="verify:kubernetes", provider="builtin", action="verify"))
    from blueprint_ai.genesis.capabilities import resolve_capabilities

    capabilities = ["family:" + intent.kind]
    if intent.ci or intent.maturity == "team":
        capabilities.append("ci")
    return GenesisPlan(
        intent=intent,
        identity=identity,
        components=[component],
        capabilities=resolve_capabilities(capabilities),
        providers=[PROVIDERS[p] for p in sorted({op.provider for op in operations})],
        operations=operations,
        claims=[
            ArtifactClaim(
                path=rel,
                owner="family:" + intent.kind,
                mode="native-cli" if initializers else "create-only",
            )
            for rel in expected
        ],
        decisions=[
            "Native tooling owns initialization and verification.",
            (
                "Cloud starters declare provider requirements only; no state "
                "backend, resources, plan or deployment is created."
            ),
            "No model calls; missing prerequisites remain explicit.",
        ],
    )


def _go(root: Path, plan: GenesisPlan) -> None:
    kind = plan.intent.kind
    library = kind == "go-library"
    package = plan.identity.module if library else "main"
    write(
        root,
        "greeting.go",
        f"""package {package}

import ("fmt"; "strings")

// Greeting returns a greeting for a nonempty name.
func Greeting(name string) (string, error) {{
    name = strings.TrimSpace(name)
    if name == "" {{ return "", fmt.Errorf("name must not be empty") }}
    return "Hello, " + name + "!", nil
}}
""",
    )
    write(
        root,
        "greeting_test.go",
        f"""package {package}
import "testing"
func TestGreeting(t *testing.T) {{
    got, err := Greeting(" Ada ")
    if err != nil || got != "Hello, Ada!" {{ t.Fatalf("unexpected greeting: %q, %v", got, err) }}
    if _, err := Greeting(" "); err == nil {{ t.Fatal("expected empty-name error") }}
}}
""",
    )
    if kind == "go-cli":
        write(
            root,
            "main.go",
            """package main
import ("fmt"; "os")
func main() {
    name := "world"
    if len(os.Args) > 1 { name = os.Args[1] }
    greeting, err := Greeting(name)
    if err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
    fmt.Println(greeting)
}
""",
        )
    elif kind == "go-api":
        write(
            root,
            "main.go",
            (
                'package main\nimport ("encoding/json"; "log"; "net/http"; "ti'
                'me")\nfunc handler() http.Handler {\n    mux := http.NewServeM'
                'ux()\n    mux.HandleFunc("GET /health", func(w http.ResponseW'
                'riter, r *http.Request) {\n        w.Header().Set("Content-Ty'
                'pe", "application/json")\n        if err := json.NewEncoder(w'
                ').Encode(map[string]string{"status":"ok"}); err != nil { log'
                ".Print(err) }\n    })\n    return mux\n}\nfunc main() {\n    serv"
                'er := &http.Server{Addr: ":8000", Handler: handler(), ReadHe'
                "aderTimeout: 5*time.Second}\n    log.Fatal(server.ListenAndSe"
                "rve())\n}\n"
            ),
        )
        write(
            root,
            "http_test.go",
            (
                'package main\nimport ("net/http"; "net/http/httptest"; "testi'
                'ng"; "encoding/json")\nfunc TestHealthContract(t *testing.T) '
                '{\n    request := httptest.NewRequest(http.MethodGet, "/healt'
                'h", nil)\n    response := httptest.NewRecorder()\n    handler('
                ").ServeHTTP(response, request)\n    var body map[string]strin"
                "g\n    if err := json.Unmarshal(response.Body.Bytes(), &body)"
                "; err != nil { t.Fatal(err) }\n    if response.Code != 200 ||"
                ' body["status"] != "ok" { t.Fatalf("unexpected health: %v", '
                "response) }\n    unknown := httptest.NewRecorder()\n    handle"
                'r().ServeHTTP(unknown, httptest.NewRequest(http.MethodGet,"/'
                'missing",nil))\n    if unknown.Code != 404 { t.Fatal("unknown'
                ' path must return 404") }\n}\n'
            ),
        )


def _rust(root: Path, plan: GenesisPlan) -> None:
    write(
        root,
        "src/lib.rs",
        """pub fn greeting(name: &str) -> Result<String, &'static str> {
    let name = name.trim();
    if name.is_empty() { return Err("name must not be empty"); }
    Ok(format!("Hello, {name}!"))
}
#[cfg(test)]
mod tests {
    use super::greeting;
    #[test]
    fn validates_and_trims() {
        assert_eq!(greeting(" Ada "), Ok("Hello, Ada!".to_string()));
        assert!(greeting(" ").is_err());
    }
}
""",
    )
    if plan.intent.kind == "rust-cli":
        write(
            root,
            "src/main.rs",
            f"""fn main() {{
    let name = std::env::args().nth(1).unwrap_or_else(|| "world".to_string());
    match {plan.identity.module}::greeting(&name) {{
        Ok(value) => println!("{{value}}"),
        Err(error) => {{ eprintln!("{{error}}"); std::process::exit(1); }}
    }}
}}
""",
        )


def _dotnet(root: Path, plan: GenesisPlan) -> None:
    module = plan.identity.module
    write(
        root,
        "src/Health.cs",
        f"""namespace {module};
public static class Health {{ public static string Status() => "ok"; }}
""",
    )
    write(
        root,
        "tests/UnitTest1.cs",
        f"""using Xunit;
namespace {module}Tests;
public class HealthTests {{
    [Fact]
    public void HealthIsReady() {{ Assert.Equal("ok", {module}.Health.Status()); }}
}}
""",
    )
    if plan.intent.kind == "dotnet-api":
        write(
            root,
            "src/Program.cs",
            f"""var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.MapGet("/health", () => new {{ status = {module}.Health.Status() }});
app.Run();
public partial class Program {{ }}
""",
        )
        import xml.etree.ElementTree as ET

        project = root / "tests" / (module + "Tests.csproj")
        document = ET.parse(project)
        group = ET.SubElement(document.getroot(), "ItemGroup")
        ET.SubElement(
            group, "PackageReference", Include="Microsoft.AspNetCore.Mvc.Testing", Version="10.0.12"
        )
        document.write(project, encoding="unicode")
        write(
            root,
            "tests/HealthIntegrationTests.cs",
            """using System.Net;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

public class HealthIntegrationTests {
    [Fact]
    public async Task HealthAndMissingRouteRespectTheContract() {
        await using var application = new WebApplicationFactory<Program>();
        using var client = application.CreateClient();
        using var response = await client.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal("ok", body.RootElement.GetProperty("status").GetString());
        using var missing = await client.GetAsync("/missing");
        Assert.Equal(HttpStatusCode.NotFound, missing.StatusCode);
    }
}
""",
        )


def _python_web(root: Path, plan: GenesisPlan) -> None:
    module = plan.identity.module
    framework = "django>=5.2,<5.3" if plan.intent.kind == "django" else "flask>=3.1,<4"
    write(
        root,
        "pyproject.toml",
        f'''[project]
name = "{plan.identity.package}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["{framework}"]
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[dependency-groups]
dev = ["pytest>=9.1.1,<10", "ruff>=0.16.6,<1"]
[tool.ruff]
line-length = 100
''',
    )
    if plan.intent.kind == "django":
        path = root / "project_config/settings.py"
        settings = path.read_text()
        settings = (
            "\n".join(
                "SECRET_KEY = 'django-insecure-development-only-change-before-deploy'"
                if line.startswith("SECRET_KEY =")
                else line
                for line in settings.splitlines()
            )
            + "\n"
        )
        write(root, "project_config/settings.py", settings)
        write(
            root,
            "project_config/urls.py",
            """from django.http import JsonResponse
from django.urls import path

def health(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [path("health", health)]
""",
        )
        write(
            root,
            "project_config/test_health.py",
            """from django.test import SimpleTestCase

class HealthTests(SimpleTestCase):
    def test_health_contract(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
    def test_unknown_route(self):
        self.assertEqual(self.client.get("/missing").status_code, 404)
""",
        )
    else:
        write(
            root,
            f"src/{module}/app.py",
            """from flask import Flask

def create_app() -> Flask:
    app = Flask(__name__)
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}
    return app
""",
        )
        write(
            root,
            "tests/test_health.py",
            f"""from {module}.app import create_app

def test_health_contract():
    client = create_app().test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {{"status": "ok"}}
    assert client.get("/missing").status_code == 404
""",
        )


def _frontend(root: Path, plan: GenesisPlan) -> None:
    package = json.loads((root / "package.json").read_text())
    package["scripts"]["test"] = "vitest run"
    package.setdefault("devDependencies", {})["vitest"] = "^4.0.0"
    if plan.intent.kind == "vue":
        package["devDependencies"]["@vue/server-renderer"] = package["dependencies"]["vue"]
        test = """import { expect, test } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import App from './App.vue';
test('starter renders its interface', async () => {
    const html = await renderToString(createSSRApp(App));
    expect(html).toContain('Documentation');
    expect(html).toContain('https://vite.dev/');
});
"""
    else:
        test = """import { expect, test } from 'vitest';
import { render } from 'svelte/server';
import App from './App.svelte';
test('starter renders its interface', () => {
    const html = render(App).body;
    expect(html).toContain('Documentation');
    expect(html).toContain('https://vite.dev/');
});
"""
    json_file(root, "package.json", package)
    write(root, "src/App.test.ts", test)


def _infrastructure(root: Path, plan: GenesisPlan) -> None:
    kind = plan.intent.kind
    if kind in {"terraform", "opentofu"}:
        provider, source, version = {
            "aws": ("aws", "hashicorp/aws", "~> 6.0"),
            "azure": ("azurerm", "hashicorp/azurerm", "~> 4.0"),
            "gcp": ("google", "hashicorp/google", "~> 7.0"),
        }[plan.intent.cloud]
        write(
            root,
            "main.tf",
            f'''terraform {{
  required_version = ">= 1.8, < 2.0"
  required_providers {{
    {provider} = {{
      source = "{source}"
      version = "{version}"
    }}
  }}
}}
# No resources or remote state backend: callers supply provider configuration.
''',
        )
    elif kind in {"kubernetes", "kustomize"}:
        write(
            root,
            "namespace.yaml",
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: "
            + plan.identity.repository
            + "\n",
        )
        if kind == "kustomize":
            write(
                root,
                "kustomization.yaml",
                (
                    "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomizat"
                    "ion\nresources:\n  - namespace.yaml\n"
                ),
            )


def strengthen_family(root: Path, plan: GenesisPlan) -> None:
    kind = plan.intent.kind
    if kind in {"java-library", "nextjs"}:
        # These generators require a writable parent and create a named child directory.
        nested = root / plan.identity.package
        for item in nested.iterdir():
            item.rename(root / item.name)
        nested.rmdir()
    if kind == "nextjs":
        package = json.loads((root / "package.json").read_text())
        package["scripts"]["lint"] = "eslint ."
        package["scripts"]["test"] = "vitest run"
        package.setdefault("devDependencies", {})["vitest"] = "^4.0.0"
        json_file(root, "package.json", package)
        write(
            root,
            "src/app/page.tsx",
            "export default function Home() { return <main><h1>"
            + plan.identity.repository
            + "</h1></main>; }\n",
        )
        write(
            root,
            "src/app/layout.tsx",
            (
                'import type { ReactNode } from "react";\nexport default funct'
                "ion Layout({ children }: { children: ReactNode }) { return <"
                'html lang="en"><body>{children}</body></html>; }\n'
            ),
        )
        write(
            root,
            "src/app/page.test.tsx",
            (
                'import { expect, test } from "vitest";\nimport { createElemen'
                't } from "react";\nimport { renderToStaticMarkup } from "reac'
                't-dom/server";\nimport Home from "./page";\ntest("home renders'
                ' a main heading", () => { expect(renderToStaticMarkup(create'
                'Element(Home))).toContain("<h1>"); });\n'
            ),
        )
    elif kind == "pulumi":
        package = json.loads((root / "package.json").read_text())
        provider, version = {
            "aws": ("aws", "^7.0.0"),
            "azure": ("azure-native", "^3.0.0"),
            "gcp": ("gcp", "^9.0.0"),
        }[plan.intent.cloud]
        package.setdefault("dependencies", {})["@pulumi/" + provider] = version
        json_file(root, "package.json", package)
        write(
            root,
            "index.ts",
            "export const cloud = "
            + json.dumps(plan.intent.cloud)
            + ";\n// Add resources after choosing an explicit state backend and credentials.\n",
        )
    elif kind == "java-library":
        pass  # Native archetype sources and tests are retained.
    elif kind == "spring-boot":
        module = plan.identity.module
        java_path = "src/main/java/com/example/" + module + "/HealthController.java"
        write(
            root,
            java_path,
            f"""package com.example.{module};
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import java.util.Map;
@RestController
public class HealthController {{
    @GetMapping("/health")
    public Map<String, String> health() {{ return Map.of("status", "ok"); }}
}}
""",
        )
        write(
            root,
            "src/test/java/com/example/" + module + "/HealthIntegrationTest.java",
            f"""package com.example.{module};
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import static org.junit.jupiter.api.Assertions.assertEquals;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class HealthIntegrationTest {{
    @LocalServerPort int port;
    @Test void healthAndMissingRouteRespectTheContract() throws Exception {{
        var client = HttpClient.newHttpClient();
        var base = "http://127.0.0.1:" + port;
        var response = client.send(HttpRequest.newBuilder(URI.create(base + "/health")).build(),
            HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        assertEquals({json.dumps('{"status":"ok"}')}, response.body());
        var missing = client.send(HttpRequest.newBuilder(URI.create(base + "/missing")).build(),
            HttpResponse.BodyHandlers.ofString());
        assertEquals(404, missing.statusCode());
    }}
}}
""",
        )
    elif kind.startswith("go-"):
        _go(root, plan)
    elif kind.startswith("rust-"):
        _rust(root, plan)
    elif kind.startswith("csharp-") or kind == "dotnet-api":
        _dotnet(root, plan)
    elif kind in {"django", "flask"}:
        _python_web(root, plan)
    elif kind in {"vue", "svelte"}:
        _frontend(root, plan)
    else:
        _infrastructure(root, plan)
    commands = [
        " ".join(op.command) for op in plan.operations if op.command and op.action != "initialize"
    ]
    write(
        root,
        "README.md",
        f"# {plan.identity.display}\n\nGenerated with {FAMILIES[kind].provider}; "
        "see `.blueprint-ai/genesis.json` for versions and execution evidence.\n\n"
        "## Verify\n\n```sh\n"
        + "\n".join(commands)
        + (
            "\n```\n\nCloud and cluster deployment is a separate explicit ac"
            "tion. Review development defaults before deployment.\n"
        ),
    )
    if "ci" in plan.capabilities:
        # Reuse the exact native verification commands, without introducing a second task engine.
        setup = {
            "Go": (
                "      - uses: actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad0"
                '53ef454303e\n        with:\n          go-version: "1.26"\n'
            ),
            "Rust": "      - run: rustup component add clippy rustfmt\n",
            "C#": (
                "      - uses: actions/setup-dotnet@a98b56852c35b8e3190ac28c8"
                'c2271da59106c68\n        with:\n          dotnet-version: "10.'
                '0.x"\n'
            ),
            "Python": "      - run: python -m pip install uv==0.12.13\n",
            "TypeScript": (
                "      - uses: actions/setup-node@820762786026740c76f36085b0e"
                'fc47a31fe5020\n        with:\n          node-version: "24"\n'
            ),
        }.get(FAMILIES[kind].language)
        if setup:
            write(
                root,
                ".github/workflows/ci.yml",
                (
                    "name: CI\non: [push, pull_request]\npermissions:\n  contents: r"
                    "ead\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n  "
                    "    - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181"
                    "273ba90b1\n        with:\n          persist-credentials: false"
                    "\n"
                )
                + setup
                + "".join("      - run: " + command + "\n" for command in commands),
            )
