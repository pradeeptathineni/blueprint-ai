# Blueprint AI

Blueprint AI is a deterministic-first toolkit for inspecting, reviewing, strengthening, and extending software projects. It works on any folder; Git is useful but optional.

## Architecture

The engine discovers compact `ProjectFacts`, selects applicable blueprints, runs native and OSS tools through typed adapters, normalizes and deduplicates `Finding` records, and only then uses optional bounded model judgment. Tool commands are implemented in code—not loaded from repository configuration. Safe remediations preserve existing files and are verified after application.

## Install

Python 3.12 or newer is required. With `uv`:

```bash
uv tool install .
```

For development:

```bash
uv sync --extra dev
uv run pytest
```

Optional model review uses the OpenAI Responses API with Structured Outputs:

```bash
uv tool install '.[model]'
export OPENAI_API_KEY=...
```

## Core commands

```bash
blueprint-ai inspect [PATH]
blueprint-ai review [PATH] [--blueprint NAME] [--profile NAME] [--model auto|off|on] [--no-model]
blueprint-ai plan [PATH]
blueprint-ai apply [PATH]
blueprint-ai verify [PATH]
blueprint-ai baseline [PATH]
blueprint-ai doctor
```

Use `blueprint-ai --help` for blueprint, kit, rollback, bootstrap, benchmark, and schema commands.
Use `--format json|markdown|sarif|junit` on reviews (`--json` remains shorthand), and
`--changed --base-ref REF` for a Git-aware regression review. Optional `.blueprint-ai.yml` can
compose profiles, select blueprints, override tool paths, add ignores, set bounded workers/timeouts,
set run/blueprint model limits, define reasoned expiring suppressions, and choose a baseline path.
Defaults require no configuration.

Project tool overrides, repository wrappers, and `node_modules/.bin` are never executed unless the
operator passes `--trust-project-executables` after reviewing the repository. `offline: true` disables
network-capable adapters and model calls while retaining deterministic analysis. `fail_on_priority`
provides a stable CI exit policy; only new, unsuppressed findings count.

DAST is opt-in: only an explicit credential-free `authorized_target` enables the bounded OWASP ZAP
baseline adapter. Discovered URLs never authorize a scan.

## Blueprints and the deterministic-first principle

A blueprint combines applicability, deterministic checks and tool adapters, optional model review,
safe remediation, and verification. Phase 2 includes identity/repository, code
quality/design/architecture, testing, API/data/config, security/supply chain,
Terraform/CloudFormation, containers, Kubernetes, CI/CD, reliability, operations, documentation, AI
context, and a best-practice completeness meta-check.

Applicability is explicit (`applicable`, `partial`, or `not_applicable`, always with a reason).
Profiles compose without copying rules: `library`, `cli`, `api`, `backend-service`, `frontend`,
`web-app`, `full-stack`, `iac`, `terraform`, `container`, `kubernetes`, `data-pipeline`, `ai-app`,
`ai-agent`, `open-source`, `portfolio`, and `production`.

Each blueprint uses native or mature OSS tools first, small Blueprint AI rules second, and model reasoning only for questions deterministic evidence cannot answer. Missing optional tools produce partial results rather than aborting a run.

Validated optional integrations include Gitleaks, OSV-Scanner, Trivy, actionlint, zizmor,
markdownlint-cli2, and Lychee. Language and platform routes also cover configured Ruff, mypy,
pytest, ESLint/Biome, TypeScript, Go, Rust, Java, ShellCheck, Spectral, Terraform, container, and
Kubernetes tools. Optional tools are never downloaded automatically. A tool that is missing,
disabled by the trust boundary, offline, fails, or times out is shown as incomplete—not as a pass.

## End-to-end example

```bash
blueprint-ai inspect ./service
blueprint-ai review ./service --profile api,production --no-model --format sarif > review.sarif
blueprint-ai plan ./service --profile api --model off
blueprint-ai apply ./service --profile api
blueprint-ai verify ./service --profile api
```

A compact review excerpt looks like:

```text
P1 supply-chain CVE-2021-23337 package-lock.json
   lodash 4.17.20; sources: osv-scanner, trivy; fixed version: 4.17.21
P1 ci-cd github-actions/template-injection .github/workflows/ci.yml:10
   sources: actionlint, zizmor
PARTIAL iac: checkov missing; terraform disabled until project executables are trusted
```

Findings have message-independent fingerprints, aggregated sources, separate severity/priority,
confidence/provenance, baseline state, and suppressions. Record accepted current findings with
`blueprint-ai baseline PATH`.

`blueprint-ai kits` lists versioned testing/quality/CI/IaC/container/API/observability/OSS/AI-context
capability kits. `apply --kit NAME` uses atomic create-only writes, preserves existing files, emits an
operation manifest, and is idempotent. `blueprint-ai rollback OPERATION_ID PATH` removes only
unchanged files created by that operation. Generated tests are marked and immediately executed by the
verification pass. Generated smoke checks prove only basic execution/layout; they never satisfy the
separate unit-test requirement. `bootstrap` prints official install guidance and never downloads
executables.

Project-local extensions are strict declarative YAML under `.blueprint-ai/blueprints/`; they cannot
define commands, imports, templates, or model calls. See [the extension contract](docs/extending.md).
JSON run metadata records schema and application versions, configuration hash, tool versions,
provider/model identity, prompt versions, mode, and elapsed time. Model context is bounded, redacted,
cache-keyed by content and prompt version, and clearly marked as untrusted repository data.

## Limitations and validation evidence

Model review requires the optional `model` extra and an `OPENAI_API_KEY`; deterministic analysis is
independent of model availability. Network-backed scanners and link checks depend on their local
databases or reachable targets, and target-controlled linters/tests require the explicit trust flag.
Large legacy lockfiles can legitimately produce many advisories, so priority and aggregated sources
should guide triage rather than raw finding count.

The [Phase 4 validation report](docs/phase-4-validation.md) records the controlled fixtures,
six-repository matrix, real strengthening case study, benchmarks, and release decision. See
[tool research](docs/tool-research.md), [the threat model](docs/threat-model.md), and
[release verification](docs/releasing.md) for maintained operational detail.
