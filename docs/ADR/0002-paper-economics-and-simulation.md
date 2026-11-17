# ADR-0002: Exact paper economics and evidence-qualified simulation

Status: accepted for Task 2, 2026-11-17.

## Decision

Implement internal paper execution as a bounded extension of accepted canonical
book transitions. Preserve raw journals, recorded order, as-known references and
historical terminal hash contracts. Use separate versioned paper identities and
fresh disjoint outputs. Mutable orders, ledger and risk state have explicit
owners; core economic computations remain pure.

Support four selective quote actions and one active market. Rank bounded
tick/size candidates using conservative prior-calibrated execution scenarios
after exit costs, capital charges and downside. Unverified incentives contribute
zero. Missing evidence causes abstention. Synthetic calibration cannot qualify
observed data. Hysteresis never delays safety invalidation.

Keep exact decimal cash values. Preserve recurring reward scores as rational
integer pairs and round exported bounds directionally. Scope economics by
market, protocol, source hash and knowledge/effective validity. Preserve the
official mathematical reward formula, age exceptions, complementary grouping,
conditional share bound and daily threshold. Do not infer complete competition,
wallet eligibility, random samples or paid income from aggregate public depth.

The official reward documentation does not specify midpoint reconstruction.
Only a named synthetic cumulative-depth convention is implemented, and its
reference is rejected for observed-data qualification. Fee rounding ties and
USDC/pUSD denomination require additional evidence. Unknowns remain explicit;
historical foundation rules are not silently rewritten.

Run pessimistic, conservative and sensitivity queue scenarios independently.
Touching a price is insufficient to fill. Pressure consumes a shared liquidity
budget and retains its observed/inferred/synthetic classification. Reservations
cover both potentially executable legs through pending states and cancel races.
Duplicate reports are idempotent and contradictions fail closed. Liquidation
sweeps available bids with explicit costs; missing depth leaves unresolved
inventory. Complete-set value, mark-to-market and liquidation are alternative
valuations, not additive income.

The configured exit policy is `ON_ABSTENTION_OR_STOP_V1`: after outstanding
orders reconcile, abstention or a stop triggers an internal taker sweep using
fresh accepted bid depth at a recorded boundary. Missing depth/fee evidence
preserves unresolved inventory. Disconnects leave orders unknown and halt the
session until explicit reconciliation. Delayed pressure overlapping a prior
active interval fails closed when reconciliation is unavailable, before capacity
is released.

## Consequences

The synthetic demo can exercise quotes, partial fills, cancellation and exact
accounting without asserting venue profitability. Default real replay abstains
without qualified references and calibration. Estimated incentives never become
wallet payments. Independent Task 3 must qualify empirical execution, economics,
performance and inherited collector gaps. Live execution remains structurally
unavailable.
