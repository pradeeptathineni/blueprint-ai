# Blueprint AI 0.8.0 release validation

Prepared 2026-09-12 from released `0.7.1` commit
`e193a78e85feb9861e58931d255a495771d14c90`. The read-only Phase 8 audit in
`../blueprint-ai-tmp/phase8-audit` remained the empirical input and was not modified.

## Capability boundary

Phase 8 adds one ordered pipeline over the existing sealed evolution plan, staged transaction,
publication receipt, and rollback model. A stage can be an authoritative native command, an
established codemod, a bounded built-in edit, a typed postcondition, or a manual/residual boundary.
Dependencies, rediscovery, downstream resealing, stage-local write scopes, final-state replay,
source/HEAD/index protection, and failure recovery remain deterministic.

External commands use versioned authoritative-tool contracts. The contracts separate runner and
recipe identity, record source and license, distinguish invocation from redistribution, constrain
networking and credentials, seal expected writes and successful exits, and apply time, process,
memory, scratch, and file limits through the existing sandbox policy. Acquisition is explicit;
execution for every exercised migration used networking disabled.

Typed postconditions cover exact TOML/XML/JSON values, dependency versions, required and forbidden
syntax, path presence/absence, migration markers, compiler/build/test commands, and native no-change
replay. A zero tool exit is never sufficient by itself.

## Supported and partial migrations

| Migration | Mechanism and verification | Boundary |
| --- | --- | --- |
| Rust 2018→2021→2024 | `cargo fix --edition`, literal manifest edit, rediscovery, offline locked fmt/check/test/doctest, final replay | Supported for one root package; workspaces, nested packages, inherited/mixed editions, and unknown cfg combinations are rejected or explicit limitations |
| .NET→net10.0 | Literal TFM XML edit plus .NET 10 offline restore/build | Supported for one dependency-free `Microsoft.NET.Sdk` project; multi-target, inherited, conditional, Web SDK, package/API, and complex applications are rejected |
| Legacy setuptools→PEP 517 | Minimal `[build-system]` edit plus wheel build | Supported; preserves `setup.py` and `setup.cfg`, does not infer PEP 621 metadata or uv workflow intent |
| Next.js 14→15→16 | Integrity-pinned `@next/codemod` 16.3.5 stages, deterministic dependency/config edits, rediscovery, residual scans, final replay | Partial; nested/workspace packages and ambiguous states are rejected, then exactly one new root lockfile plus operator build/type/lint/test evidence is required for acceptance |
| React 18.3→19 | Read-only verified-recipe acquisition boundary | Automatic execution rejected: the registry recipe is resolved separately and cannot be integrity-verified before it sees writable source; the observed invalid `this.refs` output remains a regression and its narrow repair is test-only |
| `actions/setup-java` pinning | Reviewed same-major tag-to-SHA mapping plus YAML/static checks | Supported for v2–v6; never changes the selected major and never guesses third-party mappings |

The 0.7.x Ruff, Go, Terraform, Dockerfile, and known official-action transformations remain
supported. CRA destination selection, requirements-to-uv intent, CommonJS-to-ESM, stateful
Terraform/OpenTofu changes, Kubernetes target-policy acceptance, runtime/base-image upgrades,
OpenRewrite/Spring, and generic ast-grep mutation remain manual, partial, or deferred.

## Tool provenance

| Tool | Pinned identity | License and network |
| --- | --- | --- |
| Rust | `blueprint-tools/rust:1.98.1`, local image ID `sha256:180af6c640009e371a18a6a5e7ba5aef4b85a2f4663df146fe7b0e6b921145d7`; Cargo `>=1.85,<2`, recipe `cargo fix --edition` v1 | MIT OR Apache-2.0; invoke, do not redistribute; execution network none |
| .NET | `mcr.microsoft.com/dotnet/sdk@sha256:2fa828c68761b1b8c23d7662dc134421b9d3b59fe1425fdbc80804e390cdb24d`; SDK `>=10,<11` | MIT; invoke, do not redistribute; execution network none |
| Python build | `blueprint-tools/python-build:3.12-setuptools84`, local image ID `sha256:b7ab70455de525e13751f2d226fa548dd5bfa5826191accbd1f985868b0f357b`; Python `>=3.12,<4` | PSF-2.0 toolchain and MIT setuptools recipe; invoke, do not redistribute; execution network none |
| Next.js | `blueprint-tools/next-codemod:16.3.5`, local image ID `sha256:86642a418fbd40000f4a8240476c9f4b54c8b0ec8fa6ab3fca45946ace039f58`; `@next/codemod` runner/recipe 16.3.5 with reviewed npm integrity | MIT; invoke, do not redistribute; registry access only during explicit image acquisition, execution network none |
| React | Codemod CLI 1.18.3 and registry recipe 0.1.5 recorded independently | Apache-2.0 runner and MIT source recipe; no automatic invocation because the runtime-resolved recipe artifact is not pre-mutation verifiable |

## Corpus and adversarial evidence

The real managed-image corpus produced these final outcomes:

- Rust: six stages, 21 verifications, two edition boundaries, reproducible output, no-op rerun, and
  exact rollback.
- .NET: two stages and six verifications; Python: two stages and seven verifications; setup-java:
  two deterministic checks. Each reproduced and rolled back exactly.
- Next.js: ten stages and 43 verifications. The prefix remained `partial`; repeat before acceptance
  was blocked, explicit lockfile/evidence acceptance made the exact target a no-op, and rollback was
  exact.
- React automatic execution, already-current Rust, nested Rust/Next component topologies, and
  ambiguous .NET were rejected read-only.
- Every exercised case recorded zero model calls and zero agent calls.

Adversarial coverage includes stale plans, changed source/HEAD/index, unsupported and ambiguous
targets, stage-crossing and ignored writes, timeouts/nonzero tools, malformed output, denied network,
wrong executables, postcondition failure after tool success, final replay, introduced findings,
partial-prefix failure, forged acceptance receipts, lockfile conflicts, rollback conflicts, nested
component boundaries, and exact recovery. Public receipts are bound to Git-private operation-state
seals; ignored accepted lockfiles are fingerprinted and removed on rollback.

## Candidate quality gate

- Ruff formatting/lint and mypy passed across 83 source files.
- The full source suite passed: 474 tests, 7 environment-specific skips, 82% total coverage.
- The explicit live OCI sandbox suite passed all 118 tests with Python and Node images present.
- Phase 7 real tools passed all five supported transformations. The Phase 8 real corpus passed every
  expected supported/partial/rejected outcome with exact rollback and reproducibility.
- `pip-audit`, OSV Scanner, Trivy high/critical vulnerability/misconfiguration/secret scanning, and
  Gitleaks reported no findings. The local 0.8.0 distribution is skipped by registry lookup because
  it is not yet published on PyPI.
- All 13 public schemas parsed, generated support documentation matched canonical metadata,
  Actionlint passed, and Zizmor pedantic offline analysis reported no findings.
- Wheel and sdist passed the repository archive verifier and Twine 7.0.0. Fresh wheel and sdist
  installs each passed 33 CLI checks on Python 3.12.10 and 3.14.7.
- Deterministic no-model production self-review emitted no P1/P2 findings. Its 18 P3 items are
  existing large-function and missing-cache heuristics rather than migration correctness failures;
  optional tool coverage remained explicitly partial where project execution was not trusted.

The candidate wheel SHA-256 is
`0ea50e5fd154bf81dec7abeb18d3741cb51d0448ddf988a5d387aaf4b2e83bd3`; the candidate sdist SHA-256
is `01ad23cb7f70de05424f89bdea3c8fad1a5ea73bdd9834eb247d265c2e1e92e3`. These identify the local
candidate only. Hosted tag-built asset hashes and attestations must be verified independently.

An independent adversarial review found and drove fixes for ignored-file recovery, trusted manual
acceptance, nested component ownership, all-target Rust scope, and lockfile ambiguity. Its final
implementation review found no remaining blocker after those fixes.

No `OPENAI_API_KEY` was present. No live model request, latency, token, cost, cache, or quality claim
is made. `ModelProvider` remains bounded decision support; `ResidualContract` now includes path,
postcondition, network, credential, command, budget, and rollback authority, but no production
`AgentBackend` exists because the corpus demonstrated no need for one.

This evidence classifies 0.8.0 as release-ready pending exact merged-source hosted CI, immutable tag
publication, GitHub Release asset/checksum/attestation verification, and an explicit PyPI publisher
opt-in. PyPI publication must remain skipped unless Trusted Publishing is correctly configured and
`BLUEPRINT_PUBLISH_PYPI=true`.
