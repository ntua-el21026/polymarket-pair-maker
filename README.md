# polymarket-pair-maker

Research-grade Polymarket liquidity-reward market making with adaptive YES/NO quoting, conservative execution modelling, inventory risk control, and deterministic replay.

The objective is to develop and validate selective liquidity provision on the
international Polymarket CLOB. Decisions must maximize realistic risk-adjusted net
profitability after adverse selection, execution costs, reward uncertainty, and
capital exposure. `NO_QUOTE` is a valid and often optimal decision.

The current code provides a **read-only market-data and replay foundation**.
The market-making decision engine, maker-fill simulator, inventory accounting,
reward estimator, and paper-order lifecycle are specified for the next task and
are not implemented. Live order placement is structurally unavailable.

## Read first

| Document | Authority |
| --- | --- |
| [Project specification](docs/PROJECT_SPEC.md) | Scope, operational modes, deliverables, and implementation status |
| [Architecture](docs/ARCHITECTURE.md) | Module ownership, data flow, compatibility, and runtime policy |
| [Invariants](docs/INVARIANTS.md) | Required correctness and safety contracts |
| [Strategy specification](docs/STRATEGY_SPEC.md) | Normative contract for Prompt 2 |
| [Research basis](docs/RESEARCH_BASIS.md) | Sources, findings, and implementation consequences |
| [Validation gates](docs/VALIDATION_GATES.md) | Migration, implementation, and independent acceptance |
| [Roadmap](docs/ROADMAP.md) | The agreed three-task sequence |
| [Accepted decisions](docs/ADR/0001-foundation-and-safety-boundaries.md) | Foundation and compatibility decisions |
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
