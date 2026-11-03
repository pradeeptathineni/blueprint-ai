from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blueprint_ai.impact import analyze_impact


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    write_json(root / "package.json", {"private": True, "workspaces": ["packages/*", "apps/*"]})
    write_json(
        root / "packages/core/package.json",
        {"name": "@example/core", "scripts": {"test": "node --test"}},
    )
    write_json(root / "packages/util/package.json", {"name": "@example/util"})
    write_json(
        root / "apps/web/package.json",
        {"name": "@example/web", "dependencies": {"@example/core": "workspace:*"}},
    )
    (root / "packages/core/src").mkdir()
    (root / "packages/core/src/index.ts").write_text("export const value = 1\n")
    (root / "packages/core/src/index.test.ts").write_text("// test candidate\n")
    git(root, "init", "-q")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return root


def test_changed_package_selects_declared_dependents_without_running_tests(workspace: Path) -> None:
    (workspace / "packages/core/src/index.ts").write_text("export const value = 2\n")
    result = analyze_impact(workspace)
    assert result["status"] == "mapped"
    assert [row["name"] for row in result["packages"]] == ["@example/core", "@example/web"]
    core, web = result["packages"]
    assert core["reasons"] == ["changed"]
    assert core["test_script"] == "node --test"
    assert core["sample_test_files"] == ["packages/core/src/index.test.ts"]
    assert web["reasons"] == ["depends-on-workspace"]
    assert web["candidate_test_count"] == 0


def test_root_configuration_is_conservative_and_unmapped_paths_remain_visible(
    workspace: Path,
) -> None:
    (workspace / "package-lock.json").write_text("{}\n")
    (workspace / "README.md").write_text("Unmapped project note.\n")
    result = analyze_impact(workspace)
    assert result["status"] == "partial"
    assert result["shared_config_files"] == ["package-lock.json"]
    assert result["unmapped_changed_files"] == ["README.md"]
    assert len(result["packages"]) == 3
    assert all("shared-config" in row["reasons"] for row in result["packages"])
    assert all("unknown-impact" in row["reasons"] for row in result["packages"])


def test_unknown_root_change_selects_all_candidates_but_stays_partial(workspace: Path) -> None:
    (workspace / "root-tooling.conf").write_text("changed\n")
    result = analyze_impact(workspace)
    assert result["status"] == "partial"
    assert result["unmapped_changed_files"] == ["root-tooling.conf"]
    assert len(result["packages"]) == 3
    assert all(row["reasons"] == ["unknown-impact"] for row in result["packages"])


def test_root_npm_config_selects_all_packages_as_known_shared_input(workspace: Path) -> None:
    (workspace / ".npmrc").write_text("engine-strict=true\n")
    result = analyze_impact(workspace)
    assert result["status"] == "mapped"
    assert result["shared_config_files"] == [".npmrc"]
    assert len(result["packages"]) == 3
    assert all(row["reasons"] == ["shared-config"] for row in result["packages"])


def test_nested_workspace_selects_parent_and_parent_dependent(workspace: Path) -> None:
    write_json(
        workspace / "package.json",
        {"private": True, "workspaces": ["packages/*", "packages/core/packages/*", "apps/*"]},
    )
    write_json(workspace / "packages/core/packages/child/package.json", {"name": "@example/child"})
    (workspace / "packages/core/packages/child/index.test.ts").write_text("// child test\n")
    git(workspace, "add", ".")
    git(
        workspace,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "nested workspace",
    )
    (workspace / "packages/core/packages/child/index.test.ts").write_text("// changed test\n")
    result = analyze_impact(workspace)
    rows = {row["name"]: row for row in result["packages"]}
    assert set(rows) == {"@example/child", "@example/core", "@example/web"}
    assert rows["@example/core"]["reasons"] == ["contains-workspace"]
    assert rows["@example/web"]["reasons"] == ["depends-on-workspace"]
    assert rows["@example/core"]["sample_test_files"] == ["packages/core/src/index.test.ts"]


def test_invalid_workspace_or_missing_git_root_refuses_complete_impact(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    write_json(root / "package.json", {"workspaces": ["packages/*"]})
    with pytest.raises(ValueError, match="exact Git repository root"):
        analyze_impact(root)
