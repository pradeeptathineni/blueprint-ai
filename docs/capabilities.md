# Add capabilities to an existing project

`add` reuses the capability kits and transaction engine used by remediation. It does not recreate the
project or overwrite its configuration. The [registry](support.md) lists exact files and verifiers.

```bash
blueprint-ai add dependency-updates ./project
blueprint-ai add dependency-updates ./project --apply
blueprint-ai rollback OPERATION_ID ./project
```

The default command prints full proposed content, hashes, conflicts, and verification commands.
`--apply` recomputes the plan, refuses conflicting content and symlink paths, and atomically creates
absent files. Existing identical assets are idempotent. Generated JSON/YAML/TOML/Python assets are
parsed, and declared native verifiers run under the common sandbox policy.

Known equivalent configuration locations also conflict: a root `.devcontainer.json`, an existing
Renovate file or `package.json` Renovate section, or `.semgrep.yaml` will prevent a second default
configuration from being created. These alternatives are declared by the kit metadata and checked
by planning, direct kit application, and remediation. Existing configuration is never merged.

Use `--sandbox docker --sandbox-image IMAGE` for a suitable acquired tool image, or explicitly trust
host execution with `--sandbox host --trust-project-executables`. Unavailable verifiers leave an
explicit incomplete result. A failed verifier rolls back unchanged files created by the operation.
Exit 3 means incomplete verification, conflict, or rollback; inspect the JSON details.

Successful changes record a hash-bound operation manifest. Rollback removes only matching created
files and, for current manifests, its own receipt and newly created empty directories. User edits
and unrelated files survive. Existing version-1 receipts remain readable. This is exact rollback of
supported create-only writes, not a general inverse for arbitrary external tool mutations.

Security policy and release documentation are prompts for the project's maintainers to complete.
A dependency-update config does not install or activate a hosting app. A secret/SAST config does not
prove a scan ran. The Dev Container kit adds configuration; editor attachment is a separate workflow.
The container kit adds ignore rules, while fresh API genesis owns executable service Dockerfiles.
General merges into existing manifests, codemods, migrations, and credential/service activation are
outside this release's create-only boundary.
