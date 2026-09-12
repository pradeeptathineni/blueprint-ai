# AI context contract

Blueprint AI uses a model only after deterministic project discovery, built-in checks, and optional
native/OSS tools. Model review is optional; `--model off` runs deterministic checks; semantic concerns remain explicitly partial.

## Trust and permissions

Repository files and tool output are untrusted data, never instructions. The model receives only a
blueprint-specific, size-bounded context with likely secrets redacted. It cannot execute tools or
write files. Model findings can inform a plan but model-proposed remediations are never auto-applied.
Network or intrusive security checks require an explicit `authorized_target`; only the bounded ZAP
baseline adapter consumes it. URLs in repository content do not grant authorization.

## Provider and structured output

The provider protocol is replaceable. The optional OpenAI provider uses the Responses API with strict
structured output, bounded output tokens, zero SDK retries, and `store=False`. Operator environment
variables select `BLUEPRINT_AI_PROVIDER` (`openai`), `BLUEPRINT_AI_MODEL` (default
`gpt-5-mini`), optional `BLUEPRINT_AI_REASONING_EFFORT` (`none`, `low`, `medium`, `high`, or
`xhigh`), and `BLUEPRINT_AI_MODEL_TIMEOUT` (greater than zero and at most 600 seconds). Repository
content cannot set these runtime choices. Each finding records provider/model provenance. Provider
failures make the result partial and never suppress deterministic results.

Responses record input, output, cached-input, and reasoning token counts when the service supplies
them. Cost remains unset unless a provider supplies authoritative cost data; Blueprint AI does not
embed a price table. `doctor --json` shows the effective non-secret runtime selection and explains
invalid or unavailable configuration.

## Budgets, cache, and reproducibility

`.blueprint-ai.yml` controls an estimated repository-context token budget, per-blueprint ceilings, maximum uncached
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

## Phase 5 retrieval and budget boundary

One `ContextBuilder` shares a read cache across concerns. It uses the component graph, verification
nodes, relevant paths, findings, and changed files to rank before reading. Each concern reads at most
24 new candidates, reserves source space after compact facts/map/findings, and removes duplicate
snippets. File reads are bounded at 2 MB and compressed to source excerpts/signatures. The character
ceiling is four times the allocated estimated context tokens. Component scopes exclude compiler
fixtures, generated outputs, and dependencies. No second full discovery occurs per model call.

The engine allocates 80% of the configured context budget across the smaller of applicable concerns
and the call ceiling; per-blueprint overrides cannot exceed that allocation. Output tokens are bounded
separately. Estimates exclude schema/system/transport overhead and are not a hard billing quota.
Actual provider usage is reported when supplied. No silent malformed-response repair call occurs;
default SDK retries are zero. Exhausted call budgets, missing models, and malformed results produce
partial concern analysis while retaining deterministic findings. Cache hits do not consume uncached
call capacity; cached input/output usage describes the original response, not a new billable call.

## Independent boundary corrections

The provider wire schema contains only semantic finding fields and closed objects, with root-level
references. It excludes mutation, suppression, and tool-authority fields. This follows the
[Structured Outputs contract](https://developers.openai.com/api/docs/guides/structured-outputs).
Incomplete responses fail explicitly. Cache envelopes and metric values are validated; cache hits
are normalized as model evidence again. Model and deterministic findings cannot share an aggregation
or baseline identity, so model severity cannot promote deterministic evidence.

Redaction recognizes quoted configuration keys, quoted/truncated values, private-key blocks, and
credential-bearing non-HTTP URLs. Key tokenization avoids quadratic regex behavior on hostile long
identifiers. It remains a likely-secret filter, not a complete data-loss-prevention guarantee.
Reports expose current `calls` and latency; cached token/cost fields describe the original response.
An allocation below 64 estimated context tokens is skipped with partial status instead of multiplying
a minimum allocation beyond the configured budget. No live API credential was present in the audit.

## Coding-agent boundary

Coding agents are not model providers. Blueprint AI sends bounded review context only through the
provider protocol and does not invoke Codex CLI, App Server, an SDK agent, or an autonomous patch loop.
The retained interoperability boundary is: Blueprint AI decides or validates, an explicitly chosen
agent may implement, and Blueprint AI verifies. Agent orchestration remains outside this release.
