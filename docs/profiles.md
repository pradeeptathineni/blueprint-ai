# Review profiles and output

Profiles compose relevant blueprints without duplicating rules. Run `blueprint-ai blueprints` and
inspect the settings schema for the installed profile names. Common choices are `library`, `cli`,
`api`, `frontend`, `full-stack`, `iac`, `production`, `ai-app`, and `open-source`.

```bash
blueprint-ai review ./project --profile api,production --no-model --format sarif
blueprint-ai review ./project --changed --base-ref main --no-model
blueprint-ai baseline ./project
blueprint-ai schema settings
```

Repository configuration may select blueprints, profiles, bounded workers/timeouts, ignores,
reasoned expiring suppressions, baselines, and model budgets. It cannot authorize host execution or
change sandbox policy. Review supports JSON, Markdown, SARIF, and JUnit. Priority-based CI failure
counts new, active findings; partial coverage is reported independently and needs an operator policy.

Deterministic evidence precedes model judgment. Models are appropriate for architecture tradeoffs,
semantic consistency, test gaps, and project-specific recommendations. Parsers, schemas, compiler
results, graph facts, and tool diagnostics remain deterministic. Model calls require the optional
provider dependency and credentials; `--no-model` makes zero model calls. Context metrics record
bytes/files read, unique snippets, token budgets, calls, cache behavior, and latency. The current
validation report states whether live credentials were available.

Changed-file review requires usable Git comparison evidence and includes individual files in new
untracked directories. Invalid refs fail explicitly; a clean tree does not reintroduce file-scoped
findings. JUnit represents incomplete concerns without active findings as skipped, and counts only
active failures. SARIF includes execution completeness and per-concern tool/sandbox evidence.
