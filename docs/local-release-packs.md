# Local release packs

`blueprint-ai release-pack preview` reviews exact selected tracked files for one managed project. It shows file hashes and omissions, selected registered capability records and maintenance receipts, and the actual text of selected `LICENSE`, `COPYING`, and `NOTICE` files. Selection remains explicit; Blueprint does not walk every source file into the archive. Untracked, private-inventory-excluded, symlinked, binary, unreadable, and oversized selections are omitted and block pack creation.

`blueprint-ai release-pack build` creates a deterministic ZIP with a `PROVENANCE.json` manifest. The manifest records the base commit, whether the working tree was dirty, an independent Blueprint source-snapshot digest, exact file hashes, selected receipt/capability evidence, and qualifications. It does not claim dirty bytes came from the base commit. The ZIP is unsigned and local; it is not a signed attestation, release-quality result, publication, deployment, or legal compatibility judgment. `blueprint-ai release-pack verify <pack.zip>` reopens and checks the exact pack bytes and each selected file without extracting into a project.

For a multi-package project, native graph ownership groups only explicitly selected files under their exact package component and records the package manifests. Selected files without a known package owner remain listed as unmapped; selection does not expand to dependencies or imply that omitted package files are complete. The verifier checks that package groups and unmapped paths cover the archive's selected files exactly. Older schema-1 packs without package groups remain readable.

Deep can optionally attach a saved Planning assessment to a selected project pack. It cold-reads the current assessment and specification, requires the exact Planning source binding to cite this configured Blueprint target and its retained review, then rereads the report and confirms that its source digest matches the current pack snapshot. Stale, missing, ambiguous, or unavailable evidence is retained only as `unknown`; the ordinary pack-only path remains available. The copied readiness means `ready_for_human_review` at the captured source snapshot only. It is not release, signature, legal, publication, or deployment approval. The manifest carries only bounded assessment/specification/report identities and counts, not private gate notes or requirement prose.

Example:

```bash
blueprint-ai release-pack preview --project ./checkout \
  --file README.md --file LICENSE --file src/package.py
blueprint-ai release-pack build --project ./checkout \
  --output ../package-candidate.zip \
  --file README.md --file LICENSE --file src/package.py
blueprint-ai release-pack verify ../package-candidate.zip
```

The Deep Projects panel uses the same native owner and stores the exact ZIP and manifest in its private, configured artifact directory. A cold reopen verifies the archive and checksum. Comparison rechecks the configured project's current source snapshot against the prior pack without publishing or modifying the project.
