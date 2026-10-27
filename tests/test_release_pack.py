from __future__ import annotations

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
        release_pack.verify_pack(
            data, legacy["pack_sha256"], expected_target_id="fixture"
        )
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
