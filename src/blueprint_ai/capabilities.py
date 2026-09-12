"""Reviewable capability plans using the existing create-only transaction engine."""

from __future__ import annotations

import hashlib
from pathlib import Path

from blueprint_ai.core import ProjectFacts
from blueprint_ai.remediation import (
    KITS,
    ApplyResult,
    _kit_configuration_conflicts,
    _safe_target,
    apply_kit,
)
from blueprint_ai.safety import read_text_bounded
from blueprint_ai.sandbox import SandboxPolicy


def plan_add(root: Path, name: str) -> dict:
    if name not in KITS:
        raise ValueError(f"unknown capability: {name}")
    kit = KITS[name]
    files = []
    for rel, content in kit.files.items():
        target = _safe_target(root, rel)
        digest = hashlib.sha256(content.encode()).hexdigest()
        status = "create"
        if target is None:
            status = "conflict"
        elif target.exists():
            try:
                status = (
                    "already-present"
                    if read_text_bounded(target, 2_000_000, root=root) == content
                    else "conflict"
                )
            except (OSError, ValueError, UnicodeError):
                status = "conflict"
        files.append({"path": rel, "sha256": digest, "content": content, "status": status})
    return {
        "capability": name,
        "version": kit.version,
        "mode": "create-only",
        "files": files,
        "conflicts": [row["path"] for row in files if row["status"] == "conflict"]
        + _kit_configuration_conflicts(root, kit),
        "verification": kit.verification,
        "execution": "sandbox by default; host requires explicit trust",
    }


def add_capability(
    root: Path, facts: ProjectFacts, name: str, *, policy: SandboxPolicy | None = None
) -> ApplyResult:
    plan = plan_add(root, name)
    if plan["conflicts"]:
        raise ValueError(
            "capability conflicts with existing paths: " + ", ".join(plan["conflicts"])
        )
    return apply_kit(root, facts, name, sandbox=policy)
