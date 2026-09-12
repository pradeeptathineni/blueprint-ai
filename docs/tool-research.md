# Tool research

Snapshot: 2026-09-12. This refresh uses upstream documentation and repositories rather than
memory. The actively maintained [static-analysis](https://github.com/analysis-tools-dev/static-analysis)
and [dynamic-analysis](https://github.com/analysis-tools-dev/dynamic-analysis) catalogs were checked
again for language/ecosystem gaps. Their stale/archive markers are useful discovery evidence, but a
catalog entry alone is not sufficient for integration.

Selection weights maintenance, meaningful adoption, OSI license, machine-readable output, fully
local execution, CI suitability, startup/runtime cost, macOS/Linux/Windows availability, overlap,
configuration burden, and false-positive behavior. Tools remain optional CLIs; none of their code is
redistributed. Blueprint AI never silently downloads a tool.

## Phase 7 transformation refresh — 2026-09-12

Migration selection was refreshed from primary vendor/project sources. Catalog metadata is canonical;
this table records why the 0.7.0 executable boundary is intentionally narrower than discovery.

| Ecosystem | Decision | Evidence and boundary |
| --- | --- | --- |
| Python syntax | wrap [Ruff UP](https://docs.astral.sh/ruff/rules/#pyupgrade-up) | Stable automatic fixes can be previewed, scoped, parsed, rerun for idempotency, and bound to `project.requires-python`. 0.7.0 runs Ruff isolated from repository lint policy. |
| Python packaging | partial adapt from [uv's pip-to-project guide](https://docs.astral.sh/uv/guides/migration/pip-to-project/) | Indexes, requirement groups, editable/path sources, build metadata, and application versus package intent cannot be safely inferred from a requirements file alone. |
| Go | wrap native [Go 1.26 `go fix`](https://go.dev/blog/gofix) | The version-gated modernizers expose `-diff`, skip generated files, and preserve compiler ownership. Apply is limited to selected package directories and followed by offline `go test ./...`. |
| Rust | partial adapt from [Cargo `fix --edition`](https://doc.rust-lang.org/cargo/commands/cargo-fix.html) | Cargo supplies compiler suggestions but deliberately does not update `Cargo.toml`; inactive cfg/features and manifest intent remain unresolved. |
| Java/JDK | partial wrap of [OpenRewrite runners](https://docs.openrewrite.org/running-recipes/getting-started) | Maven/Gradle execution and target JDK selection are authoritative, but exact recipes and build prerequisites need explicit selection. Core availability does not license every recipe. |
| Spring | defer under [OpenRewrite licensing](https://docs.openrewrite.org/licensing/openrewrite-licensing) | Current modules/recipes span Apache, source-available, and commercial terms. Blueprint AI neither bundles nor executes a recipe on ecosystem name alone. |
| React/CRA | partial planning from [React's CRA sunset guidance](https://react.dev/blog/2025/02/14/sunsetting-create-react-app) and [React 19 guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide) | React recommends frameworks for many apps and build tools such as Vite/Parcel/Rsbuild for others; routing, rendering, data, deployment, and dependency intent determine the destination. React-recommended codemods remain preferred once an explicit target is supported. |
| Next.js | partial wrap of [official codemods](https://nextjs.org/docs/app/guides/upgrading/codemods) | Native upgrade/codemod dry-run facilities are preferred, but interactive dependency and semantic version transitions are not yet adapted to the transaction contract. |
| .NET | defer to the [official modernization successor](https://learn.microsoft.com/en-us/dotnet/core/porting/upgrade-assistant-overview) | Upgrade Assistant is deprecated. The GitHub Copilot modernization agent is an external authorized-agent workflow, not a deterministic CLI Blueprint AI can silently substitute. |
| Terraform | wrap native [fmt](https://developer.hashicorp.com/terraform/cli/commands/fmt) only | Native HCL formatting has clear scope and idempotency. Provider/module/backend/state upgrade claims require initialization and project-specific verification, so they remain outside the supported formatter. |
| OpenTofu | defer following the [official migration guide](https://opentofu.org/docs/intro/migration/) | State backup, backend/provider compatibility, and remote-state dependency order make Terraform-to-OpenTofu a stateful platform migration, not a textual rename. |
| Kubernetes | partial wrap candidate under the [API deprecation guide](https://kubernetes.io/docs/reference/using-api/deprecation-guide/) | API conversion must target a known cluster version and can select non-ideal defaults; no automatic apply is exposed. |
| Dockerfile | native deterministic from [Docker's deprecation guidance](https://docs.docker.com/reference/build-checks/maintainer-deprecated/) | Single-line `MAINTAINER` has a direct OCI authors-label replacement. Existing labels and multiline values are rejected instead of merged or guessed. |
| GitHub Actions | native schema-aware scalar edit under [GitHub secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use) | Only reviewed `actions/*` major tags with registry full SHAs are changed. The major is preserved; unknown, third-party, expression, quoted, and reusable workflow references remain untouched. |
| Generic structural tools | experimental adapt of [ast-grep rewrite](https://ast-grep.github.io/guide/rewrite-code.html) | It is preferred over textual replacement for a reviewed project-specific JS/TS syntax rule, but Blueprint AI does not infer a universal old/new pattern. jscodeshift, LibCST, compiler APIs, and Tree-sitter remain future recipe-specific options, not a universal AST subsystem. |
| Codex | researched [App Server and exec](https://openai.com/index/unlocking-the-codex-harness/) but defer implementation | App Server is the rich bidirectional JSONL-over-stdio and approval surface; exec fits one-shot automation. No supported step has residual semantic implementation, so 0.7.0 adds neither agent authority nor an unused integration abstraction. |

Semgrep autofix is rejected as a general migration authority: it is useful only with an explicitly
reviewed rule and cannot replace framework/compiler migration semantics. Schema parsers already in the
runtime handle the two bounded config transforms, so adding a Tree-sitter, CST, or TOML/YAML rewriting
dependency without another proven recipe would not earn its cost.

The Phase 4 refresh rechecked the actively changing defaults against upstream releases and then ran
the priority tools against controlled defects. Ruff remains
the Python default and documents JSON/SARIF output. Semgrep remains on major version 1 with frequent
2026 releases. Gitleaks remains on major version 8. OSV-Scanner v2 documents
`scan source --format json`
and guarantees compatible JSON/CLI behavior within a major release. Trivy 0.72, Syft 1.51, and Grype
0.116 publish checksums plus signed bundles or release attestations. The broad compatible ranges in
`doctor` intentionally avoid claiming exact reproducibility; CI should pin an exact release and
verify the upstream checksum/attestation. `bootstrap` only reports official guidance and never pipes
an installer to a shell.

The release dependency audit found the prior pytest 8.4.2 development pin affected by
PYSEC-2026-1845. The development/test range therefore starts at 9.0.3; this is a test-only upgrade,
not a runtime dependency.

## Language authorities

| Stack | Default | Useful alternate | Decision |
| --- | --- | --- | --- |
| Python | [Ruff](https://github.com/astral-sh/ruff), configured mypy, pytest | Pyright, Pylint | Ruff is fast, cross-platform, MIT, and emits JSON/SARIF. Type checking runs only when the project configures it. pytest remains the suite authority. |
| JavaScript/TypeScript | project-configured [ESLint](https://github.com/eslint/eslint) or [Biome](https://github.com/biomejs/biome), `tsc --noEmit`, package test script | oxlint, project framework CLI | `tsc` owns type correctness. Configured lint avoids inventing policy and avoids duplicate Biome/ESLint noise. Package scripts preserve Vitest/Jest/Node conventions. |
| Go | `gofmt`, `go vet`, `go test` | golangci-lint | Native commands are maintained with Go, cross-platform, fast, and need no extra policy. `go test` also vets package/test source. |
| Rust | `cargo fmt`, [Clippy](https://doc.rust-lang.org/stable/clippy/), `cargo test` | cargo-nextest | Rustup components are the native authorities. Default Clippy groups cover correctness, complexity, and performance; noisy restriction groups are not enabled wholesale. |
| Java | existing Maven/Gradle `verify`/`check` and `test` lifecycle | [Checkstyle](https://checkstyle.org/), SpotBugs, PMD | The build lifecycle preserves configured Checkstyle/SpotBugs and toolchains. Blueprint AI does not inject a second Java policy. Gradle can emit Checkstyle SARIF when configured. |
| Shell | [ShellCheck](https://github.com/koalaman/shellcheck), existing Bats suite | shfmt | ShellCheck is mature, local, fast, cross-platform, GPL-3.0, and emits JSON. It is invoked only with discovered shell paths. |

Generic languages still receive repository, security, supply-chain, docs, and model-assisted reviews.
This is deliberate graceful coverage, not a claim of native compilation support.

## Test capability routing

Applicability is evidence-driven: absence of an irrelevant test class is not a finding.

| Capability | Preferred route | Why / boundary |
| --- | --- | --- |
| Unit/regression/smoke | existing native runner | Project configuration and fixtures are semantic authority. Deterministic smoke generation is limited to Python/JS layouts, marked as generated, executed immediately, and never masks a product failure. |
| Browser E2E | [Playwright](https://github.com/microsoft/playwright) | Mature Apache-2.0 project with cross-browser CI and strong auto-waiting. Recommended only for detected web UI; not auto-installed. |
| Property-based | [Hypothesis](https://github.com/HypothesisWorks/hypothesis) or ecosystem-native fuzz/property tools | High value for pure/stateful surfaces, but behavior and invariants require project judgment. |
| API property/contract | [Schemathesis](https://github.com/schemathesis/schemathesis), [Pact](https://docs.pact.io/) | Schemathesis derives OpenAPI/GraphQL cases and emits JUnit/HAR; Pact is the code-first integration-contract choice. Only applicable when a contract/service boundary exists. |
| Load/performance | [k6](https://github.com/grafana/k6), [Locust](https://github.com/locustio/locust), native benchmarks | k6 is a strong compiled CLI; Locust fits Python suites. Neither is demanded without production/capacity evidence. Stress/soak/resilience remain project-authored variants. |
| Fuzz | Go/Rust/native compiler fuzzers first | Native fuzz targets preserve compiler instrumentation and corpus conventions. |
| DAST | [OWASP ZAP](https://github.com/zaproxy/zaproxy) baseline scan | Never runs by default. The bounded baseline adapter is selected only for an explicit credential-free `authorized_target`. A URL discovered in source is never authorization. |

## API, security, and supply chain

| Capability | Default | Alternates / overlap decision |
| --- | --- | --- |
| OpenAPI/AsyncAPI lint | [Spectral](https://github.com/stoplightio/spectral) plus native parser | Spectral is Apache-2.0, local/CI capable, and JSON/JUnit/SARIF-friendly. The official AsyncAPI CLI/parser is preferable for specification validity; Spectral adds governance. GraphQL uses its project compiler because generic lint policy varies. |
| SAST | [Semgrep](https://github.com/semgrep/semgrep) when a local config exists | Broad language coverage and JSON/SARIF. No `--config auto` default because fetching a registry ruleset is an implicit network/dependency action and can change false-positive behavior. |
| Secrets | [Gitleaks](https://github.com/gitleaks/gitleaks) | Fast MIT single binary with JSON/SARIF. Blueprint AI's small high-confidence literal/file checks provide fallback; Trivy overlap deduplicates by rule/location. |
| Dependency vulnerabilities | [OSV-Scanner](https://github.com/google/osv-scanner), then [Trivy](https://github.com/aquasecurity/trivy) | Focused OSV coverage plus broad artifact/config coverage. [Grype](https://github.com/anchore/grype) is a useful alternate. All have structured local/CI output; network database refresh failures are tool errors, not product findings. |
| SBOM | [Syft](https://github.com/anchore/syft) | Mature Apache-2.0 local scanner emitting SPDX/CycloneDX/Syft JSON. Native build attestations are preferable when already present. |
| Signing/provenance | [Cosign](https://github.com/sigstore/cosign) guidance | Verification/signing is artifact- and identity-specific. It is not run against guessed images and signing is never an automatic remediation. |
| Repository posture | [OpenSSF Scorecard](https://github.com/ossf/scorecard) guidance for public GitHub projects | Valuable public-host posture checks, but inappropriate for arbitrary local folders and often requires remote API evidence. Local deterministic workflow/OSS checks run first. |
| Dependency licenses | Syft/native package metadata, policy-specific review | License compatibility requires declared policy and legal context. Report evidence; do not invent allow/deny decisions. |

## Infrastructure and delivery

| Area | Default route | Comparison |
| --- | --- | --- |
| Terraform/OpenTofu | native fmt/validate, [TFLint](https://github.com/terraform-linters/tflint), then [Checkov](https://github.com/bridgecrewio/checkov) | Native syntax/provider initialization remains authoritative; TFLint adds provider rules; Checkov adds broader security policy with more overlap/noise. Trivy is an alternate misconfiguration source. |
| CloudFormation/SAM | [cfn-lint](https://github.com/aws-cloudformation/cfn-lint), then [CloudFormation Guard](https://github.com/aws-cloudformation/cloudformation-guard) only with rules | AWS documents that Guard is policy, not syntax/property validation; cfn-lint is the correctness default. Checkov is the cross-IaC security alternate. |
| Dockerfile/container | [Hadolint](https://github.com/hadolint/hadolint), Trivy | Hadolint is a fast Haskell binary with JSON/SARIF/Checkstyle; Trivy adds image/dependency/misconfiguration coverage. Built-in rules cover only high-confidence base/user/health concerns. |
| Kubernetes/Helm/Kustomize | native Helm/Kustomize render, [kubeconform](https://github.com/yannh/kubeconform), [KubeLinter](https://github.com/stackrox/kube-linter) | Kubeconform is fast schema correctness with CRD support; KubeLinter provides opinionated workload checks. [Kubescape](https://github.com/kubescape/kubescape) adds CNCF security/compliance breadth and overlap, so sources are aggregated. No live cluster scan is inferred. |
| GitHub Actions | [actionlint](https://github.com/rhysd/actionlint), [zizmor](https://github.com/zizmorcore/zizmor) | Both are fast local binaries; actionlint covers semantics and shell integration, zizmor emits SARIF for security. Built-in SHA and `pull_request_target` checks remain available. |
| Docs | [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2), [lychee](https://github.com/lycheeverse/lychee) | Markdown style and links are distinct. Network-related link failures remain tool errors/partial results, not deterministic broken-link findings. |

Renovate/Dependabot are recommendations/templates only because they mutate hosting state. Sigstore,
Scorecard, DAST, cloud-account scanners, and live Kubernetes scans remain explicit-scope features.

## Model context and interchange

[Repomix](https://github.com/yamadashy/repomix) offers Git-aware packing, secret checks, token counts,
and Tree-sitter `--compress`. [Aider's repo map](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/repomap.md)
uses key symbols plus dependency-graph ranking and normally budgets a small map. Both are proven.
Adding Node/Tree-sitter solely for context would exceed Blueprint AI's current dependency budget, so
Phase 2 keeps a native, replaceable approximation: blueprint-specific ranking, changed-file priority,
file index, symbol/dependency map, large-file signature compression, cross-review content hashes,
secret redaction, cache keys, per-blueprint/per-run budgets, structured outputs, and token metrics.
The whole repository is never sent simply because a model has a large context window.

JSON is canonical. Markdown is for humans and SARIF 2.1.0 is emitted/consumed for CI interchange.
JUnit, JSON Lines, native JSON, and selected tool schemas are parsed before text fallback. Stable
fingerprints use blueprint/rule/location rather than message wording; sources, severity, priority,
confidence, provenance, baseline state, and reasoned/expiring suppressions remain distinct.

## Operational policy

Commands are argument arrays constructed in code and execute without a shell. Availability/version,
command, exit class, exit code, and duration are captured. Independent tools run concurrently with a
bounded worker count, per-tool timeout, bounded sanitized output, and controlled environment.
Outcomes distinguish finding, passed, tool error, missing, and unsupported. Missing tools make a
blueprint partial and include explicit install guidance. Tools that run target code or executable
configuration are unsupported until the operator passes `--trust-project-executables`; DAST requires
the separate exact `--authorize-target` scope. Network-capable tools are suppressed in offline mode.
The `bootstrap` command only prints guidance: package-native ephemeral runners and containers are not
invoked automatically because mutable registries/tags weaken reproducibility and trust.

## Phase 4 verified command contracts

These invocations were exercised with Gitleaks 8.30.1, OSV-Scanner 2.5.1, Trivy 0.74.0,
actionlint 1.7.12, zizmor 1.30.1, markdownlint-cli2 0.23.2, and Lychee 0.24.2:

| Tool | Validated contract | Normalization boundary |
| --- | --- | --- |
| Gitleaks | `dir --report-format json --report-path - --redact` (or `git` in a Git repository) | Rule/file/line are preserved; a generic-key duplicate at the exact same location is discarded only when a specific secret rule exists. |
| OSV-Scanner | `scan source --format json` | Advisory severity, aliases, package/ecosystem, installed version, and fixed version are retained; a group CVE becomes the stable cross-tool rule ID. |
| Trivy | `fs --format json --scanners vuln,misconfig` with dependency directories skipped | Vulnerabilities and nested IaC `CauseMetadata` locations are retained. Secret scanning is left to Gitleaks to avoid duplicate scope. |
| actionlint | JSON format with explicit discovered workflow paths | Expression findings retain snippet and location and share a template-injection identity with zizmor. |
| zizmor | `--offline --format sarif .github/workflows` | SARIF findings are accepted even though SARIF mode exits zero for audit findings; original rule IDs remain in metadata when normalized for aggregation. |
| markdownlint-cli2 | Explicit discovered Markdown paths | Ignored/vendor files are not linted. Target configuration can execute JavaScript, so it remains behind the project-executable trust boundary. |
| Lychee | JSON over explicit discovered Markdown paths, 10-second request timeout, one retry, loopback exclusion, 30-second adapter deadline | Exit 2 means broken links and is a finding; runtime/input failures remain tool errors. File, line, URL, and status detail are retained. |

Loopback links are excluded because service documentation commonly names a deliberately unavailable
local development endpoint. Missing local files remain checked. Network reachability and private/403
targets can still require project-specific Lychee exclusions; Blueprint AI does not silently convert
those results to passes.

## Phase 5 decisions — 2026-09-12

The supplied ecosystem supplement was reassessed against the nine-repository audit. The following
supersedes earlier launch recommendations where the scope differs. Existing scanner adapters remain
optional; genesis explicitly authorizes a small registered set of ephemeral providers. “Defer” means
no new implementation or verification claim in 0.5.0, even when an older guidance/adapter entry exists.
No remote service is silently activated. Grouped candidates share the stated decision and boundary.

| Capability / candidates | Phase 5 decision | Evidence, value, and boundary |
| --- | --- | --- |
| PyPA rules, `packaging`, npm naming | source/reuse + integrate | `packaging` parses requirements and canonicalizes names; distinct display/repo/package/module namespaces fix the Flask audit defect. Availability remains unchecked. |
| DNS/IDNA, OCI, Kubernetes and cloud resource naming | defer | No corresponding genesis deployment provider ships; do not imply one slug satisfies every platform. |
| uv init (MIT/Apache-2.0), npm init (Artistic-2.0) | wrap/adapt | Maintained native noninteractive initializers; installed versions probed, executable hashes recorded, fresh staging tested. |
| Vite/create-vite (MIT) | wrap/adapt | Current maintained SPA foundation; exact 9.2.1 version and registry integrity verified. Native configuration is retained. React SSR/framework selection requires different intent. |
| Create React App | reject for genesis | Upstream deprecation plus audited maintenance state; recognize existing projects without recommending it for new ones. |
| create-next-app, create-vue, Svelte, Angular, Astro | defer | Separate framework/runtime verification matrices would exceed this release's useful scope. |
| Go/Cargo/.NET/Spring/Quarkus initializers | defer genesis | Existing-review native tool routing remains; launch coverage concentrates on actually built Python/TS compositions. |
| Copier, Cookiecutter, GitHub templates/gh repo create, Yeoman | defer | Remote archives, hooks, answer-file updates, auth, and conflict semantics deserve a separate verified trust/transaction boundary. Native initializers meet the present common paths. |
| Backstage, Plop, Hygen | inspire | Portable intent/ownership/verification contracts; no embedded portal or generic template engine. |
| npm/pnpm/Cargo/uv workspace metadata | integrate discovery | Component roots and manifest workspace edges support real polyglot reviews. Generated full stack uses independent native component manifests. |
| Nx, Turbo, Pants, Bazel, Buck2 | defer | No task/build engine duplication; graph facts do not pretend to replace compiler or build-system graphs. |
| OpenAPI 3.1, JSON Schema; openapi-spec-validator | source/reuse + integrate | Native schema validation rather than a custom spec validator; Python library dependency exercised on real generated contracts. |
| openapi-typescript (MIT) | wrap/adapt | Exact 7.13.0 plus registry integrity; smaller TS-only client-type surface than Java-based OpenAPI Generator, compiled with the generated frontend. |
| Spectral/Redocly, oasdiff, OpenAPI Generator | defer new default | Existing configured Spectral adapter retained. Governance, cross-language generation, and contract evolution need explicit policies/baselines; no second default lint engine. |
| Schemathesis, AsyncAPI, Buf/Protobuf, Pact, GraphQL tooling | defer genesis | Contract graph accommodates them; active tests need declared service/channel/consumer boundaries and authorized endpoints. |
| Playwright, Hypothesis, cargo-fuzz, Testcontainers | integrate inventory; defer automatic new stacks | Real inline/fuzz/cassette/integration evidence replaces filename-only inventory. Starter UI render tests do not claim end-to-end coverage. |
| Stryker/PIT, Toxiproxy, Chaos Mesh/Litmus | defer | Mutation/fault/cluster tests need meaningful invariants and explicit execution/impact budgets. |
| Tree-sitter, Aider/Repomix maps | inspire | Bounded graph-ranked native retrieval fixes facts-only contexts without another parser/runtime distribution. Multi-language compiler-level indexing remains deferred. |
| Python AST; python-hcl2 (MIT) | integrate | Compact AST evidence and real HCL parsing resolve demonstrated API/test/module/caller-context gaps. No HCL evaluator or compiler is recreated. |
| ast-grep, LibCST, jscodeshift, OpenRewrite, Clang/LSP refactoring | defer | Future staged transforms can use the existing ownership/verification contracts; no speculative migration engine. |
| Ruff, mypy, TypeScript, Biome/ESLint, native Go/Rust/JVM tools | integrate/adapt | Module roots, scope filters, project trust, and explicit compiler prerequisites correct empirical routing failures. Generated stacks run their native checks. |
| dependency-cruiser, Import Linter, ArchUnit, Knip | defer | Useful once projects declare enforceable boundaries; do not invent architecture rules from weak metadata. |
| Renovate/Dependabot | source/reuse | Genesis optionally writes one Renovate policy; no hosting configuration or bot activation. Existing configurations count as evidence. |
| Gitleaks, OSV-Scanner, Trivy | integrate/adapt | Existing structured adapters, scope/package/advisory identity, malformed/truncated evidence, external caches. Trivy remote-module downloads now remain unsupported without a hard bounded network path. |
| Semgrep, Checkov, Hadolint, kubeconform/kube-linter/Kubescape | retain adapters | No integration sweep; target code/config execution and missing prerequisites remain explicit. Hadolint was unavailable in this environment. |
| CodeQL, TruffleHog, Scorecard | defer expansion | Licensing/hosting/dataflow or overlapping default scans add little to the bounded local launch scope. |
| Syft/Grype, SPDX/CycloneDX, REUSE, ORT/ScanCode | retain existing guidance; defer new defaults | SBOM/license policy and deep download surfaces require a declared release/compliance purpose. No legal compatibility claims. |
| SLSA/in-toto, cosign, GitHub attestations | source/reuse concepts; defer signing | Receipts identify input/tool/output, but are not signed attestations or a SLSA level. Identity/auth/publish remain separate. |
| OPA/Conftest, Kyverno/Gatekeeper, CUE | defer; inspire constraints | Strict typed schemas and a small dependency/conflict resolver suffice; no mandatory policy language or solver. |
| Git attributes/ignores, CODEOWNERS, GitHub rulesets/APIs | source/reuse hygiene; defer remote governance | Generated local hygiene is small; owners/teams and remote changes are never invented. |
| actionlint/zizmor, SHA pins/least permissions/reusable workflows | integrate/adapt | Real CI checks pass on generated Python/Node workflows; valid same-repository `$/.github/workflows/...` is recognized. |
| Harden-Runner, GitLab/CircleCI/Buildkite/Jenkins | defer | Egress telemetry and separate hosting models require provider-specific review. |
| Docker/BuildKit, Dev Container specification | wrap/adapt + source/reuse | Real app and development image builds, non-root users, explicit runtimes, local daemon prerequisite. No Docker credential/socket mounting into images. |
| Compose | integrate discovery; defer service generation | Build/service relationships recognized; no database/dependency service is invented for an empty starter. |
| Helm/Kustomize, OpenTofu/Terraform, Pulumi, CDK/Bicep/SAM/azd | defer genesis | Provider credentials, backend/state policy, cost, runtime/platform choices, and meaningful cloud tests would dilute verified local composition. Existing IaC review improved. |
| Crossplane, Terragrunt, Infrastructure Manager, Infracost | defer | Platform orchestration, live cloud APIs, and cost assumptions remain explicit future scope. Implicit `azd up`/cloud apply is rejected. |
| uvx/npm runners | wrap/adapt | Selected package/version/integrity only; explicit network and provider trust, no arbitrary remote template commands. |
| mise, Nix/devenv/Flox, Make/Just/Task | defer new defaults | Use native package scripts; no extra runtime/task system for the starter. |
| C4/ADRs, Diátaxis, native docs builders/TechDocs | inspire; defer new generators | Inventory and concise runnable README guidance before generic documentation machinery. |
| OTel/Prometheus, SLOs, k6/Lighthouse/profilers, WCAG/axe | integrate existing evidence; defer automated policy | Detect telemetry and tests; generate health endpoints/accessibility-conscious markup. No SLO, performance, accessibility-conformance, or production guarantee. |
| Alembic/Prisma, Flyway/Liquibase, Atlas, SQLFluff | defer | Migration scope is modeled, but schema/data changes need declared database ownership and rollback evidence. |
| Changesets, semantic-release, release-please, GoReleaser, commitlint | defer | Packaging is verified; publish workflows and commit conventions are not imposed on starters. |
| AGENTS.md/skills/MCP | source/reuse inventory; defer generated integrations | AI instruction files trigger their own profile without implying the application uses AI. No autonomous execution server. |
| Structured model output and regression rubrics | integrate/adapt | Existing provider protocol, bounded contexts/calls, cache identity, strict validation; no repair call outside the call budget. |
| Semantic intent/naming, architecture/test-completeness judgment | model-assist design; defer genesis invocation | Deterministic intent/name validation is authoritative. Existing review can request semantic judgment; no live model result is claimed for this release. |
| OpenHands, Aider, SWE-agent patterns | inspire | Small evidence packages, bounded commands, and verified transactions; reject an embedded open-ended coding agent or unbounded prompt-to-patch provider. |
| Promptfoo, Langfuse, Phoenix/OpenInference | defer | Current graph captures AI dependency/cassette evidence; evaluation/telemetry platform setup is separate. |

Selected-provider primary references were refreshed: [uv CLI](https://docs.astral.sh/uv/reference/cli/),
[npm init](https://docs.npmjs.com/cli/v11/commands/npm-init),
[Vite guide](https://vite.dev/guide/),
[React's CRA retirement](https://react.dev/blog/2025/02/14/sunsetting-create-react-app),
[python-hcl2](https://github.com/amplify-education/python-hcl2),
[openapi-spec-validator](https://github.com/python-openapi/openapi-spec-validator),
[OpenAPI TypeScript](https://openapi-ts.dev/introduction),
[PyPA normalization](https://packaging.python.org/en/latest/specifications/name-normalization/),
[setup-node](https://github.com/actions/setup-node),
[Dev Container specification](https://github.com/devcontainers/spec), and
[Docker build](https://docs.docker.com/reference/cli/docker/buildx/build/).
Fetched source is not vendored. MIT/UNLICENSED is an explicit generated-project choice; provider
licenses and resulting dependency licenses remain distinct. Deferred-candidate evidence comes from
the supplied supplement and has not been recertified as a newly shipped integration.

## Phase 6 decisions — 2026-09-12

This refresh builds on the released Phase 5 evidence and its locally available ecosystem supplement.
The [support registry](support.md) contains executable version/source/license/acquisition metadata;
[the gap inventory](phase-6-gaps.json) records classifications and final outcomes. These decisions
select representative paths rather than promise every framework or deployment permutation.

**Isolation and acquisition.** Docker's native controls supply mounts, non-root execution, resource
limits, and private networking; recursive bind mounts are disabled. Podman uses the same policy
contract on local Linux. Configured runsc supplies an optional stronger boundary. Bubblewrap/nsjail
need deployment-specific namespace/seccomp/cgroup policies and remain deferred. Official sources:
[Docker run](https://docs.docker.com/engine/containers/run/),
[bind mounts](https://docs.docker.com/engine/storage/bind-mounts/),
[rootless Docker](https://docs.docker.com/engine/security/rootless/),
[Podman run](https://docs.podman.io/en/latest/markdown/podman-run.1.html),
[gVisor](https://gvisor.dev/docs/user_guide/quick_start/docker/),
[bubblewrap](https://github.com/containers/bubblewrap), and [nsjail](https://github.com/google/nsjail).

Explicit OCI acquisition avoids global host installs. Small tool-image recipes use a resolved base
digest and pinned packages/components. Native ephemeral generators keep their upstream ownership;
Blueprint AI verifies available registry integrity and records output/image hashes. Read the
[uv tool model](https://docs.astral.sh/uv/concepts/tools/),
[uv Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/), and
[npm exec contract](https://docs.npmjs.com/cli/v11/commands/npm-exec/).
The Helm image selected for these local tests is the community `alpine/helm` distribution; the Helm
CLI source is authoritative, but that image is not represented as an official Helm publication.

**Application foundations.** Keep uv/FastAPI and TypeScript/Fastify; add Django and Flask through
native Python packaging, Go modules, Cargo, dotnet templates, Maven's quickstart archetype, and the
official Initializr service. HTTP tests exercise generated Spring Boot and ASP.NET health/404 routes.
These choices follow [Django](https://docs.djangoproject.com/en/5.2/ref/django-admin/),
[Flask](https://flask.palletsprojects.com/en/stable/),
[Go](https://go.dev/doc/tutorial/create-module),
[Cargo](https://doc.rust-lang.org/cargo/commands/cargo-init.html),
[dotnet new](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-new),
[ASP.NET integration tests](https://learn.microsoft.com/en-us/aspnet/core/test/integration-tests?view=aspnetcore-10.0),
[Maven quickstart](https://maven.apache.org/archetypes/maven-archetype-quickstart/),
[Initializr](https://docs.spring.io/initializr/docs/current/reference/html/), and
[Spring Boot testing](https://docs.spring.io/spring-boot/reference/testing/spring-boot-applications.html).

Use Vite's native React/Vue/Svelte TypeScript starters and pinned create-next-app for Next.js.
Keep full stack as separate native components with a generated OpenAPI client option.
Angular and Nx are capable choices, but their CLI/builder/plugin/version matrices add a distinct
maintenance obligation; this release recognizes projects and defers dedicated initialization.
See [Vite](https://vite.dev/guide/), [Vue](https://vuejs.org/guide/quick-start),
[Svelte testing](https://svelte.dev/docs/svelte/testing),
[Next CLI](https://nextjs.org/docs/app/api-reference/cli/create-next-app),
[Angular CLI](https://angular.dev/cli/new), and [Nx commands](https://nx.dev/docs/reference/nx-commands).
Laravel/Symfony initialization and Rust asynchronous APIs remain deferred pending dedicated
application/database or async-runtime fixtures. Existing Composer/Rust inventory and native review
remain available. Sources: [Laravel](https://laravel.com/framework/docs/installation),
[Symfony](https://symfony.com/doc/current/setup.html), [Axum](https://docs.rs/axum/latest/axum/).
Express/NestJS skeletons are not additional defaults alongside the existing Fastify service path.

**Infrastructure.** Terraform/OpenTofu perform local format/init/validate with backend initialization
explicitly disabled. Pulumi uses generate-only and type checks. The three clouds receive provider
requirements without billable resources, credentials, remote state, or deployment commands.
Kubernetes uses a Namespace baseline; Helm and Kustomize own native rendering.
[Terraform validate](https://developer.hashicorp.com/terraform/cli/commands/validate),
[OpenTofu validate](https://opentofu.org/docs/cli/commands/validate/),
[Pulumi new](https://www.pulumi.com/docs/iac/cli/commands/pulumi_new/),
[Helm create](https://helm.sh/docs/helm/helm_create/) document the selected mechanics.
CDK/SAM and Bicep/azd are deferred as separate synthesis/template matrices; current multi-cloud
foundations cover the local creation need. GCP Infrastructure Manager is a managed Terraform
workflow, and Crossplane requires explicit provider/schema/cluster context. Sources:
[CDK init](https://docs.aws.amazon.com/cdk/v2/guide/ref-cli-cmd-init.html),
[azd init](https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/azd-init-workflow),
[Infrastructure Manager](https://docs.cloud.google.com/infrastructure-manager/docs/overview),
[Crossplane composition](https://docs.crossplane.io/latest/composition/).

**Complementary hardening.** Conftest executes configured Rego policies; ast-grep executes configured
structural rules; Buf lints configured Protobuf projects. No remote breaking-change baseline is guessed.
Syft's SBOM envelope is normalized as inventory evidence, separately from vulnerabilities. Semgrep
uses local rules, bounded jobs/memory, disabled metrics and disabled version checks. Gitleaks retains
redacted output. Kubescape/Grype remain explicit alternatives to default Kubernetes/vulnerability
routes to limit duplicate signal. See [Conftest](https://www.conftest.dev/),
[ast-grep scan](https://ast-grep.github.io/guide/scan-project.html),
[Buf](https://buf.build/docs/cli/installation/),
[Syft installation](https://oss.anchore.com/docs/installation/syft/),
[Semgrep CLI](https://docs.semgrep.dev/cli-reference), and
[Gitleaks](https://github.com/gitleaks/gitleaks).

OpenAPI remains the generated contract/client foundation. GraphQL recognition and configured
AsyncAPI/Spectral governance remain partial; native schema/client generation and Pact/Schemathesis
live-service testing need explicit service/baseline intent. No DAST target is inferred from a repo.
[AsyncAPI validation](https://www.asyncapi.com/docs/guides/validate) distinguishes schema validation
from governance and generation. Existing native tests and deterministic AST/dependency facts remain
authoritative; no new default model dependency or scanner bundle is introduced.

**Release and hosting.** GitHub Actions stays the generated CI default; GitLab inventory is retained,
with hosted lint/generation deferred. Package publishing remains manual preparation. Prefer a scoped
OIDC/trusted publisher and protected release environment over a stored long-lived token when a
registry owner configures publication. This session neither configures a registry account nor publishes.
Sources: [GitLab CI](https://docs.gitlab.com/ci/) and
[PyPA trusted publishing guidance](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).
