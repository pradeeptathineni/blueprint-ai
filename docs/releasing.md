# Release verification

Release from a clean branch after source, package, security, and documentation gates pass. Existing
release tags are immutable and use the no-`v` convention. The tag-driven `release.yml` workflow
builds the public artifacts from the exact tag; do not build or upload a second copy manually.

## Local gate

Use Python 3.12–3.14 and `uv`. The complete source gate is:

```bash
uv sync --extra dev --extra model --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -W error::DeprecationWarning --cov=blueprint_ai --cov-report=term-missing
uv run pip-audit
uv run python benchmarks/compatibility_snapshot.py
uv run blueprint-ai support --markdown > /tmp/blueprint-support.md
cmp docs/support.md /tmp/blueprint-support.md
uv run blueprint-ai doctor --json
uv run blueprint-ai schema sandbox-policy
uv run blueprint-ai schema evolution-plan
uv run blueprint-ai schema evolution-report
uv run blueprint-ai schema transformation
uv run blueprint-ai review . --profile production --no-model --fail-on P1 --format json
PYTHONPATH=src uv run python benchmarks/phase7_migrations.py \
  --output /tmp/blueprint-phase7-migrations.json \
  --require python/ruff-pyupgrade --require go/native-fix \
  --require terraform/native-format
PYTHONPATH=src uv run python benchmarks/phase8_migrations.py \
  --audit-root ../blueprint-ai-tmp/phase8-audit \
  --output /tmp/blueprint-phase8-migrations.json
```

Run Actionlint and Zizmor against every workflow. For the live OCI boundary, explicitly acquire the
Python, Node, Rust, .NET, Python-build, and Next-codemod images and run the sandbox and migration
corpora; a skipped boundary is not a passing live test:

```bash
docker pull python:3.12-slim-bookworm
docker pull node:24-bookworm-slim
uv run blueprint-ai tools install cargo
uv run blueprint-ai tools install dotnet
uv run blueprint-ai tools install python-build
uv run blueprint-ai tools install next-codemod
BLUEPRINT_SANDBOX_TEST_IMAGE=python:3.12-slim-bookworm \
BLUEPRINT_SANDBOX_NODE_IMAGE=node:24-bookworm-slim \
  uv run pytest tests/test_phase6.py tests/test_redteam_sandbox.py -W error::DeprecationWarning
```

Target repositories and their executable configuration remain untrusted. Only the dedicated trusted
self-review may use `--trust-project-executables`; never copy that flag to a public corpus.

## Distribution gate

The Python distribution is `blueprint-ai-cli`; it installs the `blueprint-ai` executable and
`blueprint_ai` import. Build in an unused directory so stale files cannot enter verification:

```bash
uv build --out-dir /tmp/blueprint-0.9.1-dist
uv run python benchmarks/verify_distribution.py \
  --dist /tmp/blueprint-0.9.1-dist --tag 0.9.1
uv venv /tmp/blueprint-wheel
uv pip install --python /tmp/blueprint-wheel/bin/python \
  /tmp/blueprint-0.9.1-dist/blueprint_ai_cli-0.9.1-py3-none-any.whl
env -u PYTHONPATH /tmp/blueprint-wheel/bin/python benchmarks/release_smoke.py \
  --output /tmp/blueprint-wheel.json
uv venv /tmp/blueprint-sdist
uv pip install --python /tmp/blueprint-sdist/bin/python \
  /tmp/blueprint-0.9.1-dist/blueprint_ai_cli-0.9.1.tar.gz
env -u PYTHONPATH /tmp/blueprint-sdist/bin/python benchmarks/release_smoke.py \
  --output /tmp/blueprint-sdist.json
```

The verifier rejects package/tag/import version drift, a missing console entry point, unsafe archive
paths, development cache files, non-portable members, and unnecessary sdist roots. Inspect the final
file lists and scan archives for credentials and machine-specific paths. Record SHA-256 hashes from
the hosted assets, not a second local build.

## Tag, GitHub Release, and provenance

Commit and reconcile the verified branch with `origin/main`, rerun the full gate, and fast-forward or
merge it to `main`. Push `main` and wait for every hosted CI job on the exact commit to pass before
creating and pushing an annotated tag on that commit:

```bash
git status --porcelain
git push origin main
git tag -a 0.9.1 -m 'Blueprint AI 0.9.1'
git cat-file -t 0.9.1
git rev-parse '0.9.1^{commit}'
git push origin 0.9.1
```

The tag-driven Release workflow first calls the complete reusable CI gate and waits for its quality,
Python 3.12–3.14, live sandbox, native Windows, workflow-security, compatibility, and support-parity
jobs. It then builds the wheel and source archive once and verifies fresh installs. No publication job
can start before those gates and distribution verification pass.

The build installs only the wheel and runtime dependencies in a clean environment, generates an SPDX
2.3 SBOM from that environment, and checks exact package/version parity against its independent
runtime inventory. Development packages and checkout paths are rejected. `SHA256SUMS` covers the two
distributions and the runtime SBOM. GitHub attestations bind both distributions to build provenance
and that SBOM, and bind the SBOM file to its own build provenance. The GitHub Release job alone
receives `contents: write`; the build alone receives attestation write access and OIDC.
Repository release immutability must be enabled before publishing so assets and the associated tag
cannot be replaced after publication.

Verify each hosted artifact after downloading it:

```bash
gh release verify 0.9.1 -R pradeeptathineni/blueprint-ai
gh release verify-asset 0.9.1 blueprint_ai_cli-0.9.1-py3-none-any.whl \
  -R pradeeptathineni/blueprint-ai
sha256sum -c SHA256SUMS
gh attestation verify blueprint_ai_cli-0.9.1-py3-none-any.whl \
  -R pradeeptathineni/blueprint-ai
gh attestation verify blueprint_ai_cli-0.9.1.tar.gz \
  -R pradeeptathineni/blueprint-ai
gh attestation verify blueprint_ai_cli-0.9.1-py3-none-any.whl \
  -R pradeeptathineni/blueprint-ai --predicate-type https://spdx.dev/Document/v2.3
gh attestation verify blueprint_ai_cli-0.9.1-runtime.spdx.json \
  -R pradeeptathineni/blueprint-ai
```

The workflow also compares the release's exact four-member asset list, verifies every asset against
GitHub's immutable-release digest, checks `SHA256SUMS`, and executes the attestation commands above
before PyPI can publish. An attestation binds artifacts to source and workflow identity; it is not a
security certification.

## PyPI Trusted Publishing

The `publish-pypi` job runs only when repository variable `BLUEPRINT_PUBLISH_PYPI` equals `true`.
Configure the initial pending PyPI publisher exactly as:

- owner: `pradeeptathineni`
- repository: `blueprint-ai`
- workflow: `release.yml`
- environment: `pypi`
- project: `blueprint-ai-cli`

Create the matching protected GitHub environment with an approval rule and tag-only deployment policy,
then set the repository variable. The publisher job receives only `id-token: write`, downloads the
already-verified workflow artifact, and uses the commit-pinned PyPA action. Do not add an API token. A
missing or mismatched publisher fails the job visibly. TestPyPI is optional and deliberately outside
the production tag path.

After Trusted Publishing, the workflow checks the exact PyPI file membership, metadata, project
links, yanked state, and registry hashes, downloads both files and compares their bytes with the
GitHub checksums, then installs the exact version into unused pipx and uv tool directories. Manual
reproduction is:

```bash
python benchmarks/verify_pypi.py --version 0.9.1 --checksums SHA256SUMS
pipx run --spec blueprint-ai-cli==0.9.1 blueprint-ai --version
uvx --from blueprint-ai-cli==0.9.1 blueprint-ai doctor
```

Record exact commit/tag identities, CI and Release run URLs, hosted asset hashes, attestation results,
PyPI state, and only actual remaining limitations in the final release report.
