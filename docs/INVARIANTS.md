# Invariants

These requirements govern the retained foundation and implemented Task 2 paper
system. Local regression evidence and independent empirical acceptance remain
distinct; the implementation handoff records executed checks.

## Retained data foundation

1. **Exact financial arithmetic.** Prices, quantities, rates, fees, and money use
   exact representations. Financial paths reject binary-float inputs. Tick and
   size quantization is explicit, bounded, and directionally specified.
2. **Immutable raw observations.** Preserve application payload bytes before
   interpretation. Never rewrite a historical record to repair an interpretation.
   Payload, segment, and completion hashes must validate before trusted replay.
3. **Complete provenance.** Derived records retain raw ingest identities, payload
   hashes, reference versions, source/configuration versions, and run identity.
   Conflicting identities for one raw source fail closed. Credentials and
   unrestricted request objects cannot enter raw transport metadata or logs.
4. **Canonical identity.** Market and token identities bind exact native IDs and
   their venue. A metadata update creates a new version without changing stable
   identity. Import renaming cannot silently change stored IDs or hash domains.
5. **Strict schemas.** Unknown/invalid required fields, duplicate JSON keys,
   unsupported formats, indeterminate rules, and contradictory references produce
   explicit typed outcomes. Missing evidence cannot become an affirmative result.
6. **Deterministic replay.** Verified durable recorded order, as-known-at-time
   references, exact timestamps, and the declared configuration determine state.
   Replay performs no fresh network lookups and does not invent missing controls.
7. **No silent loss.** Sequence gaps, duplicates, queue saturation, rejected
   records, interruptions, and incomplete output are explicit. A derived output
   completion claim requires reconciled accepted/processed/rejected evidence.
8. **Fail-closed state.** Stale, future-relative, inconsistent, crossed,
   uncertain, or audit-ineligible books cannot support an affirmative admission
   decision. Recovery must satisfy its own canonical evidence contract.
9. **Bounded resources.** Queues, message sizes, journal segments/runs,
   analytical buffers, histories, and diagnostics have enforced limits.
   Exhaustion records failure instead of dropping evidence or increasing a bound
   implicitly. Collection setup and measured execution remain distinguishable.
10. **Full-depth ownership.** Simultaneous logical mutations respect canonical
    event ordering. Reported BBO cannot overwrite full-depth liquidity. Venue
    timestamps and opaque hashes cannot substitute for project-owned exact-state
    comparison or a proven REST/WebSocket ordering relation.
11. **YES/NO semantics.** Complementarity requires verified ordinary YES and NO
    tokens in the same condition, with explicit payout asset and settlement
    rules. UI similarity and price sums do not prove a settlement relationship.

## Strategy and paper execution

12. **Optional exposure.** Every decision permits `NO_QUOTE`. A pair is optional;
    no rule requires symmetric quoting or participation in an ineligible market.
13. **Conservative fills.** Unknown queue position remains uncertainty. Touching
    a quote does not establish a fill. Partial fills, latency, queue-ahead,
    cancel-in-flight exposure, and uncertain responses remain explicit.
14. **Versioned economics.** Fees, liquidity rewards, and maker rebates are
    separate, dated rule sets with validated source provenance. Unknown costs or
    qualification cannot be credited as realized income. Estimated rewards and
    actual reconciled payouts remain distinct.
15. **Accurate accounting.** Every fill reconciles cash, fees, token inventory,
    outstanding orders, and reserved exposure. Spread capture, paired settlement,
    mark-to-market, and liquidation proceeds cannot double-count the same value.
    Resolution and unresolved terminal inventory use declared valuation rules.
16. **Atomic exposure reservation.** Outstanding orders and uncertain responses
    consume inventory/capital capacity. The same capacity cannot fund concurrent
    quotes twice. Cancel requests release exposure only on sufficient evidence.
17. **No automatic live transition.** Paper actions cannot invoke a signer or
    live-order transport. A future live-capable task requires explicit user
    authorization, authoritative eligibility verification, and separate
    acceptance. Historical/paper P&L does not grant that authorization.
18. **Economic evidence boundaries.** Synthetic midpoint, fee or calibration
    conventions cannot qualify observed market data. Unsupported midpoint
    reconstruction, unknown rounding, asset mismatches and incomplete competitor
    depth stay unavailable. A snapshot share bound cannot establish daily income.
19. **Causal estimation.** Calibration precedes evaluation and uses observations
    whose availability is already known. Missing 1s/10s/60s/300s markouts remain
    missing. Training and evaluation periods cannot overlap.
20. **Independent scenarios and liquidity budgets.** Pessimistic, conservative
    and sensitivity portfolios are separate. A pressure event cannot fund more
    than its declared liquidity across hypothetical orders; a price touch or
    unattributed depth reduction is never a verified execution.
21. **Daily incentive integrity.** UTC-day samples are deduplicated; contradictory
    identities fail. Payout thresholds apply to complete scoped daily earnings,
    with no liquidity-reward rollover. Estimated rewards/rebates and verified
    payments occupy separate fields. Paper code cannot create verified payments.
22. **Fresh derived destinations.** Paper output cannot overlap raw journals or
    replace existing results. Runtime provenance and terminal content hashes
    identify each reproducible result independently of historical processor hashes.

Validation must preserve negative findings and unavailable evidence.
Migration checks, strategy tests, and empirical acceptance remain separate under
[VALIDATION_GATES.md](VALIDATION_GATES.md).
