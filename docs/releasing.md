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
no-model review. Exercise the hostile repository cases in `tests/test_phase3.py` and real-tool
contract regressions in `tests/test_phase4.py`. Recreate scanner fixtures with synthetic credentials
only; never commit their contents. For external-project strengthening, use a disposable copy or a
dedicated `codex/phase-5-*` branch and verify the source repository before and after.

Release notes must record missing optional OSS tools and absent model credentials as limitations,
not successful tool/model validation. Review the JSON metadata for tool/provider/model/prompt
versions, config hash, duration, token/call/cache metrics, and the distinction between missing,
unsupported, failed, and finding outcomes.

The Phase 5 evidence and current release classification live in
[phase-5-validation.md](phase-5-validation.md). Existing release tags are immutable; preparing a
version does not authorize tagging or publishing it.

Genesis and corpus gates (only generated code executes; external target code stays untrusted):

```bash
PYTHONPATH=src uv run python benchmarks/genesis_matrix.py --output /tmp/blueprint-genesis-verify --network --add-ons
PYTHONPATH=src uv run python benchmarks/corpus.py --output /tmp/blueprint-corpus-verify --review
```

Use fresh output paths. Modern Node/npm and uv must be installed; image variants need local Docker.
Network tools may remain partial. Preserve exact external SHAs and compare source snapshots.
