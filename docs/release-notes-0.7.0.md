# Blueprint AI 0.7.0

Blueprint AI can now safely plan and execute supported evolution of existing Git projects.

- `blueprint-ai evolve catalog|plan|apply` exposes a strict, sealed desired-state workflow with
  candidate inspection, dry-run diffs, ordered steps, provenance, staged verification, and compact
  before/plan/change/after reports.
- Five transformations are supported: isolated Ruff Python syntax modernization, Go 1.26+ native
  fixes, Terraform native formatting, deprecated Dockerfile maintainer conversion, and immutable
  pinning for reviewed official GitHub Actions tags.
- Evolution refuses implicit main/dirty mutation and implicit host tools, bounds checkpoints and
  tool execution, isolates caches and network, enforces planned file scope, auto-restores failures,
  rejects rollback conflicts, and verifies exact restoration.
- Eleven researched migration families remain honestly partial, experimental, or deferred where
  target intent, licensing, runtime behavior, state, or agent authorization is unresolved.
- A reproducible migration corpus and Create React App 5.0.1 dogfood prove actual diffs, native tests,
  idempotency, deterministic review comparison, repeatability, and exact rollback with zero model or
  agent calls.

Python 3.12 or newer is required. Evolution requires Git. External transformation tools are never
installed or executed on the host without explicit operator trust.
