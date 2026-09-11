from __future__ import annotations

import fnmatch
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from blueprint_ai.adapters import applicable_adapters
from blueprint_ai.blueprints import PROFILES, get_blueprint
from blueprint_ai.config import Settings, SuppressionRule, load_settings
from blueprint_ai.core import BlueprintResult, Finding, RunContext, RunReport
from blueprint_ai.core.models import Suppression
from blueprint_ai.core.priority import ORDER, deduplicate
from blueprint_ai.discovery import discover_project
from blueprint_ai.model import CachedModelReviewer, ModelProvider, provider_from_environment


def detect_profiles(project_types: list[str]) -> list[str]:
    preferred = (
        "full-stack",
        "web-app",
        "frontend",
        "api",
        "backend-service",
        "ai-agent",
        "ai-app",
        "data-pipeline",
        "kubernetes",
        "terraform",
        "container",
        "cli",
        "library",
        "iac",
    )
    detected = [name for name in preferred if name in project_types]
    return detected or ["default"]


def detect_profile(project_types: list[str]) -> str:
    """Compatibility helper returning the primary profile."""
    return detect_profiles(project_types)[0]


def _parse_profiles(profile: str | None, project_types: list[str]) -> list[str]:
    profiles = (
        [item.strip() for item in profile.split(",") if item.strip()]
        if profile
        else detect_profiles(project_types)
    )
    unknown = [item for item in profiles if item not in PROFILES]
    if unknown:
        raise ValueError(f"unknown profile: {', '.join(unknown)}")
    return list(dict.fromkeys(profiles))


def make_context(
    path: Path,
    *,
    profile: str | None = None,
    blueprints: list[str] | None = None,
    model_mode: str | None = None,
    json_output: bool = False,
    output_mode: str | None = None,
    changed_only: bool = False,
    base_ref: str | None = None,
) -> tuple[RunContext, Settings]:
    root = path.expanduser().resolve()
    settings = load_settings(root)
    facts = discover_project(
        root,
        settings.ignores,
        changed_only=changed_only,
        base_ref=base_ref,
    )
    profiles = _parse_profiles(profile, facts.project_types)
    selected = list(blueprints or [])
    if not selected:
        for selected_profile in profiles:
            selected.extend(settings.profiles.get(selected_profile) or PROFILES[selected_profile])
    selected = [name for name in selected if name not in settings.disabled_blueprints]
    selected.extend(settings.enabled_blueprints)
    selected = list(dict.fromkeys(selected))
    for name in selected:
        get_blueprint(name)
    mode = output_mode or ("json" if json_output else "human")
    context = RunContext(
        root=root,
        profile="+".join(profiles),
        profiles=profiles,
        selected_blueprints=selected,
        model_mode=model_mode or settings.model_mode,
        model_budget=settings.model_budget,
        output_mode=mode,
        changed_only=changed_only,
        base_ref=base_ref,
        config=settings.model_dump(mode="json"),
        cache_dir=root / settings.output_path,
    )
    return context, settings


def _load_baseline(root: Path, settings: Settings) -> set[str]:
    if not settings.baseline_path:
        return set()
    path = root / settings.baseline_path
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if isinstance(data, list):
        return {str(value) for value in data}
    if isinstance(data, dict):
        rows = data.get("fingerprints", data.get("findings", []))
        return {
            str(row.get("fingerprint") if isinstance(row, dict) else row) for row in rows if row
        }
    return set()


def _matches(rule: SuppressionRule, finding: Finding) -> bool:
    if rule.fingerprint and rule.fingerprint != finding.fingerprint:
        return False
    if rule.blueprint and rule.blueprint != finding.blueprint:
        return False
    if rule.category and rule.category != finding.category:
        return False
    return not rule.file or bool(finding.file and fnmatch.fnmatch(finding.file, rule.file))


def _classify(findings: list[Finding], baseline: set[str], settings: Settings) -> list[Finding]:
    now = datetime.now(UTC)
    for finding in findings:
        finding.sources = sorted(set(finding.sources or [finding.source]))
        for rule in settings.suppressions:
            expiry = (
                datetime.combine(rule.expires, datetime.min.time(), tzinfo=UTC)
                if rule.expires
                else None
            )
            if expiry and expiry < now:
                continue
            if _matches(rule, finding):
                finding.disposition = "suppressed"
                finding.suppression = Suppression(reason=rule.reason, expires=expiry)
                break
        else:
            if finding.fingerprint in baseline:
                finding.disposition = "baseline"
    return findings


def _run_tools(context: RunContext, settings: Settings, facts, blueprint: str):
    adapters = applicable_adapters(
        facts,
        blueprint,
        settings.tool_overrides,
        authorized_target=settings.authorized_target,
    )
    if not adapters:
        return []
    workers = min(settings.max_workers, len(adapters))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="blueprint-tool") as pool:
        futures = [
            pool.submit(adapter.run, context.root, settings.tool_timeout) for adapter in adapters
        ]
        return [future.result() for future in futures]


def _disabled_capability_findings(settings: Settings, facts) -> list[Finding]:
    findings = []
    for name in sorted(set(settings.disabled_blueprints)):
        if name == "completeness":
            continue
        try:
            assessment = get_blueprint(name).assess(facts)
        except ValueError:
            continue
        if assessment.state == "not_applicable":
            continue
        findings.append(
            Finding(
                blueprint="completeness",
                category="disabled-applicable-blueprint",
                rule_id=f"blueprint-ai/completeness/disabled/{name}",
                source="blueprint-ai",
                sources=["blueprint-ai"],
                severity="low",
                message=f"Applicable blueprint '{name}' is disabled.",
                recommendation=(
                    f"Enable {name}, or suppress this completeness finding with an owned reason."
                ),
                evidence=[assessment.reason],
            )
        )
    return findings


def review(context: RunContext, provider: ModelProvider | None = None) -> RunReport:
    settings = Settings.model_validate(context.config)
    facts = discover_project(
        context.root,
        settings.ignores,
        changed_only=context.changed_only,
        base_ref=context.base_ref,
    )
    facts.project_types = sorted(set(facts.project_types + context.profiles))
    facts.suggested_profiles = sorted(set(facts.suggested_profiles + context.profiles))
    provider = provider if provider is not None else provider_from_environment()
    model_names = [
        name
        for name in context.selected_blueprints
        if get_blueprint(name).model_review and get_blueprint(name).applicability(facts)
    ]
    default_budget = max(context.model_budget // max(len(model_names), 1), 256)
    baseline = _load_baseline(context.root, settings)
    results = []
    for name in context.selected_blueprints:
        definition = get_blueprint(name)
        assessment = definition.assess(facts)
        if assessment.state == "not_applicable":
            results.append(
                BlueprintResult(
                    blueprint=name,
                    status="not_applicable",
                    applicability=assessment,
                    notes=[assessment.reason],
                )
            )
            continue
        findings = definition.check(context.root, facts)
        if name == "completeness":
            findings.extend(_disabled_capability_findings(settings, facts))
        tools = []
        notes = []
        missing = 0
        errors = 0
        for status, adapter_findings, error in _run_tools(context, settings, facts, name):
            tools.append(status)
            if status.outcome == "tool_missing":
                missing += 1
            elif status.outcome == "tool_error":
                errors += 1
            if error:
                notes.append(f"{status.name}: {error}")
            findings.extend(adapter_findings)
        model_metrics: dict[str, int | float | str | None] = {}
        if definition.model_review and context.model_mode != "off":
            if provider:
                budget = settings.blueprint_model_budgets.get(name, default_budget)
                reviewer = CachedModelReviewer(
                    provider,
                    context.cache_dir or context.root / ".blueprint-ai",
                    budget,
                    changed_files=facts.changed_files if context.changed_only else None,
                )
                try:
                    findings.extend(reviewer.review(context.root, name, facts, findings))
                    model_metrics = reviewer.last_metrics
                except Exception as exc:  # provider errors never break deterministic operation
                    errors += 1
                    notes.append(f"model review unavailable: {exc}")
            elif context.model_mode == "on":
                missing += 1
                notes.append("model requested but no configured provider is available")
        findings = deduplicate(findings, facts.project_types)
        if context.changed_only and facts.changed_files:
            changed = set(facts.changed_files)
            findings = [item for item in findings if item.file is None or item.file in changed]
        findings = _classify(findings, baseline, settings)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        severity_limit = severity_order.get(settings.minimum_severity, 4)
        priority_limit = ORDER.get(settings.maximum_priority, 3)
        findings = [
            item
            for item in findings
            if severity_order[item.severity] <= severity_limit
            and ORDER[item.priority or "P3"] <= priority_limit
        ]
        active = [item for item in findings if item.disposition == "new"]
        if errors or missing or assessment.state == "partial":
            status_name = "partial"
        else:
            status_name = "findings" if active else "passed"
        results.append(
            BlueprintResult(
                blueprint=name,
                status=status_name,
                findings=findings,
                tools=tools,
                notes=notes,
                applicability=assessment,
                model_metrics=model_metrics,
            )
        )
    return RunReport(facts=facts, profile=context.profile, results=results)


def remediation_plan(report: RunReport) -> list[Finding]:
    return sorted(
        report.active_findings,
        key=lambda item: (ORDER[item.priority or "P3"], item.blueprint, item.file or ""),
    )


def write_baseline(report: RunReport, path: Path) -> None:
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "fingerprints": sorted({finding.fingerprint for finding in report.findings}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
