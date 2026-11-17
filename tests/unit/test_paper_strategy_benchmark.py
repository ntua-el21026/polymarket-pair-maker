from __future__ import annotations

from typing import cast

import pytest

from benchmarks.paper_system import run_benchmark
from polymarket_pair_maker.paper.config import PaperConfig


def test_benchmark_runs_real_strategy_and_exact_partial_fill_workload() -> None:
    result = run_benchmark(PaperConfig(), iterations=2)
    assert result["classification"] == "SYNTHETIC_MICROBENCHMARK_EMPIRICALLY_UNVALIDATED"
    protocol = cast("dict[str, object]", result["protocol"])
    assert protocol["candidate_count"] == 49
    assert protocol["setup_excluded"] is True
    events = cast("dict[str, object]", result["paper_event_processing"])
    assert events["simulated_filled_quantity"] == "0.002"
    assert events["operations"] == 2
    footprint = cast("dict[str, int]", result["bounded_state"])
    assert footprint["paper_events"] == 4
    assert footprint["paper_orders"] == 1
    assert footprint["decision_cache_entries"] <= footprint["decision_cache_capacity"]
    assert footprint["allocated_peak_bytes"] >= footprint["allocated_current_bytes"] > 0


@pytest.mark.parametrize("iterations", [0, 1, 1001, True])
def test_benchmark_rejects_unbounded_or_noninteger_workload(iterations: int) -> None:
    with pytest.raises(ValueError, match="iterations"):
        run_benchmark(PaperConfig(), iterations=iterations)
