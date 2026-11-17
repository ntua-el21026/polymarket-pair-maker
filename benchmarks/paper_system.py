"""Reproducible SYNTHETIC PAPER microbenchmark, without acceptance claims.

Run from the repository root with the locked CPython environment:
python -m benchmarks.paper_system --iterations 200 --output data/paper-benchmark.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import platform
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics.rewards import (
    RewardOrder,
    RewardScoreCache,
    estimate_reward_snapshot,
)
from polymarket_pair_maker.paper.config import PaperConfig, load_paper_config
from polymarket_pair_maker.paper.demo import FIXTURES, demo_market_context
from polymarket_pair_maker.paper.execution import (
    ObservedPressure,
    PaperSimulator,
    PressureKind,
    QueueScenario,
)
from polymarket_pair_maker.paper.ledger import PaperLedger
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderId, PaperOrderIntent
from polymarket_pair_maker.risk.limits import RiskController
from polymarket_pair_maker.strategy import DecisionEngine, MarketContext

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "ppm-paper-microbenchmark-v1"
WORKLOAD_VERSION = "ppm-synthetic-positive-candidates-partial-fills-v1"


def _latencies(operation: Callable[[], object], iterations: int) -> dict[str, int]:
    samples: list[int] = []
    for _ in range(iterations):
        before = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - before)
    ordered = sorted(samples)
    return {
        "operations": iterations,
        "minimum_ns": ordered[0],
        "median_ns": ordered[len(ordered) // 2],
        "p95_ns": ordered[(95 * len(ordered) + 99) // 100 - 1],
        "maximum_ns": ordered[-1],
        "mean_ns_integer_floor": sum(samples) // len(samples),
        "total_operation_ns": sum(samples),
    }


def _simulator(context: MarketContext, config: PaperConfig, iterations: int) -> PaperSimulator:
    ledger = PaperLedger(config.starting_capital, context.bundle.payout_asset_id)
    execution = replace(
        config.execution,
        submission_latency_ns=0,
        cancellation_latency_ns=0,
        scenario=QueueScenario.CONSERVATIVE,
        unknown_queue_buffer=Quantity.parse(0),
        maximum_orders=1,
        maximum_events=iterations + 4,
    )
    simulator = PaperSimulator(ledger, RiskController(config.risk), execution)
    bid, ask = context.yes_book.best_bid, context.yes_book.best_ask
    tick = context.bundle.precision.tick_size
    if bid is None or ask is None or tick is None:
        raise ValueError("benchmark requires a validated two-sided book and tick")
    price = Price(bid.price.value + tick)
    if price.value >= ask.price.value:
        raise ValueError("benchmark requires a one-tick inside-spread passive price")
    intent = PaperOrderIntent(
        PaperOrderId("synthetic-benchmark-order"),
        context.bundle.market.market_id,
        context.yes_book.instrument_id,
        context.bundle.market.gamma_event_id or "synthetic-benchmark-event",
        Outcome.YES,
        price,
        Quantity.parse(10),
        context.as_of,
        UtcTimestamp(context.as_of.nanoseconds_since_epoch + config.risk.maximum_quote_age_ns),
    )
    books = {context.yes_book.instrument_id: context.yes_book}
    simulator.submit((intent,), books, context.as_of)
    simulator.advance(context.as_of, books)
    return simulator


def _pressures(
    context: MarketContext, simulator: PaperSimulator, iterations: int
) -> tuple[ObservedPressure, ...]:
    price = simulator.active_orders()[0].intent.price
    return tuple(
        ObservedPressure(
            f"synthetic-microfill-{index}",
            context.yes_book.instrument_id,
            price,
            Quantity.parse("0.001"),
            UtcTimestamp(context.as_of.nanoseconds_since_epoch + index + 1),
            UtcTimestamp(context.as_of.nanoseconds_since_epoch + index + 1),
            PressureKind.SYNTHETIC_EXECUTION,
            canonical_sha256((WORKLOAD_VERSION, index)),
        )
        for index in range(iterations)
    )


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - repository established Git executable
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _cpu_model() -> str:
    path = Path("/proc/cpuinfo")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    return platform.processor() or "unavailable"


def run_benchmark(config: PaperConfig, *, iterations: int = 200) -> dict[str, object]:
    if type(iterations) is not int or not 2 <= iterations <= 1000:
        raise ValueError("benchmark iterations must lie in [2, 1000]")
    # All fixture reads, journal verification and setup occur outside timings.
    context = demo_market_context(config)
    engine = DecisionEngine(config.strategy)
    decision = engine.evaluate(context)
    if decision.candidate is None or context.reward_rules is None:
        raise ValueError("benchmark requires the validated positive SYNTHETIC fixture")
    own_orders = tuple(
        RewardOrder(
            f"benchmark-{leg.outcome}", leg.outcome, "BUY", leg.price, leg.quantity, context.as_of
        )
        for leg in decision.candidate.legs
    )
    rules = context.reward_rules
    reward_cache = RewardScoreCache(capacity=8)

    def reward_uncached() -> object:
        return estimate_reward_snapshot(
            context.yes_book, context.no_book, own_orders, rules, context.as_of
        )

    def reward_cached() -> object:
        return reward_cache.estimate(
            context.yes_book, context.no_book, own_orders, rules, context.as_of
        )

    for _ in range(3):
        engine.evaluate(context)
        reward_uncached()
        reward_cached()
    cold = _latencies(lambda: DecisionEngine(config.strategy).evaluate(context), iterations)
    warm = _latencies(lambda: engine.evaluate(context), iterations)
    reward_cold = _latencies(reward_uncached, iterations)
    reward_warm = _latencies(reward_cached, iterations)
    simulator = _simulator(context, config, iterations)
    pressures = _pressures(context, simulator, iterations)
    iterator = iter(pressures)
    execution = _latencies(lambda: simulator.process_pressure(next(iterator)), iterations)
    snapshot = simulator.ledger.snapshot()
    expected_quantity = ExactDecimal(iterations, 3)
    if (
        sum((position.quantity.value for position in snapshot.positions), ExactDecimal.zero())
        != expected_quantity
    ):
        raise ValueError("benchmark did not execute the declared partial-fill workload")
    if simulator.event_count != iterations + 2:
        raise ValueError("benchmark event inventory does not reconcile")
    # Separate memory pass: excludes already loaded immutable input and pressures.
    tracemalloc.start()
    memory_engine = DecisionEngine(config.strategy)
    memory_simulator = _simulator(context, config, iterations)
    for pressure in pressures:
        memory_engine.evaluate(context)
        memory_simulator.process_pressure(pressure)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    source_paths = sorted((REPOSITORY / "src/polymarket_pair_maker").rglob("*.py"))
    source_paths.append(Path(__file__))
    return {
        "schema_version": SCHEMA_VERSION,
        "workload_version": WORKLOAD_VERSION,
        "classification": "SYNTHETIC_MICROBENCHMARK_EMPIRICALLY_UNVALIDATED",
        "git_commit": _git_commit(),
        "source_hashes": {
            str(path.relative_to(REPOSITORY)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths
        },
        "fixture_hashes": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(FIXTURES.glob("*.json"))
        },
        "config_hash": config.config_hash,
        "seed": config.execution.seed,
        "hardware": {
            "cpu_model": _cpu_model(),
            "logical_cpus": os.cpu_count(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "gil_enabled": sys._is_gil_enabled(),  # pyright: ignore[reportPrivateUsage]
            "executable": sys.executable,
        },
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("httpx", "websockets", "pydantic", "duckdb", "pyarrow", "zstandard")
        },
        "protocol": {
            "iterations": iterations,
            "warmups": 3,
            "timer": "perf_counter_ns",
            "candidate_count": len(decision.evaluations),
            "book_levels": sum(
                len(book.bids) + len(book.asks) for book in (context.yes_book, context.no_book)
            ),
            "execution_scenario": "CONSERVATIVE",
            "execution_latency_ns": 0,
            "queue_ahead": "0",
            "partial_fill_quantity": "0.001",
            "setup_excluded": True,
            "memory_pass_separate_from_timings": True,
            "performance_acceptance": "NOT_ASSESSED",
        },
        "decision_uncached": cold,
        "decision_cached": warm,
        "reward_uncached": reward_cold,
        "reward_cached": reward_warm,
        "paper_event_processing": {
            **execution,
            "events_per_second": str(
                ExactDecimal(iterations * 1_000_000_000).divide(
                    ExactDecimal(execution["total_operation_ns"]),
                    scale=6,
                    rounding=RoundingMode.FLOOR,
                )
            ),
            "simulated_filled_quantity": str(expected_quantity),
            "terminal_ledger_hash": canonical_sha256(snapshot),
        },
        "bounded_state": {
            "allocated_current_bytes": current_bytes,
            "allocated_peak_bytes": peak_bytes,
            "decision_cache_entries": memory_engine.cached_evaluations,
            "decision_cache_capacity": config.strategy.cache_capacity,
            "paper_orders": len(memory_simulator.orders()),
            "paper_order_capacity": memory_simulator.config.maximum_orders,
            "paper_events": memory_simulator.event_count,
            "paper_event_capacity": memory_simulator.config.maximum_events,
            "pressure_records": len(memory_simulator.pressure_results()),
        },
        "limitations": [
            "Small synthetic books and simulated partial fills; no real queue placement.",
            "No counterfactual market impact or venue profitability validation.",
            "Cached measurements include validity gates and canonical decision provenance.",
            "Allocation footprint excludes immutable loaded fixture and prebuilt pressure inputs.",
            "No inference about high-frequency capability or Task 3 performance acceptance.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-config", type=Path, default=Path("config/paper.toml"))
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path = args.output
    if output.exists() or output.is_symlink():
        parser.error("benchmark output must be a fresh file")
    if output.resolve().is_relative_to(FIXTURES.resolve()):
        parser.error("benchmark output cannot overlap committed fixture inputs")
    result = run_benchmark(load_paper_config(args.paper_config), iterations=args.iterations)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(canonical_json_bytes(result) + b"\n")
    print(f"Wrote SYNTHETIC benchmark: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
