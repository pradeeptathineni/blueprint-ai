# Changelog

## 0.4.1 — Unreleased

- Preserve a bounded diagnostic excerpt when a trusted project tool fails instead of reporting only
  its exit code.
- Recognize JavaScript CLI tests that invoke declared package binaries as smoke coverage.
- Distinguish blocked or transient link checks from confirmed broken links and collapse repeated
  occurrences of the same URL in one file.
- Route Trivy misconfiguration scans through IaC applicability, including IaC-only repositories,
  and preserve container checks while keeping dependency reviews limited to vulnerability scanning.

## 0.4.0 — 2026-09-11

- Validated Gitleaks, OSV-Scanner, Trivy, actionlint, zizmor, markdownlint-cli2, and Lychee against
  controlled defects and corrected their current command and output contracts.
- Preserved advisory severity, affected/fixed versions, IaC source locations, broken-link locations,
  and multi-tool provenance while suppressing only demonstrated same-location duplicates.
- Made unavailable or trust-disabled tools explicitly partial instead of presenting an incomplete
  blueprint as passed; bounded Lychee and OpenAI provider execution.
- Fixed lockfile-only and nested-manifest applicability, Git-aware secret scan scope, actionlint
  workflow scope, Trivy dependency-directory scope, and generated GitHub Actions credentials.
- Distinguished generated smoke tests from meaningful unit coverage and stopped documentation-only
  observability remediation from falsely closing an integration gap.
- Added real-tool, parser, applicability, aggregation, timeout, and incomplete-report regressions,
  plus an independent six-repository matrix and disposable service-strengthening case study.
- Reconciled the release with the September dependency and GitHub Actions updates, regenerated the
  locked environment, and migrated ignore matching to `GitIgnoreSpec` for pathspec 1.1 compatibility.

## 0.3.0 — 2026-09-11

- Established an explicit trust boundary for target-controlled tool overrides, local wrappers, and
  package-local executables; added offline/no-model execution semantics.
- Bounded and sanitized tool output, isolated subprocess environments, killed timed-out process
  groups, hardened Git/config/file parsing, and added permanent hostile-repository regressions.
- Added stable report metadata/schema, JUnit output, configurable CI exit policy, prompt identifiers,
  call/cache controls, corrupt-cache recovery, and richer context metrics.
- Added safe declarative project-local blueprints with strict schemas and built-in blueprint/adapter
  contract validation; executable extension hooks remain trusted Python only.
- Hardened create-only transactions, manifest validation, symlink handling, conflict rollback, and
  atomic baseline/model-cache writes.
- Added repeatable large-repository benchmarks and documented extension, threat, and release gates.

## 0.2.0 — 2026-09-11

- Expanded the catalog to the Phase 2 project, code, testing, API/data/config, security/supply-chain,
  infrastructure, delivery, reliability/operations, documentation, and AI capability families.
- Added composable profiles and deterministic applicability states with reasons.
- Added bounded concurrent tool orchestration, richer structured parsers, and six first-class language
  routes.
- Added stable finding fingerprints, source aggregation, baselines, reasoned expiring suppressions,
  Markdown, and SARIF.
- Added changed-file model context, symbol/dependency summaries, secret redaction, cache and token
  metrics.
- Added versioned atomic capability kits with operation manifests, verification, idempotency, and
  safe rollback.

## 0.1.0

- Established the deterministic-first Phase 1 engine, CLI, blueprint contract, adapters, bounded
  model review, and safe create-only remediation.
