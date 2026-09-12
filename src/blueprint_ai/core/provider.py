from __future__ import annotations

from pydantic import BaseModel


class Provider(BaseModel):
    id: str
    executable: str | None = None
    supported_versions: str | None = None
    package: str | None = None
    version: str | None = None
    registry_integrity: str | None = None
    image: str | None = None
    source: str
    license: str
    network: bool = False
    executes_code: bool = False
    reviewed: str = "2026-09-12"
