"""Single-finding, explicit reviewed repair workflow.

This is a narrow receiving path over the existing review and remediation engines. It
does not treat comparison evidence as authorization or as proof of real-world quality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from blueprint_ai.core import Finding, RunReport
from blueprint_ai.discovery import project_source_snapshot
from blueprint_ai.engine import make_context, review
from blueprint_ai.remediation import apply_findings
from blueprint_ai.safety import read_bytes_bounded

FORMAT = "blueprint-ai/reviewed-repair-plan/v1"
MAX_PLAN_BYTES = 2_000_000
MAX_TARGET_BYTES = 8_000_000


class RepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = FORMAT
    project: str
    source_snapshot: str
    finding_fingerprint: str
    finding: dict[str, Any]
    target: str
    target_sha256: str | None
    plan_sha256: str = ""

    def digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"plan_sha256"})
        return hashlib.sha256(_canonical(payload)).hexdigest()

    def validate_digest(self) -> None:
        if self.format != FORMAT or self.digest() != self.plan_sha256:
            raise ValueError("invalid or modified reviewed repair plan")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _target_bytes(root: Path, target: str) -> bytes | None:
    path = (root / target).resolve()
    if path != root and root not in path.parents:
        raise ValueError("repair target escapes project root")
    unresolved = root / target
    if unresolved.is_symlink():
        raise ValueError("repair target is a symlink")
    if not unresolved.exists():
        return None
    if not unresolved.is_file():
        raise ValueError("repair target is not a regular file")
    return read_bytes_bounded(unresolved, MAX_TARGET_BYTES, root=root)


def _load_inspection_report(raw: bytes) -> RunReport:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("invalid inspection report")
    for result in payload["results"]:
        if not isinstance(result, dict) or not isinstance(result.get("findings", []), list):
            raise ValueError("invalid inspection report findings")
        for row in result.get("findings", []):
            if not isinstance(row, dict):
                raise ValueError("invalid inspection finding")
            expected = row.pop("fingerprint", None)
            parsed = Finding.model_validate(row)
            if expected != parsed.fingerprint:
                raise ValueError("inspection finding fingerprint mismatch")
    return RunReport.model_validate(payload)


def _current_finding(root: Path, finding: Finding) -> tuple[RunReport, Finding]:
    context, _ = make_context(
        root,
        blueprints=[finding.blueprint],
        model_mode="off",
        run_external_tools=False,
    )
    report = review(context)
    matches = [item for item in report.findings if item.fingerprint == finding.fingerprint]
    if len(matches) != 1 or matches[0].model_dump(mode="json") != finding.model_dump(mode="json"):
        raise ValueError("stale inspected finding: current deterministic evidence differs")
    return report, matches[0]


def prepare_repair(report_path: Path, finding_fingerprint: str, root: Path) -> RepairPlan:
    """Prepare a read-only single-finding plan from an inspection report."""
    root = root.expanduser().resolve(strict=True)
    raw = report_path.read_bytes()
    if len(raw) > MAX_PLAN_BYTES:
        raise ValueError("inspection report exceeds size limit")
    report = _load_inspection_report(raw)
    if Path(report.facts.path).expanduser().resolve() != root:
        raise ValueError("inspection report belongs to another project")
    selected = [item for item in report.findings if item.fingerprint == finding_fingerprint]
    if len(selected) != 1:
        raise ValueError("finding fingerprint must identify exactly one finding in the report")
    finding = selected[0]
    if (
        finding.provenance != "deterministic"
        or finding.source != "blueprint-ai"
        or finding.disposition != "new"
        or finding.remediation is None
        or not finding.remediation.safe
    ):
        raise ValueError(
            "only active deterministic findings with a declared safe remediation qualify"
        )
    current, exact = _current_finding(root, finding)
    snapshot = project_source_snapshot(root, current.facts)
    if not snapshot["complete"]:
        raise ValueError("project source snapshot is incomplete; repair plan refused")
    target_bytes = _target_bytes(root, finding.remediation.target)
    plan = RepairPlan(
        project=str(root),
        source_snapshot=snapshot["source_digest"],
        finding_fingerprint=exact.fingerprint,
        finding=exact.model_dump(mode="json", exclude={"fingerprint"}),
        target=exact.remediation.target,
        target_sha256=_sha(target_bytes),
    )
    plan.plan_sha256 = plan.digest()
    return plan


def apply_repair(plan_path: Path, root: Path, confirm_digest: str) -> dict[str, Any]:
    """Apply exactly one confirmed finding and retain finding-bound verification."""
    root = root.expanduser().resolve(strict=True)
    raw = plan_path.read_bytes()
    if len(raw) > MAX_PLAN_BYTES:
        raise ValueError("repair plan exceeds size limit")
    plan = RepairPlan.model_validate_json(raw)
    plan.validate_digest()
    if confirm_digest != plan.plan_sha256:
        raise ValueError("explicit --confirm-digest does not match the repair plan")
    if Path(plan.project).resolve() != root:
        raise ValueError("repair plan belongs to another project")
    if plan_path.expanduser().resolve() == root or root in plan_path.expanduser().resolve().parents:
        raise ValueError("repair plan must be stored outside the project source tree")
    finding = Finding.model_validate(plan.finding)
    if (
        finding.fingerprint != plan.finding_fingerprint
        or finding.remediation is None
        or finding.remediation.target != plan.target
        or finding.provenance != "deterministic"
        or finding.source != "blueprint-ai"
        or finding.disposition != "new"
        or not finding.remediation.safe
    ):
        raise ValueError("repair plan finding binding is invalid")

    before, exact = _current_finding(root, finding)
    snapshot = project_source_snapshot(root, before.facts)
    if not snapshot["complete"] or snapshot["source_digest"] != plan.source_snapshot:
        raise ValueError("stale repair plan: project source changed after inspection")
    if _sha(_target_bytes(root, plan.target)) != plan.target_sha256:
        raise ValueError("stale repair plan: repair target changed after inspection")

    applied = apply_findings(root, before.facts, [exact])
    if not applied.changed:
        raise ValueError(
            "the selected finding produced no applied change; no verification receipt created"
        )
    verify_context, _ = make_context(
        root,
        blueprints=[finding.blueprint],
        model_mode="off",
        run_external_tools=False,
    )
    after = review(verify_context)
    remaining = [item for item in after.findings if item.fingerprint == finding.fingerprint]
    return {
        "format": "blueprint-ai/reviewed-repair-receipt/v1",
        "plan_sha256": plan.plan_sha256,
        "finding_fingerprint": finding.fingerprint,
        "target": plan.target,
        "target_before_sha256": plan.target_sha256,
        "target_after_sha256": _sha(_target_bytes(root, plan.target)),
        "changes": [change.model_dump(mode="json") for change in applied.changes],
        "verification": "finding-absent" if not remaining else "finding-remains",
        "remaining_finding_fingerprints": [item.fingerprint for item in remaining],
        "verification_scope": (
            "same deterministic blueprint/finding only; no independent trial or quality claim"
        ),
    }
