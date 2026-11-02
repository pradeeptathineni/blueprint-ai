# Reviewed repair of one finding

The ordinary `apply` command applies every supported safe remediation from a fresh review. For a
single inspected finding, `repair prepare` creates an external, read-only plan from a saved JSON
review. It accepts only one active deterministic Blueprint finding with a declared safe
remediation, confirms that exact finding against a new deterministic review, and records a complete
native source snapshot plus the target's current SHA-256 (or explicit absence).

```bash
blueprint-ai review ./project --blueprint repository --model off --json > /tmp/review.json
blueprint-ai repair prepare --project ./project --report /tmp/review.json \
  --finding 7d2a8c991d2f0abc --output /tmp/repair-plan.json
blueprint-ai repair apply --project ./project --plan /tmp/repair-plan.json \
  --confirm-digest <digest-printed-by-prepare>
```

The plan is outside the project tree so it does not invalidate its own source snapshot. The
explicit `apply` command requires the plan digest; it refuses a modified plan, a changed source
snapshot, a changed target, or a finding whose current deterministic evidence differs. It delegates
the one finding to the existing safe remediation engine and re-runs only that finding's blueprint
with model review and external tools disabled. The receipt records whether that same fingerprint
remains. A missing finding is only a scoped post-repair check; it is not independent review, a
provider trial, or proof of production quality.

Sandbox SB02 paired-comparison packets may be useful context for a human deciding whether a finding
matters, but they do not authorize changes or verify a Blueprint repair. This initial receiving path
does not consume an SB02 packet; it preserves the finding's native evidence and exact local source
binding instead of guessing a cross-system finding identity.
