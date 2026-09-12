from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from blueprint_ai import __version__
from blueprint_ai.adapters import applicable_adapters
from blueprint_ai.blueprints import BLUEPRINTS, PROFILES
from blueprint_ai.config import Settings, SuppressionRule, load_settings
from blueprint_ai.core import BlueprintResult, Finding, RunContext, RunReport
from blueprint_ai.core.models import ResultStatus, RunMetadata, Suppression
from blueprint_ai.core.priority import ORDER, deduplicate
from blueprint_ai.discovery import discover_project
from blueprint_ai.extensions import load_custom_blueprints
from blueprint_ai.model import CachedModelReviewer, ModelProvider, provider_from_environment
from blueprint_ai.model.reviewer import PROMPT_VERSION
from blueprint_ai.safety import MAX_MANIFEST_BYTES, atomic_write_text, read_text_bounded


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
    trust_project_executables: bool = False,
    authorized_target: str | None = None,
) -> tuple[RunContext, Settings]:
    root = path.expanduser().resolve()
    settings = load_settings(
        root,
        trust_project_executables=trust_project_executables,
        authorized_network_target=authorized_target,
    )
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
    catalog = {**BLUEPRINTS, **load_custom_blueprints(root)}
    unknown = [name for name in selected if name not in catalog]
    if unknown:
        raise ValueError(f"unknown blueprint: {', '.join(unknown)}")
    mode = output_mode or ("json" if json_output else "human")
    context = RunContext(
        root=root,
        profile="+".join(profiles),
        profiles=profiles,
        selected_blueprints=selected,
        model_mode=cast(Any, model_mode or settings.model_mode),
        model_budget=settings.model_budget,
        output_mode=cast(Any, mode),
        changed_only=changed_only,
        base_ref=base_ref,
        config=settings.model_dump(mode="json"),
        cache_dir=root / settings.output_path,
        trust_project_executables=trust_project_executables,
    )
    return context, settings


def _load_baseline(root: Path, settings: Settings) -> set[str]:
    if not settings.baseline_path:
        return set()
    path = root / settings.baseline_path
    if not path.is_file():
        return set()
    try:
        data = json.loads(read_text_bounded(path, MAX_MANIFEST_BYTES, root=root))
    except (OSError, UnicodeError, ValueError):
        return set()
    if isinstance(data, list):
        return {str(value) for value in data}
    if isinstance(data, dict):
        rows = data.get("fingerprints", data.get("findings", []))
        if not isinstance(rows, list):
            return set()
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
        offline=settings.offline,
        allow_project_executables=context.trust_project_executables,
    )
    if not adapters:
        return []
    workers = min(settings.max_workers, len(adapters))
    for adapter in adapters:
        adapter.max_output_bytes = settings.max_tool_output_bytes
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="blueprint-tool") as pool:
        futures = []
        for adapter in adapters:
            default_timeout = getattr(adapter, "default_timeout", None)
            timeout = (
                min(settings.tool_timeout, default_timeout)
                if isinstance(default_timeout, (int, float))
                else settings.tool_timeout
            )
            futures.append(pool.submit(adapter.run, context.root, float(timeout)))
        return [future.result() for future in futures]


def _disabled_capability_findings(settings: Settings, facts) -> list[Finding]:
    findings = []
    for name in sorted(set(settings.disabled_blueprints)):
        if name == "completeness":
            continue
        try:
            assessment = BLUEPRINTS[name].assess(facts)
        except (KeyError, ValueError):
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


def _collect_tool_results(
    context: RunContext, settings: Settings, facts, name: str, findings: list[Finding]
) -> tuple[list, list[str], int, int]:
    tools = []
    notes = []
    missing = errors = 0
    for status, adapter_findings, error in _run_tools(context, settings, facts, name):
        tools.append(status)
        missing += status.outcome in {"tool_missing", "unsupported"}
        errors += status.outcome == "tool_error"
        if error:
            notes.append(f"{status.name}: {error}")
        findings.extend(adapter_findings)
    return tools, notes, missing, errors


def _finalize_findings(
    findings: list[Finding], context: RunContext, facts, baseline: set[str], settings: Settings
) -> list[Finding]:
    findings = deduplicate(findings, facts.project_types)
    if context.changed_only and facts.changed_files:
        changed = set(facts.changed_files)
        findings = [item for item in findings if item.file is None or item.file in changed]
    findings = _classify(findings, baseline, settings)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    severity_limit = severity_order[settings.minimum_severity]
    priority_limit = ORDER[settings.maximum_priority]
    return [
        item
        for item in findings
        if severity_order[item.severity] <= severity_limit
        and ORDER[item.priority or "P3"] <= priority_limit
    ]


def _run_model_review(
    context: RunContext,
    settings: Settings,
    provider: ModelProvider | None,
    name: str,
    facts,
    findings: list[Finding],
    notes: list[str],
    default_budget: int,
    model_calls: int,
) -> tuple[dict[str, int | float | str | None], int, int, int]:
    if settings.offline:
        notes.append("model review disabled by offline mode")
        return {}, 0, 0, model_calls
    if model_calls >= settings.model_max_calls:
        notes.append("model review skipped because the per-run call budget was exhausted")
        return {}, 0, 0, model_calls
    if not provider:
        if context.model_mode == "on":
            notes.append("model requested but no configured provider is available")
            return {}, 1, 0, model_calls
        return {}, 0, 0, model_calls
    reviewer = CachedModelReviewer(
        provider,
        context.cache_dir or context.root / ".blueprint-ai",
        settings.blueprint_model_budgets.get(name, default_budget),
        changed_files=facts.changed_files if context.changed_only else None,
        cache_mode=settings.model_cache,
    )
    try:
        findings.extend(reviewer.review(context.root, name, facts, findings))
        calls = model_calls + (reviewer.last_metrics.get("cache") != "hit")
        return reviewer.last_metrics, 0, 0, calls
    except Exception as exc:  # provider errors never break deterministic operation
        notes.append(f"model review unavailable: {exc}")
        return {}, 0, 1, model_calls + 1


def review(context: RunContext, provider: ModelProvider | None = None) -> RunReport:
    started_at = datetime.now(UTC)
    started = time.monotonic()
    settings = Settings.model_validate(context.config)
    catalog = {**BLUEPRINTS, **load_custom_blueprints(context.root)}
    facts = discover_project(
        context.root,
        settings.ignores,
        changed_only=context.changed_only,
        base_ref=context.base_ref,
    )
    facts.project_types = sorted(set(facts.project_types + context.profiles))
    facts.suggested_profiles = sorted(set(facts.suggested_profiles + context.profiles))
    provider = (
        provider
        if provider is not None
        else provider_from_environment()
        if context.model_mode != "off" and not settings.offline
        else None
    )
    model_names = [
        name
        for name in context.selected_blueprints
        if catalog[name].model_review and catalog[name].applicability(facts)
    ]
    default_budget = max(context.model_budget // max(len(model_names), 1), 256)
    baseline = _load_baseline(context.root, settings)
    results = []
    model_calls = 0
    for name in context.selected_blueprints:
        definition = catalog[name]
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
        tools, notes, missing, errors = _collect_tool_results(
            context, settings, facts, name, findings
        )
        model_metrics: dict[str, int | float | str | None] = {}
        if definition.model_review and context.model_mode != "off":
            model_metrics, model_missing, model_errors, model_calls = _run_model_review(
                context,
                settings,
                provider,
                name,
                facts,
                findings,
                notes,
                default_budget,
                model_calls,
            )
            missing += model_missing
            errors += model_errors
        findings = _finalize_findings(findings, context, facts, baseline, settings)
        active = [item for item in findings if item.disposition == "new"]
        if errors or missing or assessment.state == "partial":
            status_name: ResultStatus = "partial"
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
    finished_at = datetime.now(UTC)
    tools = [tool for result in results for tool in result.tools]
    config_hash = hashlib.sha256(
        json.dumps(context.config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RunReport(
        facts=facts,
        profile=context.profile,
        results=results,
        metadata=RunMetadata(
            blueprint_ai_version=__version__,
            config_sha256=config_hash,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=round((time.monotonic() - started) * 1000),
            offline=settings.offline,
            model_mode=context.model_mode,
            model_provider=provider.name if provider else None,
            model_name=provider.model if provider else None,
            prompt_versions={name: PROMPT_VERSION for name in model_names},
            tool_versions={tool.name: tool.version for tool in tools},
        ),
    )


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
    atomic_write_text(
        path,
        json.dumps(payload, indent=2) + "\n",
        root=Path(report.facts.path),
    )
