"""Show package-level change impact without running project code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from blueprint_ai.discovery.project import _git_changed_files, _git_files, _git_run
from blueprint_ai.safety import read_text_bounded, safe_regular_file

MAX_PACKAGES = 256
MAX_CHANGED = 10_000
MAX_MANIFEST_BYTES = 256_000
SHARED_FILES = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "turbo.json",
    "nx.json",
}
DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


class WorkspacePackage(TypedDict):
    path: str
    dependencies: set[str]
    test_script: str | None


def _manifest(path: Path, root: Path) -> dict[str, Any]:
    if not safe_regular_file(path, root):
        raise ValueError(f"package manifest is missing or unsafe: {path.relative_to(root)}")
    try:
        value = json.loads(read_text_bounded(path, MAX_MANIFEST_BYTES, root=root))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"package manifest cannot be read: {path.relative_to(root)}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"package manifest must be an object: {path.relative_to(root)}")
    return value


def _patterns(root_manifest: dict[str, Any]) -> list[str]:
    workspaces = root_manifest.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list) or not workspaces:
        raise ValueError("root package.json must declare npm workspace package patterns")
    result = []
    for pattern in workspaces:
        if (
            not isinstance(pattern, str)
            or not pattern
            or pattern.startswith(("/", "!"))
            or any(part in {"", ".", "..", "**"} for part in pattern.split("/"))
            or any(character in pattern for character in "[]{}\\")
        ):
            raise ValueError("workspace pattern is unsupported; no impact claim was made")
        result.append(pattern.rstrip("/"))
    return result


def _packages(root: Path) -> dict[str, WorkspacePackage]:
    found = set()
    for pattern in _patterns(_manifest(root / "package.json", root)):
        for path in root.glob(f"{pattern}/package.json"):
            if not safe_regular_file(path, root):
                raise ValueError(f"workspace manifest is unsafe: {path.relative_to(root)}")
            found.add(path)
            if len(found) > MAX_PACKAGES:
                raise ValueError("workspace has too many packages for bounded impact analysis")
    if not found:
        raise ValueError("workspace patterns matched no package manifests")
    packages: dict[str, WorkspacePackage] = {}
    for path in sorted(found):
        data = _manifest(path, root)
        name = data.get("name")
        if not isinstance(name, str) or not name or name in packages:
            raise ValueError("workspace package name is missing or duplicated")
        dependencies: set[str] = set()
        for field in DEPENDENCY_FIELDS:
            value = data.get(field, {})
            if not isinstance(value, dict):
                raise ValueError(f"{name} has invalid {field}")
            dependencies.update(value)
        scripts = data.get("scripts")
        test_script = scripts.get("test") if isinstance(scripts, dict) else None
        packages[name] = {
            "path": path.parent.relative_to(root).as_posix(),
            "dependencies": dependencies,
            "test_script": test_script if isinstance(test_script, str) else None,
        }
    for package in packages.values():
        package["dependencies"] &= packages.keys()
    return packages


def _test_files(files: list[str], package_path: str, nested_paths: list[str]) -> list[str]:
    prefix = package_path + "/"
    candidates = []
    for path in files:
        if not path.startswith(prefix) or any(
            path.startswith(nested + "/") for nested in nested_paths
        ):
            continue
        name = Path(path).name.lower()
        if (
            name.startswith("test_")
            or ".test." in name
            or ".spec." in name
            or "/tests/" in f"/{path}/"
            or "/test/" in f"/{path}/"
        ):
            candidates.append(path)
    return sorted(candidates)


def analyze_impact(root: Path, base_ref: str = "HEAD") -> dict[str, Any]:
    """List changed npm workspace packages and reverse dependents as check candidates."""
    root = root.resolve()
    tracked = _git_files(root)
    if tracked is None:
        raise ValueError("impact analysis needs this exact Git repository root")
    packages = _packages(root)
    changed = set(_git_changed_files(root, base_ref))
    removed = _git_run(
        root,
        "diff",
        "--no-ext-diff",
        "--name-only",
        "-z",
        "--diff-filter=D",
        "--end-of-options",
        base_ref,
        "--",
    )
    if removed.returncode != 0 or removed.output_truncated or removed.timed_out:
        raise ValueError("removed-file inventory incomplete; verify the Git comparison ref")
    changed.update(item.decode(errors="replace") for item in removed.stdout.split(b"\0") if item)
    changed_paths = sorted(changed)
    if len(changed_paths) > MAX_CHANGED:
        raise ValueError("change inventory exceeds the bounded impact limit")
    ordered_paths = sorted(packages, key=lambda name: len(packages[name]["path"]), reverse=True)
    contained = {
        name: {
            child
            for child in packages
            if child != name and packages[child]["path"].startswith(package["path"] + "/")
        }
        for name, package in packages.items()
    }
    direct: dict[str, set[str]] = {name: set() for name in packages}
    unmapped = []
    global_files = []
    for path in changed_paths:
        if path in SHARED_FILES or path.startswith((".github/workflows/", "scripts/", "tools/")):
            global_files.append(path)
            continue
        owner = next(
            (name for name in ordered_paths if path.startswith(packages[name]["path"] + "/")), None
        )
        if owner is None:
            unmapped.append(path)
        else:
            direct[owner].add(path)

    reasons: dict[str, set[str]] = {name: set() for name in packages}
    for name, paths in direct.items():
        if paths:
            reasons[name].add("changed")
    if global_files:
        for name in packages:
            reasons[name].add("shared-config")
    frontier = {name for name, paths in direct.items() if paths}
    visited = set(frontier)
    while frontier:
        following = set()
        for name, package in packages.items():
            if name in visited:
                continue
            if package["dependencies"] & frontier:
                reasons[name].add("depends-on-workspace")
                following.add(name)
            if contained[name] & frontier:
                reasons[name].add("contains-workspace")
                following.add(name)
        visited.update(following)
        frontier = following

    impacted = []
    for name in sorted(packages):
        if not reasons[name]:
            continue
        package = packages[name]
        tests = _test_files(
            tracked, package["path"], [packages[child]["path"] for child in contained[name]]
        )
        impacted.append(
            {
                "name": name,
                "path": package["path"],
                "reasons": sorted(reasons[name]),
                "changed_files": sorted(direct[name]),
                "internal_dependencies": sorted(package["dependencies"]),
                "test_script": package["test_script"],
                "candidate_test_count": len(tests),
                "sample_test_files": tests[:20],
            }
        )
    return {
        "schema": "blueprint.impact.v1",
        "base_ref": base_ref,
        "changed_files": changed_paths,
        "shared_config_files": sorted(global_files),
        "unmapped_changed_files": sorted(unmapped),
        "packages": impacted,
        "status": "partial" if unmapped else "mapped",
        "qualification": (
            "Package-level candidates from Git changes and declared npm workspace "
            "dependencies. Test files and scripts were not run or proven to cover "
            "the change; unmapped files need review."
        ),
    }
