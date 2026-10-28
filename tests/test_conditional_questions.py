"""copier-safe-v2: ordered `when` evaluation, multiselect answers and their update lifecycle."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from blueprint_ai.cli import app
from blueprint_ai.evolution.copier_intake import create, evaluate, preview, questions
from blueprint_ai.evolution.template_update import apply, inspect_target, plan, rollback

CONFIG = """\
_subdirectory: template
project_name:
  type: str
  help: Project name
ci:
  type: bool
  help: Enable CI
ci_provider:
  type: str
  help: CI provider
  choices: [github, gitlab]
  when: "{{ ci }}"
manual_release:
  type: bool
  default: true
  when: "{{ not ci }}"
features:
  type: str
  help: Optional features
  multiselect: true
  choices: {Docs: docs, Lint: lint, Tests: tests}
  default: [tests, docs]
ports:
  type: int
  multiselect: true
  choices: [80, 443, 8080]
  default: []
"""
README = "# {{ project_name }}\nCI: {{ ci }}\nFeatures: {{ features }}\nPorts: {{ ports }}\n"
CI_FILE = {"template/ci.yml.jinja": "provider: {{ ci_provider }}\n"}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(source: Path, files: dict[str, str], message: str) -> str:
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(source, "add", "-A")
    git(source, "commit", "-qm", message)
    return git(source, "rev-parse", "HEAD")


def tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "template-src"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    commit(root, {"copier.yml": CONFIG, "template/README.md.jinja": README}, "v1")
    git(root, "tag", "v1")
    return root


def names(result: dict) -> list[str]:
    return [q["name"] for q in result["questions"]]


def test_questions_report_v2_descriptors(source: Path) -> None:
    result = questions(source, "v1")
    assert result["supported"] and result["dialect"] == "copier-safe-v2"
    by_name = {q["name"]: q for q in result["questions"]}
    assert by_name["ci_provider"]["when"] == {"question": "ci", "equals": True}
    assert by_name["manual_release"]["when"] == {"question": "ci", "equals": False}
    assert by_name["features"]["multiselect"] is True
    assert by_name["features"]["default"] == ["docs", "tests"]
    assert by_name["features"]["choices"][0] == {"label": "Docs", "value": "docs"}
    assert "when" not in by_name["project_name"] and "multiselect" not in by_name["ci"]


def test_unanswered_controller_leaves_dependants_unresolved(source: Path) -> None:
    result = evaluate(source, "v1", {"project_name": "Shop"})
    assert result["evaluation"] == "blueprint-template-evaluation/1"
    assert names(result) == ["project_name", "ci", "features", "ports"]
    assert result["missing"] == ["ci"] and result["skipped"] == []
    assert result["unresolved"] == [
        {"name": "ci_provider", "waiting_for": "ci"},
        {"name": "manual_release", "waiting_for": "ci"},
    ]
    assert not result["complete"]
    assert result["answers"] == {"project_name": "Shop", "features": ["docs", "tests"], "ports": []}
    assert result["answer_sources"] == {
        "project_name": "explicit",
        "features": "default",
        "ports": "default",
    }


def test_enabled_ci_shows_provider_and_skips_manual_release(source: Path) -> None:
    partial = evaluate(source, "v1", {"project_name": "Shop", "ci": True})
    assert names(partial) == ["project_name", "ci", "ci_provider", "features", "ports"]
    provider = partial["questions"][2]
    assert provider["status"] == "missing" and provider["choices"][1]["value"] == "gitlab"
    assert partial["skipped"] == ["manual_release"] and not partial["complete"]
    done = evaluate(source, "v1", {"project_name": "Shop", "ci": True, "ci_provider": "github"})
    assert done["complete"] and done["answers"]["ci_provider"] == "github"
    assert "manual_release" not in done["answers"]


def test_disabling_ci_drops_a_filled_provider(source: Path) -> None:
    result = evaluate(source, "v1", {"project_name": "Shop", "ci": False, "ci_provider": "github"})
    assert result["complete"] and result["skipped"] == ["ci_provider"]
    assert "ci_provider" not in result["answers"] and "ci_provider" not in names(result)
    assert result["answers"]["manual_release"] is True
    assert result["answer_sources"]["manual_release"] == "default"


def test_invalid_answers_are_reported_without_discarding_valid_input(source: Path) -> None:
    result = evaluate(
        source,
        "v1",
        {"project_name": "Shop", "ci": "true", "features": ["lint"], "ports": ["80"], "x": 1},
    )
    assert result["errors"] == {
        "ci": "must be a JSON bool, got str",
        "ports": "selection 0 must be a JSON int, got str",
    }
    assert result["unknown"] == ["x"] and not result["complete"]
    assert result["answers"] == {"project_name": "Shop", "features": ["lint"]}
    assert [q["status"] for q in result["questions"]] == [
        "explicit",
        "invalid",
        "explicit",
        "invalid",
    ]
    assert {u["name"] for u in result["unresolved"]} == {"ci_provider", "manual_release"}
    with pytest.raises(ValueError, match="answers must be a JSON object"):
        evaluate(source, "v1", ["ci"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["tests", "lint"], ["lint", "tests"]),
        (["docs"], ["docs"]),
        ([], []),
        (["tests", "docs", "lint"], ["docs", "lint", "tests"]),
    ],
)
def test_multiselect_keeps_declared_choice_order(source: Path, value: list, expected: list) -> None:
    answers = {"project_name": "A", "ci": False, "features": value}
    assert evaluate(source, "v1", answers)["answers"]["features"] == expected


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("features", ["docs", "docs"], "selection 'docs' is repeated"),
        ("features", ["api"], "selection 'api' is not one of the choices"),
        ("features", "docs", "must be a JSON list of str choices, got str"),
        ("features", None, "must be a JSON list of str choices, got NoneType"),
        ("features", ["Docs"], "selection 'Docs' is not one of the choices"),
        ("ports", [True], "selection 0 must be a JSON int, got bool"),
        ("ports", [80, 22], "selection 22 is not one of the choices"),
        ("ports", [80] * 65, "has more than 64 selections"),
    ],
)
def test_multiselect_refuses_untyped_or_repeated_values(
    source: Path, tmp_path: Path, name: str, value: object, error: str
) -> None:
    answers = {"project_name": "A", "ci": False, name: value}
    assert evaluate(source, "v1", answers)["errors"] == {name: error}
    with pytest.raises(ValueError, match=f"answer {name} "):
        preview(source, tmp_path / "out", "v1", answers)


def test_multiselect_renders_as_a_json_array(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "out"
    answers = {"project_name": "A", "ci": False, "features": ["tests", "lint"], "ports": [443, 80]}
    result = preview(source, target, "v1", answers)
    readme = next(d["diff"] for d in result["diffs"] if d["path"] == "README.md")
    assert '+Features: ["lint", "tests"]\n' in readme and "+Ports: [80, 443]\n" in readme
    assert "+CI: False\n" in readme


@pytest.mark.parametrize(
    ("question", "message"),
    [
        ("type: str\n  multiselect: true\n  choices: [a, b]\n  default: [c]", "not one of"),
        ("type: str\n  multiselect: true\n  choices: [a, b]\n  default: [a, a]", "repeated"),
        ("type: str\n  multiselect: true\n  choices: [a, b]\n  default: a", "JSON list"),
        ("type: int\n  multiselect: true\n  choices: [1, 2]\n  default: ['1']", "JSON int"),
        ("type: str\n  multiselect: true", "multiselect needs literal choices"),
        ("type: str\n  multiselect: 'yes'\n  choices: [a]", "multiselect must be true or false"),
        ("type: str\n  multiselect: true\n  choices: '{{ x }}'", "choices must be a literal"),
    ],
)
def test_multiselect_declarations_are_validated(source: Path, question: str, message: str) -> None:
    commit(source, {"copier.yml": f"pick:\n  {question}\n"}, "bad multiselect")
    result = questions(source, "HEAD")
    assert not result["supported"] and any(message in r for r in result["refusals"])


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ("'{{ ci and lint }}'", "when must be true, false"),
        ("'{{ ci == true }}'", "when must be true, false"),
        ("'{{ ci.enabled }}'", "when must be true, false"),
        ("'{{ ci() }}'", "when must be true, false"),
        ("'{{ ci | bool }}'", "when must be true, false"),
        ("'{{ not not ci }}'", "when must be true, false"),
        ("'{{ not }}'", "when must be true, false"),
        ("'{{ True }}'", "when must be true, false"),
        ("'{{ ci }} '", "when must be true, false"),
        ("'{% if ci %}true{% endif %}'", "when must be true, false"),
        ("\"{{ __import__('os') }}\"", "when must be true, false"),
        ("'true'", "when must be true, false"),
        ("1", "when must be true, false"),
        ("'{{ later }}'", "when must refer to an earlier question, not later"),
        ("'{{ pick }}'", "when must refer to an earlier question, not pick"),
        ("'{{ nope }}'", "when refers to unknown question nope"),
        ("'{{ name }}'", "when refers to non-boolean question name"),
        ("'{{ many }}'", "when refers to non-boolean question many"),
        ("'{{ broken }}'", "when refers to unsupported question broken"),
    ],
)
def test_unsupported_conditions_refuse(
    source: Path, tmp_path: Path, when: str, message: str
) -> None:
    config = (
        "ci: false\nname: x\nbroken:\n  type: yaml\n"
        "many:\n  type: bool\n  multiselect: true\n  choices: [true, false]\n"
        f"pick:\n  type: str\n  default: a\n  when: {when}\nlater: false\n"
    )
    commit(source, {"copier.yml": config}, "bad when")
    shown = evaluate(source, "HEAD", {})
    assert shown["dialect"] == "copier-safe-v2" and not shown["supported"]
    assert any(message in reason for reason in shown["refusals"]), shown["refusals"]
    assert shown["questions"] == [] and not shown["complete"]
    with pytest.raises(ValueError, match="outside copier-safe-v2"):
        preview(source, tmp_path / "out", "HEAD", {})


@pytest.mark.parametrize(
    "config",
    [
        "token:\n  type: str\n  secret: true\n  default: x\n  when: true\n",
        "a:\n  type: str\n  validator: '{{ a }}'\n  when: false\n",
        "a:\n  type: str\n  default: x\n  when: false\n_tasks: [echo]\n",
        "a:\n  type: str\n  default: x\n  when: false\n_migrations: []\n",
        "a:\n  type: str\n  default: x\n  when: false\n_jinja_extensions: [x]\n",
    ],
)
def test_other_copier_features_stay_refused(source: Path, config: str) -> None:
    commit(source, {"copier.yml": config}, "unsupported")
    assert not questions(source, "HEAD")["supported"]


def test_literal_when_values(source: Path, tmp_path: Path) -> None:
    commit(
        source,
        {
            "copier.yml": "a:\n  type: str\n  default: x\n  when: false\n"
            "b:\n  type: str\n  default: y\n  when: true\n"
            "c:\n  type: bool\n  when: '{{ a }}'\n",
            "README.md": "{{ b }}\n",
        },
        "literal",
    )
    shown = questions(source, "HEAD")
    assert not shown["supported"]  # `a` is a str, so it cannot control `c`
    commit(
        source,
        {
            "copier.yml": "a:\n  type: str\n  default: x\n  when: false\n"
            "b:\n  type: str\n  default: y\n  when: true\n"
        },
        "literal ok",
    )
    result = evaluate(source, "HEAD", {"a": "ignored"})
    assert result["skipped"] == ["a"] and result["answers"] == {"b": "y"} and result["complete"]
    assert questions(source, "HEAD")["questions"][1].get("when") is None


def test_skipped_controller_skips_its_dependants(source: Path) -> None:
    commit(
        source,
        {
            "copier.yml": CONFIG + "coverage:\n  type: bool\n  default: true\n"
            "  when: '{{ manual_release }}'\n"
        },
        "chained",
    )
    enabled = evaluate(source, "HEAD", {"project_name": "A", "ci": True, "ci_provider": "github"})
    assert enabled["skipped"] == ["manual_release", "coverage"]
    disabled = evaluate(source, "HEAD", {"project_name": "A", "ci": False})
    assert disabled["answers"]["coverage"] is True
    off = evaluate(source, "HEAD", {"project_name": "A", "ci": False, "manual_release": False})
    assert off["skipped"] == ["ci_provider", "coverage"]


def test_evaluation_and_preview_write_nothing(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "new-project"
    answers = {"project_name": "Shop", "ci": True, "ci_provider": "github"}
    before = tree(tmp_path)
    questions(source, "v1")
    evaluate(source, "v1", {"project_name": "Shop"})
    evaluate(source, "v1", answers)
    preview(source, target, "v1", answers)
    with pytest.raises(ValueError):
        preview(source, target, "v1", {"project_name": "Shop"})
    target.mkdir()
    after_mkdir = tree(tmp_path)
    preview(source, target, "v1", answers)
    assert tree(tmp_path) == after_mkdir
    target.rmdir()
    assert tree(tmp_path) == before


def test_changed_answers_invalidate_confirmation(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "project"
    answers = {"project_name": "Shop", "ci": True, "ci_provider": "github", "features": ["docs"]}
    digest = preview(source, target, "v1", answers)["preview_sha256"]
    for changed in (
        {**answers, "ci": False},
        {**answers, "ci_provider": "gitlab"},
        {**answers, "features": ["docs", "lint"]},
        {**answers, "features": []},
    ):
        assert preview(source, target, "v1", changed)["preview_sha256"] != digest
        with pytest.raises(ValueError, match="digest does not match"):
            create(source, target, "v1", changed, digest)
        assert not target.exists()
    with pytest.raises(ValueError, match="requires copier-safe-v2, not copier-safe-v1"):
        create(source, target, "v1", answers, digest, "copier-safe-v1")
    assert not target.exists()


def test_hidden_input_has_no_effect_on_preview_or_record(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "project"
    clean = {"project_name": "Shop", "ci": False, "features": ["tests", "docs"]}
    stale = {**clean, "ci_provider": "gitlab", "features": ["docs", "tests"]}
    shown = preview(source, target, "v1", stale)
    assert shown["preview_sha256"] == preview(source, target, "v1", clean)["preview_sha256"]
    assert shown["skipped"] == ["ci_provider"] and "ci_provider" not in shown["answers"]
    result = create(source, target, "v1", stale, shown["preview_sha256"], "copier-safe-v2")
    assert result["dialect"] == "copier-safe-v2"
    record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert record["schema"] == 3 and record["format"] == "copier-safe-v2"
    assert record["answers"] == {
        "project_name": "Shop",
        "ci": False,
        "manual_release": True,
        "features": ["docs", "tests"],
        "ports": [],
    }
    written = tree(target)
    assert all(b"gitlab" not in content for content in written.values() if content)


def test_reference_to_a_skipped_answer_refuses(source: Path, tmp_path: Path) -> None:
    commit(source, CI_FILE, "ci file")
    with pytest.raises(ValueError, match="ci_provider belongs to a skipped question"):
        preview(source, tmp_path / "out", "HEAD", {"project_name": "A", "ci": False})
    shown = preview(
        source, tmp_path / "out", "HEAD", {"project_name": "A", "ci": True, "ci_provider": "gitlab"}
    )
    ci = next(d["diff"] for d in shown["diffs"] if d["path"] == "ci.yml")
    assert "+provider: gitlab\n" in ci
    commit(source, {"template/{{ features }}.txt": "x"}, "multiselect path")
    with pytest.raises(ValueError, match="multiselect answer features cannot be used in a path"):
        preview(
            source,
            tmp_path / "out",
            "HEAD",
            {"project_name": "A", "ci": True, "ci_provider": "gitlab"},
        )
    assert not (tmp_path / "out").exists()


def made(source: Path, target: Path, ref: str, answers: dict) -> None:
    create(source, target, ref, answers, preview(source, target, ref, answers)["preview_sha256"])


def test_v2_update_adds_conditional_question_and_rolls_back(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "project"
    commit(source, CI_FILE, "ci file")
    answers = {"project_name": "Shop", "ci": True, "ci_provider": "github", "features": ["docs"]}
    made(source, target, "HEAD", answers)
    record_before = (target / ".blueprint-ai/template.json").read_bytes()
    (target / "README.md").write_text("Authored.\n" + (target / "README.md").read_text())
    (target / "NOTES.md").write_text("mine\n")
    git(target, "config", "user.name", "Fixture")
    git(target, "config", "user.email", "fixture@example.invalid")
    git(target, "add", "-A")
    git(target, "commit", "-qm", "created and authored")
    v2 = commit(
        source,
        {
            "copier.yml": CONFIG + "coverage:\n  type: bool\n  default: true\n  when: '{{ ci }}'\n"
            "badge:\n  type: str\n  default: none\n  when: '{{ not ci }}'\n",
            "template/README.md.jinja": README + "Coverage: {{ coverage }}\n",
            "template/ci.yml.jinja": "provider: {{ ci_provider }}\ncoverage: {{ coverage }}\n",
        },
        "v2",
    )
    saved = plan(target, v2)
    assert saved["status"] == "ready"
    result = apply(target, saved)
    assert (target / "README.md").read_text() == (
        'Authored.\n# Shop\nCI: True\nFeatures: ["docs"]\nPorts: []\nCoverage: True\n'
    )
    assert (target / "ci.yml").read_text() == "provider: github\ncoverage: True\n"
    record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert record["schema"] == 3 and record["commit"] == v2
    assert record["answers"]["coverage"] is True and "badge" not in record["answers"]
    assert plan(target, v2)["status"] == "noop"
    rollback(target, result["operation_id"])
    assert (target / ".blueprint-ai/template.json").read_bytes() == record_before
    assert (target / "ci.yml").read_text() == "provider: github\n"
    assert (target / "NOTES.md").read_text() == "mine\n"
    assert (target / "README.md").read_text().startswith("Authored.\n")


def test_update_drops_a_recorded_answer_that_becomes_skipped(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "project"
    made(source, target, "v1", {"project_name": "Shop", "ci": False, "features": ["lint"]})
    gated = CONFIG.replace(
        "features:\n",
        "extras:\n  type: bool\n  default: false\nfeatures:\n  when: '{{ extras }}'\n",
    )
    still_used = commit(source, {"copier.yml": gated}, "gated features")
    before = (target / ".blueprint-ai/template.json").read_bytes()
    with pytest.raises(ValueError, match="features belongs to a skipped question"):
        plan(target, still_used)
    assert (target / ".blueprint-ai/template.json").read_bytes() == before
    unused = commit(
        source,
        {"template/README.md.jinja": README.replace("Features: {{ features }}\n", "")},
        "readme without features",
    )
    result = apply(target, plan(target, unused))
    answers = json.loads((target / ".blueprint-ai/template.json").read_text())["answers"]
    assert "features" not in answers and answers["extras"] is False
    assert "Features" not in (target / "README.md").read_text()
    rollback(target, result["operation_id"])
    assert (target / ".blueprint-ai/template.json").read_bytes() == before


def test_v1_record_upgrades_to_v2_and_rolls_back(tmp_path: Path) -> None:
    source = tmp_path / "plain"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    v1 = commit(
        source, {"copier.yml": "name:\n  type: str\n", "README.md.jinja": "# {{ name }}\n"}, "v1"
    )
    target = tmp_path / "project"
    made(source, target, v1, {"name": "Shop"})
    record_before = (target / ".blueprint-ai/template.json").read_bytes()
    assert json.loads(record_before)["schema"] == 2
    v2 = commit(
        source,
        {
            "copier.yml": "name:\n  type: str\nci:\n  type: bool\n  default: false\n"
            "ci_provider:\n  type: str\n  default: github\n  when: '{{ ci }}'\n",
            "README.md.jinja": "# {{ name }}\nCI: {{ ci }}\n",
        },
        "v2",
    )
    result = apply(target, plan(target, v2))
    assert (target / "README.md").read_text() == "# Shop\nCI: False\n"
    record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert (record["schema"], record["format"]) == (3, "copier-safe-v2")
    assert record["answers"] == {"name": "Shop", "ci": False}
    rollback(target, result["operation_id"])
    assert (target / ".blueprint-ai/template.json").read_bytes() == record_before


@pytest.mark.parametrize(
    ("schema", "format_"),
    [(3, "copier-safe-v1"), (2, "copier-safe-v2"), (4, None), ([3], "copier-safe-v2")],
)
def test_mismatched_record_schema_refuses(
    source: Path, tmp_path: Path, schema: object, format_: object
) -> None:
    target = tmp_path / "project"
    made(source, target, "v1", {"project_name": "Shop", "ci": False})
    path = target / ".blueprint-ai/template.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps(record | {"schema": schema, "format": format_}))
    with pytest.raises(ValueError, match="invalid template record"):
        plan(target, "v1")


def test_cli_evaluate_preview_and_init_with_json_answers(source: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    answers = tmp_path / "answers.json"
    answers.write_text('{"project_name": "Cli", "ci": true}')
    evaluated = runner.invoke(
        app, ["template", "evaluate", str(source), "--ref", "v1", "--answers", str(answers)]
    )
    assert evaluated.exit_code == 0, evaluated.output
    partial = json.loads(evaluated.stdout)
    assert not partial["complete"] and partial["missing"] == ["ci_provider"]
    assert partial["skipped"] == ["manual_release"]
    bare = runner.invoke(app, ["template", "evaluate", str(source), "--ref", "v1"])
    assert json.loads(bare.stdout)["missing"] == ["project_name", "ci"]
    answers.write_text(
        '{"project_name": "Cli", "ci": true, "ci_provider": "gitlab", "features": ["lint"]}'
    )
    complete = runner.invoke(
        app, ["template", "evaluate", str(source), "--ref", "v1", "--answers", str(answers)]
    )
    assert json.loads(complete.stdout)["complete"]
    target = tmp_path / "cli-project"
    args = [str(source), str(target), "--ref", "v1", "--answers", str(answers)]
    viewed = runner.invoke(app, ["template", "preview", *args])
    assert viewed.exit_code == 0 and not target.exists()
    digest = json.loads(viewed.stdout)["preview_sha256"]
    wrong = runner.invoke(
        app, ["template", "init", *args, "--format", "copier-safe-v1", "--confirm-digest", digest]
    )
    assert wrong.exit_code == 2 and not target.exists()
    made_ = runner.invoke(
        app, ["template", "init", *args, "--format", "copier-safe-v2", "--confirm-digest", digest]
    )
    assert made_.exit_code == 0, made_.output
    assert 'Features: ["lint"]' in (target / "README.md").read_text()


def test_cli_recipe_lifecycle_inspects_drift_and_previews_conflicting_update(
    source: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    commit(source, CI_FILE, "first composed recipe")
    initial_commit = git(source, "rev-parse", "HEAD")
    answers = tmp_path / "recipe-inputs.json"
    answers.write_text(json.dumps({"project_name": "Shop", "ci": True, "ci_provider": "github"}))
    target = tmp_path / "bounded-output" / "shop"

    questionnaire = runner.invoke(
        app, ["template", "questions", str(source), "--ref", initial_commit]
    )
    assert questionnaire.exit_code == 0, questionnaire.output
    declared = json.loads(questionnaire.stdout)
    assert declared["supported"] and declared["dialect"] == "copier-safe-v2"

    evaluated = runner.invoke(
        app,
        ["template", "evaluate", str(source), "--ref", initial_commit, "--answers", str(answers)],
    )
    assert evaluated.exit_code == 0, evaluated.output
    evaluation = json.loads(evaluated.stdout)
    assert evaluation["complete"] and evaluation["answers"]["ci_provider"] == "github"

    previewed = runner.invoke(
        app,
        [
            "template",
            "preview",
            str(source),
            str(target),
            "--ref",
            initial_commit,
            "--answers",
            str(answers),
        ],
    )
    assert previewed.exit_code == 0, previewed.output
    preview_value = json.loads(previewed.stdout)
    assert not target.exists(), "preview resolves files without materializing the target"
    assert preview_value["file_count"] == 2
    assert {row["path"] for row in preview_value["files"]} == {"README.md", "ci.yml"}
    assert preview_value["total_bytes"] > 0
    assert all(row["sha256"] and row["mode"] for row in preview_value["files"])

    initialized = runner.invoke(
        app,
        [
            "template",
            "init",
            str(source),
            str(target),
            "--ref",
            initial_commit,
            "--answers",
            str(answers),
            "--format",
            "copier-safe-v2",
            "--confirm-digest",
            preview_value["preview_sha256"],
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert record == {
        "schema": 3,
        "format": "copier-safe-v2",
        "source": str(source.resolve()),
        "commit": initial_commit,
        "answers": evaluation["answers"],
    }
    clean = runner.invoke(app, ["template", "inspect", str(target)])
    assert clean.exit_code == 0, clean.output
    clean_value = json.loads(clean.stdout)
    assert clean_value["status"] == "current"
    assert clean_value["recipe"]["source"] == str(source.resolve())
    assert clean_value["recipe"]["commit"] == initial_commit
    assert clean_value["recipe"]["answers"] == evaluation["answers"]
    assert clean_value["summary"] == {
        "file_count": 2,
        "unchanged": 2,
        "modified": 0,
        "missing": 0,
    }
    generated_ci = (target / "ci.yml").read_bytes()
    (target / "ci.yml").unlink()
    missing_file = inspect_target(target)
    assert missing_file["status"] == "drift"
    assert (
        next(row for row in missing_file["files"] if row["path"] == "ci.yml")["status"] == "missing"
    )
    (target / "ci.yml").write_bytes(generated_ci)

    authored = (target / "README.md").read_text().replace("CI: True", "CI: Human choice", 1)
    (target / "README.md").write_text(authored)
    drifted = inspect_target(target)
    assert drifted["status"] == "drift"
    assert (
        next(row for row in drifted["files"] if row["path"] == "README.md")["status"] == "modified"
    )
    assert next(row for row in drifted["files"] if row["path"] == "ci.yml")["status"] == "unchanged"

    next_commit = commit(
        source,
        {
            "template/README.md.jinja": README.replace("CI: {{ ci }}", "CI: Enabled {{ ci }}"),
            "template/ci.yml.jinja": "provider: {{ ci_provider }}\ncoverage: true\n",
            "template/START-HERE.md.jinja": "Project: {{ project_name }}\n",
        },
        "updated recipe",
    )
    conflict_plan_path = tmp_path / "recipe-update-conflict.json"
    conflicting = runner.invoke(
        app,
        [
            "template",
            "plan",
            str(target),
            "--ref",
            next_commit,
            "--output",
            str(conflict_plan_path),
        ],
    )
    assert conflicting.exit_code == 0, conflicting.output
    saved_conflict = json.loads(conflict_plan_path.read_text())
    assert saved_conflict["status"] == "conflict"
    assert next(row for row in saved_conflict["rows"] if row["path"] == "README.md")[
        "conflict_reasons"
    ] == ["content"]
    before_refusal = tree(target)
    refused_apply = runner.invoke(
        app,
        ["template", "apply", str(target), "--plan", str(conflict_plan_path)],
    )
    assert refused_apply.exit_code == 2
    assert tree(target) == before_refusal

    resolved_path = tmp_path / "recipe-update-resolved.json"
    resolved = runner.invoke(
        app,
        [
            "template",
            "plan",
            str(target),
            "--ref",
            next_commit,
            "--resolve",
            "README.md=project",
            "--output",
            str(resolved_path),
        ],
    )
    assert resolved.exit_code == 0, resolved.output
    saved_resolved = json.loads(resolved_path.read_text())
    assert saved_resolved["status"] == "ready"
    readme_row = next(row for row in saved_resolved["rows"] if row["path"] == "README.md")
    assert readme_row["status"] == "noop" and readme_row["after"] == readme_row["before"]
    published = runner.invoke(
        app,
        ["template", "apply", str(target), "--plan", str(resolved_path)],
    )
    assert published.exit_code == 0, published.output
    assert (target / "README.md").read_text() == authored
    assert (target / "ci.yml").read_text() == "provider: github\ncoverage: true\n"
    assert (target / "START-HERE.md").read_text() == "Project: Shop\n"
    updated_record = json.loads((target / ".blueprint-ai/template.json").read_text())
    assert updated_record["commit"] == next_commit
    assert updated_record["answers"] == evaluation["answers"]
    inspected_update = json.loads(runner.invoke(app, ["template", "inspect", str(target)]).stdout)
    assert inspected_update["status"] == "drift"
    assert (
        next(row for row in inspected_update["files"] if row["path"] == "README.md")["status"]
        == "modified"
    )
    assert (
        runner.invoke(app, ["template", "check", str(target), "--ref", next_commit]).exit_code == 0
    )


@pytest.mark.parametrize(
    "content",
    ['{"ci": true, "ci": false}', "[1]", "{not json", b"\xff\xfe", " " * (1024 * 1024 + 1)],
    ids=["duplicate", "array", "invalid", "not-utf8", "oversized"],
)
def test_cli_evaluate_refuses_bad_answer_files(
    source: Path, tmp_path: Path, content: str | bytes
) -> None:
    answers = tmp_path / "answers.json"
    if isinstance(content, bytes):
        answers.write_bytes(content)
    else:
        answers.write_text(content)
    args = ["template", "evaluate", str(source), "--ref", "v1", "--answers", str(answers)]
    assert CliRunner().invoke(app, args).exit_code == 2
    missing = ["template", "evaluate", str(source), "--ref", "v1", "--answers", str(tmp_path)]
    assert CliRunner().invoke(app, missing).exit_code == 2


def test_cli_evaluate_reports_unsupported_template(source: Path) -> None:
    commit(source, {"copier.yml": "a:\n  type: str\n  when: '{{ b or c }}'\n"}, "bad")
    result = CliRunner().invoke(app, ["template", "evaluate", str(source), "--ref", "HEAD"])
    assert result.exit_code == 3
    assert not json.loads(result.stdout)["supported"]
