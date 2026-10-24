"""Conditional questionnaire journey through the CLI, one fresh process per step.

Builds a disposable synthetic local Git template in copier-safe-v2 (a CI switch, a CI provider
asked only when CI is on, a release question asked only when it is off, and a multiselect of
optional features), then drives the edit loop a consumer such as Deep would run: evaluate
partial answers, read visible questions, errors and skipped names, preview with no writes,
refuse a stale confirmation and a hidden-answer reference, create, commit the project, adopt
a changed template with plan/apply and roll it back. Nothing outside the temporary directory
is touched and nothing is fetched or executed besides Git and this package's CLI.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CONFIG = """\
_subdirectory: template
project_name:
  type: str
  help: Project name
ci:
  type: bool
  help: Run continuous integration?
ci_provider:
  type: str
  help: CI provider
  choices: {GitHub Actions: github, GitLab CI: gitlab}
  when: "{{ ci }}"
manual_release:
  type: bool
  help: Release by hand?
  default: true
  when: "{{ not ci }}"
features:
  type: str
  help: Optional project features
  multiselect: true
  choices: {Documentation: docs, Linting: lint, Tests: tests}
  default: [tests]
"""
README = "# {{ project_name }}\n\nCI: {{ ci }}\nFeatures: {{ features }}\n"
CI_FILE = "provider: {{ ci_provider }}\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)


def tree(root: Path) -> dict[str, str | None]:
    return {
        str(p.relative_to(root)): p.read_bytes().hex() if p.is_file() else None
        for p in sorted(root.rglob("*"))
    }


def cli(*args: str, expect: int = 0) -> Any:
    result = subprocess.run(
        [sys.executable, "-m", "blueprint_ai.cli", *args], capture_output=True, text=True
    )
    if result.returncode != expect:
        raise SystemExit(f"{args[:2]} exited {result.returncode}: {result.stderr[-800:]}")
    if expect == 0:
        return json.loads(result.stdout)
    lines = (line.strip(" │╭╮╰╯─") for line in result.stderr.splitlines())
    return " ".join(line for line in lines if line).partition("Invalid value: ")[2]


def shown(result: dict) -> dict:
    """The part of an evaluation a consumer renders; it interprets nothing itself."""
    return {
        "visible": [(q["name"], q["status"]) for q in result["questions"]],
        "answers": result["answers"],
        "errors": result["errors"],
        "skipped": result["skipped"],
        "unresolved": result["unresolved"],
        "complete": result["complete"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    steps: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="conditional-journey-") as temporary:
        base = Path(temporary)
        source = base / "template"
        source.mkdir()
        git(source, "init", "-q")
        git(source, "config", "user.name", "Journey")
        git(source, "config", "user.email", "journey@example.invalid")
        write(
            source,
            {
                "copier.yml": CONFIG,
                "template/README.md.jinja": README,
                "template/ci/pipeline.yml.jinja": CI_FILE,
            },
        )
        git(source, "add", "-A")
        git(source, "commit", "-qm", "v1")
        v1 = git(source, "rev-parse", "HEAD")
        draft = base / "draft.json"
        target = base / "shop"

        def evaluate(answers: dict) -> dict:
            draft.write_text(json.dumps(answers))
            return cli("template", "evaluate", str(source), "--ref", v1, "--answers", str(draft))

        before = tree(base)
        declared = cli("template", "questions", str(source), "--ref", v1)
        steps["questions"] = {
            "dialect": declared["dialect"],
            "declared": [
                {k: q[k] for k in ("name", "type", "required", "when", "multiselect") if k in q}
                for q in declared["questions"]
            ],
        }
        steps["evaluate_empty"] = shown(evaluate({}))
        steps["evaluate_ci_on"] = shown(evaluate({"project_name": "Shop", "ci": True}))
        steps["evaluate_invalid"] = shown(
            evaluate(
                {
                    "project_name": "Shop",
                    "ci": True,
                    "ci_provider": "circle",
                    "features": ["docs", "docs"],
                }
            )
        )
        filled = {"project_name": "Shop", "ci": True, "ci_provider": "gitlab"}
        disabled = evaluate({**filled, "ci": False})
        steps["evaluate_ci_turned_off"] = shown(disabled)
        assert "ci_provider" in disabled["skipped"] and "ci_provider" not in disabled["answers"]

        draft.write_text(json.dumps({**filled, "ci": False}))
        args = [str(source), str(target), "--ref", v1, "--answers", str(draft)]
        steps["preview_hidden_reference_refused"] = cli("template", "preview", *args, expect=2)

        final = {**filled, "features": ["tests", "docs"]}
        steps["evaluate_final"] = shown(evaluate(final))
        viewed = cli("template", "preview", *args)
        steps["preview"] = {
            "dialect": viewed["dialect"],
            "answers": viewed["answers"],
            "answer_sources": viewed["answer_sources"],
            "skipped": viewed["skipped"],
            "files": [(f["path"], f["size"]) for f in viewed["files"]],
            "diffs": {d["path"]: d["diff"] for d in viewed["diffs"]},
            "preview_sha256": viewed["preview_sha256"],
        }
        steps["no_writes_during_evaluate_and_preview"] = tree(base) == {
            **before,
            "draft.json": draft.read_bytes().hex(),
        }
        assert steps["no_writes_during_evaluate_and_preview"] and not target.exists()

        digest = viewed["preview_sha256"]
        init = ["template", "init", *args, "--format", "copier-safe-v2", "--confirm-digest", digest]
        draft.write_text(json.dumps({**final, "features": ["docs"]}))
        steps["stale_confirmation_refused"] = cli(*init, expect=2)
        draft.write_text(json.dumps({**final, "features": ["docs", "tests"]}))
        steps["init"] = cli(*init)
        steps["record"] = json.loads((target / ".blueprint-ai/template.json").read_text())
        steps["created"] = {
            "README.md": (target / "README.md").read_text(),
            "ci/pipeline.yml": (target / "ci/pipeline.yml").read_text(),
        }

        readme = target / "README.md"
        readme.write_text(readme.read_text() + "\nOwned by the shop team.\n")
        (target / "NOTES.md").write_text("Authored notes.\n")
        git(target, "config", "user.name", "Journey")
        git(target, "config", "user.email", "journey@example.invalid")
        git(target, "add", "-A")
        git(target, "commit", "-qm", "Create shop from template")
        record_before = (target / ".blueprint-ai/template.json").read_bytes()

        write(
            source,
            {
                "copier.yml": CONFIG + "coverage:\n  type: bool\n  help: Upload coverage?\n"
                '  default: true\n  when: "{{ ci }}"\n',
                "template/README.md.jinja": "# {{ project_name }}\n\nService for "
                "{{ project_name }}.\n\nCI: {{ ci }}\nFeatures: {{ features }}\n",
                "template/ci/pipeline.yml.jinja": CI_FILE + "coverage: {{ coverage }}\n",
            },
        )
        git(source, "add", "-A")
        git(source, "commit", "-qm", "v2 adds a CI-only coverage question")
        v2 = git(source, "rev-parse", "HEAD")
        steps["check"] = cli("template", "check", str(target), "--ref", v2)
        plan_path = base / "update-plan.json"
        planned = cli("template", "plan", str(target), "--ref", v2, "--output", str(plan_path))
        steps["update_plan"] = {
            "status": planned["status"],
            "rows": [(r["path"], r["status"]) for r in planned["rows"]],
        }
        applied = cli("template", "apply", str(target), "--plan", str(plan_path))
        steps["update_apply"] = {k: applied[k] for k in ("status", "operation_id")}
        steps["after_update"] = {
            "README.md": readme.read_text(),
            "ci/pipeline.yml": (target / "ci/pipeline.yml").read_text(),
            "NOTES.md": (target / "NOTES.md").read_text(),
            "record_answers": json.loads((target / ".blueprint-ai/template.json").read_text())[
                "answers"
            ],
        }
        steps["rollback"] = cli("rollback", applied["operation_id"], str(target), "--json")
        steps["after_rollback"] = {
            "record_restored": (target / ".blueprint-ai/template.json").read_bytes()
            == record_before,
            "README.md": readme.read_text(),
            "ci/pipeline.yml": (target / "ci/pipeline.yml").read_text(),
            "NOTES.md": (target / "NOTES.md").read_text(),
        }
        assert steps["after_rollback"]["record_restored"]
    (output / "journey.json").write_text(json.dumps(steps, indent=2) + "\n")
    print(json.dumps(steps, indent=2))


if __name__ == "__main__":
    main()
