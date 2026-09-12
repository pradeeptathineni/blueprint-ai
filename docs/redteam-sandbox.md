# Independent 0.6.0 sandbox red team

Audit starting point: `6c376b9`, on `codex/phase-6-ecosystem-sandbox`, 2026-09-12.
This supplement records independent checks and corrections. It does not certify a runtime
that was unavailable. The audit did not move a tag, push, publish, provision a VM, or change
host runtime configuration.

## Findings and corrections

### Container lifecycle evidence could disagree with a successful result

The candidate accepted malformed or failed post-execution container inspection and returned
successful tool output with `isolated=false`. A caller could subsequently classify the scan
as complete. A safe reproduction loaded the original `6c376b9` module, supplied a successful
scanner result and a JSON-array inspection response, and observed exactly that disagreement.

Inspection failures, malformed metadata, and an unconfirmed startup accompanying an otherwise
successful command now raise `SandboxUnavailable`. Cleanup still runs. A cleanup failure now
takes precedence even if an earlier execution error is already propagating. Regression tests
cover failed inspection, malformed JSON, non-object JSON, unconfirmed startup, and cleanup
failure after an execution exception. Missing Podman controller or runsc metadata is also an
explicit unavailable state.

### Podman inherited policy-affecting host defaults

The original Podman invocation omitted controls for default configuration, automatic secret
mounts, recursive target mounts, and automatically writable temporary directories. These are
conditional boundary defects on affected Podman installations, established from upstream
documentation and source; they were not demonstrated against a live Podman engine here.

Podman's system/user configuration can set mounts, devices, environment, and privilege defaults.
`CONTAINERS_CONF` now selects a neutral file and registry authentication uses an empty config.
This follows the documented exclusive-config behavior.
See [containers.conf](https://raw.githubusercontent.com/containers/common/main/docs/containers.conf.5.md).

Podman's mount defaults are independently disabled with an empty default-mounts file; OCI hooks
use an empty directory. The upstream implementation selects only the supplied mounts file when
that argument is set. See the [Podman option implementation](https://raw.githubusercontent.com/containers/podman/main/cmd/podman/root.go)
and [mount loading implementation](https://raw.githubusercontent.com/containers/common/main/pkg/subscriptions/subscriptions.go).

The target bind is explicitly nonrecursive. Automatic writable `/run` and `/var/tmp` mounts are
disabled, while the explicitly sized `/tmp` remains available. Proxy/host environment inheritance
and host `/etc/hosts` copying are also disabled explicitly. These correct documented defaults;
the flags still require live validation on a compatible installation.
See [Podman run options](https://docs.podman.io/en/latest/markdown/podman-run.1.html).

### A file could change between safety validation and opening

The candidate checked a pathname, then opened that pathname later. A controlled reproduction
replaced the validated file with a symlink and observed the original implementation reading a
synthetic canary outside the authorized root. A FIFO replacement could instead block the read.

Bounded reads now validate the opened file descriptor and its size. POSIX opens use no-follow
and nonblocking flags; root-bounded reads pin each parent directory through descriptor-relative
opens. Regression tests replace the leaf with a symlink or FIFO, and replace a parent with an
outside symlink, immediately after successful preflight. All are rejected. Non-POSIX systems
retain regular-file and byte bounds; the POSIX descriptor race guarantees are not claimed for
native Windows.

### Cargo retried downloads despite enforced offline networking

The final public corpus exposed two Cargo commands spending their full 120-second budgets
retrying dependency downloads under `network=none`. Cargo now receives `CARGO_NET_OFFLINE=true`
for `none` and `loopback`, and `false` for explicitly authorized `normal` networking. This aligns
dependency resolution with the enforced network policy without changing network permissions.
Three policy regressions and three real Docker environment checks verified those values;
structured evidence is retained in `/tmp/blueprint-redteam/cargo-offline-policy/live-environment.json`.
The affected OpenTelemetry Cargo Clippy/test checks then completed in 587/563 ms. Both reported
missing offline dependencies as `tool_error` with `analysis_state=incomplete` and zero findings;
the complete target snapshot remained unchanged and both containers were removed. Raw and
normalized evidence is retained as `/tmp/blueprint-redteam/cargo-offline-native.json` and
`/tmp/blueprint-redteam/cargo-offline-adapter.json`.

## Live execution

The available backend was Docker Desktop 4.55.0, Engine 29.1.3, Linux amd64 kernel
6.12.54-linuxkit, on macOS. The daemon was not rootless. These are Linux containers across
Docker Desktop's VM boundary, not native Linux-host or native Windows certification.

The controlled image was `python:3.12-slim-bookworm`, local immutable identity
`sha256:59bf1d95c965f12dfc14afaf5af778fc1dbe5b372bd5c281645fc34d4c75d4e7`.
The lifecycle-suppression fixture used the already-local `node:24-bookworm-slim` image.
No image acquisition was implicit in these tests.

The existing five-execution hostile test was rerun and independently inspected. It covered:

- Non-root execution, denied target writes, outside-path and symlink canaries, missing host
  credential environment and daemon socket, and unchanged target bytes.
- Denied private, metadata and public egress; disposable scratch and individual-file bounds.
- Wall timeout, bounded output, a 64 MiB OOM kill, PID exhaustion, and cleanup.

Eight additional live executions covered:

| Check | Observed result |
| --- | --- |
| Writable controlled stage | Created only the intended fixture file; preexisting canary preserved |
| Cross-run cache poisoning | A cache marker written in the first container was absent in the next |
| Private loopback | A listening host-loopback socket was unreachable; container-internal loopback worked |
| Explicit network | Authorized HTTPS request to PyPI returned HTTP 200 |
| CPU and scratch | `cpu.max` matched 0.25 CPU; approximately 0.75 CPU seconds over 3 wall seconds; 16 MiB scratch |
| Detached child | A new-session child could not write its delayed marker after container teardown |
| Blocked FIFO | A blocking FIFO read timed out and the container was removed |
| Hostile npm config | Offline installation succeeded, lifecycle marker absent, project-selected persistent cache absent |

Each new execution writes optional structured evidence to `BLUEPRINT_SANDBOX_EVIDENCE_DIR`.
The retained local run used `/tmp/blueprint-redteam-sandbox`; records include the policy, image
identity, engine version, trust, mutability, isolation flags, limits, duration, exit state,
stdout/stderr, and teardown. The synthetic candidate/correction comparison is retained as
`candidate-defect-reproductions.json` in that directory. These temporary artifacts are local
audit evidence; the durable regressions live in `tests/test_redteam_sandbox.py`.

All tested containers reported successful teardown. Other agents ran containers concurrently,
so no blanket container deletion was used. The npm script was a benign marker writer inside
a disposable writable target; no deliberately dangerous payload ran on the host.

## Limitations and interpretation

Podman is not installed and runsc is not configured in the local Docker engine. Doctor reports
both unavailable. No Podman VM or privileged nested runtime was created merely to claim coverage.
The Podman corrections have source and contract coverage, and remain environment-dependent
until live Linux execution. Windows and live gVisor validation remain absent.

`normal` networking intentionally permits ordinary destinations, including private addresses,
after explicit trust and network authorization. `none` and `loopback` deny egress but permit
container-internal loopback. There is no destination allowlist implementation. Containers share
the Linux kernel unless a configured runsc backend is selected.

The output bound applies independently to the raw stdout and stderr streams: the default retains
at most 4 MB per stream before sanitization. Writable stages have no total disk quota; their
trust requirement and individual-file/resource limits do not imply one. Trusted host execution
does not gain filesystem, network, memory, PID, or detached-session isolation from these tests.

## Reproduction commands

From the repository root, with the two named images already present:

```sh
BLUEPRINT_SANDBOX_TEST_IMAGE=python:3.12-slim-bookworm \
BLUEPRINT_SANDBOX_NODE_IMAGE=node:24-bookworm-slim \
BLUEPRINT_SANDBOX_EVIDENCE_DIR=/tmp/blueprint-redteam-sandbox \
.venv/bin/pytest -o addopts='' -q tests/test_redteam_sandbox.py \
  tests/test_phase6.py::test_live_hostile_process_filesystem_network_limits_and_teardown

.venv/bin/ruff check src/blueprint_ai/safety.py src/blueprint_ai/sandbox.py \
  tests/test_redteam_sandbox.py
.venv/bin/ruff format --check src/blueprint_ai/safety.py src/blueprint_ai/sandbox.py \
  tests/test_redteam_sandbox.py
.venv/bin/mypy src/blueprint_ai/safety.py src/blueprint_ai/sandbox.py
```

The focused gate contains 20 test cases, including 13 real container executions. Broad release
gates and the final verified commit are recorded by the coordinating independent release audit.
