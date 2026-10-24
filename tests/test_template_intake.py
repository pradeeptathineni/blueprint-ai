"""copier-safe-v1 questionnaire intake: typed answers, bound preview, creation and updates."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from blueprint_ai.cli import app
from blueprint_ai.evolution.copier_intake import create, preview, questions
from blueprint_ai.evolution.template_update import apply, plan, rollback

CONFIG = """\
_subdirectory: template
project_name:
  type: str
  help: Project name
package: demo
use_cli:
  type: bool
  default: false
port:
  type: int
  default: 8000
license:
  type: str
  default: MIT
  choices: [MIT, Apache-2.0]
"""
README = "# {{ project_name }}\n\nCLI: {{use_cli}}, port {{ port }}\n"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(source: Path, files: dict[str, str | bytes], message: str) -> str:
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(source, "add", "-A")
    git(source, "commit", "-qm", message)
    return git(source, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "template-src"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    commit(
        root,
        {
            "copier.yml": CONFIG,
            "template/README.md.jinja": README,
            "template/src/{{package}}/__init__.py.jinja": '"""{{project_name}}."""\n',
            "template/static.txt": "kept {{ literally }} {% raw %}\n",
            "template/logo.bin": b"\x00\x01{{x}}\xff",
            "template/run.sh": "#!/bin/sh\necho ok\n",
        },
        "v1",
    )
    (root / "template/run.sh").chmod(0o755)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "exec")
    git(root, "tag", "v1")
    return root


def test_questions_are_typed_and_qualified(source: Path) -> None:
    result = questions(source, "v1")
    assert result["supported"] and result["subdirectory"] == "template"
    by_name = {q["name"]: q for q in result["questions"]}
    assert by_name["project_name"] == {
        "name": "project_name",
        "type": "str",
        "help": "Project name",
        "required": True,
    }
    assert by_name["package"]["default"] == "demo"
    assert by_name["use_cli"]["default"] is False and by_name["port"]["default"] == 8000
    assert [c["value"] for c in by_name["license"]["choices"]] == ["MIT", "Apache-2.0"]


def test_preview_binds_answers_without_creating_target(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "new-project"
    result = preview(source, target, "v1", {"project_name": "Shop", "use_cli": False})
    assert not target.exists()
    assert result["answers"] == {
        "project_name": "Shop",
        "package": "demo",
        "use_cli": False,
        "port": 8000,
        "license": "MIT",
    }
    assert result["answer_sources"]["use_cli"] == "explicit"
    assert result["answer_sources"]["port"] == "default"
    files = {row["path"]: row for row in result["files"]}
    assert set(files) == {"README.md", "src/demo/__init__.py", "static.txt", "logo.bin", "run.sh"}
    assert files["run.sh"]["mode"] == 0o755 and not files["static.txt"]["rendered"]
    assert result["excluded"] == []
    again = preview(source, target, "v1", {"use_cli": False, "project_name": "Shop"})
    assert again["preview_sha256"] == result["preview_sha256"]
    other = preview(source, tmp_path / "elsewhere", "v1", {"project_name": "Shop"})
    assert other["preview_sha256"] != result["preview_sha256"]


def test_v1_templates_keep_v1_binding_and_evaluate(source: Path, tmp_path: Path) -> None:
    from blueprint_ai.evolution.copier_intake import evaluate
    from blueprint_ai.evolution.template_update import _json, _sha

    result = preview(source, tmp_path / "out", "v1", {"project_name": "Shop"})
    v1_binding = (
        "dialect source target ref commit config_path subdirectory templates_suffix answers"
        " answer_sources excluded files file_count total_bytes record record_sha256"
    ).split()
    assert result["dialect"] == "copier-safe-v1" and "skipped" not in result
    assert result["preview_sha256"] == _sha(_json({k: result[k] for k in v1_binding}))
    evaluated = evaluate(source, "v1", {"project_name": "Shop"})
    assert evaluated["dialect"] == "copier-safe-v1" and evaluated["complete"]
    assert evaluated["answers"] == result["answers"] and evaluated["skipped"] == []
    with pytest.raises(ValueError, match="requires copier-safe-v1, not copier-safe-v2"):
        create(
            source,
            tmp_path / "out",
            "v1",
            {"project_name": "Shop"},
            result["preview_sha256"],
            "copier-safe-v2",
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"project_name": "A", "use_cli": "false"}, "use_cli must be a JSON bool"),
        ({"project_name": "A", "use_cli": 0}, "use_cli must be a JSON bool"),
        ({"project_name": "A", "port": True}, "port must be a JSON int"),
        ({"project_name": "A", "port": "8000"}, "port must be a JSON int"),
        ({"project_name": "A", "license": "GPL"}, "not one of the choices"),
        ({"project_name": "A", "extra": 1}, "unknown answers: extra"),
        ({}, "missing required answers: project_name"),
        ({"project_name": "../x", "package": ".."}, "unsafe"),
        ({"project_name": "A", "package": "a/b"}, "unsafe"),
        ({"project_name": "A", "package": ".git"}, "unsafe"),
    ],
)
def test_invalid_answers_refuse(source: Path, tmp_path: Path, answers: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        preview(source, tmp_path / "out", "v1", answers)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ("_tasks: [echo hi]\nname: x\n", "_tasks: unsupported template setting"),
        ("_exclude: ['*.md']\n", "_exclude: unsupported template setting"),
        (
            "token:\n  type: str\n  secret: true\n  default: x\n",
            "unsupported question fields: secret",
        ),
        ("a:\n  type: str\n  validator: x\n", "unsupported question fields: validator"),
        ("a:\n  type: yaml\n", "type must be declared"),
        ("a:\n  help: no type\n", "type must be declared"),
        ("a:\n  type: str\n  default: '{{ b }}'\n", "default must be a literal"),
        ("a:\n  type: str\n  default: 3\n", "default must be a literal str"),
        ("a: &x 1\nb: *x\n", "anchors and aliases"),
        ("a: 1\na: 2\n", "duplicate YAML key"),
        ("a: !include other.yml\n", "YAML tag"),
        ("a: 1\n---\nb: 2\n", "multiple YAML documents"),
        ("a: 1.5\n", "type must be declared"),
    ],
)
def test_unsupported_config_refuses_before_writes(
    source: Path, tmp_path: Path, config: str, message: str
) -> None:
    commit(source, {"copier.yml": config}, "bad config")
    result = questions(source, "HEAD")
    assert not result["supported"]
    assert any(message in reason for reason in result["refusals"])
    with pytest.raises(ValueError, match="outside copier-safe-v1|YAML"):
        preview(source, tmp_path / "out", "HEAD", {})
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (
            {"template/x.txt.jinja": "{% if use_cli %}y{% endif %}"},
            "unsupported template expression",
        ),
        ({"template/x.txt.jinja": "{{ project_name | upper }}"}, "unsupported template expression"),
        ({"template/x.txt.jinja": "{{ unknown }}"}, "unknown template variable"),
        ({"template/README.md": "dup"}, "both render to README.md"),
        ({"template/readme.md": "dup"}, "both render to readme.md"),
        ({"template/.blueprint-ai/template.json": "{}"}, "reserved"),
        ({"template/src/demo": "file"}, "collide"),
        ({"template/big.bin": b"\0" * (4 * 1024 * 1024 + 1)}, "exceeds 4 MiB"),
    ],
)
def test_unsafe_rendering_refuses(source: Path, tmp_path: Path, files: dict, message: str) -> None:
    commit(source, files, "unsafe")
    with pytest.raises(ValueError, match=message):
        preview(source, tmp_path / "out", "HEAD", {"project_name": "A"})


def test_symlink_in_template_refuses(source: Path, tmp_path: Path) -> None:
    os.symlink("/etc/passwd", source / "template" / "link")
    commit(source, {}, "symlink")
    with pytest.raises(ValueError, match="unsupported entry"):
        preview(source, tmp_path / "out", "HEAD", {"project_name": "A"})


def test_create_requires_matching_digest_and_records_typed_answers(
    source: Path, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    answers = {"project_name": "Shop", "use_cli": False, "port": 9000}
    with pytest.raises(ValueError, match="digest does not match"):
        create(source, target, "v1", answers, "0" * 64)
    assert not target.exists()
    digest = preview(source, target, "v1", answers)["preview_sha256"]
    with pytest.raises(ValueError, match="digest does not match"):
        create(source, target, "v1", {**answers, "port": 9001}, digest)
    result = create(source, target, "v1", answers, digest)
    assert result["status"] == "initialized"
    assert (target / "README.md").read_text() == "# Shop\n\nCLI: False, port 9000\n"
    assert (target / "src/demo/__init__.py").read_text() == '"""Shop."""\n'
    assert (target / "static.txt").read_text() == "kept {{ literally }} {% raw %}\n"
    assert (target / "logo.bin").read_bytes() == b"\x00\x01{{x}}\xff"
    assert os.access(target / "run.sh", os.X_OK)
    record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert record["schema"] == 2 and record["format"] == "copier-safe-v1"
    assert record["answers"]["use_cli"] is False and record["answers"]["port"] == 9000
    with pytest.raises(ValueError, match="must be empty"):
        preview(source, target, "v1", answers)


def test_update_with_new_default_question_and_rollback(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "project"
    answers = {"project_name": "Shop"}
    create(source, target, "v1", answers, preview(source, target, "v1", answers)["preview_sha256"])
    (target / "README.md").write_text("Authored note.\n" + (target / "README.md").read_text())
    v2 = commit(
        source,
        {
            "copier.yml": CONFIG + "ci:\n  type: bool\n  default: true\n",
            "template/README.md.jinja": README + "CI: {{ ci }}\n",
            "template/CHANGELOG.md": "v2\n",
        },
        "v2",
    )
    saved = plan(target, v2)
    assert saved["status"] == "ready"
    result = apply(target, saved)
    assert (target / "README.md").read_text() == (
        "Authored note.\n# Shop\n\nCLI: False, port 8000\nCI: True\n"
    )
    record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert record["commit"] == v2 and record["answers"]["ci"] is True
    rollback(target, result["operation_id"])
    assert not (target / "CHANGELOG.md").exists()
    assert json.loads((target / ".blueprint-ai/template.json").read_text())["schema"] == 2


@pytest.mark.parametrize(
    "config",
    [
        CONFIG.replace("port:\n  type: int", "port:\n  type: str").replace("8000", "'8000'"),
        CONFIG.replace("port:\n  type: int\n  default: 8000\n", ""),
        CONFIG + "owner:\n  type: str\n",
    ],
)
def test_unsupported_update_transition_refuses(source: Path, tmp_path: Path, config: str) -> None:
    target = tmp_path / "project"
    answers = {"project_name": "Shop"}
    create(source, target, "v1", answers, preview(source, target, "v1", answers)["preview_sha256"])
    before = (target / ".blueprint-ai/template.json").read_bytes()
    v2 = commit(source, {"copier.yml": config}, "v2")
    with pytest.raises(ValueError, match="unsupported for this project"):
        plan(target, v2)
    assert (target / ".blueprint-ai/template.json").read_bytes() == before


def test_cli_questions_preview_and_confirmed_init(source: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    answers = tmp_path / "answers.json"
    answers.write_text('{"project_name": "Cli", "use_cli": true}')
    target = tmp_path / "cli-project"
    shown = runner.invoke(app, ["template", "questions", str(source), "--ref", "v1"])
    assert shown.exit_code == 0 and json.loads(shown.stdout)["supported"]
    args = [str(source), str(target), "--ref", "v1", "--answers", str(answers)]
    viewed = runner.invoke(app, ["template", "preview", *args])
    assert viewed.exit_code == 0 and not target.exists()
    digest = json.loads(viewed.stdout)["preview_sha256"]
    missing = runner.invoke(app, ["template", "init", *args, "--format", "copier-safe-v1"])
    assert missing.exit_code == 2 and not target.exists()
    made = runner.invoke(
        app, ["template", "init", *args, "--format", "copier-safe-v1", "--confirm-digest", digest]
    )
    assert made.exit_code == 0, made.output
    assert "CLI: True" in (target / "README.md").read_text()
    answers.write_text('{"project_name": "Cli", "project_name": "Other"}')
    duplicate = runner.invoke(app, ["template", "preview", *args])
    assert duplicate.exit_code == 2


@pytest.mark.parametrize("verification_failure", [False, True])
def test_creation_preserves_a_concurrently_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verification_failure: bool
) -> None:
    from blueprint_ai.evolution import template_update

    target = tmp_path / "new-project"
    original_git = template_update._git

    def concurrent_git(directory: Path, *args: str) -> bytes:
        result = original_git(directory, *args)
        if args == ("init", "-q"):
            target.mkdir(exist_ok=True)
            (target / "unrelated.txt").write_text("Concurrent work\n")
        return result

    monkeypatch.setattr(template_update, "_git", concurrent_git)
    if verification_failure:

        def failed_read(*_args: object) -> None:
            raise OSError("Synthetic verification failure")

        monkeypatch.setattr(template_update, "_read", failed_read)
    with pytest.raises((ValueError, OSError)):
        template_update._create(
            target,
            {"README.md": (b"Scaffold\n", 0o644)},
            {"schema": 1, "source": "synthetic", "commit": "a" * 40, "answers": {}},
        )
    assert (target / "unrelated.txt").read_text() == "Concurrent work\n"
    assert not (target / "README.md").exists()
    assert not list(tmp_path.glob(".blueprint-create-*"))
