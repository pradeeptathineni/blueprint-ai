from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from blueprint_ai.core import Finding, ProjectFacts

TEMPLATES = {
    ".editorconfig": """root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.md]
trim_trailing_whitespace = false
""",
    ".pre-commit-config.yaml": """repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: check-yaml
      - id: end-of-file-fixer
      - id: trailing-whitespace
""",
}


class Change(BaseModel):
    target: str
    status: str
    blueprint: str
    detail: str


class ApplyResult(BaseModel):
    changes: list[Change] = Field(default_factory=list)

    @property
    def changed(self) -> list[Change]:
        return [change for change in self.changes if change.status == "changed"]


def _readme(facts: ProjectFacts) -> str:
    return f"""# {facts.name}

{facts.name} project.

## Install

Use the package manager declared by the project.

## Usage

See the source entry points for current usage.
"""


def _test_scaffold(root: Path, facts: ProjectFacts, target: str) -> str:
    if target.endswith(".py"):
        packages = []
        source = root / "src"
        if source.is_dir():
            packages = [
                path.name
                for path in source.iterdir()
                if path.is_dir() and (path / "__init__.py").is_file()
            ]
        if packages:
            return f"""from pathlib import Path


def test_package_layout() -> None:
    package = Path(__file__).parents[1] / "src" / {packages[0]!r}
    assert (package / "__init__.py").is_file()
"""
        return """from pathlib import Path


def test_project_manifest_exists() -> None:
    assert (Path(__file__).parents[1] / "pyproject.toml").is_file()
"""
    module = False
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            module = json.loads(package_json.read_text(encoding="utf-8")).get("type") == "module"
        except ValueError:
            pass
    if module:
        return """import assert from "node:assert/strict";
import test from "node:test";

test("project smoke", () => assert.ok(true));
"""
    return """const assert = require("node:assert/strict");
const test = require("node:test");

test("project smoke", () => assert.ok(true));
"""


def apply_findings(root: Path, facts: ProjectFacts, findings: list[Finding]) -> ApplyResult:
    result = ApplyResult()
    seen: set[str] = set()
    for finding in findings:
        remediation = finding.remediation
        if (
            not remediation
            or not remediation.safe
            or remediation.target in seen
            or finding.provenance != "deterministic"
            or finding.source != "blueprint-ai"
        ):
            continue
        seen.add(remediation.target)
        relative_target = Path(remediation.target)
        if relative_target.is_absolute() or ".." in relative_target.parts:
            result.changes.append(
                Change(
                    target=remediation.target,
                    status="conflicted",
                    blueprint=finding.blueprint,
                    detail="target escapes the project",
                )
            )
            continue
        target = root / relative_target
        if target.exists():
            result.changes.append(
                Change(
                    target=remediation.target,
                    status="skipped",
                    blueprint=finding.blueprint,
                    detail="existing file preserved",
                )
            )
            continue
        if remediation.kind == "template" and remediation.target in TEMPLATES:
            content = TEMPLATES[remediation.target]
        elif remediation.kind == "template" and remediation.target == "README.md":
            content = _readme(facts)
        elif remediation.kind == "test-scaffold":
            content = _test_scaffold(root, facts, remediation.target)
        else:
            result.changes.append(
                Change(
                    target=remediation.target,
                    status="conflicted",
                    blueprint=finding.blueprint,
                    detail="unsupported remediation",
                )
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result.changes.append(
            Change(
                target=remediation.target,
                status="changed",
                blueprint=finding.blueprint,
                detail=remediation.description,
            )
        )
    return result
