"""Read a bounded uv workspace graph without resolving or importing project code."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import InvalidName, canonicalize_name

from blueprint_ai.safety import read_text_bounded

if TYPE_CHECKING:
    from blueprint_ai.impact import WorkspacePackage


def _table(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a TOML table")
    return value


def _patterns(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of workspace patterns")
    for pattern in value:
        if (
            not isinstance(pattern, str)
            or not pattern
            or len(pattern) > 512
            or pattern.startswith(("/", "!"))
            or any(part in {"", ".", "..", "**", ".git"} for part in pattern.split("/"))
            or any(character in pattern for character in "[]{}\\")
            or any(ord(character) < 32 for character in pattern)
        ):
            raise ValueError(f"{label} has an unsupported workspace pattern")
    return value


def _read(path: Path, root: Path, limit: int) -> dict[str, Any]:
    try:
        return tomllib.loads(read_text_bounded(path, limit, root=root))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(
            f"workspace manifest cannot be read safely: {path.relative_to(root)}"
        ) from exc


def _dependencies(data: dict[str, Any]) -> set[str]:
    project = _table(data["project"], "project")
    groups = _table(data.get("dependency-groups", {}), "dependency-groups")
    normalized_groups = set()
    for name in groups:
        try:
            normalized = canonicalize_name(name, validate=True)
        except InvalidName as exc:
            raise ValueError("dependency group name is invalid") from exc
        if normalized in normalized_groups:
            raise ValueError("dependency group names collide after normalization")
        normalized_groups.add(normalized)
    optional = _table(project.get("optional-dependencies", {}), "project.optional-dependencies")
    build = _table(data.get("build-system", {}), "build-system")
    uv = _table(_table(data.get("tool", {}), "tool").get("uv", {}), "tool.uv")
    lists = [
        project.get("dependencies", []),
        build.get("requires", []),
        uv.get("dev-dependencies", []),
        *optional.values(),
    ]
    # Union all groups rather than interpreting the caller's active group/marker environment.
    for group in groups.values():
        if not isinstance(group, list):
            raise ValueError("dependency groups must contain requirement lists")
        for item in group:
            if isinstance(item, dict):
                if (
                    set(item) != {"include-group"}
                    or not isinstance(item["include-group"], str)
                    or canonicalize_name(item["include-group"]) not in normalized_groups
                ):
                    raise ValueError("dependency group inclusion is invalid")
            else:
                lists.append([item])
    dependencies: set[str] = set()
    for requirements in lists:
        if not isinstance(requirements, list):
            raise ValueError("Python dependencies must be requirement lists")
        for requirement in requirements:
            if not isinstance(requirement, str):
                raise ValueError("Python dependencies must be requirement strings")
            try:
                dependencies.add(canonicalize_name(Requirement(requirement).name))
            except InvalidRequirement as exc:
                raise ValueError("Python dependency declaration cannot be parsed") from exc
    return dependencies


def _members(root: Path, patterns: list[str], excluded: list[str]) -> Iterator[Path]:
    scanned = 0
    for pattern in patterns:
        parents = [root]
        parts = pattern.split("/")
        for index, part in enumerate(parts):
            matched = []
            for parent in parents:
                try:
                    with os.scandir(parent) as entries:
                        for entry in entries:
                            scanned += 1
                            if scanned > 10_000:
                                raise ValueError(
                                    "workspace member discovery exceeds the bounded impact limit"
                                )
                            if not fnmatchcase(entry.name, part):
                                continue
                            directory = Path(entry.path)
                            relative = directory.relative_to(root).as_posix()
                            if index == len(parts) - 1 and any(
                                PurePosixPath("/" + relative).match("/" + rule) for rule in excluded
                            ):
                                continue
                            # Reject aliases before traversing another pattern component
                            if entry.is_symlink() or entry.name.casefold() == ".git":
                                raise ValueError(
                                    "workspace member crosses a symlink or Git directory"
                                )
                            if entry.is_dir(follow_symlinks=False):
                                matched.append(directory)
                except OSError as exc:
                    raise ValueError("workspace member directory cannot be read safely") from exc
            parents = matched
        yield from parents


def uv_packages(
    root: Path, *, max_packages: int, max_manifest_bytes: int
) -> tuple[dict[str, WorkspacePackage], list[str]]:
    """Read local members and a conservative union of their static dependency names."""
    manifest = root / "pyproject.toml"
    data = _read(manifest, root, max_manifest_bytes)
    uv = _table(_table(data.get("tool", {}), "tool").get("uv", {}), "tool.uv")
    workspace = _table(uv.get("workspace"), "tool.uv.workspace")
    members = _patterns(workspace.get("members"), "tool.uv.workspace.members")
    excluded = _patterns(workspace.get("exclude", []), "tool.uv.workspace.exclude")
    found = {manifest} if "project" in data else set()
    for directory in _members(root, members, excluded):
        found.add(directory / "pyproject.toml")
        if len(found) > max_packages:
            raise ValueError("workspace has too many packages for bounded impact analysis")
    if not found:
        raise ValueError("workspace patterns matched no project manifests")
    packages: dict[str, WorkspacePackage] = {}
    unresolved = []
    for path in sorted(found):
        current = data if path == manifest else _read(path, root, max_manifest_bytes)
        project = _table(current.get("project"), "workspace project")
        name = project.get("name")
        if not isinstance(name, str):
            raise ValueError("workspace project name is missing")
        try:
            name = canonicalize_name(name, validate=True)
        except InvalidName as exc:
            raise ValueError("workspace project name is invalid") from exc
        if name in packages:
            raise ValueError("workspace project names collide after Python name normalization")
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic, list) or any(not isinstance(item, str) for item in dynamic):
            raise ValueError("project.dynamic must be a list of field names")
        if {"dependencies", "optional-dependencies"} & set(dynamic):
            unresolved.append(name)
        packages[name] = {
            "path": path.parent.relative_to(root).as_posix(),
            "dependencies": _dependencies(current),
            "test_script": None,
        }
    for package in packages.values():
        package["dependencies"] &= packages.keys()
    return packages, sorted(unresolved)
