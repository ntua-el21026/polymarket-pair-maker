# polymarket-pair-maker

Research-grade Polymarket liquidity-reward market making with adaptive YES/NO quoting, conservative execution modelling, inventory risk control, and deterministic replay.

The objective is to develop and validate selective liquidity provision on the
international Polymarket CLOB. Decisions must maximize realistic risk-adjusted net
profitability after adverse selection, execution costs, reward uncertainty, and
capital exposure. `NO_QUOTE` is a valid and often optimal decision.

The code implements a **read-only market-data foundation and deterministic paper
system**: bounded adaptive YES/NO quote decisions, exact economics, causal
calibration, simulated queues and orders, collateral reservations and inventory
accounting. Controlled fixtures exercise these components. Real-market
profitability and collector acceptance remain unvalidated. Missing reference or
calibration evidence produces `NO_QUOTE`. Live order placement is structurally
unavailable.

## Read first

| Document | Authority |
| --- | --- |
| [Project specification](docs/PROJECT_SPEC.md) | Scope, operational modes, deliverables, and implementation status |
| [Architecture](docs/ARCHITECTURE.md) | Module ownership, data flow, compatibility, and runtime policy |
| [Invariants](docs/INVARIANTS.md) | Required correctness and safety contracts |
| [Strategy specification](docs/STRATEGY_SPEC.md) | Implemented strategy and empirical prerequisites |
| [Research basis](docs/RESEARCH_BASIS.md) | Sources, findings, and implementation consequences |
| [Validation gates](docs/VALIDATION_GATES.md) | Migration, implementation, and independent acceptance |
| [Roadmap](docs/ROADMAP.md) | The agreed three-task sequence |
| [Paper decisions](docs/ADR/0002-paper-economics-and-simulation.md) | Economic uncertainty and simulation contracts |
| [SDK decision](docs/ADR/0003-defer-official-sdk.md) | Retained public transport and deferred SDK |
| [Historical evidence](docs/historical/README.md) | Preserved failed-soak evidence and access limitations |

## Development

Use ordinary-GIL CPython **3.14.7** and **uv 0.12.5**, as declared in
`.python-version` and `pyproject.toml`. Direct dependencies and transitive
dependencies are pinned. Run commands from the repository root.

```bash
git clone git@github.com:ntua-el21026/polymarket-pair-maker.git
cd polymarket-pair-maker
uv python install
uv lock --check
uv sync --frozen --all-groups
uv run --frozen --all-groups ppm --help
```

The public-data CLI supports bounded observation and calibration, raw-journal
replay, analytical reconstruction, and integrity validation:

```bash
uv run --frozen --all-groups ppm observe polymarket --duration 60 --gamma-market-id <id> --config config/phase1.toml
uv run --frozen --all-groups ppm replay data/raw --run-id <run-id> --config config/phase1.toml
uv run --frozen --all-groups ppm rebuild-analytical data/raw --output data/rebuilt/<run-id> --run-id <run-id> --config config/phase1.toml
uv run --frozen --all-groups ppm validate-data data
```

Observation and calibration contact external public services. Replay and local
checks do not require venue credentials. Store runs under ignored `data/`,
preserve raw manifests, and rebuild only into a fresh directory disjoint from the
raw journal. `replay --expect-terminal-hash <hash>` compares against a separately
recorded terminal hash for the same processor contract and configuration.

```bash
uv run --frozen --all-groups ruff check .
uv run --frozen --all-groups ruff format --check .
uv run --frozen --all-groups pyright
uv run --frozen --all-groups pytest -q
uv run --frozen --all-groups pre-commit run --all-files
uv build
```

Dependency-audit instructions and the distinction between local checks and
empirical acceptance are in [Validation gates](docs/VALIDATION_GATES.md).
GitHub Actions remains owner-paused; local commands provide reviewable evidence.

## Paper execution

Run the committed **SYNTHETIC** fixture in a fresh output directory:

```bash
mkdir -p data/paper
uv run --frozen --all-groups ppm paper demo --paper-config config/paper.toml --output data/paper/demo-a
uv run --frozen --all-groups ppm paper demo --paper-config config/paper.toml --output data/paper/demo-b
diff -r data/paper/demo-a data/paper/demo-b
```

Replay a complete, integrity-verified raw run without network access:

```bash
uv run --frozen --all-groups ppm paper replay data/raw --run-id <run-id> --config config/phase1.toml --paper-config config/paper.toml --output data/paper/<fresh-session>
```

`config/paper.toml` declares exact decimal assumptions, bounds and engineering
risk defaults: 1,000 simulated payout units, 25 committed per market, 100 total,
one active market and two passive bids. Its optional `evidence_file` points to a
strict offline evidence document relative to the configuration file. The default
has no empirical calibration or independently qualified economic references;
real journal replay therefore abstains. The official size-cutoff midpoint
reconstruction and fee denomination/rounding need additional authoritative
evidence. The demo's explicitly synthetic conventions cannot qualify real data.

Three queue scenarios run as independent portfolios. Outputs distinguish
observations, simulated execution, estimated incentives and unavailable verified
payments. The positive demo case is a positive **SYNTHETIC quote decision**, not
realized profit. Its stress ending leaves two YES tokens unresolved and 999.2
simulated collateral in the conservative/sensitivity portfolios; the pessimistic
portfolio does not assume a fill with unknown priority. Missing exit depth is
preserved, rather than assigning a favorable terminal sale or payout.
Session manifests retain input/configuration/rule/calibration identities,
runtime provenance and deterministic result hashes. Raw evidence is never an
output destination; existing output directories are rejected.

```bash
uv run --frozen --all-groups pytest -q tests/unit tests/property tests/integration tests/golden
uv run --frozen --all-groups python -m benchmarks.paper_system --iterations 200 --output data/paper-benchmark.json
```

The benchmark records its runtime, host and bounded workload; its measurements
do not establish venue execution performance. Task 2 execution evidence and
Gate B results are reported in the implementation handoff. Task 3 remains an
independent empirical evaluation.

## Acceptance and deployment status

The first six-hour collector soak returned **`GATE 1: FAIL`**. Raw preservation
and deterministic replay succeeded in that historical review, while book-state
correctness, bounded reporting, and completeness of recovery/availability
evidence required remediation. Relevant regressions remain in the test suite.
The migration does not replace a fresh independently accepted collector run.

The full failed journal is an external input and is absent from a clean clone.
The repository contains byte-preserved failure reports and sanitized regression
fixtures, with their integrity metadata. See the
[historical evidence index](docs/historical/README.md) before reproducing claims.

Live deployment remains blocked pending explicit authorization, independent
acceptance, and authoritative verification of legal and geographic eligibility,
including the unresolved restrictions affecting access from Greece. A venue
geographic-check endpoint alone cannot establish legal eligibility. There is no
paper-to-live switch, signer, wallet configuration, or live-order command.

Maintained by **Michail Athanasios Peppas**. Credentials, collected journals,
checkpoints, and large research artifacts belong outside tracked source.

## Normalized paper output and verification

New paper results use the explicit v2 artifact contract in
[ADR-0004](docs/ADR/0004-normalized-paper-artifacts.md). Complete candidate grids
are stored once and referenced by compact decisions and selections. The existing
256 MiB cumulative bound remains unchanged. Replay verifies the completed
inventory, scoped references, chronology, state chains and terminal result before
atomic publication. Independently verify an existing completed result with:

```bash
uv run --frozen --all-groups python -m polymarket_pair_maker.paper.verification data/paper/<completed-session>
```

`iter_reconstructed_records(path)` in `paper.verification` yields exact typed
`CandidateEvaluation` tuples, `StrategyDecision` and `MarketRanking` objects for
the three normalized streams, and original values for the other streams.
Exhaust the iterator to validate final state chains; `verify_paper_artifacts`
always performs complete validation. Missing/conflicting references, modified
content, malformed or truncated records and incorrect inventories fail explicitly.
Legacy v1 completed results retain their original interpretation and container
hash checks. Their absent cross-stream record indices prevent independent
reconstruction of historical state-chain order; the verifier reports that limit.
Raw evidence and historical artifact files are never migrated in place.
