# Phase 4 validation

Date: 2026-09-11. Release candidate: 0.4.0.

## Decision

**RELEASE READY WITH DOCUMENTED LIMITATIONS.** Deterministic behavior, packaging, and the priority
optional-tool integrations have real execution evidence. No live model credential was available, so
remote model quality, billing, and service behavior remain an explicit release limitation. The
existing `0.3.0` tag was not moved. This evidence document was finalized before the annotated 0.4.0
release tag; no package publication is part of this release.

## Baseline

Commit `7cb2c9a` is tagged `0.3.0` (the blueprint called it `v0.3.0`; the repository uses an
unprefixed tag). The starting worktree was clean and Phase 4 used branch
`codex/phase-4-real-world-validation`.

The 0.3.0 gate passed formatting, linting, typing, 78 tests, dependency audit, schema generation, and
wheel/sdist build at 79% coverage. `doctor` found no model credential and none of the seven priority
optional tools. Baseline self-review exposed a workflow credential-persistence finding and, more
importantly, labeled trust-disabled mypy/pytest/markdownlint analysis as passed.

Baseline synthetic benchmark:

| Files | Discovery | Context | Peak memory | Files read | Estimated tokens |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 101 | 71.209 ms | 108.680 ms | 677,643 B | 101 | 5,011 |
| 1,001 | 287.965 ms | 503.496 ms | 1,884,257 B | 500 | 12,000 |
| 5,001 | 1,068.346 ms | 1,498.039 ms | 7,149,567 B | 500 | 12,000 |

## Architecture and implementation findings

- Tool status was semantically wrong: `unsupported` work could leave a blueprint `passed`. It now
  produces `partial`, is named under incomplete tools, and reports that no findings from completed
  checks is not proof of a complete pass.
- Manifest discovery flattened paths and missed lockfile-only/nested projects. It now retains actual
  relative paths and selects supply-chain analysis from lockfile evidence.
- Adapter parsers discarded useful advisory severity, installed/fixed versions, IaC locations, and
  link locations. Those fields now survive normalization and report serialization.
- OSV/Trivy and actionlint/zizmor overlap did not aggregate reliably. Common stable IDs now merge
  equivalent findings while preserving every source and tool-specific evidence.
- Filesystem Gitleaks scanned ignored runtime caches in Git projects; Git mode now limits the scan to
  repository history. Trivy no longer descends into dependency directories or duplicates Gitleaks'
  secret responsibility. actionlint receives explicit discovered workflow paths.
- Network link checks could stall and produced noise for expected local development URLs. Lychee now
  has request/retry and adapter deadlines and excludes loopback, while still checking missing local
  files. Documentation tools receive discovered files rather than recursively scanning ignored
  environments.
- A generated manifest assertion was called a unit test, and an observability document was offered
  as a safe fix for missing runtime instrumentation. Generated checks now claim only smoke coverage;
  unit and integration work remain manual. The observability guidance kit remains available but is
  not presented as closing the integration finding.
- Generated CI and the repository's own CI now disable checkout credential persistence. OpenAI client
  construction uses a 60-second timeout and one retry, and `doctor` distinguishes a missing key from
  a missing SDK.

## Real optional-tool evidence

The tools were installed outside runtime dependencies and run both directly and through Blueprint
AI. Fixtures used a synthetic non-live GitHub-shaped token, lodash 4.17.20 lockfile, unrestricted SSH
Terraform rule, unsafe/unpinned Actions workflow, malformed Markdown, and missing local link.

| Tool/version | Controlled result preserved by Blueprint AI |
| --- | --- |
| Gitleaks 8.30.1 | Specific secret rule, redacted evidence, file and line; same-location generic duplicate removed. |
| OSV-Scanner 2.5.1 | Advisory severity, aliases, ecosystem, installed and fixed versions. |
| Trivy 0.74.0 | Dependency CVEs and nested Terraform cause line; duplicate CVEs aggregated with OSV. |
| actionlint 1.7.12 | Untrusted-expression message, workflow line, and snippet. |
| zizmor 1.30.1 | Offline SARIF findings, including template injection, unpinned action, permissions, and credential persistence. |
| markdownlint-cli2 0.23.2 | Markdown rule and source location through the explicit trust boundary. |
| Lychee 0.24.2 | Missing local link, source line, URL, detail, and exit-2 finding semantics. |

Malformed output, non-finding failures, output bounds, timeouts, missing tools, and trust-disabled
tools remain explicit tool errors/incomplete states. Synthetic fixtures were deleted after validation.

## Model validation

The optional OpenAI SDK was installed, but `OPENAI_API_KEY` was not available. No billable call was
attempted and no model value claim is made. Existing controlled-provider tests still cover structured
responses, malformed responses, timeout/failure degradation, cache hits and prompt-version keys,
redaction, injection isolation, budgets, metrics, and deterministic independence under `--no-model`.
The real service path remains the principal documented limitation.

The final locked OpenAI SDK is 3.13.0. A local contract check confirmed that its Responses API still
accepts the provider's `instructions`, `input`, token limit, storage, metadata, and structured-text
arguments, as well as the configured client timeout and retry bound. This is SDK compatibility
evidence only; it does not substitute for a live service call.

## Real-repository matrix

Six unrelated local repositories were reviewed read-only with production/no-model semantics:

| Shape | Main evidence and resulting decision |
| --- | --- |
| Python CLI | An ignored runtime cache caused a secret false positive; Git-aware Gitleaks scope removed it. |
| Node/Express container service | Old lockfile advisories, missing tests, root container, and absent health check were correctly prioritized; used for the case study. |
| Frontend/web application | Trivy initially reported Dockerfiles under `node_modules`; dependency-directory exclusion removed that false scope. |
| Terraform/IaC | Unrestricted SSH was located correctly; native validate/TFLint remained partial without project trust and Checkov was missing. |
| Documentation-heavy repository | Lychee completed with bounded retries and found five broken links; markdownlint remained explicitly trust-gated. |
| Full-stack template | A legacy dependency graph yielded hundreds of real advisories, confirming useful priority/source aggregation but also the triage limitation of old lockfiles. |

The matrix favored accuracy over zero findings. Missing or trust-gated tools were never treated as
passes. The source repositories remained unchanged.

## Strengthening case study

`customer-api`, an older Express/container service, was copied to a disposable directory. Baseline
review found 56 items: P0 3, P1 17, P2 15, P3 21. It had no test command, ran its container as root,
had no health check or `.dockerignore`, and carried a substantially outdated dependency graph.

Blueprint AI's container kit added `.dockerignore`. Human judgment added two behavior-level Node
tests for query construction, a native `node --test` script, and a Dockerfile using a current pinned
Node tag, reproducible production install ordering, one no-cache package layer, non-root runtime, and
a health check. Both project tests passed. Review then found 48 items: P0 3, P1 13, P2 13, P3 19.
The unit-test, root-runtime, package-cache, and health-check findings genuinely disappeared; no
suppressions or baseline were used. Critical legacy dependency upgrades were intentionally not
guessed, and integration/performance/observability gaps correctly remained.

This exercise directly caused the smoke/unit distinction and observability-remediation correction.
The first attempted generic CI kit also created a new missing-cache recommendation; it was rejected
as case-study evidence rather than counted as improvement.

## Final verification and performance

Before the release gate, Phase 4 was rebased onto the two Dependabot merges already present on
`main`. The current checkout/setup action pins were retained while the Phase 4 credential-persistence
hardening was preserved. The Python constraint updates were retained and `uv.lock` was regenerated.
That exercise exposed pathspec 1.1 deprecation warnings in discovery; ignore matching now uses its
dedicated `GitIgnoreSpec` API, with a regression proving `.gitignore` negation remains visible.

The final gate passes Ruff formatting/linting, mypy, 93 tests, 80% coverage, dependency audit, schema
generation, wheel/sdist build, fresh-wheel installation, CLI smoke tests, and deterministic
self-review. The package audit reports no known third-party vulnerabilities; the local package is
properly skipped because it is not yet on PyPI.

Final synthetic benchmark:

| Files | Discovery | Context | Peak memory | Files read | Estimated tokens |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 101 | 66.379 ms | 100.205 ms | 677,518 B | 101 | 5,011 |
| 1,001 | 202.334 ms | 461.558 ms | 1,884,273 B | 500 | 12,000 |
| 5,001 | 973.023 ms | 1,553.885 ms | 7,150,988 B | 500 | 12,000 |

Context remains capped at 500 file reads and 12,000 estimated tokens. `--no-model` made zero model
calls. Discovery medians remained below baseline at every scale; context construction showed
host-load variance and the 5,001-file sample was about 4% slower than baseline in the recorded final
run, so no context-speed improvement is claimed. Controlled cache tests prove repeat hits and
content/prompt invalidation without remote calls. The final trusted self-review produced zero
findings and passed all twelve applicable blueprints using all ten tools. The untrusted review also
produced zero findings but correctly remained partial for mypy, pytest, and markdownlint. No fixture,
generated cache, external-repository edit, tag, or publication is included.
