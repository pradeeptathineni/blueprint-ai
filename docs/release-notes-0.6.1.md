# Blueprint AI 0.6.1

This maintenance release closes validation and operational-observability gaps without beginning the
broader Phase 7 evolution work.

- Hosted CI now has regression-tested triggers for pull requests, `main`, no-`v` release tags, and
  manual dispatch. Linux quality work is no longer repeated across every Python version, and a native
  Windows smoke job covers representative portable workflows.
- The OpenAI provider remains a bounded Responses API integration with strict Structured Outputs,
  `store=False`, and zero SDK retries. Operator-owned reasoning and timeout settings plus cached-input
  and reasoning-token metrics make live behavior more observable. No coding-agent backend was added.
- Sandbox network evidence uses `none` and explicitly authorized `unrestricted`. Internet-only and
  allowlist modes fail closed because the local OCI adapters cannot guarantee destination filtering.
- Sandbox evidence distinguishes bounded scratch and individual files, an enforced read-only
  workspace, and the absence of a portable total quota for trusted writable bind mounts.
- The canonical 52-tool registry now projects one intentional support classification per entry.

Docker policy was rerun on the local macOS Docker Desktop Linux VM. Podman and runsc were not present;
their detection and failure behavior remain tested without claiming live execution. No live model
credential was available, so provider quality is not fabricated. Native Windows status is determined
only by the hosted `windows-latest` result for the final release commit.
