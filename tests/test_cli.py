from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    return subprocess.run(
        [sys.executable, "-m", "blueprint_ai.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_inspect_json_subprocess(python_project: Path) -> None:
    result = run_cli("inspect", str(python_project), "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["name"] == python_project.name
    assert payload["languages"]["Python"] == 1


def test_review_plan_apply_verify_e2e(python_project: Path) -> None:
    for command in ("review", "plan", "apply", "verify"):
        result = (
            run_cli(
                command,
                str(python_project),
                "--blueprint",
                "repository",
                "--model",
                "off",
                "--json",
            )
            if command in {"review", "plan", "apply"}
            else run_cli(command, str(python_project), "--blueprint", "repository", "--json")
        )
        assert result.returncode == 0, f"{command}: {result.stderr}"
        json.loads(result.stdout)
    assert (python_project / ".editorconfig").is_file()


def test_review_markdown_and_sarif_cli_contracts(python_project: Path) -> None:
    markdown = run_cli(
        "review",
        str(python_project),
        "--blueprint",
        "repository",
        "--model",
        "off",
        "--format",
        "markdown",
    )
    assert markdown.returncode == 0, markdown.stderr
    assert markdown.stdout.startswith("# Blueprint AI review")

    sarif = run_cli(
        "review",
        str(python_project),
        "--blueprint",
        "repository",
        "--model",
        "off",
        "--format",
        "sarif",
    )
    assert sarif.returncode == 0, sarif.stderr
    assert json.loads(sarif.stdout)["version"] == "2.1.0"


def test_cli_baseline_separates_existing_findings(python_project: Path) -> None:
    created = run_cli("baseline", str(python_project), "--profile", "portfolio", "--json")
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["findings"] > 0
    reviewed = run_cli(
        "review",
        str(python_project),
        "--profile",
        "portfolio",
        "--model",
        "off",
        "--json",
    )
    payload = json.loads(reviewed.stdout)
    dispositions = {
        finding["disposition"] for result in payload["results"] for finding in result["findings"]
    }
    assert dispositions == {"baseline"}


def test_schema_help_lists_every_supported_schema() -> None:
    result = run_cli("schema", "--help")
    assert result.returncode == 0, result.stderr
    for name in (
        "settings",
        "report",
        "custom-blueprint",
        "intent",
        "genesis-plan",
        "project-graph",
        "sandbox-policy",
        "evolution-plan",
        "evolution-report",
        "transformation",
    ):
        assert name in result.stdout
