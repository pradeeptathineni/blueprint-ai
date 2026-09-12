# Blueprint AI 0.7.1 release validation

Prepared 2026-09-12 from released 0.7.0 source at
`302ac22a635cf307a1ccbc1f6cb67e68638ff16c`.

## Correctness evidence

The migration-audit defects were reproduced before implementation on disposable copies. Managed
Ruff attempted the host-style `ruff` executable and failed safely; the normal review gate rejected
the pyupgrade result's new F401 and restored source; Next 14 omitted its catalog candidate; and Rust
2024/net10.0 controls received misleading old-version candidates. The authoritative audit directory
remained read-only and unchanged.

The corrected managed Ruff transaction uses `/ruff` for version, preflight, transformation, cleanup,
and idempotency commands. On the audit's exact package-initializer fixture, its complete dry-run diff
is non-mutating; normal review changes 14 findings to 12, resolves the two target annotation findings,
and introduces none; a second apply is a no-op; and rollback restores the exact project fingerprint
and Git inventory. Host execution exercises the same recipe with the explicitly resolved host Ruff
0.16.7 binary. The safe composition retains initializer typing names as explicit exports rather than
requesting Ruff's unsafe standard-library-import removal.

Next 14 now receives `next/official-upgrade-codemod`, while React-only, incidental-text, and fixture
controls do not. Rust 2018 and net6.0 expose exact version evidence and their existing manual
candidates; Rust 2024 and net10.0 do not. Missing, mixed, inherited-unresolved, and non-runtime TFM
evidence remains non-applicable rather than fabricating a current-state label.

A mixed Docker/OpenTofu plan exposes its independent Docker step as ready, applies it, reports the
remaining manual work as partial, and rolls back exactly. Manual-only plans remain blocked, and the
ordered dependency boundary still prevents a later step from bypassing an unresolved predecessor.

## Source and corpus gate

The release candidate passed Ruff lint and format, mypy across 81 source files, and 419 tests with
seven environment-gated skips and 82% coverage on Python 3.12 under deprecation-warning escalation.
Pip-audit found no known dependency vulnerabilities and skipped only the unpublished local
`blueprint-ai-cli` package. Actionlint 1.7.12 and offline Zizmor 1.30.1 reported no workflow findings.
The doctor contract is healthy, all release schemas render, and generated support is byte-identical
to [support.md](support.md).

The network-disabled corpus passed all five supported migrations using Ruff 0.16.7, Go 1.27.0,
Terraform 1.16.0, and both built-in transformations. Every case produced the inspected expected diff,
was idempotent and repeatable, made zero model/agent calls, and rolled back exactly. Ten read-only
inspection controls include Next/React separation, Rust 2018/2024, and net6.0/net10.0. Live Docker
policy coverage passed 117 tests against `python:3.12-slim-bookworm` and
`node:24-bookworm-slim`; the managed Ruff lifecycle passed separately against
`ghcr.io/astral-sh/ruff:0.16.7`.

Deterministic self-review found no P0-P2 issue. Its 15 P3 observations are existing large-function
heuristics plus the existing generic build-cache suggestion; no model or agent was invoked.

## Distribution gate

Fresh wheel and source-archive builds passed the archive verifier, unsafe-member checks, and scans
for credentials and machine-specific paths. Both archives contain 52 intentional members. Separate
fresh Python 3.14 installations of the wheel and source archive each passed all 30 release-smoke
checks, including version, doctor, schemas, tool planning, deterministic review, plan/dry-run/apply,
and exact rollback. The disposable local-build SHA-256 values were:

- wheel: `f969450b9d1a85854e88530e1752635fb4dd2bbc163d6bfc49921cd918ca6998`;
- source archive: `1d9366d75a6de0152814bc846a81bdb0fe37933430765e4a367d5e1bb00037f3`.

Hosted hashes are recorded from the release workflow's artifacts rather than inferred from this
independent local build.

## Publication boundary

The established tag workflow builds once, repeats distribution and fresh-install checks, emits
checksums and an SPDX SBOM, records GitHub build/SBOM attestations, and creates the GitHub Release
from the pre-existing remote tag. PyPI remains gated by `BLUEPRINT_PUBLISH_PYPI=true` and the
protected `pypi` Trusted Publishing environment; no token or alternate publication path is used.

This source-side document cannot truthfully embed outcomes produced only after its immutable tag.
The exact final commit, tag object/target, hosted run URLs, hosted hashes, attestation results, release
assets, and PyPI outcome remain in the GitHub Actions/Release records and final release report.
