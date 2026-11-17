# Roadmap

The agreed sequence consists of three separately authorized tasks.

1. **Repository migration — completed foundation.** Rename the existing repository,
   distribution, package, and CLI; preserve history and useful evidence; remove
   unrelated scope; retain and verify the market-data/replay foundation; and
   establish the authoritative strategy, architecture, and acceptance contracts.
   Delivery evidence is recorded in the migration report. This task implements
   no market-making strategy and enables no trading.
2. **Functional implementation — Task 2.** Implement
   [STRATEGY_SPEC.md](STRATEGY_SPEC.md): deterministic selective quoting, exact
   reward/fee mathematics with explicit unknowns, conservative maker fills, risk
   and inventory accounting, and offline paper execution. Controlled fixtures
   test the implementation. The official SDK is explicitly deferred; existing
   public transport and locked dependencies remain. The handoff records Gate B
   execution evidence while retaining collector and economic-reference gaps.
3. **Independent testing, empirical validation, and final acceptance — third
   task.** After separate authorization, apply Gate C to frozen implementations
   and reproducible data. Produce an independent GO/NO-GO verdict with full-cost
   P&L, uncertainty, adverse scenarios, and measured performance. Resolve the
   inherited collector acceptance requirements before relying on its data.

Task 2 status: **IMPLEMENTED** paper functionality; **TESTED SYNTHETICALLY**
controlled behavior; **EMPIRICALLY UNVALIDATED** profitability and real fill
calibration; **BLOCKED** real quote admission when midpoint/fee/collector evidence
is unresolved; **OUT OF SCOPE** live trading, other venues and automatic deployment.

The first six-hour collector soak remains failed. Its evidence and criteria are
preserved under [historical/](historical/README.md). The migration does not run a
new six-hour soak; Task 2 also does not initiate one. No step automatically authorizes live
execution. Legal/geographic eligibility and a future live-capable task remain
separate unresolved gates.

Tasks 4–5 remediated normalized artifact capacity and the original synthetic
performance screens. Task 6 adds a separately measured 120 records/s target,
current official-source verification, bounded historical-data recovery and an
offline calibration-readiness contract. The original collector corpus and
authoritative rule gaps still block empirical research. A future Task 7 economic
evaluation requires qualified real data, resolved applicable venue rules,
independent execution calibration and frozen confirmation evidence. Synthetic
engineering completion does not satisfy these prerequisites.
