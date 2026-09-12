from pathlib import Path

import yaml


def test_ci_trigger_and_permission_contract() -> None:
    workflow = yaml.load(
        (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    triggers = workflow["on"]
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["tags"] == ["[0-9]+.[0-9]+.[0-9]+"]
    assert "paths" not in triggers["push"] and "paths-ignore" not in triggers["push"]
    assert "pull_request" in triggers and "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"].endswith("${{ github.ref }}")


def test_ci_has_distinct_quality_compatibility_sandbox_and_windows_jobs() -> None:
    workflow = yaml.load(
        (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    jobs = workflow["jobs"]
    assert set(jobs) == {"quality", "compatibility", "sandbox", "windows"}
    assert jobs["windows"]["runs-on"] == "windows-latest"
    assert jobs["compatibility"]["strategy"]["matrix"]["python"] == ["3.12", "3.13", "3.14"]
    commands = {name: [step.get("run", "") for step in job["steps"]] for name, job in jobs.items()}
    assert not any("pytest" in command for command in commands["quality"])
    assert sum("pip-audit" in command for rows in commands.values() for command in rows) == 1
    assert any("release_smoke.py" in command for command in commands["windows"])
