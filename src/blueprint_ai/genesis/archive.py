"""Bounded official Initializr download and archive unpacking, without remote hooks."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from blueprint_ai.safety import atomic_write_text, run_process_bytes


def unpack_source(data: bytes, root: Path) -> None:
    if len(data) > 2_000_000:
        raise ValueError("provider archive exceeds compressed size limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 256 or sum(info.file_size for info in entries) > 20_000_000:
            raise ValueError("provider archive exceeds entry/expanded-size limit")
        paths: set[str] = set()
        contents: list[tuple[str, str]] = []
        for info in entries:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or ".git" in path.parts
                or not path.parts
                or path.as_posix() in paths
                or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                or info.file_size > 2_000_000
            ):
                raise ValueError("unsafe provider archive entry")
            paths.add(path.as_posix())
            if info.is_dir():
                continue
            content = archive.read(info).decode("utf-8")
            contents.append((path.as_posix(), content))
        for name, _ in contents:
            if any(
                parent.as_posix() in {p for p, _ in contents}
                for parent in PurePosixPath(name).parents
            ):
                raise ValueError("provider archive contains conflicting file/directory paths")
        for name, content in contents:
            atomic_write_text(root / name, content, root=root, overwrite=False)


def initialize_spring(root: Path, package: str, module: str) -> str:
    import hashlib

    params = urlencode(
        {
            "type": "maven-project",
            "language": "java",
            "bootVersion": "4.1.1",
            "baseDir": "",
            "groupId": "com.example",
            "artifactId": package,
            "name": module,
            "packageName": "com.example." + module,
            "packaging": "jar",
            "javaVersion": "21",
            "dependencies": "web,actuator",
        }
    )
    output = run_process_bytes(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--max-time",
            "30",
            "--max-filesize",
            "2000000",
            "https://start.spring.io/starter.zip?" + params,
        ],
        root,
        35,
        output_limit=2_000_000,
    )
    if output.returncode or output.timed_out or output.output_truncated:
        raise ValueError("official Initializr download failed or exceeded bounds")
    unpack_source(output.stdout, root)
    return hashlib.sha256(output.stdout).hexdigest()
