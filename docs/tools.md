# Tool resolution and acquisition

`support.py` is the canonical tool/provider metadata registry. Adapter implementations add commands,
parsers, expected exit codes, and resource bounds. Planning, doctor, execution, and the
[generated support document](support.md) use this source.

```bash
blueprint-ai tools doctor
blueprint-ai tools plan ruff
blueprint-ai tools install ruff --dry-run
blueprint-ai tools install ruff
blueprint-ai tools install cargo
blueprint-ai support --markdown > docs/support.md
```

Planning is pure. Installation is an explicit local container acquisition: pull a selected version,
or build a small registered recipe in an isolated context. Recipes pin the fetched base registry
digest and selected package/component versions. No arbitrary download script, target Dockerfile,
host package manager, global npm/pip installation, or cloud service is invoked.

Receipts live under `~/.cache/blueprint-ai/tools/` and record the source, license, backend, image ID,
and (for builds) base digest and recipe hash. Later execution inspects and uses the immutable local
image ID. Changing a tag or deleting an image does not authorize an automatic pull. Native adapters
share their provider's receipt where the toolchain is identical. `tools doctor` distinguishes cached
receipt identities from runtime detection; execution confirms that an image is still present.

Some tools require locked project dependencies, a preloaded vulnerability database, platform-specific
installation, or an operator-built image. Their registry entries link to official acquisition guidance;
`tools install` will not pretend those entries have a managed installer. `--sandbox-image` selects an
explicit local image. It does not bypass the filesystem/network/resource policy. Host binaries and
project-local wrappers are available only under the explicit trusted host boundary.

Upgrades are explicit. Rerun the clean/bad/repaired native contract fixture and relevant generated
projects before changing a selected tool version. Version tags and package ranges are not hermetic
build promises: receipts and project lockfiles retain the observed identities. The registry's platform
metadata describes upstream/runtime paths, not a claim that each tool was tested on every OS.

Missing images/backends are `sandbox_unavailable`; disabled/offline tools are incomplete. Malformed
output, unexpected exits, timeouts, and truncation cannot become a pass. Successful SBOM generation
records artifact count/schema/output hash; it does not fabricate a vulnerability finding. A scanner's
findings remain separate from execution coverage.

## Integration roles

The canonical registry distinguishes review, genesis, toolchain acquisition, capability-kit
verification, and deferred entries. The 52 IDs comprise 47 review integrations, OpenTofu genesis,
Cargo toolchain acquisition, pre-commit kit verification, and deferred PHP lint/Kubescape entries.
Registration alone does not claim an executable review integration. The generated support table
derives an intentional classification for every entry: managed OCI, safely acquirable,
platform-constrained, experimental, deferred, or superseded/rejected. Runtime availability remains a
separate `doctor` result. The [independent tool inventory](redteam-tool-inventory.json) records all 52
lifecycles. The [native audit](redteam-tools.md) records parser corrections and actual runs.
