# Security policy

## Supported versions

Security fixes are made on the current minor release. This project is pre-1.0; upgrade to the latest
release before reporting an issue that may already be resolved.

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
