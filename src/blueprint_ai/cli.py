from __future__ import annotations

import importlib.util
import json
import os
import platform
from pathlib import Path
from typing import Annotated, Any

import typer

from blueprint_ai import __version__
from blueprint_ai.adapters import known_tools
from blueprint_ai.benchmark import benchmark_repository
from blueprint_ai.blueprints import BLUEPRINTS, PROFILES
from blueprint_ai.config import Settings, load_settings
from blueprint_ai.contracts import validate_builtin_contracts
from blueprint_ai.core import RunReport
from blueprint_ai.discovery import discover_project
from blueprint_ai.engine import make_context, remediation_plan, review, write_baseline
from blueprint_ai.extensions import custom_blueprint_schema, load_custom_blueprints
from blueprint_ai.model import provider_from_environment
from blueprint_ai.output import serialize_report
from blueprint_ai.remediation import KITS, apply_findings, apply_kit, rollback_operation
from blueprint_ai.safety import sanitize_label

app = typer.Typer(help="Deterministic-first software project analysis.", no_args_is_help=True)
PathArg = Annotated[Path, typer.Argument(exists=True, file_okay=False, resolve_path=True)]
BlueprintOption = Annotated[list[str] | None, typer.Option("--blueprint", "-b")]
ProfileOption = Annotated[str | None, typer.Option("--profile", "-p")]
ModelOption = Annotated[str | None, typer.Option("--model", help="auto, off, or on")]
NoModelOption = Annotated[
    bool, typer.Option("--no-model", help="Disable model calls for deterministic CI execution")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit stable JSON output")]
FormatOption = Annotated[
    str | None, typer.Option("--format", help="Output format: human, json, markdown, or sarif")
]
ChangedOption = Annotated[
    bool, typer.Option("--changed", help="Prioritize/filter changed Git files")
]
BaseRefOption = Annotated[
    str | None, typer.Option("--base-ref", help="Git ref for changed-file mode")
]
TrustOption = Annotated[
    bool,
    typer.Option(
        "--trust-project-executables",
        help="Allow reviewed project config, wrappers, and local tool binaries to execute",
    ),
]
FailOnOption = Annotated[
    str | None,
    typer.Option("--fail-on", help="Fail on new findings at or above P0, P1, P2, or P3"),
]
AuthorizeTargetOption = Annotated[
    str | None,
    typer.Option(
        "--authorize-target",
        help="Explicitly authorize this exact credential-free HTTP(S) DAST target",
    ),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the Blueprint AI version and exit.",
        ),
    ] = False,
) -> None:
    """Analyze and strengthen software projects."""


def _dump(data: object) -> None:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    typer.echo(json.dumps(data, indent=2, sort_keys=True))


def _report_human(report) -> None:
    typer.echo(
        f"{report.facts.name}: {report.facts.file_count} files | profile {report.profile} | "
        f"{len(report.active_findings)} new, {len(report.baseline_findings)} baseline, "
        f"{len(report.suppressed_findings)} suppressed"
    )
    for result in report.results:
        typer.echo(f"\n{result.blueprint}: {result.status} ({len(result.findings)} findings)")
        for tool in result.tools:
            if tool.outcome in {"tool_missing", "tool_error", "unsupported"}:
                typer.echo(
                    f"  tool {tool.name}: {tool.outcome}"
                    f" ({sanitize_label(tool.detail or 'analysis incomplete', 300)})"
                )
        for item in result.findings:
            location = f" {sanitize_label(item.file)}" if item.file else ""
            typer.echo(
                f"  {item.priority} {item.category}{location} [{item.disposition}]: "
                f"{sanitize_label(item.message, 1000)}"
            )
        for note in result.notes:
            typer.echo(f"  note: {sanitize_label(note, 1000)}")


def _context(
    path: Path,
    profile: str | None,
    blueprints: list[str] | None,
    model: str | None,
    json_output: bool,
    output_format: str | None = None,
    changed: bool = False,
    base_ref: str | None = None,
    trust_project_executables: bool = False,
    fail_on: str | None = None,
    authorized_target: str | None = None,
):
    if model and model not in {"auto", "off", "on"}:
        raise typer.BadParameter("model must be auto, off, or on")
    if output_format and output_format not in {"human", "json", "markdown", "sarif", "junit"}:
        raise typer.BadParameter("format must be human, json, markdown, sarif, or junit")
    if json_output and output_format and output_format != "json":
        raise typer.BadParameter("--json cannot be combined with a different --format")
    if fail_on and fail_on not in {"P0", "P1", "P2", "P3"}:
        raise typer.BadParameter("fail-on must be P0, P1, P2, or P3")
    try:
        context = make_context(
            path,
            profile=profile,
            blueprints=blueprints,
            model_mode=model,
            json_output=json_output,
            output_mode=output_format,
            changed_only=changed,
            base_ref=base_ref,
            trust_project_executables=trust_project_executables,
            authorized_target=authorized_target,
        )[0]
        if fail_on:
            context.config["fail_on_priority"] = fail_on
        return context
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _emit_report(report, mode: str) -> None:
    if mode == "human":
        _report_human(report)
    else:
        typer.echo(serialize_report(report, mode), nl=False)


def _enforce_exit_policy(report: RunReport, context) -> None:
    threshold = context.config.get("fail_on_priority")
    if threshold is None:
        return
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    if any(order[item.priority or "P3"] <= order[threshold] for item in report.active_findings):
        raise typer.Exit(code=2)


@app.command()
def inspect(path: PathArg = Path("."), json_output: JsonOption = False) -> None:
    """Discover project facts without running tools or models."""
    try:
        settings = load_settings(path, trust_project_executables=True)
        facts = discover_project(path, settings.ignores)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _dump(facts)
    else:
        typer.echo(f"{facts.name}: {facts.file_count} files ({facts.ignored_count} ignored)")
        typer.echo(f"languages: {', '.join(facts.languages) or 'none'}")
        typer.echo(f"types: {', '.join(facts.project_types) or 'unknown'}")
        typer.echo(f"frameworks: {', '.join(facts.frameworks) or 'none'}")
        typer.echo(f"git: {'yes' if facts.is_git else 'no'}")


@app.command("blueprints")
def list_blueprints(
    name: Annotated[str | None, typer.Argument()] = None,
    path: Annotated[Path | None, typer.Option("--path", exists=True, file_okay=False)] = None,
    json_output: JsonOption = False,
) -> None:
    """List blueprints or show one blueprint."""
    facts = discover_project(path.resolve()) if path else None
    catalog = {**BLUEPRINTS, **(load_custom_blueprints(path.resolve()) if path else {})}
    rows: list[dict[str, Any]] = []
    for blueprint in catalog.values():
        if name and blueprint.name != name:
            continue
        assessment = blueprint.assess(facts) if facts else None
        rows.append(
            {
                "name": blueprint.name,
                "description": blueprint.description,
                "model_review": blueprint.model_review,
                "applicable": assessment.state != "not_applicable" if assessment else None,
                "applicability": assessment.model_dump(mode="json") if assessment else None,
            }
        )
    if name and not rows:
        raise typer.BadParameter(f"unknown blueprint: {name}")
    if json_output:
        _dump({"blueprints": rows, "profiles": PROFILES})
    else:
        for row in rows:
            row_assessment = row["applicability"]
            applicable = (
                ""
                if row_assessment is None
                else (f" [{row_assessment['state']}: {row_assessment['reason']}]")
            )
            typer.echo(f"{row['name']}: {row['description']}{applicable}")


@app.command("review")
def review_command(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    model: ModelOption = None,
    no_model: NoModelOption = False,
    json_output: JsonOption = False,
    output_format: FormatOption = None,
    changed: ChangedOption = False,
    base_ref: BaseRefOption = None,
    trust_project_executables: TrustOption = False,
    fail_on: FailOnOption = None,
    authorized_target: AuthorizeTargetOption = None,
) -> None:
    """Run read-only deterministic checks, then bounded model review when enabled."""
    if no_model and model not in {None, "off"}:
        raise typer.BadParameter("--no-model cannot be combined with --model auto or on")
    context = _context(
        path,
        profile,
        blueprint,
        "off" if no_model else model,
        json_output,
        output_format,
        changed,
        base_ref,
        trust_project_executables,
        fail_on,
        authorized_target,
    )
    report = review(context)
    _emit_report(report, context.output_mode)
    _enforce_exit_policy(report, context)


@app.command()
def plan(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    model: ModelOption = None,
    json_output: JsonOption = False,
    trust_project_executables: TrustOption = False,
    authorized_target: AuthorizeTargetOption = None,
) -> None:
    """Create a deduplicated prioritized remediation plan."""
    report = review(
        _context(
            path,
            profile,
            blueprint,
            model,
            json_output,
            trust_project_executables=trust_project_executables,
            authorized_target=authorized_target,
        )
    )
    findings = remediation_plan(report)
    if json_output:
        _dump({"findings": [item.model_dump(mode="json") for item in findings]})
    else:
        for index, item in enumerate(findings, 1):
            typer.echo(
                f"{index}. {item.priority} [{item.blueprint}] {sanitize_label(item.message, 1000)}"
            )
            typer.echo(f"   {sanitize_label(item.recommendation, 1000)}")


@app.command()
def apply(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    model: ModelOption = "off",
    json_output: JsonOption = False,
    kit: Annotated[str | None, typer.Option("--kit", help="Apply one capability kit")] = None,
    trust_project_executables: TrustOption = False,
    authorized_target: AuthorizeTargetOption = None,
) -> None:
    """Apply supported safe, merge-aware, idempotent remediations and verify them."""
    context = _context(
        path,
        profile,
        blueprint,
        model,
        json_output,
        trust_project_executables=trust_project_executables,
        authorized_target=authorized_target,
    )
    if kit:
        try:
            applied = apply_kit(context.root, discover_project(context.root), kit)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        payload = applied.model_dump(mode="json")
        if json_output:
            _dump(payload)
        else:
            for change in applied.changes:
                typer.echo(f"{change.status}: {change.target} ({change.detail})")
            for check in applied.verifications:
                typer.echo(f"verify {check.status}: {' '.join(check.command)} ({check.detail})")
            if applied.operation_id:
                typer.echo(f"rollback: blueprint-ai rollback {applied.operation_id} {context.root}")
        return
    before = review(context)
    applied = apply_findings(context.root, before.facts, remediation_plan(before))
    changed_blueprints = sorted({change.blueprint for change in applied.changed})
    verification_report: RunReport | None = None
    if changed_blueprints:
        verify_context = context.model_copy(
            update={"selected_blueprints": changed_blueprints, "model_mode": "off"}
        )
        verification_report = review(verify_context)
    payload = {
        "changes": applied.model_dump(mode="json")["changes"],
        "verification": verification_report.model_dump(mode="json")
        if verification_report
        else None,
    }
    if json_output:
        _dump(payload)
    else:
        if not applied.changes:
            typer.echo("No supported remediations were needed.")
        for change in applied.changes:
            typer.echo(f"{change.status}: {change.target} ({change.detail})")
        if verification_report:
            typer.echo("\nVerification:")
            _report_human(verification_report)


@app.command()
def verify(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    json_output: JsonOption = False,
    output_format: FormatOption = None,
    changed: ChangedOption = False,
    base_ref: BaseRefOption = None,
    trust_project_executables: TrustOption = False,
    fail_on: FailOnOption = None,
    authorized_target: AuthorizeTargetOption = None,
) -> None:
    """Rerun selected checks without model judgment."""
    context = _context(
        path,
        profile,
        blueprint,
        "off",
        json_output,
        output_format,
        changed,
        base_ref,
        trust_project_executables,
        fail_on,
        authorized_target,
    )
    report = review(context)
    _emit_report(report, context.output_mode)
    _enforce_exit_policy(report, context)


@app.command()
def baseline(
    path: PathArg = Path("."),
    profile: ProfileOption = None,
    json_output: JsonOption = False,
    trust_project_executables: TrustOption = False,
    authorized_target: AuthorizeTargetOption = None,
) -> None:
    """Record current findings so later reviews identify only regressions as new."""
    context, settings = make_context(
        path,
        profile=profile,
        model_mode="off",
        json_output=True,
        trust_project_executables=trust_project_executables,
        authorized_target=authorized_target,
    )
    if not settings.baseline_path:
        raise typer.BadParameter("baseline_path is disabled in configuration")
    report = review(context)
    target = context.root / settings.baseline_path
    write_baseline(report, target)
    payload = {
        "path": target.relative_to(context.root).as_posix(),
        "findings": len(report.findings),
    }
    _dump(payload) if json_output else typer.echo(
        f"Recorded {payload['findings']} finding fingerprints in {payload['path']}."
    )


@app.command("kits")
def list_kits(json_output: JsonOption = False) -> None:
    """List versioned, project-aware capability kits."""
    rows = [kit.model_dump(mode="json") for kit in KITS.values()]
    if json_output:
        _dump({"kits": rows})
    else:
        for kit in KITS.values():
            typer.echo(f"{kit.name} v{kit.version}: {', '.join(kit.files)}")


@app.command()
def rollback(
    operation_id: Annotated[str, typer.Argument()],
    path: PathArg = Path("."),
    json_output: JsonOption = False,
) -> None:
    """Remove unchanged files created by one apply operation."""
    try:
        result = rollback_operation(path.resolve(), operation_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _dump(result)
    else:
        for change in result.changes:
            typer.echo(f"{change.status}: {change.target} ({change.detail})")


@app.command()
def bootstrap(json_output: JsonOption = False) -> None:
    """Print explicit install guidance; never downloads or installs tools."""
    statuses = [adapter.status() for adapter in known_tools()]
    missing = [status for status in statuses if not status.available]
    data = {
        "mode": "guidance-only",
        "tools": [status.model_dump(mode="json") for status in missing],
        "note": (
            "Blueprint AI never silently downloads executables; pin tools in project or CI config."
        ),
    }
    if json_output:
        _dump(data)
    else:
        typer.echo(data["note"])
        for status in missing:
            typer.echo(f"{status.name}: {status.detail}")


@app.command()
def doctor(json_output: JsonOption = False) -> None:
    """Show runtime, external tool, and model availability."""
    definitions = known_tools()
    tools = [
        {**adapter.status().model_dump(mode="json"), "blueprint": adapter.blueprint}
        for adapter in definitions
    ]
    provider = provider_from_environment()
    if provider:
        model_detail = None
    elif not os.environ.get("OPENAI_API_KEY"):
        model_detail = "OPENAI_API_KEY is not set; deterministic review remains available"
    elif importlib.util.find_spec("openai") is None:
        model_detail = "install the optional model extra to enable OpenAI review"
    else:
        model_detail = "the configured model provider is unavailable"
    contracts = validate_builtin_contracts(BLUEPRINTS, definitions)
    data = {
        "blueprint_ai": __version__,
        "python": platform.python_version(),
        "runtime_ok": tuple(map(int, platform.python_version_tuple())) >= (3, 12, 0),
        "model": {
            "available": provider is not None,
            "provider": provider.name if provider else None,
            "detail": model_detail,
        },
        "tools": tools,
        "contracts": {"ok": True, **contracts},
    }
    if json_output:
        _dump(data)
    else:
        typer.echo(f"Blueprint AI {__version__} | Python {data['python']}")
        typer.echo(f"model: {'available' if provider else 'unavailable'}")
        for tool in tools:
            version = f" — {tool['version']}" if tool["version"] else ""
            typer.echo(
                f"{tool['name']} ({tool['blueprint']}): "
                f"{'available' if tool['available'] else tool['outcome']}{version}"
            )


@app.command("schema")
def schema_command(
    name: Annotated[str, typer.Argument(help="settings, report, or custom-blueprint")] = "report",
) -> None:
    """Print stable machine-readable JSON schemas for integrations and extensions."""
    schemas = {
        "settings": Settings.model_json_schema(),
        "report": RunReport.model_json_schema(mode="serialization"),
        "custom-blueprint": custom_blueprint_schema(),
    }
    if name not in schemas:
        raise typer.BadParameter("schema must be settings, report, or custom-blueprint")
    _dump({"schema_version": "1.0.0", "schema": schemas[name]})


@app.command("benchmark")
def benchmark_command(
    path: PathArg = Path("."),
    repeats: Annotated[int, typer.Option(min=1, max=20)] = 3,
) -> None:
    """Measure discovery and model-context construction without invoking tools or a model."""
    _dump(benchmark_repository(path, repeats))


if __name__ == "__main__":
    app()
