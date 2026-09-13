"""Verify release archives agree with source identity and contain only portable files."""

from __future__ import annotations

import argparse
import email.parser
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

NORMALIZE = re.compile(r"[-_.]+")
VERSION_ASSIGNMENT = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']$', re.MULTILINE)
FORBIDDEN_PARTS = {".DS_Store", ".git", ".pytest_cache", "__pycache__"}
PROJECT_URLS = {
    "Changelog, https://github.com/pradeeptathineni/blueprint-ai/blob/main/CHANGELOG.md",
    "Documentation, https://github.com/pradeeptathineni/blueprint-ai#readme",
    "Issues, https://github.com/pradeeptathineni/blueprint-ai/issues",
    "Repository, https://github.com/pradeeptathineni/blueprint-ai",
}


def normalized(name: str, separator: str = "-") -> str:
    return NORMALIZE.sub(separator, name).lower()


def metadata_fields(text: str) -> dict[str, object]:
    message = email.parser.Parser().parsestr(text)
    return {
        "Name": str(message["Name"]),
        "Version": str(message["Version"]),
        "Requires-Python": str(message["Requires-Python"]),
        "License-Expression": str(message["License-Expression"]),
        "Project-URL": set(message.get_all("Project-URL", [])),
    }


def expected_metadata(project_name: str, version: str) -> dict[str, object]:
    return {
        "Name": project_name,
        "Version": version,
        "Requires-Python": ">=3.12",
        "License-Expression": "MIT",
        "Project-URL": {
            *PROJECT_URLS,
            (
                "Release validation, https://github.com/pradeeptathineni/blueprint-ai/"
                f"blob/{version}/docs/release-validation-{version}.md"
            ),
        },
    }


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or FORBIDDEN_PARTS.intersection(path.parts):
        raise ValueError(f"unsafe or development-only archive member: {name}")
    return path


def verify_wheel(path: Path, project_name: str, version: str) -> None:
    expected_stem = f"{normalized(project_name, '_')}-{version}"
    if path.name != f"{expected_stem}-py3-none-any.whl":
        raise ValueError(f"unexpected wheel filename: {path.name}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            safe_member(name)
        allowed_roots = {"blueprint_ai", f"{expected_stem}.dist-info"}
        extra_roots = {PurePosixPath(name).parts[0] for name in names} - allowed_roots
        if extra_roots:
            raise ValueError(f"wheel contains unnecessary top-level paths: {sorted(extra_roots)}")
        metadata_name = f"{expected_stem}.dist-info/METADATA"
        entry_points_name = f"{expected_stem}.dist-info/entry_points.txt"
        license_name = f"{expected_stem}.dist-info/licenses/LICENSE"
        fields = metadata_fields(archive.read(metadata_name).decode())
        entry_points = archive.read(entry_points_name).decode()
        archive.getinfo(license_name)
    if fields != expected_metadata(project_name, version):
        raise ValueError(f"wheel metadata mismatch: {fields}")
    if "blueprint-ai = blueprint_ai.cli:app" not in entry_points:
        raise ValueError("wheel does not install the blueprint-ai executable")


def verify_sdist(path: Path, project_name: str, version: str) -> None:
    expected_stem = f"{normalized(project_name, '_')}-{version}"
    if path.name != f"{expected_stem}.tar.gz":
        raise ValueError(f"unexpected sdist filename: {path.name}")
    prefix = f"{expected_stem}/"
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        for member in members:
            safe_member(member.name)
            if not member.name.startswith(prefix) or not (member.isfile() or member.isdir()):
                raise ValueError(f"unexpected sdist member: {member.name}")
        metadata = archive.extractfile(f"{prefix}PKG-INFO")
        if metadata is None:
            raise ValueError("sdist has no PKG-INFO")
        fields = metadata_fields(metadata.read().decode())
    required = {f"{prefix}LICENSE", f"{prefix}README.md", f"{prefix}pyproject.toml"}
    has_sources = any(name.startswith(f"{prefix}src/blueprint_ai/") for name in names)
    if not required.issubset(names) or not has_sources:
        raise ValueError("sdist is missing required package sources or metadata")
    # Hatch always includes the root VCS ignore file in standards-compliant sdists.
    allowed_roots = {".gitignore", "LICENSE", "PKG-INFO", "README.md", "pyproject.toml", "src"}
    extra_roots = {
        PurePosixPath(name.removeprefix(prefix)).parts[0]
        for name in names
        if name != expected_stem and name.removeprefix(prefix)
    } - allowed_roots
    if extra_roots:
        raise ValueError(f"sdist contains unnecessary top-level paths: {sorted(extra_roots)}")
    if fields != expected_metadata(project_name, version):
        raise ValueError(f"sdist metadata mismatch: {fields}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--tag")
    args = parser.parse_args()

    project = tomllib.loads((args.source / "pyproject.toml").read_text())["project"]
    project_name = project["name"]
    version = project["version"]
    source_version_match = VERSION_ASSIGNMENT.search(
        (args.source / "src/blueprint_ai/__init__.py").read_text()
    )
    source_version = source_version_match.group(1) if source_version_match else None
    if project_name != "blueprint-ai-cli" or version != source_version:
        raise ValueError(
            f"source identity mismatch: distribution={project_name!r}, "
            f"metadata={version!r}, import={source_version!r}"
        )
    if args.tag is not None and args.tag != version:
        raise ValueError(f"release tag {args.tag!r} does not match source version {version!r}")

    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(f"expected one wheel and one sdist, found {wheels + sdists}")
    verify_wheel(wheels[0], project_name, version)
    verify_sdist(sdists[0], project_name, version)
    print(f"verified {project_name} {version}: {wheels[0].name}, {sdists[0].name}")


if __name__ == "__main__":
    main()
