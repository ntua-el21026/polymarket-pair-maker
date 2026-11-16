# Roadmap

The agreed sequence consists of three separately authorized tasks.

1. **Repository migration — this task.** Rename the existing repository,
   distribution, package, and CLI; preserve history and useful evidence; remove
   unrelated scope; retain and verify the market-data/replay foundation; and
   establish the authoritative strategy, architecture, and acceptance contracts.
   Delivery evidence is recorded in the migration report. This task implements
   no market-making strategy and enables no trading.
2. **Functional implementation — next task.** After user authorization, implement
   [STRATEGY_SPEC.md](STRATEGY_SPEC.md): deterministic selective quoting, validated
   reward/fee economics, conservative maker fills, risk and inventory accounting,
   and paper execution. Resolve SDK/dependency compatibility through a locked,
   tested integration. Meet Gate B while retaining collector qualification gaps.
3. **Independent testing, empirical validation, and final acceptance — third
   task.** After separate authorization, apply Gate C to frozen implementations
   and reproducible data. Produce an independent GO/NO-GO verdict with full-cost
   P&L, uncertainty, adverse scenarios, and measured performance. Resolve the
   inherited collector acceptance requirements before relying on its data.

The first six-hour collector soak remains failed. Its evidence and criteria are
preserved under [historical/](historical/README.md). The migration does not run a
new six-hour soak or begin Prompt 2. No step automatically authorizes live
execution. Legal/geographic eligibility and a future live-capable task remain
separate unresolved gates.
