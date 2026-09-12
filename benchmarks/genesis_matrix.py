"""Real-provider matrix. Only generated projects are installed and executed."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from blueprint_ai.genesis import IntentSpec, plan_project
from blueprint_ai.genesis.executor import create_project


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--kinds", nargs="*")
    parser.add_argument("--add-ons", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    kinds = args.kinds or IntentSpec.model_json_schema()["properties"]["kind"]["enum"]

    cases: dict[str, dict[str, object]] = {kind: {"kind": kind} for kind in kinds}
    if "full-stack" in cases:
        cases["full-stack"].update(api_client=True, ci=True)
    if args.add_ons:
        cases.update(
            {
                "python-api-addons": dict(
                    kind="python-api", container=True, ci=True, devcontainer=True
                ),
                "node-full-stack-addons": dict(
                    kind="full-stack",
                    backend="node",
                    api_client=True,
                    container=True,
                    ci=True,
                    devcontainer=True,
                ),
            }
        )

    def generate(kind: str) -> dict:
        name = "matrix-" + kind
        values = {"name": name, **cases[kind]}
        result = create_project(
            args.output / name,
            plan_project(IntentSpec.model_validate(values)),
            allow_network=args.network,
            trust_providers=True,
        )
        (args.output / (kind + ".json")).write_text(result.model_dump_json(indent=2))
        row = {
            "kind": kind,
            "status": result.status,
            "duration_ms": result.duration_ms,
            "operations": len(result.operations),
            "detail": result.detail,
        }
        print(json.dumps(row), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(generate, cases))
    (args.output / "summary.json").write_text(json.dumps(rows, indent=2))
    if any(row["status"] != "verified" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
