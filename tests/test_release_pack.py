from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blueprint_ai import release_pack


def _project(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("# local pack\n", encoding="utf-8")
    (path / "LICENSE").write_text("Example license evidence\n", encoding="utf-8")
    (path / "private.txt").write_text("not selected\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md", "LICENSE", "private.txt"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return path.resolve()


def _workspace_project(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "package.json").write_text(
        json.dumps(
            {
                "name": "sample-workspace",
                "packageManager": "npm@10.0.0",
                "workspaces": ["packages/*", "apps/*"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for directory, name, dependencies in (
        ("packages/library", "@sample/library", {}),
        ("apps/client", "@sample/client", {"@sample/library": "workspace:*"}),
    ):
        folder = path / directory
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "exports": "./index.js",
                    "dependencies": dependencies,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (folder / "index.js").write_text(
            f"export const packageName = {name!r};\n", encoding="utf-8"
        )
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "synthetic workspace",
        ],
        check=True,
    )
    return path.resolve()


def test_pack_roundtrip_is_deterministic_and_only_contains_selected_files(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")
    selected = ["LICENSE", "README.md"]
    first = release_pack.build(project, selected, target_id="fixture")
    second = release_pack.build(project, selected, target_id="fixture")
    assert first["pack_sha256"] == second["pack_sha256"]
    reopened = release_pack.verify_pack(
        __import__("base64").b64decode(first["zip_base64"], validate=True),
        first["pack_sha256"],
        expected_target_id="fixture",
    )
    assert reopened["verified"] is True
    assert [row["path"] for row in reopened["manifest"]["files"]] == selected
    assert reopened["manifest"]["license_notice_evidence_status"] == "selected-evidence-only"
    assert reopened["manifest"]["source"]["working_tree_changed"] is False
    with pytest.raises(ValueError, match="target identity"):
        release_pack.verify_pack(
            __import__("base64").b64decode(first["zip_base64"], validate=True),
            first["pack_sha256"],
            expected_target_id="other-project",
        )


def test_legacy_pack_can_only_use_explicit_private_scope_compatibility(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")
    legacy = release_pack.build(project, ["LICENSE"])
    data = __import__("base64").b64decode(legacy["zip_base64"], validate=True)
    with pytest.raises(ValueError, match="target identity"):
        release_pack.verify_pack(data, legacy["pack_sha256"], expected_target_id="fixture")
    reopened = release_pack.verify_pack(
        data,
        legacy["pack_sha256"],
        expected_target_id="fixture",
        allow_legacy_scoped_target=True,
    )
    assert reopened["verified"] is True
    assert reopened["target_binding"] == "legacy-private-journal-scope"
    assert "predates embedded target IDs" in reopened["qualification"]
    assert reopened["manifest"]["target"] == {"name": "project"}


def test_pack_refuses_omissions_binary_and_source_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path / "project")
    with pytest.raises(ValueError, match="omission"):
        release_pack.build(project, ["README.md", "private.txt", "absent.txt"])
    (project / "binary.dat").write_bytes(b"\x00payload")
    subprocess.run(["git", "-C", str(project), "add", "binary.dat"], check=True)
    with pytest.raises(ValueError, match="omission"):
        release_pack.build(project, ["README.md", "binary.dat"])

    original = release_pack.preview
    calls = 0

    def change_after_first_snapshot(root, paths, **kwargs):
        nonlocal calls
        calls += 1
        result = original(root, paths, **kwargs)
        if calls == 2:
            (project / "README.md").write_text("# changed during pack\n", encoding="utf-8")
            result = original(root, paths, **kwargs)
        return result

    monkeypatch.setattr(release_pack, "preview", change_after_first_snapshot)
    with pytest.raises(
        ValueError, match="changed during pack|changed during pack creation|changed"
    ):
        release_pack.build(project, ["README.md"])


def test_multi_package_selection_and_planning_readiness_remain_source_bound(tmp_path: Path) -> None:
    project = _workspace_project(tmp_path / "workspace")
    selected = [
        "apps/client/index.js",
        "apps/client/package.json",
        "packages/library/index.js",
        "packages/library/package.json",
    ]
    base = release_pack.preview(project, selected, target_id="workspace")
    readiness = {
        "schema": "deep.planning-release-readiness.v1",
        "owner": "planning-ai",
        "owner_revision": "8f1ccf3a79e1720c3714681438264f6d0db0821a",
        "assessment": {
            "id": "10101010-1010-4010-8010-101010101010",
            "revision": 1,
            "sha256": "a" * 64,
            "readiness": "ready_for_human_review",
            "requirement_count": 1,
            "blocker_count": 0,
        },
        "specification": {
            "id": "20202020-2020-4020-8020-202020202020",
            "revision": 2,
            "sha256": "b" * 64,
        },
        "project_binding": {
            "target_id": "workspace",
            "report_id": "c" * 64,
            "source_digest": base["source"]["source_digest"],
            "currentness": "current",
        },
        "currentness": "current",
        "qualification": [
            "Planning readiness is a human-review decision, not release or deployment approval."
        ],
    }
    reviewed = release_pack.preview(
        project,
        selected,
        target_id="workspace",
        planning_readiness=readiness,
    )
    repeated = release_pack.preview(
        project,
        selected,
        target_id="workspace",
        planning_readiness=readiness,
    )
    assert reviewed["selected_packages"] == repeated["selected_packages"]
    assert {row["name"] for row in reviewed["selected_packages"]} == {
        "@sample/client",
        "@sample/library",
    }
    assert reviewed["unmapped_selected_files"] == []
    assert reviewed["planning_readiness"] == readiness
    built = release_pack.build(
        project,
        selected,
        target_id="workspace",
        planning_readiness=readiness,
    )
    cold = release_pack.verify_pack(
        __import__("base64").b64decode(built["zip_base64"], validate=True),
        built["pack_sha256"],
        expected_target_id="workspace",
    )
    assert cold["verified"] is True
    assert cold["manifest"]["planning_readiness"] == readiness
    assert cold["manifest"]["selected_packages"] == reviewed["selected_packages"]

    (project / "apps/client/index.js").write_text(
        "export const packageName = 'changed-after-readiness';\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not bind this exact project source"):
        release_pack.preview(
            project,
            selected,
            target_id="workspace",
            planning_readiness=readiness,
        )
    unknown = {
        **readiness,
        "currentness": "unknown",
        "project_binding": {
            **readiness["project_binding"],
            "currentness": "unknown",
        },
        "qualification": [
            "The saved assessment is retained, but project source currentness is unknown."
        ],
    }
    unknown_pack = release_pack.build(
        project,
        selected,
        target_id="workspace",
        planning_readiness=unknown,
    )
    unknown_reopened = release_pack.verify_pack(
        __import__("base64").b64decode(unknown_pack["zip_base64"], validate=True),
        unknown_pack["pack_sha256"],
        expected_target_id="workspace",
    )
    assert unknown_reopened["manifest"]["planning_readiness"]["currentness"] == "unknown"
