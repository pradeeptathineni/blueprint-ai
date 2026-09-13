# Blueprint AI 0.9.1

Blueprint AI 0.9.1 is a focused stabilization release that closes the identified 1.0-readiness
gaps without adding a migration capability, agent backend, or new architecture.

- A tag cannot build until the complete reusable CI gate passes. The resulting wheel and source
  archive are built once, installed independently, published in an exact immutable GitHub Release,
  checked against release digests, and verified against their build and SBOM attestations.
- The release SBOM now describes a clean wheel-installed runtime instead of the development checkout.
  Its exact package inventory is checked against the environment, development dependencies and source
  paths are rejected, and the SBOM file itself receives provenance before publication.
- PyPI publication uses the protected `pypi` environment and OIDC Trusted Publishing. A post-publish
  job verifies the exact version metadata, two-file membership, GitHub-bound hashes, downloaded bytes,
  canonical links, and fresh `pipx` and `uv` installations from the production registry.
- The intended 1.x CLI, exit-code, configuration, schema, report, Python API, deprecation, and security
  policy is explicit and enforced by a deterministic compatibility snapshot.
- New remediation and evolution records carry the exact Blueprint AI producer version. Cross-version
  use fails closed; legacy records without that field retain bounded best-effort compatibility.
- Next.js codemod stages no longer claim an existing or prospective lockfile as a writable path; that
  file remains exclusive to sealed manual acceptance. Exact replay ignores only explicitly declared
  transaction-private caches, so safe cleanup cannot be mistaken for a public mutation.
- Documentation no longer implies that a declared destination is a network allowlist. The optional
  OpenAI provider remains a bounded preview and is not represented as live-validated without a release
  credential.

No production `AgentBackend` was added, and no post-1.0 capability work is included.
