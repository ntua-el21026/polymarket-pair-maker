# ADR-0001: Focused foundation and explicit safety boundaries

Status: accepted for the repository migration, 2026-11-16.

## Decision

Use a Python modular monolith for international Polymarket ordinary binary
markets. Retain exact values, immutable raw journals, strict schemas,
provenance-bound canonical events, bounded queues/storage, single ownership of
mutable book state, versioned reference data, and deterministic recorded-order
replay. Keep public acquisition, interpretation, diagnostics, and future paper
execution as explicit boundaries.

Remove the broad strategy catalog and complete-set taker scanner. Reuse binary
complement semantics, market/reference validation, exact book state, freshness,
and recovery admission primitives. No placeholder strategy, reward, risk, or
execution modules are required for this migration.

Keep ordinary-GIL CPython 3.14.7, uv 0.12.5, pinned production/development tools,
strict Pyright, Ruff, deterministic/property tests, package builds, pre-commit,
and dependency auditing. Each retained production dependency has a current
consumer. Defer the official SDK until compatibility is resolved and an actual
implementation requires it.

Preserve persisted identities across the Python package rename. Historical hash
domains, schema identifiers, Parquet metadata/columns, and control channels stay
unchanged where they are required to decode or identify existing records. Typed
ID hashing explicitly preserves the historical fully qualified wire identity;
there is one current Python import namespace. A new derived processor contract
must disclose intentional terminal-hash changes rather than changing golden
values without justification.

Keep historical failed-soak reports byte-exact with a checksum inventory.
Historical document references resolve against their recorded source commit.
The historical collector acceptance result remains `FAIL`; local migration
checks do not certify long-run recovery correctness.

Provide public observation, research diagnostics, and replay. Specify future
paper execution without adding live capability, signers, trading credentials,
jurisdiction routing, or a paper-to-live toggle. Any later live-capable work
requires separate explicit authorization, authoritative eligibility verification,
and independent acceptance. Preserve owner-paused GitHub Actions.

## Consequences

The current source is smaller and serves one research objective. Existing raw
data can retain its identity and decoding contract. Historical scanner-derived
aggregate results remain reproducible at their original source/configuration,
while new derived results use their declared migrated contract. Strategy and
profitability claims remain blocked until their implementation and independent
validation gates supply evidence.
