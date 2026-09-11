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
blueprint-ai blueprints [NAME] [--path PATH]
blueprint-ai review [PATH] [--blueprint NAME] [--profile NAME] [--model auto|off|on]
blueprint-ai plan [PATH]
blueprint-ai apply [PATH]
blueprint-ai verify [PATH]
blueprint-ai baseline [PATH]
blueprint-ai kits
blueprint-ai rollback OPERATION_ID [PATH]
blueprint-ai bootstrap
blueprint-ai doctor
```

Use `--format json|markdown|sarif` on reviews (`--json` remains shorthand), and
`--changed --base-ref REF` for a Git-aware regression review. Optional `.blueprint-ai.yml` can
compose profiles, select blueprints, override tool paths, add ignores, set bounded workers/timeouts,
set run/blueprint model limits, define reasoned expiring suppressions, and choose a baseline path.
Defaults require no configuration.

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

## End-to-end example

```bash
blueprint-ai inspect ./service
blueprint-ai review ./service --profile api,production --model off --format sarif > review.sarif
blueprint-ai plan ./service --profile api --model off
blueprint-ai apply ./service --profile api
blueprint-ai verify ./service --profile api
```

Findings have message-independent fingerprints, aggregated sources, separate severity/priority,
confidence/provenance, baseline state, and suppressions. Record accepted current findings with
`blueprint-ai baseline PATH`.

`blueprint-ai kits` lists versioned testing/quality/CI/IaC/container/API/observability/OSS/AI-context
capability kits. `apply --kit NAME` uses atomic create-only writes, preserves existing files, emits an
operation manifest, and is idempotent. `blueprint-ai rollback OPERATION_ID PATH` removes only
unchanged files created by that operation. Generated tests are marked and immediately executed by the
verification pass. `bootstrap` prints official install guidance and never downloads executables.
