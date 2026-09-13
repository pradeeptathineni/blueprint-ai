# Blueprint AI 0.9.0

Blueprint AI 0.9.0 makes supported migrations dependency-aware without adding speculative model or
agent mutation.

- Rust edition migration now vendors the locked crates.io graph under explicit network
  authorization, then runs fix, manifest transition, formatting, compiler, all-feature/all-target
  tests, library doctests, and final replay offline. The managed Rust image includes native build
  prerequisites required by real crates.
- One exact-package `Microsoft.NET.Sdk` or `Microsoft.NET.Sdk.Web` project can move to `net10.0`:
  NuGet restore is private to the transaction and verification builds offline in an isolated copy.
- Root Go modules with an explicit language directive can acquire checksums and a private vendor
  tree before offline `go fix` and tests. Nested modules and implicit language upgrades remain
  outside the supported lane.
- Next.js jscodeshift stages run serially, unresolved unsafe request-API markers fail closed, and the
  manual completion receipt supports exactly one existing or newly generated root lockfile.
- Process OOM, timeout, and truncated-output evidence can no longer be overridden by a nominal zero
  exit. Read-only plans fingerprint leaf symlinks, while mutation checkpoints continue to reject
  them.

All executable migration decisions remain deterministic and no-model. No production `AgentBackend`
has been added.
