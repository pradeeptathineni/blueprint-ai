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

`.blueprint-ai.yml` controls a per-run model budget and optional per-blueprint budgets. The context
contains compact facts, normalized findings, an index, a symbol/dependency map, and only ranked
snippets. Git changed-file mode prioritizes relevant diffs. Cache keys include the model, system
contract, blueprint, and exact redacted content hash. Reports expose estimated/actual token metrics
when available. Cached model output is validated against the same strict `Finding` schema.

## Evaluation

Unit tests use a deterministic fake provider to verify structured requests, cache behavior,
provenance, token bounds, secret redaction, and provider failure isolation. Versioned review cases in
`tests/evals/review_cases.json` record representative and adversarial expectations. Changes to prompt,
selection, redaction, or schemas must keep those cases and the full regression suite green.
