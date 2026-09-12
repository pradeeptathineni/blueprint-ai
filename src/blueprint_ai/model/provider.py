from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from blueprint_ai.core import Finding
from blueprint_ai.core.models import FileRange, Severity


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic JSON Schema into the strict Structured Outputs subset."""
    if "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"])
        for child in schema["properties"].values():
            _strict_schema(child)
    if isinstance(schema.get("items"), dict):
        _strict_schema(schema["items"])
    for keyword in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(keyword, []):
            _strict_schema(child)
    for child in schema.get("$defs", {}).values():
        _strict_schema(child)
    return schema


class ModelRequest(BaseModel):
    blueprint: str
    system: str
    context: str
    max_output_tokens: int = 2_000
    prompt_version: str = "phase3-review-v1"


class ModelResponse(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None


class ModelConfiguration(BaseModel):
    """Operator-owned model runtime configuration; repository content never populates this."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["openai"] = "openai"
    model: str = Field(default="gpt-5-mini", min_length=1, max_length=200)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] | None = None
    timeout: float = Field(default=60.0, gt=0, le=600)


class _SemanticFinding(BaseModel):
    """Only review evidence crosses the model boundary; authority is assigned locally."""

    model_config = ConfigDict(extra="forbid")
    category: str
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    file: str | None
    line: int | None
    message: str
    recommendation: str
    evidence: list[str]


class _SemanticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[_SemanticFinding]


class ModelProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def review(self, request: ModelRequest) -> ModelResponse: ...


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-5-mini",
        client: Any = None,
        *,
        timeout: float = 60.0,
        max_retries: int = 0,
        reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] | None = None,
    ):
        self.model = model
        self._client = client
        self.timeout = timeout
        self.max_retries = max_retries
        self.reasoning_effort = reasoning_effort

    def available(self) -> bool:
        if self._client is not None:
            return True
        if not os.environ.get("OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def review(self, request: ModelRequest) -> ModelResponse:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(timeout=self.timeout, max_retries=self.max_retries)
        schema = _strict_schema(_SemanticResponse.model_json_schema())
        parameters: dict[str, Any] = {
            "model": self.model,
            "instructions": request.system,
            "input": request.context,
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "metadata": {"prompt_version": request.prompt_version},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "blueprint_findings",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if self.reasoning_effort is not None:
            parameters["reasoning"] = {"effort": self.reasoning_effort}
        response = self._client.responses.create(
            **parameters,
        )
        if getattr(response, "status", "completed") != "completed":
            raise ValueError("model response did not complete")
        payload = _SemanticResponse.model_validate(json.loads(response.output_text))
        findings = [
            Finding(
                **item.model_dump(exclude={"line"}),
                blueprint=request.blueprint,
                source=f"{self.name}:{self.model}",
                provenance="model",
                range=FileRange(start_line=item.line) if item.line is not None else None,
            )
            for item in payload.findings
        ]
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return ModelResponse(
            findings=findings,
            model=self.model,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cached_input_tokens=getattr(input_details, "cached_tokens", None),
            reasoning_tokens=getattr(output_details, "reasoning_tokens", None),
        )


def configuration_from_environment() -> ModelConfiguration:
    """Resolve operator-owned settings without treating repository data as authority."""
    provider_name = os.environ.get("BLUEPRINT_AI_PROVIDER", "openai").strip().lower()
    if provider_name != "openai":
        raise ValueError(f"unsupported model provider: {provider_name or '<empty>'}")
    model = os.environ.get("BLUEPRINT_AI_MODEL", "gpt-5-mini").strip()
    reasoning = os.environ.get("BLUEPRINT_AI_REASONING_EFFORT", "").strip().lower() or None
    if reasoning not in {None, "none", "low", "medium", "high", "xhigh"}:
        raise ValueError("model reasoning effort must be none, low, medium, high, or xhigh")
    try:
        timeout = float(os.environ.get("BLUEPRINT_AI_MODEL_TIMEOUT", "60"))
    except ValueError as exc:
        raise ValueError("model timeout must be a number") from exc
    effort = cast(Literal["none", "low", "medium", "high", "xhigh"] | None, reasoning)
    return ModelConfiguration(
        provider="openai",
        model=model,
        reasoning_effort=effort,
        timeout=timeout,
    )


def provider_from_environment() -> ModelProvider | None:
    try:
        configuration = configuration_from_environment()
    except ValueError:
        return None
    provider = OpenAIProvider(
        model=configuration.model,
        timeout=configuration.timeout,
        reasoning_effort=configuration.reasoning_effort,
    )
    return provider if provider.available() else None
