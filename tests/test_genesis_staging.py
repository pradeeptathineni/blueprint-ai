from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from blueprint_ai.genesis.executor import (
    create_project,
    inspect_published_project,
    inspect_staged_project,
    publish_staged_project,
)
from blueprint_ai.genesis.models import IntentSpec
from blueprint_ai.genesis.plan import plan_project
from blueprint_ai.sandbox import SandboxPolicy


def test_go_cli_stage_failure_recovery_explicit_publish_and_cold_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_go = shutil.which("go")
    if not real_go:
        pytest.skip("the focused no-dependency Go starter proof needs local Go")

    destination = tmp_path / "published" / "hello"
    destination.parent.mkdir()
    stages = tmp_path / "private" / "stages"
    stages.mkdir(parents=True)
    intent = IntentSpec(name="Hello", kind="go-cli")
    plan = plan_project(intent)
    policy = SandboxPolicy(backend="host", trusted=True, writable=True)
    failed_id = str(uuid.uuid4())
    failed_stage = stages / failed_id
    shim_dir = tmp_path / "provider-shim"
    shim_dir.mkdir()
    shim = shim_dir / "go"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if sys.argv[1:2] == ['test']:\n"
        "    print('synthetic verifier failure', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        f"os.execv({real_go!r}, [{real_go!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{Path(real_go).parent}")

    failed = create_project(
        destination,
        plan,
        allow_network=False,
        trust_providers=True,
        sandbox=policy,
        staging_path=failed_stage,
        stage_id=failed_id,
    )
    assert failed.status == "failed"
    assert "staged output rolled back" in failed.detail
    assert failed_stage.is_dir()
    assert (failed_stage / "main.go").is_file()
    assert not destination.exists()
    failed_readback = inspect_staged_project(
        failed_stage,
        destination,
        expected_plan_sha256=plan.digest(),
        expected_stage_id=failed_id,
    )
    assert failed_readback["state"] == "validation-failed"
    assert failed_readback["result"]["status"] == "failed"

    monkeypatch.setenv("PATH", str(Path(real_go).parent))
    stage_id = str(uuid.uuid4())
    stage = stages / stage_id
    generated = create_project(
        destination,
        plan,
        allow_network=False,
        trust_providers=True,
        sandbox=policy,
        staging_path=stage,
        stage_id=stage_id,
    )
    assert generated.status == "verified", generated.detail
    assert not destination.exists()
    assert (stage / "main.go").is_file()
    assert (stage / "greeting_test.go").is_file()
    validated = inspect_staged_project(
        stage,
        destination,
        expected_plan_sha256=plan.digest(),
        expected_stage_id=stage_id,
    )
    assert validated["state"] == "validated"

    readme = stage / "README.md"
    original = readme.read_text(encoding="utf-8")
    readme.write_text(original + "\nchanged after validation\n", encoding="utf-8")
    with pytest.raises(ValueError, match="staged files changed"):
        publish_staged_project(
            stage,
            destination,
            expected_plan_sha256=plan.digest(),
            expected_stage_id=stage_id,
        )
    assert stage.is_dir()
    assert not destination.exists()
    readme.write_text(original, encoding="utf-8")

    published = publish_staged_project(
        stage,
        destination,
        expected_plan_sha256=plan.digest(),
        expected_stage_id=stage_id,
    )
    assert published["state"] == "published"
    assert destination.is_dir()
    assert not stage.exists()
    assert (
        inspect_published_project(
            destination,
            expected_plan_sha256=plan.digest(),
            expected_stage_id=stage_id,
        )["state"]
        == "published"
    )
    assert (destination / "main.go").is_file()
    assert (destination / "go.mod").is_file()
