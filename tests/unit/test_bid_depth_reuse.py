"""Exact bid totals reuse only actual immutable inputs and retain fresh admission."""

import builtins
from collections.abc import Iterable
from dataclasses import replace
from typing import Any, cast
from unittest.mock import patch

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.ids import InstrumentId, MarketId
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.market_data import (
    BinaryBookEligibilityConfig,
    BinaryBookEligibilityEvaluator,
)
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_market_context
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state.order_books import BookLevel
from polymarket_pair_maker.strategy import engine as module
from polymarket_pair_maker.strategy.engine import DecisionEngine, MarketContext


def naive_total(bids: tuple[BookLevel, ...]) -> ExactDecimal:
    """The original transparent ordered financial sum, independent of cache code."""
    total = ExactDecimal.zero()
    for level in bids:
        total = total + level.quantity.value
    return total


def reassessed(context: MarketContext) -> MarketContext:
    assessment = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
    ).assess(
        bundle=context.bundle,
        proof=prove_binary_complement(context.bundle),
        books=(context.yes_book, context.no_book),
        observed_at=context.as_of,
        provenance=context.assessment.provenance,
    )
    return replace(context, assessment=assessment)


def deep_context() -> MarketContext:
    base = demo_market_context(PaperConfig())
    bids = tuple(
        BookLevel(Price(ExactDecimal(40 - index, 2)), Quantity(ExactDecimal(index + 10)))
        for index in range(37)
    )
    yes = replace(base.yes_book, bids=bids, best_bid=bids[0])
    no_bids = tuple(
        replace(level, quantity=Quantity(level.quantity.value + ExactDecimal.one()))
        for level in bids
    )
    no = replace(base.no_book, bids=no_bids, best_bid=no_bids[0])
    return reassessed(replace(base, yes_book=yes, no_book=no))


def reference_decision(
    context: MarketContext, config: module.StrategyConfig
) -> module.StrategyDecision:
    oracle = DecisionEngine(config)
    # Replacing only this pure operation leaves every candidate and gate intact.
    with patch.object(oracle, "_bid_total", side_effect=naive_total):
        return oracle.evaluate(context)


def test_repeated_complete_decisions_do_not_repeat_full_bid_sums(monkeypatch: pytest.MonkeyPatch):
    context = deep_context()
    calls: list[tuple[object, ...]] = []

    def observed(values: Iterable[Any], start: Any = 0) -> Any:
        entries = tuple(values)
        if len(entries) == 37 and all(type(value) is ExactDecimal for value in entries):
            calls.append(entries)
        return builtins.sum(entries, start)

    monkeypatch.setattr(module, "sum", observed, raising=False)
    engine = DecisionEngine(PaperConfig().strategy)
    for _ in range(4):
        assert engine.evaluate(context).evaluations
    assert len(calls) == 2


def test_totals_equal_independent_ordered_sum_and_two_cells_evict():
    engine = DecisionEngine()
    contexts = deep_context()
    first, second = contexts.yes_book.bids, contexts.no_book.bids
    third = (BookLevel(Price.parse("0.1"), Quantity.parse("3.14159")),)
    for bids in (first, second, first, third, second, first):
        assert engine._bid_total(bids) == naive_total(bids)  # pyright: ignore[reportPrivateUsage]
        assert len(engine._bid_totals) <= 2  # pyright: ignore[reportPrivateUsage]
        assert sum(len(entry[0]) for entry in engine._bid_totals.values()) <= 8192  # pyright: ignore[reportPrivateUsage]
    assert set(engine._bid_totals) == {id(first), id(second)}  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "change",
    [
        "quantity",
        "new_tuple",
        "future",
        "stale",
        "fee",
        "reward",
        "calibration",
        "capacity",
        "market",
        "timestamp",
        "minimum_depth",
    ],
)
def test_complete_decisions_equal_naive_after_input_or_admission_change(change: str):
    paper = PaperConfig()
    context = deep_context()
    engine = DecisionEngine(paper.strategy)
    assert canonical_json_bytes(engine.evaluate(context)) == canonical_json_bytes(
        reference_decision(context, paper.strategy)
    )
    config = paper.strategy
    if change == "quantity":
        levels = list(context.yes_book.bids)
        levels[-1] = replace(levels[-1], quantity=Quantity.parse("0.003"))
        book = replace(context.yes_book, bids=tuple(levels))
        changed = reassessed(replace(context, yes_book=book))
    elif change == "new_tuple":
        changed = reassessed(
            replace(
                context,
                yes_book=replace(
                    context.yes_book, bids=tuple(level for level in context.yes_book.bids)
                ),
            )
        )
    elif change == "future":
        changed = replace(context, as_of=UtcTimestamp(context.as_of.nanoseconds_since_epoch - 1))
    elif change == "stale":
        changed = reassessed(
            replace(
                context,
                as_of=UtcTimestamp(
                    context.as_of.nanoseconds_since_epoch + config.maximum_book_age_ns + 1
                ),
            )
        )
    elif change == "fee":
        changed = replace(context, fee_rules=None)
    elif change == "reward":
        changed = replace(context, reward_rules=None)
    elif change == "calibration":
        changed = replace(context, calibration=None)
    elif change == "capacity":
        changed = replace(context, risk_capacity=Money.parse("0"))
    elif change == "market":
        changed = replace(context, yes_book=replace(context.yes_book, market_id=MarketId("other")))
    elif change == "timestamp":
        changed = reassessed(
            replace(
                context,
                yes_book=replace(
                    context.yes_book,
                    last_valid_update_time=UtcTimestamp(context.as_of.nanoseconds_since_epoch - 1),
                ),
            )
        )
    else:
        changed = context
        config = replace(config, minimum_exit_depth=Quantity.parse("100000"))
        engine.config = config
    for candidate in (changed, context, changed):
        actual, expected = engine.evaluate(candidate), reference_decision(candidate, config)
        assert actual == expected
        assert canonical_json_bytes(actual) == canonical_json_bytes(expected)


def test_mutable_bid_list_is_never_retained_and_mutation_recomputes():
    engine = DecisionEngine()
    known = deep_context().yes_book.bids
    engine._bid_total(known)  # pyright: ignore[reportPrivateUsage]
    values = list(known)
    bids = cast(tuple[BookLevel, ...], cast(object, values))
    assert engine._bid_total(bids) == naive_total(bids)  # pyright: ignore[reportPrivateUsage]
    assert not engine._bid_totals  # pyright: ignore[reportPrivateUsage]
    values[-1] = replace(values[-1], quantity=Quantity.parse("987.001"))
    assert engine._bid_total(bids) == naive_total(bids)  # pyright: ignore[reportPrivateUsage]
    assert not engine._bid_totals  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "kind", ["level", "price", "quantity", "decimal", "oversized_coefficient", "oversized_scale"]
)
def test_unknown_or_oversized_inputs_bypass_and_clear_success(kind: str):
    class OtherLevel(BookLevel):
        pass

    class OtherPrice(Price):
        pass

    class OtherQuantity(Quantity):
        pass

    class OtherDecimal(ExactDecimal):
        pass

    known = BookLevel(Price.parse("0.2"), Quantity.parse("1.25"))
    if kind == "level":
        level = OtherLevel(known.price, known.quantity)
    elif kind == "price":
        level = BookLevel(OtherPrice(known.price.value), known.quantity)
    elif kind == "quantity":
        level = BookLevel(known.price, OtherQuantity(known.quantity.value))
    elif kind == "decimal":
        level = BookLevel(known.price, Quantity(OtherDecimal(125, 2)))
    elif kind == "oversized_coefficient":
        level = BookLevel(known.price, Quantity(ExactDecimal(2**513)))
    else:
        level = BookLevel(known.price, Quantity(ExactDecimal(1, 129)))
    engine = DecisionEngine()
    engine._bid_total((known,))  # pyright: ignore[reportPrivateUsage]
    values = (level,)
    assert engine._bid_total(values) == naive_total(values)  # pyright: ignore[reportPrivateUsage]
    assert not engine._bid_totals  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("count", [4096, 4097])
def test_bid_level_bound_is_bypass_not_new_admission_limit(count: int):
    level = BookLevel(Price.parse("0.01"), Quantity.parse("0.001"))
    bids = (level,) * count
    engine = DecisionEngine()
    assert engine._bid_total(bids) == naive_total(bids)  # pyright: ignore[reportPrivateUsage]
    assert len(engine._bid_totals) == int(count == 4096)  # pyright: ignore[reportPrivateUsage]


def test_engines_are_independent_and_exact_numeric_sides_can_cross_market_identity():
    context = deep_context()
    first, second = DecisionEngine(), DecisionEngine()
    assert first._bid_total(context.yes_book.bids) == naive_total(context.yes_book.bids)  # pyright: ignore[reportPrivateUsage]
    assert not second._bid_totals  # pyright: ignore[reportPrivateUsage]
    other = replace(
        context.yes_book, market_id=MarketId("other"), instrument_id=InstrumentId("other")
    )
    assert first._bid_total(other.bids) == naive_total(other.bids)  # pyright: ignore[reportPrivateUsage]
    assert len(first._bid_totals) == 1  # pyright: ignore[reportPrivateUsage]


def test_gate_keeps_original_lazy_order_when_hashing_fails(monkeypatch: pytest.MonkeyPatch):
    context = deep_context()
    engine = DecisionEngine()
    calls: list[str] = []

    def invalid_hash(_value: object) -> str:
        calls.append("input hash")
        raise ValueError("original input failure")

    def forbidden_total(_bids: tuple[BookLevel, ...]) -> ExactDecimal:
        pytest.fail("bid total ran before original input hash")

    monkeypatch.setattr(module, "canonical_sha256", invalid_hash)
    with (
        patch.object(engine, "_bid_total", side_effect=forbidden_total),
        pytest.raises(ValueError, match="original input failure"),
    ):
        engine.evaluate(context)
    assert calls == ["input hash"]


def test_failed_unknown_input_clears_cell_and_preserves_original_exception():
    engine = DecisionEngine()
    engine._bid_total(deep_context().yes_book.bids)  # pyright: ignore[reportPrivateUsage]
    bad = cast(tuple[BookLevel, ...], cast(object, (object(),)))
    with pytest.raises(AttributeError) as expected:
        naive_total(bad)
    with pytest.raises(type(expected.value), match=str(expected.value)):
        engine._bid_total(bad)  # pyright: ignore[reportPrivateUsage]
    assert not engine._bid_totals  # pyright: ignore[reportPrivateUsage]


def test_identity_cell_requires_retained_tuple_identity_even_if_key_collides():
    engine = DecisionEngine()
    context = deep_context()
    first, second = context.yes_book.bids, context.no_book.bids
    engine._bid_totals[id(first)] = (second, naive_total(second))  # pyright: ignore[reportPrivateUsage]
    assert engine._bid_total(first) == naive_total(first)  # pyright: ignore[reportPrivateUsage]
    assert engine._bid_totals[id(first)][0] is first  # pyright: ignore[reportPrivateUsage]


def test_high_variability_replacements_and_reappearance_match_every_naive_decision():
    context = deep_context()
    config = PaperConfig().strategy
    engine = DecisionEngine(config)
    contexts: list[MarketContext] = []
    for index in range(24):
        bids = tuple(
            replace(
                level, quantity=Quantity(level.quantity.value + ExactDecimal(index + offset, 3))
            )
            for offset, level in enumerate(context.yes_book.bids)
        )
        changed = reassessed(
            replace(context, yes_book=replace(context.yes_book, bids=bids, best_bid=bids[0]))
        )
        contexts.append(changed)
    for candidate in (*contexts, context, contexts[0], contexts[-1], context):
        actual = engine.evaluate(candidate)
        expected = reference_decision(candidate, config)
        assert canonical_json_bytes(actual) == canonical_json_bytes(expected)
        assert len(engine._bid_totals) <= 2  # pyright: ignore[reportPrivateUsage]
