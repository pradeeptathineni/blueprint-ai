import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_ci_trigger_and_permission_contract() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    triggers = workflow["on"]
    assert triggers["push"]["branches"] == ["main"]
    assert "tags" not in triggers["push"]
    assert "paths" not in triggers["push"] and "paths-ignore" not in triggers["push"]
    assert {"pull_request", "workflow_dispatch", "workflow_call"} <= set(triggers)
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"].endswith("${{ github.ref }}")


def test_ci_has_distinct_quality_compatibility_sandbox_and_windows_jobs() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    jobs = workflow["jobs"]
    assert set(jobs) == {"quality", "workflow-audit", "compatibility", "sandbox", "windows"}
    assert jobs["windows"]["runs-on"] == "windows-latest"
    assert jobs["compatibility"]["strategy"]["matrix"]["python"] == ["3.12", "3.13", "3.14"]
    commands = {name: [step.get("run", "") for step in job["steps"]] for name, job in jobs.items()}
    assert not any("pytest" in command for command in commands["quality"])
    assert sum("pip-audit" in command for rows in commands.values() for command in rows) == 1
    assert any("release_smoke.py" in command for command in commands["windows"])
    assert any("phase7_migrations.py" in command for command in commands["quality"])
    assert any("compatibility_snapshot.py" in command for command in commands["quality"])
    assert any("blueprint-ai support" in command for command in commands["quality"])
    assert any("actionlint" in command for command in commands["workflow-audit"])
    assert any("zizmor" in command for command in commands["workflow-audit"])


def test_release_is_tag_only_build_once_and_least_privilege() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert workflow["on"] == {"push": {"tags": ["[0-9]+.[0-9]+.[0-9]+"]}}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert set(jobs) == {
        "release-gate",
        "build",
        "github-release",
        "verify-github-release",
        "publish-pypi",
        "verify-pypi",
    }
    assert jobs["release-gate"] == {
        "name": "Complete release quality gate",
        "uses": "./.github/workflows/ci.yml",
        "permissions": {"contents": "read"},
    }
    assert jobs["build"]["needs"] == "release-gate"
    assert jobs["build"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert jobs["publish-pypi"]["permissions"] == {"id-token": "write"}
    commands = [step.get("run", "") for job in jobs.values() for step in job.get("steps", [])]
    assert sum("uv build" in command for command in commands) == 1
    assert all("${{" not in command for command in commands)
    release_commands = "\n".join(commands)
    assert "--verify-tag" in release_commands
    assert "release-assets/SHA256SUMS" in release_commands
    assert "gh release verify" in release_commands
    assert "gh release verify-asset" in release_commands
    assert "gh attestation verify" in release_commands
    assert "verify_pypi.py" in release_commands
    assert "pipx install" in release_commands and "uv tool install" in release_commands


def test_release_actions_are_pinned_and_publishing_is_secretless_opt_in() -> None:
    workflow_path = ROOT / ".github/workflows/release.yml"
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    action_refs = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    ]
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref) for ref in action_refs)
    publisher = workflow["jobs"]["publish-pypi"]
    assert publisher["if"] == "vars.BLUEPRINT_PUBLISH_PYPI == 'true'"
    assert publisher["environment"]["name"] == "pypi"
    assert publisher["needs"] == ["build", "verify-github-release"]
    assert "password" not in workflow_path.read_text().lower()


def test_release_sbom_is_runtime_only_and_all_publication_waits_for_full_gate() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    jobs = workflow["jobs"]
    build = jobs["build"]
    sbom = next(
        step for step in build["steps"] if step.get("uses", "").startswith("anchore/sbom-action@")
    )
    assert sbom["with"]["path"] == "/tmp/blueprint-runtime"
    assert "runtime.spdx.json" in sbom["with"]["output-file"]
    assert all(step.get("with", {}).get("path") != "." for step in build["steps"])
    assert jobs["github-release"]["needs"] == "build"
    assert jobs["verify-github-release"]["needs"] == ["build", "github-release"]
    assert jobs["verify-pypi"]["needs"] == "publish-pypi"


def test_distribution_name_preserves_executable_and_import() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["name"] == "blueprint-ai-cli"
    assert metadata["project"]["version"] == "0.9.1"
    assert metadata["project"]["scripts"] == {"blueprint-ai": "blueprint_ai.cli:app"}
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/blueprint_ai"
    ]
