"""Pinned external regression corpus; never executes target code on the host.

Usage: python benchmarks/corpus.py --output /tmp/blueprint-corpus [--clones PATH] [--review]
With --clones, repositories must already exist at owner_repo under PATH at exact SHAs.
Without it, fresh shallow clones are obtained from GitHub. Target code is never vendored;
optional native checks remain inside OCI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from blueprint_ai.discovery import discover_project
from blueprint_ai.engine import make_context, review
from blueprint_ai.model.context import ContextBuilder

FASTAPI_CORPUS_COMMIT = "cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7"  # gitleaks:allow

CORPUS = {
    "fastapi/full-stack-fastapi-template": FASTAPI_CORPUS_COMMIT,
    "open-telemetry/opentelemetry-demo": "9bfe486ff48ee8a6ea942be74171342cb71a9327",
    "terraform-aws-modules/terraform-aws-vpc": "cf0e3ca46fd51f47bf095957f2a6ac6127c89045",
    "isovalent/terraform-aws-vpc": "2bb6b130d0d884808fce9590a5a6c47dd931596d",
    "astral-sh/ruff": "4188373bdd5e3d0b05bbe254f98fd763b37519d6",
    "pallets/flask": "d73fa1cdcbd8b1465c151db8924ba58b1dd14e35",
    "open-webui/open-webui": "0a7c15832fb30b1903753e83f81dc7d27e5b0944",
    "facebook/create-react-app": "6254386531d263688ccfa542d0e628fbc0de0b28",
    "sindresorhus/awesome": "bc98e517ddca672f55f9857d714fc3ea3c3540b2",
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args], text=True, timeout=120
    )


def snapshot(root: Path) -> str:
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            path = Path(directory) / name
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(os.readlink(path).encode() if path.is_symlink() else path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--clones", type=Path)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--repos", nargs="+", choices=CORPUS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for repo, sha in CORPUS.items():
        if args.repos and repo not in args.repos:
            continue
        slug = repo.replace("/", "_")
        root = (args.clones or args.output / "repos") / slug
        if not root.exists() and not args.clones:
            root.mkdir(parents=True)
            git(root, "init", "-q")
            git(root, "remote", "add", "origin", "https://github.com/" + repo)
            git(root, "fetch", "--depth=1", "origin", sha)
            git(root, "checkout", "--detach", "FETCH_HEAD")
        assert git(root, "rev-parse", "HEAD").strip() == sha
        before = snapshot(root)
        started = time.monotonic()
        facts = discover_project(root)
        discovery_ms = round((time.monotonic() - started) * 1000)
        (args.output / f"{slug}.inspect.json").write_text(facts.model_dump_json(indent=2))
        contexts = {}
        builder = ContextBuilder(root, 2000)
        for blueprint in (
            "architecture",
            "testing",
            "security",
            "api-data-config",
            "operations",
            "documentation",
        ):
            value = builder.build(blueprint, facts, [])
            (args.output / f"{slug}.{blueprint}.context.txt").write_text(value)
            contexts[blueprint] = {
                **builder.metrics,
                "sha256": hashlib.sha256(value.encode()).hexdigest(),
            }
        row = {
            "repo": repo,
            "sha": sha,
            "discovery_ms": discovery_ms,
            "types": facts.project_types,
            "components": len(facts.graph.components),
            "lifecycle": facts.graph.lifecycle,
            "diagnostics": facts.graph.diagnostics,
            "contexts": contexts,
        }
        if args.review:
            context, _ = make_context(root, model_mode="off")
            context.config["offline"] = args.offline
            report = review(context)
            assert report.metadata is not None
            (args.output / f"{slug}.review.json").write_text(report.model_dump_json(indent=2))
            row.update(
                findings=len(report.findings),
                duration_ms=report.metadata.duration_ms,
                statuses={r.blueprint: r.status for r in report.results},
            )
        row["unchanged"] = before == snapshot(root)
        assert row["unchanged"], f"review mutated {repo}"
        summaries.append(row)
        print(
            json.dumps({k: v for k, v in row.items() if k not in {"contexts", "diagnostics"}}),
            flush=True,
        )
        (args.output / "summary.json").write_text(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
