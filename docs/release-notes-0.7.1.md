# Blueprint AI 0.7.1

Blueprint AI 0.7.1 is a narrow correctness patch for the 0.7.0 evolution workflow.

- Managed evolution now honors canonical container executable paths such as Ruff's `/ruff`, while
  trusted host execution continues to resolve the host binary explicitly.
- Ruff pyupgrade now previews and applies its complete safe composition, including bounded unused
  import cleanup, without weakening the deterministic review gate.
- Next.js discovery and catalog applicability share the canonical `next` framework identity.
- Rust editions and SDK-style .NET target frameworks are parsed before old-version candidates are
  offered; Rust 2024 and net10.0 controls are correctly current.
- Mixed plans can expose and apply an independent ready prefix while retaining later manual work as
  an explicit partial boundary.

No migration family or agent backend is added. React, Next, Rust, .NET, OpenRewrite, Kubernetes,
PyPA, OpenTofu, and generic structural migration execution remain outside this patch.
