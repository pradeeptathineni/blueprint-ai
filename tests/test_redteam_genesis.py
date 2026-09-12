"""Adversarial create-only transaction and genesis support regressions."""

import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from blueprint_ai import remediation
from blueprint_ai.capabilities import add_capability, plan_add
from blueprint_ai.core import Finding
from blueprint_ai.core.models import Remediation
from blueprint_ai.discovery import discover_project
from blueprint_ai.genesis import IntentSpec, plan_project
from blueprint_ai.genesis.assets import strengthen
from blueprint_ai.genesis.executor import create_project
from blueprint_ai.remediation import KITS, apply_kit, rollback_operation
from blueprint_ai.safety import MAX_MANIFEST_BYTES, ProcessResult
from blueprint_ai.sandbox import SandboxUnavailable


def test_add_rejects_leaf_symlinks_before_mutation(tmp_path: Path) -> None:
    target = tmp_path / "renovate.json"
    target.symlink_to("user-owned.json")
    assert plan_add(tmp_path, "dependency-updates")["conflicts"] == ["renovate.json"]
    with pytest.raises(ValueError, match="conflicts"):
        add_capability(tmp_path, discover_project(tmp_path), "dependency-updates")
    assert target.is_symlink()
    assert not (tmp_path / "user-owned.json").exists()
    assert not (tmp_path / ".blueprint-ai").exists()


def test_equivalent_existing_configurations_block_all_kit_entry_points(tmp_path: Path) -> None:
    for index, (name, relative, content) in enumerate(
        [
            ("devcontainer", ".devcontainer.json", '{"image":"user-owned"}\n'),
            ("dependency-updates", ".renovaterc.json", '{"extends":["local>owned"]}\n'),
            ("sast", ".semgrep.yaml", "rules: []\n"),
            ("dependency-updates", "package.json", '{"name":"example","renovate":{}}\n'),
        ]
    ):
        root = tmp_path / str(index)
        root.mkdir()
        target = root / relative
        target.write_text(content)
        facts = discover_project(root)
        assert plan_add(root, name)["conflicts"]
        with pytest.raises(ValueError, match="conflicts"):
            add_capability(root, facts, name)
        direct = apply_kit(root, facts, name)
        assert not direct.changed and direct.operation_id is None
        assert all(change.status == "conflicted" for change in direct.changes)
        finding = Finding(
            blueprint="repository",
            category="capability",
            source="blueprint-ai",
            message="Add capability",
            recommendation="Apply kit",
            remediation=Remediation(kind="kit", target=name, safe=True, description="Apply kit"),
        )
        assert not remediation.apply_findings(root, facts, [finding]).changed
        assert target.read_text() == content
        assert list(root.iterdir()) == [target]


def test_rollback_preserves_leaf_symlink_and_its_referent(tmp_path: Path) -> None:
    result = add_capability(tmp_path, discover_project(tmp_path), "dependency-updates")
    assert result.operation_id and result.manifest_path
    target = tmp_path / "renovate.json"
    target.rename(tmp_path / "user-owned.json")
    target.symlink_to("user-owned.json")
    before = (tmp_path / "user-owned.json").read_bytes()
    rolled = rollback_operation(tmp_path, result.operation_id)
    assert rolled.changes[0].status == "conflicted"
    assert (tmp_path / "user-owned.json").read_bytes() == before
    assert target.is_symlink()
    assert (tmp_path / result.manifest_path).is_file()


def test_write_failure_preserves_concurrent_user_edit(tmp_path: Path, monkeypatch) -> None:
    create = remediation._atomic_create

    def conflicting_create(root, target, content):
        if target.name == "CONTRIBUTING.md":
            (root / "SECURITY.md").write_text("Concurrent user edit\n")
            raise FileExistsError(target)
        return create(root, target, content)

    monkeypatch.setattr(remediation, "_atomic_create", conflicting_create)
    result = apply_kit(tmp_path, discover_project(tmp_path), "oss-repository")
    assert not result.changed
    assert all(change.status == "conflicted" for change in result.changes)
    assert (tmp_path / "SECURITY.md").read_text() == "Concurrent user edit\n"
    assert not (tmp_path / "CONTRIBUTING.md").exists()
    assert result.operation_id is None


def test_rollback_bounds_changed_file_reads(tmp_path: Path) -> None:
    result = add_capability(tmp_path, discover_project(tmp_path), "dependency-updates")
    assert result.operation_id
    target = tmp_path / "renovate.json"
    target.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1))
    rolled = rollback_operation(tmp_path, result.operation_id)
    assert rolled.changes[0].status == "conflicted"
    assert target.stat().st_size == MAX_MANIFEST_BYTES + 1


def test_malformed_receipt_is_rejected_before_any_rollback(tmp_path: Path) -> None:
    result = add_capability(tmp_path, discover_project(tmp_path), "oss-repository")
    assert result.operation_id and result.manifest_path
    manifest = tmp_path / result.manifest_path
    data = json.loads(manifest.read_text())
    # Reversal visits a valid row first; eager validation must stop all mutation.
    data["changes"].insert(0, None)
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid operation manifest"):
        rollback_operation(tmp_path, result.operation_id)
    assert (tmp_path / "SECURITY.md").is_file()
    assert (tmp_path / "CONTRIBUTING.md").is_file()
    data["version"] = []
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity or version"):
        rollback_operation(tmp_path, result.operation_id)


@pytest.mark.parametrize("timed_out,truncated", [(True, False), (False, True)])
def test_kit_incomplete_evidence_cannot_pass(tmp_path: Path, monkeypatch, timed_out, truncated):
    (tmp_path / "package.json").write_text('{"name":"example"}')
    (tmp_path / "index.js").write_text("export const value = 1;\n")
    monkeypatch.setattr(
        "blueprint_ai.sandbox.execute",
        lambda *args, **kwargs: SimpleNamespace(
            result=ProcessResult(0, "partial output", "", timed_out, truncated),
            evidence=SimpleNamespace(model_dump=lambda **kwargs: {}),
        ),
    )
    result = apply_kit(tmp_path, discover_project(tmp_path), "testing-javascript")
    assert result.verifications[-1].status == "failed"
    assert result.changes[0].status == "rolled_back"
    assert not (tmp_path / "tests").exists()


def test_all_kits_create_conflict_idempotence_and_rollback(tmp_path: Path, monkeypatch) -> None:
    def missing(*args, **kwargs):
        raise SandboxUnavailable("controlled unavailable verifier")

    monkeypatch.setattr("blueprint_ai.sandbox.execute", missing)
    for name, kit in KITS.items():
        root = tmp_path / name
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname="example"\nversion="1.0"\n')
        (root / "package.json").write_text('{"name":"example"}')
        (root / "index.js").write_text("export const value = 1;\n")
        (root / "example.py").write_text("VALUE = 1\n")
        facts = discover_project(root)
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        assert not plan_add(root, name)["conflicts"]
        result = add_capability(root, facts, name)
        assert len(result.changed) == len(kit.files), result
        assert result.verifications[0].status == "passed"
        assert all(v.status == "tool_error" for v in result.verifications[1:])
        second = add_capability(root, facts, name)
        assert not second.changed
        assert second.operation_id is None
        assert all(row["status"] == "already-present" for row in plan_add(root, name)["files"])
        assert result.operation_id
        rollback_operation(root, result.operation_id)
        after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        assert after == before
        first = root / next(iter(kit.files))
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("User-owned configuration\n")
        with pytest.raises(ValueError, match="conflicts"):
            add_capability(root, facts, name)
        assert first.read_text() == "User-owned configuration\n"
        assert not (root / ".blueprint-ai").exists()


def test_namespace_names_are_validated_before_generation() -> None:
    for kind in ("kubernetes", "kustomize"):
        plan = plan_project(IntentSpec(name="a" * 63, kind=kind))
        assert plan.identity.repository == "a" * 63
        with pytest.raises(ValueError, match="at most 63"):
            plan_project(IntentSpec(name="a" * 64, kind=kind))


def test_native_ci_preserves_complex_verifier_arguments(tmp_path: Path) -> None:
    plan = plan_project(IntentSpec(name="Sample Service", kind="django", ci=True))
    (tmp_path / "project_config").mkdir()
    (tmp_path / "project_config/settings.py").write_text("DEBUG = True\n")
    strengthen(tmp_path, plan)
    workflow = yaml.safe_load((tmp_path / ".github/workflows/ci.yml").read_text())
    commands = [
        shlex.split(step["run"]) for step in workflow["jobs"]["verify"]["steps"] if "run" in step
    ][1:]  # The first run installs the pinned uv toolchain.
    assert commands == [
        op.command for op in plan.operations if op.command and op.action != "initialize"
    ]
    assert commands[-1][commands[-1].index("python") + 1] == "-I"


def test_scaffolding_refuses_unsafe_or_malformed_manifest(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"type":"module"}\n')
    for label in ("symlink", "array"):
        root = tmp_path / label
        root.mkdir()
        (root / "index.js").write_text("export const value = 1;\n")
        facts = discover_project(root)
        manifest = root / "package.json"
        if label == "symlink":
            manifest.symlink_to(outside)
        else:
            manifest.write_text("[]\n")
        finding = Finding(
            blueprint="testing",
            category="missing-test",
            source="blueprint-ai",
            message="Add smoke test",
            recommendation="Add smoke test",
            remediation=Remediation(
                kind="test-scaffold",
                target="tests/test_smoke.js",
                safe=True,
                description="Scaffold",
            ),
        )
        result = remediation.apply_findings(root, facts, [finding])
        assert not result.changed and result.operation_id is None
        assert result.changes[0].status == "conflicted"
        assert not (root / "tests").exists()
    assert outside.read_text() == '{"type":"module"}\n'


def test_oversized_generated_source_fails_without_publication(tmp_path: Path, monkeypatch) -> None:
    from blueprint_ai.genesis import executor

    def oversized(stage, plan):
        strengthen(stage, plan)
        with (stage / "oversized.py").open("wb") as handle:
            handle.truncate(MAX_MANIFEST_BYTES + 1)

    monkeypatch.setattr(executor, "strengthen", oversized)
    destination = tmp_path / "project"
    result = create_project(destination, plan_project(IntentSpec(name="Example")))
    assert result.status == "failed"
    assert "generated source exceeds" in result.detail
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


def test_inventory_bounds_source_growth_after_preflight(tmp_path: Path, monkeypatch) -> None:
    from blueprint_ai.genesis import executor

    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n")
    original = executor.iter_project_files

    def grow_after_listing(root, **kwargs):
        listed = original(root, **kwargs)
        with source.open("wb") as handle:
            handle.truncate(MAX_MANIFEST_BYTES + 1)
        return listed

    monkeypatch.setattr(executor, "iter_project_files", grow_after_listing)
    with pytest.raises(ValueError, match="safety limit"):
        executor._inventory(tmp_path)
