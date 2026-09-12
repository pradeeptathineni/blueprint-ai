# Project genesis

`init` implements `intent -> name -> resolve -> plan -> initialize -> compose -> strengthen -> verify`.
It creates fresh projects only. Existing-project `plan`, `apply`, kits, and rollback keep their original
meaning. Common generation never calls a model. `catalog` exposes providers and capabilities;
`schema intent`, `schema genesis-plan`, and `schema project-graph` expose the strict interchange data.
The genesis-plan schema describes `init --dry-run` output, including its `plan_sha256` checksum.

## Families and compositions

The [generated support registry](support.md) is the authoritative family/provider matrix. It includes
Python, TypeScript, Go, Rust, C#, Java, frontend frameworks, and infrastructure foundations. `catalog`
and `support` expose the same data used by planning. Inspect a concrete `--dry-run` to see its exact
native generators and verifiers; unsupported option combinations fail before mutation.

`--ci` adds pinned GitHub Actions for supported family combinations. Python/Node compositions also
include Renovate configuration; hosting-app activation remains an operator action. Java, Next.js and
IaC CI combinations are currently rejected explicitly pending their own pinned workflow gate. `--container` adds a service image for API/full-stack kinds and a real
Docker build verifier. `--devcontainer` adds a separate non-root development image containing the
selected Python/uv and/or Node toolchain, with its own Docker build. The Docker verifier uses a local
Unix socket; a missing daemon is partial. No host credentials or Docker socket are mounted into images.
Core generation targets Windows. The 0.6.1 hosted gate exercises a representative built-in genesis
path natively on `windows-latest`; it does not extend the Docker verifier, which requires a local
Unix socket and does not support Windows named pipes. Dev Container editor attachment and
cross-platform image execution are not claimed as validated.

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
then rerun with `--sandbox docker --trust-providers --allow-network` after acquiring its images. When `--spec` is supplied, the spec controls intent;
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

Provider versions, package integrity, image references, sources, and licenses are held in the registry.
Use `tools plan PROVIDER` and `tools install PROVIDER` for explicit container acquisition. Native
host tools remain available through explicit trust. OCI receipts retain image IDs; native host
receipts retain observed versions and executable hashes. Lockfiles retain resolved dependencies.
Version tags and dependency ranges can change upstream; recorded provenance is not a hermetic-build
promise. See [tools](tools.md) for the lifecycle and [providers](providers.md) for extension contracts.

A pure dry run does not probe tools, access registries, create caches, or touch the destination.
`--trust-providers` permits listed providers, staged dependency builds, and generated verification
code to execute. `--allow-network` permits declared downloads/installations and final review scanners.
Lifecycle install scripts are disabled for npm; explicit generated build/test commands still execute.
No global tools are installed. Select `--sandbox docker`, `podman`, or `gvisor` for OS/container isolation. The default `auto` with
explicit provider trust preserves native host execution. OCI operations mount only staging writable,
use bounded scratch, and permit network only for declared network operations. Host execution reports
its weaker guarantees. See [the sandbox contract](sandbox.md).

Initialization or verifier failures discard staging and leave the destination absent. Missing or
unauthorized verification prerequisites can publish a clearly partial source project. Publication
uses an atomic no-replace directory rename, including concurrent empty-destination protection.
An existing destination, symlink ancestor, or modified serialized plan is rejected. Plans are data;
execution always recomputes commands from validated intent. No remote template hooks, repository
creation, publishing, cloud provisioning, or package release occurs.

Eligible generated source files are limited to 2,000,000 bytes each. Oversized source fails
generation before publication, rather than being silently omitted. Inventory hashing and native
JSON/YAML validation use bounded, no-follow reads; transient dependencies/build output remain excluded.

Exit codes: `0` verified, `3` partial, `2` failed or invalid input. Inspect the JSON result even when the
command exits successfully: review assurance and improvement findings are separate from verifier status.
`.blueprint-ai/genesis.json` owns the receipt; `.blueprint-ai/review.json` records the final review.
Environments (`.venv`, `node_modules`) and transient build output are not published: follow each
component README to reinstall from its lockfile. Review later imports only hash-matching generated
contract relationships; a receipt never grants execution trust or overrides deterministic discovery.

## Cloud and platform baselines

`--kind terraform`, `opentofu`, or `pulumi` accepts `--cloud aws|azure|gcp`. Terraform/OpenTofu declare
provider requirements and run format, init with `-backend=false`, and validate. Pulumi uses native
`new --generate-only` and TypeScript checking; its empty program does not exercise cloud SDK resource
APIs and is classified partial. No starter declares billable resources, configures a
remote backend, previews, plans, applies, or deploys infrastructure. Supply account/state decisions
explicitly in your own later workflow. Kubernetes starts with a Namespace and bounded structural
checks; Helm uses its native chart generator/linter/renderer; Kustomize uses its native local build.
Full Kubernetes schema/security validation remains an optional scanner result.

## Boundaries

Capabilities resolve dependencies and conflicts topologically. Artifact claims reject incompatible
owners, and receipts map published source to components. Existing-project `add` shares remediation's
create-only transactions. There is no generic template merge DSL, migration engine, or background
agent. The registry records deliberate framework/cloud/provider deferrals. Generated API checks cover
health behavior, frontend tests cover initial rendering, and neither implies complete business tests,
browser journeys, deployment security, or production readiness.
