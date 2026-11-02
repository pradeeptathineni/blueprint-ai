from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    return subprocess.run(
        [sys.executable, "-m", "blueprint_ai.cli", *args],
        cwd=root,
        env={"PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_reviewed_repair_cold_plan_confirmation_and_verification(python_project: Path, tmp_path: Path) -> None:
    report_path = tmp_path / "review.json"
    plan_path = tmp_path / "repair-plan.json"
    reviewed = _cli("review", str(python_project), "--blueprint", "repository", "--model", "off", "--json")
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
        "repair", "prepare", "--project", str(python_project), "--report", str(report_path),
        "--finding", finding["fingerprint"], "--output", str(plan_path),
    )
    assert prepared.returncode == 0, prepared.stderr
    plan_ref = json.loads(prepared.stdout)
    cold_plan = json.loads(plan_path.read_text())
    assert cold_plan["finding_fingerprint"] == finding["fingerprint"]

    applied = _cli(
        "repair", "apply", "--project", str(python_project), "--plan", str(plan_path),
        "--confirm-digest", plan_ref["plan_sha256"],
    )
    assert applied.returncode == 0, applied.stderr
    receipt = json.loads(applied.stdout)
    assert receipt["finding_fingerprint"] == finding["fingerprint"]
    assert receipt["verification"] == "finding-absent"
    assert len(receipt["changes"]) == 1
    assert "no independent trial" in receipt["verification_scope"]


def test_reviewed_repair_refuses_stale_source(python_project: Path, tmp_path: Path) -> None:
    report_path = tmp_path / "review.json"
    plan_path = tmp_path / "repair-plan.json"
    reviewed = _cli("review", str(python_project), "--blueprint", "repository", "--model", "off", "--json")
    report = json.loads(reviewed.stdout)
    report_path.write_text(reviewed.stdout)
    finding = next(
        item
        for result in report["results"]
        for item in result["findings"]
        if item["provenance"] == "deterministic" and item["source"] == "blueprint-ai"
        and item["disposition"] == "new" and item["remediation"] and item["remediation"]["safe"]
    )
    prepared = _cli(
        "repair", "prepare", "--project", str(python_project), "--report", str(report_path),
        "--finding", finding["fingerprint"], "--output", str(plan_path),
    )
    assert prepared.returncode == 0, prepared.stderr
    digest = json.loads(prepared.stdout)["plan_sha256"]
    (python_project / "README.md").write_text("changed after inspection\n")
    stale = _cli(
        "repair", "apply", "--project", str(python_project), "--plan", str(plan_path),
        "--confirm-digest", digest,
    )
    assert stale.returncode != 0
    assert "stale repair plan" in stale.stderr
