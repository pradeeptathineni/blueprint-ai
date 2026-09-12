# Untrusted repository threat model

The target repository, its Git metadata, names, configuration, source, manifests, model context, and
tool output are hostile inputs. The primary assets are host files outside the target, credentials and
environment variables, network authority, terminal integrity, compute/memory/disk, and the accuracy
of findings and remediation state.

Controls include:

- file discovery rejects symlinks, non-regular files, generated/vendor trees, and files over 2 MB;
  configuration/model/cache/manifest reads have tighter limits;
- Git and tools use argument arrays, no shell, timeouts, noninteractive controlled environments, and
  bounded output with terminal escape/control sanitization;
- target-supplied executable overrides, wrappers, and package-local binaries are disabled unless the
  operator explicitly trusts them; repository YAML cannot define commands or imports;
- offline mode disables model and adapters classified as potentially networked; DAST additionally
  requires an explicit credential-free HTTP(S) target;
- model input is ranked before invocation, bounded, secret-redacted, content-delimited, tagged as
  untrusted data, versioned, measurable, optional, and never authoritative scanner evidence;
- remediation preflights all paths, refuses symlink parents and existing paths, atomically creates
  files, records exact hashes, and rolls back writes if its own transaction encounters a conflict;
  rollback removes only byte-identical files from the matching validated operation manifest.

Residual limits: installed third-party tools execute with the current OS account and are not an OS
sandbox. Their internal parsers and network behavior remain upstream trust decisions. Very large Git
indexes can still make Git itself expensive before Blueprint AI receives output. Run high-risk targets
inside an OS/container sandbox with network disabled and resource quotas.

## Phase 5 changes

Read-only scanner execution uses external disposable HOME/cache directories and no-cache flags,
including Ruff, mypy, Go, Cargo, and Trivy. Trivy database access is serialized and reused within
a process to avoid repeated downloads; the cache is removed on process exit. Compile/test tools remain trust-gated when they can load
project code. Module tools run at their owning roots; project-local TypeScript is required rather
than silently substituting an incompatible global compiler. Native parsers and bounded source reads
never execute manifests. Output truncation and malformed diagnostic envelopes are incomplete evidence.

Lychee receives an explicit neutral config, HTTP(S) schemes, and `--exclude-all-private` so target
configuration cannot override loopback/private/link-local exclusions. It still depends on upstream
DNS/redirect enforcement. Trivy receives a neutral config and external caches. Automatic remote
Terraform-module resolution is unsupported where the graph discovers external module calls: scanner
flags that hide downloaded findings do not prevent downloads. Offline mode remains the strongest
application-level default for unknown targets; an OS network sandbox is required for a hard egress
boundary. `--authorize-target` only enables the documented ZAP scan, not arbitrary provider endpoints.

Genesis has a separate explicit provider-execution and network boundary. It accepts only registered
providers and validated intent, verifies fetched-provider registry integrity, rejects path/plan
conflicts, and publishes fresh output with atomic no-replace semantics. Dependency resolution and
Docker builds execute trusted publisher/generated code with the OS account's privileges. No credentials,
remote GitHub writes, cloud state changes, signing, or release operations are implicit. See
[the genesis contract](genesis.md) for rollback and partial-publication behavior.
