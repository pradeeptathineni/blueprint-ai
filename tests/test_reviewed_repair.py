from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    return subprocess.run(
        [sys.executable, "-m", "blueprint_ai.cli", *args],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_reviewed_repair_cold_plan_confirmation_and_verification(
    python_project: Path, tmp_path: Path
) -> None:
    report_path = tmp_path.parent / f"{tmp_path.name}-review.json"
    plan_path = tmp_path.parent / f"{tmp_path.name}-repair-plan.json"
    reviewed = _cli(
        "review", str(python_project), "--blueprint", "repository", "--model", "off", "--json"
    )
    assert reviewed.returncode == 0, reviewed.stderr
    report_path.write_text(reviewed.stdout)
    report = json.loads(reviewed.stdout)
    finding = next(
        item
        for result in report["results"]
        for item in result["findings"]
        if item["provenance"] == "deterministic"
        and item["source"] == "blueprint-ai"
        and item["disposition"] == "new"
        and item["remediation"]
        and item["remediation"]["safe"]
    )
    prepared = _cli(
        "repair",
        "prepare",
        "--project",
        str(python_project),
        "--report",
        str(report_path),
        "--finding",
        finding["fingerprint"],
        "--output",
        str(plan_path),
    )
    assert prepared.returncode == 0, prepared.stderr
    plan_ref = json.loads(prepared.stdout)
    cold_plan = json.loads(plan_path.read_text())
    assert cold_plan["finding_fingerprint"] == finding["fingerprint"]
    assert plan_ref["proposed_content"] == cold_plan["proposed_content"]
    assert cold_plan["proposed_content"]

    applied = _cli(
        "repair",
        "apply",
        "--project",
        str(python_project),
        "--plan",
        str(plan_path),
        "--confirm-digest",
        plan_ref["plan_sha256"],
    )
    assert applied.returncode == 0, applied.stderr
    receipt = json.loads(applied.stdout)
    assert receipt["finding_fingerprint"] == finding["fingerprint"]
    assert receipt["verification"] == "finding-absent"
    assert len(receipt["changes"]) == 1
    assert receipt["changes"][0]["target"] == cold_plan["target"]
    assert receipt["target_after_sha256"] == receipt["proposed_content_sha256"]
    assert "no independent trial" in receipt["verification_scope"]


def test_reviewed_repair_refuses_stale_source(python_project: Path, tmp_path: Path) -> None:
    report_path = tmp_path.parent / f"{tmp_path.name}-review.json"
    plan_path = tmp_path.parent / f"{tmp_path.name}-repair-plan.json"
    reviewed = _cli(
        "review", str(python_project), "--blueprint", "repository", "--model", "off", "--json"
    )
    assert reviewed.returncode == 0, reviewed.stderr
    report = json.loads(reviewed.stdout)
    report_path.write_text(reviewed.stdout)
    finding = next(
        item
        for result in report["results"]
        for item in result["findings"]
        if item["provenance"] == "deterministic"
        and item["source"] == "blueprint-ai"
        and item["disposition"] == "new"
        and item["remediation"]
        and item["remediation"]["safe"]
    )
    prepared = _cli(
        "repair",
        "prepare",
        "--project",
        str(python_project),
        "--report",
        str(report_path),
        "--finding",
        finding["fingerprint"],
        "--output",
        str(plan_path),
    )
    assert prepared.returncode == 0, prepared.stderr
    digest = json.loads(prepared.stdout)["plan_sha256"]
    (python_project / "README.md").write_text("changed after inspection\n")
    stale = _cli(
        "repair",
        "apply",
        "--project",
        str(python_project),
        "--plan",
        str(plan_path),
        "--confirm-digest",
        digest,
    )
    assert stale.returncode != 0
    assert "stale repair plan" in stale.stderr


# Keep the original review alongside a digest-confirmed plan for recovery trials


def _prepared(root: Path, tmp_path: Path):
    from blueprint_ai.reviewed_repair import prepare_repair

    report = _cli("review", str(root), "--blueprint", "repository", "--model", "off", "--json")
    assert report.returncode == 0, report.stderr
    report_path = tmp_path.parent / f"{tmp_path.name}-inspection.json"
    report_path.write_text(report.stdout)
    finding = next(
        item
        for row in json.loads(report.stdout)["results"]
        for item in row["findings"]
        if item.get("remediation") and item["remediation"]["target"] == ".editorconfig"
    )
    plan = prepare_repair(report_path, finding["fingerprint"], root)
    plan_path = tmp_path.parent / f"{tmp_path.name}-plan.json"
    plan_path.write_text(plan.model_dump_json())
    return plan, plan_path


# Losing verification after the commit must remain recoverable without another write


def test_lost_response_recovers_original_plan_and_preserves_manual_findings(
    python_project: Path, tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from blueprint_ai import reviewed_repair
    from blueprint_ai.remediation import ApplyResult, rollback_operation

    plan, path = _prepared(python_project, tmp_path)
    real_review = reviewed_repair.review
    calls = 0

    def interrupted(context):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected lost verification response")
        return real_review(context)

    monkeypatch.setattr(reviewed_repair, "review", interrupted)
    with pytest.raises(OSError, match="lost verification response"):
        reviewed_repair.apply_repair(path, python_project, plan.plan_sha256)
    recovered = _cli("repair", "status", "--project", str(python_project), "--plan", str(path))
    assert recovered.returncode == 0, recovered.stderr
    receipt = json.loads(recovered.stdout)
    assert receipt["state"] == "applied-content-present"
    assert receipt["verification"] == "not-rerun"
    assert receipt["inspection_report_not_rerun"] == plan.inspection_report
    assert any(
        "LICENSE" in finding["message"]
        for row in receipt["inspection_report_not_rerun"]["results"]
        for finding in row["findings"]
    )
    target = python_project / plan.target
    assert target.read_bytes() == plan.proposed_content.encode()
    target.write_text("later authored edit\n")
    conflict = rollback_operation(python_project, receipt["operation_id"])
    assert isinstance(conflict, ApplyResult)
    assert conflict.changes[0].status == "conflicted"
    assert target.read_text() == "later authored edit\n"
    assert reviewed_repair.repair_status(path, python_project)["state"] == "conflicted-or-absent"
    # Explicitly restore the reviewed bytes before retrying the original rollback
    target.write_bytes(plan.proposed_content.encode())
    restored = rollback_operation(python_project, receipt["operation_id"])
    assert isinstance(restored, ApplyResult)
    assert restored.changes[0].status == "rolled_back"
    assert not target.exists()
    assert reviewed_repair.repair_status(path, python_project)["state"] == "unconfirmed"


# Legacy plans keep their digest but cannot gain invented inspection history


def test_legacy_plan_digest_and_noop_do_not_create_recovery_receipts(
    python_project: Path, tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from blueprint_ai import reviewed_repair
    from blueprint_ai.remediation import ApplyResult

    plan, path = _prepared(python_project, tmp_path)
    plan.inspection_report = None
    plan.plan_sha256 = plan.digest()
    data = plan.model_dump()
    data.pop("inspection_report")
    path.write_text(json.dumps(data))
    reviewed_repair.RepairPlan.model_validate_json(path.read_text()).validate_digest()
    monkeypatch.setattr(reviewed_repair, "apply_findings", lambda *_a, **_k: ApplyResult())
    with pytest.raises(ValueError, match="no applied change"):
        reviewed_repair.apply_repair(path, python_project, plan.plan_sha256)
    assert reviewed_repair.repair_status(path, python_project)["state"] == "unconfirmed"
    assert not (python_project / ".blueprint-ai").exists()


# A matching digest alone must not let a malformed local receipt claim success


def test_recovery_refuses_corrupt_matching_receipt(python_project: Path, tmp_path: Path) -> None:
    import pytest

    from blueprint_ai.reviewed_repair import apply_repair, repair_status

    plan, path = _prepared(python_project, tmp_path)
    applied = apply_repair(path, python_project, plan.plan_sha256)
    manifest = python_project / ".blueprint-ai/operations" / f"{applied['operation_id']}.json"
    data = json.loads(manifest.read_text())
    data["changes"] = [None]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="does not match"):
        repair_status(path, python_project)
    assert (python_project / plan.target).read_bytes() == plan.proposed_content.encode()


# Historical partial coverage stays distinct from the narrow post-repair verification


def test_success_retains_partial_inspection_and_config_digest(python_project: Path, tmp_path: Path):
    from blueprint_ai.reviewed_repair import apply_repair, prepare_repair

    plan, path = _prepared(python_project, tmp_path)
    report = plan.inspection_report
    assert report is not None
    report["results"].append(
        {
            "blueprint": "synthetic-unavailable-check",
            "status": "partial",
            "findings": [],
            "notes": ["Synthetic coverage probe: external checker not run"],
        }
    )
    report_path = tmp_path.parent / f"{tmp_path.name}-partial.json"
    report_path.write_text(json.dumps(report))
    plan = prepare_repair(report_path, plan.finding_fingerprint, python_project)
    path.write_text(plan.model_dump_json())
    receipt = apply_repair(path, python_project, plan.plan_sha256)
    historical = receipt["inspection_report_not_rerun"]
    assert historical["results"][-1]["status"] == "partial"
    assert historical["metadata"]["config_sha256"] == report["metadata"]["config_sha256"]
    assert receipt["verification_report"]["results"][0]["blueprint"] == "repository"
    assert len(receipt["verification_report"]["results"]) == 1
