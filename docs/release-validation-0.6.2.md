# Blueprint AI 0.6.2 release validation

Prepared 2026-09-12 from released `0.6.1` commit
`288c0173aa683dd9f8c1efdeef4713b24a9161df`. This report covers only the distribution, publication,
and model-validation delta; the 0.6.1 and Phase 6 reports retain the broader platform evidence.

## Distribution and release design

PyPI's authoritative project JSON reports `blueprint-ai` as an unrelated distribution. Queries for
the valid alternatives `blueprint-ai-cli`, `blueprint-ai-tool`, `blueprint-ai-toolkit`, and
`blueprint-ai-dev` returned no project. `blueprint-ai-cli` was selected because it is the shortest
clear name that preserves the established `blueprint-ai` command and `blueprint_ai` import.
Availability was observed immediately before release; registry publication is the only reservation.

The tag-only release workflow uses reviewed commit-pinned actions and per-job permissions. It checks
tag/source/CLI/archive metadata agreement, builds once, smoke-tests separate fresh wheel and source
installs, creates SHA-256 checksums and the existing SPDX SBOM, then attests both provenance and SBOM.
Immutable workflow artifacts feed GitHub Release creation and the optional publisher. Release
creation requires the pre-existing remote tag and fails instead of moving or creating one.

PyPI publication uses the PyPA publishing action with only job-scoped `id-token: write`, the `pypi`
GitHub environment, and artifacts from the completed GitHub Release path. It is gated by the
repository variable `BLUEPRINT_PUBLISH_PYPI=true`; leaving the variable unset intentionally skips
publication rather than failing an otherwise valid GitHub Release or weakening authentication.

Before tagging, repository release immutability was enabled and verified through GitHub's repository
API. The `pypi` environment was created with required approval by the repository owner and a tag-only
`[0-9]*.[0-9]*.[0-9]*` deployment policy. The publication variable remains unset.

The remaining registry setup is exact and small because PyPI was not authenticated on this host:

1. Create the `blueprint-ai-cli` pending publisher on PyPI for owner `pradeeptathineni`, repository
   `blueprint-ai`, workflow `release.yml`, environment `pypi`.
2. Set repository variable `BLUEPRINT_PUBLISH_PYPI` to `true`, then rerun the skipped publish job or
   issue the next authorized release tag. No PyPI token or repository secret is required.

TestPyPI is not in the production tag workflow: it needs a separate trusted identity, does not reserve
the production name, and would add a second public side effect without improving artifact validation.

## Model and agent boundary

No `OPENAI_API_KEY` was present in the release environment, so no live invocation, latency, token,
cache, cost, or incremental-quality claim is made. The controlled provider gate verifies official
Responses API construction, strict structured output, `store=False`, zero SDK retries, bounded output,
operator-owned model/reasoning/timeout settings, actual/cached/reasoning token capture, call limits,
estimated context budgets, cache hits, malformed/failed response degradation, secret redaction,
untrusted repository delimiters, and no-model/offline operation. Deterministic findings survive model
failure, and `--no-model` makes zero provider calls.

The provider call shape was checked against the current official Responses API reference and the
installed OpenAI SDK signature. `gpt-5-mini` remains the documented cost-sensitive default because it
supports the Responses endpoint and structured outputs; operators can select another supported model
and reasoning effort without changing the release.

`ModelProvider` remains semantic inference over bounded evidence. A future `AgentBackend` would hand an
explicit plan to a coding-agent harness and verify its changes; it is not an ordinary model provider.
No Codex or other coding-agent orchestration was added.

## Candidate gate

The final release gate records formatting, lint, types, tests/coverage, dependency audit, schema and
generated-document parity, workflow static analysis, package content checks, fresh installations,
CLI smoke, self-review, local model regressions, tag/main identity, hosted CI, release assets,
checksums, and attestations. Exact final commit, hashes, and hosted URLs are reported out-of-band so
recording them cannot alter the source being verified.

Pre-release self-review in both untrusted and explicitly trusted project modes reported no P1 or P2
findings. Its release-specific P3 link warning is expected until this new document reaches `main`;
the other P3 findings are pre-existing function-size heuristics and a cache-detection heuristic, not
distribution regressions.

Actual remaining limitations: live OpenAI provider quality is unmeasured without an API credential;
PyPI publication remains pending until the trusted publisher identity is configured. Historical
Podman/runsc, filtered-egress, writable bind-mount quota, and native Windows OCI limitations are
unchanged by this release.
