# Blueprint AI

Create, inspect, review, strengthen, and safely evolve software projects with native tools and
deterministic checks. Optional model reasoning works from bounded evidence. Review works in any
folder; transactional evolution requires Git.

## Install

Python 3.12 or newer is required. The Python distribution is `blueprint-ai-cli`; it installs the
`blueprint-ai` command and the `blueprint_ai` import. From a checkout:

```bash
uv tool install .
blueprint-ai --version
blueprint-ai doctor
blueprint-ai review . --no-model
```

Install the published package with `pipx install blueprint-ai-cli` or
`uv tool install blueprint-ai-cli`. A GitHub Release wheel can also be installed directly:

```bash
pipx install https://github.com/pradeeptathineni/blueprint-ai/releases/download/0.9.1/blueprint_ai_cli-0.9.1-py3-none-any.whl
```

For development, run `uv sync --extra dev --locked`.

Optional preview model support is installed with `uv tool install 'blueprint-ai-cli[model]'` (or `'.[model]'`
from a checkout) and reads `OPENAI_API_KEY` from the operator's environment. Provider, model,
reasoning effort, timeout, call count, and context-token budgets remain operator controlled. Model
access is optional; `--no-model` makes no remote calls. See
[the AI context contract](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/ai-context.md).

## Usage

```bash
blueprint-ai inspect ./project
blueprint-ai impact ./npm-workspace --base-ref main --json
blueprint-ai impact ./python-workspace --workspace uv --base-ref main --json
blueprint-ai review ./project --no-model --format json
blueprint-ai repair prepare --project ./project --report /tmp/review.json \
  --finding <fingerprint> --output /tmp/repair-plan.json
blueprint-ai repair apply --project ./project --plan /tmp/repair-plan.json \
  --confirm-digest <plan-sha256>
blueprint-ai repair status --project ./project --plan /tmp/repair-plan.json
blueprint-ai support
blueprint-ai init ./new-service --kind python-api --dry-run --no-model
blueprint-ai add security-policy ./project
blueprint-ai tools install cargo
blueprint-ai evolve plan ./project --target rust/edition=2024 --output evolution-plan.json
blueprint-ai evolve apply evolution-plan.json ./project --dry-run \
  --sandbox docker --trust-project-executables
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

The local Settings page also offers System and six shared appearance presets, with validated
browser-only overrides. Theme changes preserve project drafts and reviewed previews. See
[browser appearance](docs/profiles.md#browser-appearance).

## Choose a workflow

- [Workspace change impact](docs/monorepo-impact.md): read-only npm and uv package and dependent check candidates.
- [Generated support registry](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/support.md): families, tools, availability, and capabilities.
- [Project genesis](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/genesis.md): naming, providers, cloud starters, and compositions.
- [Existing-project capabilities](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/capabilities.md): plan, add, verify, and rollback.
- [Project evolution](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/evolution.md): desired-state plans, supported transformations, safety, and exact rollback.
- [Template updates](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/template-update.md): preview and adopt pinned template changes while preserving authored files.
- [Tools and sandbox policy](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/sandbox.md): acquisition and execution boundaries.
- [Profiles](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/profiles.md): review selection, CI output, baselines, and suppressions.
- [Project settings](docs/profiles.md#project-settings): shared CLI and local browser controls, defaults, preview, persistence and reset.
- [Provider and extension contracts](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/providers.md): architecture.
- [1.x compatibility contract](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/compatibility.md): stable surfaces, deprecation, and exceptions.
- [Testing, threat model, and release gate](https://github.com/pradeeptathineni/blueprint-ai/blob/main/docs/releasing.md): verification and release operations.
- [0.9.1 release validation](https://github.com/pradeeptathineni/blueprint-ai/blob/0.9.1/docs/release-validation-0.9.1.md):
  1.0-readiness stabilization, compatibility fixtures, distribution, and publication evidence.

Review, plan and apply distinguish safe repair candidates from manual work. A no-change apply keeps
unresolved findings and incomplete coverage visible. Verification after a change names its scope,
while other pre-apply results remain explicitly unverified. See the
[finding-to-repair workflow](docs/profiles.md#from-findings-to-repair).

A finding, a failed tool, an unavailable prerequisite, and a successful check are distinct results.
Generated smoke tests establish basic behavior; they do not establish production readiness or
complete test coverage. Public package publishing and cloud deployment are separate explicit actions.
The single-finding reviewed repair flow is documented in [reviewed repair](docs/reviewed-repair.md).
