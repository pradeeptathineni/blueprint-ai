"""Java package names must not override native source-root ownership."""

from pathlib import Path

import pytest

from blueprint_ai.adapters.registry import applicable_adapters
from blueprint_ai.core.project import path_scope
from blueprint_ai.discovery import discover_project


@pytest.mark.parametrize("source,expected", [("main", "runtime"), ("test", "test")])
def test_java_namespace_segments_preserve_source_role(source: str, expected: str) -> None:
    for namespace in (
        "com/example",
        "com/fixtures",
        "com/vendor",
        "com/generated",
        "com/tests",
        "com/migrations",
        "com/benchmarks",
        "com/docs",
    ):
        assert path_scope(f"module/src/{source}/java/{namespace}/Example.java") == expected


@pytest.mark.parametrize(
    "prefix,expected",
    [("examples", "example"), ("vendor", "vendor"), ("fixtures", "fixture")],
)
def test_java_source_roots_preserve_enclosing_exclusions(prefix: str, expected: str) -> None:
    for source in ("main", "test"):
        assert path_scope(f"{prefix}/module/src/{source}/java/com/example/Main.java") == expected


def test_java_source_role_reaches_native_testing_route(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><artifactId>example</artifactId></project>\n")
    runtime = tmp_path / "src/main/java/com/example/Example.java"
    test = tmp_path / "src/test/java/com/example/ExampleTest.java"
    runtime.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    runtime.write_text("package com.example; public class Example {}\n")
    test.write_text("package com.example; public class ExampleTest {}\n")
    facts = discover_project(tmp_path)
    assert facts.graph.scope(runtime.relative_to(tmp_path).as_posix()) == "runtime"
    assert facts.tests == [test.relative_to(tmp_path).as_posix()]
    assert [adapter.name for adapter in applicable_adapters(facts, "testing", sandboxed=True)] == [
        "maven-test"
    ]
