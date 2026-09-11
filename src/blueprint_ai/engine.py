from __future__ import annotations

from pathlib import Path

from blueprint_ai.adapters import applicable_adapters
from blueprint_ai.blueprints import PROFILES, get_blueprint
from blueprint_ai.config import Settings, load_settings
from blueprint_ai.core import BlueprintResult, Finding, RunContext, RunReport
from blueprint_ai.core.priority import ORDER, deduplicate
from blueprint_ai.discovery import discover_project
from blueprint_ai.model import CachedModelReviewer, ModelProvider, provider_from_environment


def detect_profile(project_types: list[str]) -> str:
    for name in ("web-app", "api", "cli", "iac", "library"):
        if name in project_types:
            return name
    return "default"


def make_context(
    path: Path,
    *,
    profile: str | None = None,
    blueprints: list[str] | None = None,
    model_mode: str | None = None,
    json_output: bool = False,
) -> tuple[RunContext, Settings]:
    root = path.expanduser().resolve()
    settings = load_settings(root)
    facts = discover_project(root, settings.ignores)
    selected_profile = profile or detect_profile(facts.project_types)
    if selected_profile not in PROFILES:
        raise ValueError(f"unknown profile: {selected_profile}")
    selected = blueprints or settings.profiles.get(selected_profile) or PROFILES[selected_profile]
    selected = [name for name in selected if name not in settings.disabled_blueprints]
    if settings.enabled_blueprints:
        selected.extend(settings.enabled_blueprints)
    for name in selected:
        get_blueprint(name)
    context = RunContext(
        root=root,
        profile=selected_profile,
        selected_blueprints=list(dict.fromkeys(selected)),
        model_mode=model_mode or settings.model_mode,
        model_budget=settings.model_budget,
        output_mode="json" if json_output else "human",
        config=settings.model_dump(),
        cache_dir=root / settings.output_path,
    )
    return context, settings


def review(context: RunContext, provider: ModelProvider | None = None) -> RunReport:
    settings = Settings.model_validate(context.config)
    facts = discover_project(context.root, settings.ignores)
    results = []
    provider = provider if provider is not None else provider_from_environment()
    model_blueprints = sum(
        1
        for name in context.selected_blueprints
        if get_blueprint(name).model_review and get_blueprint(name).applicability(facts)
    )
    per_blueprint_budget = max(context.model_budget // max(model_blueprints, 1), 64)
    reviewer = (
        CachedModelReviewer(
            provider,
            context.cache_dir or context.root / ".blueprint-ai",
            per_blueprint_budget,
        )
        if provider
        else None
    )
    for name in context.selected_blueprints:
        definition = get_blueprint(name)
        if not definition.applicability(facts):
            results.append(BlueprintResult(blueprint=name, status="not_applicable"))
            continue
        findings = definition.check(context.root, facts)
        tools = []
        notes = []
        missing = 0
        errors = 0
        for adapter in applicable_adapters(facts, name, settings.tool_overrides):
            status, adapter_findings, error = adapter.run(context.root)
            tools.append(status)
            if not status.available:
                missing += 1
            elif error:
                errors += 1
                notes.append(f"{adapter.name}: {error}")
            findings.extend(adapter_findings)
        if definition.model_review and context.model_mode != "off":
            if reviewer:
                try:
                    findings.extend(reviewer.review(context.root, name, facts, findings))
                except Exception as exc:  # provider errors must not break deterministic operation
                    errors += 1
                    notes.append(f"model review unavailable: {exc}")
            elif context.model_mode == "on":
                missing += 1
                notes.append("model requested but no configured provider is available")
        findings = deduplicate(findings, facts.project_types)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        severity_limit = severity_order.get(settings.minimum_severity, 4)
        priority_limit = ORDER.get(settings.maximum_priority, 3)
        findings = [
            item
            for item in findings
            if severity_order[item.severity] <= severity_limit
            and ORDER[item.priority or "P3"] <= priority_limit
        ]
        if errors or missing:
            status = (
                "partial" if findings or tools or definition.check is not None else "tool_missing"
            )
        else:
            status = "findings" if findings else "passed"
        results.append(
            BlueprintResult(
                blueprint=name, status=status, findings=findings, tools=tools, notes=notes
            )
        )
    return RunReport(facts=facts, profile=context.profile, results=results)


def remediation_plan(report: RunReport) -> list[Finding]:
    return sorted(
        report.findings,
        key=lambda item: (ORDER[item.priority or "P3"], item.blueprint, item.file or ""),
    )
