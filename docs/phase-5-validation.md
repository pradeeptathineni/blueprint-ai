# Phase 5 validation and release decision

Prepared 2026-09-12; release finalization is recorded below. Recommended release: **0.5.0, ready
for the documented local genesis/review scope**. The initial implementation validation did not
create a release tag or remote publication. Production readiness of generated
applications, live model judgment, remote templates, and cloud deployment are not certified.
The compact [machine-readable evidence](phase-5-evidence.json) records exact corpus SHAs, contexts,
provider results, image subjects, tool versions, and measurement identities. It contains derived
results, not vendored external source or raw research artifacts.

## Exact starting baseline

Started from clean `main`, commit `b3c5fc673a0741bdeec29eedc13b3485c564ad02`, package/changelog 0.4.2.
Created `codex/phase-5-project-genesis`. The existing annotated tag `0.4.2` had object
`0769530be53e97f0c22e66971894736f4555fe9b` and pointed to that same commit. The retained audit reported
package 0.4.2 while observing 0.4.1 as its latest tag. Same-commit evidence supports a tag-observation
timing difference, not a different analyzer revision; retained evidence does not establish the exact
publication sequence. No historical tag was created, moved, or rewritten.

Baseline: 114 tests, 81% statement coverage; lint, format, mypy, wheel/sdist build, and dependency
audit passed. The local package itself is absent from PyPI and therefore excluded by pip-audit.
Baseline receipts were retained under `/tmp/blueprint-phase5-baseline-*`. The three supplied briefs
were read together; `../blueprint-ai-tmp/` remained read-only and was not copied into this repository.

## Converged architecture and empirical corrections

A shared `ProjectGraph` contains components, dependency declarations, manifest evidence, workspace
edges, scopes, lifecycle, API/generation/module/Compose relationships, and verification nodes.
Discovery derives legacy `ProjectFacts` for compatibility. Genesis plans use the same component
models and rediscover generated projects before review. Hash-matching genesis receipts contribute
generated-client relationships on later review, without granting trust or overriding roles.

The scope vocabulary covers runtime/development/test/fixture/example/generated/vendor/migration/
benchmark/fuzz/documentation, build/test/runtime images, reusable modules, and remote modules.
Roles and lifecycle distinguish applications/services/frontends/backends/APIs, libraries/CLIs,
templates, AI components, documentation projects, reusable/deployed infrastructure, and explicit
deprecation. Unknown evidence stays unknown. The graph does not evaluate Terraform expressions,
prove reachability, resolve every transitive dependency scope, or replace a compiler/build graph.

The audit's weak AI/data substring classifiers were removed. Exact dependency and AST/HCL evidence
recognizes nested frontends/backends and actual AI dependencies while excluding compiler fixtures.
Rust inline tests, fuzz/bench declarations, test clients, VCR cassettes, and reusable CI contribute
verification evidence. Ruff/module tools avoid excluded fixtures, generated files, migrations,
benchmark resources, and documented completion corpora. Go needs an owning module; TypeScript needs
a trusted local compiler. Valid `$/.github/workflows/...` references, Python name normalization,
alternate policy/license/release-note locations, placeholder environments, and known dummy tokens
no longer trigger the demonstrated generic false positives.

Dependency aggregation preserves component, scope, ecosystem, package/version, advisory aliases,
locations, and original evidence; distinct packages remain distinct. Dependency fingerprints now
include that identity, so previously recorded vulnerability baselines may require regeneration.
Reusable IaC findings are caller-context candidates. Build/test/development images do not inherit
runtime-user/health rules. Unavailable, unauthorized, prerequisite-failed, malformed, truncated, and
partial concern states remain explicit; passing an individual tool is separately recorded.

Native initializers and the existing kits/adapters remain authorities. Removed root classifiers and
shared retrieval replace duplicated heuristics. Small compatibility/topological and ownership checks
support composition without a general solver, template merge language, or evolution engine.
[Tool research](tool-research.md#phase-5-decisions--2026-09-12) classifies all serious researched
capability groups and explains why several originally suggested launch integrations were deferred.

## Real generated-project matrix

All common paths ran without model access. Native initialization, real dependency installation,
format/lint/type checks, behavior/contract tests, builds/package checks, optional scanners, and final
Blueprint AI review ran against clean temporary outputs. No generated fixture was manually repaired;
recipe fixes were followed by clean regeneration.

| Path | Final result | Wall seconds | Verification operations |
| --- | --- | ---: | ---: |
| Generic repository | verified | 0.4 | 1 |
| Python library | verified | 67.1 | 10 |
| Python CLI | verified | 64.2 | 10 |
| Python API | verified | 75.0 | 11 |
| TypeScript library | verified | 48.1 | 7 |
| TypeScript CLI | verified | 68.0 | 7 |
| Node API | verified | 85.2 | 8 |
| React/Vite | verified | 80.2 | 7 |
| React + Python API + generated types + CI | verified | 201.4 | 18 |
| OpenAPI contract | verified | 0.4 | 2 |
| Python API + CI + service image + Dev Container | verified | 180.7 | 13 |
| React + Node API + generated types + CI + both images | verified | 187.0 | 17 |

“Operations” includes initialization and strengthening as well as verification. These timings include
cold dependency/scanner downloads and concurrent validation load. A later change shares Trivy's
external database within each process, serializing access; no persistent target cache is created.
Core results are in `/tmp/blueprint-phase5-matrix-final`; image variants in
`/tmp/blueprint-phase5-addons-final`. Earlier failures exposed Python formatting order, TypeScript 7
Node types, and an npm optional-binary installation failure. Recipes were fixed and clean reruns passed.

Validated providers: uv 0.12.13, npm 11.17.0/Node 24.19.0, create-vite 9.2.1,
openapi-typescript 7.13.0, Python 3.12.10, Docker 29.1.3. Exact fetched-provider registry integrity,
native executable/evidence hashes, source hashes, commands, and observed versions are recorded.
Current upstream Vite/ESLint configuration was retained. The generated typed frontend client is used
by the UI and tests reject incompatible/unavailable responses. CI actionlint and zizmor checks passed.

Both final service images also passed live health checks with a read-only filesystem, network `none`,
all added capabilities dropped, and non-root users (`10001` and `node`). Only those temporary test
containers were removed afterward. Their exact image digests are retained in the evidence summary.
Dev Container image builds verified the selected runtimes; editor attachment was not tested.

`verified` describes completion of declared generated-project checks, not complete review assurance.
Generated reviews retain optional-tool/semantic partials and starter improvement findings such as
missing meaningful E2E journeys or production observability. No broad suppression was added to make
starters appear production ready. The matrix also tests rollback, absent providers, malformed intent,
plan tampering, symlink/existing destinations, concurrent empty destination conflicts, dry-run purity,
provider trust, and hash-bound provenance drift. Remote arbitrary templates are not accepted.

## Pinned external regression corpus

All nine retained shallow/disposable clones were checked at their exact audited SHAs. No external
project build, test, dependency installation, executable configuration, or hook was authorized.
Before/after snapshots included ignored source/cache files outside `.git`; **9/9 stayed unchanged**.
Exact SHAs and measured contexts are committed in the evidence summary and `benchmarks/corpus.py`.

| Repository | Audited rows | Phase 5 rows | Review seconds | Main observed correction |
| --- | ---: | ---: | ---: | --- |
| fastapi/full-stack-fastapi-template | 55 | 21 | 59.1 | Nested full-stack template, API/client/tests, real runtime image scope |
| open-telemetry/opentelemetry-demo | 247 | 205 | 138.4 | 20 components, actual AI service, module roots, cassette regressions, image scopes |
| terraform-aws-modules/terraform-aws-vpc | 51 | 37 | 34.1 | 19 components, reusable module semantics, generated usage docs; remote downloads now incomplete |
| isovalent/terraform-aws-vpc | 10 | 11 | 2.2 | Reusable composition, HCL relationships, explicit unavailable verification |
| astral-sh/ruff | 1,820 | 108 | 160.7 | Rust workspace/CLI/library, inline/fuzz/bench evidence; fixture lint and timeout floods removed |
| pallets/flask | 67 | 19 | 29.8 | Library/framework plus CLI, no false AI/service role, name/license and example dependency corrections |
| open-webui/open-webui | 2,981 | 2,781 | 174.6 | Actual AI/full-stack roles, no false data pipeline, migration scope, local compiler prerequisite |
| facebook/create-react-app | 303 | 308 | 67.1 | Deprecated generator/workspace lifecycle, scope/provenance instead of ordinary app assumptions |
| sindresorhus/awesome | 7 | 4 | 31.4 | Documentation-only profile avoids application/test requirements |

Total rows: 5,541 -> 3,494. Counts are diagnostic, not accuracy scores: changed applicability and
intentional incomplete scanner states also affect them. In particular, remote Terraform-module
resolution is not relabeled as a pass. Open WebUI retains substantial actual lint output; CRA retains
legacy dependency/ShellCheck findings. Some Flask documentation dummy secrets remain Gitleaks
candidates, and transitive development/runtime scope and reachability remain incomplete. Heuristic
container/lifecycle inference, external workflow contents, organization-level policies, and broad
semantic test/AI-system completeness still need project-specific evidence. Native prerequisite/tool
availability and large truncated OSV evidence remain partial. Lychee can time out on public links.

The full corpus pass is under `/tmp/blueprint-phase5-corpus-final`; Flask was rerun after the final
CLI/library correction under `/tmp/blueprint-phase5-corpus-flask-final`. Corpus data was not vendored.

## Context, model budgets, and performance

The audit's six medium/large repositories received facts-only contexts. In Phase 5, all 54 measured
contexts at a 2,000 estimated-token ceiling contain 4–8 real file snippets plus a map, with six
distinct context hashes per repository. Shared reads across the six concerns total 7–34 files per
repository, 203 across the corpus. Ruff now reads 33 files for those six contexts, versus 3,000 in the
audit. Hash differences alone are not quality evidence; source presence, scope, and selected test/API
files were inspected. These are deterministic retrieval measurements, not live model quality claims.

A separate real Open WebUI inventory exercise used a deterministic metering provider, a 12,000-token
context budget, an oversized per-blueprint override, and a two-call cap. It made exactly two simulated
calls, skipped four concerns, used 19,198 characters per request (9,600 estimated context input tokens
total), and bounded each output at 1,200 tokens. Both requests contained source; 18 unique files were
read across them. **Live model calls: 0; actual model tokens/cost: unmeasured.** Estimates omit system,
JSON-schema, and transport overhead and are not a billing cap. Malformed responses get no silent repair
call. Prompt version, context hashes, usage when available, source identity, and cache state are reported.

| Synthetic Python files | Discovery median ms | Context median ms | Traced peak MiB | Files read |
| --- | ---: | ---: | ---: | ---: |
| 100 | 155.7 | 59.9 | 2.1 | 24 |
| 1,000 | 1,058.5 | 428.7 | 3.3 | 24 |
| 5,000 | 5,055.1 | 2,036.5 | 9.0 | 24 |

Three repeats, with tracemalloc measured separately. Own-repository discovery/context medians:
503.5/63.1 ms; traced peak 4.0 MiB. External discovery ranged 0.23–7.43 seconds. The graph adds parsing
cost compared with root-level heuristics; tool/network/cold-cache costs dominate many full reviews.
The final self-review took 102.5 seconds, down from 233.4 seconds during the earlier cold repeated-cache
run; these are observed runs, not a controlled causal benchmark.

## Final gates and trust evidence

- 150 tests pass; 79.3% statement coverage (3,473/4,378 statements). Real native-provider subprocess
  matrix coverage is not folded into this unit-test number; the lower percentage than baseline is
  disclosed rather than masked with implementation-mirroring tests.
- Ruff lint/format, mypy across source/tests/benchmarks, pip-audit, wheel/sdist build, and fresh-wheel
  CLI/schema/repository/OpenAPI generation checks pass. The local package itself is unauditable on
  PyPI; all resolved audited dependencies had no known vulnerabilities at the check time.
- Final trusted no-model production self-review: zero P0/P1/P2; eight P3 large-function observations.
  They remain visible as maintainability debt in the orchestration/parsing code. Ruff, mypy, pytest,
  Gitleaks, OSV-Scanner, Trivy, actionlint, zizmor, and Lychee passed. markdownlint-cli2 was unavailable;
  Hadolint was unavailable for generated image reviews. Missing/semantic coverage stays partial.
- A live Lychee 0.24.2 test made zero requests to a temporary loopback listener despite a target config
  attempting to disable private exclusions. Config, scheme, and all-private flags were preserved.
  Automatic approval review rejected a proposed live metadata-address test because a failed guard
  could contact a sensitive endpoint; the safer loopback-only test was used. No metadata request ran.
- External temporary caches and no-cache flags preserve read-only targets. Trivy remote-module
  downloads remain unsupported without a bounded egress boundary. Native providers and third-party
  tools execute under the OS account; filesystem staging/controlled environments are not an OS sandbox.

## Independent verification

From a fresh checkout with Python 3.12, uv, Node 24/npm 11, and optional local Docker installed:

```bash
uv sync --extra dev --extra model --locked
uv run ruff check src tests benchmarks
uv run ruff format --check src tests benchmarks
uv run mypy .
uv run pytest -W error::DeprecationWarning --cov=blueprint_ai --cov-report=term-missing
uv run pip-audit --progress-spinner off
uv build
uv run blueprint-ai schema intent
uv run blueprint-ai schema genesis-plan
uv run blueprint-ai schema project-graph
uv run blueprint-ai review . --profile production --no-model --trust-project-executables --fail-on P1 --format json
PYTHONPATH=src uv run python benchmarks/run.py
PYTHONPATH=src uv run python benchmarks/genesis_matrix.py --output /tmp/blueprint-genesis-independent --network --add-ons
PYTHONPATH=src uv run python benchmarks/corpus.py --output /tmp/blueprint-corpus-independent --review
uv venv /tmp/blueprint-wheel-independent
uv pip install --python /tmp/blueprint-wheel-independent/bin/python dist/blueprint_ai-0.5.0-py3-none-any.whl
/tmp/blueprint-wheel-independent/bin/blueprint-ai init /tmp/blueprint-openapi-independent --kind openapi --no-model
```

Use unused output paths. External corpus checks do not execute target code; the generated matrix does
execute native publishers and generated projects and requires network. Availability/network-sensitive
results may differ and must remain explicit. OS/tool/registry versions, source hashes, exact SHAs, and
lockfiles are the reproduction evidence; neither mutable dependency ranges nor container tags imply
bit-for-bit reproducibility. Tagging and publishing remain separate release actions.

## 0.5.0 release finalization

The release pass fetched `origin` and reconfirmed clean `codex/phase-5-project-genesis` at
`fd30f8004678ad0e9b50b646bd50dcf1f4f49a09`. Local and remote `main` still pointed to the original
`b3c5fc673a0741bdeec29eedc13b3485c564ad02` base, so no reconciliation was necessary. All six historical
annotated tag objects and commit targets were retained unchanged. The release tag is **`0.5.0`**.

The final engineering review found and fixed three concrete release issues in `0793e15`:

- Malformed script/workspace declarations and Compose services now retain explicit incomplete
  inventory diagnostics instead of crashing discovery. Regression cases also verify unaffected
  components and read-only behavior.
- The genesis-plan schema now describes the complete dry-run output, including its checksum.
  The CLI preview is checked against that schema, including rejection of invalid checksums.
- The optional MIT template leaves year and copyright holder for the project owner to complete.
  It no longer assigns the tool author's identity to generated projects.

Fresh validation passes **160 tests with deprecations treated as errors**, with **79.8% statement
coverage (3,506/4,394 statements)**. Ruff format/lint, mypy over source/tests/benchmarks, dependency
audit, wheel/sdist builds, CLI version/help/doctor/inspect, all six schemas, and dry-run schema
validation pass. Version metadata and the CLI consistently report `0.5.0`. Package archives contain
no corpus clones, environments, bytecode, or unintended build artifacts. The local package remains
excluded from the PyPI vulnerability lookup; all audited resolved dependencies pass.

The trusted no-model candidate self-review passes with zero P0/P1/P2 findings and nine P3
large-function observations. Ruff, mypy, pytest, Gitleaks, OSV-Scanner, Trivy, actionlint, zizmor, and
Lychee pass; markdownlint-cli2 remains unavailable and semantic coverage remains partial. A fresh
isolated wheel install passes version/help/doctor/inspect/schema, OpenAPI genesis, and no-model
review checks. Its analyzer source hash matches the candidate:
`a7623c451aa52f8f64974c69b03e21f596dd10773a73de5882b5acd9d46e0e84`.

Gitleaks initially mistook the
pinned public FastAPI commit ID in `benchmarks/corpus.py` for an API key after that file entered Git
history. The ID was checked against the retained source clone. `.gitleaksignore` excludes only the
exact commit/file/rule/line fingerprint of that historical occurrence; no rule or whole file is
disabled, and future revisions remain scanned. The history scan passes with this documented
false-positive exception.

All twelve native-provider matrix cases were freshly regenerated and verified. Both service images
again returned HTTP 200 with the expected health body as non-root users, with network disabled,
read-only filesystems, dropped capabilities, and no-new-privileges. The two image identities match
the implementation evidence above. The release corrections do not change those default generated
assets or provider commands; the separate MIT path and preview contract have regression coverage.

The full pinned corpus rerun retains **3,494 findings** and **9/9 unchanged target snapshots**.
Discovery and context selection were rerun after the release fixes: all component/type inventories
and all **54 context hashes** are unchanged. Every context still contains 4–8 source snippets,
with 203 shared file reads across the corpus. OpenTelemetry's previously silent unsupported Compose
inventory now has a diagnostic; its affected full review was rerun and retains 205 findings with
explicitly partial concern results. Other corpus graphs are unchanged. This is a regression result,
not a claim that every finding is correct or every concern is fully assessed.

Fresh final-source benchmark medians (three repeats; memory traced separately):

| Synthetic Python files | Discovery ms | Context ms | Peak MiB | Files read |
| --- | ---: | ---: | ---: | ---: |
| 100 | 128.7 | 64.9 | 2.1 | 24 |
| 1,000 | 848.8 | 494.3 | 3.3 | 24 |
| 5,000 | 4,363.5 | 2,772.0 | 9.0 | 24 |

These runs overlapped other validation work and are not controlled performance comparisons. No live
model calls were made. Previously documented optional-tool, semantic, cloud, template/publication,
migration, and OS-isolation limitations remain in force.

The release process merges the verified branch with the repository's existing merge-commit
convention, reruns the smoke gate from the exact resulting `main` commit, and only then creates the
annotated `0.5.0` tag. The tag's peeled commit identifies the release; historical tags are immutable.
No package-registry publication is part of this release. See the concise
[0.5.0 release notes](release-notes-0.5.0.md).
