from __future__ import annotations

from pathlib import Path

from blueprint_ai.engine import make_context, review


def test_static_review_marks_external_tools_not_run_and_skips_project_blueprints(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const value: number = 1;\n")
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}\n')
    custom = tmp_path / ".blueprint-ai" / "blueprints"
    custom.mkdir(parents=True)
    (custom / "local-example.yml").write_text(
        "schema_version: 1\n"
        "name: local-example\n"
        "description: Local declarative check\n"
        "rules:\n"
        "  - kind: required-files\n"
        "    id: requires-marker\n"
        "    paths_any: [marker.txt]\n"
        "    message: marker missing\n"
        "    recommendation: add marker\n"
    )

    context, _settings = make_context(
        tmp_path,
        blueprints=["code-quality"],
        model_mode="off",
        run_external_tools=False,
        include_custom_blueprints=False,
    )
    result = review(context)

    assert [row.blueprint for row in result.results] == ["code-quality"]
    assert result.results[0].status == "partial"
    assert result.results[0].tools
    assert all(tool.outcome == "not_run" for tool in result.results[0].tools)
    assert "External analyzers were not run" in " ".join(result.results[0].notes)
    assert "local-example" not in [row.blueprint for row in result.results]
