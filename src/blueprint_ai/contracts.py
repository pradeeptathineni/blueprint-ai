from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BlueprintContract(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    applicability: Any
    check: Any
    model_review: bool


class AdapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    blueprint: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    executable: str = Field(min_length=1, max_length=1_024)
    args: list[str] = Field(max_length=10_000)
    parser: Any
    expected_codes: set[int] = Field(min_length=1, max_length=16)


def validate_builtin_contracts(
    blueprints: Mapping[str, Any], adapters: Sequence[Any]
) -> dict[str, int]:
    """Validate stable extension surfaces without executing repository content."""
    for key, blueprint in blueprints.items():
        BlueprintContract.model_validate(
            {
                "name": blueprint.name,
                "description": blueprint.description,
                "applicability": blueprint.applicability,
                "check": blueprint.check,
                "model_review": blueprint.model_review,
            }
        )
        if (
            key != blueprint.name
            or not callable(blueprint.applicability)
            or not callable(blueprint.check)
        ):
            raise ValueError(f"invalid blueprint registration: {key}")
    names = set()
    for adapter in adapters:
        AdapterContract.model_validate(
            {
                "name": adapter.name,
                "blueprint": adapter.blueprint,
                "executable": adapter.executable,
                "args": adapter.args,
                "parser": adapter.parser,
                "expected_codes": adapter.expected_codes,
            }
        )
        if adapter.name in names or not callable(adapter.parser):
            raise ValueError(f"invalid or duplicate adapter: {adapter.name}")
        command = adapter.command(Path("."))
        if not command or not all(isinstance(argument, str) for argument in command):
            raise ValueError(f"adapter command is not an argument array: {adapter.name}")
        names.add(adapter.name)
    return {"blueprints": len(blueprints), "adapters": len(adapters)}
