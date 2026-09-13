"""Generate and enforce the Blueprint AI 1.x public compatibility baseline."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blueprint_ai.config import Settings
from blueprint_ai.core import Applicability, BlueprintResult, Finding, ProjectFacts, RunReport
from blueprint_ai.core.project import ProjectGraph
from blueprint_ai.evolution.models import (
    AuthoritativeToolContract,
    EvolutionPlan,
    EvolutionReport,
    ResidualContract,
    TransformationSpec,
    TypedPostcondition,
)
from blueprint_ai.extensions import custom_blueprint_schema
from blueprint_ai.genesis.models import GenesisPlanPreview, IntentSpec
from blueprint_ai.output import serialize_report
from blueprint_ai.sandbox import SandboxPolicy

FIXTURE = Path(__file__).parents[1] / "tests/fixtures/compatibility/contract-v1.json"
PUBLIC_MODULES = (
    "blueprint_ai.adapters",
    "blueprint_ai.blueprints",
    "blueprint_ai.core",
    "blueprint_ai.discovery",
    "blueprint_ai.evolution",
    "blueprint_ai.genesis",
    "blueprint_ai.model",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if value is inspect.Parameter.empty:
        return {"required": True}
    try:
        return json.loads(json.dumps(value))
    except TypeError:
        return repr(value)


def _signature(value: Any) -> dict[str, dict[str, Any]]:
    try:
        parameters = inspect.signature(value).parameters.values()
    except (TypeError, ValueError):
        return {}
    return {
        parameter.name: {
            "kind": parameter.kind.name,
            "default": _json_default(parameter.default),
        }
        for parameter in parameters
    }


def _public_python() -> dict[str, dict[str, dict[str, Any]]]:
    modules: dict[str, dict[str, dict[str, Any]]] = {}
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        exports: dict[str, dict[str, Any]] = {}
        for name in sorted(module.__all__):
            value = getattr(module, name)
            kind = "class" if inspect.isclass(value) else "function" if callable(value) else "data"
            item: dict[str, Any] = {"kind": kind}
            if inspect.isfunction(value):
                item["signature"] = _signature(value)
            if inspect.isclass(value):
                methods = {
                    method_name: _signature(method)
                    for method_name, method in vars(value).items()
                    if not method_name.startswith("_") and callable(method)
                }
                if methods:
                    item["methods"] = methods
            exports[name] = item
        modules[module_name] = exports
    return modules


def _cli_surface() -> dict[str, Any]:
    from typer.main import get_command

    from blueprint_ai.cli import app

    def commands(group: Any, prefix: str = "") -> dict[str, Any]:
        rows: dict[str, Any] = {}
        for name, command in sorted(group.commands.items()):
            path = f"{prefix} {name}".strip()
            parameters = {}
            for parameter in command.params:
                parameters[parameter.name] = {
                    "opts": sorted(
                        [
                            *getattr(parameter, "opts", []),
                            *getattr(parameter, "secondary_opts", []),
                        ]
                    ),
                    "required": parameter.required,
                    "multiple": getattr(parameter, "multiple", False),
                    "is_flag": getattr(parameter, "is_flag", None),
                    "default": _json_default(parameter.default),
                }
            rows[path] = {"parameters": parameters}
            if hasattr(command, "commands"):
                rows.update(commands(command, path))
        return rows

    root = get_command(app)
    return {
        "root_options": sorted(
            option
            for parameter in root.params
            for option in [
                *getattr(parameter, "opts", []),
                *getattr(parameter, "secondary_opts", []),
            ]
        ),
        "commands": commands(root),
    }


def _schemas() -> dict[str, Any]:
    return {
        "settings": Settings.model_json_schema(),
        "report": RunReport.model_json_schema(mode="serialization"),
        "custom-blueprint": custom_blueprint_schema(),
        "intent": IntentSpec.model_json_schema(),
        "genesis-plan": GenesisPlanPreview.model_json_schema(),
        "project-graph": ProjectGraph.model_json_schema(),
        "sandbox-policy": SandboxPolicy.model_json_schema(),
        "evolution-plan": EvolutionPlan.model_json_schema(),
        "evolution-report": EvolutionReport.model_json_schema(),
        "transformation": TransformationSpec.model_json_schema(),
        "authoritative-tool": AuthoritativeToolContract.model_json_schema(),
        "migration-postcondition": TypedPostcondition.model_json_schema(),
        "residual-contract": ResidualContract.model_json_schema(),
    }


def _report_formats() -> dict[str, Any]:
    finding = Finding(
        blueprint="compatibility",
        category="fixture",
        rule_id="compatibility/fixture",
        source="fixture",
        severity="low",
        priority="P3",
        message="Stable fixture finding.",
        recommendation="Keep the contract compatible.",
    )
    report = RunReport(
        facts=ProjectFacts(path=".", name="compatibility-fixture"),
        profile="compatibility",
        results=[
            BlueprintResult(
                blueprint="compatibility",
                status="findings",
                findings=[finding],
                applicability=Applicability(state="applicable", reason="fixture"),
            )
        ],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return {
        "json": json.loads(serialize_report(report, "json")),
        "sarif": json.loads(serialize_report(report, "sarif")),
        "junit": serialize_report(report, "junit"),
    }


def snapshot() -> dict[str, Any]:
    return {
        "contract": "1.x",
        "cli": _cli_surface(),
        "public_python": _public_python(),
        "schemas": _schemas(),
        "settings_defaults": Settings().model_dump(mode="json"),
        "report_formats": _report_formats(),
    }


def _schema_compatible(old: Any, current: Any, path: str) -> None:
    if isinstance(old, dict):
        if not isinstance(current, dict):
            raise AssertionError(f"{path} changed object type")
        stable_keywords = {
            "$ref",
            "additionalProperties",
            "const",
            "format",
            "maxItems",
            "maximum",
            "minItems",
            "minimum",
            "pattern",
            "type",
        }
        for key in stable_keywords & old.keys():
            if current.get(key) != old[key]:
                raise AssertionError(f"{path}.{key} changed")
        if "enum" in old and not set(old["enum"]).issubset(set(current.get("enum", []))):
            raise AssertionError(f"{path}.enum was narrowed")
        if "required" in old and set(current.get("required", [])) != set(old["required"]):
            raise AssertionError(f"{path}.required changed")
        for key in ("$defs", "properties"):
            if key in old:
                if not isinstance(current.get(key), dict):
                    raise AssertionError(f"{path}.{key} was removed")
                for name, value in old[key].items():
                    if name not in current[key]:
                        raise AssertionError(f"{path}.{key}.{name} was removed")
                    _schema_compatible(value, current[key][name], f"{path}.{key}.{name}")
        for key in ("items", "anyOf", "oneOf", "allOf"):
            if key in old:
                _schema_compatible(old[key], current.get(key), f"{path}.{key}")
    elif isinstance(old, list):
        if not isinstance(current, list) or len(current) < len(old):
            raise AssertionError(f"{path} was shortened")
        for index, value in enumerate(old):
            _schema_compatible(value, current[index], f"{path}[{index}]")


def _mapping_subset(old: Any, current: Any, path: str) -> None:
    if isinstance(old, dict):
        if not isinstance(current, dict):
            raise AssertionError(f"{path} changed object type")
        for key, value in old.items():
            if key not in current:
                raise AssertionError(f"{path}.{key} was removed")
            _mapping_subset(value, current[key], f"{path}.{key}")
    elif isinstance(old, list):
        if not isinstance(current, list) or len(current) < len(old):
            raise AssertionError(f"{path} was shortened")
        for index, value in enumerate(old):
            _mapping_subset(value, current[index], f"{path}[{index}]")
    elif old != current:
        raise AssertionError(f"{path} changed from {old!r} to {current!r}")


def assert_compatible(fixture: Path = FIXTURE) -> None:
    old = json.loads(fixture.read_text())
    current = snapshot()
    _mapping_subset(old["cli"], current["cli"], "cli")
    _mapping_subset(old["public_python"], current["public_python"], "public_python")
    _mapping_subset(old["settings_defaults"], current["settings_defaults"], "settings_defaults")
    _mapping_subset(old["report_formats"], current["report_formats"], "report_formats")
    if set(old["schemas"]) != set(current["schemas"]):
        raise AssertionError("schema command names changed")
    for name, schema in old["schemas"].items():
        _schema_compatible(schema, current["schemas"][name], f"schemas.{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n")
        print(FIXTURE)
    else:
        assert_compatible()
        print(json.dumps({"contract": "1.x", "fixture": str(FIXTURE), "status": "passed"}))


if __name__ == "__main__":
    main()
