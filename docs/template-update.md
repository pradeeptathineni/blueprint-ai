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

For a conflict, `--resolve PATH=project` retains the authored file and `--resolve PATH=template` accepts the new template file. A file path supplies exact merged bytes. Review the resulting plan before applying it. Blueprint refuses a plan if a target's content or mode changed after preview. Applying a matching update again returns `noop`.

The apply result contains `operation_id`, changed file hashes, a manifest path and a rollback command. `blueprint-ai rollback OPERATION_ID ./my-api --json` restores exact pre-update bytes and modes only while every changed file still matches its published version. Unrelated files are left alone. Run the project's own tests after applying; the `verified` status means Blueprint checked published bytes, not that project code was executed. The runnable API journey in `benchmarks/template_update_journey.py` includes a project smoke test.

The current lane supports local Git templates, static paths, regular files, string answers and files up to 4 MiB each or 32 MiB per rendered template. It does not render Jinja control flow, rename files, fetch remote templates, run hooks or contain project commands. A Git commit is the template pin; `check` compares commit identity strictly, so a different commit is reported as behind even if rendered files happen to match. Concurrent updates use Blueprint's Git-private evolution lock. Commit or copy the template source before moving a project to another machine.

Source review and license notices are recorded in [TEMPLATE_UPDATE_NOTICES.md](../src/blueprint_ai/evolution/TEMPLATE_UPDATE_NOTICES.md).
