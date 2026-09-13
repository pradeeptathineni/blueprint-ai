# Blueprint AI 1.x compatibility contract

This contract becomes effective at 1.0.0. Version 0.9.1 establishes its fixtures and release gate so
the promise is enforced before the stable release. Blueprint AI follows semantic versioning for the
surfaces below: compatible additions may ship in a minor release, fixes in a patch, and incompatible
changes require 2.0.0 except for the explicit safety and sealed-state exceptions.

## Stable surfaces

- **Runtime:** Python 3.12, 3.13, and 3.14 remain supported throughout 1.x. Later Python versions may
  be added. Supported operating-system behavior is the portable Python CLI and the documented
  Linux-container boundary; unavailable local engines and tools remain explicit incomplete results.
- **CLI:** existing command names, positional arguments, option names, meanings, defaults, and
  machine-output selection remain compatible. New commands and optional flags may be added. Human
  prose, help wrapping, ordering, and diagnostics may improve without notice.
- **Exit codes:** `0` is success or a completed result below the selected policy threshold; `1` is a
  completed check that reports the requested policy failure or the wrapped sandbox command's nonzero
  result; `2` is invalid input, configuration, authorization, prerequisite, or an operational error
  before a valid result; `3` is a valid structured transaction result that failed, remained partial,
  conflicted, or could not be safely published. Signals and interpreter failures remain platform
  behavior rather than Blueprint AI exit codes.
- **Configuration:** `.blueprint-ai.yml`, operator environment variables documented in this
  repository, and every field in `blueprint-ai schema settings` retain their meaning and accepted
  values. New fields are optional. Unknown fields continue to fail closed. Safer limits may tighten
  in a security patch only under the exception below.
- **Schemas and reports:** names emitted by `blueprint-ai schema`, their versioned required fields,
  field types, enum values, and meanings remain compatible within the same schema major. JSON,
  SARIF 2.1.0, and JUnit retain their documented structures; optional fields may be added and
  consumers must ignore unknown fields. Markdown and ordinary terminal prose are human interfaces,
  not byte-stable formats. A schema-major change is explicit and does not silently reuse old data.
- **Public Python:** `blueprint_ai.__version__` and names exported through `__all__` by
  `blueprint_ai.adapters`, `blueprint_ai.blueprints`, `blueprint_ai.core`,
  `blueprint_ai.discovery`, `blueprint_ai.evolution`, `blueprint_ai.genesis`, and
  `blueprint_ai.model` remain importable with compatible required parameters and model fields.
  Underscored names, direct implementation-module imports, catalog ordering, and object identity are
  internal. New optional parameters, fields, exports, enum values, and subclasses are compatible.
- **Support claims:** `supported`, `partial`, `deferred`, and unavailable outcomes retain their
  evidence meanings. A claim may be narrowed when real evidence disproves it; the changelog and
  generated support registry must say so rather than preserving an unsafe or false promise.

The golden fixtures under `tests/fixtures/compatibility/` preserve the 1.0 baseline. CI rejects
removed CLI or Python names, newly required parameters, narrowed schemas, changed configuration
defaults, and incompatible report shapes. Deliberate compatible additions require regenerating and
reviewing the fixture; a test edit alone does not authorize a breaking change.

## Deprecation and security

A stable surface is deprecated in documentation and emits a targeted warning where runtime use is
detectable. It remains functional for the rest of 1.x and may be removed in 2.0.0. Renames provide a
1.x alias or migration path. Deprecation warnings are not emitted for ordinary successful use.

A security patch may disable unsafe behavior or tighten a default without a deprecation period when
compatibility would expose a user, credential, host, or target. The advisory and changelog must name
the exception and safe replacement. The current security-support window and private reporting route
are in [the security policy](../SECURITY.md).

## Exact-version sealed-state exception

Evolution plans and operation receipts are execution authority, not ordinary interchange data. New
plans and receipts record the exact Blueprint AI producer version and may be applied, accepted, or
rolled back only by that version, even within 1.x. This prevents a changed catalog, verifier, parser,
or rollback implementation from silently reinterpreting sealed authority. Finish or roll back an
open transaction before upgrading; otherwise install the recorded version in an isolated tool
environment to complete it. Legacy receipts without a producer field retain their existing bounded
format handling but receive no forward-compatibility promise.

Read-only reports and schemas do not use this exception. Their explicit schema-version contract,
not the package patch version, governs compatibility.
