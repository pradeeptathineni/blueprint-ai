# Phase 6 validation and release decision

**Historical candidate report.** The independent [release red team](phase-6-redteam.md) found
and corrected defects after `6c376b9`. Its final gate supersedes the readiness decision below.
The never-published local `0.6.0` tag remains untouched on that original candidate and is stale.

Prepared 2026-09-12. Recommended release: **0.6.0, ready for the documented local creation,
strengthening, review, and isolated-execution scope** with the final gate recorded below.
The release is prepared locally on `codex/phase-6-ecosystem-sandbox`. No remote push, GitHub Release,
package upload, cloud provisioning, hosted CI run, or live-model certification is included.
The [machine-readable evidence](phase-6-evidence.json) retains exact identities and outcomes;
[releasing](releasing.md) provides independent commands.

## Baseline and scope

Started from clean released `0.5.0`, commit `60e0c41732e513d54353933c3e2882b54161e340`.
Its annotated tag object was `8a1b4958c8b7987373ebe6aadeeed14273713c3c`; local and remote tags
matched. The 160-test baseline passed. The Phase 5 report, genesis/tool/threat/extension documents,
public-corpus evidence, provider/kit contracts, and locally available research supplement were read
before broadening implementation. Earlier tags remain unchanged.

The [gap inventory](phase-6-gaps.json) classifies 22 capability groups using all eight requested
categories, with a separate observation for every registered optional tool and deferred family.
It records deliberate outcomes rather than treating environment-dependent scanners as missing product code.
[Current research decisions](tool-research.md#phase-6-decisions--2026-09-12) link authoritative sources.

## Coverage and common contracts

One canonical support registry describes 44 family records, **31 initialized families, 52 external
tools, and 17 providers**. Plans, acquisition, adapter metadata, doctor, and generated support views
consume it. Presentation is separated from data to avoid cyclic registry imports. Shared atomic asset
writers also remove a native-family import cycle. The [generated support document](support.md) has a
regression test against CLI output; recognition, generation, native verification, and maturity are
separate claims.

Initialization covers Python and TypeScript libraries/CLIs/services, React/Vite, Vue, Svelte, Next.js,
full-stack compositions, Django, Flask, Java/Maven, Spring Boot, C#/.NET, Go, Rust, OpenAPI, and local
infrastructure. Existing-project discovery additionally covers PHP, Shell, C/C++, Kotlin, Ruby,
Swift, GraphQL, Protobuf, workspace/configuration evidence, and recognized deferred frameworks.
Maven and NuGet XML are bounded and reject DTD/entities; Pulumi runtime/cloud evidence does not invent
an application role from `package.json` alone.

GitHub Actions is the generated CI default. GitLab CI is recognized but has no generated workflow or
hosted lint integration. Native CI compositions exist for the documented Python/Node/Go/Rust/.NET/
Django/Flask/Vue/Svelte paths; Java, Next.js and infrastructure CI requests are explicitly deferred.
No claim of universal workspace-task-engine generation, PHP application generation, or production
readiness is made.

## Tool completeness and hardening

**26 tool entries have explicit managed OCI acquisition**; the remaining 26 have official or locked
project-native acquisition guidance and precise unavailable behavior. Shared toolchains reuse provider
images/receipts. Installation is explicit, plan-first, and never changes global host packages.
Registered image builds retain the base registry digest, recipe hash, selected package versions, and
resulting immutable local image ID. Runtime checks reject missing/deleted images and poisoned receipts.
Upgrades require an explicit acquisition and renewed native verification. Upstream platform metadata
is not a cross-platform certification.

Configured Conftest policy, ast-grep structural rules and Buf Protobuf lint complement existing native
compiler/linter/SAST routes. Strict parsers preserve clean/finding/error distinctions, including Buf's
exit 100 and JSONL diagnostics. Syft records SBOM format, package count and output digest, without
inventing vulnerability findings. Semgrep uses local rules with telemetry/version checks disabled and
bounded jobs/memory. Configured ast-grep/Semgrep and alternative vulnerability/Kubernetes scanners
avoid a blanket duplicate-scanner bundle. Existing schema, dependency-scope, architecture and native
verification logic remains deterministic.

## Sandbox boundary and evidence

Docker, Linux Podman, and configured Docker/runsc use one policy contract. Auto-selection prefers
Podman, then runsc, then Docker for untrusted work. Host execution requires explicit trust and reports
that filesystem/network/resource isolation is absent. There is no untrusted host fallback or implicit
image download. Missing engines, unsupported resource controllers or image volumes, malformed metadata,
and failed teardown are explicit incomplete execution states.

Default OCI policy uses a non-root UID, read-only root and target, isolated executable scratch,
dropped capabilities, no-new-privileges, private IPC, no host credentials/socket/SSH agent, and a neutral
Docker client configuration that cannot inherit host credential/proxy settings. Synthetic account
records provide the numeric UID identity without exposing host account databases. Defaults are 120 s,
2 CPU quota, 1 GiB RAM without additional swap, 128 PIDs, 256 MiB scratch, a 128 MiB individual-file
limit and 4 MB captured output per stream. A rejected container startup cannot claim isolation ran.

`none`/`loopback` prohibit egress through a private network namespace. Both may have internal loopback.
An unenforceable destination allowlist is rejected. Normal networking requires explicit trust and
network authorization, and then permits private destinations. Writable generation uses a disposable
stage; native dependency acquisition is authorized separately. There is no total writable-stage quota.

Live checks used Docker Desktop 29.1.3, Linux amd64 containers, on macOS 26.6.2; the daemon was not
rootless. They exercised target write denial, host-path/symlink and synthetic credential isolation,
absence of the daemon socket, blocked host loopback/private/metadata/public egress, scratch/file limits,
wall timeout, output truncation, 64 MiB OOM termination, PID exhaustion and teardown. CPU quota is
checked in policy and runtime capability contracts; no independent CPU throughput guarantee is claimed.
These are empirical tests, not formal isolation proof. Podman/runsc have common policy-contract tests
but no live backend certification here. Native Windows named pipes and remote engines are unsupported.
Docker Desktop bind sharing of the development Documents path stalled; disposable shared temporary
paths were used for real execution. The boundary and limitations are documented in [sandbox](sandbox.md).

## Generated-project and composition evidence

All 31 families have a verified native representative. Across development and converged reruns,
**46 distinct exercised cases** cover these families, all nine Terraform/OpenTofu/Pulumi cloud pairs,
two full-stack backend/client/CI combinations, six additional native CI compositions, and two trusted
service-image/Dev Container compositions. This is curated covering coverage, not a mathematical proof
of every pair or permutation. Every retained success was checked against its published source hashes;
no generated `.git`, dependency tree, or build cache was published. Operation receipts bind commands,
provider/image identities, source hashes, plan identity and analyzer source identity.

| Family | Native result | Seconds | Operations |
| --- | --- | ---: | ---: |
| repository | verified | 2.4 | 1 |
| python-library | verified | 40.1 | 10 |
| python-cli | verified | 41.3 | 10 |
| python-api | verified | 38.9 | 11 |
| typescript-library | verified | 27.1 | 7 |
| typescript-cli | verified | 29.3 | 7 |
| node-api | verified | 35.5 | 8 |
| react | verified | 50.1 | 7 |
| full-stack | verified | 103.1 | 18 |
| openapi | verified | 3.0 | 2 |
| go-library | verified | 40.4 | 6 |
| go-cli | verified | 38.2 | 6 |
| go-api | verified | 66.6 | 6 |
| rust-library | verified | 9.3 | 6 |
| rust-cli | verified | 10.7 | 6 |
| csharp-library | verified | 45.4 | 7 |
| csharp-cli | verified | 67.3 | 7 |
| dotnet-api | verified | 58.3 | 7 |
| vue | verified | 51.3 | 5 |
| svelte | verified | 50.6 | 5 |
| django | verified | 48.0 | 8 |
| flask | verified | 30.5 | 7 |
| terraform | verified | 91.8 | 4 |
| opentofu | verified | 69.2 | 4 |
| kubernetes | verified | 1.5 | 2 |
| helm | verified | 5.2 | 4 |
| kustomize | verified | 3.0 | 2 |
| java-library | verified | 49.3 | 3 |
| spring-boot | verified | 73.6 | 3 |
| nextjs | verified | 166.4 | 6 |
| pulumi | verified | 83.9 | 4 |

The full-stack representative includes the Python backend, generated TypeScript client and CI; the
Node equivalent also passed. Operation counts include initialization/strengthening. Reported times
come from generation receipts and include differing cache/network/concurrency conditions. Matrix
records span implementation iterations; the final representative rerun rechecked Python API, Go API,
Rust CLI, Spring Boot and Next.js. Earlier failed attempts are retained and superseded by clean reruns.

Native behavior checks include greeting/CLI edge cases, Go HTTP health/404, .NET WebApplicationFactory
HTTP tests, Spring random-port HTTP health/404, Django/Flask request tests, Vue/Svelte server rendering,
Next.js build/lint/Vitest, and full-stack client/contract use. Existing React, Python and TypeScript
build/type/lint/tests remain. Terraform/OpenTofu perform format, backend-disabled initialization and
validation; Pulumi generates a TypeScript program and validates cloud dependencies/types. Kubernetes
has a built-in Namespace structural check; Helm lint/template and Kustomize build use their native tools.
No live cluster, resource deployment, infrastructure state backend, or remote plan is created.

Real runs exposed and corrected Go working-directory behavior, Rust scratch execution, Vite template
changes, numeric-user assumptions in Next/Pulumi, Next's child-directory initialization, large AWS
provider file limits, .NET cache/lock behavior, and source-only final review. Next build-cache dummy
secrets are no longer reviewed as published source. Initializr downloads are bounded and fully
preflighted for traversal, symlinks, special files, duplicate paths, UTF-8 and archive-size bombs.

The two trusted host add-on runs passed service and development image builds (51.5 and 94.3 seconds).
Their native image identities are retained. Strict OCI never mounts the Docker socket and therefore
reports nested image-build verification as unavailable. Browser journeys, editor attachment and remote
CI execution were not exercised in this phase. `verified` means declared native starter checks passed;
optional scanner and semantic review assurance remain separately partial.

## Existing-project enablement

`add` reuses the existing create-only mutation engine. All 16 kits passed plan/conflict/idempotence/
apply/exact-tree rollback tests, including restoration of newly created directories. Failed verification
rolls back owned unchanged files; drift is preserved and reported. Structural JSON/YAML/TOML/Python
validation precedes optional native verification. Missing external verification remains partial.

The kits cover Python/JavaScript smoke tests, pre-commit, GitHub CI, Terraform checks, container ignore
policy, API lint configuration, observability notes, OSS/security policy, AI context, dependency updates,
secret scanning, SAST policy, Dev Container configuration and release guidance. These are the exact
files in the generated support matrix, not a promise to merge arbitrary existing manifests or create
complete application-specific test/observability/release systems. No second mutation engine was added.

## Unit, hostile, native and package gates

- **256 tests passed**, including one live test with five hostile OCI process executions. Statement
  coverage is **78.8%** (the precise final numerator/denominator is in the evidence), compared with
  the released Phase 5 final 79.8%. Native matrix processes are not included in this coverage number.
- Deterministic parameter cases cover every family and kit, compatibility, naming, graph/lifecycle,
  parsers, provenance, strict policy bounds and acquisition plans. Hostile fixtures cover traversal,
  symlinks, FIFOs, huge/binary inputs, archives, malicious config/scripts/URLs, prompt injection,
  malformed/truncated output, hangs, cache poisoning, transaction drift and Git edge cases. This is
  parameterized boundary coverage; no Hypothesis-based property suite was added.
- **Eight live clean/bad/repaired scanner fixtures passed:** Ruff, Hadolint, markdownlint-cli2,
  Conftest, ast-grep, Buf, Gitleaks and Semgrep. Each repaired fixture returned clean and retained its
  source snapshot. Syft independently produced a valid two-package SBOM with zero invented findings.
- Lint, formatting, mypy, dependency audit, wheel/sdist build, generated support and documentation
  checks form the final gate. `pip-audit` found no known vulnerabilities in resolved dependencies;
  the unpublished local package itself is absent from PyPI and cannot be audited there.
- Fresh installed-wheel CLI checks passed on macOS Python 3.12.10 and Linux Python 3.12/3.13/3.14;
  a source-distribution install passed on macOS. Each runs 21 CLI checks. Minimal Linux images have no
  Git, which exposed and fixed a real built-in-generation dependency. The smoke harness also proves
  explicit sandbox unavailability without a runtime, read-only offline/no-model review, and exact
  add/rollback. Final artifacts are rebuilt and checked again before local tagging; `dist/validation.json` and
  `dist/SHA256SUMS` bind those last install checks to the delivered files.

Final production self-reviews in strict Docker and explicitly trusted host mode have **zero
P0/P1/P2 findings** and retain **12 P3 large-function observations** as maintainability debt.
Docker Ruff/Gitleaks/markdownlint passed; the host run passed Ruff, mypy, pytest, Gitleaks,
OSV-Scanner, Trivy, actionlint, zizmor and Lychee. Host markdownlint was unavailable and was verified
separately in Docker. Other unavailable OCI tool images and semantic concerns remain partial.
The host run took 120.7 seconds; the complete tool outcomes are retained in the evidence.

## Pinned public-repository regression

All nine pinned repositories stayed byte-for-byte unchanged outside `.git` after review. Target code
was never implicitly executed on the host. Available native checks used read-only OCI boundaries;
missing toolchains/dependencies and database/network coverage stayed partial. The exact SHAs and
54 selected context identities are in the evidence and `benchmarks/corpus.py`.

| Repository | Components | Findings | Review seconds | Source unchanged |
| --- | ---: | ---: | ---: | --- |
| fastapi/full-stack-fastapi-template | 4 | 28 | 6.8 | yes |
| open-telemetry/opentelemetry-demo | 23 | 206 | 134.5 | yes |
| terraform-aws-modules/terraform-aws-vpc | 19 | 49 | 8.9 | yes |
| isovalent/terraform-aws-vpc | 1 | 13 | 2.7 | yes |
| astral-sh/ruff | 105 | 88 | 178.0 | yes |
| pallets/flask | 4 | 20 | 9.1 | yes |
| open-webui/open-webui | 2 | 2472 | 84.4 | yes |
| facebook/create-react-app | 26 | 33 | 29.8 | yes |
| sindresorhus/awesome | 1 | 19 | 3.2 | yes |

Findings are diagnostics, not an accuracy score. Native availability changed from Phase 5, so fewer
rows do not alone establish improvement. Discovery retains the intended reusable-IaC, deprecated
CRA, documentation-only awesome, Flask library/CLI, and Open WebUI AI/full-stack distinctions.
Maven/.NET manifest evidence improves the polyglot inventory. Ruff's 105 components include its
scoped fixtures; it is not recast as 105 production applications. Existing optional scanner and
project-specific semantic limits remain explicit.

## Model boundaries and measured performance

No live credentials were configured: **live calls 0; live model tokens/cost and incremental semantic
quality unmeasured**. Bounded provider, cache, malformed-response and context tests remain; semantic
architecture/testing/documentation recommendations retain the Phase 5 evidence path. This phase does
not add speculative model calls for native facts or unvalidated natural-language generation.

Six concern contexts per corpus repository produced six distinct hashes, with bounded real source
snippets and shared reads (7–34 files per repository). Repeated synthetic context construction has a
1.0 observed cache-hit rate. Token figures below are deterministic estimates, not billing totals.

| Synthetic Python files | Discovery median ms | Context median ms | Traced peak MiB | Context reads | Estimated input tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 137.7 | 66.8 | 2.06 | 24 | 1035 |
| 1,000 | 922.2 | 479.3 | 3.29 | 24 | 1048 |
| 5,000 | 4119.1 | 2393.5 | 9.04 | 24 | 1060 |

Three repeats per size, with memory traced separately. Stable regression guards cap context reads at
24, estimated tokens at 12,000 and traced peak at 64 MiB. Every synthetic run made zero model calls.
Observed final 5,000-file discovery/context was 4.12/2.39 s. Timings varied across concurrent runs;
there is no controlled speedup claim over Phase 5. Public-corpus discovery ranged 0.22–7 seconds and
native review reached 178 seconds with many components and optional tools.

Five fixed no-op startup samples measured a host median of **41.4 ms** and Docker median of
**594.7 ms**, about **553 ms added** including local runtime/image checks and teardown. The host and
container Python environments differ, so this is an operational overhead sample, not a pure kernel
comparison. Tool startup and repeated container calls dominate small checks; broader optimization is
not justified without stable warm/cold workload comparisons.

## Documentation, release and remaining limits

README contains installation and brief usage; separate genesis, capabilities, tools, sandbox, threat,
profiles, providers, testing, extending and releasing documents describe behavior. Support tables are
generated; this phase report and evidence remain historical artifacts. Version, lockfile and changelog
are 0.6.0. Wheel/source artifacts and checksums are prepared in `dist/`; annotated local tag `0.6.0`
identifies the final release commit. Resolve it with `git rev-parse '0.6.0^{commit}'`. Gate records
collected before committing retain their baseline HEAD plus independent analyzer source hash.

The following are explicit boundaries: Angular/Axum/PHP and task-engine generation; GraphQL/AsyncAPI
and consumer-contract/server/breaking workflows beyond the documented lint/inventory paths; native
CDK/SAM/Bicep/azd/GCP-managed/Crossplane deployment; GitLab generation; application-specific browser,
mutation/load/resilience/fuzz suites; arbitrary manifest merging/codemods; and deployment/signing/
registry account configuration. Database-backed scanners and missing external tools need operator
prerequisites. Native Windows, live Podman/runsc, live model quality, remote CI and cloud provisioning
are not certified. Containers share a Linux kernel unless configured runsc is selected.

Independent source, live OCI, native matrix, corpus, package and artifact verification commands are
maintained in [releasing](releasing.md). OIDC/Trusted Publishing was researched and documented; no
stored publishing token or automatic upload workflow was introduced. Public publication remains a
separate explicitly authorized action.
