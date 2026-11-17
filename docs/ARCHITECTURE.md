# Architecture

## Modular monolith and ownership

The system is a Python modular monolith with one deterministic owner for mutable
canonical book state. Public network acquisition, durable evidence, canonical
processing, and derived research outputs have explicit boundaries. Bounded
in-process queues isolate acquisition and storage without introducing services.

The intended strategy flow is:

```text
Validated market data → Market eligibility → Candidate quotes
    → Economic evaluation → Risk decision → Paper execution
    → Accounting and replay
```

The implementation supplies the market-data foundation and a separate internal
paper layer. The strategy consumes immutable accepted canonical states through
an explicit processor transition interface. It does not own a second market-data
pipeline. The contract in [STRATEGY_SPEC.md](STRATEGY_SPEC.md) defines admission,
economics and simulation assumptions.

| Current package boundary | Responsibility |
| --- | --- |
| `core/` | Exact financial values, typed IDs, canonical serialization, time, provenance, strict configuration, validation, and typed errors |
| `venues/polymarket/` | Public international Polymarket reads, bounded retries/rate budgets, strict response schemas, and public streaming |
| `reference/`, `semantic/` | Immutable versioned market/token/precision/cost data and proof of ordinary same-condition YES/NO complements |
| `ingestion/`, `journal/` | Immutable raw envelopes, durable append, bounded segments, completion manifests, integrity checking, and interrupted-prefix recovery |
| `normalization/`, `state/order_books/` | Exact canonical events, incremental book updates, snapshots, and fail-closed state ownership |
| `market_data/eligibility.py` | Bounded binary-book data eligibility assessment, independent of strategy economics |
| `pipeline/` | Acquisition coordination, freshness, reported-top transitions, REST audits, recovery, and deterministic processing |
| `observability/` | Allowlisted structured logs, metrics, bounded monotonic checkpoints, availability intervals, and host evidence |
| `storage/`, `research/` | Provenance-bound Parquet output, bounded analytical queries, reconstruction, data validation, and causal diagnostics |
| `replay/`, `cli/` | Verified recorded-order journal replay and explicit public/read-only commands |
| `strategy/` | Bounded tick/size candidates, market ranking, conservative scenario objective, cached evaluation and safety-aware hysteresis |
| `economics/` | Market-scoped bitemporal evidence, exact reward scores and bounds, fees, rebates and daily payout estimates |
| `research/paper_calibration.py` | Chronological prior-only statistics, horizon markouts and missing-data qualification |
| `risk/` | Atomic capacity admission, market/parent/portfolio limits, directional bounds and emergency stop |
| `paper/` | Internal order state machine, queue scenarios, ledger, strict offline evidence/configuration, replay sessions and artifacts |

`PaperSimulator` owns simulated orders; `PaperLedger` owns collateral,
reservations, positions and accounting; `RiskController` owns the halt state.
Exact pure computations remain separate from these mutable owners. Each queue
scenario has an independent portfolio. Their profits cannot be added together.

Financial cash values retain `ExactDecimal`, `Money`, `Price` and `Quantity`.
Reward division can recur in base ten, so exact intermediate scores use a
serializable `ExactRatio(numerator, denominator)`. Exported decimal share bounds
round down/up explicitly. Existing source, journal and historical hash domains
remain unchanged.

## Observation and recovery

Acquisition preserves exact application payload bytes and allowlisted transport
metadata before downstream interpretation. Canonical processing consumes the
durable observation order. References bind native conditions and token IDs to
stable identities and immutable rule versions. Unknown precision, unsupported
binary semantics, malformed data, stale books, crossed books, and unresolved
audit divergence exclude the affected state from research admission.

The collector distinguishes a book baseline from global research eligibility.
A race-free REST mismatch is recorded permanently, invalidates affected state,
and requests a separately journaled authoritative resnapshot. Current audit
contracts additionally require complete, fresh replacement WebSocket capture and
a durable validated release control. A successful REST response alone cannot
restore eligibility. Historical v1 controls retain their documented narrow replay
compatibility path. Reported top-of-book values are diagnostic evidence; they
cannot fabricate full-depth liquidity.

Current health output uses `ppm-health-checkpoint-v1`; chronology validation also
accepts the historical Gate-1 v1/v2/v3 checkpoint formats. Current calibration
uses `ppm-pre-soak-calibration-v1`. These derived schemas are versioned separately
from immutable journals.

The code retains these safeguards and their adversarial regressions. The failed
six-hour acceptance status remains open. Local tests establish implementation
behavior for tested inputs; they do not establish lossless venue delivery or a
production-ready collector.

`BinaryBookEligibilityEvaluator.assess()` produces a
`BinaryBookEligibilityAssessment` with disposition `BLOCKED` or `ELIGIBLE` under
`BinaryBookEligibilityConfig`. It checks existing binary semantics, current
references, book identity/health, freshness, ticks, and coherence. An `ELIGIBLE`
result means that these data prerequisites are satisfied; it is not a strategy
market-selection decision, quote, or economic opportunity. The `[eligibility]`
section in `config/phase1.toml` sets enablement, maximum book age, and maximum
interleg skew. Analytical output uses dataset `book_eligibility` and schema
`ppm-binary-book-eligibility-flat-v1`.

## Replay and historical compatibility

Raw envelopes, sequence order, timestamps, source bytes, manifests, canonical
identities, and historical schema domains are data contracts. Replay is
authoritative only with `RECORDED_ORDER` and `AS_KNOWN_AT_TIME`. Derived datasets
can be rebuilt from verified journals into a fresh disjoint destination.

The package namespace is `polymarket_pair_maker`; a second import namespace is
unnecessary. Historical strings remain only where changing them would change
persisted identities or decoding. These include the `multi-market-arbitrage-id-v1`
hash domain, the historical fully qualified typed-ID identity used inside that
hash, `mma.control.*` channels, versioned `mma-*` content-hash domains, and
`mma_schema_version`/`mma_provenance_sha256` Parquet metadata and related columns.
They are wire identifiers, not current project branding. Legacy typed-ID class
identity is mapped explicitly at hashing. Regression vectors pin the original
values across the import rename. The byte-exact
`tests/golden/fixtures/historical_raw_envelope_v1.json` fixture also pins the
pre-rename raw-envelope encoding and identity. Remove such compatibility only with an explicit
new data-format contract and a demonstrated migration for retained inputs.

Removing the historical taker scanner changes the derived processor contract to
the explicitly versioned `ppm-processor-summary-v1`.
Raw decoding and canonical identities remain compatible; scanner-era aggregate
terminal results must be reproduced with their original source/configuration.
Do not compare terminal hashes from different processor contracts or regenerate
golden expected values to conceal a change. The historical source is retained in
Git and the verified external migration bundle.

## Runtime and dependencies

The runtime policy remains ordinary-GIL CPython 3.14.7, uv 0.12.5, exact direct
pins, and a committed transitive lock. The six production dependencies each serve
retained code: `httpx` and `websockets` acquire public data; `pydantic` validates
configuration; `zstandard` compresses raw journals; `pyarrow` writes and validates
analytical data; `duckdb` provides bounded analytical reads. Tests and tooling
retain pytest, asyncio support, Hypothesis, Ruff, strict Pyright and stubs,
pre-commit, build tooling, and pip-audit. No SDK or new infrastructure is added
solely for planned functionality.

The official SDK is **deferred**, as recorded in
[ADR-0003](ADR/0003-defer-official-sdk.md). Its examined dependency ranges conflict
with retained transport/Arrow pins and its exact runtime qualification is absent.
Task 2 keeps the existing public HTTPX/WebSocket transport. Offline paper replay
performs no new reference reads. Unavailable authoritative reference contracts
remain unavailable; no endpoint or rounding policy is inferred from SDK symmetry.

Observation, research and paper execution use explicit entry points and
configuration. There is no signer, trading credential flow, live
executor, or paper-to-live toggle. GitHub Actions remains manually paused by its
owner; the migration does not re-enable it.

## Paper evidence and outputs

`ppm paper replay` requires a verified completion manifest and processes recorded
ingest order with `AS_KNOWN_AT_TIME` references. Strict `ppm-paper-evidence-v1`
documents bind any extra calibration/reference/pressure evidence to the run and
completion hash. Unknown fields, numeric coercions, synthetic/observed mixing,
future references and contradictory samples fail closed. A new paper result
identity coexists with the unchanged foundation terminal hash.

Output is bounded and written to a fresh directory disjoint from source runs.
Session records identify code/runtime/dependencies, configuration and source
hashes, rule/calibration versions, seed and scenario. Decisions, simulated
orders/fills, ledger snapshots, economic attribution and incentive uncertainty
remain independently inspectable. Outstanding orders and unresolved inventory
remain explicit at interruption or replay end; no cancellation or settlement is
invented to make a terminal balance look complete.

## Paper configuration and evidence format

[`config/paper.toml`](../config/paper.toml) maps to `PaperConfig`, with nested
`StrategyConfig`, `RiskLimits` and `ExecutionConfig`. Economic decimals are quoted
strings, durations are integer nanoseconds and boolean fields require booleans.
Unknown keys, floats in financial fields, inconsistent safety limits and
unsupported versions are rejected. `inventory_exit_policy` accepts only
`ON_ABSTENTION_OR_STOP_V1`. Its fresh-depth internal taker sweep begins after
outstanding orders reconcile, at a recorded boundary, with declared fees,
slippage and impact. Insufficient depth or fee evidence leaves inventory
explicitly unresolved. Transport loss latches a halt and uncertain orders;
the session never clears these through an implicit reconnection.

The optional `evidence_file` resolves relative to the paper configuration and
uses strict JSON with the field names in
[`paper/evidence.py`](../src/polymarket_pair_maker/paper/evidence.py).
`PaperEvidence` contains the schema/classification, collector run/completion
hash, qualification declaration and source hash, scoped `MarketEvidence` records,
and optional ingest-bound `PressureInput` records. `MarketEvidence` contains
dated `RuleEvidence`, `FeeRules`, `RewardRules`, risk declarations and prior
`CalibrationPeriod`/`CalibrationObservation` records. IDs and exact decimals are
strings; canonical financial scalar objects use `{"value":"..."}`; UTC
timestamps use ISO-8601 ending in `Z`. Canonical serialization is deterministic
JSON with sorted keys. The decoder also accepts a plain decimal string for a
financial scalar, without allowing a JSON number to become money implicitly.

The decoder selects known dataclasses in code, never imports a type named by
input, and rejects duplicate/unknown fields, excessive nesting and input sizes.
Evidence must bind the exact completed run. Every pressure boundary must be
consumed; late execution evidence requiring unavailable order reconciliation
fails before releasing capacity. A `VERIFIED_OBSERVATIONS` declaration cannot
contain synthetic rules/calibration/pressure. Hash and schema validation establish
identity and consistency; independent source review must establish the truth of
qualification/calibration declarations. The parser does not turn a supplied
`PASS` label into independent collector acceptance.

## Normalized paper artifact contract

New sessions use `ppm-paper-session-v2` and `ppm-paper-artifacts-v2`.
`paper/normalization.py` owns compact serialization and exact typed reconstruction;
`paper/verification.py` checks completed inventories, canonical records, scoped
references, chronology, state chains and terminal result identities. Full
candidate evaluations have one authoritative payload representation with scoped
boundary bindings; decisions and selections reference those bindings. Exact
consecutive equal payloads reuse the latest per-market content, with bounded
producer/reconstructor retention. Strategy dataclasses and
policy retain their existing in-memory meanings. Per-scenario record indices
make cross-stream ordering independently recoverable with bounded state. Fixed
code-selected decoder schemas use a bounded metadata cache; validation still
checks every input value.

See [ADR-0004](ADR/0004-normalized-paper-artifacts.md) for the failure mechanism,
version/compatibility boundary and integrity contract. The 256 MiB cumulative
artifact budget and 16 MiB per-record bound remain enforced. Incomplete staging
output is retained without a successful publication. Historical v1 results and
raw sources remain read-only inputs; v2 serialization/state-chain hashes identify
a new representation and are not compared as equal to historical hashes.

## Exact computation reuse

[ADR-0005](ADR/0005-evidence-based-computation-reuse.md) records the measured
Task 5 computation bottlenecks and cache contracts. Immutable snapshot identity
and canonical encoding reuse preserve existing bytes and hash domains. Candidate
generation and evaluation caches retain complete immutable keys with explicit
bounds. Every decision still checks current data, references and calibration.
The Task 4 v2 artifact format, verification and publication contract is retained.

Task 6 adds two narrowly scoped pure optimizations under
[ADR-0006](ADR/0006-causal-readiness-and-exact-subset-composition.md): two exact
immutable bid-total cells per engine, and strict economic-subset byte composition
within the existing owner memo/sequence proof bounds. No economic state is shared.

## Offline execution-calibration readiness

`research/calibration_readiness.py` wraps existing typed observations with declared
multi-market source, parent/category, execution attribution and terminal-exposure
provenance. It reuses the strict code-selected decoder, validates chronology and
scope, and atomically publishes a deterministic fresh report. It is network-free
and independent of paper admission and the existing estimator. Complete-day
splits, embargoes, missing horizons and descriptive support are documented in
[CALIBRATION_READINESS.md](CALIBRATION_READINESS.md). Declared hashes/statuses do
not authenticate upstream evidence; readiness requires independent review.

## Task 7A operational readiness

The project-local [tool launcher](REPRODUCIBILITY.md) selects the exact uv version
without changing the global toolchain. It is a development boundary, independent
of financial or paper execution code.

Pre-soak workload calibration now emits explicit v2 reports/bindings that include
availability-ledger bytes, interval counts, queue pressure and writer integrity.
[ADR-0007](ADR/0007-complete-availability-capacity-projection.md) records the
independently reproduced omission and narrow correction. Original v1 evidence
retains its narrower scope; no in-place migration or new acceptance claim occurs.
Execution calibration and its empirical requirements remain separate and unchanged.

The [prospective collector preflight](COLLECTOR_PREFLIGHT.md) prepares a fresh
source/configuration/lock/target lineage and independent qualification procedure.
It grants no collection permission. Missing historical reconstruction cannot be
replaced by relabeling a new run, and current venue-rule uncertainty continues
to block economic admission.
