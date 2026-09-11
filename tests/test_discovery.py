import subprocess
from pathlib import Path

from blueprint_ai.discovery import discover_project, iter_project_files


def test_python_discovery_respects_gitignore_without_requiring_git(python_project: Path) -> None:
    facts = discover_project(python_project)
    assert facts.is_git is False
    assert facts.languages["Python"] == 1
    assert facts.package_managers == []
    assert facts.frameworks == ["typer"]
    assert facts.project_types == ["cli"]
    assert facts.ignored_count >= 1


def test_fixture_matrix_detects_web_and_iac(js_project: Path) -> None:
    (js_project / "main.tf").write_text('terraform { required_version = ">= 1.0" }\n')
    (js_project / "Dockerfile").write_text("FROM scratch\n")
    facts = discover_project(js_project)
    assert {"TypeScript", "HCL"} <= set(facts.languages)
    assert facts.package_managers == ["npm"]
    assert "web-app" in facts.project_types
    assert facts.iac == ["terraform"]
    assert facts.containers == ["Dockerfile"]


def test_configured_ignore_is_applied(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "hidden.py").write_text("")
    files, ignored = iter_project_files(tmp_path, ["skip/"])
    assert [path.name for path in files] == ["keep.py"]
    assert ignored == 1


def test_git_standard_excludes_are_respected(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    hidden = tmp_path / ".ref"
    hidden.mkdir()
    (hidden / "plan.md").write_text("private plan")
    (tmp_path / "visible.py").write_text("visible = True\n")
    (tmp_path / ".git" / "info" / "exclude").write_text(".ref/\n")
    files, ignored = iter_project_files(tmp_path)
    assert "visible.py" in {path.name for path in files}
    assert "plan.md" not in {path.name for path in files}
    assert ignored >= 1
