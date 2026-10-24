# Update a pinned template

Blueprint can create a project from a local Git template and later adopt changes from another commit. It records the template path, commit and string answers in `.blueprint-ai/template.json`. Templates are regular files with optional `{{answer_name}}` placeholders. The source repository must be available at update time. Blueprint does not run template hooks.

```sh
blueprint-ai template init ./api-template ./my-api --ref <v1-commit> --answers answers.json
blueprint-ai template check ./my-api --ref <v2-commit>
blueprint-ai template plan ./my-api --ref <v2-commit> --output /tmp/update-plan.json
```

The plan shows each file's change, diff and any overlap. A conflict blocks the entire update before publication. Resolve it in a separate file, then make a fresh plan:

```sh
blueprint-ai template plan ./my-api --ref <v2-commit> \
  --resolve README.md=/tmp/merged-readme.md --output /tmp/resolved-plan.json
blueprint-ai template apply ./my-api --plan /tmp/resolved-plan.json
```

For a conflict, `--resolve PATH=project` retains the authored file and `--resolve PATH=template` accepts the new template file, including its executable mode. A file path supplies exact merged bytes and retains the project's mode. The preview lists content and mode conflict reasons and both file modes. Review the resulting plan before applying it. Blueprint refuses a plan if a target's content or mode changed after preview. Applying a matching update again returns `noop`.

The apply result contains `operation_id`, changed file hashes, a manifest path and a rollback command. `blueprint-ai rollback OPERATION_ID ./my-api --json` restores exact pre-update bytes and modes only while every changed file still matches its published version. Unrelated files are left alone. Run the project's own tests after applying; the `verified` status means Blueprint checked published bytes, not that project code was executed. The runnable API journey in `benchmarks/template_update_journey.py` includes a project smoke test.

The current lane supports local Git templates, static paths, regular files, string answers and files up to 4 MiB each or 32 MiB per rendered template. It does not render Jinja control flow, rename files, fetch remote templates, run hooks or contain project commands. A Git commit is the template pin; `check` compares commit identity strictly, so a different commit is reported as behind even if rendered files happen to match. Concurrent updates use Blueprint's Git-private evolution lock. Commit or copy the template source before moving a project to another machine.

## Questionnaire templates (`copier-safe-v1`)

A local Git template with a root `copier.yml` or `copier.yaml` can be created from typed answers. Inspect the questions, preview the exact result, then create the project with the preview's digest:

```sh
blueprint-ai template questions ./service-template --ref <commit>
blueprint-ai template preview ./service-template ./orders --ref <commit> --answers answers.json
blueprint-ai template init ./service-template ./orders --ref <commit> --answers answers.json \
  --format copier-safe-v1 --confirm-digest <preview_sha256>
```

Preview writes nothing. It lists the resolved commit, every answer with its type and whether it was explicit or a default, each rendered path with its source path, mode, size and SHA-256, and a content diff. The digest binds the source, ref, commit, target, answers and rendered bytes; `init` refuses if any of them changed.

Creation verifies files in a private sibling staging directory before publishing the complete tree. Observed destination changes refuse creation, and publication never replaces a populated directory. Failure cleanup removes only the owned staging directory. An interrupted process may leave staging files for local inspection; it never automatically deletes the destination.

The dialect is deliberately small. Questions have type `str`, `bool` or `int`, with literal `help`, `default` and `choices`. Answers are JSON values of the exact type: `false` is a boolean, `"false"` is refused for a boolean, and `true` is refused for an integer. Settings are limited to literal `_subdirectory`, `_templates_suffix` (default `.jinja`; an empty suffix renders every UTF-8 text file) and `_min_copier_version`. Paths and suffix files may use `{{ name }}`; booleans render as `True`/`False`. Files without the suffix and binary files are copied byte-for-byte. Copier's default exclusions (`copier.yml`, `~*`, `*.py[co]`, `__pycache__`, `.DS_Store`, `.svn`) are listed in the preview.

Blueprint refuses, before writing, any other setting or question field (`_tasks`, `_migrations`, `_exclude`, `secret`, `when`, `validator`, `placeholder`, …), templated defaults or choices, Jinja filters or control flow, YAML anchors, tags, merge keys, duplicate keys or multiple documents, unknown or missing answers, symlinks and submodules, unsafe or colliding rendered paths, `.git` or `.blueprint-ai` targets, more than 64 questions or 1000 files, and configs over 64 KiB. Rendered files keep the 4 MiB and 32 MiB limits. This is not general Copier compatibility.

The project record is `schema: 2` with `format: copier-safe-v1` and typed answers; earlier Blueprint releases reject it rather than treat it as a static template. `check`, `plan`, `apply` and `rollback` work as above. A newer template commit may add questions with defaults, which are recorded. A removed question, a changed type, a recorded value outside the new choices or a new question without a default refuses the plan and leaves the project unchanged. `benchmarks/template_intake_journey.py` runs the whole flow.

Source review and license notices are recorded in [TEMPLATE_UPDATE_NOTICES.md](../src/blueprint_ai/evolution/TEMPLATE_UPDATE_NOTICES.md).
