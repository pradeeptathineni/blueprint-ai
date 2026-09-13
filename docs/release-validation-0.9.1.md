# Blueprint AI 0.9.1 release validation

Prepared 2026-09-13 from released `0.9.0` commit
`bd5882c275db4f93e97d873cc3c64b57fb264b3c`. This release is limited to the publication,
compatibility, and documentation findings from the 1.0-readiness audit. It adds no migration lane,
production `AgentBackend`, or new architecture.

## Stable-contract boundary

The [compatibility contract](compatibility.md) defines the intended 1.x CLI, exit-code,
configuration, JSON schema, machine-report, Python API, deprecation, and security-support surface.
`tests/fixtures/compatibility/contract-v1.json` is generated mechanically from the public runtime and
all thirteen schemas. Its comparator permits documented additive evolution but rejects removals,
signature changes, narrowed schema enums, new schema requirements, default changes, and report-shape
breakage.

Review-policy failures now consistently exit 1. Invalid usage and configuration exit 2, while failed
or partial mutation exits 3. New remediation receipts and evolution manifests include the exact
Blueprint AI producer version; acceptance and rollback reject another producer version. Legacy
records without the field remain readable on a bounded best-effort basis.

The existing Next.js lane was also reconciled with its sealed authoritative-tool contract: codemod
stages can write only source, manifest, and ESLint configuration paths, while a lockfile remains a
manual-completion path. Final-state replay compares public inventories after excluding only each
stage's declared transaction-private paths; caches can be cleaned without hiding public changes.

## Distribution and publication boundary

The tag workflow first calls the complete repository CI workflow. Only after that gate passes does it
build the wheel and source archive once. Fresh wheel and source installs run the same release smoke
contract. Syft scans a separate clean environment containing the installed wheel and runtime
dependencies; a verifier requires exact equality with `uv pip list`, rejects development packages and
the checkout path, and requires the released package/version.

One checksum manifest binds the two distributions and runtime SPDX document. GitHub attestations bind
build provenance and that SBOM to each distribution, and separately bind provenance to the published
SBOM file. The release job publishes exactly those four files. A dependent job downloads the actual
immutable GitHub Release, checks its exact member set and every asset digest, validates the checksum
manifest, and verifies all three attestation classes before registry publication can begin.

PyPI uses only the protected `pypi` environment and job-scoped OIDC. The production-registry verifier
requires exact project/version/Python metadata, canonical tagged validation links, the one wheel and
one source archive, hashes equal to the GitHub release, and downloaded bytes equal to those hashes.
Fresh `pipx` and `uv` installs are then exercised against the official PyPI index.

## Candidate evidence

Ruff formatting/lint and mypy pass across 88 checked Python files. The complete warnings-as-errors
suite passes 493 tests with 7 environment-specific skips and 83% total coverage. All thirteen schemas
parse, the compatibility snapshot and generated support document match, and deterministic no-model
production review reports no P1/P2 findings; its 19 P3 results are advisory complexity/cache heuristics.
Pip-audit and OSV report no known vulnerable dependency, while the local package is explicitly absent
from PyPI before this first publication. Trivy reports no high/critical vulnerability,
misconfiguration, or secret, and Gitleaks reports no secret after its one public pinned corpus commit
is explicitly marked at source.

The Phase 7 corpus passes five executable migrations and ten read-only inspections. The Phase 8
real-tool corpus passes six executable/partial/fail-closed cases and two negative controls, including
exact diff reproduction and rollback with zero model or agent calls. The live Docker boundary passes
all 118 Python/Node policy cases. A Python 3.14.7 candidate wheel and source archive each pass 33 smoke
checks from separate fresh installs. The Syft 1.42.3 SPDX 2.3 document exactly matches all 33 packages
in the clean installed runtime and contains neither development packages nor the checkout path. These
locally built hashes are deliberately not represented as the hosted release hashes.

No `OPENAI_API_KEY` was present during the release audit. Controlled protocol tests cover the bounded,
non-stored Responses contract, but no live request, latency, token, cost, or semantic-quality result is
claimed. The provider is preview/live-unverified for 0.9.1. Declared network destinations are recorded
provenance; local Docker/Podman adapters enforce only `none` versus authorized unrestricted egress, so
operators that need destination filtering must provide it outside Blueprint AI.

The exact released commit, hosted CI and release runs, GitHub Release bytes and attestations, PyPI
metadata and downloaded hashes, and fresh registry-install results are authoritative post-tag evidence.
