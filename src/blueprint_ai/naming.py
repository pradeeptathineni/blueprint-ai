"""Namespace-specific identity validation shared by review and genesis."""

from __future__ import annotations

import keyword
import re
import sys
import unicodedata
from typing import Literal

from packaging.utils import InvalidName, canonicalize_name
from pydantic import BaseModel, Field


class NameCheck(BaseModel):
    namespace: str
    original: str
    normalized: str
    syntax_valid: bool
    ecosystem_valid: bool
    problems: list[str] = Field(default_factory=list)
    availability: Literal["not_checked"] = "not_checked"
    semantic_quality: Literal["not_assessed"] = "not_assessed"


class IdentityMap(BaseModel):
    display: str
    repository: str
    package: str
    module: str
    checks: list[NameCheck]


def validate_name(name: str, namespace: str) -> NameCheck:
    problems: list[str] = []
    normalized = name
    valid = False
    if namespace == "python":
        try:
            normalized = canonicalize_name(name, validate=True)
            valid = True
        except InvalidName:
            pass
        module = normalized.replace("-", "_")
        if keyword.iskeyword(module) or module in sys.stdlib_module_names:
            problems.append("name shadows a Python keyword or standard-library module")
        if not module.isidentifier():
            problems.append("distribution cannot directly map to a Python import identifier")
    elif namespace == "npm":
        normalized = name.lower()
        valid = len(name) <= 214 and bool(
            re.fullmatch(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", name)
        )
        if name in {"node_modules", "favicon.ico", "node", "npm"}:
            problems.append("reserved or problematic npm package name")
    elif namespace == "repository":
        valid = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name))
        if name.lower() in {"con", "prn", "aux", "nul", ".", ".."} or re.fullmatch(
            r"(?i)(?:com|lpt)[1-9]", name
        ):
            problems.append("reserved filesystem name")
        if name.endswith((".git", ".")):
            problems.append("problematic repository suffix")
    else:
        raise ValueError(f"unsupported naming namespace: {namespace}")
    if not valid:
        problems.insert(0, f"invalid {namespace} syntax")
    return NameCheck(
        namespace=namespace,
        original=name,
        normalized=normalized,
        syntax_valid=valid,
        ecosystem_valid=valid and not problems,
        problems=problems,
    )


def resolve_identity(display: str, ecosystem: str = "repository") -> IdentityMap:
    if not display.strip() or len(display) > 200 or any(ord(c) < 32 for c in display):
        raise ValueError("name must be 1–200 printable characters")
    ascii_name = unicodedata.normalize("NFKD", display).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    checks = [validate_name(slug, "repository")]
    if ecosystem != "repository":
        checks.append(validate_name(slug, ecosystem))
    if any(not check.ecosystem_valid for check in checks):
        raise ValueError("; ".join(problem for check in checks for problem in check.problems))
    return IdentityMap(
        display=display, repository=slug, package=slug, module=slug.replace("-", "_"), checks=checks
    )
