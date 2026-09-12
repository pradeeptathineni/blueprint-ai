"""Small cross-cutting recipes applied to new, isolated native-generator output."""

from __future__ import annotations

import json
from pathlib import Path

from blueprint_ai.genesis.files import json_file, write
from blueprint_ai.genesis.models import GenesisPlan
from blueprint_ai.remediation import KITS, TEMPLATES


def contract(name: str) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": name, "version": "0.1.0"},
        "servers": [{"url": "http://localhost:8000"}],
        "paths": {
            "/api/health": {
                "get": {
                    "operationId": "getHealth",
                    "summary": "Service health",
                    "responses": {
                        "200": {
                            "description": "Service is ready",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["status"],
                                        "additionalProperties": False,
                                        "properties": {
                                            "status": {"type": "string", "enum": ["ok"]}
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def _python(root: Path, plan: GenesisPlan, roles: list[str]) -> None:
    name = plan.identity.module
    project = root / "pyproject.toml"
    text = project.read_text()
    dependencies = '["fastapi>=0.135,<1", "uvicorn>=0.41,<1"]' if "api" in roles else "[]"
    if "dependencies = []" not in text:
        raise ValueError("uv provider output changed: expected empty dependency list")
    text = text.replace("dependencies = []", "dependencies = " + dependencies)
    text += (
        "\n"
        "[dependency-groups]\n"
        'dev = ["ruff>=0.16.6,<1", "mypy>=2.3.1,<3", "pytest>=9.1.1,<10", '
        '"httpx>=0.28,<1"]\n'
    )
    text += (
        "\n"
        "[tool.ruff]\n"
        "line-length = 100\n"
        "\n"
        "[tool.ruff.lint]\n"
        'select = ["E", "F", "I", "UP", "B"]\n'
        "\n"
        "[tool.mypy]\n"
        "strict = true\n"
    )
    if "cli" in roles:
        text += f'\n[project.scripts]\n{plan.identity.package} = "{name}.cli:main"\n'
    write(root, "pyproject.toml", text)
    write(
        root,
        f"src/{name}/__init__.py",
        (
            '"""Public package interface."""\n'
            "\n"
            "\n"
            "def greeting(name: str) -> str:\n"
            '    """Return a greeting for a nonempty display name."""\n'
            "    if not name.strip():\n"
            '        raise ValueError("name must not be empty")\n'
            '    return f"Hello, {name.strip()}!"\n'
        ),
    )
    tests = f"""import pytest

from {name} import greeting


def test_greeting_trims_surrounding_whitespace() -> None:
    assert greeting("  Ada  ") == "Hello, Ada!"


def test_greeting_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="empty"):
        greeting("  ")
"""
    write(root, "tests/test_core.py", tests)
    if "cli" in roles:
        write(
            root,
            f"src/{name}/cli.py",
            f'''"""Command-line interface."""

import argparse

from {name} import greeting


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a greeting.")
    parser.add_argument("name", nargs="?", default="world")
    args = parser.parse_args()
    try:
        print(greeting(args.name))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
''',
        )
        write(
            root,
            "tests/test_cli_smoke.py",
            f'''import subprocess
import sys


def test_installed_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "{name}.cli", "Ada"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "Hello, Ada!"


def test_invalid_argument() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "{name}.cli", "  "], capture_output=True, text=True
    )
    assert result.returncode == 2
''',
        )
    if "api" in roles:
        json_file(root, "openapi.json", contract(plan.identity.display))
        write(
            root,
            f"src/{name}/app.py",
            '''"""Health endpoint; business routes belong in separate modules."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

app = FastAPI()


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"


@app.get("/api/health", response_model=Health)
def health() -> Health:
    return Health()
''',
        )
        write(
            root,
            "tests/test_api_contract_integration.py",
            f"""import json
from pathlib import Path

from fastapi.testclient import TestClient

from {name}.app import app


def test_health_matches_committed_contract() -> None:
    contract = json.loads((Path(__file__).parents[1] / "openapi.json").read_text())
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {{"status": "ok"}}
    assert "/api/health" in contract["paths"]
    assert "/api/health" in app.openapi()["paths"]


def test_unknown_route_returns_not_found() -> None:
    with TestClient(app) as client:
        assert client.get("/missing").status_code == 404
""",
        )
    write(
        root,
        "README.md",
        (
            f"# {plan.identity.display}\n"
            f"\n"
            f"## Install\n"
            f"\n"
            f"```sh\n"
            f"uv sync --locked\n"
            f"```\n"
            f"\n"
            f"## Usage\n"
            f"\n"
            f"See the public package interface in `src/{name}`.\n"
            f"\n"
            f"## Verify\n"
            f"\n"
            f"```sh\n"
            f"uv run ruff check .\n"
            f"uv run ruff format --check .\n"
            f"uv run mypy src\n"
            f"uv run pytest\n"
            f"uv build\n"
            f"```\n"
        ),
    )


def _typescript(root: Path, plan: GenesisPlan, roles: list[str]) -> None:
    package = json.loads((root / "package.json").read_text())
    package.update(
        name=plan.identity.package,
        version="0.1.0",
        type="module",
        license=plan.intent.license,
        private=True,
        engines={"node": ">=22.12.0"},
        files=["dist"],
    )
    package.pop("main", None)
    package["scripts"] = {
        "build": "tsc",
        "lint": "biome lint src tests",
        "format": "biome format --write src tests",
        "test": "node --test tests/*.test.mjs",
    }
    package["exports"] = {".": {"types": "./dist/index.d.ts", "import": "./dist/index.js"}}
    package["devDependencies"] = {
        "typescript": "7.0.2",
        "@types/node": "^24.0.0",
        "@biomejs/biome": "2.5.13",
    }
    if "api" in roles:
        package["dependencies"] = {"fastify": "5.12.4"}
        package["scripts"]["start"] = "node dist/server.js"
    if "cli" in roles:
        package["bin"] = {plan.identity.package: "dist/cli.js"}
    json_file(root, "package.json", package)
    json_file(
        root,
        "tsconfig.json",
        {
            "compilerOptions": {
                "target": "ES2022",
                "types": ["node"],
                "module": "NodeNext",
                "moduleResolution": "NodeNext",
                "strict": True,
                "outDir": "dist",
                "rootDir": "src",
                "declaration": True,
                "skipLibCheck": True,
            },
            "include": ["src/**/*.ts"],
        },
    )
    write(
        root,
        "src/index.ts",
        """export function greeting(name: string): string {
  if (!name.trim()) throw new Error("name must not be empty");
  return `Hello, ${name.trim()}!`;
}
""",
    )
    write(
        root,
        "tests/core.test.mjs",
        """import assert from "node:assert/strict";
import test from "node:test";
import { greeting } from "../dist/index.js";

test("greeting trims surrounding whitespace", () => assert.equal(greeting(" Ada "), "Hello, Ada!"));
test("greeting rejects empty input", () => assert.throws(() => greeting("  "), /empty/));
""",
    )
    if "cli" in roles:
        write(
            root,
            "src/cli.ts",
            """#!/usr/bin/env node
import { greeting } from "./index.js";

try {
  console.log(greeting(process.argv[2] ?? "world"));
} catch (error) {
  console.error(error instanceof Error ? error.message : "Invalid input");
  process.exitCode = 2;
}
""",
        )
        write(
            root,
            "tests/cli.smoke.test.mjs",
            """import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("installed CLI prints a greeting", () => {
  const result = spawnSync(process.execPath, ["dist/cli.js", "Ada"], { encoding: "utf8" });
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), "Hello, Ada!");
});
test("CLI rejects empty names", () => {
  assert.equal(spawnSync(process.execPath, ["dist/cli.js", "  "]).status, 2);
});
""",
        )
    if "api" in roles:
        json_file(root, "openapi.json", contract(plan.identity.display))
        write(
            root,
            "src/app.ts",
            """import Fastify from "fastify";

export function buildApp() {
  const app = Fastify({ logger: true, requestTimeout: 10000 });
  app.get("/api/health", async () => ({ status: "ok" }));
  return app;
}
""",
        )
        write(
            root,
            "src/server.ts",
            """import { buildApp } from "./app.js";

const app = buildApp();
const port = Number(process.env.PORT ?? "8000");
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => { void app.close(); });
}
try {
  await app.listen({ port, host: "0.0.0.0" });
} catch (error) {
  app.log.error(error);
  process.exitCode = 1;
}
""",
        )
        write(
            root,
            "tests/api.contract.integration.test.mjs",
            """import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildApp } from "../dist/app.js";

test("health matches committed API contract", async () => {
  const contract = JSON.parse(await readFile("openapi.json", "utf8"));
  const app = buildApp();
  try {
    const result = await app.inject({ method: "GET", url: "/api/health" });
    assert.equal(result.statusCode, 200);
    assert.deepEqual(result.json(), { status: "ok" });
    assert.ok(contract.paths["/api/health"]);
    assert.equal((await app.inject({ method: "GET", url: "/missing" })).statusCode, 404);
  } finally { await app.close(); }
});
""",
        )
    write(
        root,
        "README.md",
        (
            f"# {plan.identity.display}\n"
            f"\n"
            f"## Install\n"
            f"\n"
            f"```sh\n"
            f"npm ci --ignore-scripts\n"
            f"```\n"
            f"\n"
            f"## Usage\n"
            f"\n"
            f"Build the package before importing `dist/index.js`.\n"
            f"\n"
            f"## Verify\n"
            f"\n"
            f"```sh\n"
            f"npm run lint\n"
            f"npm run build\n"
            f"npm test\n"
            f"npm pack --dry-run\n"
            f"```\n"
        ),
    )


def _react(root: Path, plan: GenesisPlan) -> None:
    package = json.loads((root / "package.json").read_text())
    package["name"] = plan.identity.package + (
        "-frontend" if plan.intent.kind == "full-stack" else ""
    )
    package["license"] = plan.intent.license
    package["engines"] = {"node": ">=22.12.0"}
    package["scripts"]["test"] = "vitest run"
    package.setdefault("devDependencies", {})["vitest"] = "^4.0.0"
    json_file(root, "package.json", package)
    write(
        root,
        "src/App.tsx",
        """import { useState } from 'react'
import './App.css'

function App() {
  const [status, setStatus] = useState('Ready')
  async function checkHealth() {
    try {
      const response = await fetch('/api/health', { signal: AbortSignal.timeout(5000) })
      if (!response.ok) throw new Error('Service unavailable')
      const body: unknown = await response.json()
      const healthy = typeof body === 'object' && body !== null
        && 'status' in body && body.status === 'ok'
      setStatus(healthy ? 'Service is healthy' : 'Unexpected service response')
    } catch { setStatus('Service unavailable') }
  }
  return (
    <main>
      <h1>Workspace is ready</h1>
      <p>Build your application from this verified foundation.</p>
      <button type="button" onClick={() => void checkHealth()}>Check service</button>
      <p role="status">{status}</p>
    </main>
  )
}

export default App
""",
    )
    if plan.intent.kind != "full-stack":
        write(
            root,
            "src/App.tsx",
            """import './App.css'

function App() {
  return (
    <main>
      <h1>Workspace is ready</h1>
      <p>Build your application from this verified foundation.</p>
    </main>
  )
}

export default App
""",
        )
    write(
        root,
        "src/App.test.tsx",
        """import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import App from './App'

test('application exposes an accessible main landmark and heading', () => {
  const html = renderToStaticMarkup(<App />)
  expect(html).toContain('<main>')
  expect(html).toContain('<h1>Workspace is ready</h1>')
})
""",
    )
    if plan.intent.kind == "full-stack":
        write(
            root,
            "vite.config.ts",
            """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
""",
        )


def strengthen(root: Path, plan: GenesisPlan) -> None:
    from blueprint_ai.genesis.families import NATIVE_FAMILIES, strengthen_family

    native = plan.intent.kind in NATIVE_FAMILIES
    if native:
        strengthen_family(root, plan)
    for component in [] if native else plan.components:
        component_root = root / component.root
        if "Python" in component.languages:
            _python(component_root, plan, component.roles)
        elif "frontend" in component.roles:
            _react(component_root, plan)
        else:
            _typescript(component_root, plan, component.roles)
    if not plan.components or plan.intent.kind == "full-stack":
        readme = f"# {plan.identity.display}\n\n## Install\n\n"
        if plan.intent.kind == "full-stack":
            backend_install = (
                "uv sync --locked" if plan.intent.backend == "python" else "npm ci --ignore-scripts"
            )
            backend_run = (
                f"uv run uvicorn {plan.identity.module}.app:app --port 8000"
                if plan.intent.backend == "python"
                else "npm run build && npm start"
            )
            readme += (
                f"From `backend`, run `{backend_install}`. "
                "From `frontend`, run `npm ci --ignore-scripts`.\n"
                f"\n"
                f"## Usage\n"
                f"\n"
                f"From `backend`, run `{backend_run}`; from `frontend`, run `npm run dev`.\n"
                "The development proxy sends `/api` to port 8000. "
                "Production hosting must route `/api` to the backend.\n"
                f"\n"
                f"## Verify\n"
                f"\n"
                f"Run the build, lint, and test commands documented in each component.\n"
                f"The committed health contract is `backend/openapi.json`.\n"
            )
        elif plan.intent.kind == "openapi":
            readme += (
                "Install `openapi-spec-validator` in an isolated Python environment.\n"
                "\n"
                "## Usage\n"
                "\n"
                "Edit `openapi.json` as the contract authority.\n"
                "\n"
                "## Verify\n"
                "\n"
                "```sh\n"
                "openapi-spec-validator openapi.json\n"
                "```\n"
            )
        else:
            readme += (
                "No runtime dependencies are required.\n"
                "\n"
                "## Usage\n"
                "\n"
                "Add components and their native manifests as the project grows.\n"
                "\n"
                "## Verify\n"
                "\n"
                "```sh\n"
                "blueprint-ai review . --no-model\n"
                "```\n"
            )
        write(root, "README.md", readme)
    if plan.intent.kind == "openapi":
        json_file(root, "openapi.json", contract(plan.identity.display))
    write(root, ".editorconfig", TEMPLATES[".editorconfig"])
    write(
        root,
        ".gitignore",
        (
            "target/\n"
            "bin/\n"
            "obj/\n"
            ".next/\n"
            ".blueprint-state/\n"
            ".terraform/\n"
            "*.tfstate*\n"
            ".venv/\n"
            "node_modules/\n"
            "dist/\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".pytest_cache/\n"
            ".ruff_cache/\n"
            ".mypy_cache/\n"
            ".coverage\n"
            ".env\n"
            ".env.*\n"
            "!.env.example\n"
        ),
    )
    write(root, ".gitattributes", "* text=auto eol=lf\n")
    if plan.intent.license == "MIT":
        write(
            root,
            "LICENSE",
            (
                "MIT License\n"
                "\n"
                "Copyright (c) [year] [copyright holder]\n"
                "\n"
                "Permission is hereby granted, free of charge, to any person obtaining a "
                "copy\n"
                'of this software and associated documentation files (the "Software"), '
                "to deal\n"
                "in the Software without restriction, including without limitation the "
                "rights\n"
                "to use, copy, modify, merge, publish, distribute, sublicense, and/or "
                "sell\n"
                "copies of the Software, and to permit persons to whom the Software is\n"
                "furnished to do so, subject to the following conditions:\n"
                "\n"
                "The above copyright notice and this permission notice shall be included "
                "in all\n"
                "copies or substantial portions of the Software.\n"
                "\n"
                'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS '
                "OR\n"
                "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF "
                "MERCHANTABILITY,\n"
                "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL "
                "THE\n"
                "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
                "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING "
                "FROM,\n"
                "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS "
                "IN THE\n"
                "SOFTWARE.\n"
            ),
        )
    else:
        write(
            root,
            "LICENSE",
            "All rights reserved. No license to redistribute is granted by this project.\n",
        )
    if "ci" in plan.capabilities and not native:
        _ci(root, plan)
    if plan.intent.devcontainer:
        _devcontainer(root, plan)
    if plan.intent.container:
        _container(root, plan)
    if plan.intent.api_client:
        _api_client(root)


def _devcontainer(root: Path, plan: GenesisPlan) -> None:
    python = any("Python" in c.languages for c in plan.components)
    node = any("TypeScript" in c.languages for c in plan.components)
    docker = ""
    if python:
        docker += "FROM ghcr.io/astral-sh/uv:0.12.13 AS uv\n"
    if node and python:
        docker += "FROM node:24-bookworm-slim AS node\n"
    base = (
        "python:3.12-slim-bookworm"
        if python
        else "node:24-bookworm-slim"
        if node
        else "debian:bookworm-slim"
    )
    docker += f"FROM {base} AS development\n"
    if python:
        docker += "COPY --from=uv /uv /uvx /usr/local/bin/\nRUN python --version && uv --version\n"
    if node and python:
        docker += (
            "COPY --from=node /usr/local/bin/node /usr/local/bin/\n"
            "COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules\n"
            "RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm"
            " && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx\n"
        )
    if node:
        docker += "RUN node --version && npm --version\n"
    docker += (
        'RUN useradd --uid 10001 --create-home vscode\nUSER vscode\nCMD ["sleep", "infinity"]\n'
    )
    write(root, ".devcontainer/Dockerfile", docker)
    json_file(
        root,
        ".devcontainer/devcontainer.json",
        {
            "name": plan.identity.display,
            "build": {"dockerfile": "Dockerfile"},
            "remoteUser": "vscode",
        },
    )


def _api_client(root: Path) -> None:
    write(
        root,
        "frontend/src/api.ts",
        """import type { paths } from './generated/api'

type Health = paths['/api/health']['get']['responses'][200]['content']['application/json']

export async function getHealth(): Promise<Health> {
  const response = await fetch('/api/health', { signal: AbortSignal.timeout(5000) })
  if (!response.ok) throw new Error('Service unavailable')
  const body: unknown = await response.json()
  if (typeof body !== 'object' || body === null || !('status' in body) || body.status !== 'ok') {
    throw new Error('Unexpected health response')
  }
  return { status: body.status }
}
""",
    )

    app_path = root / "frontend/src/App.tsx"
    app = app_path.read_text()
    app = app.replace("import './App.css'", "import './App.css'\nimport { getHealth } from './api'")
    start = app.index("      const response = await fetch(")
    end = app.index("    } catch", start)
    app = (
        app[:start] + "      await getHealth()\n      setStatus('Service is healthy')\n" + app[end:]
    )
    write(root, "frontend/src/App.tsx", app)
    write(
        root,
        "frontend/src/api.test.ts",
        """import { afterEach, expect, test, vi } from 'vitest'
import { getHealth } from './api'

afterEach(() => vi.unstubAllGlobals())

test('accepts the committed health response and sends a timeout signal', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' })))
  vi.stubGlobal('fetch', fetch)
  await expect(getHealth()).resolves.toEqual({ status: 'ok' })
  expect(fetch).toHaveBeenCalledWith('/api/health', { signal: expect.any(AbortSignal) })
})

test.each([{ status: 'broken' }, {}, null])('rejects incompatible response: %j', async (body) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
  await expect(getHealth()).rejects.toThrow('Unexpected health response')
})

test('rejects an unavailable service', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 503 })))
  await expect(getHealth()).rejects.toThrow('Service unavailable')
})
""",
    )


def _ci(root: Path, plan: GenesisPlan) -> None:
    # Reuse the existing reviewed checkout pin and credential behavior.
    base = KITS["github-actions-ci"].files[".github/workflows/ci.yml"]
    checkout = base[base.index("      - uses:") : base.index("      - run:")]
    jobs = []
    for index, component in enumerate(plan.components):
        job = (
            f"  component-{index}:\n"
            f"    runs-on: ubuntu-latest\n"
            f"    defaults:\n"
            f"      run:\n"
            f"        working-directory: {component.root}\n"
            f"    steps:\n"
        ) + checkout
        if "Python" in component.languages:
            job += (
                "      - uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
                " # v10.0.1\n"
                "        with:\n"
                "          version: '0.12.13'\n"
                "          python-version: '3.12'\n"
            )
            commands = [
                "uv sync --locked",
                "uv run ruff check .",
                "uv run ruff format --check .",
                "uv run mypy src",
                "uv run pytest",
                "uv build",
            ]
        else:
            job += (
                "      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
                " # v7.0.0\n"
                "        with:\n"
                "          node-version: '24'\n"
                "          package-manager-cache: false\n"
            )
            commands = [
                "node --version",
                "npm ci --ignore-scripts",
                "npm run lint",
                "npm run build",
                "npm test",
            ]
        job += "".join("      - run: " + command + "\n" for command in commands)
        jobs.append(job)
    if not jobs:
        jobs = [base[base.index("  test:") :]]
    write(
        root,
        ".github/workflows/ci.yml",
        "name: CI\non: [push, pull_request]\npermissions:\n  contents: read\njobs:\n"
        + "".join(jobs),
    )
    json_file(
        root,
        "renovate.json",
        {
            "$schema": "https://docs.renovatebot.com/renovate-schema.json",
            "extends": ["config:recommended"],
        },
    )


def _container(root: Path, plan: GenesisPlan) -> None:
    component = next(c for c in plan.components if "api" in c.roles)
    target = root / component.root
    if "Python" in component.languages:
        docker = f'''FROM python:3.12-slim AS runtime
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c \\
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "{plan.identity.module}.app:app", "--host", "0.0.0.0", "--port", "8000"]
'''
    else:
        docker = (
            "FROM node:24-bookworm-slim AS build\nWORKDIR /app\nCOPY packag"
            "e*.json ./\nRUN npm ci --ignore-scripts\nCOPY tsconfig.json ./"
            "\nCOPY src ./src\nRUN npm run build && npm prune --omit=dev --"
            "ignore-scripts\nFROM node:24-bookworm-slim AS runtime\nWORKDIR"
            " /app\nCOPY --from=build --chown=node:node /app/dist ./dist\nC"
            "OPY --from=build --chown=node:node /app/node_modules ./node_"
            "modules\nCOPY --from=build --chown=node:node /app/package.jso"
            "n ./\nUSER node\nEXPOSE 8000\nHEALTHCHECK --interval=30s --time"
            "out=3s CMD node -e \"fetch('http://127.0.0.1:8000/api/health'"
            ").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
            '"\nCMD ["node", "dist/server.js"]\n'
        )
    write(target, "Dockerfile", docker)
    write(
        target,
        ".dockerignore",
        KITS["container"].files[".dockerignore"]
        + "dist\n.mypy_cache\n.pytest_cache\n.ruff_cache\n",
    )
