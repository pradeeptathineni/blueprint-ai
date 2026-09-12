"""Measure repeated trusted-host and read-only OCI startup for a fixed no-op command."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

from blueprint_ai.sandbox import SandboxPolicy, execute


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="python:3.12-slim-bookworm")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="blueprint-startup-") as directory:
        for backend in ("host", "docker"):
            samples = []
            for _ in range(5):
                started = time.monotonic()
                result = execute(
                    [sys.executable if backend == "host" else "python", "-c", "print('ready')"],
                    Path(directory),
                    SandboxPolicy(
                        backend=backend,
                        trusted=backend == "host",
                        image=args.image if backend == "docker" else None,
                    ),
                )
                assert result.result.returncode == 0 and result.result.stdout.strip() == "ready"
                samples.append(round((time.monotonic() - started) * 1000, 3))
            rows.append(
                {
                    "backend": backend,
                    "milliseconds": samples,
                    "median_ms": statistics.median(samples),
                    "evidence": result.evidence.model_dump(mode="json"),
                }
            )
    args.output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
