"""Fresh generated-project and cloud covering matrix through the common sandbox executor."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from blueprint_ai.genesis import IntentSpec, plan_project
from blueprint_ai.genesis.executor import create_project
from blueprint_ai.sandbox import SandboxPolicy
from blueprint_ai.support import FAMILIES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--kinds", help=("comma-separated subset; defaults to all initialized families")
    )
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--backend", default="docker")
    parser.add_argument("--compositions", action="store_true")
    parser.add_argument("--clouds", default="aws", help="cloud subset for IaC kinds")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    kinds = (
        args.kinds.split(",") if args.kinds else [f.id for f in FAMILIES.values() if f.initialize]
    )
    cases = [
        (
            kind + ("-" + cloud if cloud != "aws" else ""),
            IntentSpec.model_validate({"name": "Example Service", "kind": kind, "cloud": cloud}),
        )
        for kind in kinds
        for cloud in (
            args.clouds.split(",") if kind in {"terraform", "opentofu", "pulumi"} else ["aws"]
        )
    ]
    if args.compositions:
        cases += [
            (
                f"{kind}-{cloud}",
                IntentSpec.model_validate({"name": "Cloud Baseline", "kind": kind, "cloud": cloud}),
            )
            for kind in ["terraform", "opentofu", "pulumi"]
            for cloud in ["azure", "gcp"]
        ]
        cases += [
            (
                f"full-stack-{backend}-client",
                IntentSpec.model_validate(
                    {
                        "name": "Full Stack",
                        "kind": "full-stack",
                        "backend": backend,
                        "ci": True,
                        "api_client": True,
                    }
                ),
            )
            for backend in ["python", "node"]
        ]
        cases += [
            (
                kind + "-ci",
                IntentSpec.model_validate({"name": "Team Project", "kind": kind, "ci": True}),
            )
            for kind in ["go-api", "rust-cli", "dotnet-api", "django", "vue", "svelte"]
        ]
    rows = []
    for label, intent in cases:
        started = time.monotonic()
        result = create_project(
            args.output / label,
            plan_project(intent),
            allow_network=args.network,
            trust_providers=True,
            sandbox=SandboxPolicy(backend=args.backend, trusted=True, writable=True),
        )
        (args.output / (label + ".json")).write_text(result.model_dump_json(indent=2) + "\n")
        row = {
            "case": label,
            "status": result.status,
            "seconds": round(time.monotonic() - started, 3),
            "operations": len(result.operations),
            "detail": result.detail,
            "failed": [o.model_dump() for o in result.operations if o.status != "passed"],
        }
        rows.append(row)
        (args.output / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps(row), flush=True)
    raise SystemExit(0 if all(r["status"] == "verified" for r in rows) else 1)


if __name__ == "__main__":
    main()
