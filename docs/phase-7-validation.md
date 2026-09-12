# Phase 7 evolution validation

Phase 7 started from released 0.6.2 on 2026-09-12. The baseline was clean and synchronized with
`origin/main`; Ruff, formatting, mypy, and 384 tests passed with seven environment skips and 82%
coverage. The implementation establishes a strict desired-state plan, canonical transformation
catalog, bounded transaction, migration-specific verification, deterministic review comparison, and
exact rollback without adding a runtime dependency.

Native tools execute against a staged copy of the tracked/unignored Git inventory. The engine detects
even normally ignored files created there and publishes only verified planned text files with their
original modes. This removed the risk that a failing trusted host formatter could corrupt ignored
workspace data. A pre-publication fingerprint check and published-path-only failure restoration avoid
overwriting unrelated concurrent edits.

## Research and scope decisions

Fresh primary-source research favored authoritative tools: Ruff `UP`, uv migration guidance, Go 1.26
`go fix`, Cargo edition fixes, OpenRewrite recipe runners, React/Next codemods, Terraform/OpenTofu
native guidance, Kubernetes conversion guidance, Docker's OCI-label replacement, GitHub Actions full
SHA guidance, and Codex App Server/exec. The executable set was limited to five paths whose target,
scope, dry run, idempotency, postcondition, and rollback could be established locally.

OpenRewrite remains planning-only because recipe artifacts have individual licensing and target-JDK
requirements; Spring recipe availability cannot be treated as uniformly Apache licensed. Create React
App destination choice is semantic. Rust's native edition workflow also needs manifest and inactive
configuration handling. The official .NET Upgrade Assistant is deprecated in favor of an authorized
agent workflow. Kubernetes conversion can choose unsuitable defaults. Terraform-to-OpenTofu is state,
backend, provider, and dependency-order sensitive. None is disguised as a textual migration.

Codex research confirmed App Server as the full JSONL-over-stdio, approval-capable integration and
`codex exec` as the one-shot/CI option. No executable Phase 7 step has residual semantic edits, so an
agent backend and MCP server would add unused authority and protocol surface. The strict JSON CLI and
three evolution schemas are the machine interface for this release.

## Corpus and dogfood

`benchmarks/phase7_migrations.py` creates fresh Git repositories without network or installation. It
exercises actual Ruff 0.16.7, Go 1.27.0, and Terraform 1.16.0 commands plus both built-in transforms.
Each supported case produced a real diff, passed native/structural postconditions, produced no second
change, restored the full source fingerprint, then repeated with the same output hashes. Six additional
fixtures prove read-only candidate detection for legacy Python packaging, Java/Spring, CRA/React/JS,
Rust, .NET, and Kubernetes without overstating execution support. Compact results are recorded in
[phase-7-evidence.json](phase-7-evidence.json).

The public dogfood used a disposable clone of `facebook/create-react-app` v5.0.1 at exact commit
`19fa58d527ae74f2b6baa0867463eea1d290f9a5`. The selected transform replaced six checkout/setup-node
v2 tags in three workflows with reviewed full SHAs while retaining `# v2`. Strict YAML and immutable
reference checks passed. The deterministic Blueprint review moved from 53 to 47 findings, introduced
none, and resolved the six mutable-reference findings. Its status remained honestly partial because
nine optional review routes lacked complete tool/model evidence in the disposable clone. Exact
rollback restored the clean detached checkout. The archived Node 14 dependency graph would have
required network access, so application build/tests were not claimed for this workflow-only change;
no application source changed. No upstream remote, model, or agent was invoked.

## Adversarial coverage

The permanent Phase 7 suite covers catalog truthfulness, read-only inspection, plan checksums and
canonical re-resolution, stale/dirty/main guards, dry-run immutability, positive and near-match
transforms, strict duplicate-key YAML failure, ambiguous Dockerfile conflicts, scope exclusions,
partial multi-step restoration, native-tool failure after mutation, host-path redaction, unsupported
manual execution, operation locking, rollback conflict atomicity, full-SHA registry shape,
branch/HEAD/index rollback identity, idempotency, reproducibility, and exact restoration. Command
paths are reported canonically rather than leaking operator-specific executable locations.

## Release gate

The final source and distribution gate is recorded in
[release-validation-0.7.0.md](release-validation-0.7.0.md). Immutable hosted CI, release, artifact,
and attestation identities are produced only after tagging and belong to the hosted release record
and final release report. Independent commands are maintained in [testing.md](testing.md) and
[releasing.md](releasing.md).
