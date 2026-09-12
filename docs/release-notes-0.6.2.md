# Blueprint AI 0.6.2

This focused release makes Blueprint AI straightforward to obtain and independently verify.

- The Python distribution is now `blueprint-ai-cli`, while the installed command remains
  `blueprint-ai` and the import remains `blueprint_ai`.
- A tag-driven workflow builds the wheel and source archive once, smoke-tests fresh installs, records
  SHA-256 checksums, emits an SPDX SBOM, creates GitHub build/SBOM attestations, and publishes the
  assets in a GitHub Release.
- PyPI Trusted Publishing is repository-ready through a dedicated `pypi` environment and job-scoped
  OIDC. Publication remains disabled until the matching PyPI publisher and repository opt-in are
  configured; no long-lived registry token is accepted.
- The existing OpenAI Responses provider remains a bounded semantic reviewer, distinct from any
  future coding-agent backend. Controlled tests cover structured output, telemetry, call/token
  budgets, cache behavior, failures, redaction, prompt-injection isolation, and no-model operation.

Python 3.12 or newer is required. Run `blueprint-ai doctor` after installation; model support is
optional and `blueprint-ai review . --no-model` performs no model request.
