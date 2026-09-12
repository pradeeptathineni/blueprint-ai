import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from blueprint_ai.cli import app
from blueprint_ai.core.project import Component
from blueprint_ai.genesis import IntentSpec, plan_project
from blueprint_ai.genesis.executor import create_project
from blueprint_ai.genesis.models import ArtifactClaim, Capability, OperationResult
from blueprint_ai.genesis.plan import resolve_capabilities, validate_claims
from blueprint_ai.naming import resolve_identity, validate_name


@pytest.mark.parametrize(
    "display,ecosystem,expected",
    [
        ("My Project", "python", "my-project"),
        ("Café Studio", "npm", "cafe-studio"),
        ("tiny_repo", "repository", "tiny-repo"),
    ],
)
def test_identity_namespaces(display: str, ecosystem: str, expected: str) -> None:
    identity = resolve_identity(display, ecosystem)
    assert identity.repository == expected
    assert all(c.ecosystem_valid and c.availability == "not_checked" for c in identity.checks)
    assert validate_name("Flask", "python").syntax_valid
    assert not validate_name("Flask", "npm").syntax_valid


@pytest.mark.parametrize("name", ["", "../", "json", "class", "123-client", "name\nother"])
def test_unsafe_or_problematic_python_names(name: str) -> None:
    with pytest.raises(ValueError):
        resolve_identity(name, "python")


def test_intent_rejects_unknown_fields_and_incompatible_capabilities() -> None:
    with pytest.raises(ValueError):
        IntentSpec.model_validate({"name": "sample", "template": "https://evil.test/template"})
    with pytest.raises(ValueError):
        IntentSpec(name="sample", kind="react", container=True)
    with pytest.raises(ValueError):
        IntentSpec(name="sample", kind="python-cli", api_client=True)


def test_capability_cycles_conflicts_and_ownership() -> None:
    catalog = {
        "a": Capability(id="a", provider="builtin", description="a", requires=["b"]),
        "b": Capability(id="b", provider="builtin", description="b", requires=["a"]),
    }
    with pytest.raises(ValueError, match="cycle"):
        resolve_capabilities(["a"], catalog)
    catalog["b"].requires = []
    catalog["b"].conflicts = ["a"]
    with pytest.raises(ValueError, match="conflicts"):
        resolve_capabilities(["a"], catalog)
    with pytest.raises(ValueError, match="conflict"):
        validate_claims(
            [
                ArtifactClaim(path="package.json", owner="one"),
                ArtifactClaim(path="package.json", locator="scripts", owner="two"),
            ]
        )


def test_dry_run_is_pure_and_no_model_is_supported(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    result = CliRunner().invoke(
        app,
        [
            "init",
            str(destination),
            "--kind",
            "full-stack",
            "--api-client",
            "--dry-run",
            "--no-model",
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    assert {"python", "react", "api", "api-client"} <= set(plan["capabilities"])
    assert not list(tmp_path.iterdir())
    assert plan["plan_sha256"]
    schema_result = CliRunner().invoke(app, ["schema", "genesis-plan"])
    assert schema_result.exit_code == 0, schema_result.output
    schema = json.loads(schema_result.output)["schema"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(plan)
    assert not Draft202012Validator(schema).is_valid({**plan, "plan_sha256": "invalid"})


def test_plan_tampering_cannot_supply_commands(tmp_path: Path) -> None:
    plan = plan_project(IntentSpec(name="sample", kind="python-library"))
    plan.operations[0].command = ["sh", "-c", "touch PWNED"]
    with pytest.raises(ValueError, match="trusted resolver"):
        create_project(tmp_path / "sample", plan, trust_providers=True)
    assert not list(tmp_path.iterdir())


def test_real_generic_and_openapi_generation_and_provenance(tmp_path: Path) -> None:
    for kind in ("repository", "openapi"):
        destination = tmp_path / kind
        result = create_project(destination, plan_project(IntentSpec(name=kind, kind=kind)))
        assert result.status == "verified", result.detail
        receipt = json.loads((destination / ".blueprint-ai/genesis.json").read_text())
        assert receipt["result"]["model_calls"] == 0
        assert receipt["result"]["files"]["README.md"]
        assert (destination / ".blueprint-ai/review.json").is_file()
    assert json.loads((tmp_path / "openapi/openapi.json").read_text())["openapi"] == "3.1.0"


def test_nonempty_and_symlink_destinations_are_preserved(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "user.txt").write_text("keep")
    plan = plan_project(IntentSpec(name="sample"))
    with pytest.raises(ValueError, match="must not exist"):
        create_project(destination, plan)
    link = tmp_path / "link"
    link.symlink_to(destination)
    with pytest.raises(ValueError, match="symbolic"):
        create_project(link / "child", plan)
    assert (destination / "user.txt").read_text() == "keep"


def test_mit_generation_does_not_assign_the_tool_authors_copyright(tmp_path: Path) -> None:
    destination = tmp_path / "independent-project"
    result = create_project(
        destination, plan_project(IntentSpec(name="Independent Project", license="MIT"))
    )
    assert result.status == "verified", result.detail
    license_text = (destination / "LICENSE").read_text()
    assert "Copyright (c) [year] [copyright holder]" in license_text
    assert "Pradeep Tathineni" not in license_text


def test_provider_trust_preflight_has_no_side_effects(tmp_path: Path) -> None:
    result = create_project(
        tmp_path / "sample", plan_project(IntentSpec(name="sample", kind="python-library"))
    )
    assert result.status == "failed"
    assert result.operations[0].status == "unauthorized"
    assert not list(tmp_path.iterdir())


def test_missing_provider_and_transactional_failure(tmp_path: Path, monkeypatch) -> None:
    from blueprint_ai.genesis import executor

    plan = plan_project(IntentSpec(name="sample", kind="python-library"))
    monkeypatch.setattr(executor, "_probe", lambda *args: (None, None, "missing"))
    result = create_project(tmp_path / "sample", plan, trust_providers=True)
    assert result.operations[0].status == "unavailable"
    monkeypatch.setattr(executor, "preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        executor,
        "_run",
        lambda op, *args: OperationResult(id=op.id, status="failed", detail="injected failure"),
    )
    result = create_project(tmp_path / "sample", plan, trust_providers=True)
    assert result.status == "failed"
    assert not list(tmp_path.iterdir())


def test_every_shipped_kind_has_shared_components_and_verifiers() -> None:
    kinds = IntentSpec.model_json_schema()["properties"]["kind"]["enum"]
    for kind in kinds:
        plan = plan_project(IntentSpec(name="sample", kind=kind))
        assert all(isinstance(c, Component) for c in plan.components)
        assert plan_project(plan.intent).digest() == plan.digest()
        assert kind == "repository" or any(op.action == "verify" for op in plan.operations)


def test_atomic_publication_preserves_a_concurrent_empty_destination(tmp_path: Path) -> None:
    from blueprint_ai.genesis.executor import _publish_fresh

    stage, destination = tmp_path / "stage", tmp_path / "destination"
    stage.mkdir()
    (stage / "source.txt").write_text("generated")
    destination.mkdir()
    with pytest.raises(FileExistsError):
        _publish_fresh(stage, destination)
    assert not list(destination.iterdir())
    assert (stage / "source.txt").read_text() == "generated"
    destination.rmdir()
    _publish_fresh(stage, destination)
    assert (destination / "source.txt").read_text() == "generated"


def test_devcontainer_requires_execution_trust_and_a_verifier(tmp_path: Path) -> None:
    plan = plan_project(IntentSpec(name="sample", devcontainer=True))
    assert any(op.id == "verify:devcontainer" for op in plan.operations)
    result = create_project(tmp_path / "sample", plan)
    assert result.operations[0].status == "unauthorized"
    assert not list(tmp_path.iterdir())
