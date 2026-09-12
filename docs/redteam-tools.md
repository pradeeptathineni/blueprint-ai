# Independent Phase 6 tool audit

This audit challenged the `6c376b9` candidate by inspecting registry, selection, acquisition,
process runner, parsers, and native outputs. The [machine-readable inventory](redteam-tool-inventory.json)
contains every registry entry, official source/license, version policy, platforms, integration role,
exact base arguments, parser, accepted exits, timeout, output bound, project-code trust marker,
network marker, acquisition plan, observed host version, local image identity, and receipt state.
Arguments are subsequently scoped by discovered components/configuration; the inventory records
base definitions and actually constructed dynamic Go/Maven/Gradle/ZAP routes.

## What the count actually means

There are **52 registered tool IDs**, including **47 review integrations**, OpenTofu genesis
verification, a Cargo acquisition/toolchain entry, a pre-commit capability-kit entry, and two
explicitly deferred entries (PHP lint and Kubescape). Registration is not a statement that all
52 scanners run during review. The dormant Kubescape definition has no public selection route.

**26 registered entries share 17 managed image references.** All 26 entries' images were present
locally during this audit; 13 entries resolved valid cached receipts. An image without a receipt
is not implicitly acquired or adopted. The operator can explicitly acquire it or select a local
image. The other 26 entries provide upstream/project-native acquisition guidance and require
an installed trusted native binary or an explicitly prepared OCI image. No arbitrary tool installer
or global host package manager was added.

Platform lists describe upstream paths, not certified per-tool OS matrices. In particular, the
host's detected .NET was 7.0.203, outside the registry's recommended .NET 10 range, and its global
TypeScript was 3.8.3; project-local TypeScript selection and managed SDK images remain the relevant
project authorities. Version ranges are recommendations and provenance, not enforced compatibility
certificates. A missing version probe is retained as unknown.

## Corrections

- Required recognizable JSON envelopes before reporting clean results. Empty objects/absent
  report fields previously passed in multiple structured parsers. Scanner-reported parsing errors,
  skipped schema coverage, failed SARIF invocations, and missing validation prerequisites now
  report incomplete analysis. A nonzero exit with no diagnostics cannot pass or become a fabricated
  finding containing raw `[]`.
- Replaced incompatible generic parser mappings for Biome and KubeLinter. Biome uses its native
  SARIF reporter; KubeLinter validates its actual `Reports`/`Summary` JSON. CloudFormation's
  case-sensitive JSON and bitmask exit codes, Spectral's source/range/numeric severity, and
  ast-grep's native severity are preserved.
- Corrected Terraform formatting exit code 3 and the managed Go formatter executable. Dynamic
  review adapters inherit canonical version/acquisition metadata, and sandbox tool statuses retain
  their project-trust marker. Version probing selects the version line after banners.
- Isolated each Kustomize root into one native build invocation. Discovered option-shaped filenames
  receive a relative-path prefix, so a Markdown file named `--config=evil.md` cannot change a tool's
  CLI options. Adapter-side configuration reads use bounded safe-file reads.
- Native test selection now follows owned language-specific evidence and explicit auxiliary project
  boundaries. Unconfigured Rust compiler Python/Java data no longer triggers pytest/Gradle; npm tests
  require a Node project. Inline Rust tests and Cargo workspace member tests reach one native
  workspace invocation. Explicit C# test projects remain eligible for testing.
- Missing/read-only/downloading Rust toolchains, and Go's explicit local-toolchain version mismatch,
  report an incomplete native prerequisite rather than a completed project-code finding. The
  [public-corpus comparison](redteam-corpus.md) records the discovered failure and corrected run.
  Cargo now receives the policy-aligned offline flag when egress is disabled. Its demonstrated
  missing cached dependency is also incomplete; the OpenTelemetry checks stop in under one second
  instead of waiting for their 120-second network timeout.
- Malformed/non-object acquisition receipts are safe cache misses. Base-image registry metadata
  must pass bounded process and digest-shape checks before any derived Dockerfile is built.
- Removed blanket suppression of actionlint workflow-reference diagnostics. The controlled
  `./.github/workflows/called.yml` → `$/.github/workflows/called.yml` → repaired cycle now retains
  the native diagnostic. Some pinned public sources actually contain `$` references while current
  GitHub syntax documentation describes `./`; retained scanner output is compatibility evidence,
  not a claim that those repositories necessarily fail GitHub's current service.
- Restricted the known Checkov security-group-reference false-positive suppression to complete,
  parsed HCL containing a real security-group-reference attribute without public-CIDR attributes.
  Adding a comment containing `referenced_security_group_id` can no longer hide a public ingress
  diagnostic. Incomplete or ambiguous source evidence retains the native finding.

## Native executions

All **14 clean → single controlled defect → repaired cycles passed**: 42 scanner executions,
including 36 Docker executions and six explicitly trusted host executions on controlled fixtures.
Every fixture snapshot remained unchanged; every Docker run reported teardown, read-only target,
no network, and resource enforcement. No target repository code ran on the host.

| Path | Tools | Executions |
| --- | --- | ---: |
| Existing receipt-backed managed OCI images | Ruff, Hadolint, markdownlint-cli2, Conftest, ast-grep, Buf, Gitleaks, Semgrep | 24 |
| Existing canonical managed toolchain images | gofmt, terraform-fmt | 6 |
| Explicit prepared native tools in existing Node OCI image | Biome 2.5.1, KubeLinter 0.8.3 | 6 |
| Trusted host scanner binaries, controlled data only | actionlint 1.7.12, ShellCheck 0.11.0 | 6 |

Biome was installed only into an isolated temporary fixture through npm in Docker with lifecycle
scripts disabled. KubeLinter's official Linux binary was checked against the GitHub release asset
SHA-256 `618d299a3e2839c8ca9d86fce0db617be0fba41f0fecbbbfb7fbf1c04299fae1`
and executed only inside Docker. These are validation fixtures, not new managed acquisition paths.
CloudFormation and Spectral corrections additionally have contract regressions checked against
upstream formatter source; neither is claimed as a newly live-validated integration here.

Additional native OpenTelemetry Cargo clippy/test captures and adapter reruns verified the offline
dependency state. Both returned exit 101 with missing cached `actix-web`, no project-code findings,
unchanged source, and confirmed teardown. This does not claim the upstream Rust project built or
tested successfully without its dependencies.

The new unit regressions also exercise malformed scanner output, explicit partial-analysis envelopes,
poisoned caches, bad base-image metadata, dangerous filenames, multiple Kustomize roots, native
severity/source semantics, and the Checkov comment bypass. The full release-gate results are recorded
in the independent release report.

## Complete lifecycle inventory

`Managed` means an explicit container acquisition exists; `external` means official/project-native
installation or an operator-prepared image. All review runs default to OCI isolation with no network.
Trusted host mode explicitly lacks filesystem/network/resource isolation. Network markers indicate
known tool behavior; arbitrary trusted project code cannot be constrained by an offline CLI flag.
Kubeconform's ordinary remote schema lookup is now marked network-dependent. Database-backed
scanners, external schema/reference resolution, plugins, and target applications still require the
operator's corresponding dependencies and explicit network authorization.

| Tool | Role | Native parser | Acquisition | Observed host version |
| --- | --- | --- | --- | --- |
| `ruff` | review | `parse_ruff` | managed OCI | ruff 0.16.7 |
| `mypy` | review | `parse_lines` | external guidance | mypy 2.3.1 (compiled: no) |
| `pytest` | review | `parse_lines` | external guidance | pytest 9.1.1 |
| `biome` | review | `parse_sarif` | external guidance | unavailable / not probed |
| `eslint` | review | `parse_json_list` | external guidance | unavailable / not probed |
| `tsc` | review | `parse_lines` | external guidance | Version 3.8.3 |
| `go-vet` | review | `parse_lines` | managed OCI | go version go1.27.0 darwin/amd64 |
| `go-test` | review | `parse_lines` | managed OCI | unknown |
| `gofmt` | review | `parse_output_paths` | managed OCI | unknown |
| `cargo-fmt` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `cargo-clippy` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `cargo-test` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `maven-check` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `maven-test` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `gradle-check` | review | `parse_lines` | external guidance | unavailable / not probed |
| `gradle-test` | review | `parse_lines` | external guidance | unavailable / not probed |
| `dotnet-build` | review | `parse_lines` | managed OCI | 7.0.203 |
| `dotnet-test` | review | `parse_lines` | managed OCI | 7.0.203 |
| `php-lint` | deferred | `none` | external guidance | unavailable / not probed |
| `composer-validate` | review | `parse_lines` | external guidance | unavailable / not probed |
| `npm-test` | review | `parse_lines` | external guidance | 11.17.0 |
| `shellcheck` | review | `parse_shellcheck` | external guidance | version: 0.11.0 |
| `semgrep` | review | `parse_semgrep` | managed OCI | unavailable / not probed |
| `gitleaks` | review | `parse_json_list` | managed OCI | gitleaks version 8.30.1 |
| `osv-scanner` | review | `parse_osv` | external guidance | osv-scanner version: 2.5.1 |
| `trivy` | review | `parse_trivy` | external guidance | Version: 0.74.0 |
| `syft` | review | `parse_syft` | managed OCI | unavailable / not probed |
| `grype` | review | `parse_grype` | external guidance | unavailable / not probed |
| `terraform` | review | `parse_terraform` | managed OCI | Terraform v1.16.0 |
| `terraform-fmt` | review | `parse_lines` | managed OCI | Terraform v1.16.0 |
| `terraform-test` | review | `parse_lines` | managed OCI | Terraform v1.16.0 |
| `tofu` | genesis | `none` | managed OCI | unavailable / not probed |
| `tflint` | review | `parse_tflint` | external guidance | unavailable / not probed |
| `checkov` | review | `parse_checkov` | external guidance | unavailable / not probed |
| `cfn-lint` | review | `parse_cfn_lint` | external guidance | unavailable / not probed |
| `hadolint` | review | `parse_json_list` | managed OCI | unavailable / not probed |
| `kubeconform` | review | `parse_kubeconform` | external guidance | unavailable / not probed |
| `kube-linter` | review | `parse_kube_linter` | external guidance | unavailable / not probed |
| `kubescape` | deferred | `parse_json_list` | external guidance | unavailable / not probed |
| `helm` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `kustomize` | review | `parse_lines` | managed OCI | unavailable / not probed |
| `actionlint` | review | `parse_actionlint` | external guidance | 1.7.12 |
| `zizmor` | review | `parse_sarif` | external guidance | zizmor 1.30.1 |
| `spectral` | review | `parse_spectral` | external guidance | unavailable / not probed |
| `markdownlint-cli2` | review | `parse_markdownlint` | managed OCI | unavailable / not probed |
| `lychee` | review | `parse_lychee` | external guidance | lychee 0.24.2 |
| `conftest` | review | `parse_conftest` | managed OCI | unavailable / not probed |
| `ast-grep` | review | `parse_ast_grep` | managed OCI | unavailable / not probed |
| `buf` | review | `parse_buf` | managed OCI | unavailable / not probed |
| `zap-baseline` | review | `parse_lines` | external guidance | unavailable / not probed |
| `cargo` | toolchain | `none` | managed OCI | unavailable / not probed |
| `pre-commit` | capability-kit | `none` | external guidance | unavailable / not probed |

## Verification commands and evidence

```bash
.venv/bin/ruff check src/blueprint_ai/adapters src/blueprint_ai/tooling.py tests/test_adapters.py tests/test_redteam_tools.py benchmarks/redteam_tools.py
.venv/bin/mypy src/blueprint_ai/adapters src/blueprint_ai/tooling.py
.venv/bin/pytest tests/test_redteam_tools.py tests/test_adapters.py tests/test_phase2.py tests/test_phase3.py tests/test_phase4.py tests/test_phase6.py
.venv/bin/python benchmarks/tool_matrix.py --output /tmp/blueprint-redteam-tool-matrix-final.json
.venv/bin/python benchmarks/redteam_tools.py --native-tools /tmp/blueprint-redteam-native --native-host --output /tmp/blueprint-redteam-native-matrix.json
```

The second native benchmark never installs tools implicitly: `--native-tools` points to prepared
KubeLinter/Biome assets, and `--native-host` explicitly enables controlled host-scanner cases. Omitting
both flags runs only the existing Go/Terraform image cases. JSON evidence retains commands, policies,
immutable image identities, exit codes, durations, normalized findings, and unchanged snapshots.

- `/tmp/blueprint-redteam-tool-matrix-final.json` — SHA-256 `1f1793d8ea0d562b95946ea094d270de4483868376c96d0e291b84265dd5f5e7`
- `/tmp/blueprint-redteam-native-matrix.json` — SHA-256 `db1009ac115178286ff8cc0327ff6830c75718a9f6505d1cce12194e7517c39d`

Authoritative native contracts: [Biome reporters](https://biomejs.dev/reference/reporters/),
[KubeLinter result model](https://github.com/stackrox/kube-linter/blob/main/pkg/run/run.go),
[CloudFormation JSON formatter](https://github.com/aws-cloudformation/cfn-lint/blob/main/src/cfnlint/formatters/json.py),
[CloudFormation exit codes](https://github.com/aws-cloudformation/cfn-lint#exit-codes),
[Spectral JSON formatter](https://github.com/stoplightio/spectral/blob/develop/packages/formatters/src/json.ts),
and [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
