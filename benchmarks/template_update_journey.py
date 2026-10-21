"""Run the owned template update journey through Blueprint's public CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def command(args: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New empty temporary directory")
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("--root must be empty")
    root.mkdir(parents=True, exist_ok=True)
    source, project, receipts = root / "template", root / "project", root / "receipts"
    source.mkdir()
    receipts.mkdir()
    script_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(script_root / "src")}

    def git(*parts: str) -> str:
        return command(["git", "-C", str(source), *parts], root).strip()

    def cli(*parts: str) -> dict:
        output = command([sys.executable, "-m", "blueprint_ai.cli", *parts], root, env=env)
        return json.loads(output)

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    write(
        source / "app.py",
        'def route(path):\n    if path == "/":\n'
        '        return "{{project}}"\n    return "missing"\n\n\n'
        'def status():\n    return "ready"\n',
    )
    write(source / "README.md", "# {{project}}\n\nStatus: basic\n\nGenerated API.\n")
    git("add", ".")
    git("commit", "-qm", "v1")
    first = git("rev-parse", "HEAD")
    write(
        source / "app.py",
        'def route(path):\n    if path == "/":\n'
        '        return "{{project}}"\n    return "missing"\n\n\n'
        'def status():\n    return "ready"\n\n\n'
        'def health():\n    return {"status": "ok"}\n',
    )
    write(source / "README.md", "# {{project}}\n\nStatus: healthy\n\nGenerated API.\n")
    write(
        source / "tests/test_health.py",
        "import unittest\nfrom app import health, route\n\n"
        "class HealthTest(unittest.TestCase):\n"
        "    def test_health(self):\n"
        '        self.assertEqual(health(), {"status": "ok"})\n'
        '        self.assertEqual(route("/mine"), "authored")\n',
    )
    git("add", ".")
    git("commit", "-qm", "v2")
    second = git("rev-parse", "HEAD")
    answers = root / "answers.json"
    answers.write_text(json.dumps({"project": "Sample API"}))
    initialized = cli(
        "template", "init", str(source), str(project), "--ref", first, "--answers", str(answers)
    )
    write(
        project / "app.py",
        (project / "app.py")
        .read_text()
        .replace(
            '    return "missing"',
            '    if path == "/mine":\n        return "authored"\n    return "missing"',
        ),
    )
    write(
        project / "README.md", "# Sample API\n\nStatus: customized\n\nGenerated API.\n\nMy notes.\n"
    )
    before = {
        path: (project / path).read_bytes()
        for path in ("app.py", "README.md", ".blueprint-ai/template.json")
    }
    conflict_path = receipts / "conflict-plan.json"
    preview = cli("template", "plan", str(project), "--ref", second, "--output", str(conflict_path))
    assert preview["status"] == "conflict"
    assert not (project / "tests/test_health.py").exists()
    resolution = root / "resolved-readme.md"
    write(
        resolution,
        "# Sample API\n\nStatus: healthy and customized\n\nGenerated API.\n\nMy notes.\n",
    )
    ready_path = receipts / "ready-plan.json"
    ready = cli(
        "template",
        "plan",
        str(project),
        "--ref",
        second,
        "--output",
        str(ready_path),
        "--resolve",
        f"README.md={resolution}",
    )
    assert ready["status"] == "ready"
    original_app = (project / "app.py").read_text()
    write(project / "app.py", original_app + "# changed after preview\n")
    stale = subprocess.run(
        [
            sys.executable,
            "-m",
            "blueprint_ai.cli",
            "template",
            "apply",
            str(project),
            "--plan",
            str(ready_path),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert stale.returncode != 0 and "stale template plan" in stale.stderr
    write(project / "app.py", original_app)
    applied = cli("template", "apply", str(project), "--plan", str(ready_path))
    assert applied["status"] == "verified"
    (receipts / "applied.json").write_text(json.dumps(applied, indent=2) + "\n")
    (receipts / "operation.json").write_bytes((project / applied["manifest_path"]).read_bytes())
    smoke = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    (receipts / "smoke.txt").write_text(smoke.stdout + smoke.stderr)
    assert "def health" in (project / "app.py").read_text()
    assert "My notes." in (project / "README.md").read_text()
    noop_path = receipts / "noop-plan.json"
    noop = cli("template", "plan", str(project), "--ref", second, "--output", str(noop_path))
    assert noop["status"] == "noop"
    assert cli("template", "apply", str(project), "--plan", str(noop_path))["status"] == "noop"
    write(project / "unrelated.txt", "keep me\n")
    rolled_back = cli("rollback", applied["operation_id"], str(project), "--json")
    assert rolled_back["status"] == "rolled_back"
    assert all((project / path).read_bytes() == content for path, content in before.items())
    assert not (project / "tests/test_health.py").exists()
    assert (project / "unrelated.txt").read_text() == "keep me\n"
    summary = {
        "status": "passed",
        "template_v1": first,
        "template_v2": second,
        "initialized": initialized,
        "conflicts": [row["path"] for row in preview["rows"] if row["status"] == "conflict"],
        "stale_plan_refused": True,
        "operation_id": applied["operation_id"],
        "smoke": "passed",
        "replay": noop["status"],
        "rollback": rolled_back["status"],
    }
    (receipts / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"summary": str(receipts / "summary.json"), **summary}, indent=2))


if __name__ == "__main__":
    main()
