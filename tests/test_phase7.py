from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from blueprint_ai.discovery import discover_project
from blueprint_ai.evolution import (
    TRANSFORMATIONS,
    apply_evolution,
    catalog_data,
    load_plan,
    plan_evolution,
    project_fingerprint,
    rollback_evolution,
)
from blueprint_ai.evolution.catalog import OFFICIAL_ACTION_SHAS
from blueprint_ai.evolution.models import EvolutionPlan, EvolutionReport, TransformationSpec


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, files: dict[str, str], branch: str = "phase7") -> Path:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(tmp_path, "init", "-b", branch)
    _git(tmp_path, "config", "user.email", "tests@blueprint.invalid")
    _git(tmp_path, "config", "user.name", "Blueprint Tests")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "fixture")
    return tmp_path


def test_catalog_is_canonical_complete_and_honest() -> None:
    data = catalog_data()
    assert data["schema_version"] == "2.0.0"
    assert len(data["transformations"]) == 18
    assert set(data["transformations"]) == set(TRANSFORMATIONS)
    supported = [row for row in data["transformations"].values() if row["maturity"] == "supported"]
    assert len(supported) == 8
    assert all(row["implementation"] != "manual" for row in supported)
    assert all(row["dry_run"] and row["reversible"] for row in supported)
    assert all(row["authoritative_source"].startswith("https://") for row in supported)
    assert all(not row["model_allowed"] and not row["agent_allowed"] for row in supported)
    assert data["transformations"]["iac/terraform-to-opentofu"]["maturity"] == "deferred"
    assert data["transformations"]["dotnet/complex-modernization-agent"]["agent_allowed"]


def test_public_evolution_schemas_are_valid_draft_2020_12() -> None:
    for contract in (EvolutionPlan, EvolutionReport, TransformationSpec):
        Draft202012Validator.check_schema(contract.model_json_schema())


def test_inspection_plan_detects_candidates_without_mutation(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"Dockerfile": "FROM scratch\nMAINTAINER owner@example.com\n"})
    before = project_fingerprint(root)
    plan = plan_evolution(root)
    assert plan.status == "inspection" and not plan.steps
    assert plan.candidates == ["container/maintainer-to-oci-label"]
    assert plan.current_state.project_fingerprint == before
    assert plan.plan_sha256 == plan.digest()
    assert project_fingerprint(root) == before


def test_docker_dry_run_apply_idempotency_and_exact_rollback(tmp_path: Path) -> None:
    original = "FROM scratch\n# owner metadata\nMAINTAINER Test Owner <owner@example.com>\n"
    root = _repository(tmp_path, {"Dockerfile": original})
    plan = plan_evolution(root, ["container/maintainer-to-oci-label"])
    assert plan.status == "ready" and plan.steps[0].files == ["Dockerfile"]
    before = project_fingerprint(root)

    preview = apply_evolution(root, plan, dry_run=True, run_blueprint_review=False)
    assert preview.status == "dry_run"
    assert "org.opencontainers.image.authors" in preview.diffs["Dockerfile"]
    assert project_fingerprint(root) == before

    result = apply_evolution(root, plan, run_blueprint_review=False)
    assert result.status == "verified" and result.operation_id
    assert result.before == plan.current_state and result.after
    transformed = (root / "Dockerfile").read_text()
    assert "MAINTAINER" not in transformed
    assert 'LABEL org.opencontainers.image.authors="Test Owner <owner@example.com>"' in transformed
    assert all(item.status == "passed" for item in result.verifications)
    assert result.metrics["model_calls"] == result.metrics["agent_calls"] == 0
    assert result.rollback_command[-1] == "." and str(root) not in result.model_dump_json()

    second = plan_evolution(root, ["container/maintainer-to-oci-label"])
    assert second.status == "noop" and second.steps[0].status == "noop"
    rolled_back = rollback_evolution(root, result.operation_id)
    assert rolled_back.status == "rolled_back"
    assert (root / "Dockerfile").read_text() == original
    assert project_fingerprint(root) == before

    repeated = apply_evolution(
        root,
        plan_evolution(root, ["container/maintainer-to-oci-label"]),
        run_blueprint_review=False,
    )
    assert repeated.changes[0].after_sha256 == result.changes[0].after_sha256


def test_docker_conflict_and_multiline_are_rejected_without_mutation(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "Dockerfile": (
                "FROM scratch\n"
                'LABEL org.opencontainers.image.authors="current"\n'
                "MAINTAINER legacy@example.com\n"
            )
        },
    )
    plan = plan_evolution(root, ["container/maintainer-to-oci-label"])
    before = project_fingerprint(root)
    with pytest.raises(ValueError, match="existing .* conflicts"):
        apply_evolution(root, plan, run_blueprint_review=False)
    assert project_fingerprint(root) == before
    assert not (root / ".blueprint-ai").exists()


def test_github_action_pinning_preserves_major_and_nonmatches(tmp_path: Path) -> None:
    workflow = """name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: owner/custom@v1
      - uses: "actions/setup-python@v6"
      - run: echo actions/setup-node@v4
"""
    root = _repository(tmp_path, {".github/workflows/ci.yml": workflow})
    plan = plan_evolution(root, ["github-actions/pin-official-actions"])
    result = apply_evolution(root, plan, run_blueprint_review=False)
    text = (root / ".github/workflows/ci.yml").read_text()
    assert f"actions/checkout@{OFFICIAL_ACTION_SHAS['actions/checkout@v4']} # v4" in text
    assert "owner/custom@v1" in text
    assert 'uses: "actions/setup-python@v6"' in text
    assert "echo actions/setup-node@v4" in text
    assert result.status == "verified"


def test_malformed_workflow_fails_transactionally(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n    steps: []\n"
            )
        },
    )
    plan = plan_evolution(root, ["github-actions/pin-official-actions"])
    before = project_fingerprint(root)
    with pytest.raises(ValueError, match="duplicate"):
        apply_evolution(root, plan, dry_run=True, run_blueprint_review=False)
    assert project_fingerprint(root) == before


def test_later_structural_step_failure_restores_earlier_change(tmp_path: Path) -> None:
    dockerfile = "FROM scratch\nMAINTAINER owner\n"
    workflow = "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n    steps: []\n"
    root = _repository(
        tmp_path,
        {"Dockerfile": dockerfile, ".github/workflows/ci.yml": workflow},
    )
    plan = plan_evolution(
        root,
        ["container/maintainer-to-oci-label", "github-actions/pin-official-actions"],
    )
    before = project_fingerprint(root)
    with pytest.raises(ValueError, match="duplicate"):
        apply_evolution(root, plan, run_blueprint_review=False)
    assert project_fingerprint(root) == before
    assert (root / "Dockerfile").read_text() == dockerfile
    assert not (root / ".blueprint-ai").exists()


def test_native_tool_failure_after_writing_rolls_back_without_path_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blueprint_ai.evolution import engine
    from blueprint_ai.safety import ProcessResult
    from blueprint_ai.sandbox import Execution, SandboxEvidence, SandboxPolicy

    source = "from typing import List\nvalue: List[int] = []\n"
    root = _repository(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.12"\n'
            ),
            "src/demo.py": source,
        },
    )
    plan = plan_evolution(root, ["python/ruff-pyupgrade"])
    before = project_fingerprint(root)

    def fake_execute(command, execution_root, policy, **_kwargs):
        evidence = SandboxEvidence(backend="host", tool="ruff", policy=policy, teardown=True)
        if command[-1] == "--version":
            result = ProcessResult(0, "ruff 0.16.7\n", "", False, False)
        elif "--select" in command and command[command.index("--select") + 1] == "F401,I001":
            result = ProcessResult(0, "All checks passed!\n", "", False, False)
        elif "--diff" in command:
            result = ProcessResult(
                1,
                f"--- {execution_root}/src/demo.py\n+++ {execution_root}/src/demo.py\n",
                "",
                False,
                False,
            )
        else:
            (execution_root / "src/demo.py").write_text("value: list[int] = []\n")
            result = ProcessResult(
                2,
                "",
                f"failed while processing {execution_root}/src/demo.py",
                False,
                False,
            )
        evidence.exit_code = result.returncode
        return Execution(result, evidence)

    monkeypatch.setattr(engine, "execute", fake_execute)
    monkeypatch.setattr(
        engine,
        "_tool_command",
        lambda _root, tool, _policy, requested=None: requested or tool,
    )
    with pytest.raises(RuntimeError) as raised:
        apply_evolution(
            root,
            plan,
            policy=SandboxPolicy(backend="host", trusted=True, writable=True),
            run_blueprint_review=False,
        )
    assert str(root) not in str(raised.value)
    assert project_fingerprint(root) == before
    assert (root / "src/demo.py").read_text() == source
    assert not (root / ".blueprint-ai").exists()


def test_native_tool_staging_contains_ignored_writes(tmp_path: Path, monkeypatch) -> None:
    from blueprint_ai.evolution import engine
    from blueprint_ai.safety import ProcessResult
    from blueprint_ai.sandbox import Execution, SandboxEvidence, SandboxPolicy

    source = "from typing import List\nvalue: List[int] = []\n"
    root = _repository(
        tmp_path,
        {
            ".gitignore": "build/\n",
            "pyproject.toml": (
                '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.12"\n'
            ),
            "src/demo.py": source,
        },
    )
    plan = plan_evolution(root, ["python/ruff-pyupgrade"])

    def fake_execute(command, execution_root, policy, **_kwargs):
        evidence = SandboxEvidence(backend="host", tool="ruff", policy=policy, teardown=True)
        if command[-1] == "--version":
            result = ProcessResult(0, "ruff 0.16.7\n", "", False, False)
        elif "--select" in command and command[command.index("--select") + 1] == "F401,I001":
            result = ProcessResult(0, "All checks passed!\n", "", False, False)
        elif "--diff" in command:
            result = ProcessResult(1, "--- src/demo.py\n+++ src/demo.py\n", "", False, False)
        else:
            (execution_root / "src/demo.py").write_text("value: list[int] = []\n")
            leak = execution_root / "build/leak.txt"
            leak.parent.mkdir()
            leak.write_text("tool output\n")
            result = ProcessResult(0, "fixed\n", "", False, False)
        evidence.exit_code = result.returncode
        return Execution(result, evidence)

    monkeypatch.setattr(engine, "execute", fake_execute)
    monkeypatch.setattr(
        engine,
        "_tool_command",
        lambda _root, tool, _policy, requested=None: requested or tool,
    )
    with pytest.raises(RuntimeError, match="crossed its planned scope"):
        apply_evolution(
            root,
            plan,
            policy=SandboxPolicy(backend="host", trusted=True, writable=True),
            run_blueprint_review=False,
        )
    assert (root / "src/demo.py").read_text() == source
    assert not (root / "build").exists()


def test_stale_and_tampered_plans_cannot_apply(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"Dockerfile": "FROM scratch\nMAINTAINER owner\n"})
    plan = plan_evolution(root, ["container/maintainer-to-oci-label"])
    path = tmp_path.parent / "plan.json"
    path.write_text(plan.model_dump_json())
    assert load_plan(path) == plan
    payload = json.loads(path.read_text())
    payload["desired_state"] = ["attacker state"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        load_plan(path)

    forged = plan.model_copy(update={"desired_state": ["attacker state"]}).seal()
    with pytest.raises(ValueError, match="canonical catalog"):
        apply_evolution(root, forged, run_blueprint_review=False)

    (root / "Dockerfile").write_text("FROM scratch\nMAINTAINER changed\n")
    with pytest.raises(ValueError, match="stale"):
        apply_evolution(root, plan, allow_dirty=True, run_blueprint_review=False)


def test_main_and_dirty_guards_require_explicit_flags(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {"Dockerfile": "FROM scratch\nMAINTAINER owner\n"},
        branch="main",
    )
    plan = plan_evolution(root, ["container/maintainer-to-oci-label"])
    with pytest.raises(ValueError, match="refusing to mutate main"):
        apply_evolution(root, plan, run_blueprint_review=False)
    result = apply_evolution(root, plan, allow_main=True, run_blueprint_review=False)
    rollback_evolution(root, result.operation_id or "")

    (root / "notes.txt").write_text("understood dirty state\n")
    dirty = plan_evolution(root, ["container/maintainer-to-oci-label"])
    with pytest.raises(ValueError, match="working tree is dirty"):
        apply_evolution(root, dirty, allow_main=True, run_blueprint_review=False)
    result = apply_evolution(
        root,
        dirty,
        allow_main=True,
        allow_dirty=True,
        run_blueprint_review=False,
    )
    rollback_evolution(root, result.operation_id or "")
    assert (root / "notes.txt").read_text() == "understood dirty state\n"


def test_rollback_refuses_post_apply_edits_atomically(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "Dockerfile": "FROM scratch\nMAINTAINER owner\n",
            ".github/workflows/ci.yml": (
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
            ),
        },
    )
    plan = plan_evolution(
        root,
        ["container/maintainer-to-oci-label", "github-actions/pin-official-actions"],
    )
    result = apply_evolution(root, plan, run_blueprint_review=False)
    docker_after = (root / "Dockerfile").read_text()
    workflow = root / ".github/workflows/ci.yml"
    workflow.write_text(workflow.read_text() + "# user edit\n")
    with pytest.raises(ValueError, match="post-apply files"):
        rollback_evolution(root, result.operation_id or "")
    assert (root / "Dockerfile").read_text() == docker_after


def test_rollback_refuses_changed_git_index(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"Dockerfile": "FROM scratch\nMAINTAINER owner\n"})
    result = apply_evolution(
        root,
        plan_evolution(root, ["container/maintainer-to-oci-label"]),
        run_blueprint_review=False,
    )
    after = (root / "Dockerfile").read_text()
    _git(root, "add", "Dockerfile")
    with pytest.raises(ValueError, match="Git index"):
        rollback_evolution(root, result.operation_id or "")
    assert (root / "Dockerfile").read_text() == after


def test_rollback_preflights_every_manifest_path_before_mutation(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "Dockerfile": "FROM scratch\nMAINTAINER owner\n",
            ".github/workflows/ci.yml": (
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
            ),
        },
    )
    result = apply_evolution(
        root,
        plan_evolution(
            root,
            ["container/maintainer-to-oci-label", "github-actions/pin-official-actions"],
        ),
        run_blueprint_review=False,
    )
    docker_after = (root / "Dockerfile").read_text()
    manifest = root / (result.manifest_path or "")
    payload = json.loads(manifest.read_text())
    payload["changes"][1]["target"] = "../outside"
    content = json.dumps(payload)
    manifest.write_text(content)
    git_directory = Path(_git(root, "rev-parse", "--git-dir"))
    if not git_directory.is_absolute():
        git_directory = root / git_directory
    seal = (
        git_directory / "blueprint-ai/operations" / (result.operation_id or "") / "receipt.sha256"
    )
    seal.write_text(hashlib.sha256(content.encode()).hexdigest() + "\n")
    with pytest.raises(ValueError, match="unsafe evolution path"):
        rollback_evolution(root, result.operation_id or "")
    assert (root / "Dockerfile").read_text() == docker_after


def test_python_upgrade_requires_an_explicit_supported_lower_bound(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "demo"\nversion = "1"\nrequires-python = "<3.12"\n'
            ),
            "demo.py": "from typing import List\nvalue: List[int] = []\n",
        },
    )
    plan = plan_evolution(root, ["python/ruff-pyupgrade"])
    assert plan.status == "noop" and plan.steps[0].status == "noop"


def test_python_syntax_fixture_is_clean_idempotent_and_exactly_reversible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blueprint_ai.evolution import engine
    from blueprint_ai.sandbox import SandboxPolicy

    ruff = Path(sys.executable).with_name("ruff")
    if not ruff.is_file():
        pytest.skip("Ruff development executable is unavailable")
    which = engine.shutil.which
    monkeypatch.setattr(
        engine.shutil, "which", lambda name: str(ruff) if name == "ruff" else which(name)
    )
    source = (
        "from typing import List, Optional\n\n\n"
        "def first(values: List[int]) -> Optional[int]:\n"
        "    return values[0] if values else None\n"
    )
    root = _repository(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "audit-python-syntax"\nversion = "0.1.0"\n'
                'requires-python = ">=3.12"\n'
            ),
            "src/modernize_me/__init__.py": source,
        },
    )
    before = project_fingerprint(root)
    policy = SandboxPolicy(backend="host", trusted=True, writable=True)
    plan = plan_evolution(root, ["python/ruff-pyupgrade"])

    preview = apply_evolution(
        root,
        plan,
        dry_run=True,
        policy=policy.model_copy(update={"writable": False}),
        run_blueprint_review=False,
    )
    assert preview.status == "dry_run" and project_fingerprint(root) == before
    assert "List as List" in preview.diffs["python/ruff-pyupgrade"]
    assert "list[int]" in preview.diffs["python/ruff-pyupgrade"]

    result = apply_evolution(root, plan, policy=policy)
    assert result.status == "verified"
    assert result.review.introduced_fingerprints == []
    assert result.review.status in {"passed", "partial"}
    transformed = (root / "src/modernize_me/__init__.py").read_text()
    assert "from typing import List as List" in transformed
    assert "from typing import Optional as Optional" in transformed
    assert "def first(values: list[int]) -> int | None:" in transformed

    second = apply_evolution(
        root,
        plan_evolution(root, ["python/ruff-pyupgrade"]),
        allow_dirty=True,
        policy=policy,
    )
    assert second.status == "noop" and not second.changes
    rollback_evolution(root, result.operation_id or "")
    assert (root / "src/modernize_me/__init__.py").read_text() == source
    assert project_fingerprint(root) == before


def test_ruff_composition_refuses_preexisting_import_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blueprint_ai.evolution import engine
    from blueprint_ai.sandbox import SandboxPolicy

    ruff = Path(sys.executable).with_name("ruff")
    if not ruff.is_file():
        pytest.skip("Ruff development executable is unavailable")
    which = engine.shutil.which
    monkeypatch.setattr(
        engine.shutil, "which", lambda name: str(ruff) if name == "ruff" else which(name)
    )
    source = "import os\nfrom typing import List\nvalue: List[int] = []\n"
    root = _repository(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.12"\n'
            ),
            "demo.py": source,
        },
    )
    before = project_fingerprint(root)
    with pytest.raises(RuntimeError, match="pre-existing import cleanup"):
        apply_evolution(
            root,
            plan_evolution(root, ["python/ruff-pyupgrade"]),
            policy=SandboxPolicy(backend="host", trusted=True, writable=True),
            run_blueprint_review=False,
        )
    assert project_fingerprint(root) == before
    assert (root / "demo.py").read_text() == source


def test_manual_recipe_is_inspectable_but_not_executable(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"main.tf": 'resource "null_resource" "example" {}\n'})
    plan = plan_evolution(root, ["iac/terraform-to-opentofu"])
    assert plan.status == "blocked" and plan.steps[0].status == "manual"
    assert "state" in " ".join(plan.manual_boundaries).lower()
    with pytest.raises(ValueError, match="manual/deferred"):
        apply_evolution(root, plan, run_blueprint_review=False)


def test_mixed_ready_and_manual_plan_applies_independent_ready_prefix(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "Dockerfile": "FROM scratch\nMAINTAINER owner\n",
            "main.tf": 'resource "null_resource" "example" {}\n',
        },
    )
    plan = plan_evolution(
        root,
        ["container/maintainer-to-oci-label", "iac/terraform-to-opentofu"],
    )
    assert plan.status == "ready"
    assert [step.status for step in plan.steps] == ["ready", "manual"]
    result = apply_evolution(root, plan, run_blueprint_review=False)
    assert result.status == "partial"
    assert "MAINTAINER" not in (root / "Dockerfile").read_text()
    rollback_evolution(root, result.operation_id or "")


def test_next_framework_identity_drives_only_project_level_applicability(tmp_path: Path) -> None:
    next_root = tmp_path / "next"
    next_root.mkdir()
    _repository(
        next_root,
        {
            "package.json": json.dumps(
                {
                    "dependencies": {
                        "next": "14.2.0",
                        "react": "18.2.0",
                        "react-dom": "18.2.0",
                    }
                }
            ),
            "app/page.tsx": "export default function Page() { return <main />; }\n",
        },
    )
    next_plan = plan_evolution(next_root)
    assert next_plan.current_state.frameworks == ["next", "react"]
    assert "next/official-upgrade-codemod" in next_plan.candidates

    react_root = tmp_path / "react"
    react_root.mkdir()
    _repository(
        react_root,
        {
            "package.json": json.dumps({"dependencies": {"react": "18.2.0"}}),
            "README.md": "A comparison mentions next and Next.js without using either.\n",
            "fixtures/package.json": json.dumps({"dependencies": {"next": "14.2.0"}}),
        },
    )
    react_plan = plan_evolution(react_root)
    assert react_plan.current_state.frameworks == ["react"]
    assert "next/official-upgrade-codemod" not in react_plan.candidates


@pytest.mark.parametrize(
    ("edition", "candidate"),
    [("2018", True), ("2024", False)],
)
def test_rust_candidate_requires_an_exact_older_edition(
    tmp_path: Path, edition: str, candidate: bool
) -> None:
    root = _repository(
        tmp_path,
        {
            "Cargo.toml": (f'[package]\nname = "demo"\nversion = "0.1.0"\nedition = "{edition}"\n'),
            "Cargo.lock": "# This file is automatically @generated by Cargo.\nversion = 4\n",
            "src/lib.rs": "pub fn answer() -> u8 { 42 }\n",
        },
    )
    plan = plan_evolution(root)
    assert plan.current_state.versions["rust-edition"] == edition
    assert ("rust/edition" in plan.candidates) is candidate


@pytest.mark.parametrize(
    ("framework", "candidate"),
    [("net6.0", True), ("net10.0", False), ("netstandard2.0", False)],
)
def test_dotnet_candidate_requires_an_exact_older_target_framework(
    tmp_path: Path, framework: str, candidate: bool
) -> None:
    root = _repository(
        tmp_path,
        {
            "Demo.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                f"  <PropertyGroup><TargetFramework>{framework}</TargetFramework></PropertyGroup>\n"
                "</Project>\n"
            ),
            "Program.cs": 'Console.WriteLine("hello");\n',
        },
    )
    plan = plan_evolution(root)
    assert plan.current_state.versions["dotnet-target-framework"] == framework
    assert ("dotnet/sdk-target" in plan.candidates) is candidate


def test_ambiguous_or_missing_versions_do_not_claim_applicability(tmp_path: Path) -> None:
    rust_root = tmp_path / "rust"
    rust_root.mkdir()
    _repository(
        rust_root,
        {
            "Cargo.toml": '[workspace]\nmembers = ["old", "current"]\n',
            "old/Cargo.toml": ('[package]\nname = "old"\nversion = "0.1.0"\nedition = "2018"\n'),
            "current/Cargo.toml": (
                '[package]\nname = "current"\nversion = "0.1.0"\nedition = "2024"\n'
            ),
            "old/src/lib.rs": "pub fn old() {}\n",
            "current/src/lib.rs": "pub fn current() {}\n",
        },
    )
    rust_plan = plan_evolution(rust_root)
    assert rust_plan.current_state.versions["rust-editions"] == "2018;2024"
    assert "rust/edition" not in rust_plan.candidates

    unresolved_rust_root = tmp_path / "unresolved-rust"
    unresolved_rust_root.mkdir()
    _repository(
        unresolved_rust_root,
        {
            "Cargo.toml": '[workspace]\nmembers = ["member"]\n',
            "member/Cargo.toml": (
                '[package]\nname = "member"\nversion = "0.1.0"\nedition.workspace = true\n'
            ),
            "member/src/lib.rs": "pub fn member() {}\n",
        },
    )
    unresolved_rust_plan = plan_evolution(unresolved_rust_root)
    assert "rust-edition" not in unresolved_rust_plan.current_state.versions
    assert "rust/edition" not in unresolved_rust_plan.candidates

    dotnet_root = tmp_path / "dotnet"
    dotnet_root.mkdir()
    _repository(
        dotnet_root,
        {
            "Demo.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><TargetFrameworks>net6.0;net10.0</TargetFrameworks>"
                "</PropertyGroup>\n</Project>\n"
            ),
            "Program.cs": 'Console.WriteLine("hello");\n',
        },
    )
    dotnet_plan = plan_evolution(dotnet_root)
    assert dotnet_plan.current_state.versions["dotnet-target-frameworks"] == "net6.0;net10.0"
    assert "dotnet/modernization-agent" not in dotnet_plan.candidates

    unresolved_dotnet_root = tmp_path / "unresolved-dotnet"
    unresolved_dotnet_root.mkdir()
    _repository(
        unresolved_dotnet_root,
        {
            "Demo.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><TargetFramework>$(ConfiguredTfm)</TargetFramework>"
                "</PropertyGroup>\n</Project>\n"
            ),
            "Program.cs": 'Console.WriteLine("hello");\n',
        },
    )
    unresolved_dotnet_plan = plan_evolution(unresolved_dotnet_root)
    assert "dotnet-target-framework" not in unresolved_dotnet_plan.current_state.versions
    assert "dotnet/modernization-agent" not in unresolved_dotnet_plan.candidates


def test_plan_and_models_reject_unknown_or_extra_authority(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"Dockerfile": "FROM scratch\n"})
    with pytest.raises(ValueError, match="unknown evolution target"):
        plan_evolution(root, ["shell/curl-pipe-sh"])
    plan = plan_evolution(root)
    payload = plan.model_dump(mode="json")
    payload["execute_arbitrary_command"] = True
    with pytest.raises(ValidationError):
        type(plan).model_validate(payload)


def test_discovery_scope_excludes_fixture_python_from_upgrade(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.12"\n'
            ),
            "src/demo.py": "from typing import List\nvalue: List[int] = []\n",
            "fixtures/legacy.py": "from typing import List\nvalue: List[int] = []\n",
        },
    )
    plan = plan_evolution(root, ["python/ruff-pyupgrade"])
    assert plan.steps[0].files == ["src/demo.py"]
    assert discover_project(root).graph.scope("fixtures/legacy.py") == "fixture"


def test_operation_lock_rejects_concurrent_blueprint_transaction(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"Dockerfile": "FROM scratch\nMAINTAINER owner\n"})
    state = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-dir")) / "blueprint-ai"
    state.mkdir()
    (state / "evolution.lock").write_text("other\n")
    with pytest.raises(ValueError, match="another .* transaction"):
        apply_evolution(
            root,
            plan_evolution(root, ["container/maintainer-to-oci-label"]),
            run_blueprint_review=False,
        )


def test_concurrent_edit_before_publication_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blueprint_ai.evolution import engine

    original = "FROM scratch\nMAINTAINER owner\n"
    root = _repository(tmp_path, {"Dockerfile": original})
    inventory = engine._stage_inventory
    calls = 0

    def race(workspace: Path):
        nonlocal calls
        calls += 1
        result = inventory(workspace)
        if calls == 3:
            (root / "concurrent.txt").write_text("preserve me\n")
        return result

    monkeypatch.setattr(engine, "_stage_inventory", race)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        apply_evolution(
            root,
            plan_evolution(root, ["container/maintainer-to-oci-label"]),
            run_blueprint_review=False,
        )
    assert (root / "Dockerfile").read_text() == original
    assert (root / "concurrent.txt").read_text() == "preserve me\n"


def test_official_action_registry_contains_only_full_sha_same_major_keys() -> None:
    assert OFFICIAL_ACTION_SHAS
    assert all(re.fullmatch(r"[a-f0-9]{40}", value) for value in OFFICIAL_ACTION_SHAS.values())
    assert all(key.startswith("actions/") and "@v" in key for key in OFFICIAL_ACTION_SHAS)


def test_native_commands_do_not_interpret_project_paths_as_options(tmp_path: Path) -> None:
    python = TRANSFORMATIONS["python/ruff-pyupgrade"]
    terraform = TRANSFORMATIONS["terraform/native-format"]
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "safe-paths"\nversion = "1"\nrequires-python = ">=3.12"\n'
    )
    assert python.preview_command is not None
    python_command = python.preview_command(tmp_path, ["--config=attacker.py"])
    assert python_command[-2:] == ["--", "--config=attacker.py"]
    assert terraform.preview_command is not None
    terraform_command = terraform.preview_command(tmp_path, ["-state.tf"])
    assert terraform_command[-1] == "./-state.tf"


def test_tool_contract_resolves_divergent_host_and_container_executables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blueprint_ai.evolution import engine
    from blueprint_ai.sandbox import SandboxPolicy
    from blueprint_ai.support import TOOLS, backend_executable

    monkeypatch.setattr(
        engine.shutil, "which", lambda name: "/trusted/ruff" if name == "ruff" else None
    )
    assert (
        engine._tool_command(tmp_path, "ruff", SandboxPolicy(backend="host", trusted=True))
        == "/trusted/ruff"
    )
    assert (
        engine._tool_command(
            tmp_path,
            "ruff",
            SandboxPolicy(backend="docker", image="ghcr.io/astral-sh/ruff:0.16.7", trusted=True),
        )
        == "/ruff"
    )
    assert (
        engine._tool_command(
            tmp_path,
            "ruff",
            SandboxPolicy(backend="docker", image="example.invalid/custom-ruff", trusted=True),
        )
        == "/ruff"
    )
    assert backend_executable("go", "go", "docker") == "go"
    assert backend_executable("terraform", "terraform", "docker") == "terraform"

    monkeypatch.setattr(TOOLS["ruff"], "container_executable", "../ruff")
    with pytest.raises(ValueError, match="invalid docker executable"):
        backend_executable("ruff", "ruff", "docker")
    with pytest.raises(ValueError, match="invalid docker executable"):
        backend_executable("missing", "", "docker")


def test_evolution_verification_reports_executable_actually_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blueprint_ai.evolution import engine
    from blueprint_ai.safety import ProcessResult
    from blueprint_ai.sandbox import Execution, SandboxEvidence, SandboxPolicy

    policy = SandboxPolicy(backend="docker", image="ghcr.io/astral-sh/ruff:0.16.7", trusted=True)

    def fake_execute(command, _root, actual_policy, **_kwargs):
        evidence = SandboxEvidence(
            backend="docker",
            tool="ruff",
            policy=actual_policy,
            exit_code=0,
            teardown=True,
        )
        return Execution(ProcessResult(0, "ruff 0.16.7\n", "", False, False), evidence)

    monkeypatch.setattr(engine, "execute", fake_execute)
    verification, _output = engine._run(tmp_path, ["ruff", "--version"], policy, {}, tool="ruff")
    assert verification.command == ["/ruff", "--version"]
    assert verification.sandbox and verification.sandbox["backend"] == "docker"
