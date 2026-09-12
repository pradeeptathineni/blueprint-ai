# Blueprint AI 0.6.0

Blueprint AI now creates and verifies a broader set of projects while isolating untrusted tool
execution and making optional coverage explicit.

- 31 native project families cover Python, TypeScript, Go, Rust, C#, Java, web applications,
  and local infrastructure foundations. Terraform, OpenTofu, and Pulumi cover AWS, Azure, and GCP.
- A canonical registry describes 52 external tools and 17 providers, with 26 tool entries offering
  explicit managed OCI acquisition. `support` and `tools plan/install/doctor` expose the actual metadata.
- Docker, Linux Podman, and configured gVisor share a policy for read-only source, no default egress,
  non-root execution, resource limits, bounded output, and teardown. Untrusted work has no host fallback.
- `add` plans and transactionally applies 16 create-only capability kits, with conflict detection,
  asset verification, idempotence, and exact rollback of owned files and directories.
- Configured Conftest, ast-grep, and Buf add complementary checks. Syft retains SBOM inventory evidence.
  Maven, .NET, Pulumi, and framework discovery use bounded native manifest evidence.

The release gate includes 256 tests, all 31 generated families across 46 distinct exercised cases,
eight native clean/bad/repaired scanner fixtures, nine unchanged pinned public repositories, hostile
OCI process checks, and fresh wheel/source installs. Full evidence, reproduction commands, performance,
and the observed 78.8% statement coverage are in the [validation report](phase-6-validation.md).

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
live-certified. No cloud resources, hosted CI run, browser E2E journey, or live model quality are
certified. Optional dependencies/databases and semantic review can remain partial even when native
starter verification passes. Unsupported generators and deliberately deferred tools are listed in
[the generated support matrix](support.md) and [gap inventory](phase-6-gaps.json).

Prepared as a local 0.6.0 release. No GitHub Release, remote push, or package-registry upload is implied.
