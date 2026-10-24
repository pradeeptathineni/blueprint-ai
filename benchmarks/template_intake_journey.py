"""Questionnaire template intake journey through the CLI, one fresh process per step.

Builds a synthetic local Git template (str/bool/int/choice questions, templated filename),
then: questions, no-effect preview, refused stale digest, confirmed init, provenance,
second-version preview/update, refused type change, and rollback.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal, overload

CONFIG = """\
_subdirectory: template
project_name:
  type: str
  help: Human readable project name
package:
  type: str
  default: app
  help: Python package directory
with_cli:
  type: bool
  default: false
port:
  type: int
  default: 8000
license:
  type: str
  default: MIT
  choices: {MIT License: MIT, Apache 2.0: Apache-2.0}
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)


@overload
def cli(*args: str, expect: Literal[0] = 0) -> dict[str, Any]: ...


@overload
def cli(*args: str, expect: int) -> dict[str, Any] | str: ...


def cli(*args: str, expect: int = 0) -> dict[str, Any] | str:
    result = subprocess.run(
        [sys.executable, "-m", "blueprint_ai.cli", *args], capture_output=True, text=True
    )
    if result.returncode != expect:
        raise SystemExit(f"{args[:2]} exited {result.returncode}: {result.stderr[-800:]}")
    if expect == 0:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise SystemExit("Expected a JSON object from the successful CLI")
        return value
    lines = (line.strip(" │╭╮╰╯─") for line in result.stderr.splitlines())
    return " ".join(line for line in lines if line).partition("Invalid value: ")[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True)
    steps: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="intake-journey-") as temporary:
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
                "template/README.md.jinja": "# {{ project_name }}\n\nLicense: {{ license }}\n",
                "template/src/{{ package }}/settings.py.jinja": (
                    'NAME = "{{ project_name }}"\nPORT = {{ port }}\nWITH_CLI = {{ with_cli }}\n'
                ),
                "template/.gitignore": "__pycache__/\n",
            },
        )
        git(source, "add", "-A")
        git(source, "commit", "-qm", "v1")
        v1 = git(source, "rev-parse", "HEAD")
        answers = base / "answers.json"
        answers.write_text(
            json.dumps({"project_name": "Orders", "package": "orders", "port": 9100})
        )
        target = base / "orders"

        shown = cli("template", "questions", str(source), "--ref", v1)
        steps["questions"] = [
            {k: q.get(k) for k in ("name", "type", "required", "default")}
            for q in shown["questions"]
        ]
        args = [str(source), str(target), "--ref", v1, "--answers", str(answers)]
        viewed = cli("template", "preview", *args)
        assert not target.exists(), "preview created the target"
        steps["preview"] = {
            "target_exists_after_preview": target.exists(),
            "answers": viewed["answers"],
            "answer_sources": viewed["answer_sources"],
            "files": [(f["path"], f["source_path"], oct(f["mode"])) for f in viewed["files"]],
            "preview_sha256": viewed["preview_sha256"],
        }
        answers.write_text(
            json.dumps({"project_name": "Orders", "package": "orders", "port": 9101})
        )
        steps["stale_digest"] = cli(
            "template",
            "init",
            *args,
            "--format",
            "copier-safe-v1",
            "--confirm-digest",
            viewed["preview_sha256"],
            expect=2,
        )
        assert not target.exists()
        answers.write_text(
            json.dumps({"project_name": "Orders", "package": "orders", "port": 9100})
        )
        steps["init"] = cli(
            "template",
            "init",
            *args,
            "--format",
            "copier-safe-v1",
            "--confirm-digest",
            viewed["preview_sha256"],
        )
        settings = (target / "src/orders/settings.py").read_text()
        steps["settings.py"] = settings
        steps["provenance"] = json.loads((target / ".blueprint-ai/template.json").read_text())

        readme = target / "README.md"
        readme.write_text(readme.read_text() + "\nOwned by the orders team.\n")
        write(
            source,
            {
                "copier.yml": CONFIG + "docker:\n  type: bool\n  default: true\n",
                "template/README.md.jinja": "# {{ project_name }}\n\n"
                "Service for {{ project_name }}.\n\nLicense: {{ license }}\n",
                "template/deploy/{{ package }}.env.jinja": "PORT={{ port }}\nDOCKER={{ docker }}\n",
            },
        )
        git(source, "add", "-A")
        git(source, "commit", "-qm", "v2")
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
            "deploy/orders.env": (target / "deploy/orders.env").read_text(),
            "record_answers": json.loads((target / ".blueprint-ai/template.json").read_text())[
                "answers"
            ],
        }

        v3_config = CONFIG.replace("type: int\n  default: 8000", "type: str")
        write(source, {"copier.yml": v3_config + "docker:\n  type: bool\n  default: true\n"})
        git(source, "commit", "-qam", "v3 changes port type")
        v3 = git(source, "rev-parse", "HEAD")
        record_before = (target / ".blueprint-ai/template.json").read_bytes()
        steps["type_change_refused"] = cli(
            "template",
            "plan",
            str(target),
            "--ref",
            v3,
            "--output",
            str(base / "v3.json"),
            expect=2,
        )
        assert (target / ".blueprint-ai/template.json").read_bytes() == record_before

        steps["rollback"] = cli("rollback", applied["operation_id"], str(target), "--json")
        steps["after_rollback"] = {
            "commit_is_v1": json.loads((target / ".blueprint-ai/template.json").read_text())[
                "commit"
            ]
            == v1,
            "deploy_exists": (target / "deploy").exists() and any((target / "deploy").iterdir()),
            "README.md": readme.read_text(),
        }
    (output / "journey.json").write_text(json.dumps(steps, indent=2) + "\n")
    print(json.dumps(steps, indent=2))


if __name__ == "__main__":
    main()
