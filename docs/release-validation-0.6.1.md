# Blueprint AI 0.6.1 maintenance validation

Prepared 2026-09-12 from released `0.6.0` commit
`5e19dac63b7d24fc36d1ec9c1a29425adafffafd`. This report records the maintenance delta; the
[independent Phase 6 audit](phase-6-redteam.md) remains the broader release baseline.

## Hosted CI incident

The reported missing `0.6.0` run was a stale timing/visibility observation, not a trigger failure.
GitHub's public Actions API records successful push run
[`34704439936`](https://github.com/pradeeptathineni/blueprint-ai/actions/runs/34704439936)
for the exact release commit. It started at `2026-09-12T16:10:50Z`, completed at
`2026-09-12T16:12:36Z`, and all three Python quality jobs plus the sandbox job succeeded.

The workflow now makes the intended contract explicit and regression-tested: pull requests, pushes
to `main`, no-`v` semantic release tags, and manual dispatch run CI; no path filters can silently
exclude a release change. Repository permissions are read-only, concurrency is scoped by ref, and
tag runs are not canceled. Quality/build/audit/SBOM work runs once, compatibility tests run on Python
3.12–3.14, live Linux sandbox tests have a dedicated job, and Windows has a native installed-wheel
job. Actionlint and Zizmor accept the final workflow. The earlier `setup-uv` cache setting was removed
after Zizmor identified a cache-poisoning risk and because it also caused a harmless matrix cache-key
collision. GitHub Actions and branch-protection settings were not readable anonymously; repository
settings remain an administrator-owned control outside the committed workflow.

## Model provider

OpenAI remains a bounded semantic-review provider using the
[Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
strict Structured Outputs, `store=False`, explicit output-token limits, and zero SDK retries. Provider, model,
reasoning effort, and timeout are operator-owned environment settings. Reports preserve input,
output, cached-input, and reasoning tokens when supplied. Controlled providers verify structured
success, malformed and failed response degradation, budgets, caching, redaction, and prompt-injection
isolation. No `OPENAI_API_KEY` was available, so no live request, latency, token, cache, or incremental
quality claim is made. Deterministic/no-model behavior remains the proven comparison and makes zero
calls.

Coding agents remain a separate future boundary: Blueprint AI decides or validates, an explicitly
selected agent may implement, and Blueprint AI verifies. No agent backend or orchestration was added.

## Sandbox evidence

Docker Desktop 29.1.3 (`linux/amd64`) passed the live hostile policy suite with:

- `python:3.12-slim-bookworm` image ID
  `sha256:59bf1d95c965f12dfc14afaf5af778fc1dbe5b372bd5c281645fc34d4c75d4e7`,
  repository digest `sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254`;
- `node:24-bookworm-slim` image ID
  `sha256:1cc81b664ff520c96dde07893e717ff070f9f7bd8bbcec50e10fa2f3cb5c4b1c`,
  repository digest `sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553`.

The suite exercises a read-only untrusted target, writable trusted stage, environment and symlink
isolation, no-network policy, internal versus host loopback, public/private/metadata denial, explicitly
authorized networking, CPU/PID/memory/time/output limits, 16 MiB scratch capacity, 1 MiB individual
file limit, detached-process teardown, FIFO timeout, and npm lifecycle suppression.

Network evidence now uses `none` and explicitly authorized `unrestricted`. `restricted` and
`allowlist` fail closed because the local Docker/Podman adapters cannot guarantee destination
filtering without deployment-owned firewall policy. This avoids presenting application filtering as
an OCI network boundary.

Scratch tmpfs and individual-file limits are enforced and recorded. A read-only target records an
enforced zero-byte writable-workspace capacity. Docker and Podman have no portable total-size quota
for a persistent writable host bind mount; such execution records the workspace limit as unset and
unenforced. Docker's
[container storage limit](https://docs.docker.com/reference/cli/docker/container/run/#set-storage-driver-options-per-container)
does not change that bind-mount property, while a
[bounded tmpfs](https://docs.docker.com/engine/storage/tmpfs/) is deliberately ephemeral.

Podman and runsc were not installed. Contract and failure-path tests remain, but no live claim is
made. Docker had no runsc runtime configured. Installing a local VM or changing host firewall/runtime
configuration solely to manufacture coverage was disproportionate for this patch.

## Platforms and workflows

The local macOS gate passes source checks, the installed-package smoke workflow, live Docker policy,
and real Docker-backed generation. The retained generation matrix at
`/tmp/blueprint-061-matrix.OUz2Ef/generated` reports:

| Project | Result | Operations | Inspected verification |
| --- | --- | ---: | --- |
| Python API | verified | 11 | sync, format, lint, types, tests, build |
| React | verified | 7 | locked install, lint, build, test, pack |
| Terraform | verified | 4 | format, provider init, JSON validation |

Each command receipt includes the exact image ID, limits, network decision, exit state, duration, and
teardown. Generated source, tests, lockfiles, manifests, and review reports were inspected. The
installed-package smoke workflow covers version/help, doctor, schemas, tool planning, discovery,
naming, deterministic genesis, no-model review, capability plan/apply, and exact rollback.

Native Windows evidence is supplied only by the final `windows-latest` hosted job, which installs the
built wheel into a fresh environment and runs representative CLI/config/provider tests plus the same
portable smoke workflow. Release finalization requires that job to pass for the exact tagged commit.
Existing Phase 6 Playwright evidence remains applicable because this patch does not change generated
browser behavior; exhaustive browser reruns would add little value.

## Tool registry and maintainability

The canonical registry still contains exactly 52 tool IDs. Every entry now exposes a derived
classification alongside its role, maturity, acquisition route, version policy, source/license, OCI
image where applicable, and dynamic availability. Current registry counts are 26 managed OCI, 24
safely acquirable, and 2 deliberately deferred (`php-lint` and `kubescape`). No entry currently
requires the platform-constrained, experimental, or superseded/rejected state.

The released source retained 12 P3 large-function heuristics. An initially introduced thirteenth
case, `sandbox.execute`, was decomposed into evidence, lifecycle, and OCI execution helpers. The
remaining 12 predate this patch:

| Disposition | Functions | Decision |
| --- | --- | --- |
| Registry/decision tables | `applicable_adapters`, `ci_checks`, `_generation_evidence`, `_operations`, `strengthen_family`, `plan_project` | Cohesive declarative branching; splitting would obscure the canonical decision surface. |
| Cohesive orchestration | `strengthen`, `review` | Ordered pipelines with tested stage boundaries; retain. |
| Maintainability debt | adapter `run`, `_manifest`, `build_graph`, `create_project` | Real decomposition candidates, but broad refactoring would increase release risk without addressing a maintenance defect in this patch. |

The final self-review has 13 P3 items: these 12 size observations and one missing-build-cache
heuristic. Cache restoration is rejected for this workflow because the concrete Zizmor
cache-poisoning P1 outweighs a generic performance suggestion. Security/action workflow review is
free of P1/P2 findings.

## Final gate and limitations

The local final gate passed Ruff format/lint, Mypy, 387 Pytest cases with 82% coverage, dependency
audit, all seven JSON schemas, generated support-document parity, wheel and source distribution
builds, separate fresh installation of each artifact, representative generation, live sandbox cases,
22-step release smoke, Actionlint, Zizmor, and Blueprint AI's own deterministic/tool review. The
dependency audit found no known vulnerabilities and skipped only the unpublished local
`blueprint-ai` distribution. Artifacts are retained in `/tmp/blueprint-061-artifacts.AnGvrA`:

- wheel SHA-256: `77a8864a18629bf4f89e516c4766bdf073d563c8473fab98e21f44ca77cada46`;
- source SHA-256: `85ba025b00c451f352bac6d5208dd530fef762554a4d9f2dbff732d0c0ce31f9`.

Merge/tag identities and hosted run URLs belong to the final release report so recording them cannot
itself change the commit being verified.

Remaining limitations are explicit: no live model quality result; no live Podman/runsc result; no
portable total writable bind-mount quota; no destination-filtered OCI network mode; and no native
Windows OCI or POSIX descriptor-race claim. No package is published to PyPI by this release process.
