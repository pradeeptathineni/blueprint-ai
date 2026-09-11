from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Annotated

import typer

from blueprint_ai import __version__
from blueprint_ai.adapters import known_tools
from blueprint_ai.blueprints import BLUEPRINTS, PROFILES
from blueprint_ai.config import load_settings
from blueprint_ai.discovery import discover_project
from blueprint_ai.engine import make_context, remediation_plan, review
from blueprint_ai.model import provider_from_environment
from blueprint_ai.remediation import apply_findings

app = typer.Typer(help="Deterministic-first software project analysis.", no_args_is_help=True)
PathArg = Annotated[Path, typer.Argument(exists=True, file_okay=False, resolve_path=True)]
BlueprintOption = Annotated[list[str] | None, typer.Option("--blueprint", "-b")]
ProfileOption = Annotated[str | None, typer.Option("--profile", "-p")]
ModelOption = Annotated[str | None, typer.Option("--model", help="auto, off, or on")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit stable JSON output")]


def _dump(data: object) -> None:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    typer.echo(json.dumps(data, indent=2, sort_keys=True))


def _report_human(report) -> None:
    typer.echo(f"{report.facts.name}: {report.facts.file_count} files | profile {report.profile}")
    for result in report.results:
        typer.echo(f"\n{result.blueprint}: {result.status} ({len(result.findings)} findings)")
        for item in result.findings:
            location = f" {item.file}" if item.file else ""
            typer.echo(f"  {item.priority} {item.category}{location}: {item.message}")
        for note in result.notes:
            typer.echo(f"  note: {note}")


def _context(
    path: Path,
    profile: str | None,
    blueprints: list[str] | None,
    model: str | None,
    json_output: bool,
):
    if model and model not in {"auto", "off", "on"}:
        raise typer.BadParameter("model must be auto, off, or on")
    try:
        return make_context(
            path, profile=profile, blueprints=blueprints, model_mode=model, json_output=json_output
        )[0]
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def inspect(path: PathArg = Path("."), json_output: JsonOption = False) -> None:
    """Discover project facts without running tools or models."""
    try:
        settings = load_settings(path)
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
    rows = []
    for blueprint in BLUEPRINTS.values():
        if name and blueprint.name != name:
            continue
        rows.append(
            {
                "name": blueprint.name,
                "description": blueprint.description,
                "model_review": blueprint.model_review,
                "applicable": blueprint.applicability(facts) if facts else None,
            }
        )
    if name and not rows:
        raise typer.BadParameter(f"unknown blueprint: {name}")
    if json_output:
        _dump({"blueprints": rows, "profiles": PROFILES})
    else:
        for row in rows:
            applicable = (
                ""
                if row["applicable"] is None
                else f" [{('applicable' if row['applicable'] else 'not applicable')}]"
            )
            typer.echo(f"{row['name']}: {row['description']}{applicable}")


@app.command("review")
def review_command(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    model: ModelOption = None,
    json_output: JsonOption = False,
) -> None:
    """Run read-only deterministic checks, then bounded model review when enabled."""
    report = review(_context(path, profile, blueprint, model, json_output))
    _dump(report) if json_output else _report_human(report)


@app.command()
def plan(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    model: ModelOption = None,
    json_output: JsonOption = False,
) -> None:
    """Create a deduplicated prioritized remediation plan."""
    report = review(_context(path, profile, blueprint, model, json_output))
    findings = remediation_plan(report)
    if json_output:
        _dump({"findings": [item.model_dump(mode="json") for item in findings]})
    else:
        for index, item in enumerate(findings, 1):
            typer.echo(f"{index}. {item.priority} [{item.blueprint}] {item.message}")
            typer.echo(f"   {item.recommendation}")


@app.command()
def apply(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    model: ModelOption = "off",
    json_output: JsonOption = False,
) -> None:
    """Apply supported safe, merge-aware, idempotent remediations and verify them."""
    context = _context(path, profile, blueprint, model, json_output)
    before = review(context)
    applied = apply_findings(context.root, before.facts, remediation_plan(before))
    changed_blueprints = sorted({change.blueprint for change in applied.changed})
    verification = None
    if changed_blueprints:
        verify_context = context.model_copy(
            update={"selected_blueprints": changed_blueprints, "model_mode": "off"}
        )
        verification = review(verify_context)
    payload = {
        "changes": applied.model_dump(mode="json")["changes"],
        "verification": verification.model_dump(mode="json") if verification else None,
    }
    if json_output:
        _dump(payload)
    else:
        if not applied.changes:
            typer.echo("No supported remediations were needed.")
        for change in applied.changes:
            typer.echo(f"{change.status}: {change.target} ({change.detail})")
        if verification:
            typer.echo("\nVerification:")
            _report_human(verification)


@app.command()
def verify(
    path: PathArg = Path("."),
    blueprint: BlueprintOption = None,
    profile: ProfileOption = None,
    json_output: JsonOption = False,
) -> None:
    """Rerun selected checks without model judgment."""
    report = review(_context(path, profile, blueprint, "off", json_output))
    _dump(report) if json_output else _report_human(report)


@app.command()
def doctor(json_output: JsonOption = False) -> None:
    """Show runtime, external tool, and model availability."""
    tools = [adapter.status().model_dump(mode="json") for adapter in known_tools()]
    provider = provider_from_environment()
    data = {
        "blueprint_ai": __version__,
        "python": platform.python_version(),
        "runtime_ok": tuple(map(int, platform.python_version_tuple())) >= (3, 12, 0),
        "model": {
            "available": provider is not None,
            "provider": provider.name if provider else None,
            "detail": None
            if provider
            else "set OPENAI_API_KEY and install the openai SDK to enable model review",
        },
        "tools": tools,
    }
    if json_output:
        _dump(data)
    else:
        typer.echo(f"Blueprint AI {__version__} | Python {data['python']}")
        typer.echo(f"model: {'available' if provider else 'unavailable'}")
        for tool in tools:
            version = f" — {tool['version']}" if tool["version"] else ""
            typer.echo(
                f"{tool['name']}: {'available' if tool['available'] else 'missing'}{version}"
            )


if __name__ == "__main__":
    app()
