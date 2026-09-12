# Independent public-corpus regression audit

Nine exact pinned public repositories were independently reviewed with model mode off. Every
checkout retained its original source snapshot and clean Git state in every run. Project types,
component counts, and lifecycle classifications remained consistent with the original Phase 6
evidence. This is read-only review evidence; unavailable native builds/tests are not counted as
successful upstream tests. The [comparison JSON](redteam-corpus.json) retains pinned commits,
fingerprint changes, priorities, tool-state transitions, timings, execution policies, and final
report hashes.

## Measured results

`Phase 6` is the original checked-in evidence, `first` is the initial independent run,
and `checkpoint` precedes the last native test ownership/error corrections. `Final` is a fresh
process after those corrections and Cargo offline alignment. A separate run before the Cargo
offline change is preserved in the JSON; its later disk hashes do not prove the already-running
process imported that change.

| Repository | Phase 6 | First | Checkpoint | Final | Final review | Discovery |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fastapi/full-stack-fastapi-template` | 28 | 29 | 29 | 29 | 7.50 s | 0.625 s |
| `open-telemetry/opentelemetry-demo` | 206 | 206 | 206 | 206 | 19.05 s | 1.263 s |
| `terraform-aws-modules/terraform-aws-vpc` | 49 | 54 | 54 | 54 | 7.97 s | 2.931 s |
| `isovalent/terraform-aws-vpc` | 13 | 13 | 13 | 13 | 2.82 s | 0.247 s |
| `astral-sh/ruff` | 88 | 108 | 107 | 105 | 204.97 s | 11.160 s |
| `pallets/flask` | 20 | 23 | 23 | 23 | 7.36 s | 0.706 s |
| `open-webui/open-webui` | 2472 | 2473 | 2473 | 2473 | 80.89 s | 5.450 s |
| `facebook/create-react-app` | 33 | 55 | 55 | 55 | 27.73 s | 0.816 s |
| `sindresorhus/awesome` | 19 | 21 | 21 | 21 | 3.48 s | 0.228 s |

The final total is **2,979 findings**: 0 P0, 98 P1,
2,510 P2 and 371 P3. Review time totals 361.76 seconds and discovery
23.43 seconds. These are sequential operational measurements under shared-host load, not an
isolated A/B benchmark. Memory and repeated context/cache measurements belong to the main release report.

## Changes explained

- The first independent run retained every original fingerprint. Its 54 additional findings were
  entirely native markdownlint-cli2 P3 documentation-style diagnostics: FastAPI +1, Terraform AWS
  VPC +5, Ruff +20, Flask +3, Open WebUI +1, Create React App +22, and awesome +2. These remain in
  the final report. They reflect optional native availability; no global suppression hides them.
- Ruff loses three false P2 findings relative to that first run: one claimed missing end-to-end
  coverage despite native verification evidence, and two Cargo toolchain setup failures previously
  misclassified as completed code findings. Actual Rust tests increase from 3 to 205 recognized
  files. The single native workspace command now includes `--workspace`; unconfigured Python
  compiler data no longer activates pytest. Explicit nested Python project boundaries remain
  eligible and report their missing execution image honestly.
- Open WebUI Hadolint `SC2006` retains its fingerprint and source but changes from P2 to P3 to
  preserve the scanner's style severity. No new P0/P1 findings appeared. Its large inherited
  volume remains: 2,251 backend Ruff diagnostics dominate the 2,473 findings. This audit does not
  equate each native lint diagnostic with a distinct product defect or certify the corpus clean.
- OpenTelemetry now schedules its C# test project, two Go test components and inline Rust tests.
  C#/Go remain `sandbox_unavailable` without acquisition receipts. Rust clippy/test genuinely run
  in Docker but cannot resolve cached `actix-web`. Both are `tool_error`/`incomplete`, not findings.
  Explicit policy-aligned Cargo offline mode removes the two 120-second download waits.
- The known OpenTelemetry `compose.yaml: invalid Compose inventory` graph diagnostic persists.
  Other unavailable scanners/dependencies and partial analysis remain visible. No native Java
  or .NET success is claimed by this public corpus; generated-project validation supplies separate
  real native build/test evidence.

## Execution and limitations

All 49 actual sandbox executions in the final reports used Docker with no network, read-only
targets, resource enforcement and confirmed teardown. Missing-image statuses record `executed: false`;
they are excluded from that execution count. No corpus target code executed on the host. Rust Ruff
still requests toolchain 1.98.0 while the selected managed image contains 1.98.1: rustup cannot
install the requested toolchain into its read-only image. All three Cargo operations report that
toolchain prerequisite as incomplete. The filesystem policy was not weakened to make them pass.

Native tool acquisition, project dependencies and policy-compatible working storage remain explicit
operator prerequisites. Successful project generation and its controlled writable native verification
do not guarantee that arbitrary unprepared upstream repositories can build during read-only review.
Sandbox startup, scanner runtime, missing dependencies, and timeouts dominate some review timings.

## Exact commands and evidence

```bash
.venv/bin/python benchmarks/corpus.py \
  --clones /private/tmp/blueprint-ai-capability-audit.jJxr3g/repos \
  --output /tmp/blueprint-redteam/corpus-final --review
.venv/bin/pytest tests/test_redteam_tools.py tests/test_adapters.py -q
```

Native offline process capture used `blueprint_ai.sandbox.execute` on the pinned OpenTelemetry root
with `cwd="src/shipping"`, Docker image `blueprint-tools/rust:1.98.1`, default no-network/read-only
policy and a 30-second bound. Commands were `cargo clippy --quiet -- -D warnings` and
`cargo test --quiet`. The adapters were then executed against the same unchanged snapshot.
Raw and normalized evidence is retained in `/tmp/blueprint-redteam/cargo-offline-native.json` and
`/tmp/blueprint-redteam/cargo-offline-adapter.json`; each finishes in under one second.

- Final summary SHA-256: `83e893dc0fc3b2e9cf378db0af696398435c2066789d95e58b7fec0314764548`.
- All nine final reports identify analyzer source SHA-256: `d5986b3139552a5310ef83071c5590b949873b1c72c7dfac97df0cb6679f193c`.
- Final report JSON and context files: `/tmp/blueprint-redteam/corpus-final/`.
- Original evidence: `docs/phase-6-evidence.json`, independently matched against
  `/tmp/blueprint-phase6-corpus-release/summary.json`.
- Independent checkpoints: `/tmp/blueprint-redteam/corpus/`, `corpus-corrected/`, and
  `corpus-before-cargo-offline/`.
- The final release commit and broad gates are recorded in [the release report](phase-6-redteam.md).
