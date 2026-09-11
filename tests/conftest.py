from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "__init__.py").write_text('__version__ = "1"\n')
    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = "demo"
version = "1.0.0"
dependencies = ["typer"]

[project.scripts]
demo = "demo:main"
"""
    )
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("ignored = True\n")
    return tmp_path


@pytest.fixture
def js_project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const value: number = 1;\n")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-web",
                "type": "module",
                "dependencies": {"react": "latest"},
                "devDependencies": {"vitest": "latest"},
                "scripts": {"test": "vitest"},
            }
        )
    )
    (tmp_path / "package-lock.json").write_text("{}")
    return tmp_path
