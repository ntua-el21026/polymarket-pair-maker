# Project specification

## Objective and scope

`polymarket-pair-maker` develops and validates a selective liquidity-provision
strategy that maximizes realistic risk-adjusted net profitability after adverse
selection, execution costs, reward uncertainty, and capital exposure. The
strategy concept is **Conservative Adaptive Pair Making**. Participation is
optional: `NO_QUOTE` is an affirmative decision when conservative economics do
not justify exposure.

The venue is the **international Polymarket CLOB at polymarket.com**. Polymarket
US is a separate platform whose incentive and execution rules are outside this
project's contract. Supported instruments are ordinary binary YES/NO tokens
belonging to the same verified condition. Negative-risk conversion sets,
augmented outcomes, ambiguous settlement relationships, and unverified token
mappings are excluded.

Changeable precision, fees, rewards, market status, and exchange rules must come
from validated and versioned current reference data. Dated local configuration
and historical fixtures are evidence of their declared dates, rather than proof
that a rule remains current.

## Modes and current status

| Capability | Task 2 status |
| --- | --- |
| Public market discovery, bounded HTTP/WebSocket collection, and explicit-target observation | Implemented; collector acceptance remains unresolved |
| Exact arithmetic, strict parsing, reference reconciliation, binary complement proof, normalized books, and provenance | Implemented and covered by retained local regressions |
| Durable raw journals, deterministic recorded-order replay, analytical reconstruction, integrity validation, and structured health evidence | Implemented; historical full-corpus reproduction requires external artifacts |
| Selective actions `NO_QUOTE`, `QUOTE_YES`, `QUOTE_NO`, `QUOTE_PAIR` | IMPLEMENTED; TESTED SYNTHETICALLY |
| Reward mathematics, explicit unknowns, fees and executed-maker rebate estimates | IMPLEMENTED; official numeric examples and synthetic edge cases tested; venue midpoint reconstruction BLOCKED by missing authoritative detail |
| Conservative queue scenarios, risk reservations, exact inventory/cash ledger and paper lifecycle | IMPLEMENTED; TESTED SYNTHETICALLY |
| Offline paper journal replay and synthetic demonstration | IMPLEMENTED; empirical admission requires separately qualified evidence |
| Strategy profitability, calibrated fill assumptions, and execution performance | Empirically unvalidated |
| Authenticated order placement and live deployment | Unimplemented and blocked pending separate authorization, acceptance, and verified eligibility |

The retained collector roles are `OBSERVE`, `RESEARCH`, and `REPLAY`. Internal
paper execution has separate CLI commands and result identities. It reuses
accepted canonical book transitions; it cannot invoke a production order
transport. No role authorizes signed trades or an automatic mode transition.

## Deliverables and boundaries

The retained foundation supplies immutable observation, canonical data,
reference validation, binary semantics, reconstruction, replay, diagnostics, and
reproducible local quality checks. Task 2 adds the deterministic strategy,
fee/reward model, conservative simulator, inventory/risk accounting, and paper
lifecycle described in [STRATEGY_SPEC.md](STRATEGY_SPEC.md). Task 3 must evaluate
them independently under [VALIDATION_GATES.md](VALIDATION_GATES.md).

An absent or expired economic reference, missing calibration, an unqualified
collector run, or unresolved market risk blocks an affirmative quote. Default
paper configuration deliberately supplies no empirical edge. Synthetic evidence
is explicitly classified and cannot be attached to observed-data replay as
verified calibration. Estimated rewards/rebates never become simulated cash or
verified wallet income. Independent empirical acceptance is **EMPIRICALLY
UNVALIDATED**; live execution and other venues remain **OUT OF SCOPE**.

Excluded functionality includes additional venues, exchange synchronization,
cross-venue settlement, taker-arbitrage strategies, generalized arbitrage graphs,
arbitrary multi-leg orchestration, funding and derivatives strategies, unrelated
portfolio management, ML/RL systems, dashboards, distributed services, speculative
adapters, and infrastructure without a concrete retained requirement. Complete
YES/NO sets remain relevant settlement and accounting primitives; the old
complete-set taker scanner is outside the active strategy.

## Deployment gate

Legal and geographic eligibility is unresolved, particularly for access from
Greece. Authoritative verification of the applicable Hellenic Gaming Commission
entry and its implications is required before any live-capable deployment.
Venue geoblocking responses are supplementary evidence. They do not establish
compliance with local restrictions. Research, public observation, replay, and
paper simulation remain limited to activities permitted in the user's actual
jurisdiction. Routing through another jurisdiction to evade restrictions is
outside scope.

Live capability requires a separate user-authorized task and independent
acceptance. Historical or paper profitability does not provide that authorization.
