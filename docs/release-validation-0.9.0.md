# Blueprint AI 0.9.0 release validation

Prepared 2026-09-13 from released `0.8.0` commit
`61853f60cb0c9f53c818ace5a5da0bd9a9741647`. The focused read-only audit and its copied target
repositories remained external evidence; release-source changes were made only in this repository.

## Capability boundary

This release adds one deterministic pipeline step kind: dependency acquisition. Only that stage can
request explicitly authorized network access. Acquired dependency data is bounded to private
transaction paths, preserved across stages, excluded from publication, and removed only after
final-state idempotence replay. Migration and verification stages remain offline.

Execution fails on a nonzero exit, timeout, truncated output, or sandbox OOM evidence. Planning can
fingerprint a tracked leaf symlink without dereferencing it; mutation checkpoints still reject
symlinks. These behaviors preserve the prior sealed plan, exact-write, recovery, and rollback model.

## Real migration evidence

| Repository or fixture | Result | Evidence |
| --- | --- | --- |
| Hyperfine at `4fa16852` | Verified Rust 2018→2021→2024 apply, no-op repeat plan, exact rollback | Locked crates.io vendoring, both edition fixes, manifest edits, authoritative formatting, compiler and all-feature/all-target tests passed; binary-only target correctly omitted doctests |
| .NET console formatter | Verified `net7.0`→`net10.0`, no-op repeat plan, exact rollback | Exact `Microsoft.Extensions.Hosting` 7.0.0 restore succeeded under authorized network access and the SDK 10 build passed offline |
| .NET App Configuration Web sample | Verified `net6.0`→`net10.0`, exact rollback | `Microsoft.NET.Sdk.Web` and exact Azure package restore/build passed; one upstream ASP.NET analyzer suggestion remained a warning |
| Contentful Next.js template at `fd136d0` | Correctly failed closed and recovered exactly | Both codemods processed 48 files serially without OOM; the typed gate rejected an unresolved `UnsafeUnwrappedDraftMode` marker before publication |
| rsc/quote at `5d9f230` | Correctly rejected after exploratory execution narrowed the lane | Acquisition and offline vendoring worked, but the module lacked an explicit Go directive and the tool proposed an implicit language upgrade; its existing `buggy` test also failed |
| Derived rsc/quote v3 at `30c3fee` | Verified no-change Go pipeline | After explicitly sealing the language version, committed dependency graph, and truncated source repair, checksum acquisition, vendoring, offline fix/test, and final replay all passed without public writes |

The supported Rust lane is limited to one root package, a lockfile, crates.io or local-path
dependencies, and explicit editions. Git/custom registries remain rejected. The supported .NET lane
requires one literal target framework and exact package versions; floating, conditional, and central
package management remain rejected. Go requires one root module and explicit language version.
Next.js remains partial at the explicit lockfile-backed project verification boundary.

## Candidate gate

Ruff formatting/lint and mypy pass across 83 source files. The complete pytest suite passes 486
tests with 7 environment-specific skips, deprecations treated as errors, and 83% total coverage.
Generated support-registry equality is covered by the suite. Public schema parsing, deterministic
no-model self-review, dependency audit, distribution archive verification, Twine checks, Actionlint,
offline pedantic Zizmor, and 33-check fresh wheel and sdist smoke installs on Python 3.14.7 all pass.
The deterministic production self-review reported no P1/P2 findings; its 18 P3 findings are the
existing large-function and cache heuristics. Live OSV and pip-audit found no known vulnerable
dependency, Trivy reported no high/critical vulnerability, misconfiguration, or secret, and Gitleaks
reported no secret in the candidate archives. Link validation passed 268 links while explicitly
accepting one upstream page's automated-client 403 response.
The local candidate wheel SHA-256 is
`b8f0ebfede578bfa0ed6e1b9d47221c547427d1e2d6facae3b8cbf154b8ba99b`; the sdist SHA-256 is
`7563b37cd31adf2f6c4a4079477cdbc8a8169cb20622d80fdf02377f11e02cd7`. These hashes identify the
local candidate only. Hosted CI, tag-built checksums, GitHub Release assets, build/SBOM attestations,
and the repository's explicit PyPI opt-in are verified separately after tagging.

No live model request was needed or made for migration execution. No production `AgentBackend` was
added; residual work remains a sealed descriptive contract rather than autonomous authority.
