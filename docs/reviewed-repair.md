# Reviewed repair of one finding

The ordinary `apply` command applies every supported safe remediation from a fresh review. For a
single inspected finding, `repair prepare` creates an external, read-only plan from a saved JSON
review. It accepts only one active deterministic Blueprint finding with a declared safe
remediation that creates one missing file, confirms that exact finding against a new deterministic
review, and records a complete native source snapshot. `prepare` prints the finding, remediation,
and complete bounded proposed file content for human review; that content is bound into the digest.

```bash
blueprint-ai review ./project --blueprint repository --model off --json > /tmp/review.json
blueprint-ai repair prepare --project ./project --report /tmp/review.json \
  --finding 7d2a8c991d2f0abc --output /tmp/repair-plan.json
blueprint-ai repair apply --project ./project --plan /tmp/repair-plan.json \
  --confirm-digest <digest-printed-by-prepare>
blueprint-ai repair status --project ./project --plan /tmp/repair-plan.json
```

The plan is outside the project tree so it does not invalidate its own source snapshot. The
explicit `apply` command requires the plan digest; it refuses a modified plan, a changed source
snapshot, a changed target or proposed content, or a finding whose current deterministic evidence differs. It delegates
the one finding to the existing safe remediation engine and re-runs only that finding's blueprint
with model review and external tools disabled. The receipt records whether that same fingerprint
remains. A missing finding is only a scoped post-repair check; it is not independent review, a
provider trial, or proof of production quality.

New plans retain the complete original inspection, including manual findings, tool limitations,
profile and configuration digest. The apply receipt returns that history under
`inspection_report_not_rerun` alongside the narrow `verification_report`. The original report
can contain sensitive project details. Keep the external plan and receipts with the project owner.
Older plans remain readable and return null for unavailable historical evidence.

If the apply reply is lost, use `repair status` with the original plan before considering another
apply. The operation manifest records the plan digest with the same create-only write used for
ordinary rollback receipts. Status reads those bounded local records and compares the current
target against the exact proposed bytes. It never applies a change or reruns a checker.
`applied-content-present` confirms matching retained evidence and current content.
`conflicted-or-absent` preserves uncertainty about a changed or missing target.
`unconfirmed` means no matching receipt was found, including after rollback. It never means a
retry is safe. A crash between file creation and manifest persistence remains unconfirmed.
These project-local records are unsigned and do not authenticate a release.

Rollback uses the operation ID returned by apply or recovered by status:

```bash
blueprint-ai rollback OPERATION_ID ./project --json
```

This repair creates a previously absent file, so restoration removes that file only when its
bytes still match the retained digest. Later authored edits cause a conflict and remain intact.
Keep the conflict receipt. If you deliberately restore the exact reviewed bytes after preserving
your edit elsewhere, retry the same rollback. The original external plan retains inspection
history after a successful rollback removes the active operation manifest.

Sandbox SB02 paired-comparison packets may be useful context for a human deciding whether a finding
matters, but they do not authorize changes or verify a Blueprint repair. This initial receiving path
does not consume an SB02 packet; it preserves the finding's native evidence and exact local source
binding instead of guessing a cross-system finding identity.
