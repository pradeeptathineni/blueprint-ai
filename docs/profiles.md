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

## Project settings

Inspect, search, preview, save or reset ordinary defaults from either the CLI or a local browser:

```bash
blueprint-ai settings show ./project --json
blueprint-ai settings show ./project --category Reports --changed
blueprint-ai settings set minimum_severity high ./project --dry-run --json
blueprint-ai settings set minimum_severity high ./project
blueprint-ai settings reset minimum_severity ./project
blueprint-ai settings serve ./project
```

Open the printed `http://127.0.0.1:PORT` URL manually. The page searches controls by name or
description, filters categories and changed values, and shows each default, saved effective value,
source and project scope. It requires a valid preview before saving. A lost save response requires
reloading saved settings before another edit. The server stops with Ctrl-C and starts no review.
Persistent defaults live in this dedicated Settings section. A receiving GUI should keep temporary
filters and one-time actions beside their affected data, and use Settings for saved configuration.

The editable controls are `ignores`, `minimum_severity`, `maximum_priority`, `fail_on_priority`, `tool_timeout`,
`max_workers` and `max_tool_output_bytes`. Settings constrain the next operation. A tool can impose
a shorter timeout. The existing review CLI's `--fail-on` overrides the saved failure threshold for
that command. `null` disables the saved failure policy. Reset removes just the selected YAML key,
so the installed version's default takes effect. Different from defaults compares effective values.
An explicit project value equal to its built-in default does not appear in this filter.

Additional ignored paths accepts one Git-style pattern per line in the browser, or a JSON list:

```bash
blueprint-ai settings set ignores '["scratch/", "*.generated.py"]' ./project --dry-run --json
blueprint-ai settings set ignores '["scratch/", "*.generated.py"]' ./project
blueprint-ai inspect ./project --json
blueprint-ai settings reset ignores ./project
```

These patterns change the next inspection and review's file selection. Built-in exclusions always
apply. The existing discovery engine processes `.gitignore` rules after the additional patterns,
and a Git checkout's standard excludes still apply. Reset removes only these additional patterns.
It does not clear `.gitignore`. Removing an exclusion can include more files in the next review.
Native limits allow at most 1,000 patterns and 1,000 characters per pattern. Browser requests have
a 16 KiB limit. Larger supported lists can use the CLI. The line editor preserves spaces and
comment lines, and leaves an existing list unchanged until the user edits it.

The native `blueprint_ai.settings` API and `settings show --json` expose `blueprint.settings.v1`.
Read and preview start no tools or models. Each control has a stable ID, category, constraints,
default, effective value, source, scope and next-operation timing. The document revision is the
SHA-256 of the exact saved bytes, or `missing`. API writes require `expected_revision`. CLI callers
can pass `--expect REVISION` to bind a reviewed draft, or omit it for an immediate edit against the
current document. `--dry-run` returns the proposed values and changes without saving them.

Edits validate against the existing `Settings` model and preserve unrelated YAML values, comments
and formatting. Writers serialize with a project-local lock and replace the document atomically.
Other settings writers and changed bytes are refused. Direct editors do not take this lock, so
avoid editing the file simultaneously. A leftover `.blueprint-ai.settings.lock` after a terminated
process requires the operator to check that no settings writer is active before removing it.
Symlinks, invalid YAML, duplicate keys and aliases are refused. Editing requires block-style YAML.
The settings reader can inspect valid flow-style mappings, but never rewrites them silently.

The interface returns only the seven ordinary values. Other project fields are listed by name and
keep their existing YAML interfaces. Model/provider spending controls, credentials, executable
trust, network target authorization, suppressions and output paths stay outside this editor.
Saving defaults never grants execution or network authority. Consumers such as Deep must map
trusted, configured target IDs to project roots on the server and invoke this owner API. A browser
must never supply a root path, executable or credentials. Existing Deep capability discovery and
appearance adapters remain separate owners.
