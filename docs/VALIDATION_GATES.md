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

Prompt 2 must implement and test the deterministic decision engine specified in
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

Gate B is unexecuted at migration. This task does not implement its deliverables.

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
