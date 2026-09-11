# Contributing

## Setup

Python 3.12 or newer and `uv` are required.

```bash
uv sync --extra dev --locked
```

## Checks

Keep changes deterministic-first and preserve existing project conventions. Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=blueprint_ai --cov-report=term-missing
uv run pip-audit
uv build
uv run blueprint-ai review . --profile production --model off --fail-on P1
```

Add a regression test for every fixed defect. External-tool parsers need representative structured
golden payloads. Applicability changes need both applicable and not-applicable coverage. Generated
files must remain atomic, create-only, idempotent, manifest-backed, and rollback-safe. Changes to file
walking or context selection should compare `python benchmarks/run.py` on the same host.

## Design boundaries

Prefer native/OSS authority, then small deterministic logic, then bounded model judgment. Never add
silent executable downloads or infer authorization for remote security tests. Keep canonical data in
the typed core contracts, normalize adapters at the edge, and avoid duplicate rules across profiles.

Pull requests should explain the evidence for new dependencies/tools, their overlap and
false-positive tradeoffs, and the exact commands used to verify the change.
