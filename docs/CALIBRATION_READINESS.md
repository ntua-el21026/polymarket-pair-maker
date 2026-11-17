# Execution-calibration readiness

The offline `research.calibration_readiness` module checks typed declarations,
source relationships, chronology and sample support. It does not fit estimates,
admit real markets or issue empirical acceptance. Existing `fit_calibration`,
its synthetic defaults, financial arithmetic and paper admission stay unchanged.

Run with the pinned project environment:

```bash
uv run --frozen --all-groups python -m polymarket_pair_maker.research.calibration_readiness input.json --output fresh-report.json
```

The input is `CalibrationReadinessInput`, canonically serialized using the existing
strict bounded codec: at most 8 MiB, 100,000 trials/sources, no duplicate JSON keys,
unknown fields, floats for monetary values, implicit coercion or dynamic types.
Reports publish atomically to a fresh file. Exit 0 means
`READY_FOR_INDEPENDENT_REVIEW`; exit 4 means `INSUFFICIENT_EVIDENCE` or
`SYNTHETIC_ONLY`; invalid input/output returns 3. None of these statuses is a
calibration, venue, collector or profitability PASS.

## Provenance and causal scope

Each trial wraps the existing `CalibrationObservation` with parent-event and
category identities, protocol version, paired ingest/payload references,
observed/inferred/synthetic attribution, censoring and terminal-exposure event and
availability times. Preserve unfilled and partial trials. Unknown order/inventory
termination excludes a trial rather than releasing hypothetical exposure.

The batch declares a market domain. Each condition has one declared parent/category;
conflicting source mappings fail. Support counts may span a cohort of distinct
markets and parents; a single condition cannot supply 20 independent parents.
This descriptive pooling does not change or call the existing market-scoped
estimator. Scope and cohort comparability still require independent review.

Each source declaration binds its source hash, collector run, raw completion hash,
collector and venue qualification report hashes/statuses, market/protocol and
report availability at analysis time. Its `times` field dates the reference known
and effective when the historical quote was formed. A later run completion or
review report need not exist before its own quotes; the quote's rules must.
Unknown effective start, superseded/expired or future-known references block
support. Report availability must be known by the declared analysis cutoff.

Hashes identify declarations. They do not authenticate executions, report contents,
parent assignment or the time of preregistration. The producer and independent
review must verify actual raw membership, source/completion integrity, identities,
venue deployment/assets/effective intervals, upstream qualification reports and
ground truth. Renaming duplicate episodes cannot manufacture independent support.
An aggregate L2 decrement cannot establish specific maker execution or priority.
`INFERRED_L2` trials remain visible but never count as observed executions.

## Frozen population and support

Declare complete UTC-day population bounds before inspecting outcomes. Assign
60/20/20 percent of declared days to calibration, validation and confirmation
(integer floors at 60% and 80%; half-open boundaries). A parent belongs to its
earliest partition; its later-partition rows are excluded. The boundary embargo
is 300 seconds plus an explicitly finite maximum outstanding-order or inventory
holding lifetime. Check observed exposure and residence against that bound.
An unavailable bound, censored trial, unavailable exposure or outcome cannot
qualify support. Outcome/exposure availability at the partition end is excluded.

The frozen Task 3 minimum remains 100 complete prior episodes, 20 parents and 10
UTC days per stratum. This readiness tool additionally requires 100 available
filled observations **per markout horizon** before marking that horizon supported;
this is consistent with the existing estimator's filled/horizon support checks
when called with `minimum_samples=100`. It does not replace the frozen episode
threshold or modify the estimator. All no-fill trials remain in
the episode denominator, and missing horizons remain missing. The existing
1/10/60/300 second join selects the first causally available executable observation
within the one-second lateness limit. This readiness layer checks the supplied
joined observations; it does not prove from declarations that no earlier raw point
was omitted. A point at the partition end or without
sufficient declared liquidation depth does not count. Prices/quantities and
source identity require independent reconstruction from raw evidence.

Episodes overlap. Counts do not establish statistical independence. Subsequent
confirmation retains the Task 3 requirements: 60 parents, 30 UTC days, three
categories with at least 10 parents each, 100 confirmation episodes per stratum,
at least 30 connected parent/day blocks and effective sample size 30, component
capital-time concentration at most 10%, 20,000 blocked-bootstrap replicates with
seed 20261117 and one-sided 97.5% lower bounds for the two registered claims.
The readiness report leaves confirmation economics `NOT_EVALUATED`; no tiny
synthetic bootstrap, confidence bound or profit estimate is generated.

## Required real observations

| Target | Minimum direct evidence and limitation |
| --- | --- |
| Fill probability, partial fills and quote survival | Complete eligible quote episodes, including no quote, rejected, unfilled and censored periods; exact order/ack/fill identities, cumulative quantities and terminal status. Public counterfactual L2 fills remain inference. |
| Queue uncertainty and trade pressure | Reconciled snapshots/deltas/trades, event/availability times, gaps and directional uncertainty. Individual queue priority needs independent order-level evidence; retain conservative uncertainty bounds otherwise. |
| Cancellation risk | Request, acknowledgement, reconciliation and intervening fill identities, with recorded send/cancel delays. Unknown terminal status remains reserved/censored. |
| Adverse selection | Exact filled quantity and executable bid sweeps at 1/10/60/300 seconds, available by the analysis cutoff; missing depth/horizons unavailable. |
| Liquidation and slippage | Full relevant bid depth, tick/share rules, exact taker fees, sweep VWAP and separately declared incremental impact. No midpoint liquidation. |
| Collateral and inventory duration | Reservations, partial releases, cash/inventory transitions, known terminal exposure and capital-time denominator; retain unresolved positions. |
| Resolution exposure | Authoritative condition, payout/finality, timestamp and asset scope; unresolved resolution has no invented settlement. |
| Incentive revenue | Effective eligibility/sampling/allocation rules plus independently reconciled payment records. Estimates and unverified rebates/rewards are zero in conservative realized cash. |

Current Task 6 source/data manifests contain no qualified execution population.
The actual-input readiness result is `INSUFFICIENT_EVIDENCE`; synthetic fixtures
test mechanics only. No calibration or profitability optimization is authorized
by a readiness result. See the external Task 6 handoff for current blockers and
the draft verification/collection requests; neither has been sent or executed.
