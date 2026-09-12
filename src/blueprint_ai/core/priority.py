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
    if (
        finding.scope
        in {"example", "fixture", "generated", "development", "test", "benchmark", "fuzz"}
        and finding.category == "dependency-vulnerability"
    ):
        return "P2" if base in {"P0", "P1"} else base
    if finding.tool_metadata.get("requires_caller_context") is True:
        return "P2" if base in {"P0", "P1"} else base
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
    # Alias equivalence is transitive, but only within the same dependency identity.
    aliases: dict[str, str] = {}

    def representative(alias: str) -> str:
        while aliases.get(alias, alias) != alias:
            alias = aliases[alias]
        return alias

    for item in findings:
        if item.provenance == "model":
            continue
        ids = set(item.advisory_aliases)
        if item.category == "dependency-vulnerability" and item.rule_id:
            ids.add(item.rule_id)
        roots = sorted({representative(value) for value in ids})
        if roots:
            for value in roots:
                aliases[value] = roots[0]

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
        if item.category == "dependency-vulnerability" and item.tool_metadata.get("package"):
            advisory = (
                item.rule_id or item.message
                if item.provenance == "model"
                else representative(item.rule_id or item.message)
            )
            package = str(item.tool_metadata["package"])
            ecosystem = str(item.tool_metadata.get("ecosystem") or "").lower()
            ecosystem = {"pip": "pypi", "python": "pypi", "node-pkg": "npm"}.get(
                ecosystem, ecosystem
            )
            if ecosystem == "pypi":
                package = re.sub(r"[-_.]+", "-", package).lower()
            identity = (
                item.component or item.file,
                item.scope,
                ecosystem,
                package,
                item.tool_metadata.get("installed_version"),
                advisory,
            )
            key = "dependency:" + repr(identity)
            item.rule_id = advisory
        if item.provenance == "model":
            key = "model:" + key
        previous = grouped.get(key)
        if not previous:
            grouped[key] = item
            continue
        previous.evidence = sorted(
            set(previous.evidence + item.evidence + [f"also reported by {item.source}"])
        )
        previous.sources = sorted(set(previous.sources + item.sources + [item.source]))
        previous.locations = sorted(set(previous.locations + item.locations))
        previous.advisory_aliases = sorted(set(previous.advisory_aliases + item.advisory_aliases))
        previous.evidence.extend([f"original evidence: {item.source}: {item.file}: {item.message}"])

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
