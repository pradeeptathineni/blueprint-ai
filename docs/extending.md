# Extension contract

Blueprint AI deliberately has three extension surfaces:

1. Built-in blueprints are trusted Python `Blueprint` values. Metadata, applicability, deterministic
   checks, model need, and remediation are separate fields. `validate_builtin_contracts` validates
   every registration in tests and `doctor`.
2. External tool adapters are trusted Python `ExternalToolAdapter` values. Commands are argument
   arrays defined by installed Blueprint AI code, with a parser, expected exit codes, bounded output,
   timeout, lifecycle metadata, and optional network classification.
3. Project-local blueprints are untrusted declarative YAML. They may require files or detect a fixed
   literal in selected files. They cannot define commands, Python imports, templates, model calls, or
   arbitrary regular expressions.

Put local definitions in `.blueprint-ai/blueprints/*.yml` and enable them in `.blueprint-ai.yml`:

```yaml
enabled_blueprints: [local-release-policy]
```

```yaml
schema_version: 1
name: local-release-policy
description: Require the project's release ownership document.
applicability:
  files_any: [pyproject.toml, package.json]
rules:
  - kind: required-files
    id: release-ownership
    paths_any: [docs/releasing.md]
    severity: medium
    message: Release ownership is not documented.
    recommendation: Add docs/releasing.md with owner and verification commands.
```

Inspect the exact schemas with `blueprint-ai schema custom-blueprint`, `schema settings`, and
`schema report`. Names use the `local-` prefix so project rules cannot shadow built-ins. Paths must be
relative and cannot traverse upward. YAML aliases and oversized definitions are rejected.

Executable hooks are intentionally not loadable from a target repository. A distributor can add a
trusted Python blueprint/adapter in the package and cover it with the common contract tests. Tool
overrides and local wrappers run only in the selected OCI boundary or with explicit trusted host
execution. Register new tools/providers in `support.py`; keep parsers and exit-code contracts in
adapters, and reuse the common sandbox and transaction engines. See [providers](providers.md).
