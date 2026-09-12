# Untrusted repository threat model

Repository files, Git metadata, manifests, configuration, URLs, archive entries, model context, and
tool output are untrusted. Protected assets include host files, credentials, network authority,
terminal integrity, bounded compute/storage, and the accuracy of findings and mutation receipts.

## Controls

Discovery and context building reject symlinks/special files, skip generated/vendor state, and bound
file counts and bytes. Manifest parsing never evaluates project code; XML entity declarations and
unsafe YAML constructs are rejected. Framework and dependency evidence is declarative and does not
prove deployed behavior or resolve every transitive dependency.

External review tools run in the [common OCI boundary](sandbox.md) by default. They receive a read-only
target, sanitized environment, disposable caches, no host credential/socket mounts, denied egress,
and resource/output/time limits. Backend and local-image availability are prerequisites. Target code
never falls back to the host. Explicit trusted host execution is reported with weaker guarantees.
Runtime binaries/images are operator-owned dependencies; an image receipt is provenance, not a
cryptographic endorsement of its publisher or protection against host/runtime compromise.

Untrusted network access defaults to none. Lychee additionally uses a neutral config and excludes
private/link-local/loopback addresses when trusted networking is authorized. ZAP requires an explicit
credential-free authorized endpoint. Target-derived URLs, suppression text, and model responses
cannot authorize a scan, download, deployment, or tool acquisition.

All commands are registered argument arrays, with noninteractive environments, bounded capture,
timeouts, and terminal-control sanitization. Malformed output and unexpected exits are incomplete
coverage. Repository YAML cannot define arbitrary commands, imports, executable templates, or
sandbox policy. Project-local wrappers may run within OCI; host execution requires explicit trust.

Model input is ranked, bounded, redacted, delimited as untrusted, versioned, and measurable. Models
receive evidence after deterministic analysis and cannot turn their recommendations into trusted
compiler facts, permissions, or mutations. No-model mode needs no credentials and makes zero calls.

## Mutation and generation

Create-only capabilities preflight paths and conflicts, validate generated assets, write atomically,
and retain hashes. Failed verification rolls back unchanged created files; manual rollback preserves
user edits and removes only eligible files and new empty directories. This does not promise to undo
arbitrary side effects of an explicitly trusted host verifier. No background service is activated.

Genesis accepts validated intent and registered providers, recomputes the plan, stages output,
checks provider integrity where available, and rejects symlinks/special files before strengthening.
Initializr archives have compressed/expanded/file-count limits, normalized path/duplicate checks,
and UTF-8 preflight; remote hooks are never executed. Source is copied out of dependency/build state
before the final review. Publication uses an atomic no-replace rename and includes hash-bound
provenance. Failures leave the destination absent; unavailable verification can publish explicit
partial source. Receipts do not grant execution trust.

Dependency installation runs explicitly trusted publisher code only in the selected execution mode.
Generation may write its disposable stage and use authorized network for declared installation steps.
Cloud resources, remote state, account credentials, GitHub changes, signing, and package publication
are never implicit. Native Docker builds require the trusted host path; no daemon socket is exposed
inside OCI generation.

## Residual boundaries

Containers share a Linux kernel; rootless operation is preferred when available. gVisor must already
be configured. Non-OCI Linux sandboxes, native Windows named pipes, remote engines, destination
allowlists, internet-only egress, and total writable bind-mount quotas are unsupported. No
kernel/runtime isolation proof, exhaustive browser journey coverage, production readiness, or
live-model quality is implied by the local tests. Very large
Git indexes and upstream dependency graphs can consume resources within the documented limits.
Concurrent hostile host administrators are outside the threat model. Keep the runtime patched and
review explicit trust/network decisions for the task being performed.

The [Phase 6 validation report](phase-6-validation.md) separates tested controls from these limits.

The [independent red team](phase-6-redteam.md) supersedes candidate self-review conclusions.
POSIX bounded reads pin parent directory descriptors and reject leaf substitution by symlinks or
FIFOs. Native Windows CI covers representative portable workflows but does not carry that POSIX
descriptor-race or OCI-engine claim. Browser evidence is limited to the generated React and
Python-backed full-stack starter workflows.
