# Providers and shared contracts

The shared graph represents components, scope, lifecycle, dependencies, relationships, ownership,
and verification evidence. Genesis intent resolves capabilities topologically, checks artifact
conflicts, and produces descriptive operations. Execution recomputes the plan from validated intent;
serialized commands are not an authority to execute arbitrary code.

The canonical registry is `src/blueprint_ai/support.py`. Native family recipes use the common
`genesis` models/executor and the same sandbox contract as review and capability verification.
Provider metadata includes source, license, version policy, network/trust, registry integrity where
available, and selected tool image. External-tool adapters own machine-output normalization.

Native generators own their skeletons. Blueprint AI composes focused application behavior,
verification, repository files, and provenance. Initializr uses a bounded HTTPS archive download
from its registered endpoint; archives are fully checked for paths, duplicates, sizes, special files,
and UTF-8 source before extraction. Downloading an archive does not execute its hooks or wrappers.

To add a provider, add canonical metadata, a recipe using existing operation types, explicit output
postconditions and ownership, and a real sandbox generation/build/test run. Add parser contracts,
known-bad fixtures, acquisition evidence, and a precise unavailable state before advertising support.
Regenerate the support document and update the maintained research decisions. Repository-local
provider Python plugins and arbitrary remote template hooks are deliberately unsupported.
