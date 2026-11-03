from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from blueprint_ai.cli import app
from blueprint_ai.impact import analyze_impact
from blueprint_ai.uv_impact import uv_packages


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(root: Path) -> None:
    for args in (("add", "."), ("commit", "-qm", "fixture")):
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *args,
            ],
            check=True,
            capture_output=True,
        )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    write(
        tmp_path / "pyproject.toml",
        """[project]
name = "workspace-root"
dependencies = ["consumer"]
[tool.uv.workspace]
members = ["packages/*"]
""",
    )
    write(tmp_path / "packages/core/pyproject.toml", '[project]\nname = "Core_Pkg"\n')
    write(tmp_path / "packages/core/core.py", "VALUE = 1\n")
    write(tmp_path / "packages/core/tests/test_core.py", "# candidate, never executed\n")
    write(
        tmp_path / "packages/consumer/pyproject.toml",
        """[project]
name = "consumer"
dependencies = ["Core.Pkg; python_version < '2'"]
[project.optional-dependencies]
extra = ["Core_Pkg"]
[dependency-groups]
Test_Group = ["Core-Pkg"]
dev = [{include-group = "test.group"}]
[build-system]
requires = ["Core_Pkg"]
""",
    )
    write(tmp_path / "packages/unrelated/pyproject.toml", '[project]\nname = "unrelated"\n')
    write(tmp_path / "tests/test_root.py", "# root candidate, never executed\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    commit(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "declaration",
    [
        "dependencies = [\"Core.Pkg; python_version < '2'\"]\n",
        '[project.optional-dependencies]\nextra = ["Core_Pkg"]\n',
        '[dependency-groups]\nTest_Group = ["Core-Pkg"]\ndev = [{include-group = "test.group"}]\n',
        '[build-system]\nrequires = ["Core_Pkg"]\n',
    ],
)
def test_static_union_selects_normalized_dependents_and_root(
    workspace: Path, declaration: str
) -> None:
    write(
        workspace / "packages/consumer/pyproject.toml",
        '[project]\nname = "consumer"\n' + declaration,
    )
    commit(workspace)
    write(workspace / "packages/core/core.py", "VALUE = 2\n")
    result = analyze_impact(workspace)
    rows = {row["name"]: row for row in result["packages"]}
    assert result["workspace_type"] == "uv" and result["status"] == "mapped"
    assert set(rows) == {"core-pkg", "consumer", "workspace-root"}
    assert rows["consumer"]["internal_dependencies"] == ["core-pkg"]
    assert rows["consumer"]["reasons"] == ["depends-on-workspace"]
    assert rows["core-pkg"]["sample_test_files"] == ["packages/core/tests/test_core.py"]
    assert rows["workspace-root"]["sample_test_files"] == ["tests/test_root.py"]
    assert all(row["test_script"] is None for row in rows.values())


def test_package_limit_and_normalized_name_collisions_refuse(workspace: Path) -> None:
    with pytest.raises(ValueError, match="too many packages"):
        uv_packages(workspace, max_packages=2, max_manifest_bytes=256_000)
    write(workspace / "packages/unrelated/pyproject.toml", '[project]\nname = "core.pkg"\n')
    with pytest.raises(ValueError, match="collide"):
        analyze_impact(workspace)


@pytest.mark.parametrize("path", ["pyproject.toml", "uv.lock", "root-input.txt"])
def test_root_changes_select_all_and_remain_partial(workspace: Path, path: str) -> None:
    with (workspace / path).open("a") as stream:
        stream.write("\n# changed root input\n")
    result = analyze_impact(workspace)
    assert result["status"] == "partial"
    assert len(result["packages"]) == 4
    key = "unmapped_changed_files" if path == "root-input.txt" else "shared_config_files"
    assert result[key] == [path]


def test_dynamic_dependencies_never_produce_a_narrow_selection(workspace: Path) -> None:
    write(
        workspace / "packages/core/pyproject.toml",
        '[project]\nname = "Core_Pkg"\ndynamic = ["dependencies", "optional-dependencies"]\n',
    )
    result = analyze_impact(workspace)
    assert result["status"] == "partial"
    assert result["unresolved_dependency_packages"] == ["core-pkg"]
    assert len(result["packages"]) == 4
    assert all("unknown-dependencies" in row["reasons"] for row in result["packages"])


def test_exclusions_are_root_relative_and_nested_packages_keep_their_owner(workspace: Path) -> None:
    write(
        workspace / "pyproject.toml",
        """[project]
name = "workspace-root"
[tool.uv.workspace]
members = ["packages/*", "nested/packages/*", "packages/consumer/children/*"]
exclude = ["packages/skip"]
""",
    )
    write(workspace / "packages/skip/pyproject.toml", "invalid but excluded")
    write(workspace / "nested/packages/skip/pyproject.toml", '[project]\nname = "nested-skip"\n')
    write(
        workspace / "packages/consumer/children/child/pyproject.toml", '[project]\nname = "child"\n'
    )
    write(workspace / "packages/consumer/children/child/tests/test_child.py", "# candidate\n")
    commit(workspace)
    write(
        workspace / "packages/consumer/children/child/tests/test_child.py", "# changed candidate\n"
    )
    result = analyze_impact(workspace)
    rows = {row["name"]: row for row in result["packages"]}
    assert set(rows) == {"child", "consumer", "workspace-root"}
    assert rows["consumer"]["reasons"] == ["contains-workspace"]
    assert rows["consumer"]["candidate_test_count"] == 0
    packages, _ = uv_packages(workspace, max_packages=256, max_manifest_bytes=256_000)
    assert "nested-skip" in packages


@pytest.mark.parametrize(
    "unsafe", ["member-link", "ancestor-link", "manifest-link", "oversized", "traversal"]
)
def test_unsafe_workspace_reads_refuse_before_content_is_consumed(
    workspace: Path, tmp_path: Path, unsafe: str
) -> None:
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    write(outside / "pyproject.toml", '[project]\nname = "outside"\n')
    if unsafe == "member-link":
        (workspace / "packages/link").symlink_to(outside, target_is_directory=True)
    elif unsafe == "ancestor-link":
        (workspace / "alias").symlink_to(outside, target_is_directory=True)
        write(workspace / "pyproject.toml", '[tool.uv.workspace]\nmembers = ["alias/*"]\n')
    elif unsafe == "manifest-link":
        manifest = workspace / "packages/core/pyproject.toml"
        manifest.unlink()
        manifest.symlink_to(outside / "pyproject.toml")
    elif unsafe == "oversized":
        write(workspace / "packages/core/pyproject.toml", "#" * 256_001)
    else:
        write(workspace / "pyproject.toml", '[tool.uv.workspace]\nmembers = ["../*"]\n')
    with pytest.raises(ValueError, match="symlink|safely|unsupported workspace pattern"):
        analyze_impact(workspace, workspace="uv")


def test_virtual_root_and_mixed_repo_cli_keep_existing_npm_selection(workspace: Path) -> None:
    write(workspace / "pyproject.toml", '[tool.uv.workspace]\nmembers = ["packages/*"]\n')
    write(workspace / "package.json", json.dumps({"workspaces": ["js/*"]}))
    write(workspace / "js/web/package.json", '{"name":"web"}')
    commit(workspace)
    write(workspace / "packages/core/core.py", "VALUE = 2\n")
    runner = CliRunner()
    uv = runner.invoke(app, ["impact", str(workspace), "--workspace", "uv", "--json"])
    assert uv.exit_code == 0, uv.output
    result = json.loads(uv.output)
    assert {row["name"] for row in result["packages"]} == {"core-pkg", "consumer"}
    automatic = runner.invoke(app, ["impact", str(workspace), "--json"])
    npm = runner.invoke(app, ["impact", str(workspace), "--workspace", "npm", "--json"])
    assert automatic.exit_code == npm.exit_code == 0
    assert json.loads(automatic.output) == json.loads(npm.output)
    assert "workspace_type" not in json.loads(npm.output)
