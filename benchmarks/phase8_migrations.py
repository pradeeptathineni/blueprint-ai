"""Run the Phase 8 audit-derived migration corpus in disposable repositories.

Tool images must be acquired separately with ``blueprint-ai tools install``. The React
registry recipe is exercised as a read-only rejected-automation case because its artifact
cannot be integrity-verified before source mutation. The authoritative audit directory is
copied and never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from blueprint_ai.evolution import (
    accept_evolution,
    apply_evolution,
    plan_evolution,
    project_fingerprint,
    rollback_evolution,
)
from blueprint_ai.sandbox import SandboxPolicy


@dataclass(frozen=True)
class Case:
    name: str
    source: str | None
    target: str
    image: str | None
    expected_status: str = "verified"
    executable: bool = True


DOTNET_IMAGE = (
    "mcr.microsoft.com/dotnet/sdk@"
    "sha256:2fa828c68761b1b8c23d7662dc134421b9d3b59fe1425fdbc80804e390cdb24d"
)

CASES = (
    Case(
        "rust-2018-to-2024",
        "generated/rust-2018",
        "rust/edition=2024",
        "blueprint-tools/rust:1.98.1-r2",
    ),
    Case("dotnet6-to-net10", "generated/dotnet6", "dotnet/sdk-target=net10.0", DOTNET_IMAGE),
    Case(
        "python-legacy-build",
        "generated/python-legacy",
        "python/pep517-build-system=setuptools.build_meta",
        "blueprint-tools/python-build:3.12-setuptools84",
    ),
    Case(
        "next14-to-next16",
        "generated/next14-app",
        "next/official-upgrade-codemod=16.3.5",
        "blueprint-tools/next-codemod:16.3.5",
        "partial",
    ),
    Case(
        "react18.3-to-react19",
        "generated/react19-app",
        "react/19-official-codemods=19.3.0",
        "blueprint-tools/react-codemod:1.18.3",
        "partial",
        False,
    ),
    Case("setup-java-v2-pin", None, "github-actions/pin-official-actions", None),
)


def _git(root: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return process.stdout.strip()


def _prepare(parent: Path, audit: Path, case: Case) -> Path:
    root = parent / case.name
    if case.source:
        shutil.copytree(audit / case.source, root)
        _git(root, "switch", "-c", "phase8-corpus")
    else:
        root.mkdir()
        workflow = root / ".github/workflows/ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/setup-java@v2\n"
        )
        _git(root, "init", "-q", "-b", "phase8-corpus")
        _git(root, "config", "user.email", "corpus@blueprint.invalid")
        _git(root, "config", "user.name", "Blueprint Corpus")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "fixture")
    _git(root, "config", "user.email", "corpus@blueprint.invalid")
    _git(root, "config", "user.name", "Blueprint Corpus")
    if case.name == "react18.3-to-react19":
        package = json.loads((root / "package.json").read_text())
        package["dependencies"]["react"] = "18.3.1"
        package["dependencies"]["react-dom"] = "18.3.1"
        (root / "package.json").write_text(json.dumps(package, indent=2) + "\n")
        _git(root, "add", "package.json")
        _git(root, "commit", "-q", "-m", "establish React 18.3 prerequisite")
    if case.name == "next14-to-next16":
        (root / "middleware.ts").write_text(
            "export function middleware() { return Response.redirect('/new'); }\n"
        )
        _git(root, "add", "middleware.ts")
        _git(root, "commit", "-q", "-m", "add middleware rename regression")
    return root


def _policy(case: Case, *, writable: bool) -> SandboxPolicy | None:
    if not case.image:
        return None
    return SandboxPolicy(
        backend="docker",
        image=case.image,
        trusted=True,
        writable=writable,
        network="none",
    )


def _diff_sha(report) -> str:
    paths = sorted(change.target for change in report.changes)
    payload = "".join(f"{key}\0{report.diffs[key]}" for key in paths)
    return hashlib.sha256(payload.encode()).hexdigest()


def _exercise(root: Path, case: Case) -> dict:
    baseline = project_fingerprint(root)
    plan = plan_evolution(root, [case.target])
    if not case.executable:
        if plan.status != "blocked" or project_fingerprint(root) != baseline:
            raise AssertionError(f"{case.name}: unsafe automation was not rejected read-only")
        return {
            "case": case.name,
            "status": "rejected-automatic-execution",
            "steps": len(plan.steps),
            "read_only": True,
            "reason": "; ".join(plan.manual_boundaries),
            "model_calls": 0,
            "agent_calls": 0,
        }
    if plan.status != "ready":
        raise AssertionError(f"{case.name}: expected a ready executable prefix")
    dry = apply_evolution(
        root,
        plan,
        dry_run=True,
        policy=_policy(case, writable=False),
        run_blueprint_review=False,
    )
    if dry.status != "dry_run" or project_fingerprint(root) != baseline or not dry.diffs:
        raise AssertionError(f"{case.name}: dry-run was empty or changed the source")
    applied = apply_evolution(
        root,
        plan,
        policy=_policy(case, writable=True),
        run_blueprint_review=True,
    )
    if applied.status != case.expected_status or not applied.operation_id or not applied.changes:
        raise AssertionError(f"{case.name}: apply status was {applied.status}")
    if any(item.status != "passed" for item in applied.verifications):
        raise AssertionError(f"{case.name}: a required verification failed")
    if applied.metrics.get("model_calls") or applied.metrics.get("agent_calls"):
        raise AssertionError(f"{case.name}: deterministic migration used AI")
    pre_accept_repeat = plan_evolution(root, [case.target])
    expected_pre_accept = "blocked" if applied.status == "partial" else "noop"
    if pre_accept_repeat.status != expected_pre_accept:
        raise AssertionError(
            f"{case.name}: repeat plan was {pre_accept_repeat.status}, "
            f"expected {expected_pre_accept}"
        )
    accepted = False
    if applied.status == "partial":
        (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
        accept_evolution(
            root,
            applied.operation_id,
            ["corpus lockfile install completed", "corpus build/type/lint/test completed"],
        )
        accepted = True
    repeated = plan_evolution(root, [case.target])
    if repeated.status != "noop":
        raise AssertionError(f"{case.name}: accepted repeat plan was {repeated.status}")
    rollback_evolution(root, applied.operation_id)
    if project_fingerprint(root) != baseline:
        raise AssertionError(f"{case.name}: rollback was not exact")
    reproduced = apply_evolution(
        root,
        plan_evolution(root, [case.target]),
        policy=_policy(case, writable=True),
        run_blueprint_review=False,
    )
    if _diff_sha(reproduced) != _diff_sha(applied):
        raise AssertionError(f"{case.name}: clean rerun changed the exact diff")
    rollback_evolution(root, reproduced.operation_id or "")
    return {
        "case": case.name,
        "status": applied.status,
        "steps": len(plan.steps),
        "changed": sorted(change.target for change in applied.changes),
        "verifications": len(applied.verifications),
        "diff_sha256": _diff_sha(applied),
        "review": applied.review.status,
        "pre_accept_repeat_status": pre_accept_repeat.status,
        "repeat_plan_status": repeated.status,
        "manual_acceptance": accepted,
        "rollback_exact": True,
        "reproducible": True,
        "model_calls": 0,
        "agent_calls": 0,
    }


def _controls(parent: Path) -> list[dict]:
    rows = []
    fixtures = {
        "rust-current": {
            "Cargo.toml": '[package]\nname="current"\nversion="1.0.0"\nedition="2024"\n',
            "Cargo.lock": "version = 4\n",
            "src/lib.rs": "pub fn current() {}\n",
        },
        "dotnet-ambiguous": {
            "Demo.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                "<TargetFrameworks>net6.0;net8.0</TargetFrameworks>"
                "</PropertyGroup></Project>\n"
            )
        },
    }
    for name, files in fixtures.items():
        root = parent / name
        root.mkdir()
        for relative, text in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        _git(root, "init", "-q", "-b", "phase8-control")
        _git(root, "add", ".")
        _git(
            root,
            "-c",
            "user.name=Blueprint Corpus",
            "-c",
            "user.email=corpus@invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        )
        before = project_fingerprint(root)
        plan = plan_evolution(root)
        if {"rust/edition", "dotnet/sdk-target"} & set(plan.candidates):
            raise AssertionError(f"{name}: unsafe candidate was offered")
        if project_fingerprint(root) != before:
            raise AssertionError(f"{name}: inspection changed the control")
        rows.append({"case": name, "status": "rejected", "read_only": True})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, default=Path("../blueprint-ai-tmp/phase8-audit"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = args.audit_root.resolve()
    if not (audit / "REPORT.md").is_file():
        raise SystemExit(f"Phase 8 audit unavailable: {audit}")
    rows = []
    with tempfile.TemporaryDirectory(prefix="blueprint-phase8-corpus-") as directory:
        parent = Path(directory)
        for case in CASES:
            rows.append(_exercise(_prepare(parent, audit, case), case))
        rows.extend(_controls(parent))
    args.output.write_text(json.dumps({"audit": str(audit), "cases": rows}, indent=2) + "\n")
    print(json.dumps({"cases": rows}, indent=2))


if __name__ == "__main__":
    main()
