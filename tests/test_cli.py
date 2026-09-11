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
