# Independent 0.6.0 release red team

Started 2026-09-12 from `6c376b9c4ee10eaf7ec8f63c580db18b8003de43` on
`codex/phase-6-ecosystem-sandbox`. This audit independently challenged the completed candidate,
corrected demonstrated defects, and added regression evidence. No Phase 7 work, additional project
family, registry tool, cloud deployment, remote push, package publication, or tag change was performed.

The original candidate's zero-P0–P2 self-review conclusion was disproved. Final release classification
and gate measurements are recorded in the completion section below and the accompanying
[machine-readable evidence](phase-6-redteam-evidence.json). The three focused audits retain details:
[sandbox](redteam-sandbox.md), [tools](redteam-tools.md), and [genesis/capabilities](redteam-genesis.md).

## Git and baseline

The starting working tree was clean. Local `main`, `origin/main`, and the released `0.5.0` tag resolve
to `60e0c41732e513d54353933c3e2882b54161e340`. The local annotated `0.6.0` tag object is
`ecd146d4890e5b1829c200e90c3683d962cc9c66`; its peeled commit is the starting candidate. Remote refs
contained neither the Phase 6 branch nor `0.6.0`. No remote branch contained candidate changes.
Historical released tag objects were inspected and left unchanged.

The Phase 6 baseline diff touched 57 files, adding 20,843 and removing 497 lines, including its large
historical evidence file. The independent initial suite reproduced 255 passing tests and one skipped
live OCI test, with 4,301/5,461 statements covered (78.758%). Docker Desktop was available; neither
Podman nor runsc was available. Tool availability was inventoried before testing. No supported API
credential was present; only credential-presence booleans were inspected.

Corrections make the retained local `0.6.0` tag stale. Release finalization must recreate that
never-published tag on the final verified audit commit. The audit itself does not move it.

## Architecture and serious findings

The registry, component graph, capability resolver, shared provider/operation contracts, sandbox,
and transaction engine are useful shared abstractions. CLI/API/library variants contain meaningful
behavior and native tests; they are not merely aliases. However, aggregate coverage concealed
boundary bugs, parser assumptions, duplicated discovery logic, and shallow packaging checks.

| Area | Demonstrated problem | Correction |
| --- | --- | --- |
| Host filesystem | Non-Git `trap.yaml` FIFO hung discovery; symlinked package identity read a synthetic outside-root value | Shared regular-file inventory and bounded reads; unsafe or malformed built-in checks report partial |
| File-read races | A validated path could be replaced by a FIFO or outside symlink before open | Nonblocking/no-follow descriptor opens, fstat bounds, POSIX pinned parent descriptors |
| Sandbox evidence | Successful process output survived failed/malformed container inspection | Fail closed while guaranteeing cleanup; teardown failure remains explicit |
| Podman defaults | Host configuration, secret mounts, recursive binds, hooks, and writable temporary mounts could alter policy | Neutral configuration/auth/mount/hook inputs and explicit mount/temp/environment controls; live limitation retained |
| Transactions | Rollback followed a leaf symlink and deleted its referent; later failure could delete concurrently edited output | Reject symlink components; rollback only matching owned bytes; preflight every receipt row before mutation |
| Scanner integrity | Malformed envelopes, partial reports, unsupported parser shapes/exits and blanket suppressions hid errors or findings | Validate native contracts, preserve incomplete status, correct parsers/exits and narrowly justify suppressions |
| Model privacy | Quoted JSON secrets, quoted passwords and non-HTTP credential URLs escaped redaction | Key/value tokenization, complete/truncated-value handling, credential URI and private-key filtering |
| Model schema/cache | Open-ended objects and misplaced schema references broke strict output; malformed cache envelopes could pass or crash | Closed semantic-only wire schema, complete-response checks and typed cache-envelope validation |
| Model authority | Cached findings could forge deterministic provenance; deduplication could promote deterministic severity using model evidence | Normalize every cache/provider finding and separate model aggregation/baseline identity |
| Report truth | JUnit could count suppressed findings as failures or show partial concerns as success; SARIF omitted execution completeness | Consistent active counts, skipped partial concerns, SARIF invocation status and sandbox evidence |
| Changed-file scope | New directories collapsed into a directory marker; renames/newlines were parsed incorrectly; clean trees retained file findings | NUL-safe Git parsing, individual untracked paths, explicit invalid-ref failures and exact scope filtering |
| Native evidence | Go tests/stdlib-only modules, NuGet lock files and Django client tests produced false missing-capability findings | Reuse graph test scope and recognize native lock/test evidence |
| Test execution ownership | Inline Rust and Cargo workspace tests, C# test projects and Java source packages could lose native test invocation; incidental compiler data could trigger foreign test runners | Use graph verification evidence, explicit native project boundaries, workspace ownership and native Java source roots |
| Generated artifacts | Django's wheel omitted its application; its generic pytest review failed despite native Django tests passing | Package actual modules, verify an isolated installed-wheel health request, configure and require both test runners |
| Scaffolding/publication | Test scaffolding followed unsafe package metadata; eligible oversized generated source could disappear from publication | Bounded typed manifest reads with conflict results; strict bounded generated-source inventory rejects publication before mutation |

Independent peer review rejected an initial redaction correction that had quadratic regex behavior.
The final tokenizer completes 200,000-character adversarial identifiers in approximately 0.03 seconds,
and subprocess-bound regressions protect both ordinary and repeated-secret-word shapes. The report
records the final correction rather than treating an intermediate passing test count as sufficient.

Remaining design concerns are bounded starter depth, large recipe/executor functions, optional
scanner prerequisite management, and coarse repository-level heuristics. Native checks establish
starter behavior, not production quality, every composition, or exhaustive security coverage.
No broad framework rewrite or unrelated dependency was added. The generated Django development
fixture adds pytest-django because a real generated-project test failed without that integration.

## Project-family and tool claims

All 31 initializable families were independently generated with real native or declared built-in
verification. The [family-by-family classification](redteam-genesis.md) identifies the actual contract.
Kubernetes is partial: Namespace-only generation and structural checks. Pulumi is partial: native
empty-program initialization and selected cloud SDK installation, with no SDK resource API, preview,
state or deployment verification. The other families are supported within their documented starter
contracts; infrastructure support remains local and requires no account or provisioning.

The canonical registry and generated support table now distinguish 52 IDs: 47 review integrations,
OpenTofu genesis verification, Cargo toolchain acquisition, pre-commit kit verification, and two
explicitly deferred entries (PHP lint and Kubescape). Twenty-six managed entries share 17 image
references. The [complete inventory](redteam-tool-inventory.json) includes acquisition, provenance,
version, invocation, parser, bounds, trust, network, platforms and failure states for every entry.

Fourteen clean → controlled defect → repair scanner cycles passed: 42 executions, of which 36 used
Docker and six used trusted native scanners against controlled fixtures. These cover the original
eight managed OCI scanners plus gofmt, Terraform formatting, Biome, KubeLinter, actionlint and
ShellCheck. CloudFormation/Spectral fixes have authoritative native contract coverage. No optional
scanner is certified merely because its name is registered.

## Sandbox, hostile input and platforms

Live execution used Docker Desktop 29.1.3, Linux amd64 containers on macOS, with the VM boundary
explicit. Thirteen hostile/boundary process executions exercised read-only and controlled writable
targets, outside-file/symlink canaries, credentials/SSH-agent/socket absence, private/metadata/public
egress denial, internal loopback versus host loopback, explicit HTTPS, timeout, OOM, PID exhaustion,
CPU quota, scratch/file/output bounds, lifecycle-script suppression, cache poisoning and detached
child teardown. All test-owned containers reported teardown; unrelated containers were not deleted.
At a 0.25 CPU quota, a three-second sample consumed about 0.75 CPU seconds. Scratch capacity was
16 MiB when configured to 16 MiB.

POSIX read-race fixtures cover leaf/parent substitution and FIFOs. Unit/hostile fixtures also cover
archives, path traversal, option-shaped filenames, binary/large inputs, malicious config, prompt
injection, malformed/truncated scanner output, cache poisoning, Git edge cases and rollback drift.
No deliberately dangerous target payload executed outside an isolation boundary.

Podman and runsc remain environment-dependent and live-unverified. No Podman VM, nested privileged
runtime or remote runner was provisioned to manufacture coverage. Native Windows was unavailable;
the existing repository workflow has no dispatchable Windows job, and auditing an unpublished local
commit through GitHub Actions would require remote changes excluded by this task. Mocked Windows
branches and Linux containers are not native Windows verification.

Default network denial still permits container-internal loopback. Authorized normal networking
permits private destinations. No destination allowlist or writable-stage total disk quota exists.
Output capture is bounded to 4 MB per stream by default. Host execution remains explicitly trusted
and lacks OCI filesystem/network/resource guarantees. Native Windows lacks the POSIX descriptor-race
validation claim. These limitations are stated in the support/security/sandbox documents.

## Browser, capabilities and AI

Playwright 1.62.1 drove real Chrome 152.0.7977.83 against independently generated React/Vite and
Python-backed full-stack applications. Fresh dependency installation in bounded Docker application
containers required no source repair. Browser checks covered render, narrow viewport, real backend
health, keyboard activation, failure presentation and recovery, with no page errors. The generated
apps and browser contexts were stopped afterward. This validates sampled journeys, not universal
browser test generation or exhaustive UI coverage.

All sixteen kits passed live dry-run, create/parse, conflict preservation, idempotence and exact-tree
rollback checks. Native pytest, Node test and Terraform verifiers ran through OCI. Pre-commit and
Spectral remained explicitly incomplete without a selected image. Missing optional verification does
not silently claim native success. Failed actual verification, truncated output, malformed receipts,
symlink replacement, oversized drift and concurrent edits have regression coverage.

Live model calls were zero: no supported API credential was available. Controlled providers verified
structured requests/responses, cache hits/misses/poisoning, token/context and call budgets, latency
metrics, secret redaction, injection-as-data, authority separation and deterministic degradation.
No live semantic-quality or cost improvement is claimed. Genesis remains deterministic; semantic
naming and ambiguous-intent model generation are outside its current implemented contract.

## Regression and performance interpretation

All nine pinned public repositories were reviewed again without changing source bytes. They cover
Ruff, the FastAPI full-stack template, OpenTelemetry's polyglot demo, Open WebUI, two Terraform VPC
projects, Flask, deprecated Create React App and documentation-only awesome. Initial independent
comparison retained every previous fingerprint; 54 added findings were P3 Markdown-style diagnostics
from native scanner availability. No speculative global suppressions were introduced to hide them.
Corrected final counts and source snapshots are retained in the completion evidence.

Discovery, context construction, memory/read bounds, repeated context caching and sandbox startup
were measured again. Native generator/build/scanner time dominates large checks. Concurrency and
cold/warm dependency state affect timings; this audit makes no general speedup claim. The final
measurements below describe operational samples, not formal runtime or resource guarantees.

| Sample | Files | Discovery median | Context median | Traced Python peak |
| --- | ---: | ---: | ---: | ---: |
| Tiny OpenAPI starter | 6 | 51.3 ms | 3.0 ms | 1.93 MiB |
| Python API | 14 | 62.7 ms | 9.5 ms | 1.95 MiB |
| OpenTelemetry monorepo | 603 | 1,311.7 ms | 247.1 ms | 4.00 MiB |
| Ruff public repository | 10,371 | 14,605.3 ms | 3,601.7 ms | 23.12 MiB |
| Generated full-stack/client | 40 | 146.0 ms | 45.3 ms | 2.11 MiB |

Each row uses three repetitions. Context read at most 23 files and 12,000 estimated input tokens;
a second context build reused all file reads. Discovery includes graph construction. Tracemalloc
measures controller allocations, not child-process or VM RSS. Full public scan timings are reported
separately in [the corpus audit](redteam-corpus.md). The complete tool/sandbox doctor took 647 ms.
Five warm empty-process samples had medians of 75.4 ms on the trusted host and 774.1 ms in Docker,
an approximately 699 ms per-process isolation cost. This overhead is material for very short tools
but acceptable relative to native builds and scans; no safety checks were removed to improve it.

One material native-tool delay was corrected: OTel's Cargo Clippy and test commands each exhausted
120 seconds retrying downloads inside a no-network sandbox. Cargo now receives explicit offline
configuration for `none`/`loopback`, consistent with the existing uv policy. The same commands return
missing-dependency evidence in 548–606 ms; adapter runs take 563–587 ms and report incomplete analysis
with zero fabricated findings. Authorized normal networking remains online. Both generated Rust
families were regenerated afterward: twelve operations passed and seventeen source hashes matched.

## Final completion gate

**RELEASE READY WITH DOCUMENTED LIMITATIONS.** The original tagged candidate required corrections.
The corrected product source digest is
`d5986b3139552a5310ef83071c5590b949873b1c72c7dfac97df0cb6679f193c`.
Use the audit completion commit for release finalization; the retained `0.6.0` tag still names the
original candidate. The exact final Git identity, rebuilt distribution hashes and post-commit install
results are retained in `dist/validation.json` and `dist/SHA256SUMS`, outside the source archive to
avoid a self-referential artifact hash.

- Native full suite: **382 passed**, no skips, with deprecation warnings treated as errors; 78.23s.
  Statement coverage: **4,747 / 5,820 = 81.5636%**, up from 78.758% at the starting candidate.
- Linux full suites: Python **3.12.14, 3.13.15, 3.14.7**, each **375 passed and seven opt-in OCI skips**,
  with zero failures/errors. Live OCI checks ran separately through Docker on the macOS controller.
- Fresh independent wheel/source environments: **12 successful combinations, 252 CLI assertions**.
  Native macOS Python versions were **3.12.10, 3.13.7, 3.14.7**; Linux versions are listed above.
  Checks include CLI, doctor, all schemas, support/tool planning, genesis, no-model read-only review,
  capability planning/application, exact rollback and unavailable-boundary failure. Final artifacts
  are rebuilt and the same installation gate repeated after the audit commit.
- Formatting, lint, mypy, all seven JSON Schemas, generated-support parity, wheel and sdist passed.
  Every packaged Python source matched the corrected checkout; no Git/environment/cache directories
  were packaged. Dependency audit reported no known vulnerabilities; only the unpublished
  `blueprint-ai` distribution itself is absent from PyPI and cannot be audited there.
- Final public corpus: **nine unchanged repositories, 2,979 findings**, with each change explained in
  [the corpus report](redteam-corpus.md). All 49 actual final corpus sandbox executions retained
  Docker isolation, read-only source, no network, resource limits and teardown. Five Cargo prerequisite
  failures are explicitly incomplete. The local review retains thirteen P3 function-size observations;
  no known P0–P2 defect remains after the independent corrections and peer review.

The remaining limitations are native Windows, live Podman/runsc, live model-provider behavior,
selected unavailable scanner/kit prerequisites, limited Kubernetes/Pulumi starters, no deployed cloud
or cluster validation, no writable-stage total disk quota, and sampled rather than exhaustive browser
coverage. These do not enlarge the implemented product guarantees. No push, publication, deployment,
merge to `main`, historical tag mutation or Phase 7 work occurred.

### Exact verification commands

Run from the checkout with the named images and native Python interpreters available. Output
locations below identify this audit; use fresh locations when repeating it. Full scanner, browser,
image-build and capability prerequisites are documented in the three focused audit reports.

```sh
.venv/bin/uv sync --extra dev --extra model --locked
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src tests benchmarks
.venv/bin/pip-audit
BLUEPRINT_SANDBOX_TEST_IMAGE=python:3.12-slim-bookworm \
BLUEPRINT_SANDBOX_NODE_IMAGE=node:24-bookworm-slim \
BLUEPRINT_SANDBOX_EVIDENCE_DIR=/tmp/blueprint-redteam/sandbox-stable \
.venv/bin/pytest -o addopts='' -q -W error::DeprecationWarning \
  --junitxml=/tmp/blueprint-redteam/tests-final.xml --cov=blueprint_ai \
  --cov-report=term-missing --cov-report=json:/tmp/blueprint-redteam/coverage-final.json
.venv/bin/python benchmarks/redteam_platforms.py --linux-tests \
  --output /tmp/blueprint-redteam/linux-suite-final
.venv/bin/python benchmarks/phase6_matrix.py --network --compositions \
  --output /tmp/blueprint-redteam-genesis-20260912
.venv/bin/python benchmarks/phase6_matrix.py --kinds rust-library,rust-cli --network \
  --output /tmp/blueprint-redteam-genesis-rust-offline-final
.venv/bin/python benchmarks/redteam_kits.py --output /tmp/blueprint-redteam-kits-final
.venv/bin/python benchmarks/tool_matrix.py --output /tmp/blueprint-redteam-tool-matrix-final.json
.venv/bin/python benchmarks/redteam_tools.py --native-tools /tmp/blueprint-redteam-native \
  --native-host --output /tmp/blueprint-redteam-native-matrix.json
.venv/bin/python benchmarks/corpus.py --output /tmp/blueprint-redteam/corpus-final \
  --clones /private/tmp/blueprint-ai-capability-audit.jJxr3g/repos --review
.venv/bin/uv build
.venv/bin/python benchmarks/redteam_platforms.py --installs \
  --output /tmp/blueprint-redteam/installs-final
.venv/bin/blueprint-ai support --markdown > /tmp/blueprint-redteam/support-final.md
cmp docs/support.md /tmp/blueprint-redteam/support-final.md
.venv/bin/blueprint-ai doctor --json
.venv/bin/blueprint-ai tools doctor
.venv/bin/blueprint-ai review . --profile production --no-model \
  --trust-project-executables --fail-on P1 --format json
.venv/bin/blueprint-ai review /tmp/blueprint-redteam/self-source-final \
  --profile production --no-model --sandbox docker --fail-on P1 --format json
```

The final strict self-review uses a disposable tracked-source copy including `.git`, so history
scanners inspect the corrected commit. A directory-only copy has different Gitleaks semantics and
can flag the known public FastAPI corpus SHA without matching its historical fingerprint exception.
Source reviews and fresh installs are inspected, not inferred from the shell exit alone. The final
Git check compares local historical tag objects with remote refs and confirms the candidate remains
unpublished; it does not change refs.
