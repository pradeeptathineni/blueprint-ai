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
blueprint-ai doctor
```

Add `--json` to any command for machine-readable output. Optional `.blueprint-ai.yml` configuration can select blueprints/profiles, override tool paths, add ignores and set model limits. Defaults require no configuration.

## Blueprints and the deterministic-first principle

A blueprint combines applicability, deterministic checks and tool adapters, optional model review, safe remediation, and verification. V1 includes identity, repository, code quality, security, testing, IaC, CI/CD, documentation, architecture, and AI-context blueprints.

Each blueprint uses native or mature OSS tools first, small Blueprint AI rules second, and model reasoning only for questions deterministic evidence cannot answer. Missing optional tools produce partial results rather than aborting a run.

## End-to-end example

```bash
blueprint-ai inspect ./service
blueprint-ai review ./service --profile api --model off --json > review.json
blueprint-ai plan ./service --profile api --model off
blueprint-ai apply ./service --profile api
blueprint-ai verify ./service --profile api
```

`apply` currently proves the remediation contract with merge-aware repository baselines, README generation, and Python or JavaScript smoke-test scaffolding. Running it twice makes no second semantic change.
