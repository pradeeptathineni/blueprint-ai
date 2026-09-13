"""Verify that a release SBOM describes only a clean installed runtime environment."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from packaging.utils import canonicalize_name

DEV_ONLY = {
    "coverage",
    "mypy",
    "pip-audit",
    "pytest",
    "pytest-cov",
    "ruff",
    "twine",
}


def _inventory(path: Path) -> dict[str, str]:
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError("runtime inventory must be a JSON list")
    inventory: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("runtime inventory contains an invalid package row")
        version = row.get("version")
        if not isinstance(version, str) or not version:
            raise ValueError("runtime inventory package version is missing")
        inventory[canonicalize_name(row["name"])] = version
    return inventory


def verify_sbom(
    sbom_path: Path,
    inventory_path: Path,
    package: str,
    version: str,
    forbidden_paths: list[str],
) -> dict[str, object]:
    raw = sbom_path.read_text()
    for forbidden in forbidden_paths:
        if forbidden and forbidden in raw:
            raise ValueError(f"SBOM contains forbidden checkout path: {forbidden}")
    document = json.loads(raw)
    if document.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("release SBOM must use SPDX 2.3")
    creators = document.get("creationInfo", {}).get("creators", [])
    if not any(isinstance(value, str) and "syft" in value.lower() for value in creators):
        raise ValueError("release SBOM does not identify the Syft generator")
    describes = document.get("documentDescribes")
    relationships = document.get("relationships", [])
    relationship_describes = any(
        isinstance(row, dict)
        and row.get("spdxElementId") == document.get("SPDXID", "SPDXRef-DOCUMENT")
        and row.get("relationshipType") == "DESCRIBES"
        and isinstance(row.get("relatedSpdxElement"), str)
        for row in relationships
    )
    if not document.get("documentNamespace") or not (describes or relationship_describes):
        raise ValueError("release SBOM is missing its document identity")

    expected = _inventory(inventory_path)
    observed: dict[str, str] = {}
    for row in document.get("packages", []):
        if not isinstance(row, dict):
            raise ValueError("release SBOM contains an invalid package")
        name = row.get("name")
        package_version = row.get("versionInfo")
        if isinstance(name, str) and isinstance(package_version, str) and package_version:
            observed[canonicalize_name(name)] = package_version
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    mismatched = sorted(
        name for name in expected.keys() & observed.keys() if expected[name] != observed[name]
    )
    if missing or unexpected or mismatched:
        raise ValueError(
            "SBOM/runtime inventory mismatch: "
            f"missing={missing}, unexpected={unexpected}, versions={mismatched}"
        )
    canonical_package = canonicalize_name(package)
    if observed.get(canonical_package) != version:
        raise ValueError(f"SBOM does not describe {package} {version}")
    leaked_dev = sorted(DEV_ONLY & set(observed))
    if leaked_dev:
        raise ValueError(
            "release SBOM contains development-only packages: " + ", ".join(leaked_dev)
        )
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("release version must be semantic")
    return {
        "package": package,
        "version": version,
        "runtime_packages": len(observed),
        "generator": next(value for value in creators if "syft" in value.lower()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--forbid-path", action="append", default=[])
    args = parser.parse_args()
    print(
        json.dumps(
            verify_sbom(
                args.sbom,
                args.inventory,
                args.package,
                args.version,
                args.forbid_path,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
