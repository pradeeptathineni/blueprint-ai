# Release verification

Release from a clean branch after the code, native execution, hostile input, documentation, and
package gates pass. A zero-finding review is only one input. Existing release tags are immutable;
use the repository's no-`v` convention. The [independent Phase 6 audit](phase-6-redteam.md) records the observed
0.6.0 gate and its limits; the [0.6.1 validation](release-validation-0.6.1.md) records the maintenance
release delta and [release notes](release-notes-0.6.1.md).

## Source and boundary gate

Use Python 3.12–3.14 and `uv`. Core discovery and built-in generation also work without Git installed;
Git-specific inventory and revision operations require the Git client.

```bash
uv sync --extra dev --extra model --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pip-audit
uv run blueprint-ai support --markdown > /tmp/blueprint-support.md
cmp docs/support.md /tmp/blueprint-support.md
uv run blueprint-ai doctor --json
uv run blueprint-ai schema sandbox-policy
uv run blueprint-ai review . --profile production --no-model --sandbox docker --fail-on P1 --format json
uv run blueprint-ai review . --profile production --no-model --trust-project-executables --fail-on P1 --format json
```

The second review deliberately executes this trusted source checkout. Never copy that trust flag to
an untrusted public corpus. Strict review needs explicitly acquired tool images; unavailable tools
remain partial. Review's priority exit policy does not certify complete tool coverage. Use a disposable
source checkout in a Docker-shared path if Docker Desktop cannot mount the development directory.

After explicitly acquiring the Python and Node test images:

```bash
docker pull python:3.12-slim-bookworm
docker pull node:24-bookworm-slim
BLUEPRINT_SANDBOX_TEST_IMAGE=python:3.12-slim-bookworm \
BLUEPRINT_SANDBOX_NODE_IMAGE=node:24-bookworm-slim \
  uv run pytest -W error::DeprecationWarning --cov=blueprint_ai --cov-report=term-missing
PYTHONPATH=src uv run python benchmarks/sandbox_overhead.py \
  --output /tmp/blueprint-startup.json
PYTHONPATH=src uv run python benchmarks/run.py
```

Without those environment variables the ordinary suite skips live OCI boundary tests. Set both in
release verification; a missing runtime/image is not a successful isolation test. CI has a dedicated
Linux Docker job, a Python 3.12/3.13/3.14 compatibility matrix, one Linux quality/build/audit job,
and a native Windows smoke job. A configured job is not a completed remote run.

## Native projects and scanners

Inspect `tools plan` before explicitly acquiring the selected tool/provider images. For the complete
matrix, provider names are `uv`, `npm`, `vite`, `next`, `go`, `cargo`, `dotnet`, `maven`, `spring`,
`terraform`, `tofu`, `pulumi`, `helm`, and `kustomize`. Shared images need not be downloaded twice.
Native mutation fixtures need `ruff`, `gitleaks`, `semgrep`, `hadolint`, `markdownlint-cli2`,
`conftest`, `ast-grep`, and `buf`; Syft has its own SBOM evidence contract.

Use unused output paths:

```bash
PYTHONPATH=src uv run python benchmarks/tool_matrix.py --output /tmp/blueprint-native-tools.json
PYTHONPATH=src uv run python benchmarks/phase6_matrix.py \
  --output /tmp/blueprint-generated --backend docker --network --compositions
PYTHONPATH=src uv run python benchmarks/corpus.py --output /tmp/blueprint-corpus --review
PYTHONPATH=src uv run python benchmarks/genesis_matrix.py \
  --output /tmp/blueprint-trusted-images --network --kinds repository --add-ons
```

The final command intentionally uses trusted host providers and local Docker to build generated
service and development images. It requires native uv/Node/npm. Strict OCI generation never receives
the Docker socket and reports nested image builds as unavailable. Cloud generation performs local
validation only, with no provisioning, state backend, or deployment. Compare pinned corpus source
snapshots and component/context evidence; total findings depend on optional tool and database availability.

## Wheel and source distribution

```bash
uv build --out-dir /tmp/blueprint-0.6.1-dist
uv venv /tmp/blueprint-fresh
uv pip install --python /tmp/blueprint-fresh/bin/python \
  /tmp/blueprint-0.6.1-dist/blueprint_ai-0.6.1-py3-none-any.whl
env -u PYTHONPATH /tmp/blueprint-fresh/bin/python benchmarks/release_smoke.py \
  --output /tmp/blueprint-wheel-smoke.json --image python:3.12-slim-bookworm
uv pip install --python /tmp/blueprint-fresh/bin/python --reinstall-package blueprint-ai \
  /tmp/blueprint-0.6.1-dist/blueprint_ai-0.6.1.tar.gz
env -u PYTHONPATH /tmp/blueprint-fresh/bin/python benchmarks/release_smoke.py \
  --output /tmp/blueprint-sdist-smoke.json
```

The smoke harness checks the installed distribution through its CLI: version/help, doctor, seven
schemas, support/acquisition planning, repository/OpenAPI generation, read-only offline/no-model
review, capability planning/apply/exact rollback, and either real OCI execution or explicit unavailable
execution. Repeat in isolated supported Python/platform environments. Inspect wheel/sdist contents,
record SHA-256 sums alongside the artifacts, and rerun smoke checks on the final build.

## Tag and publication

After release authorization, commit the verified tree and create an annotated `0.6.1` tag on that
exact commit. Check `git status --porcelain`, `git cat-file -t 0.6.1`, and
`git rev-parse '0.6.1^{commit}'`. Preparing an artifact or tag does not itself authorize pushing it,
creating a public GitHub Release, or uploading a package.

For a later authorized PyPI release, prefer [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
with a protected GitHub environment, an exact repository/workflow identity, short-lived OIDC, and
publication of the already-verified artifacts. Follow the
[PyPA GitHub Actions publishing guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).
Configure publisher identity at the registry before enabling a release job, pin actions to reviewed
commits, and scope `id-token: write` to that job. No publisher credentials or automatic upload workflow
are introduced by this release.

The published `0.6.0` tag is immutable and resolves to `5e19dac`. The [independent release
audit](phase-6-redteam.md) remains the historical source gate for that release; never reuse or move
the tag. Maintenance releases receive a new tag after final CI succeeds on the exact merged commit.
