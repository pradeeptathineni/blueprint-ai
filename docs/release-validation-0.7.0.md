# Blueprint AI 0.7.0 release validation

Prepared 2026-09-12 from released 0.6.2 source. Phase-specific architecture, corpus, dogfood, and
adversarial evidence are in [phase-7-validation.md](phase-7-validation.md) and
[phase-7-evidence.json](phase-7-evidence.json).

## Source gate

The release candidate passed Ruff lint and format, mypy, and 407 tests with seven environment skips
and 82% coverage on Python 3.12 under deprecation-warning escalation. Pip-audit found no known
dependency vulnerabilities; the local editable `blueprint-ai-cli` package is intentionally not a
published audit target. Actionlint and offline pedantic Zizmor reported no workflow findings. All
public JSON schemas validate, and generated support is byte-identical to [support.md](support.md).

The network-disabled corpus passed five real supported migrations with Ruff 0.16.7, Go 1.27.0, and
Terraform 1.16.0 plus both built-in transformations; all were idempotent, repeatable, and exactly
reversible. Six additional ecosystem fixtures remained read-only. Live OCI sandbox coverage passed
117 tests with the pinned local Python and Node test images. Deterministic self-review found no P0-P2
issue; its P3 results were complexity/cache advice, pre-publication links to this release's new files,
and an inconclusive HTTP 403 from the official OpenAI page. No model or agent call was made.

## Distribution gate

Fresh wheel and source-archive builds passed the distribution archive verifier, machine-path and
secret scans, and separate Python 3.14 installations. Each installed distribution passed all 30
release-smoke checks, including evolution catalog/schema availability and a complete
plan/dry-run/apply/exact-rollback transaction.

## Publication boundary

The established tag workflow builds once, verifies both distributions, generates checksums and an
SPDX SBOM, produces GitHub build/SBOM attestations, and creates the GitHub Release from the existing
remote tag. PyPI remains gated by `BLUEPRINT_PUBLISH_PYPI=true` and the protected `pypi` Trusted
Publishing environment. No API token or alternative publication route is authorized.

This source-side document cannot truthfully embed outcomes produced only after its immutable tag.
The exact commit, hosted run URLs, hosted SHA-256 values, attestation checks, release assets, and PyPI
outcome are therefore retained in the GitHub Actions and Release records and reported with the final
release verification, rather than backfilled by moving the tag or rebuilding the artifacts.
