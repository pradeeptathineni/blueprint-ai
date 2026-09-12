# Blueprint AI

Create, inspect, review, and strengthen software projects with native tools and deterministic
checks. Optional model reasoning works from bounded evidence. Any folder works; Git is optional.

## Install

Python 3.12 or newer is required. From a checkout:

```bash
uv tool install .
blueprint-ai --help
```

For development, run `uv sync --extra dev --locked`. Optional model support is installed with
`uv tool install '.[model]'` and reads `OPENAI_API_KEY` from the operator's environment. Provider,
model, reasoning effort, and timeout are operator-owned environment settings; repository content
cannot select them. See [the AI context contract](docs/ai-context.md).

## Usage

```bash
blueprint-ai inspect ./project
blueprint-ai review ./project --no-model --format json
blueprint-ai support
blueprint-ai init ./new-service --kind python-api --dry-run --no-model
blueprint-ai add security-policy ./project
```

Inspection and planning do not execute project code. Review defaults to a read-only OCI sandbox
for external tools. Without a local engine and acquired tool image, it reports incomplete tool
coverage and still performs deterministic analysis.

To acquire a tool explicitly and review with it:

```bash
blueprint-ai tools plan ruff
blueprint-ai tools install ruff
blueprint-ai review ./project --sandbox docker --no-model
```

To create a Python API using an acquired toolchain:

```bash
blueprint-ai tools install uv
blueprint-ai init ./new-service --kind python-api --sandbox docker \
  --trust-providers --allow-network --no-model
```

Generation uses a fresh staging directory, native generators, declared verifiers, and an atomic
publication step. It retains source, lockfiles, provenance, and a separate review report. Cloud
starters validate locally without provisioning resources or choosing remote state.

## Choose a workflow

- [Generated support registry](docs/support.md): families, tools, availability, and capabilities.
- [Project genesis](docs/genesis.md): naming, providers, cloud starters, and compositions.
- [Existing-project capabilities](docs/capabilities.md): plan, add, verify, and rollback.
- [Tools](docs/tools.md) and [sandbox policy](docs/sandbox.md): acquisition and execution boundaries.
- [Profiles](docs/profiles.md): review selection, CI output, baselines, and suppressions.
- [Provider contracts](docs/providers.md) and [extensions](docs/extending.md): architecture.
- [Testing](docs/testing.md), [threat model](docs/threat-model.md), and [release gate](docs/releasing.md).
- [Phase 6 evidence](docs/phase-6-validation.md): measured results and explicit remaining limits.
- [Independent release audit](docs/phase-6-redteam.md): candidate defects, corrections, and final gate.
- [0.6.1 maintenance validation](docs/release-validation-0.6.1.md): CI, Windows, provider, and
  sandbox follow-up evidence.

A finding, a failed tool, an unavailable prerequisite, and a successful check are distinct results.
Generated smoke tests establish basic behavior; they do not establish production readiness or
complete test coverage. Public package publishing and cloud deployment are separate explicit actions.
