import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from blueprint_ai.core import Finding
from blueprint_ai.discovery import discover_project
from blueprint_ai.model.context import ContextBuilder, redact_secrets
from blueprint_ai.model.provider import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OpenAIProvider,
    _strict_schema,
)
from blueprint_ai.model.reviewer import CachedModelReviewer


class FakeProvider(ModelProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.calls = 0

    def available(self) -> bool:
        return True

    def review(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            model=self.model,
            findings=[
                Finding(
                    blueprint=request.blueprint,
                    category="judgment",
                    source="fake",
                    provenance="model",
                    severity="low",
                    message="Consider an explicit boundary.",
                    recommendation="Document it.",
                )
            ],
        )


def test_context_budget_excludes_vendor_and_redacts(python_project: Path) -> None:
    (python_project / "secret.py").write_text('api_key = "super-secret"\n')
    vendor = python_project / "node_modules"
    vendor.mkdir()
    (vendor / "huge.js").write_text("x" * 100_000)
    facts = discover_project(python_project)
    context = ContextBuilder(python_project, token_budget=500).build("architecture", facts, [])
    assert len(context) <= 2_000
    assert "super-secret" not in context
    assert "huge.js" not in context


def test_model_review_is_cached(python_project: Path, tmp_path: Path) -> None:
    provider = FakeProvider()
    reviewer = CachedModelReviewer(provider, tmp_path / "cache", token_budget=500)
    facts = discover_project(python_project)
    first = reviewer.review(python_project, "architecture", facts, [])
    second = reviewer.review(python_project, "architecture", facts, [])
    assert first[0].provenance == "model"
    assert second[0].message == first[0].message
    assert provider.calls == 1


def test_private_keys_are_fully_redacted() -> None:
    text = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    assert redact_secrets(text) == "[REDACTED]"


def test_strict_schema_requires_every_object_property() -> None:
    schema = {
        "type": "object",
        "properties": {"optional": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    }
    strict = _strict_schema(schema)
    assert strict["required"] == ["optional"]
    assert strict["additionalProperties"] is False


def test_openai_provider_uses_bounded_nonstored_structured_response() -> None:
    class Response:
        output_text = '{"findings": []}'
        usage = None

    class Responses:
        def __init__(self) -> None:
            self.arguments: dict[str, Any] = {}

        def create(self, **kwargs):
            self.arguments = kwargs
            return Response()

    class Client:
        def __init__(self) -> None:
            self.responses = Responses()

    client = Client()
    provider = OpenAIProvider(client=client)
    response = provider.review(
        ModelRequest(
            blueprint="architecture", system="system", context="context", max_output_tokens=300
        )
    )
    assert response.findings == []
    assert client.responses.arguments["store"] is False
    assert client.responses.arguments["max_output_tokens"] == 300
    assert client.responses.arguments["text"]["format"]["strict"] is True


def test_openai_provider_constructs_client_with_timeout_and_retry_bound(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Response:
        output_text = '{"findings": []}'
        usage = None

    class Responses:
        def create(self, **_kwargs):
            return Response()

    class Client:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.responses = Responses()

    module = ModuleType("openai")
    module.OpenAI = Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)

    provider = OpenAIProvider(timeout=12.0, max_retries=0)
    provider.review(
        ModelRequest(
            blueprint="architecture", system="system", context="context", max_output_tokens=100
        )
    )
    assert captured == {"timeout": 12.0, "max_retries": 0}
