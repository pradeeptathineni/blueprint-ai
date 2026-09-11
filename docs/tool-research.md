# Phase 2 tool research

Snapshot: 2026-09-11. This refresh uses upstream documentation and repositories rather than
memory. The actively maintained [static-analysis](https://github.com/analysis-tools-dev/static-analysis)
and [dynamic-analysis](https://github.com/analysis-tools-dev/dynamic-analysis) catalogs were checked
again for language/ecosystem gaps. Their stale/archive markers are useful discovery evidence, but a
catalog entry alone is not sufficient for integration.

Selection weights maintenance, meaningful adoption, OSI license, machine-readable output, fully
local execution, CI suitability, startup/runtime cost, macOS/Linux/Windows availability, overlap,
configuration burden, and false-positive behavior. Tools remain optional CLIs; none of their code is
redistributed. Blueprint AI never silently downloads a tool.

## Language authorities

| Stack | Default | Useful alternate | Decision |
|---|---|---|---|
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
|---|---|---|
| Unit/regression/smoke | existing native runner | Project configuration and fixtures are semantic authority. Deterministic smoke generation is limited to Python/JS layouts, marked as generated, executed immediately, and never masks a product failure. |
| Browser E2E | [Playwright](https://github.com/microsoft/playwright) | Mature Apache-2.0 project with cross-browser CI and strong auto-waiting. Recommended only for detected web UI; not auto-installed. |
| Property-based | [Hypothesis](https://github.com/HypothesisWorks/hypothesis) or ecosystem-native fuzz/property tools | High value for pure/stateful surfaces, but behavior and invariants require project judgment. |
| API property/contract | [Schemathesis](https://github.com/schemathesis/schemathesis), [Pact](https://docs.pact.io/) | Schemathesis derives OpenAPI/GraphQL cases and emits JUnit/HAR; Pact is the code-first integration-contract choice. Only applicable when a contract/service boundary exists. |
| Load/performance | [k6](https://github.com/grafana/k6), [Locust](https://github.com/locustio/locust), native benchmarks | k6 is a strong compiled CLI; Locust fits Python suites. Neither is demanded without production/capacity evidence. Stress/soak/resilience remain project-authored variants. |
| Fuzz | Go/Rust/native compiler fuzzers first | Native fuzz targets preserve compiler instrumentation and corpus conventions. |
| DAST | [OWASP ZAP](https://github.com/zaproxy/zaproxy) baseline scan | Never runs by default. The bounded baseline adapter is selected only for an explicit credential-free `authorized_target`. A URL discovered in source is never authorization. |

## API, security, and supply chain

| Capability | Default | Alternates / overlap decision |
|---|---|---|
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
|---|---|---|
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
bounded worker count and per-tool timeout. Outcomes distinguish finding, passed, tool error, missing,
and unsupported. Missing tools make a blueprint partial and include explicit install guidance. The
`bootstrap` command only prints guidance: package-native ephemeral runners and containers are not
invoked automatically because mutable registries/tags weaken reproducibility and trust.
