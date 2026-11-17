# Validation gates

The gates below distinguish repository correctness, functional behavior, and
independent empirical acceptance. Passing one gate makes no claim about another.
The migration report records executed commands and outcomes; this document
defines the requirements.

## Gate A — Migration and foundation

Require a coherent `polymarket_pair_maker` package and `ppm` CLI; a reproducible
locked environment; relevant code, tests, configuration, and documentation; no
obsolete active strategy; preserved Git history and branch-only tips; and clean
synchronized local/GitHub `main` after publication. Validate existing data and
replay contracts, historical fixture hashes, namespace-compatible IDs, strict
typing, lint/format, package installation/build, tests, and the configured
dependency audit. Report exact commands, exit results, test counts, failures,
skips, and unavailable artifacts.

Gate A also requires honest collector limitations. The first six-hour soak
remains **`GATE 1: FAIL`**; neither passing unit tests nor migration publication
changes that result. The full raw run is an external input absent from a clean
clone. The retained documents and sanitized fixtures establish recoverable
failure provenance, not a fresh empirical pass.

### Reproducible local checks

Run from the repository root with ordinary-GIL CPython 3.14.7 and uv 0.12.5:

```bash
uv lock --check
python3 scripts/check_repository.py
uv sync --frozen --all-groups
uv run --frozen --all-groups python -c 'import polymarket_pair_maker'
uv run --frozen --all-groups ppm --help
uv run --frozen --all-groups ruff check .
uv run --frozen --all-groups ruff format --check .
uv run --frozen --all-groups pyright
uv run --frozen --all-groups pytest -q
uv run --frozen --all-groups pre-commit run --all-files
uv build
```

The configured security check audits the locked environment, including development
and security groups. Use a fresh temporary directory and retain the audit output:

```bash
audit_dir="$(mktemp -d -t ppm-pip-audit-XXXXXXXX)"
mkdir -p "${audit_dir}/cache"
uv export --frozen --all-groups --no-emit-project --no-header --format pylock.toml --output-file "${audit_dir}/pylock.toml" > /dev/null
uv run --frozen --all-groups pip-audit --locked "${audit_dir}" --strict --vulnerability-service pypi --cache-dir "${audit_dir}/cache" --progress-spinner off --timeout 30
```

The vulnerability service and initial installation require network access.
Unavailable upstream services are reported as unavailable; an incomplete audit
is not a pass. Preserve dependency constraints and investigate findings before
claiming acceptance. GitHub Actions stays owner-paused. Public observation and
calibration are separate empirical commands, not prerequisites for offline tests.

## Gate B — Strategy implementation

Task 2 implements and tests the deterministic decision engine specified in
[STRATEGY_SPEC.md](STRATEGY_SPEC.md), including all four actions, eligibility
gates, bounded candidate generation, correct reward formulas, cost separation,
inventory/cash accounting, risk reservations, and conservative maker-fill
simulation. Require a reproducible paper-order lifecycle with partial fills,
latency, uncertain queue position, cancel races, response uncertainty, failure
handling, and exact replay of decisions/accounting.

Tests must exercise meaningful economic and state invariants, adversarial inputs,
unknown reference data, budget limits, and rejection paths. A positive synthetic
opportunity is not evidence of realizable profit. Collector acceptance gaps stay
visible and block claims dependent on reliable empirical market data.

The implementation handoff records actual Task 2 commands, counts, failures,
skips, deterministic hashes and a Gate B verdict. This specification does not
replace that execution evidence. The synthetic suite covers all four actions,
reward grouping/bounds/unknowns, fees, queue scenarios, lifecycle races, atomic
reservations, accounting and replay integrity. Official-source gaps intentionally
block real-market admission, rather than being waived for a positive demo.

### Paper reproduction

Use fresh output locations; existing output is never overwritten:

```bash
mkdir -p data/paper
uv run --frozen --all-groups ppm paper demo --paper-config config/paper.toml --output data/paper/demo-a
uv run --frozen --all-groups ppm paper demo --paper-config config/paper.toml --output data/paper/demo-b
diff -r data/paper/demo-a data/paper/demo-b
uv run --frozen --all-groups ppm paper replay data/raw --run-id <run-id> --config config/phase1.toml --paper-config config/paper.toml --output data/paper/<fresh-session>
uv run --frozen --all-groups pytest -q tests/unit tests/property tests/integration tests/golden
uv run --frozen --all-groups python -m benchmarks.paper_system --iterations 200 --output data/paper-benchmark.json
```

The default paper config has no empirical evidence. A complete real journal may
therefore produce a valid abstaining session. Corrupt/incomplete runs must be
rejected. A separately prepared offline evidence document binds economic rules,
calibration and collector qualification to a run/completion hash; a bare claim
or synthetic fixture cannot satisfy empirical qualification.

The benchmark measures decision evaluation, reward arithmetic and paper event
throughput with runtime/host/workload and bounded-state evidence. Setup is
outside measured loops. Local timing is not venue latency or an independent
performance acceptance result. No new long collector soak is part of Task 2.

## Gate C — Independent acceptance

Prompt 3 requires an independent GO/NO-GO verdict supported by:

- Repeatable historical replay with complete source, configuration, dependency,
  model/assumption, dataset, and trace fingerprints.
- Multiple independent event groups, chronological splits, out-of-sample
  evaluation, controlled seeds, and separation of exploration from confirmation.
- Conservative queue and latency scenarios, partial fills, adverse-selection
  markouts, realistic exits, capital exposure, and full-cost realized P&L.
- Explicit reward-estimation uncertainty, competition sensitivity, qualification
  changes, payout reconciliation, and conservative reward bounds.
- Stress tests and failure injection for stale/corrupt data, disconnects,
  backpressure, unknown responses, cancel races, and exhausted risk budgets.
- Measured latency and memory under a declared protocol, with setup separated
  from measured execution and actual hardware/runtime recorded.
- Collector correctness and recovery evidence sufficient for the claims being
  assessed, including the independent acceptance still missing after the failed
  six-hour run. A new long soak requires its own explicit authorization.

Gate C is unexecuted at migration. Historical or paper profitability alone
does not authorize live execution. Live capability remains outside the
three-task sequence and requires explicit authorization, verified legal/geographic
eligibility, and its own acceptance gates.

## Task 4 — Artifact remediation and performance requalification

Require lossless typed reconstruction of every evaluation, decision and market
ranking, exact economic equivalence to the original shared complete prefix,
independent verification of the complete normalized run, and deterministic
byte-identical results. V2 format/hash identities are distinct from v1; semantic
accounting goldens and historical source evidence remain unchanged.

The primary capacity workload retains all 1,004 records, all three scenarios,
the original chronology and 49-candidate grid under 268,435,456 bytes. Verify the
complete manifest before publication and report every stream's bytes. Repeat the
applicable 6/100/1000-level replay workloads with setup separated from timing,
three warmups, actual RSS and exact host/runtime/source/config/input identities.
The Task 3 synthetic screens remain decision p99 <=100 ms and reward p99 <=20 ms
at 49 candidates/1000 levels, complete replay >=100 raw records/s, RSS <2 GiB and
comparable post-warmup growth <=64 MiB. A measured throughput failure remains a
failure even if output capacity improves. Incomplete, altered or shortened runs
cannot pass a complete replay screen.

Synthetic engineering results do not supply representative observed arrival
rates, 2x production headroom, collector qualification, venue-rule authority,
execution calibration or empirical economic viability. Those gates remain
independent and unavailable evidence cannot be promoted to PASS. The dedicated
engineering report records Task 4's actual measurements and remaining blockers.

## Task 5 — Measured computation reuse

Profile complete existing workloads before optimization. Record inclusive and
self CPU costs, call counts, pipeline phases, memory and actual source identities;
diagnostic instrumentation does not qualify acceptance timings. Computation reuse
must have exact immutable keys, bounded retention, defined eviction and stale or
malformed-input regressions. See [ADR-0005](ADR/0005-evidence-based-computation-reuse.md).

Require exact before/after stream bytes and reconstructed logical objects across
all three scenarios, with only explicitly identified source-provenance metadata
and its derived terminal/report hashes excluded. Retain historical v1/v2 readers,
every baseline test identity and all engineering checks. Separately stress changing
full evaluation grids, multiple markets, eviction/reappearance and actual output
exhaustion; exhaustion must never publish successful completion.

The unchanged formal 6/100/1000-level matrix retains all inputs, candidates,
scenarios, three warmups, the timed fourth replay and required verification. The
100 records/s, latency, memory, 300-second child deadline and artifact bounds above
continue to apply. A faster incomplete or failed workload cannot pass. Synthetic
qualification and representative production qualification remain separate.

## Task 6 — External evidence and repeated internal throughput target

Retain every original Gate C criterion. Source retrieval and reference-schema
compatibility do not qualify a market when effective rules remain unresolved.
Bounded offline recovery does not replace the failed-run corpus or authorize a
new collection. A requested future collector run remains AWAITING_AUTHORIZATION
and must preserve the original remediation prerequisites and acceptance standard.

The offline [readiness layer](CALIBRATION_READINESS.md) validates declared
provenance, market/protocol/parent scope, known/effective references, terminal
exposure and chronological support. Observed, L2-inferred and synthetic fills
remain distinct. Its READY_FOR_INDEPENDENT_REVIEW status is not Gate D acceptance;
missing real evidence means INSUFFICIENT_EVIDENCE and unavailable empirical periods.

Separately freeze the new internal target: at least three independent valid
complete formal attempts at one source revision; all 6/100/1000-level timed
replays in all attempts must reach 120 raw records/s (1,004/120 seconds maximum).
Retain every valid observation and report minimum/median/maximum and host load.
Preserve the original 100 records/s screen, 14-case matrix, 9/25/49 grids, three
scenarios, 1,004 raw/2,000 canonical records, three warmups and timed fourth replay,
300-second child/900-second matrix deadlines, 512 MiB retained matrix output,
2 GiB RSS, 64 MiB comparable growth, latency screens and 256 MiB per-run bound.
Every required phase stays timed. Diagnostic profiles/pilots remain separate.

Historical collector Gate 1 remains FAIL. Venue qualification remains BLOCKED;
execution calibration and empirical economic viability remain
INSUFFICIENT_EVIDENCE. Synthetic performance cannot qualify representative
production throughput or authorize live execution.
