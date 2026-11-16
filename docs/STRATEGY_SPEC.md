# Conservative Adaptive Pair Making

This is the normative contract for Prompt 2. The decision engine, reward model,
inventory ledger and paper order lifecycle are specified here and remain
unimplemented after the migration. Their profitability is empirically unvalidated.
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

Reference checked on 2026-11-16: [official methodology](https://docs.polymarket.com/programs/liquidity-rewards)
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

Prompt 2 must specify and test the precise size-cutoff midpoint reconstruction,
complementary-book grouping, boundary units and rounding against authoritative
examples and available venue scoring observations. Fail closed when those rules
or qualifying depth cannot be established. Recompute qualification after inserting
our hypothetical quotes: they can move the midpoint. Preserve original book
observations separately. Polymarket US and perpetual-market incentives supply no
parameters to this model.

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
Current pages mix legacy USDC field names and pUSD descriptions; the reference
model must identify assets explicitly and reject an unresolved mismatch.

Maintain a cash-and-position ledger as the authoritative economic identity:
terminal wealth minus initial wealth, adjusted for external flows, with all fills,
costs and actually reconciled income represented once. Report spread/entry
economics, paired inventory economics, rewards, rebates, exit fees, slippage,
post-fill adverse selection, capital commitment and tail exposure as
reconcilable attribution. Complete-set payout and mark-to-market cannot both
count the same gain. Estimated incentives remain estimates until reconciliation.

Rank candidates by a conservative estimate of realized net wealth change less
declared capital and tail-risk charges over a fixed horizon. Prompt 2 must freeze
the estimators, horizon, risk limits, uncertainty scenarios and calibration data
before confirmation. Rewards cannot be assumed to rescue losing execution.

## Fill model and lifecycle

The paper simulator must track queue ahead, explicit queue uncertainty, partial
fills, order-response uncertainty, submission/cancel latency and cancel-in-flight
exposure. Touching a price is insufficient evidence of a fill. Public depth
reductions do not automatically establish trades or cancellations ahead of us.
Declare pessimistic queue scenarios and prevent reuse of historical liquidity
across simulated fills. Account for fill-after-cancel-request and deduplicate
execution reports; release reservations only on a reconciled terminal outcome.

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

Prompt 2 implements the deterministic engine, reference rules, paper lifecycle,
conservative fills, risk reservations and accounting with meaningful tests.
Prompt 3 evaluates independent acceptance. Historical or paper profit does not
authorize live orders. No signed order adapter, trading credentials or live toggle
is part of this specification's authorized implementation.
