"""Verify one immutable Blueprint AI release from PyPI and its downloaded bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


def _canonicalize_name(name: str) -> str:
    """Apply the PyPA name normalization rule without a runtime dependency."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _checksums(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})\s+\*?(?:\./)?([^/]+)", line)
        if match:
            values[match.group(2)] = match.group(1)
    if not values:
        raise ValueError("SHA256SUMS contains no release assets")
    return values


def _read_json(url: str, retries: int, delay: float) -> dict:
    error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = json.load(response)
            if isinstance(payload, dict):
                return payload
            error = ValueError("registry response is not an object")
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            error = exc
        if attempt + 1 < retries:
            time.sleep(delay)
    raise ValueError(f"PyPI metadata unavailable after {retries} attempts: {error}")


def verify_pypi(
    package: str,
    version: str,
    checksum_path: Path,
    retries: int,
    delay: float,
) -> dict[str, object]:
    expected_all = _checksums(checksum_path)
    expected_names = {
        f"blueprint_ai_cli-{version}-py3-none-any.whl",
        f"blueprint_ai_cli-{version}.tar.gz",
    }
    expected = {name: expected_all[name] for name in expected_names if name in expected_all}
    if set(expected) != expected_names:
        raise ValueError("hosted checksums do not identify the expected wheel and source archive")

    endpoint = f"https://pypi.org/pypi/{package}/{version}/json"
    document = _read_json(endpoint, retries, delay)
    info = document.get("info", {})
    if _canonicalize_name(str(info.get("name", ""))) != _canonicalize_name(package):
        raise ValueError("PyPI project name does not match")
    if info.get("version") != version or info.get("requires_python") != ">=3.12":
        raise ValueError("PyPI version or Python requirement does not match release metadata")
    expected_urls = {
        "Changelog": "https://github.com/pradeeptathineni/blueprint-ai/blob/main/CHANGELOG.md",
        "Documentation": "https://github.com/pradeeptathineni/blueprint-ai#readme",
        "Issues": "https://github.com/pradeeptathineni/blueprint-ai/issues",
        "Repository": "https://github.com/pradeeptathineni/blueprint-ai",
        "Release validation": (
            "https://github.com/pradeeptathineni/blueprint-ai/blob/"
            f"{version}/docs/release-validation-{version}.md"
        ),
    }
    if info.get("project_urls") != expected_urls:
        raise ValueError("PyPI project URLs do not match the release contract")

    rows = document.get("urls")
    if not isinstance(rows, list):
        raise ValueError("PyPI release file metadata is missing")
    by_name: dict[str, dict] = {
        row["filename"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("filename"), str)
    }
    if set(by_name) != expected_names:
        raise ValueError(f"PyPI release membership differs: {sorted(by_name)}")
    downloaded: dict[str, str] = {}
    for name in sorted(expected_names):
        row = by_name[name]
        digest = row.get("digests", {}).get("sha256")
        if digest != expected[name] or row.get("yanked") is not False:
            raise ValueError(f"PyPI metadata for {name} has an invalid hash or yanked state")
        url = row.get("url")
        if not isinstance(url, str) or not url.startswith("https://files.pythonhosted.org/"):
            raise ValueError(f"PyPI file URL for {name} is not canonical")
        hasher = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                hasher.update(chunk)
        downloaded[name] = hasher.hexdigest()
        if downloaded[name] != expected[name]:
            raise ValueError(f"downloaded PyPI bytes for {name} do not match GitHub checksums")
    return {
        "package": package,
        "version": version,
        "files": downloaded,
        "registry": endpoint,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", default="blueprint-ai-cli")
    parser.add_argument("--version", required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--delay", type=float, default=5)
    args = parser.parse_args()
    print(
        json.dumps(
            verify_pypi(args.package, args.version, args.checksums, args.retries, args.delay),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
