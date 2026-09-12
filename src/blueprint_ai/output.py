from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from blueprint_ai.core import RunReport
from blueprint_ai.safety import sanitize_label


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
        incomplete = [
            tool
            for tool in result.tools
            if tool.outcome in {"tool_missing", "tool_error", "unsupported"}
        ]
        if incomplete:
            lines.append(
                "Incomplete tools: "
                + "; ".join(
                    f"`{tool.name}` ({tool.outcome}: "
                    f"{sanitize_label(tool.detail or 'analysis incomplete', 300)})"
                    for tool in incomplete
                )
            )
            lines.append("")
        if not result.findings:
            summary = (
                "No findings from completed checks; analysis is incomplete."
                if result.status == "partial"
                else "No findings."
            )
            lines.extend([summary, ""])
            continue
        lines.extend(
            [
                "| Priority | Disposition | Location | Finding | Remediation |",
                "|---|---|---|---|---|",
            ]
        )
        for finding in result.findings:
            location = sanitize_label(finding.file or "—")
            if finding.range and finding.range.start_line:
                location += f":{finding.range.start_line}"
            message = sanitize_label(finding.message, 1000).replace("|", "\\|")
            recommendation = sanitize_label(finding.recommendation, 1000).replace("|", "\\|")
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
        row: dict[str, Any] = {
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
                "toolMetadata": finding.tool_metadata,
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
                        "version": report.metadata.blueprint_ai_version
                        if report.metadata
                        else None,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def report_junit(report: RunReport) -> str:
    suite = ET.Element(
        "testsuite",
        {
            "name": "blueprint-ai",
            "tests": str(len(report.results)),
            "failures": str(sum(bool(r.findings) for r in report.results)),
            "errors": str(sum(r.status == "error" for r in report.results)),
            "skipped": str(sum(r.status == "not_applicable" for r in report.results)),
            "time": f"{(report.metadata.duration_ms if report.metadata else 0) / 1000:.3f}",
        },
    )
    for result in report.results:
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": "blueprint-ai", "name": result.blueprint},
        )
        active = [finding for finding in result.findings if finding.disposition == "new"]
        if result.status == "not_applicable":
            ET.SubElement(
                case, "skipped", {"message": result.notes[0] if result.notes else "not applicable"}
            )
        elif result.status == "error":
            error = ET.SubElement(case, "error", {"message": "blueprint execution error"})
            error.text = "\n".join(result.notes)
        elif active:
            failure = ET.SubElement(
                case,
                "failure",
                {"message": f"{len(active)} new finding(s)", "type": "BlueprintFindings"},
            )
            failure.text = "\n".join(
                f"{finding.priority} {finding.rule_id or finding.category} "
                f"{finding.file or '-'}: {sanitize_label(finding.message, 1000)}"
                for finding in active
            )
        if result.notes:
            output = ET.SubElement(case, "system-out")
            output.text = "\n".join(sanitize_label(note, 1000) for note in result.notes)
    ET.indent(suite)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def serialize_report(report: RunReport, mode: str) -> str:
    if mode == "markdown":
        return report_markdown(report)
    if mode == "sarif":
        return json.dumps(report_sarif(report), indent=2, sort_keys=True) + "\n"
    if mode == "junit":
        return report_junit(report)
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
