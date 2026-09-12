"""Exercise an already-installed distribution in a disposable offline workspace.

Run with the fresh environment's Python, without PYTHONPATH. No tool installation or
target-controlled host execution is performed. Optional OCI execution uses --image.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", help="Optional local Docker image for the CLI boundary check")
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="blueprint-release-smoke-") as directory:
        root = Path(directory)
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "OPENAI_API_KEY"}}

        def cli(*arguments: str, codes: tuple[int, ...] = (0,)) -> str:
            process = subprocess.run(
                [sys.executable, "-m", "blueprint_ai.cli", *arguments],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert process.returncode in codes, (arguments, process.stdout, process.stderr)
            rows.append({"command": list(arguments), "exit_code": process.returncode})
            return process.stdout

        version = importlib.metadata.version("blueprint-ai-cli")
        assert version in cli("--version")
        assert "Usage" in cli("--help")
        assert json.loads(cli("doctor", "--json"))["runtime_ok"]
        assert json.loads(cli("tools", "doctor"))["sandboxes"]
        for schema in (
            "report",
            "settings",
            "custom-blueprint",
            "intent",
            "genesis-plan",
            "project-graph",
            "sandbox-policy",
        ):
            assert json.loads(cli("schema", schema))["schema"]
        assert json.loads(cli("support"))["families"]
        assert json.loads(cli("tools", "install", "ruff", "--dry-run"))["command"]
        identity = json.loads(cli("name", "Windows Service", "--ecosystem", "python"))
        assert identity["package"] == "windows-service" and identity["module"] == "windows_service"
        assert not snapshot(root)
        for kind in ("repository", "openapi"):
            target = root / kind
            result = json.loads(cli("init", str(target), "--kind", kind))
            assert result["status"] == "verified", result
            assert (target / ".blueprint-ai/genesis.json").exists()
        project = root / "repository"
        (project / "main.py").write_text("answer = 42\n")
        (project / ".blueprint-ai.yml").write_text("offline: true\nmodel_mode: off\n")
        assert json.loads(cli("inspect", str(project), "--json"))["file_count"]
        before = snapshot(project)
        report = json.loads(cli("review", str(project), "--no-model", "--format", "json"))
        assert report["metadata"]["offline"] and report["metadata"]["model_mode"] == "off"
        assert all(r["model_metrics"].get("calls", 0) == 0 for r in report["results"])
        assert snapshot(project) == before
        assert json.loads(cli("add", "security-policy", str(project)))["files"]
        assert snapshot(project) == before
        added = json.loads(cli("add", "security-policy", str(project), "--apply"))
        assert any(c["status"] == "changed" for c in added["changes"]) and added["operation_id"]
        cli("rollback", added["operation_id"], str(project), "--json")
        assert snapshot(project) == before
        if args.image:
            isolated = json.loads(
                cli("sandbox", str(project), args.image, "--", "python", "--version")
            )
            assert isolated["evidence"]["isolated"] and isolated["evidence"]["teardown"]
        else:
            # A missing boundary is explicit; this command must never run on the host.
            cli("sandbox", str(project), "unavailable-release-image", "must-not-run", codes=(2,))
        args.output.write_text(
            json.dumps(
                {
                    "version": version,
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "checks": rows,
                    "passed": True,
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
