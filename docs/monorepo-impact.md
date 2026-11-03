# Workspace change impact

`blueprint-ai impact PROJECT --base-ref REF --json` reads Git changes and the root npm `workspaces` declaration. It maps each changed file to the deepest declared package, then includes packages that declare a dependency on a changed package, directly or transitively. A parent package containing a nested workspace is included even without a declared dependency. Root package and lockfiles, `.npmrc`, workspace configuration, root scripts and workflow changes conservatively select every package. Unmapped paths also select every package but retain `partial` status and their exact filenames so a maintainer can review the unknown edge. No project code or test script runs.

The default comparison ref is `HEAD`, which includes current worktree changes. Supply a reviewed base ref to compare a longer branch range. The result lists each selected package's declared test script and tracked candidate test files. These are suggestions for a maintainer to inspect and run, not proof of coverage or a safe reason to skip other checks. Package-level selection does not understand task inputs, dynamic imports, generated code, CI policy outside the listed global paths, or changes in an unavailable package manifest.

## uv workspaces

`impact` also reads a root `pyproject.toml` with `[tool.uv.workspace]`. Automatic selection keeps npm first when both workspace formats are present. Use `--workspace uv` or `--workspace npm` to choose explicitly:

```sh
blueprint-ai impact ./python-workspace --workspace uv --base-ref HEAD --json
```

The graph includes local members and the root project when it has a `[project]` table. A virtual workspace without a root project is supported. Member and exclude patterns are relative to the root and support literal components, `*` and `?`. Recursive `**`, parent traversal, character classes, braces, symlinks and Git metadata directories refuse. Discovery inspects at most 10,000 directory entries and accepts at most 256 packages, with each manifest limited to 256,000 bytes. External workspace and path-source declarations do not cause outside files to be read.

Dependencies use normalized Python package names. Selection unions ordinary dependencies, every optional extra, every dependency group, legacy uv development dependencies and build requirements. Markers are not evaluated, so an inactive extra or platform-specific requirement can still select a dependent. Root-level tests belong to the root project, while nested member tests belong to their deepest package. No uv command, resolver, build backend, project code or test runs. Python packages have no inferred test command, so `test_script` is `null`.

Root configuration, lockfiles, scripts and workflow inputs select all packages with `partial` status. Other root or unmapped files do the same and remain listed by path. A project declaring dynamic dependencies or optional dependencies also selects all packages and appears in `unresolved_dependency_packages`. These cases need human review before narrowing checks. Invalid or unavailable metadata refuses analysis rather than claiming a complete graph. Static metadata still cannot prove that all runtime imports or shared inputs were declared.

uv results keep `blueprint.impact.v1` and add `workspace_type: "uv"` and `unresolved_dependency_packages`. npm results retain their existing JSON fields and shared-input status behavior. The parser follows the existing [uv workspace contract](https://docs.astral.sh/uv/concepts/projects/workspaces/) and [Python dependency-group name rules](https://packaging.python.org/en/latest/specifications/dependency-groups/).

This deliberately adopts the package-graph idea described by [Nx affected projects](https://nx.dev/docs/reference/nx-commands#nx-affected) and [Turborepo affected packages](https://turborepo.dev/docs/reference/run#--affected). Blueprint does not copy their source, run either product, use their caches, or claim their finer task-input analysis. The implementation uses Blueprint's existing bounded Git inventory, Python standard library and existing `packaging` dependency.
