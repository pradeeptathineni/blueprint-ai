from __future__ import annotations

import json
from pathlib import Path

from blueprint_ai.core import RunReport


def report_markdown(report: RunReport) -> str:
    lines = [
        f"# Blueprint AI review: {report.facts.name}",
        "",
        f"Profile: `{report.profile}` · Files: {report.facts.file_count} · "
        f"New: {len(report.active_findings)} · Baseline: {len(report.baseline_findings)} · "
        f"Suppressed: {len(report.suppressed_findings)}",
        "",
    ]
    for result in report.results:
        reason = f" — {result.applicability.reason}" if result.applicability else ""
        lines.extend([f"## {result.blueprint}: {result.status}{reason}", ""])
        if not result.findings:
            lines.extend(["No findings.", ""])
            continue
        lines.extend(
            [
                "| Priority | Disposition | Location | Finding | Remediation |",
                "|---|---|---|---|---|",
            ]
        )
        for finding in result.findings:
            location = finding.file or "—"
            if finding.range and finding.range.start_line:
                location += f":{finding.range.start_line}"
            message = finding.message.replace("|", "\\|").replace("\n", " ")
            recommendation = finding.recommendation.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {finding.priority} | {finding.disposition} | `{location}` | "
                f"{message} | {recommendation} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_sarif(report: RunReport) -> dict:
    results = []
    rules = {}
    levels = {
        "critical": "error",
        "high": "error",
        "medium": "warning",
        "low": "note",
        "info": "note",
    }
    for finding in report.findings:
        rule_id = finding.rule_id or f"blueprint-ai/{finding.blueprint}/{finding.category}"
        rules[rule_id] = {
            "id": rule_id,
            "shortDescription": {"text": finding.category},
            "help": {"text": finding.recommendation},
        }
        row = {
            "ruleId": rule_id,
            "level": levels[finding.severity],
            "message": {"text": finding.message},
            "partialFingerprints": {"blueprintAiFingerprint": finding.fingerprint},
            "properties": {
                "blueprint": finding.blueprint,
                "priority": finding.priority,
                "confidence": finding.confidence,
                "provenance": finding.provenance,
                "sources": finding.sources or [finding.source],
                "disposition": finding.disposition,
                "verification": finding.verification,
            },
        }
        if finding.file:
            region = {}
            if finding.range and finding.range.start_line:
                region["startLine"] = finding.range.start_line
            row["locations"] = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": Path(finding.file).as_posix()},
                        **({"region": region} if region else {}),
                    }
                }
            ]
        if finding.disposition == "suppressed":
            row["suppressions"] = [
                {
                    "kind": "external",
                    "justification": finding.suppression.reason
                    if finding.suppression
                    else "configured",
                }
            ]
        elif finding.disposition == "baseline":
            row["baselineState"] = "unchanged"
        else:
            row["baselineState"] = "new"
        results.append(row)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Blueprint AI",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def serialize_report(report: RunReport, mode: str) -> str:
    if mode == "markdown":
        return report_markdown(report)
    if mode == "sarif":
        return json.dumps(report_sarif(report), indent=2, sort_keys=True) + "\n"
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
