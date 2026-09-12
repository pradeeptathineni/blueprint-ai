# Verification

Use deterministic unit and property checks, native scanner mutation fixtures, fresh generated
projects, compositions, public repositories, hostile inputs, and isolated package installs together.
The [independent Phase 6 audit](phase-6-redteam.md) records corrected results and environment limits.

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
PYTHONPATH=src uv run python benchmarks/phase7_migrations.py \
  --output /tmp/blueprint-phase7-migrations.json \
  --require python/ruff-pyupgrade --require go/native-fix \
  --require terraform/native-format
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

The Phase 7 migration corpus makes no network calls or installation attempts. It executes already
available compatible native tools only with an explicit trusted writable host policy in disposable
repositories. Each supported case must create a real diff, pass migration-specific verification,
become idempotent, restore its complete fingerprint, and reproduce the same result. `--only` selects
a portable CI subset; `--require` turns a missing selected tool into a failure. Planning-only fixtures
exercise researched legacy states without claiming that partial/deferred recipes are executable.
The inspection set includes Next/React separation plus Rust 2024 and net10.0 negative controls; the
Ruff case mirrors the audited package-initializer fixture and verifies the complete composed diff.

## Independent release regression gate

The [red-team report](phase-6-redteam.md) records the corrected source gate. Enable both
`BLUEPRINT_SANDBOX_TEST_IMAGE=python:3.12-slim-bookworm` and
`BLUEPRINT_SANDBOX_NODE_IMAGE=node:24-bookworm-slim` to execute all optional live boundary tests.
`benchmarks/redteam_kits.py` checks all sixteen live kit transactions;
`benchmarks/redteam_tools.py` adds the native formatter/parser mutation fixtures.
`benchmarks/redteam_browser.cjs` drives installed Playwright/Chrome against explicitly supplied
loopback applications. See the linked audit documents for prerequisites, exact commands and evidence.
`benchmarks/redteam_platforms.py --linux-tests --output PATH` creates temporary Git-enabled Linux
images and runs the full suite on Python 3.12–3.14. After building both distributions, its `--installs`
mode checks each artifact in separate fresh Linux and native environments. Output paths must be fresh
and outside the checkout; images and native Python interpreters must already be available.

## Hosted CI contract

CI runs on pull requests, pushes to `main`, semantic release tags without a `v` prefix, and manual
dispatch. It has read-only repository permissions and ref-scoped concurrency; release-tag runs are
never canceled by a later run. Quality/build/audit/SBOM work runs once on Linux, while the full test
suite covers Python 3.12–3.14. A dedicated Docker job exercises live sandbox policy. A native
`windows-latest` job runs representative CLI/config/provider tests plus the installed-style smoke
workflow covering version, doctor, discovery, naming, deterministic genesis, no-model review, and
transaction apply/rollback. Trigger structure is itself regression-tested in `tests/test_ci.py`.
