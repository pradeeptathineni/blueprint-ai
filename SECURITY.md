# Security policy

## Supported versions

The latest published 0.9.x release receives best-effort security fixes until 1.0.0. Starting with
1.0.0, only the latest 1.x patch is security-supported; fixes are not routinely backported to
superseded minors or patches. The 1.x line will remain security-supported until at least 90 days
after 2.0.0, with the end date announced here.

Security fixes normally preserve the [1.x compatibility contract](docs/compatibility.md). A patch
may disable or remove unsafe behavior when preserving it would expose users, credentials, hosts, or
targets. Such an exception is documented prominently in the advisory and changelog, and Blueprint AI
continues to fail closed where a safe compatible behavior is unavailable.

## Reporting a vulnerability

Do not open a public issue containing credentials, exploit details, or private source. Use the
repository host's private vulnerability-reporting feature. Include the affected version, minimal
reproduction, impact, and any suggested mitigation. Maintainers should acknowledge a report within
five business days and coordinate disclosure after a fix is available.

## Trust boundaries

Blueprint AI treats analyzed repositories, Git metadata, config, filenames, local executables, model
context, and tool output as untrusted data. Tool commands are fixed argument arrays and run with an
isolated home, constrained environment, bounded sanitized output, and process-group timeouts. Tools
that execute target code or executable configuration require `--trust-project-executables`.
Model review is optional, receives redacted bounded context, and cannot apply changes. Network DAST
is never authorized by project content; the operator must repeat the exact scope with
`--authorize-target`. See [the threat model](docs/threat-model.md) for controls and residual risk.
