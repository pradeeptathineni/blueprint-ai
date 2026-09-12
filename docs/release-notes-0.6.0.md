# Blueprint AI 0.6.0

Blueprint AI now creates and verifies a broader set of projects while isolating untrusted tool
execution and making optional coverage explicit.

- 31 starter families cover Python, TypeScript, Go, Rust, C#, Java, web applications,
  and local infrastructure foundations. Terraform, OpenTofu, and Pulumi cover AWS, Azure, and GCP.
  Kubernetes generation is limited to a structurally checked Namespace; Pulumi installs a cloud SDK
  and checks an empty program. Both are classified partial.
- A canonical registry describes 52 external tools and 17 providers, with 26 tool entries offering
  explicit managed OCI acquisition. `support` and `tools plan/install/doctor` expose the actual metadata.
- Docker, Linux Podman, and configured gVisor share a policy for read-only source, no default egress,
  non-root execution, resource limits, bounded output, and teardown. Untrusted work has no host fallback.
- `add` plans and transactionally applies 16 create-only capability kits, with conflict detection,
  asset verification, idempotence, and exact rollback of owned files and directories.
- Configured Conftest, ast-grep, and Buf add complementary checks. Syft retains SBOM inventory evidence.
  Maven, .NET, Pulumi, and framework discovery use bounded native manifest evidence.

The [independent release gate](phase-6-redteam.md) supersedes the original candidate self-review.
It includes all 31 generated families, native clean/bad/repaired scanner fixtures, nine unchanged
pinned public repositories, hostile OCI process checks, real React/full-stack browser workflows,
and fresh wheel/source installs. It records exact test/coverage counts and remaining limitations.
Corrections include fail-closed sandbox evidence, safe file reads, rollback ownership, scanner
output contracts, model authority/redaction, and installed Django application verification.

Use explicit OCI selection with provider trust when generating in isolation:

```bash
blueprint-ai tools plan go
blueprint-ai tools install go
blueprint-ai init ./hello --kind go-api --sandbox docker --trust-providers --allow-network
blueprint-ai add security-policy ./hello
blueprint-ai add security-policy ./hello --apply
```

Host execution now requires explicit project/provider trust. A previously available host executable
may be unavailable to an untrusted review until its isolated image is explicitly acquired. Existing
`auto` plus explicit trust retains the trusted host workflow.

Live validation used Docker Desktop and Linux containers. Podman/runsc and native Windows were not
live-certified. No cloud resources, hosted CI run, exhaustive browser coverage, or live model quality are
certified. Optional dependencies/databases and semantic review can remain partial even when native
starter verification passes. Unsupported generators and deliberately deferred tools are listed in
[the generated support matrix](support.md) and [gap inventory](phase-6-gaps.json).

Prepared as a local 0.6.0 release. No GitHub Release, remote push, or package-registry upload is implied.
