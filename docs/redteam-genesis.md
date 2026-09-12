# Independent genesis and capability audit — 0.6.0

This audit started from candidate `6c376b9`. Its purpose was to challenge existing support,
generation, packaging, and transaction guarantees. It added no family or registry tool. The
local `0.6.0` tag was not changed; the final audit report identifies the corrected release commit.

## Concrete corrections

| Finding | Correction and evidence |
| --- | --- |
| A create/rollback leaf symlink was resolved before checking whether it was a symlink. Rollback could delete its in-project referent. | Check every relative path component, including the leaf, before resolving. A controlled generated-file rename followed by a symlink replacement now preserves both the link and referent and reports a conflict. |
| A later create failure unconditionally deleted earlier created files, including concurrent user edits. | Every failure rollback checks the original hash. Concurrently edited files survive and are reported as conflicts. |
| Malformed operation-receipt rows could raise after earlier rows had already been rolled back. | Validate the entire receipt, including version, all changes, hashes, and directory-list types, before deletion. |
| Rollback hash checks read an arbitrarily enlarged replacement file into memory. | Use the existing bounded, no-follow regular-file reader. Oversized replacements are preserved as conflicts. |
| A kit verifier could accept a zero exit code with truncated evidence. | Timeout and truncation both fail verification and trigger hash-preserving rollback. |
| Exact-path conflict checks allowed a second Dev Container, Renovate or Semgrep configuration beside an existing equivalent filename. | Canonical kit metadata records known alternate files and embedded JSON sections. Planning, direct application and remediation all refuse the duplicate configuration without touching existing content. |
| Test scaffolding followed a package-manifest symlink, read it without a bound, and assumed parsed JSON was an object. | Use bounded no-follow reads and explicit mapping validation. Unsafe or malformed input produces a conflicted remediation without mutation or a partial-write exception. Python source-directory symlinks are also refused. |
| Source inventory hashed files without a final read bound and silently omitted oversized generated source through discovery's ordinary review limit. | Genesis opts into strict source-size enforcement and bounded inventory/schema reads. A controlled oversized source fails before publication; source growth after listing is rejected too. |
| Django's wheel built successfully but contained only the initial empty Python package, excluding its actual Django application. | Explicit wheel selection includes `project_config`. A new isolated wheel installation runs Python with `-I`, imports the installed WSGI application, and checks its real health response. |
| Generated Django's native tests passed, but Blueprint's pytest integration failed both discovered tests because Django settings were unconfigured. | Add the pytest-django development dependency and explicit settings configuration. Generation now requires both the native Django runner and pytest to pass. |
| Kubernetes/Kustomize accepted a 64-character Namespace name; plain Kubernetes generation reported it verified. | Reject names longer than 63 characters during planning. Namespace-only support is explicitly partial in the canonical registry. |
| Java package namespace labels such as `com.example` classified generated application and test sources as examples. | Recognize native `src/main/java` and `src/test/java` source roots before interpreting namespace suffixes. Enclosing example, fixture and vendor projects remain excluded; six scope and native-route regressions pass. |

Fourteen genesis/transaction regression cases cover all sixteen kits and the correction boundaries;
six additional cases cover Java source ownership. Native CI and
README command rendering now preserve argument boundaries with shell quoting; CI uses YAML
block scalars so complex verification arguments remain executable. This was necessary for the
new wheel verifier, rather than an additional workflow feature.

Kubernetes naming rules are documented in the [upstream object-name specification](https://kubernetes.io/docs/concepts/overview/working-with-objects/names/).
Django configuration follows [pytest-django's settings contract](https://pytest-django.readthedocs.io/en/latest/configuring_django.html).
Renovate alternatives follow its [documented configuration precedence](https://docs.renovatebot.com/configuration-options/#locations-for-configuration-filenames).

## Architecture and support boundaries

The family registry, provider metadata, component model, operation/result types, common sandbox
executor, staging directory, source inventory, and atomic no-replace publication provide shared
behavior. Language recipes retain native generators and verifiers. The distinct CLI/API/library
families add meaningful executable or HTTP behavior; they are not merely renamed output trees.
The templates remain small starter programs, with bounded sample coverage rather than broad
application certification.

There are 31 initializable families; additional recognition-only and deferred entries in the
registry do not increase that count. The support classifications below concern the shipped local
starter contract, not deployment, production readiness, arbitrary existing projects, or all
possible names and option combinations.

| Family | Actual support | Independently exercised contract |
| --- | --- | --- |
| repository | supported | Atomic baseline, source receipt and review |
| python-library | supported | uv init/sync, Ruff, mypy, behavioral pytest, wheel/sdist |
| python-cli | supported | Python checks plus actual CLI subprocess assertions |
| python-api | supported | Python checks, HTTP health/missing route, OpenAPI validation |
| typescript-library | supported | npm install, native lint, tsc, Node tests, package dry-run |
| typescript-cli | supported | TypeScript checks plus CLI subprocess assertions |
| node-api | supported | TypeScript checks, real Fastify injection, OpenAPI validation |
| react | supported | Vite generator, build/lint/render tests; actual browser rendering |
| full-stack | supported | Separate Python/Node backend and React frontend manifests; OpenAPI type generation; real Python-backed browser workflow |
| openapi | supported | Local OpenAPI schema validation; no deployed API implied |
| go-library | supported | Native module, formatting, vet, unit tests and build |
| go-cli | supported | Executable source, native Go unit tests and build |
| go-api | supported | Native Go checks plus HTTP handler and missing-route assertions |
| rust-library | supported | Cargo init, fmt, Clippy, test and build |
| rust-cli | supported | Native executable, shared behavior tests and build |
| csharp-library | supported | Native class library, restore, locked packages, build and xUnit |
| csharp-cli | supported | Native console application, restore, build and xUnit |
| dotnet-api | supported | Native ASP.NET application and real in-process HTTP integration tests |
| vue | supported | Vite generator, TypeScript build, server-rendering tests |
| svelte | supported | Vite generator, native build, server-rendering tests |
| django | supported after corrections | Native Django tests, pytest integration, wheel/sdist and isolated installed-app health verification |
| flask | supported | uv/Ruff, Flask test-client behavior, wheel/sdist |
| terraform | supported local foundation | AWS/Azure/GCP requirements, format/init/validate; no resources, state backend or deployment |
| opentofu | supported local foundation | AWS/Azure/GCP requirements, format/init/validate; no resources, state backend or deployment |
| kubernetes | partial | Namespace name/shape validation only; no cluster, general workload schema or admission assurance |
| helm | supported local foundation | Native chart creation, strict lint and rendering; no cluster execution |
| kustomize | supported local foundation | Local Namespace composition/build; no cluster execution |
| java-library | supported | Native Maven archetype and Maven verify |
| spring-boot | supported | Bounded official Initializr archive and actual HTTP integration tests under Maven verify |
| nextjs | supported | Native generator, production build, lint and rendering tests |
| pulumi | partial local foundation | Native generate-only starter, cloud SDK installation and empty-program TypeScript check; no SDK resource API, preview, state or resource verification |

Nx/Turbo task-engine generation remains deferred; the full-stack starter is a component
composition with independent manifests, not a workspace task-engine certification. AWS/Azure/GCP
rows prove local provider configuration or dependency installation, not account behavior. Pulumi
does not exercise SDK resource types; its emitted program only exports the selected cloud. CLI variants
whose native tests exercise shared logic do not claim exhaustive CLI semantics.

The architecture still contains language recipes and a typed `Kind` declaration alongside the
canonical registry. Existing consistency tests tie these together; no observed drift justified
a broad resolver rewrite during release verification.

## Independent matrix result

The independent matrix completed **45/45 verified cases and 282 declared operations** in
2,368.250 seconds of summed case time. It covered every initializable family, all nine
Terraform/OpenTofu/Pulumi cloud selections, both full-stack OpenAPI-type compositions, and six
CI variants. The initial matrix process started before corrections; Django and Django-CI were
then regenerated from corrected code with ten required operations each, both verified. Final
Django took 76.357 seconds; the corrected CI case passed through the actual CLI in 101.364 seconds.

The extra API/container/Dev Container case retained its honest partial OCI receipt, supplemented
by successful external image builds and controlled runtime checks. Across all 48 output trees
(the 45 matrix cases, two corrected Django reruns, and this extra case), **684 published source
file hashes matched their receipts**, and every output had the expected absence of Git metadata.
The integrity audit is `/tmp/blueprint-redteam-genesis-integrity.json`.

After the final sandbox change aligned Cargo offline mode with disabled networking, Rust library
and CLI generation were independently repeated. Both passed all six operations in 8.765s and
8.675s. The ten native Cargo invocations used Docker with network `none`, enforced resource
limits, complete output and successful teardown. All seventeen published source hashes matched
their fresh receipts; neither output contained Git metadata. This was an affected rerun, not an
additional family claim.

Manual review found false positives in the original Go testing/checksum, .NET lockfile, and
Django integration-test discovery. The parent audit corrected those shared discovery paths.
Fresh re-reviews of Go CLI, C# library, and Django removed the corresponding findings. Remaining
starter findings concerned explicit security/reliability guidance and browser tests absent from
generated source; the external browser audit does not silently manufacture source test coverage.

## Browser and generated container evidence

The browser audit used independently generated React and Python full-stack projects. Sources
were mounted read-only into bounded Docker application containers; dependencies and build state
lived in temporary container filesystems. `npm ci --ignore-scripts` and `uv sync --locked` worked
without editing generated files. The full-stack frontend retained its generated loopback proxy;
the backend shared that container network namespace. Only the Vite ports were published, bound
to host `127.0.0.1`.

Bundled Playwright 1.62.1 drove a fresh headless Chrome 152.0.7977.83 session. React rendered its
accessible heading/main at a mobile viewport. Full-stack interaction made a real request through
Vite to FastAPI, displayed `Service is healthy`, handled a separately intercepted failed request,
accepted keyboard activation, and recovered after interception was removed. Both cases had zero
page errors. Browser durations were 3.340s and 1.335s, excluding app installation/startup.
Screenshots were inspected. The standalone React starter has no interactive feature to exercise;
the actual button workflow belongs to full-stack. This does not add browser tests to generated
source or claim comprehensive user journeys.

An additional Python API with service-image and Dev Container capabilities correctly published
as partial under OCI generation: both nested Docker verifiers explicitly reported unavailable,
and no daemon socket entered a sandbox. The two generated Dockerfiles were then built directly
through the host's authorized Docker engine. The service image ran read-only with no network,
no capabilities, CPU/memory/process limits, UID 10001, and returned `{"status":"ok"}`. The Dev
Container ran as UID 10001/vscode and reported Python 3.12.14 and uv 0.12.13. Editor attachment
remains unverified. All audit app containers and the three temporary audit image tags were removed.

## Capability kits

The live audit exercised every kit: testing-python, testing-javascript, pre-commit,
github-actions-ci, terraform, container, api-contract, observability, oss-repository, ai-context,
security-policy, dependency-updates, secret-scanning, sast, devcontainer, and release-checklist.

All sixteen passed reviewable planning, asset creation/parsing, repeat-application idempotence,
exact file/directory rollback, and preservation of conflicting preexisting content. Actual
no-network Docker verification passed for pytest, Node's test runner, and Terraform fmt/validate.
Pre-commit and Spectral returned explicit unavailable-image `tool_error` results; these two
native verification paths remain incomplete in this kit run. Their configuration was not
misrepresented as a successful scan. Other kits intentionally provide declarative assets or
maintainer guidance, with no implied service activation. The SAST kit supplies one Python
dynamic-evaluation rule; it does not provide a general policy for all supported languages.

The live kit test used a temporary image installing pytest 9.1.1 on the existing uv/Python image.
It introduced no host Python dependency. Its image tag was removed after execution.

## Reproduction and evidence

The audit artifacts are local, session-specific evidence. Full operation receipts include
native commands, sandbox image IDs, output hashes, limits, network state, and teardown state.
Source hashes were checked against published source files; browser workloads did not change
their read-only inputs. No generated project was initialized as a Git repository or pushed.
After the final bounded-read correction, strict source inventory was repeated successfully on
all 48 published trees, and all sixteen live kits were rerun from the final source. The focused
genesis/remediation/Phase 2/Phase 6 regression gate passed 159 tests with one skipped; Ruff and
mypy passed for the changed genesis, discovery, capability and remediation code and audit tests.
The final peer review inventoried testing-adapter selection across all 45 generated trees.
It exposed omitted Rust inline tests, skipped C# test components, and the Java namespace issue.
After coordinated discovery and adapter corrections, Rust selects Cargo, C# selects the test
project's `dotnet test`, Java/Spring selects Maven, and Go/Python/JavaScript retain their expected
native routes. Java scope and Phase 6 checks passed 101 tests with one skipped. This selection
check complements the actual generation-time native suites; it does not substitute for them.

```sh
.venv/bin/python benchmarks/phase6_matrix.py \
  --output /tmp/blueprint-redteam-genesis-20260912 --network --compositions
.venv/bin/python benchmarks/phase6_matrix.py \
  --output /tmp/blueprint-redteam-genesis-django-final --kinds django --network
.venv/bin/python benchmarks/phase6_matrix.py --kinds rust-library,rust-cli --network \
  --output /tmp/blueprint-redteam-genesis-rust-offline-final
docker build -t blueprint-redteam/pytest:9.1.1 /tmp/blueprint-redteam-kit-image
.venv/bin/python benchmarks/redteam_kits.py --output /tmp/blueprint-redteam-kits-final
.venv/bin/pytest tests/test_redteam_genesis.py tests/test_genesis.py \
  tests/test_engine_remediation.py tests/test_phase2.py tests/test_phase6.py
.venv/bin/ruff check src/blueprint_ai/genesis src/blueprint_ai/remediation.py \
  tests/test_redteam_genesis.py benchmarks/redteam_kits.py
.venv/bin/mypy src/blueprint_ai/genesis src/blueprint_ai/remediation.py \
  tests/test_redteam_genesis.py benchmarks/redteam_kits.py
```

Output directories must be fresh. The kit command requires acquired node/Terraform images and
the temporary pytest image described above. Browser execution uses an existing Playwright and
Chrome installation, plus the controlled app containers:

The temporary pytest image's complete Dockerfile was:

```dockerfile
FROM ghcr.io/astral-sh/uv:0.12.13-python3.12-trixie-slim
RUN uv pip install --system --no-cache pytest==9.1.1
```

```sh
NODE_PATH=/path/to/installed/node_modules node benchmarks/redteam_browser.cjs \
  http://127.0.0.1:18081 http://127.0.0.1:18082 /tmp/blueprint-redteam-browser
docker build -t blueprint-redteam/api:0.6.0 /tmp/blueprint-redteam-genesis-containers
docker build -t blueprint-redteam/devcontainer:0.6.0 \
  /tmp/blueprint-redteam-genesis-containers/.devcontainer
```

Principal artifacts:

- `/tmp/blueprint-redteam-genesis-20260912/summary.json` and per-case receipts/reviews.
- `/tmp/blueprint-redteam-genesis-django-final/` and
  `/tmp/blueprint-redteam-genesis-django-ci-final.json` are the final ten-operation Django reruns.
- `/tmp/blueprint-redteam-kits-final/summary.json` with all sixteen final apply/rollback results.
- `/tmp/blueprint-redteam-strict-inventory.log` records the final 48-tree receipt comparison.
- `/tmp/blueprint-redteam-genesis-testing-route-final.json` records final native testing selection
  across all 45 independently generated cases.
- `/tmp/blueprint-redteam-genesis-rust-offline-final/` contains both affected Rust generation
  receipts after the final sandbox Cargo network-mode correction.
- `/tmp/blueprint-redteam-browser/results.json`, `react.png`, and `full-stack.png`.
- `/tmp/blueprint-redteam-django-pytest.log` records the original pytest settings failure.
- `/tmp/blueprint-redteam-genesis-containers.json` records explicit nested-Docker limitations;
  separate container/devcontainer build logs record successful artifact builds.

Native project build/test/container execution here was Linux x86_64 inside Docker Desktop on
macOS. Built-in asset composition and the bounded official Initializr download ran on the macOS
controller; Chrome also ran on macOS with an ephemeral browser profile. The Linux workloads
crossed a real VM boundary and do not establish native macOS or Windows toolchain certification.
The parent audit reports broader platform and release gates separately.
