# Verification

Use deterministic unit and property checks, native scanner mutation fixtures, fresh generated
projects, compositions, public repositories, hostile inputs, and isolated package installs together.
The [Phase 6 report](phase-6-validation.md) records actual results and environment limits.

```bash
uv sync --extra dev --locked
uv run pytest -W error::DeprecationWarning --cov=blueprint_ai --cov-report=term-missing
BLUEPRINT_SANDBOX_TEST_IMAGE=python:3.12-slim-bookworm uv run pytest tests/test_phase6.py
PYTHONPATH=src uv run python benchmarks/tool_matrix.py --output /tmp/blueprint-tool-results.json
PYTHONPATH=src uv run python benchmarks/phase6_matrix.py \
  --output /tmp/blueprint-generated-check --network --compositions
PYTHONPATH=src uv run python benchmarks/corpus.py \
  --output /tmp/blueprint-corpus-check --review --offline
PYTHONPATH=src uv run python benchmarks/run.py
```

Use fresh output paths and explicitly acquire registered images first; these test commands never
install a container engine. Native generators may require network to fetch locked dependencies.
The ordinary suite skips the live boundary test unless its image environment variable is set.
The suite also covers exhaustive policy combinations, every supported intent, parser contracts,
all-kit round trips, archive bounds, symlinks, FIFOs, cache poisoning, and plan integrity.

The tool matrix records clean, known-bad, and repaired status, expected rules, bounded errors,
sandbox evidence, and source snapshots. SBOM output has a separate evidence contract. The genesis
matrix retains operation receipts and source hashes. Compositions cover representative language,
framework, client, CI, and cloud choices; they are curated coverage, not every permutation.
The earlier add-on harness covers trusted image builds. A successful local workflow command does
not prove a hosted GitHub Actions run or browser journey occurred.

The public corpus uses exact SHAs and disposable shallow clones; repositories are never vendored.
Target-controlled code may run only in an available OCI image, never implicitly on the host.
The harness checks source snapshots before and after review. Runtime, database availability, and
scanner configuration can change findings, so compare deterministic discovery/context separately
from total scanner findings. See [releasing](releasing.md) for the complete package gate.
