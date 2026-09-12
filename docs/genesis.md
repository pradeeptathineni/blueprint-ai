# Project genesis

`init` implements `intent -> name -> resolve -> plan -> initialize -> compose -> strengthen -> verify`.
It creates fresh projects only. Existing-project `plan`, `apply`, kits, and rollback keep their original
meaning. Common generation never calls a model. `catalog` exposes providers and capabilities;
`schema intent`, `schema genesis-plan`, and `schema project-graph` expose the strict interchange data.
The genesis-plan schema describes `init --dry-run` output, including its `plan_sha256` checksum.

## Delivered kinds

| Kind | Native foundation | Generated verification |
| --- | --- | --- |
| repository | Built-in repository hygiene | Blueprint AI review |
| python-library / python-cli | `uv init --lib`, Hatch | Ruff format/lint, mypy, behavior and CLI tests, wheel/sdist build |
| python-api | uv + FastAPI/Uvicorn | Python checks, health/404 integration tests, OpenAPI validation |
| typescript-library / typescript-cli | `npm init`, TypeScript | Biome lint, compiler, Node behavior/CLI tests, package dry run |
| node-api | npm + Fastify | TypeScript checks, injected health/404 integration tests, OpenAPI validation |
| react | `create-vite@9.2.1 --template react-ts` | Native ESLint/compiler/Vite build, Vitest render test |
| full-stack | React plus Python or Node backend | Both components, dev proxy and contract; optional generated API types compile and behavioral tests |
| openapi | OpenAPI 3.1 health contract | `openapi-spec-validator`, Blueprint AI review |

`--ci` adds pinned GitHub Actions and a Renovate configuration; enabling Renovate on a hosting service
remains an operator action. `--container` adds a service image for API/full-stack kinds and a real
Docker build verifier. `--devcontainer` adds a separate non-root development image containing the
selected Python/uv and/or Node toolchain, with its own Docker build. The Docker verifier uses a local
Unix socket; a missing daemon is partial. No host credentials or Docker socket are mounted into images.
Windows can run core generation, but this release's Docker verifier requires a local Unix socket.
Dev Container editor attachment and cross-platform image execution are not claimed as validated.

Full stack uses independent component manifests and lockfiles under `backend` and `frontend`.
It does not introduce Nx, Turbo, or a second workspace task engine. Production routing, a database,
cloud infrastructure, authentication, deployment, and meaningful browser journeys require additional
project intent. The starter is a development foundation, not a production readiness certificate.

## Structured intent and naming

```yaml
schema_version: '1.0'
name: Invoice Service
kind: full-stack
backend: python
maturity: team
api_client: true
container: true
ci: true
devcontainer: true
license: MIT
```

Run `blueprint-ai init ./invoice-service --spec intent.yml --dry-run --no-model`, inspect the plan,
then rerun with `--trust-providers --allow-network`. When `--spec` is supplied, the spec controls intent;
kind/name/capability flags do not override it. Unknown keys, duplicate YAML keys, aliases, unsupported
kinds, and incompatible options fail validation. `maturity` and `license` are spec fields. The default
license is `UNLICENSED`; the tool does not grant rights on the user's behalf.
Choosing `MIT` creates a license template; fill in its year and copyright holder before publication.

Naming keeps display, repository, package, and Python import-module identities distinct. PyPA rules
permit normalized case/dot/underscore/hyphen equivalents; npm rules require legal lowercase package
identifiers. Reserved language/standard-library names and unsafe filesystem names are rejected.
The result separates syntax/ecosystem checks and normalization from availability (`not_checked`) and
semantic quality (`not_assessed`). Registry, domain, organization, and cloud-name availability are
not inferred from syntax. Natural-language intent interpretation and semantic name ranking are deferred.

## Providers and execution

Python generation targets a locally available Python 3.12 interpreter and uv `>=0.8,<1`; TypeScript needs Node `>=22.12` and npm
`>=9,<12` (validated with Node 24.19.0/npm 11.17.0). Vite and optional `openapi-typescript@7.13.0` run
through an ephemeral npm runner. Exact registry integrity metadata is checked before those providers
execute. Native executable hashes, observed versions, command results, output-evidence hashes, source
hashes, and Docker image IDs when produced are recorded. Dependency lockfiles retain resolved versions
and integrity. Upstream generators and version-ranged Python dependencies can change output; this is
provenance and version control, not a claim of bit-for-bit hermetic builds. Container base tags are
versioned but mutable; promote image digests under the project's own update policy.

A pure dry run does not probe tools, access registries, create caches, or touch the destination.
`--trust-providers` permits listed providers, staged dependency builds, and generated verification
code to execute. `--allow-network` permits declared downloads/installations and final review scanners.
Lifecycle install scripts are disabled for npm; explicit generated build/test commands still execute.
No global tools are installed. A controlled environment and disposable sibling stage isolate ordinary
files and caches; this is **not an OS sandbox**. Trust the installed tooling and selected publishers.

Initialization or verifier failures discard staging and leave the destination absent. Missing or
unauthorized verification prerequisites can publish a clearly partial source project. Publication
uses an atomic no-replace directory rename, including concurrent empty-destination protection.
An existing destination, symlink ancestor, or modified serialized plan is rejected. Plans are data;
execution always recomputes commands from validated intent. No remote template hooks, repository
creation, publishing, cloud provisioning, or package release occurs.

Exit codes: `0` verified, `3` partial, `2` failed or invalid input. Inspect the JSON result even when the
command exits successfully: review assurance and improvement findings are separate from verifier status.
`.blueprint-ai/genesis.json` owns the receipt; `.blueprint-ai/review.json` records the final review.
Environments (`.venv`, `node_modules`) and transient build output are not published: follow each
component README to reinstall from its lockfile. Review later imports only hash-matching generated
contract relationships; a receipt never grants execution trust or overrides deterministic discovery.

## Boundaries for future work

Capabilities resolve dependencies and conflicts topologically. Artifact claims reject incompatible
whole-file/semantic owners, and receipts map every published file to a component. Native manifests
and structured JSON contributions retain ecosystem ownership. There is no general template merge DSL,
SAT solver, migration engine, or background agent. Copier/remote templates, cloud generators, database
composition, additional languages/frameworks, codemods, and remote GitHub changes remain deferred.
Their future plans can reuse the graph, ownership, evidence, and verification contracts.
