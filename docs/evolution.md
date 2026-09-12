# Project evolution

Blueprint AI 0.7.0 adds one evolution workflow: inspect current state, seal an explicit desired-state
plan, preview or apply ordered transformations, verify the result, then accept it or restore exact
pre-evolution bytes. Planning never chooses an upgrade merely because one is newer.

## Plan and apply

Inspect applicable catalog entries without selecting work:

```bash
blueprint-ai evolve catalog
blueprint-ai evolve plan ./project
```

Select stable transformation IDs and save the sealed plan in the ignored Blueprint state directory:

```bash
blueprint-ai evolve plan ./project \
  --target container/maintainer-to-oci-label \
  --target github-actions/pin-official-actions \
  --output ./project/.blueprint-ai/plans/evolution.json
blueprint-ai evolve apply ./project/.blueprint-ai/plans/evolution.json ./project --dry-run
blueprint-ai evolve apply ./project/.blueprint-ai/plans/evolution.json ./project
```

The JSON plan includes current project state and fingerprint, desired state, ordered dependencies,
matched files, preconditions, risks, manual boundaries, verification requirements, provenance, and
zero-call model/agent budgets. `schema evolution-plan`, `schema evolution-report`, and
`schema transformation` expose the strict interchange contracts.

Command-backed transformations require an explicitly available compatible tool and execution policy.
Trusted host execution is opt-in and is not isolated:

```bash
blueprint-ai evolve apply plan.json ./project --dry-run \
  --sandbox host --trust-project-executables
blueprint-ai evolve apply plan.json ./project \
  --sandbox host --trust-project-executables
```

Use `--sandbox docker --sandbox-image IMAGE --trust-project-executables` for an already-acquired
image containing the selected tool. Blueprint AI does not pull migration tools or enable network
access during apply. A tool-driven write without explicit trust and a writable target is rejected.

## Transaction and verification boundary

Evolution requires Git and refuses to mutate `main` or a dirty worktree by default. `--allow-main`
and `--allow-dirty` are explicit acknowledgements; a dirty plan still seals the exact observed bytes.
Any project change after planning makes the plan stale. Blueprint AI also re-resolves the selected
catalog entries at apply time, so modifying and re-signing plan JSON cannot introduce commands or
expand file scope.

Before mutation, Blueprint AI checkpoints the bounded Git inventory and creates a disposable staged
copy outside the worktree. Every built-in and native command changes only that copy first. Native
commands use isolated caches, no network, bounded output and time, compatible-version checks, their
own dry-run mechanism, an idempotency rerun, and declared project verification. The staged inventory
includes normally ignored tool output, so an unexpected cache/build/source write aborts publication.
Built-ins parse their target structure and produce unified diffs. Only verified graph-selected bytes
are published atomically back to the worktree with their file modes preserved. The final gate rejects
concurrent changes, verification mutation, tool failures, or new deterministic Blueprint AI findings.

Any caught failure discards the stage, restores every Blueprint-published path to its checkpoint,
preserves unrelated concurrent edits, and leaves no success receipt. A verified apply writes an
ignored manifest under `.blueprint-ai/operations/`; original changed bytes live in Git-private state.
Rollback is atomic in its preflight and refuses to overwrite post-apply edits:

```bash
blueprint-ai rollback OPERATION_ID ./project --json
```

Rollback also requires the same branch, HEAD, and Git index recorded at apply time. After restoration,
the complete tracked and unignored project fingerprint must equal the checkpoint. The checkpoint is
local and temporary operational state, not a substitute for a branch, commit, backup, or remote
recovery. Existing ignored dependencies/artifacts are not copied into the tool stage; a native
verifier that requires them must use its isolated cache/vendor inputs or will fail safely.

## Supported boundary

The supported 0.7.0 set is deliberately small:

- Ruff stable `UP` fixes, isolated and bound to `project.requires-python`, for owned Python source.
- Go 1.26+ `go fix` for selected package directories, followed by `go test ./...` with network off.
- Terraform 1.6+ native formatting for owned native-syntax HCL files; no provider, backend, or state
  migration is implied.
- Single-line Dockerfile `MAINTAINER` to OCI authors-label conversion, rejecting ambiguous/conflicting
  files.
- Known official `actions/*` mutable major tags to reviewed full SHAs, preserving the selected major
  and leaving unknown, quoted, expression, reusable-workflow, and third-party references unchanged.

The generated [support registry](support.md#evolution-transformations) is authoritative for every
supported, partial, experimental, and deferred entry and its limitation. Python requirements to uv,
Java/OpenRewrite, React/Next codemods, Rust edition changes, Kubernetes conversion, generic ast-grep,
Spring recipes, .NET modernization, and Terraform-to-OpenTofu are inspectable candidates but are not
executable Blueprint-managed migrations in 0.7.0.

No `AgentBackend` is implemented. All executable residual work in this release is deterministic, so
silently introducing a coding agent would add authority and token cost without improving coverage.
The official Codex App Server is the researched future rich boundary and `codex exec` the simpler
one-shot boundary; either would require an explicit repository, mutation, command, network, approval,
time/token, step, diff-scope, and post-verification policy before it can execute. `ModelProvider`
remains bounded review-only inference and never edits files.
