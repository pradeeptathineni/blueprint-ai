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

## Browser appearance

Open **Appearance** on the local Settings page to choose System or any of the six shared presets.
Validated color, corner and density changes apply to the page. The choice belongs only to this
browser at this address and never edits `.blueprint-ai.yml`. A new server port is a different
browser origin. Reuse an explicit `settings serve --port` value when the same address is needed
across restarts.

Appearance changes preserve project edits and a reviewed preview. Project defaults still require
Preview and Save. System follows OS color changes, tabs at the same address share appearance, and
reset returns to System. Invalid saved preferences remain untouched until an explicit choice or
reset. If browser storage is unavailable, page-local choices and native project editing remain
usable with an explanatory message.

The page adapts the unchanged MIT shared theme module and existing browser controller. The
[exact source identities](shared-appearance-source.json) and
[retained license](../src/blueprint_ai/settings_web/theme/LICENSE) distinguish declared pins from
verified file bytes. The standalone wheel and source archive include the runtime module and license.
No external font, registry install or remote runtime is required. Deep keeps its own appearance
owner and accepted Blueprint source pin.

## From findings to repair

Review, plan and apply use the same terminal report meanings. The plan includes all active
findings, including work that needs human judgment. `safe repair candidate` identifies an active,
deterministic Blueprint finding with a declared safe remediation. It is not execution authority
or a promise that the target can be changed. `manual review` means the finding is not eligible for
automatic repair. The same eligibility rule governs bulk apply and single-finding reviewed repair.
Target freshness, conflict checks and explicit confirmation still apply at their existing owners.

```bash
blueprint-ai review ./project --blueprint repository --model off
blueprint-ai plan ./project --blueprint repository --model off
blueprint-ai apply ./project --blueprint repository --model off
blueprint-ai verify ./project --blueprint repository
```

When nothing is changed, apply shows the original findings and coverage instead of implying there
was no work to do. Conflicted repairs retain that report too. After a change, automatic verification
covers only changed blueprints. Other selected findings or incomplete checks stay visible as
pre-apply results that were not rerun. Explicitly verify the selected checks for a fresh result.
Missing tools, partial coverage and errors remain visible even when the plan has no findings.
A missing LICENSE still requires a human licensing choice rather than an invented repair.

Terminal rendering and incomplete-assessment wording live in the existing output module. CLI
options, check execution, exit policy, JSON payloads, finding fingerprints and recovery receipts
retain their existing contracts. The standalone CLI remains usable without Deep or a model.

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
The Settings contextual sidebar derives File selection, Reports and Execution limits from the
owner's existing category metadata. It shows result counts and the active category. Search starts
across all supported settings, while choosing a category clears the search. Navigation preserves
unsaved changes and never saves a preference. On narrow screens the contextual navigation stacks
above the controls. An application's main navigation remains separate from this Settings sidebar.

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
