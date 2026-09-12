"""Additional real tool contracts; prepared optional tools never install implicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from blueprint_ai.adapters.base import ExternalToolAdapter, parse_output_paths
from blueprint_ai.adapters.registry import known_tools
from blueprint_ai.sandbox import SandboxPolicy


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-tools", type=Path, help="directory with kube-linter and biome/")
    parser.add_argument(
        "--native-host", action="store_true", help="trusted system scanners on controlled fixtures"
    )
    options = parser.parse_args()
    definitions = {a.name: a for a in known_tools()}
    definitions["gofmt"] = ExternalToolAdapter(
        "gofmt", "code-quality", ["-l", "main.go"], parse_output_paths, expected_codes={0}
    )
    cases: list[tuple[str, str | None, str, str, str, dict[str, str], str | None, str | None]] = [
        (
            "gofmt",
            "golang:1.26-bookworm",
            "main.go",
            "package demo\n\nvar Value = 1\n",
            "package demo\n\nvar Value=1\n",
            {},
            None,
            "gofmt/format",
        ),
        (
            "terraform-fmt",
            "hashicorp/terraform:1.16.2",
            "main.tf",
            'locals {\n  value = "ok"\n}\n',
            'locals {\nvalue="ok"\n}\n',
            {},
            None,
            None,
        ),
    ]
    if options.native_tools:
        cases.extend(
            [
                (
                    "biome",
                    "node:24-bookworm-slim",
                    "index.js",
                    "const value = 1;\n",
                    "debugger;\n",
                    {
                        "biome.json": json.dumps(
                            {
                                "formatter": {"enabled": False},
                                "assist": {"enabled": False},
                                "linter": {
                                    "rules": {
                                        "preset": "none",
                                        "suspicious": {"noDebugger": "error"},
                                    }
                                },
                            }
                        )
                    },
                    "biome/node_modules/.bin/biome",
                    "lint/suspicious/noDebugger",
                ),
                (
                    "kube-linter",
                    "node:24-bookworm-slim",
                    "deploy.yaml",
                    "apiVersion: v1\nkind: Pod\nmetadata:\n  name: test\nspec:\n  containers:\n"
                    "    - name: app\n      image: nginx:1.27\n      securityContext:\n"
                    "        privileged: false\n",
                    "apiVersion: v1\nkind: Pod\nmetadata:\n  name: test\nspec:\n  containers:\n"
                    "    - name: app\n      image: nginx:1.27\n      securityContext:\n"
                    "        privileged: true\n",
                    {
                        "config.yaml": "checks:\n  doNotAutoAddDefaults: true\n"
                        "  include: [privileged-container]\n"
                    },
                    "kube-linter",
                    "kube-linter/privileged-container",
                ),
            ]
        )
    if options.native_host:
        cases.extend(
            [
                (
                    "actionlint",
                    None,
                    ".github/workflows/caller.yml",
                    "name: Call\non: push\njobs:\n  call:\n"
                    "    uses: ./.github/workflows/called.yml\n",
                    "name: Call\non: push\njobs:\n  call:\n"
                    "    uses: $/.github/workflows/called.yml\n",
                    {
                        ".github/workflows/called.yml": "name: Called\non: workflow_call\njobs:\n"
                        "  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n"
                    },
                    None,
                    "actionlint/workflow-call",
                ),
                (
                    "shellcheck",
                    None,
                    "check.sh",
                    '#!/bin/sh\necho "$1"\n',
                    "#!/bin/sh\necho $1\n",
                    {},
                    None,
                    "2086",
                ),
            ]
        )
    rows: list[dict[str, Any]] = []
    for name, image, relative, clean, bad, assets, tool_path, expected in cases:
        with tempfile.TemporaryDirectory(prefix="blueprint-redteam-tool-") as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            fixture.mkdir()
            if tool_path:
                assert options.native_tools is not None
                if name == "biome":
                    shutil.copytree(options.native_tools / "biome", root / "biome", symlinks=True)
                else:
                    shutil.copy2(options.native_tools / "kube-linter", root / "kube-linter")
            for relative_asset, text in assets.items():
                (fixture / relative_asset).parent.mkdir(parents=True, exist_ok=True)
                (fixture / relative_asset).write_text(text)
            adapter = definitions[name]
            adapter.working_directory = "fixture"
            if tool_path:
                adapter.executable = str(root / tool_path)
            if name == "kube-linter":
                adapter.args = [
                    "lint",
                    "deploy.yaml",
                    "--config",
                    "config.yaml",
                    "--format",
                    "json",
                ]
            if name == "actionlint":
                adapter.args = ["-format", "{{json .}}", ".github/workflows/caller.yml"]
            if name == "shellcheck":
                adapter.args = ["--format=json", "check.sh"]
            adapter.sandbox = (
                SandboxPolicy(backend="docker", image=image)
                if image
                else SandboxPolicy(backend="host", trusted=True)
            )
            phases: list[dict[str, Any]] = []
            for source in (clean, bad, clean):
                (fixture / relative).write_text(source)
                before = snapshot(fixture)
                status, findings, error = adapter.run(root, timeout=40)
                phases.append(
                    {
                        "status": status.model_dump(mode="json"),
                        "findings": [f.model_dump(mode="json") for f in findings],
                        "error": error,
                        "unchanged": before == snapshot(fixture),
                    }
                )
            passed = (
                [phase["status"]["outcome"] for phase in phases] == ["passed", "finding", "passed"]
                and (expected is None or expected in {f["rule_id"] for f in phases[1]["findings"]})
                and all(
                    p["unchanged"]
                    and not p["error"]
                    and (not image or p["status"]["sandbox"]["teardown"])
                    for p in phases
                )
            )
            rows.append({"tool": name, "passed": passed, "phases": phases})
            options.output.write_text(json.dumps(rows, indent=2) + "\n")
            print(name, passed, [(p["status"]["outcome"], p["error"]) for p in phases], flush=True)
    raise SystemExit(0 if all(r["passed"] for r in rows) else 1)


if __name__ == "__main__":
    main()
