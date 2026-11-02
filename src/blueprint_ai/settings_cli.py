"""CLI access to the same project defaults used by the local settings page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from blueprint_ai.settings import preview_settings, read_settings, write_settings

app = typer.Typer(help="Inspect, edit and reset ordinary project defaults.", no_args_is_help=True)
Project = Annotated[Path, typer.Argument(exists=True, file_okay=False, resolve_path=True)]


def _emit(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
    else:
        typer.echo(f"Project settings · revision {result['revision']}")
        for item in result["settings"]:
            typer.echo(
                f"{item['category']} / {item['id']}: {json.dumps(item['effective'])} "
                f"({item['source']}, default {json.dumps(item['default'])})"
            )
        if "saved" in result:
            typer.echo(
                "Saved. Applies to the next operation." if result["saved"] else "Preview only."
            )


@app.command("show")
def show(
    path: Project = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
    query: Annotated[str, typer.Option(help="Search labels, IDs and descriptions")] = "",
    category: Annotated[str, typer.Option(help="Filter an exact category")] = "",
    changed: Annotated[
        bool, typer.Option(help="Show values different from built-in defaults")
    ] = False,
) -> None:
    """Show defaults, effective values and their source without running a review."""
    try:
        result = read_settings(path)
    except (ValueError, OSError):
        raise typer.BadParameter(
            "Cannot read valid project settings. Check the local YAML."
        ) from None
    result["settings"] = [
        item
        for item in result["settings"]
        if (not category or item["category"].casefold() == category.casefold())
        and (not changed or item["effective"] != item["default"])
        and query.casefold()
        in " ".join(
            str(item[field]) for field in ("id", "label", "category", "description")
        ).casefold()
    ]
    _emit(result, json_output)


def _edit(
    path: Path,
    updates: dict[str, Any],
    resets: list[str],
    expect: str | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    try:
        revision = expect if expect is not None else read_settings(path)["revision"]
        operation = preview_settings if dry_run else write_settings
        _emit(operation(path, updates, resets, revision), json_output)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    except OSError:
        raise typer.BadParameter(
            "Cannot access project settings. Check the document and permissions."
        ) from None


@app.command("set")
def set_value(
    key: str,
    value: Annotated[str, typer.Argument(help='JSON value, for example 8 or "high"')],
    path: Project = Path("."),
    expect: Annotated[str | None, typer.Option(help="Require this exact document revision")] = None,
    dry_run: Annotated[bool, typer.Option(help="Validate and preview without saving")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Save one supported default. Enum names can be passed without JSON quotes."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    _edit(path, {key: parsed}, [], expect, dry_run, json_output)


@app.command("reset")
def reset(
    key: str,
    path: Project = Path("."),
    expect: Annotated[str | None, typer.Option(help="Require this exact document revision")] = None,
    dry_run: Annotated[bool, typer.Option(help="Validate and preview without saving")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Remove one project override so its built-in default takes effect."""
    _edit(path, {}, [key], expect, dry_run, json_output)


@app.command("serve")
def serve(
    path: Project = Path("."),
    port: Annotated[int, typer.Option(min=0, max=65535)] = 0,
) -> None:
    """Serve settings on 127.0.0.1 until Ctrl-C. Open the printed URL manually."""
    from blueprint_ai.settings_web import settings_server

    try:
        with settings_server(path, port) as server:
            typer.echo(f"Blueprint settings: http://127.0.0.1:{server.server_port}")
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError):
        raise typer.BadParameter(
            "Cannot serve settings. Check project settings and local port."
        ) from None
