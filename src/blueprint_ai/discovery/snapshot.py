"""Exact, bounded hashes over paths admitted by Blueprint's native inventory."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from blueprint_ai.core import ProjectFacts
from blueprint_ai.discovery.project import discover_project
from blueprint_ai.safety import read_bytes_bounded

MAX_SNAPSHOT_FILES = 50_000
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024


def project_source_snapshot(
    root: Path,
    facts: ProjectFacts | None = None,
    *,
    evidence_paths: Iterable[str] = (),
) -> dict:
    """Hash the native inventory without executing project files.

    The aggregate digest covers every admitted path when complete. `files` contains
    hashes only for explicitly requested finding paths, keeping retained reports
    bounded even for large repositories.
    """
    root = root.expanduser().resolve(strict=True)
    facts = facts or discover_project(root)
    if Path(facts.path).resolve() != root:
        raise ValueError("source snapshot facts belong to another project")
    paths = sorted(facts.graph.file_scopes)
    requested = set(evidence_paths)
    known = set(paths)
    unknown_requested = sorted(requested - known)
    result: dict[str, str] = {}
    diagnostics: list[str] = []
    digest = hashlib.sha256()
    digest.update(b"blueprint-ai/project-source-snapshot/v1\0")
    total = 0
    hashed = 0
    complete = True
    for rel in paths:
        if hashed >= MAX_SNAPSHOT_FILES:
            complete = False
            diagnostics.append(
                f"file-count cap reached; {len(paths) - hashed} inventoried paths were not hashed"
            )
            break
        try:
            data = read_bytes_bounded(
                root / rel, MAX_SNAPSHOT_FILE_BYTES, root=root
            )
            if total + len(data) > MAX_SNAPSHOT_BYTES:
                complete = False
                diagnostics.append(
                    f"byte cap reached before {len(paths) - hashed} remaining inventoried paths"
                )
                break
        except (OSError, ValueError) as exc:
            complete = False
            if len(diagnostics) < 100:
                diagnostics.append(f"{rel}: source bytes unavailable ({type(exc).__name__})")
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0unknown\0")
            hashed += 1
            continue
        file_digest = hashlib.sha256(data).hexdigest()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_digest))
        if rel in requested:
            result[rel] = file_digest
        total += len(data)
        hashed += 1
    if unknown_requested:
        complete = False
        diagnostics.append(
            f"{len(unknown_requested)} requested evidence path(s) are outside the native inventory"
        )
    return {
        "schema": "blueprint-ai/project-source-snapshot/v1",
        "source_digest": digest.hexdigest(),
        "complete": complete and hashed == len(paths),
        "file_count": len(paths),
        "hashed_count": hashed,
        "bytes_hashed": total,
        "files": result,
        "diagnostics": diagnostics,
    }
