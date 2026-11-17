# ADR-0004: Lossless normalized paper artifacts

Status: accepted for Task 4, 2026-11-17.

## Problem and boundary

Task 3's valid 1,004-record synthetic paper workload exhausted the unchanged
268,435,456-byte artifact budget after 575 fully processed records. A market
ranking embedded selected and per-market decisions, each including candidate
evaluations; the selected decision and its evaluations were then exported again.
The resulting repetition consumed capacity without adding evidence.

This change concerns representation and verification. Candidate generation,
ranking, exact economics, eligibility, calibration, risk, execution, reservations,
inventory and liquidation retain their in-memory contracts. No input/candidate
count or output bound is reduced or increased to obtain completion.

## Decision

Use `ppm-paper-session-v2`, `ppm-paper-report-v2`,
`ppm-paper-artifacts-v2` and `ppm-paper-state-chain-v2` for newly produced
results. Normalize at the paper serialization boundary. Keep `StrategyDecision`,
`CandidateEvaluation` and `MarketRanking` unchanged.

An evaluation-payload record owns a complete evaluation tuple, identified by
canonical content, scenario, market and first ingestion boundary. Each economics
record binds its own scenario/ingest/market identity to that payload. Consecutive
equal complete tuples reuse the latest payload for that market; changed tuples
emit a new authoritative payload. Producer and reader retain only the latest
payload per bounded market, plus the current boundary relationships. A compact
decision references the scoped economics record and identifies its selected
candidate, reasons, flags, input hash and objective. Market selection references considered and selected decisions and
records the executed fallback separately when the original ranking has no
selection. Original fallback/retention/switching behavior remains recoverable.
A retained candidate outside the evaluation grid must remain explicitly
recoverable rather than being invented from a candidate ID.

Every stream row carries scenario, ingestion sequence and a contiguous per-owner
record index. These indices reconstruct cross-stream emission order. Canonical
hashes bind scoped content and references; the versioned state chain covers all
emitted records in order. The complete-file inventory and terminal result hash
bind metadata, source/configuration/evidence identities, reports and stream
bytes. These establish internal integrity; they do not authenticate venue rules
or counterfactual fills.

The typed reconstruction API rejects missing/conflicting/cross-scope references,
malformed/noncanonical JSON, duplicate keys, unsupported versions, chronology
errors, truncation, identity/hash mismatches and inventory discrepancies. Its
state is bounded to current decision relationships and the latest payload per
market. Fixed, code-selected decoding schema metadata uses a 128-entry bounded
cache; decoded input and financial values are never cached by that mechanism.
Successful replay is verified before publishing a fresh directory atomically. Failed output remains incomplete
and cannot acquire a valid completion claim through interruption.

## Compatibility and evidence

Immutable raw journals, historical evidence and foundation hash domains are
unchanged. Historical v1 paper artifacts are not rewritten or reinterpreted as
v2. Their explicit legacy verification boundary is documented with the verifier:
v1 file/terminal identities and typed logical reconstruction remain readable,
while cross-stream chain order cannot be recovered from absent record indices.
V2 terminal/state-chain bytes differ deliberately; economic equivalence is
shown through exact reconstructed values and independent prefix/full-run checks,
not assertions of old/new hash equality. The existing semantic accounting golden
remains authoritative and must pass without replacing historical values.

Measurements, complete regression inventory, deterministic outputs, old/new
size comparisons and remaining acceptance blockers are reported in the Task 4
engineering report. Representative production capacity, real fills, venue-rule
qualification and empirical profitability remain separate unresolved gates.
