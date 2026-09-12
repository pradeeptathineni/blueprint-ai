"""Known-bad scanner mutation cycles inside OCI, with source snapshots and native parsers."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from blueprint_ai.adapters.registry import known_tools
from blueprint_ai.sandbox import SandboxPolicy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    definitions = {a.name: a for a in known_tools()}
    cases = [
        (
            "ruff",
            "main.py",
            "value = 1\n",
            "import os\nvalue = 1\n",
            {},
            ["check", "--no-cache", "--output-format", "json", "main.py"],
        ),
        (
            "hadolint",
            "Dockerfile",
            'FROM debian:bookworm-slim\nUSER 65532:65532\nCMD ["true"]\n',
            'FROM debian\nUSER 65532:65532\nCMD ["true"]\n',
            {},
            ["--format", "json", "Dockerfile"],
        ),
        (
            "markdownlint-cli2",
            "README.md",
            "# Example\n\nHello.\n",
            "# Example\n\n\nHello.\n",
            {},
            ["README.md"],
        ),
        (
            "conftest",
            "input.json",
            '{"public":false}\n',
            '{"public":true}\n',
            {
                "policy/main.rego": "package main\ndeny contains msg if {\n  input.public == true\n"
                '  msg := "public access denied"\n}\n'
            },
            ["test", "input.json", "--output", "json", "--no-color"],
        ),
        (
            "ast-grep",
            "main.py",
            'print("safe")\n',
            'eval("1+1")\n',
            {
                "sgconfig.yml": "ruleDirs: [rules]\n",
                "rules/eval.yml": "id: no-eval\nlanguage: Python\nseverity: error\n"
                "message: Avoid eval.\nrule:\n  pattern: eval($$$ARGS)\n",
            },
            ["scan", "--json", "."],
        ),
        (
            "buf",
            "example/v1/message.proto",
            'syntax = "proto3";\npackage example.v1;\nmessage Query { string value = 1; }\n',
            'syntax = "proto3";\nmessage Query { string value = 1; }\n',
            {"buf.yaml": "version: v2\nmodules:\n  - path: .\nlint:\n  use: [STANDARD]\n"},
            ["lint", "--error-format=json"],
        ),
    ]
    rows = []
    expected_rules = {
        "ruff": "F401",
        "hadolint": "DL3006",
        "markdownlint-cli2": "markdownlint/MD012",
        "conftest": "main",
        "ast-grep": "no-eval",
        "buf": "PACKAGE_DEFINED",
        "gitleaks": "generic-api-key",
        "semgrep": "no-eval",
    }
    cases.extend(
        [
            (
                "gitleaks",
                "config.py",
                'api_key = ""\n',
                "api_key = "
                + json.dumps(hashlib.sha256(b"synthetic-fixture-only").hexdigest())
                + "\n",
                {},
                definitions["gitleaks"].args,
            ),
            (
                "semgrep",
                "main.py",
                'print("safe")\n',
                'eval("1 + 1")\n',
                {
                    ".semgrep.yml": "rules:\n  - id: no-eval\n    languages: [python]\n"
                    "    severity: ERROR\n    message: Avoid dynamic evaluation.\n"
                    "    pattern: eval(...)\n"
                },
                definitions["semgrep"].args,
            ),
        ]
    )
    for name, relative, clean, bad, assets, command in cases:
        with tempfile.TemporaryDirectory(prefix="blueprint-tool-fixture-", dir="/tmp") as temporary:
            root = Path(temporary)
            for rel, content in assets.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            adapter = definitions[name]
            adapter.args = command
            adapter.sandbox = SandboxPolicy(backend="docker")
            phases: list[dict[str, Any]] = []
            for content in (clean, bad, clean):
                target.write_text(content)
                before = {
                    str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
                }
                status, findings, error = adapter.run(root, timeout=30)
                after = {
                    str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
                }
                phases.append(
                    {
                        "status": status.model_dump(),
                        "rules": [f.rule_id for f in findings],
                        "error": error,
                        "unchanged": before == after,
                    }
                )
            passed = (
                [p["status"]["outcome"] for p in phases] == ["passed", "finding", "passed"]
                and expected_rules[name] in phases[1]["rules"]
                and all(
                    p["unchanged"]
                    and p["error"] is None
                    and p["status"]["sandbox"]["isolated"]
                    and p["status"]["sandbox"]["teardown"]
                    for p in phases
                )
            )
            rows.append(
                {
                    "tool": name,
                    "passed": passed,
                    "expected_rule": expected_rules[name],
                    "phases": phases,
                }
            )
            args.output.write_text(json.dumps(rows, indent=2) + "\n")
            print(
                name,
                passed,
                [(p["status"]["outcome"], p["rules"], p["error"]) for p in phases],
                flush=True,
            )
    raise SystemExit(0 if all(row["passed"] for row in rows) else 1)


if __name__ == "__main__":
    main()
