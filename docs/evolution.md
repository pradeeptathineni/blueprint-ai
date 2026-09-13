# Project evolution

Blueprint AI 0.9.1 plans and transactionally runs deterministic, version-aware migrations. A plan
seals the current Git inventory, explicit target, ordered stages, authoritative tool contract,
expected writes, typed postconditions, and verification. Planning never selects “latest.”

## Plan and apply

Inspect without selecting work, or request an exact target with `ID=TARGET`:

```bash
blueprint-ai evolve catalog
blueprint-ai evolve plan ./project
blueprint-ai tools install cargo
blueprint-ai evolve plan ./project --target rust/edition=2024 \
  --output ./project/.blueprint-ai/plans/rust-2024.json
blueprint-ai evolve apply ./project/.blueprint-ai/plans/rust-2024.json ./project \
  --dry-run --sandbox docker --trust-project-executables
blueprint-ai evolve apply ./project/.blueprint-ai/plans/rust-2024.json ./project \
  --sandbox docker --trust-project-executables
```

The CLI selects each stage's sealed tool image from the plan but never pulls it. `tools install` is
the separate, explicit acquisition step and records the local immutable image identity. A composed
transaction may use multiple authoritative images while retaining one sealed source transaction.
Trusted host execution remains opt-in and is not isolated.

`schema evolution-plan`, `schema evolution-report`, and `schema transformation` expose the strict
JSON contracts. Missing required targets become manual decision steps; unsupported targets fail.
Independent ready prefixes can run and report `partial`, while a step dependent on an unresolved
manual predecessor remains blocked.

## Pipeline and tool contracts

A migration pipeline reuses evolution steps and transactions. Its small step vocabulary is native
dependency acquisition, native command, established codemod, built-in edit, postcondition, manual
boundary, and future residual boundary. Dependencies preserve ordering; state-changing stages can
trigger rediscovery before the next stage. Dry-run executes the full pipeline only in a disposable
copy and returns its exact final diff. Acquisition requires explicit trusted network authorization
and records the contract's expected destinations, but the local OCI adapters cannot enforce
destination filtering. The operator must supply network-level egress controls when unrestricted
access is too broad. Acquired data persists only in private transaction paths for later offline stages.

Authoritative command contracts seal provider, host/container executable, version range, independently
pinned runner and recipe, registry integrity, source and licenses, invoke-versus-redistribute status,
image and digest, network/credential/mount needs, noninteractive flags, success exits, write scope,
resource limits, postconditions, and verification. They are bounded catalog data, not a general
command runner. Managed runs use `--pull=never`, verify the image identity, isolate caches, and apply
the contract’s time, CPU, memory, process, scratch, file, and output bounds.

Typed postconditions are serializable plan and report evidence. The vocabulary covers forbidden or
required patterns, exact manifest/config values, commands, migration markers, and new scoped findings.
Current pipelines use exact TOML/JSON/XML values and migration-specific forbidden/required patterns;
compiler, build, test, lint, native-idempotency, and Blueprint review remain explicit verification
gates. Every executable pipeline replays its final-boundary authoritative commands and structural
edits against the final staged state and requires an exact no-op. A postcondition or replay failure
discards the transaction even when an external tool exited successfully.

## Transaction and rollback

Evolution requires Git and refuses to mutate `main` or a dirty worktree by default. Any source,
index, or project-fingerprint change after planning makes the plan stale, and apply re-resolves the
canonical catalog so edited/re-signed JSON cannot add commands or scope.

Blueprint AI checkpoints the bounded inventory, runs every stage in a disposable copy, rejects
unexpected writes (including ignored files), verifies postconditions, and publishes only the exact
verified bytes. The final review rejects introduced deterministic findings. Failures restore any
published path, return a structured failed report from the CLI, and leave no success receipt. A
successful apply records original bytes in Git-private state; rollback refuses branch, HEAD, index,
or post-apply conflicts and proves the checkpoint fingerprint:

```bash
blueprint-ai rollback OPERATION_ID ./project --json
```

A truthful partial prefix records its unresolved boundary. After completing only the sealed manual
paths and stated project checks, record bounded evidence and make the accepted target eligible for
no-op planning:

```bash
blueprint-ai evolve accept OPERATION_ID ./project \
  --evidence "lockfile install completed" \
  --evidence "build, type, lint, and tests passed"
```

## Supported migrations

- `rust/edition=2021|2024`: requires one root package with an exact owned 2018/2021 edition and
  `Cargo.lock`; workspaces, independent nested packages, Git dependencies, and custom registries are
  rejected. It acquires the locked crates.io graph into private transaction state, runs
  `cargo fix --edition` one boundary at a time, edits only explicit edition fields, applies
  authoritative `cargo fmt`, and then runs locked offline fmt/check/test gates across all features
  and targets. Doctests run for library targets. Inactive cfg combinations remain a stated
  limitation.
- `dotnet/sdk-target=net10.0`: one `Microsoft.NET.Sdk` or `Microsoft.NET.Sdk.Web` project with one
  literal TFM and either no packages or exact package versions. Package graphs are restored into
  private transaction state and compiled offline in an isolated writable copy. Multi-target,
  inherited, floating, conditioned, centrally managed, and complex application/API modernization
  remain rejected or residual.
- `python/pep517-build-system=setuptools.build_meta`: adds only the current PyPA build-system table,
  preserves `setup.py`/`setup.cfg`, and builds a wheel in an isolated writable copy. It does not infer
  PEP 621 metadata or uv workflow semantics.
- `go/native-fix`: one root module with an explicit Go language directive. When dependencies exist,
  it acquires missing checksums, vendors the graph into private transaction state, and then runs
  `go fix` and `go test` offline. Nested modules and implicit `go.mod` language upgrades are rejected.
- Ruff pyupgrade, Terraform native format, Docker `MAINTAINER` conversion, and known official GitHub
  Action same-major SHA pinning retain their supported behavior.

Next.js 14→15→16 has an executable but `partial` authoritative prefix. It runs integrity-pinned
`@next/codemod` 16.3.5 major-by-major with execution networking disabled, normalizes exact
Next/React/type/config dependencies, and rejects nested/workspace packages, ambiguous convention
files, Edge Proxy runtime, dynamic removed-font references, removed cache APIs, `experimental_ppr`,
and official `@next-codemod-error` markers. Exactly one newly generated root lockfile and operator-supplied
project build/type/lint/test evidence form the sealed acceptance boundary. Jscodeshift stages run
serially, unsafe-unwrapped request API markers fail the typed residual gate, and acceptance supports
exactly one existing or newly generated root lockfile.

React 19 is truthfully `partial` but not automatically executable. Codemod CLI 1.18.3 is
integrity-pinned, while `react/19/migration-recipe@0.1.5` is resolved separately by the registry
runner and cannot be verified before it receives a writable source tree. Planning therefore stops
read-only at `verified-recipe-acquisition`. The audit's invalid `this.refs` output remains a permanent
regression, and the narrow repair is tested as a completion primitive but is not invoked
automatically.

The generated [support registry](support.md#evolution-transformations) is authoritative for every
supported, partial, experimental, and deferred entry. CRA destination choice, CommonJS-to-ESM,
Terraform/OpenTofu state, Kubernetes policy, Docker runtime upgrades, broad OpenRewrite/Spring, and
generic ast-grep remain manual or deferred.

`ResidualContract` seals desired state, completed deterministic work, failures, permitted paths,
prohibited scope, acceptance commands, postconditions, network/credential/command authority,
budgets, and rollback checkpoint.
No production `AgentBackend` consumes it in 0.9.1: the corpus demonstrated no residual that justified
broader mutation authority. `ModelProvider` remains optional bounded decision/review support and is
never used by deterministic migration execution.

Read-only planning fingerprints bounded leaf symlinks without dereferencing them. Mutation still
refuses symlink checkpoints, so a tracked link cannot redirect reads or writes outside the selected
repository.
