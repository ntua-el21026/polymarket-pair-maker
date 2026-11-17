# Conservative Adaptive Pair Making

This is the implemented Task 2 strategy contract. The decision engine, reward
mathematics, inventory ledger and paper lifecycle have controlled regression
tests. Their profitability is empirically unvalidated. Real-market admission
remains blocked when authoritative rules or trustworthy calibration are absent.
The sole objective is risk-adjusted realized net profitability from selective
liquidity provision on the international Polymarket CLOB. `NO_QUOTE` is a valid,
often optimal decision.

## Eligibility before ranking

Admit only ordinary binary YES/NO markets with validated complementary token
identity, clear resolution rules, current supported protocol/reference versions,
healthy books and sufficient depth to liquidate conservatively. Exclude
negative-risk groups, unresolved market semantics, unavailable reference rules,
stale or inconsistent state, unsupported precision and markets outside configured
time-to-resolution, exposure or event-risk limits. Exclusions run before scoring.

Among eligible markets, rank conservative expected net income relative to capital
and downside exposure using current reward availability, book quality, estimated
adverse selection, executable exit depth and time until resolution. An advertised
reward pool alone establishes neither eligibility nor value. Selection must use
only information available at the decision time; retain excluded markets and
their reasons to audit selection bias.

## Decisions and candidate quotes

The complete action set is `NO_QUOTE`, `QUOTE_YES`, `QUOTE_NO`, `QUOTE_PAIR`.
YES and NO denote collateral-backed buy quotes on the respective outcome tokens;
a pair may have unequal prices or sizes. Inventory-reducing sell/exit decisions
belong to the paper execution and risk lifecycle and must be accounted for
explicitly. Paired quoting is optional and neither leg's execution implies that
the other filled.

Evaluate a small configuration-bounded grid of valid integer price ticks and
permitted sizes. Start with the reward-qualifying minimum size when its exposure
and net economics are acceptable; this is a candidate choice, not an obligation
to quote. Validate current tick and size constraints, post-only behavior, available
collateral/token balances and market/event/portfolio inventory limits. Reserve
exposure atomically before accepting an intent. All comparisons use exact values.

Choose `NO_QUOTE` when no candidate has positive conservative economic value after
costs and risk charges, or when uncertainty prevents that conclusion. Record the
candidate bounds, rejected alternatives, rule versions, input hashes and decision
reason. Deterministic ties prefer lower exposure, then stable candidate ordering.

## International liquidity rewards

Reference checked on 2026-11-17: [official methodology](https://docs.polymarket.com/programs/liquidity-rewards)
and [market settings](https://docs.polymarket.com/market-data/market-details).
Use the size-cutoff-adjusted midpoint, not an unfiltered best-price average.
For qualifying size within maximum distance `v`, score per share is
`S(v,s) = ((v-s)/v)^2 * b`, with distance `s` and applicable multiplier `b`.
The two economic sides combine YES bids with NO asks, and YES asks with NO bids;
the side scores `Q_yes`, `Q_no` below mean these groups, not raw token totals.

For adjusted midpoint in `[0.10, 0.90]`, the documented rule is
`Q = max(min(Q_yes,Q_no), max(Q_yes,Q_no)/c)`, currently `c = 3`.
Outside this range, `Q = min(Q_yes,Q_no)`; one-sided liquidity scores zero.
Per-sample shares are normalized across makers, summed through the UTC day,
then normalized for the allocation. Sampling occurs once per minute at a random
offset, up to 1,440 observations daily. Current minimum size, maximum spread,
allocation dates, multiplier and eligibility rules require versioned reference
data; a reward-list entry may have no active allocation.

The [official age rule](https://docs.polymarket.com/programs/liquidity-rewards#minimum-order-age)
normally counts resting orders immediately. Applicable taker-delay markets impose
the corresponding age requirement; scheduled sports apply it after game start.
The documented short crypto/finance delay does not impose this minimum age.
The [Help Center payout rule](https://help.polymarket.com/en/articles/13364466-liquidity-rewards)
requires at least $1 earned for a day; smaller amounts are unpaid and do not roll
over. Payment is around midnight UTC. Apply the floor to reward earnings, not
net P&L. Do not infer undocumented per-market aggregation from third-party code.

The official pages specify minimum size in shares and maximum spread in cents,
but do not specify the size-cutoff midpoint aggregation or rounding algorithm.
Consequently `RewardRules.midpoint_convention=None` is the current real-market
outcome, and strategy admission fails closed. An independently documented and
tested venue convention would require a subsequent implementation change; a
configuration flag cannot promote the synthetic convention into venue truth.

The **SYNTHETIC** fixture convention projects YES bids/NO asks to one economic
bid side and YES asks/NO bids to the economic ask side, aggregates each price,
walks cumulative quantity to the cutoff, and averages the two cutoff prices
without rounding. Hypothetical orders participate in midpoint reconstruction
but remain outside competitor mass. Canonical books remain immutable. This
declared fixture algorithm tests cutoff depth and counterfactual midpoint
movement; it is not an asserted Polymarket reconstruction algorithm.

Regression tests reproduce both printed worked-example numeric expressions.
The official Step 3 order labels contradict the complementary grouping in its
Equations 2/3; tests preserve that discrepancy and use the equations for actual
grouping. Polymarket US and perpetual incentives supply no model parameters.

## Proposed wallet-independent reward-share bound

This is our analytical proposal, not profitability evidence. For nonnegative
individual maker side scores `a,b` in the documented middle range and `c=3`,

```text
(a+b)/4 <= Q(a,b) <= (a+b)/2.
```

Proof: take `a >= b`. If `b >= a/3`, then `Q=b`; `a <= 3b` proves the lower
bound and `b <= a` the upper bound. Otherwise `Q=a/3`; `b <= a/3` proves the
lower bound, and `2a <= 3(a+b)` proves the upper bound. The all-zero case holds.

If `T` is the sum of all competitors' eligible side scores, summing the bounds
gives `T/4 <= sum(Q_competitor) <= T/2` regardless of allocation to wallets.
For our known eligible score `q > 0`, instantaneous share therefore satisfies

```text
q/(q+T/2) <= share <= q/(q+T/4).
```

Unknown competitor order age or eligibility requires interval estimates for `T`.
A conservative lower share can use an upper bound on competitor score mass and
a lower bound on our eligible score. Missing competitor liquidity invalidates
an asserted upper bound. Zero own score earns zero; an all-zero denominator is
an undefined sampling state and earns no assumed payout.

This result assumes additive correctly reconstructed side scores, the same
middle-range formula for all participants and a common reference snapshot. It
does not apply to the outer-range `min` rule. Public L2 books do not establish
wallet ownership, individual order age or exact hidden eligibility. Ineligible
own orders earn zero. Midpoint movement, sampling, changing competition and
daily thresholds separate an instantaneous bound from payment. Officially
reconciled earnings are the final authority. Report lower/upper scenarios and
unavailable bounds; do not present a snapshot share as a daily entitlement.

## Fees, rebates and net economics

[Maker rebates](https://docs.polymarket.com/programs/maker-rebates) compensate
executed maker liquidity in fee-enabled markets and are distinct from rewards
for resting liquidity. Current documentation allocates rebates by fee-equivalent
share within each market, with daily payment and a $1 minimum. Fee-free fills
earn no maker rebate. Category percentages can change.

[Fees](https://docs.polymarket.com/trading/fees) currently describe taker cost as
`shares * feeRate * p * (1-p)`, zero maker fees, and five-decimal fee rounding.
Validate the applicable market, protocol, fee version, collateral/reward asset,
rounding and effective time rather than fixing these observations in code.
Current pages mix legacy USDC field names and pUSD descriptions. They state
five-decimal precision and a sub-quantum zero rule without specifying a
tie-rounding mode. The new fee model requires an explicit asset and rounding
reference and rejects unresolved mismatch/rounding. Synthetic tests declare
their own rounding mode; they do not amend the retained foundation cost rules
or establish a live fee convention.

Maintain a cash-and-position ledger as the authoritative economic identity:
terminal wealth minus initial wealth, adjusted for external flows, with all fills,
costs and actually reconciled income represented once. Report spread/entry
economics, paired inventory economics, rewards, rebates, exit fees, slippage,
post-fill adverse selection, capital commitment and tail exposure as
reconcilable attribution. Complete-set payout and mark-to-market cannot both
count the same gain. Estimated incentives remain estimates until reconciliation.

Rank candidates by conservative net wealth change per committed capital after
declared capital and tail-risk charges. Objective
`ppm-robust-execution-return-on-capital-v1` uses prior
calibrated fill/markout scenarios, maker and conservative taker exit fees,
liquidation/slippage/impact assumptions and downside exposure. All required
scenarios must remain positive. Potential reward and maker rebate contributions
are zero for admission. Ties prefer higher conservative economic score, lower
downside, lower capital, then stable candidate identity.

The configured grid has up to three price offsets and two size multipliers for
each outcome: at most six single-leg candidates per outcome and 36 pairs, plus
`NO_QUOTE` (49 total). One market and one passive bid per outcome can be active.
Bounded ranking preserves all excluded-market reasons. Cached candidate
evaluation invalidates on relevant books, references, calibration, capacity or
configuration. Minimum replacement intervals and hysteresis reduce churn;
safety invalidation bypasses them immediately in the paper lifecycle.

Estimator `ppm-prior-empirical-scenarios-v1` requires prior chronological observations, declared source hashes,
versions and separate evaluation periods. It includes 1s/10s/60s/300s markouts;
absent future observations are missing. Synthetic calibration establishes test
behavior only. The default real replay has no trustworthy empirical calibration
and therefore abstains. Freeze independent calibration and scenario choices
before Task 3 confirmation. Sample minima, maxima and means are declared
descriptive scenarios, not population confidence bounds. Adverse scenarios use
an upper rounded fill fraction; favorable scenarios use a lower rounded fraction.

## Fill model and lifecycle

The paper simulator must track queue ahead, explicit queue uncertainty, partial
fills, order-response uncertainty, submission/cancel latency and cancel-in-flight
exposure. Touching a price is insufficient evidence of a fill. Public depth
reductions do not automatically establish trades or cancellations ahead of us.
Declare pessimistic queue scenarios and prevent reuse of historical liquidity
across simulated fills. Account for fill-after-cancel-request and deduplicate
execution reports; release reservations only on a reconciled terminal outcome.

The internal state machine implements created, pending acknowledgement, resting,
partial fill, filled, cancel requested, cancelled, expired, rejected and unknown
states. Pending/uncertain orders and cancellation races retain reservations.
Contradictory reports fail closed; duplicates are idempotent. Strict pessimistic,
bounded conservative and sensitivity queue assumptions run as independent
portfolios. Public depth changes do not identify cancellations or executions
without additional evidence. Historical pressure has a shared consumption
budget per scenario, and simulated fills never claim verified venue execution.
Latency transitions occur at recorded availability boundaries. Delayed execution
evidence that overlaps a prior simulated acknowledgement/cancellation requires
reconciliation and fails closed when that reconciliation cannot be established.
Transport loss leaves uncertain orders and reservations explicit.

Inventory exit policy `ON_ABSTENTION_OR_STOP_V1` requests an internal taker sweep
after abstention or a risk stop. It waits for all potentially executable orders
to reconcile, then uses accepted fresh bid depth at the next recorded boundary.
Each exit charges explicit taker fees, slippage and impact. Consumed depth is
tracked per instrument and price for the session, including subsequent executable
valuations. Freshness, metadata and unrelated price updates do not replenish it;
this conservative rule can understate actual new liquidity. Missing depth or fee
references leaves inventory unresolved; partial exits retain the remainder. A
disconnected session keeps its halt and unknown orders until explicit reconciliation, so it cannot silently
liquidate against stale observations or fabricate a terminal cancellation.

Evaluate depth-aware liquidation, post-fill markouts at declared horizons and
exposure through resolution. Estimate benign queue-clearing opportunities
relative to toxic-fill hazards using simple calibrated deterministic statistics
before advanced models. Independent event groups and chronological held-out data
must challenge the estimates. Report absence of identified trade direction and
counterfactual market impact as limitations rather than inferred certainty.

## Performance and delivery boundary

Use incremental book updates, integer ticks, bounded candidate counts, event-driven
re-evaluation and cached computations with explicit invalidation. Quote hysteresis
must reduce unnecessary cancel/replace churn without retaining stale unsafe quotes.
Measure latency distributions, queue saturation and memory on declared hardware;
benchmarks must include failure/recovery paths. A complex optimizer, machine
learning model or another implementation language requires benchmark evidence
and separate authorization.

Task 2 implements the deterministic engine, reference rules, paper lifecycle,
conservative fills, risk reservations and accounting with meaningful tests.
Task 3 evaluates independent acceptance. Historical or paper profit does not
authorize live orders. No signed order adapter, trading credentials or live toggle
is part of this specification's authorized implementation.
