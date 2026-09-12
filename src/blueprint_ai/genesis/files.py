"""Shared atomic asset writers for native family recipes and publication receipts."""

from __future__ import annotations

import json
from pathlib import Path

from blueprint_ai.safety import atomic_write_text


def write(root: Path, rel: str, text: str) -> None:
    atomic_write_text(root / rel, text, root=root)


def json_file(root: Path, rel: str, data: object) -> None:
    write(root, rel, json.dumps(data, indent=2) + "\n")
