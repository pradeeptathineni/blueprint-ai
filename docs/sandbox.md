# Sandbox and trust

External review tools use `SandboxPolicy`, shared with generation and capability verification.
The operator supplies this policy; repository configuration cannot grant execution authority.
Inspect `blueprint-ai schema sandbox-policy` and `blueprint-ai tools doctor`.

## Backends

`auto` prefers local Podman, configured Docker with gVisor/runsc, then local Docker for untrusted
execution. Linux Podman runs locally without a remote service. Docker uses a local Unix socket,
including Docker Desktop on macOS. Native Windows named pipes and remote Docker/Podman contexts
are unsupported; WSL can use its local Linux engine. Core deterministic analysis is independent
of the container runtime. These platform paths have different validation coverage; see the evidence report.

`--sandbox docker`, `--sandbox podman`, and `--sandbox gvisor` select a boundary explicitly.
An unavailable engine, missing local image, unsupported image volume, or failed teardown is reported
as unavailable/incomplete. It never triggers untrusted host execution or an implicit image pull.

`--sandbox host --trust-project-executables` permits trusted host review. For compatibility,
`auto` with explicit trust and no custom image also selects the host. Host execution has bounded
process output and timeouts, but no filesystem, network, memory, or PID isolation. Generation uses
`--trust-providers`; use an explicit OCI backend when isolation is required.

## OCI policy

The default is a non-root user, read-only container root and repository, executable disposable
scratch, dropped capabilities, no-new-privileges, private IPC, no inherited credentials, and no
Docker socket or SSH agent. The only additional mounts are synthetic read-only account records
for the container UID; host account databases are never mounted. Docker and Podman target bind submounts are excluded.

Defaults: 120 seconds, 2 CPU quota, 1 GiB memory with no additional swap, 128 processes, 256 MiB
scratch, 128 MiB maximum individual file, and 4 MB captured output per stdout/stderr stream. Evidence
records the scratch and individual-file limits separately from workspace capacity. A read-only target
has an enforced zero-byte writable-workspace limit. A trusted writable bind mount has no portable
Docker/Podman total-size quota, and evidence reports that limit as unset and unenforced. Compilers use
bounded scratch caches. Tools that insist on writing source or require absent dependencies can fail
under read-only review; that failure is incomplete evidence.

Generation has a writable disposable stage and explicit provider trust. Its registered operations
use 240 seconds, 1 GiB scratch and 2 GiB memory; Terraform/OpenTofu provider installation permits
4 GiB memory and 1 GiB individual files. The final destination is absent until publication.
No daemon socket enters the sandbox, so nested Docker/Dev Container image builds are unavailable
in strict OCI generation. Trusted host generation retains the existing local image-build path.

Evolution also uses a writable disposable stage even when the operator selects trusted host
execution. The native tool sees the staged tracked/unignored source, not the real worktree; only
scope-checked verified results are published afterward. Existing ignored dependency directories are
not copied. Migration caches are empty, external, and network-disabled, so dependency-bearing native
verification must use vendored inputs or fail explicitly.

## Network

`none` uses the container's private network namespace without egress or host-loopback access. An
internal loopback interface can still exist. `restricted` (internet-only) and `allowlist` are rejected
because the local adapters cannot guarantee destination filtering across Docker/Podman firewall
backends. `unrestricted` requires both explicit trust and explicit network authorization and permits
ordinary networking, including loopback, private, link-local, and metadata destinations. The legacy
input names `loopback` and `normal` normalize to `none` and `unrestricted`; generated schemas and
evidence use only canonical names.

Default untrusted execution therefore cannot reach metadata services, private networks, or
repository-supplied endpoints. Trusted online Lychee still excludes private destinations, and DAST
requires the operator's explicit authorized target. Do not treat application filters as an OCI firewall.
`offline` disables network-classified adapters and model calls in addition to the container policy.

```bash
blueprint-ai sandbox ./project python:3.12-slim-bookworm --backend docker -- \
  python -c 'print("sandbox ready")'
```

The image must already exist locally. The JSON result records policy, immutable image ID, runtime,
mutability, limits, exit/timeout, OOM state, truncation, teardown, and elapsed time.

## Boundaries

Docker/Podman containers share a Linux kernel. gVisor adds its configured userspace kernel boundary;
this release does not install or configure runsc. Bubblewrap/nsjail require deployment-specific
seccomp, namespaces, and cgroup work and remain deferred. Runtime/host compromise and concurrent
malicious host administrators are outside this boundary. The tests are empirical checks, not a formal
isolation proof. Docker per-container storage-driver limits do not bound a host bind mount, while
tmpfs is bounded but ephemeral; copying results through an unbounded host mount would not provide a
security quota. Blueprint AI therefore does not claim total writable-stage enforcement. [Threat
model](threat-model.md) covers files, parsers, tools, and model inputs.

## Independent audit

The [independent sandbox audit](redteam-sandbox.md) records live Docker boundary checks and
corrections to lifecycle evidence, Podman configuration isolation, and filesystem read races.
Podman receives neutral configuration, authentication, mounts, and hooks files; these controls
remain source/contract tested until a compatible live engine is available. Native Windows and
live Podman/runsc remain unverified. macOS Docker results execute Linux containers across a VM.
