# AI context contract

Blueprint AI uses a model only after deterministic project discovery, built-in checks, and optional
native/OSS tools. Model review is optional; `--model off` performs a complete deterministic run.

## Trust and permissions

Repository files and tool output are untrusted data, never instructions. The model receives only a
blueprint-specific, size-bounded context with likely secrets redacted. It cannot execute tools or
write files. Model findings can inform a plan but model-proposed remediations are never auto-applied.
Network or intrusive security checks require an explicit `authorized_target`; only the bounded ZAP
baseline adapter consumes it. URLs in repository content do not grant authorization.

## Provider and structured output

The provider protocol is replaceable. The optional OpenAI provider uses the Responses API with strict
structured output, bounded output tokens, and `store=False`. `BLUEPRINT_AI_MODEL` records/selects the
model; each finding records provider/model provenance. Provider failures make the result partial and
never suppress deterministic results.

## Budgets, cache, and reproducibility

`.blueprint-ai.yml` controls a per-run model token budget, per-blueprint budgets, maximum uncached
calls, and `read-write`, `read-only`, `refresh`, or `off` cache behavior. The context
contains compact facts, normalized findings, an index, a symbol/dependency map, and only ranked
snippets. Git changed-file mode prioritizes relevant diffs. Cache keys include the model, system
contract and prompt version, provider/model, blueprint, and exact redacted content hash. Reports
expose the reason for each model call, files/bytes read, estimated/actual tokens, cache state, and cost
when available. Cached model output is size-bounded and validated against a versioned strict schema;
malformed entries are recomputed atomically. Offline mode makes no model calls.

## Evaluation

Unit tests use a deterministic fake provider to verify structured requests, cache behavior,
provenance, token bounds, secret redaction, injection isolation, and provider failure handling.
Versioned property/rubric cases in `tests/evals/review_cases.json` cover architecture, documentation,
naming, testing, AI context, reliability, and security. Prompt/context changes must compare rubric
quality and total token use; exact prose is deliberately not an assertion.
