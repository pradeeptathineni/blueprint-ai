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
