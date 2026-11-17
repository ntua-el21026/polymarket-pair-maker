"""Pure computation reuse must preserve the complete fresh-engine decision."""

import hashlib
import json
from dataclasses import replace
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.ids import InstrumentId, MarketId
from polymarket_pair_maker.core.serialization import canonical_sha256, canonicalize
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.market_data import (
    BinaryBookEligibilityConfig,
    BinaryBookEligibilityEvaluator,
    EligibilityDisposition,
)
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_market_context
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state.order_books import BookLevel
from polymarket_pair_maker.strategy import engine as module
from polymarket_pair_maker.strategy.engine import Candidate, DecisionEngine, MarketContext


def moved(context: MarketContext, index: int) -> MarketContext:
    bid = replace(context.yes_book.bids[0], price=Price(ExactDecimal(40 + index, 2)))
    book = replace(
        context.yes_book,
        bids=(bid, *context.yes_book.bids[1:]),
        best_bid=bid,
        generation=context.yes_book.generation + index + 1,
    )
    assessment = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
    ).assess(
        bundle=context.bundle,
        proof=prove_binary_complement(context.bundle),
        books=(book, context.no_book),
        observed_at=context.as_of,
        provenance=context.assessment.provenance,
    )
    return replace(context, yes_book=book, assessment=assessment)


def test_identical_candidate_grid_is_constructed_once(monkeypatch: pytest.MonkeyPatch):
    paper = PaperConfig()
    context = demo_market_context(paper)
    expected = DecisionEngine(paper.strategy).evaluate(context)
    original = module.generate_candidates
    calls: list[MarketContext] = []

    def observed(ctx: MarketContext, config: module.StrategyConfig):
        calls.append(ctx)
        return original(ctx, config)

    monkeypatch.setattr(module, "generate_candidates", observed)
    engine = DecisionEngine(paper.strategy)
    for _ in range(5):
        actual = engine.evaluate(context)
        assert actual == expected
        assert canonical_sha256(actual) == canonical_sha256(expected)
    assert len(calls) == 1


def test_evaluation_lookup_does_not_rehash_complete_candidate(monkeypatch: pytest.MonkeyPatch):
    paper = PaperConfig()
    context = demo_market_context(paper)
    expected = DecisionEngine(paper.strategy).evaluate(context)
    original = module.canonical_sha256
    calls: list[object] = []

    def observed(value: object) -> str:
        if isinstance(value, tuple):
            pair = cast(tuple[object, ...], value)
            if len(pair) == 2 and isinstance(pair[0], Candidate):
                calls.append(pair)
        return original(cast(object, value))

    monkeypatch.setattr(module, "canonical_sha256", observed)
    engine = DecisionEngine(paper.strategy)
    assert engine.evaluate(context) == expected
    assert engine.evaluate(context) == expected
    assert calls == []


def test_grid_eviction_and_reappearance_equal_fresh_computation():
    paper = PaperConfig()
    config = replace(paper.strategy, maximum_markets=2)
    engine = DecisionEngine(config)
    base = demo_market_context(paper)
    contexts = [moved(base, i) for i in range(5)]
    for context in [*contexts, contexts[0], contexts[0]]:
        actual = engine.evaluate(context)
        expected = DecisionEngine(config).evaluate(context)
        assert actual == expected
        assert canonical_sha256(actual) == canonical_sha256(expected)
        assert engine.cached_candidate_grids <= config.maximum_markets
        assert engine.cached_evaluations <= config.cache_capacity
    assert engine.candidate_cache_hits >= 1
    assert engine.candidate_cache_misses >= 6


@pytest.mark.parametrize(
    "failure", ["future", "missing_reward", "missing_fee", "missing_calibration", "no_capacity"]
)
def test_primed_computation_cache_cannot_bypass_current_admission(
    failure: str, monkeypatch: pytest.MonkeyPatch
):
    paper = PaperConfig()
    context = demo_market_context(paper)
    engine = DecisionEngine(paper.strategy)
    engine.evaluate(context)
    if failure == "future":
        invalid = replace(context, as_of=UtcTimestamp(context.as_of.nanoseconds_since_epoch - 1))
    elif failure == "missing_reward":
        invalid = replace(context, reward_rules=None)
    elif failure == "missing_fee":
        invalid = replace(context, fee_rules=None)
    elif failure == "missing_calibration":
        invalid = replace(context, calibration=None)
    else:
        invalid = replace(context, risk_capacity=Money.parse("0"))
    expected = DecisionEngine(paper.strategy).evaluate(invalid)

    def forbidden(*_args: object):
        pytest.fail("invalid evidence reached candidate generation")

    monkeypatch.setattr(module, "generate_candidates", forbidden)
    assert engine.evaluate(invalid) == expected
    assert expected.action is module.StrategyAction.NO_QUOTE
    assert expected.reasons


def test_context_economic_changes_are_isolated_across_engines():
    paper = PaperConfig()
    context = demo_market_context(paper)
    first, second = DecisionEngine(paper.strategy), DecisionEngine(paper.strategy)
    first.evaluate(context)
    assert second.cached_candidate_grids == 0
    changed = replace(context, risk_capacity=Money.parse("1"))
    for candidate_context in (changed, context, changed):
        assert first.evaluate(candidate_context) == DecisionEngine(paper.strategy).evaluate(
            candidate_context
        )
        assert second.evaluate(candidate_context) == DecisionEngine(paper.strategy).evaluate(
            candidate_context
        )


def test_unchanged_complete_evaluation_inputs_are_hashed_once(monkeypatch: pytest.MonkeyPatch):
    paper = PaperConfig()
    context = demo_market_context(paper)
    expected = DecisionEngine(paper.strategy).evaluate(context)
    original = module.canonical_sha256
    calls: list[object] = []

    def observed(value: object) -> str:
        if isinstance(value, tuple):
            values = cast(tuple[object, ...], value)
            if len(values) == 5 and isinstance(values[0], module.StrategyConfig):
                calls.append(values)
        return original(cast(object, value))

    monkeypatch.setattr(module, "canonical_sha256", observed)
    engine = DecisionEngine(paper.strategy)
    for _ in range(4):
        assert engine.evaluate(context) == expected
    assert len(calls) == 1


def test_context_hash_reuse_rechecks_full_depth_and_nonbook_inputs():
    paper = PaperConfig()
    context = demo_market_context(paper)
    engine = DecisionEngine(paper.strategy)
    deeper = replace(context.yes_book.bids[-1], quantity=module.Quantity.parse("111"))
    book = replace(context.yes_book, bids=(*context.yes_book.bids[:-1], deeper))
    assessment = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
    ).assess(
        bundle=context.bundle,
        proof=prove_binary_complement(context.bundle),
        books=(book, context.no_book),
        observed_at=context.as_of,
        provenance=context.assessment.provenance,
    )
    changed = replace(context, yes_book=book, assessment=assessment)
    for inputs in (context, context, changed, changed, context):
        assert engine.evaluate(inputs) == DecisionEngine(paper.strategy).evaluate(inputs)
    changed_capacity = replace(context, risk_capacity=Money.parse("3"))
    assert engine.evaluate(changed_capacity) == DecisionEngine(paper.strategy).evaluate(
        changed_capacity
    )
    assert engine.context_cache_hits == 2
    assert engine.context_cache_misses == 4


def test_context_hash_cache_bypasses_mutable_depth():
    paper = PaperConfig()
    context = demo_market_context(paper)
    mutable = list(context.yes_book.bids)
    book = replace(context.yes_book, bids=cast(tuple[BookLevel, ...], mutable))
    engine = DecisionEngine(paper.strategy)
    for amount in ("20", "23", "20"):
        mutable[-1] = replace(mutable[-1], quantity=module.Quantity.parse(amount))
        assessment = BinaryBookEligibilityEvaluator(
            BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
        ).assess(
            bundle=context.bundle,
            proof=prove_binary_complement(context.bundle),
            books=(book, context.no_book),
            observed_at=context.as_of,
            provenance=context.assessment.provenance,
        )
        changed = replace(context, yes_book=book, assessment=assessment)
        assert engine.evaluate(changed) == DecisionEngine(paper.strategy).evaluate(changed)
    assert engine.context_cache_hits == 0
    assert engine.context_cache_misses == 3


def test_mutable_grid_configuration_uses_original_generator():
    paper = PaperConfig()
    context = demo_market_context(paper)
    offsets = [0, -1, -2]
    settings = replace(paper.strategy, price_offsets_ticks=cast(tuple[int, ...], offsets))
    engine = DecisionEngine(settings)
    for offset in (-2, -3, -2):
        offsets[-1] = offset
        assert engine.evaluate(context) == DecisionEngine(settings).evaluate(context)
    assert engine.cached_candidate_grids == 0


@pytest.mark.parametrize("offset", [0, 1, -1])
def test_decision_input_digest_matches_original_whole_tree_encoder(offset: int):
    paper = PaperConfig()
    original = demo_market_context(paper)
    context = replace(original, as_of=UtcTimestamp(original.as_of.nanoseconds_since_epoch + offset))
    inputs = (module.OBJECTIVE_VERSION, paper.strategy, context, None)
    reference_bytes = json.dumps(
        canonicalize(inputs),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert (
        DecisionEngine(paper.strategy).evaluate(context).input_hash
        == hashlib.sha256(reference_bytes).hexdigest()
    )


def dependency_change(context: MarketContext, dependency: str) -> MarketContext:
    """Change one grid dependency and rebuild coherent synthetic admission evidence."""
    bundle = context.bundle
    yes, no = context.yes_book, context.no_book
    fee, reward, calibration = context.fee_rules, context.reward_rules, context.calibration
    assert fee is not None
    assert reward is not None
    assert calibration is not None
    if dependency == "market":
        market = MarketId("different-synthetic-market")
        bundle = replace(
            bundle,
            market=replace(bundle.market, market_id=market),
            instruments=tuple(replace(item, market_id=market) for item in bundle.instruments),
            precision=replace(bundle.precision, market_id=market),
            cost_rule_set=replace(bundle.cost_rule_set, market_id=market),
        )
        yes, no = replace(yes, market_id=market), replace(no, market_id=market)
        fee = replace(fee, evidence=replace(fee.evidence, market_id=market))
        reward = replace(reward, evidence=replace(reward.evidence, market_id=market))
        calibration = replace(calibration, period=replace(calibration.period, market_id=market))
    elif dependency == "tick":
        tick = bundle.precision.tick_size
        assert tick is not None
        bundle = replace(
            bundle,
            precision=replace(bundle.precision, tick_size=tick * ExactDecimal(5, 1)),
        )
    elif dependency == "minimum_order":
        bundle = replace(
            bundle, precision=replace(bundle.precision, minimum_order_size=Quantity.parse("7"))
        )
    elif dependency == "minimum_reward":
        reward = replace(reward, minimum_size=Quantity.parse("8"))
    elif dependency.endswith("instrument"):
        original = yes.instrument_id if dependency.startswith("yes") else no.instrument_id
        replacement = InstrumentId(f"different-{dependency}")
        bundle = replace(
            bundle,
            instruments=tuple(
                replace(item, instrument_id=replacement) if item.instrument_id == original else item
                for item in bundle.instruments
            ),
            cost_rule_set=replace(
                bundle.cost_rule_set,
                applicable_instrument_ids=tuple(
                    replacement if item == original else item
                    for item in bundle.cost_rule_set.applicable_instrument_ids
                ),
            ),
        )
        if dependency.startswith("yes"):
            yes = replace(yes, instrument_id=replacement)
        else:
            no = replace(no, instrument_id=replacement)
    elif dependency.endswith(("bid", "ask")):
        book = yes if dependency.startswith("yes") else no
        assert book.best_bid is not None
        assert book.best_ask is not None
        tick = bundle.precision.tick_size
        assert tick is not None
        if dependency.endswith("bid"):
            bid = replace(book.best_bid, price=Price(book.best_bid.price.value + tick))
            book = replace(book, bids=(bid, *book.bids[1:]), best_bid=bid)
        else:
            ask = replace(book.best_ask, price=Price(book.best_bid.price.value + tick))
            book = replace(book, asks=(ask, *book.asks[1:]), best_ask=ask)
        if dependency.startswith("yes"):
            yes = book
        else:
            no = book
    else:
        assert dependency in {"offsets", "multipliers"}
    assessment = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
    ).assess(
        bundle=bundle,
        proof=prove_binary_complement(bundle),
        books=(yes, no),
        observed_at=context.as_of,
        provenance=context.assessment.provenance,
    )
    assert assessment.disposition is EligibilityDisposition.ELIGIBLE
    return replace(
        context,
        bundle=bundle,
        assessment=assessment,
        yes_book=yes,
        no_book=no,
        fee_rules=fee,
        reward_rules=reward,
        calibration=calibration,
    )


@pytest.mark.parametrize(
    "dependency",
    [
        "market",
        "tick",
        "minimum_order",
        "minimum_reward",
        "offsets",
        "multipliers",
        "yes_instrument",
        "yes_bid",
        "yes_ask",
        "no_instrument",
        "no_bid",
        "no_ask",
    ],
)
def test_each_candidate_grid_dependency_invalidates_primed_cache(
    dependency: str, monkeypatch: pytest.MonkeyPatch
):
    paper = PaperConfig()
    # Positive offsets make an ask-boundary move alter the generated candidate grid.
    config = replace(paper.strategy, price_offsets_ticks=(0, 1, 2))
    context = demo_market_context(paper)
    engine = DecisionEngine(config)
    initial = engine.evaluate(context)
    changed = dependency_change(context, dependency)
    if dependency == "offsets":
        engine.config = replace(config, price_offsets_ticks=(-1, 0, 1))
    elif dependency == "multipliers":
        engine.config = replace(config, size_multipliers=(1, 3))
    expected = DecisionEngine(engine.config).evaluate(changed)
    assert expected.evaluations
    assert tuple(row.candidate for row in expected.evaluations) == module.generate_candidates(
        changed, engine.config
    )
    assert tuple(row.candidate for row in initial.evaluations) != tuple(
        row.candidate for row in expected.evaluations
    )
    original = module.generate_candidates
    calls: list[MarketContext] = []

    def observed(ctx: MarketContext, settings: module.StrategyConfig):
        calls.append(ctx)
        return original(ctx, settings)

    monkeypatch.setattr(module, "generate_candidates", observed)
    for _ in range(2):
        actual = engine.evaluate(changed)
        assert actual == expected
        assert canonical_sha256(actual) == canonical_sha256(expected)
    assert calls == [changed]
    assert engine.candidate_cache_misses == 2
    assert engine.candidate_cache_hits == 1
