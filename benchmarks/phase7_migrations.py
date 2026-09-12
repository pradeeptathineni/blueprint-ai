"""Exercise supported Phase 7 migrations against disposable, real-tool fixtures.

The harness makes no network calls and never installs tools. Command-backed recipes run
only when their executable is already available; ``--require`` turns absence into failure.
Every exercised recipe must change real bytes, pass its verifier, roll back exactly, and
produce the same result when repeated from the restored baseline.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from blueprint_ai.evolution import apply_evolution, plan_evolution, project_fingerprint
from blueprint_ai.evolution.engine import rollback_evolution
from blueprint_ai.sandbox import SandboxPolicy


@dataclass(frozen=True)
class CorpusCase:
    recipe_id: str
    files: dict[str, str]
    tool: str | None = None


@dataclass(frozen=True)
class InspectionCase:
    name: str
    files: dict[str, str]
    expected_candidates: set[str]


CASES = (
    CorpusCase(
        "python/ruff-pyupgrade",
        {
            "pyproject.toml": (
                '[project]\nname = "phase7-python"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
            ),
            "src/example.py": (
                "from typing import List\n\n"
                "def identity(values: List[int]) -> List[int]:\n"
                "    return values\n"
            ),
        },
        "ruff",
    ),
    CorpusCase(
        "go/native-fix",
        {
            "go.mod": "module example.com/phase7\n\ngo 1.26\n",
            "example.go": (
                "package phase7\n\nfunc identity(value interface{}) interface{} { return value }\n"
            ),
            "example_test.go": (
                "package phase7\n\n"
                'import "testing"\n\n'
                "func TestIdentity(t *testing.T) {\n"
                '\tif identity(7) != 7 { t.Fatal("identity changed") }\n'
                "}\n"
            ),
        },
        "go",
    ),
    CorpusCase(
        "terraform/native-format",
        {"main.tf": ('variable "labels" {\ntype=map(string)\ndefault={environment="test"}\n}\n')},
        "terraform",
    ),
    CorpusCase(
        "container/maintainer-to-oci-label",
        {"Dockerfile": "FROM scratch\nMAINTAINER Phase Seven <phase7@example.invalid>\n"},
    ),
    CorpusCase(
        "github-actions/pin-official-actions",
        {
            ".github/workflows/ci.yml": (
                "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - uses: actions/checkout@v4\n"
                "      - uses: actions/setup-python@v6\n"
            )
        },
    ),
)

INSPECTION_CASES = (
    InspectionCase(
        "legacy-python-packaging",
        {
            "requirements.txt": "requests==2.32.5\n",
            "legacy.py": "import requests\n",
        },
        {"python/requirements-to-uv-project"},
    ),
    InspectionCase(
        "java-spring",
        {
            "pom.xml": (
                '<project xmlns="http://maven.apache.org/POM/4.0.0">'
                "<artifactId>legacy-service</artifactId><dependencies><dependency>"
                "<groupId>org.springframework.boot</groupId>"
                "<artifactId>spring-boot-starter-webmvc</artifactId>"
                "</dependency></dependencies></project>\n"
            ),
            "src/main/java/example/App.java": "package example; class App {}\n",
        },
        {"java/openrewrite-lts", "java/spring-openrewrite"},
    ),
    InspectionCase(
        "create-react-app",
        {
            "package.json": (
                '{"name":"legacy-cra","version":"1.0.0","dependencies":'
                '{"react":"17.0.2","react-scripts":"5.0.1"}}\n'
            ),
            "src/index.js": "const element = <main>legacy</main>;\n",
        },
        {
            "react/create-react-app-foundation",
            "react/19-official-codemods",
            "generic/ast-grep",
        },
    ),
    InspectionCase(
        "rust-edition",
        {
            "Cargo.toml": (
                '[package]\nname = "legacy-rust"\nversion = "0.1.0"\nedition = "2018"\n'
            ),
            "src/lib.rs": "pub fn answer() -> u8 { 42 }\n",
        },
        {"rust/edition"},
    ),
    InspectionCase(
        "dotnet-framework",
        {
            "legacy.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                "<TargetFramework>net6.0</TargetFramework>"
                "</PropertyGroup></Project>\n"
            ),
            "Program.cs": 'System.Console.WriteLine("legacy");\n',
        },
        {"dotnet/modernization-agent"},
    ),
    InspectionCase(
        "kubernetes-api",
        {
            "deploy.yaml": (
                "apiVersion: extensions/v1beta1\nkind: Deployment\nmetadata:\n"
                "  name: legacy\nspec:\n  template:\n    spec:\n      containers:\n"
                "        - name: app\n          image: example.invalid/app:1\n"
            )
        },
        {"kubernetes/api-convert"},
    ),
)


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _fixture(parent: Path, index: int, case: CorpusCase) -> Path:
    root = parent / f"case-{index:02d}"
    root.mkdir()
    for relative, content in case.files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(root, "init", "-q", "-b", "phase7-corpus")
    _git(root, "-c", "user.name=Blueprint Corpus", "-c", "user.email=corpus@invalid", "add", ".")
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
    return root


def _inspect_fixture(parent: Path, index: int, case: InspectionCase) -> dict:
    root = _fixture(
        parent,
        index,
        CorpusCase(recipe_id=case.name, files=case.files),
    )
    baseline = project_fingerprint(root)
    plan = plan_evolution(root)
    missing = case.expected_candidates - set(plan.candidates)
    if plan.status != "inspection" or plan.steps or project_fingerprint(root) != baseline:
        raise AssertionError(f"{case.name}: inspection planning mutated or selected work")
    if missing:
        raise AssertionError(f"{case.name}: missing candidate(s): {', '.join(sorted(missing))}")
    return {
        "name": case.name,
        "status": "passed",
        "expected_candidates": sorted(case.expected_candidates),
        "detected_candidates": plan.candidates,
        "read_only": True,
    }


def _exercise(root: Path, case: CorpusCase) -> dict:
    baseline = project_fingerprint(root)
    plan = plan_evolution(root, [case.recipe_id])
    if plan.status != "ready":
        raise AssertionError(f"{case.recipe_id}: fixture did not produce a ready plan")
    policy = None
    if case.tool:
        policy = SandboxPolicy(backend="host", trusted=True, writable=True, timeout=120)
    preview = apply_evolution(
        root,
        plan,
        dry_run=True,
        policy=policy,
        run_blueprint_review=False,
    )
    if preview.status != "dry_run" or project_fingerprint(root) != baseline or not preview.diffs:
        raise AssertionError(f"{case.recipe_id}: dry-run was empty or mutated the fixture")
    applied = apply_evolution(root, plan, policy=policy, run_blueprint_review=False)
    if applied.status != "verified" or not applied.operation_id or not applied.changes:
        raise AssertionError(f"{case.recipe_id}: real apply did not make a verified change")
    if any(check.status != "passed" for check in applied.verifications):
        raise AssertionError(f"{case.recipe_id}: a required verification did not pass")
    changed_fingerprint = project_fingerprint(root)
    rollback_evolution(root, applied.operation_id)
    if project_fingerprint(root) != baseline:
        raise AssertionError(f"{case.recipe_id}: rollback did not restore exact source bytes")

    repeated = apply_evolution(
        root,
        plan_evolution(root, [case.recipe_id]),
        policy=policy,
        run_blueprint_review=False,
    )
    if [item.after_sha256 for item in repeated.changes] != [
        item.after_sha256 for item in applied.changes
    ]:
        raise AssertionError(f"{case.recipe_id}: repeated output was not deterministic")
    rollback_evolution(root, repeated.operation_id or "")
    if project_fingerprint(root) != baseline:
        raise AssertionError(f"{case.recipe_id}: repeated rollback was not exact")
    return {
        "recipe_id": case.recipe_id,
        "status": "passed",
        "tool": case.tool,
        "planned_paths": plan.steps[0].files,
        "before_fingerprint": baseline,
        "changed_fingerprint": changed_fingerprint,
        "diffs": preview.diffs,
        "verification": [item.model_dump(mode="json") for item in applied.verifications],
        "repeatable": True,
        "rollback_exact": True,
        "model_calls": applied.metrics.get("model_calls", 0),
        "agent_calls": applied.metrics.get("agent_calls", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="RECIPE_ID",
        help="Fail if this recipe's native tool is unavailable",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="RECIPE_ID",
        help="Exercise only this supported recipe; repeat to select a CI subset",
    )
    args = parser.parse_args()
    known = {case.recipe_id for case in CASES}
    unknown = (set(args.require) | set(args.only)) - known
    if unknown:
        parser.error("unknown recipe(s): " + ", ".join(sorted(unknown)))
    if set(args.require) - (set(args.only) or known):
        parser.error("every required recipe must also be selected by --only")
    rows = []
    inspections = []
    with tempfile.TemporaryDirectory(prefix="blueprint-phase7-corpus-") as directory:
        parent = Path(directory)
        for index, case in enumerate(CASES, 1):
            if args.only and case.recipe_id not in args.only:
                continue
            if case.tool and shutil.which(case.tool) is None:
                if case.recipe_id in args.require:
                    raise RuntimeError(f"{case.recipe_id}: required {case.tool} is unavailable")
                rows.append(
                    {
                        "recipe_id": case.recipe_id,
                        "status": "skipped",
                        "tool": case.tool,
                        "detail": "native executable unavailable; no installation was attempted",
                    }
                )
                continue
            rows.append(_exercise(_fixture(parent, index, case), case))
        offset = len(CASES)
        for index, inspection_case in enumerate(INSPECTION_CASES, offset + 1):
            inspections.append(_inspect_fixture(parent, index, inspection_case))
    payload = {
        "schema_version": "1.0.0",
        "network": "disabled",
        "installation_attempted": False,
        "cases": rows,
        "inspection_cases": inspections,
        "passed": all(row["status"] in {"passed", "skipped"} for row in rows)
        and all(row["status"] == "passed" for row in inspections),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
