from __future__ import annotations

import re

from blueprint_ai.core.models import Finding

ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
ISSUE_ID = re.compile(
    r"\b(?:CVE-\d{4}-\d+|GHSA-[a-z0-9-]+|OSV-[a-z0-9-]+|CKV_[a-z0-9_-]+|[A-Z]\d{3,4})\b",
    re.IGNORECASE,
)


def derive_priority(finding: Finding, project_types: list[str]) -> str:
    base = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3", "info": "P3"}[
        finding.severity
    ]
    exposed = bool(set(project_types) & {"api", "web-app", "production", "iac"})
    if exposed and finding.blueprint in {"security", "ci-cd", "iac"} and base == "P2":
        return "P1"
    return base


def deduplicate(findings: list[Finding], project_types: list[str]) -> list[Finding]:
    grouped: dict[str, Finding] = {}
    for item in findings:
        item.priority = derive_priority(item, project_types)
        issue = ISSUE_ID.search(item.message)
        if issue and not item.rule_id:
            item.rule_id = issue.group(0).upper()
        item.sources = sorted(set(item.sources or [item.source]))
        key = item.fingerprint
        previous = grouped.get(key)
        if not previous:
            grouped[key] = item
            continue
        previous.evidence = sorted(
            set(previous.evidence + item.evidence + [f"also reported by {item.source}"])
        )
        previous.sources = sorted(set(previous.sources + item.sources + [item.source]))
        if ORDER[item.priority] < ORDER[previous.priority]:
            previous.priority = item.priority
            previous.severity = item.severity
        previous.confidence = max(previous.confidence, item.confidence)
    return sorted(
        grouped.values(),
        key=lambda item: (
            ORDER[item.priority or "P3"],
            item.blueprint,
            item.file or "",
            item.message,
        ),
    )
