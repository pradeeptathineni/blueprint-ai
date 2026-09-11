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

Blueprint AI treats analyzed repositories and tool output as untrusted data. Tool commands are fixed
argument arrays and run locally with timeouts. Model review is optional, receives redacted bounded
context, and cannot apply changes. Intrusive/network security testing is never inferred from project
content and requires an explicitly authorized target adapter.
