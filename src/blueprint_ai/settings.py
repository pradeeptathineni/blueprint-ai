"""Inspect and edit ordinary project defaults without granting execution authority."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from blueprint_ai.config import Settings, _NoAliasSafeLoader
from blueprint_ai.safety import MAX_CONFIG_BYTES, read_bytes_bounded

CONFIG_FILE = ".blueprint-ai.yml"
CONTROLS = {
    "minimum_severity": (
        "Minimum severity",
        "Reports",
        "Keep findings at this severity or higher.",
    ),
    "maximum_priority": (
        "Maximum priority",
        "Reports",
        "Keep findings at this priority or higher.",
    ),
    "fail_on_priority": (
        "Fail on priority",
        "Reports",
        "Fail a review for new findings at this priority or higher. Disabled skips this check.",
    ),
    "tool_timeout": (
        "Tool timeout",
        "Execution limits",
        "Maximum seconds for each external tool. A tool may impose a shorter limit.",
    ),
    "max_workers": ("Parallel workers", "Execution limits", "Maximum concurrent review workers."),
    "max_tool_output_bytes": (
        "Tool output limit",
        "Execution limits",
        "Maximum captured bytes per external tool.",
    ),
}


class SettingsConflict(ValueError):
    """The saved document changed or another settings writer holds its lock."""


def _validate(raw: dict[str, Any], *, strict: bool = False) -> Settings:
    try:
        return Settings.model_validate(raw, strict=strict)
    except ValidationError as exc:
        # Validation errors may contain privileged field values from an existing document
        fields = sorted(
            {
                str(error["loc"][0])
                if error["loc"][0] in Settings.model_fields
                else "unknown field"
                for error in exc.errors()
                if error["loc"]
            }
        )
        raise ValueError("Invalid project settings: " + ", ".join(fields)) from None


def _parse(data: bytes) -> tuple[dict[str, Any], Settings]:
    try:
        raw = yaml.load(data.decode("utf-8"), Loader=_NoAliasSafeLoader)
    except (UnicodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ValueError(
            "Invalid project settings YAML. Repair the document before editing."
        ) from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Project settings must contain a mapping.")
    return raw, _validate(raw)


def _read(root: Path) -> tuple[bytes | None, dict[str, Any], Settings]:
    path = root / CONFIG_FILE
    # lexists distinguishes a missing document from a dangling symlink
    data = read_bytes_bounded(path, MAX_CONFIG_BYTES, root=root) if os.path.lexists(path) else None
    raw, settings = _parse(data or b"")
    return data, raw, settings


def _revision(data: bytes | None) -> str:
    return "missing" if data is None else hashlib.sha256(data).hexdigest()


def _catalog(data: bytes | None, raw: dict[str, Any], settings: Settings) -> dict[str, Any]:
    defaults = Settings().model_dump(mode="json")
    effective = settings.model_dump(mode="json")
    properties = Settings.model_json_schema()["properties"]
    entries = []
    for key, (label, category, description) in CONTROLS.items():
        entries.append(
            {
                "id": key,
                "label": label,
                "category": category,
                "description": description,
                "default": defaults[key],
                "effective": effective[key],
                "source": "project" if key in raw else "default",
                "scope": "project",
                "editable": True,
                "risk": "preference",
                "restartRequired": False,
                "takesEffect": "next operation",
                "overrides": "--fail-on overrides this value for one command"
                if key == "fail_on_priority"
                else "",
                "constraints": properties[key],
            }
        )
    return {
        "schema": "blueprint.settings.v1",
        "owner": "blueprint-ai",
        "scope": "project",
        "storage": CONFIG_FILE,
        "revision": _revision(data),
        "settings": entries,
        "unmanaged": sorted(set(Settings.model_fields) - set(CONTROLS)),
        "limitations": [
            "Only report filters, CI failure policy and execution limits are editable here.",
            "Other project values remain in YAML. Their values are not returned by this interface.",
            "Model/provider settings, credentials, network authorization and executable trust "
            "keep their existing explicit interfaces.",
            "Settings do not start a review or change an operation already running.",
        ],
    }


def read_settings(root: Path) -> dict[str, Any]:
    """Return public control metadata and effective project defaults, without running tools."""
    result = _catalog(*_read(root))
    result["projectLabel"] = root.resolve().name
    return result


def _render(data: bytes | None, updates: dict[str, Any], resets: list[str]) -> bytes:
    text = (data or b"").decode("utf-8")
    node = yaml.compose(text, Loader=_NoAliasSafeLoader)
    replacements = []
    remaining = dict(updates)
    if node is not None:
        if not isinstance(node, yaml.MappingNode) or node.flow_style:
            raise ValueError("Editing requires a block YAML mapping. Convert the document first.")
        for key, value in node.value:
            name = key.value
            if name in resets:
                replacements.append((key.start_mark.index, value.end_mark.index, ""))
            elif name in remaining:
                replacement = json.dumps(remaining.pop(name), ensure_ascii=True)
                replacements.append((value.start_mark.index, value.end_mark.index, replacement))
    # Replace only selected scalar spans so unrelated values, comments and formatting survive
    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]
    for key, value in remaining.items():
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"{key}: {json.dumps(value, ensure_ascii=True)}\n"
    rendered = text.encode("utf-8")
    if len(rendered) > MAX_CONFIG_BYTES:
        raise ValueError("Updated settings exceed the document size limit.")
    return rendered


def _prepare(
    root: Path, updates: dict[str, Any], resets: list[str], expected_revision: str
) -> tuple[bytes | None, bytes, dict[str, Any]]:
    if not isinstance(updates, dict) or not isinstance(resets, list):
        raise ValueError("Choose a settings mapping and a reset list.")
    if any(not isinstance(key, str) for key in [*updates, *resets]):
        raise ValueError("Setting IDs must be strings.")
    keys = set(updates) | set(resets)
    if not keys or keys - CONTROLS.keys() or set(updates) & set(resets):
        raise ValueError("Choose supported settings once, either set or reset.")
    # New edits are typed, while existing YAML keeps the native loader's accepted coercions
    _validate(updates, strict=True)
    data, raw, settings = _read(root)
    if expected_revision != _revision(data):
        raise SettingsConflict("Settings changed. Reload and review your changes again.")
    proposed = {key: value for key, value in raw.items() if key not in resets}
    proposed.update(updates)
    validated = _validate(proposed)
    rendered = _render(data, updates, resets)
    # A text edit must round-trip to the exact validated meaning before any write
    parsed, parsed_settings = _parse(rendered)
    if parsed != proposed:
        raise ValueError("YAML layout cannot be edited safely. Use a block mapping.")
    result = _catalog(None if data is None and not rendered else rendered, parsed, parsed_settings)
    result["projectLabel"] = root.resolve().name
    result["changes"] = [
        {
            "id": key,
            "before": getattr(settings, key),
            "after": getattr(validated, key),
            "action": "reset" if key in resets else "set",
        }
        for key in sorted(keys)
    ]
    result["saved"] = False
    return data, rendered, result


def preview_settings(
    root: Path, updates: dict[str, Any], resets: list[str], expected_revision: str
) -> dict[str, Any]:
    """Validate a draft and return its effective values without writing it."""
    return _prepare(root, updates, resets, expected_revision)[2]


def write_settings(
    root: Path, updates: dict[str, Any], resets: list[str], expected_revision: str
) -> dict[str, Any]:
    """Save selected defaults atomically after checking the caller's document revision."""
    lock = root / ".blueprint-ai.settings.lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise SettingsConflict(
            "Another settings edit is in progress. Reload before retrying."
        ) from None
    temporary: str | None = None
    try:
        data, rendered, result = _prepare(root, updates, resets, expected_revision)
        if data != rendered and not (data is None and rendered == b""):
            path = root / CONFIG_FILE
            mode = stat.S_IMODE(path.stat().st_mode) & 0o777 if data is not None else 0o600
            descriptor, temporary = tempfile.mkstemp(prefix=".blueprint-settings-", dir=root)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            if _revision(_read(root)[0]) != expected_revision:
                raise SettingsConflict("Settings changed before saving. Reload and review again.")
            os.replace(temporary, path)
            temporary = None
        result["saved"] = True
        return result
    finally:
        if temporary is not None:
            os.unlink(temporary)
        lock.rmdir()
