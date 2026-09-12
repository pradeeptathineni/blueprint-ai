from __future__ import annotations

import re

from blueprint_ai.core.models import Finding, Priority, Severity

ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
ISSUE_ID = re.compile(
    r"\b(?:CVE-\d{4}-\d+|GHSA-[a-z0-9-]+|OSV-[a-z0-9-]+|CKV_[a-z0-9_-]+|[A-Z]\d{3,4})\b",
    re.IGNORECASE,
)
EQUIVALENT_IAC_RULES = {
    "AWS-0132": "iac/aws/s3-kms-encryption",
    "CKV_AWS_145": "iac/aws/s3-kms-encryption",
    "AWS-0089": "iac/aws/s3-access-logging",
    "CKV_AWS_18": "iac/aws/s3-access-logging",
    "AWS-0178": "iac/aws/vpc-flow-logs",
    "CKV2_AWS_11": "iac/aws/vpc-flow-logs",
    "AWS-0052": "iac/aws/alb-drop-invalid-headers",
    "CKV_AWS_131": "iac/aws/alb-drop-invalid-headers",
}


def derive_priority(finding: Finding, project_types: list[str]) -> Priority:
    priorities: dict[Severity, Priority] = {
        "critical": "P0",
        "high": "P1",
        "medium": "P2",
        "low": "P3",
        "info": "P3",
    }
    base = priorities[finding.severity]
    if finding.blueprint == "iac" and finding.category == "misconfiguration" and base == "P0":
        return "P1"
    exposed = bool(set(project_types) & {"api", "web-app", "production", "iac"})
    original_severity = finding.tool_metadata.get("original_severity")
    severity_is_explicit = original_severity is None or str(original_severity).lower() in {
        "critical",
        "high",
        "medium",
        "low",
        "info",
    }
    if (
        exposed
        and severity_is_explicit
        and finding.blueprint in {"security", "ci-cd", "iac"}
        and base == "P2"
    ):
        return "P1"
    return base


def deduplicate(findings: list[Finding], project_types: list[str]) -> list[Finding]:
    grouped: dict[str, Finding] = {}
    for item in findings:
        item.priority = derive_priority(item, project_types)
        original_rule = item.rule_id
        if original_rule and (canonical_rule := EQUIVALENT_IAC_RULES.get(original_rule.upper())):
            item.rule_id = canonical_rule
            item.evidence = sorted(set(item.evidence + [f"reported as {original_rule}"]))
        issue = ISSUE_ID.search(item.message)
        if issue and not item.rule_id:
            item.rule_id = issue.group(0).upper()
        item.sources = sorted(set(item.sources or [item.source]))
        key = (
            f"iac-equivalent:{item.rule_id}:{item.file}"
            if item.rule_id in EQUIVALENT_IAC_RULES.values()
            else item.fingerprint
        )
        previous = grouped.get(key)
        if not previous:
            grouped[key] = item
            continue
        previous.evidence = sorted(
            set(previous.evidence + item.evidence + [f"also reported by {item.source}"])
        )
        previous.sources = sorted(set(previous.sources + item.sources + [item.source]))
        if ORDER[item.priority or "P3"] < ORDER[previous.priority or "P3"]:
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
