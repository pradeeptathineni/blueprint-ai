# Release verification

The release gate combines deterministic checks, integration behavior, adversarial evidence, package
installation, and independent engineering review. A zero-finding self-review is never sufficient by
itself.

Run from a clean checkout with Python 3.12+ and `uv`:

```bash
uv sync --extra dev --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=blueprint_ai --cov-report=term-missing
uv run pip-audit
uv build
uv run blueprint-ai doctor --json
uv run blueprint-ai schema report
uv run blueprint-ai review . --profile production --no-model --fail-on P1 --format sarif
uv run blueprint-ai review . --profile production --no-model --trust-project-executables
PYTHONPATH=src uv run python benchmarks/run.py
```

Install the wheel into a fresh environment and inspect `blueprint-ai --help`, `doctor`, and a
no-model review. Exercise the hostile repository cases in `tests/test_phase3.py`. For destructive
external-project work, use only a disposable `blueprint-ai/phase-3-*` branch/worktree and verify the
source repository's default branch/ref before and after.

Release notes must record missing optional OSS tools and absent model credentials as limitations,
not successful tool/model validation. Review the JSON metadata for tool/provider/model/prompt
versions, config hash, duration, token/call/cache metrics, and the distinction between missing,
unsupported, failed, and finding outcomes.
