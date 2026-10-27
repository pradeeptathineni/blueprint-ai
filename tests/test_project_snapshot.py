from __future__ import annotations

import hashlib
from pathlib import Path

from blueprint_ai.discovery import discover_project, project_source_snapshot


def test_native_snapshot_binds_admitted_files_and_detects_change(tmp_path: Path):
    python = tmp_path / "src" / "service.py"
    javascript = tmp_path / "web" / "client.ts"
    python.parent.mkdir(parents=True)
    javascript.parent.mkdir(parents=True)
    python.write_bytes(b"def serve(): return True\n")
    javascript.write_bytes(b"export const ready: boolean = true;\n")

    facts = discover_project(tmp_path)
    before = project_source_snapshot(
        tmp_path, facts, evidence_paths=["src/service.py", "missing.py"]
    )

    assert facts.languages == {"Python": 1, "TypeScript": 1}
    assert before["schema"] == "blueprint-ai/project-source-snapshot/v1"
    assert before["complete"] is False
    assert before["files"]["src/service.py"] == hashlib.sha256(python.read_bytes()).hexdigest()
    assert any("outside the native inventory" in item for item in before["diagnostics"])

    python.write_bytes(b"def serve(): return False\n")
    refreshed_facts = discover_project(tmp_path)
    after = project_source_snapshot(tmp_path, refreshed_facts)
    assert after["complete"] is True
    assert after["source_digest"] != before["source_digest"]
