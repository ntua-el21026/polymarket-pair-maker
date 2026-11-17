from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.exact import (
    ExactDecimal,
    Money,
    Price,
    Quantity,
    Rate,
    RoundingMode,
)
from polymarket_pair_maker.core.health import BookHealth
from polymarket_pair_maker.core.ids import CanonicalEventId, IngestId, MarketId
from polymarket_pair_maker.core.provenance import Provenance
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics.fees import FeeRules
from polymarket_pair_maker.economics.reference import RuleEvidence
from polymarket_pair_maker.economics.rewards import MidpointConvention, RewardRules
from polymarket_pair_maker.market_data import (
    BinaryBookEligibilityConfig,
    BinaryBookEligibilityEvaluator,
)
from polymarket_pair_maker.reference import BitemporalFields, PolymarketReferenceBuilder
from polymarket_pair_maker.research.paper_calibration import (
    MARKOUT_HORIZONS_NS,
    CalibrationObservation,
    CalibrationPeriod,
    CalibrationSource,
    MarkoutObservation,
    fit_calibration,
)
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state.order_books import BookLevel, ImmutableOrderBookSnapshot
from polymarket_pair_maker.strategy import (
    ActiveQuote,
    DecisionEngine,
    MarketContext,
    StrategyAction,
    StrategyConfig,
    generate_candidates,
)
from polymarket_pair_maker.venues.polymarket.schemas import (
    parse_clob_market_info,
    parse_gamma_markets,
    parse_rest_book,
)

FIXTURES = Path(__file__).parents[1] / "golden" / "fixtures"
NOW = UtcTimestamp(500_000_000_000)
SOURCE_HASH = canonical_sha256("SYNTHETIC strategy/calibration tests")


def calibration_observation(
    market_id: MarketId,
    outcome: str,
    index: int,
    *,
    offset: int = 0,
    multiplier: int = 1,
    exit_price: str = "0.43",
) -> CalibrationObservation:
    return CalibrationObservation(
        observation_id=f"{outcome}-{index}-{offset}-{multiplier}",
        source_sha256=SOURCE_HASH,
        market_id=market_id,
        outcome=outcome,
        offset_ticks=offset,
        size_multiplier=multiplier,
        quote_at=UtcTimestamp(90_000_000_000),
        finished_at=UtcTimestamp(120_000_000_000),
        available_at=UtcTimestamp(121_000_000_000),
        fill_at=UtcTimestamp(100_000_000_000),
        quote_price=Price(ExactDecimal(40 + offset, 2)),
        quote_quantity=Quantity.parse(5 * multiplier),
        filled_quantity=Quantity.parse(5 * multiplier),
        queue_cleared=True,
        markouts=tuple(
            MarkoutObservation(
                horizon,
                UtcTimestamp(100_000_000_000 + horizon),
                UtcTimestamp(100_000_000_000 + horizon),
                Price.parse(exit_price),
            )
            for horizon in MARKOUT_HORIZONS_NS
        ),
        liquidation_depth=Quantity.parse(100),
        liquidation_cost_per_share=ExactDecimal.zero(),
        residence_ns=30_000_000_000,
        cancel_requests=1,
        committed_capital=Money.parse(2 * multiplier),
        capital_capacity=Money.parse(100),
    )


def strategy_context(
    *,
    yes_exit: str = "0.43",
    no_exit: str = "0.43",
    all_strata: bool = False,
    native_condition_id: str = "0xcondition",
) -> MarketContext:
    gamma = parse_gamma_markets(
        (FIXTURES / "polymarket_gamma_market_keyset.json").read_bytes()
    ).markets[0]
    token_ids = tuple(f"{native_condition_id}-{token}" for token in gamma.clob_token_ids)
    gamma = replace(gamma, condition_id=native_condition_id, clob_token_ids=token_ids)
    clob = parse_clob_market_info(
        (FIXTURES / "polymarket_clob_market_info.json").read_bytes(),
        condition_id=gamma.condition_id,
    )
    clob = replace(
        clob,
        tokens=tuple(
            replace(token, token_id=token_ids[index]) for index, token in enumerate(clob.tokens)
        ),
    )
    rest = replace(
        parse_rest_book((FIXTURES / "polymarket_rest_book_yes.json").read_bytes()),
        condition_id=native_condition_id,
        asset_id=token_ids[0],
    )
    bundle = PolymarketReferenceBuilder().build(
        gamma=gamma, clob=clob, rest_book=rest, observed_at=UtcTimestamp(490_000_000_000)
    )
    bundle = replace(
        bundle, cost_rule_set=replace(bundle.cost_rule_set, rate=Rate.parse(0), exponent=1)
    )
    provenance = Provenance(
        source_ingest_ids=(IngestId("synthetic-book"),),
        source_payload_hashes=(SOURCE_HASH,),
        code_version="test-paper-v1",
    )
    bid, ask = (
        BookLevel(Price.parse("0.40"), Quantity.parse(100)),
        BookLevel(Price.parse("0.60"), Quantity.parse(100)),
    )
    books = tuple(
        ImmutableOrderBookSnapshot(
            instrument_id=item.instrument_id,
            market_id=bundle.market.market_id,
            generation=1,
            health=BookHealth.HEALTHY,
            invalidation_reason=None,
            bids=(bid,),
            asks=(ask,),
            best_bid=bid,
            best_ask=ask,
            last_trade_price=None,
            last_source_event_id=CanonicalEventId(f"event-{item.outcome_index}"),
            last_source_provenance=provenance,
            state_source_provenance=provenance,
            market_version_id=bundle.market.version_id,
            precision_rule_version_id=bundle.precision.version_id,
            cost_rule_set_version_id=bundle.cost_rule_set.version_id,
            last_valid_update_time=NOW,
            last_venue_timestamp=NOW,
        )
        for item in bundle.instruments
    )
    assessment = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
    ).assess(
        bundle=bundle,
        proof=prove_binary_complement(bundle),
        books=books,
        observed_at=NOW,
        provenance=provenance,
    )
    evidence = RuleEvidence(
        "synthetic-rules-v1",
        "SYNTHETIC-v2",
        bundle.market.market_id,
        ("fixture:synthetic",),
        SOURCE_HASH,
        BitemporalFields(
            UtcTimestamp(490_000_000_000),
            UtcTimestamp(490_000_000_000),
            UtcTimestamp(10_000_000_000_000),
        ),
        synthetic=True,
    )
    fees = FeeRules(
        evidence,
        ExactDecimal.zero(),
        ExactDecimal.zero(),
        bundle.payout_asset_id,
        Money.parse("0.00001"),
        RoundingMode.HALF_UP,
        True,
    )
    rewards = RewardRules(
        evidence,
        Quantity.parse(5),
        ExactDecimal(20),
        ExactDecimal.one(),
        ExactDecimal(3),
        0,
        MidpointConvention.SYNTHETIC_COMPLEMENTARY_CUMULATIVE_DEPTH,
        bundle.payout_asset_id,
        Money.parse(1),
    )
    period = CalibrationPeriod(
        bundle.market.market_id,
        UtcTimestamp(0),
        UtcTimestamp(450_000_000_000),
        NOW,
        UtcTimestamp(10_000_000_000_000),
        (SOURCE_HASH,),
        CalibrationSource.SYNTHETIC,
    )
    observations = tuple(
        calibration_observation(
            bundle.market.market_id,
            outcome,
            index,
            offset=offset,
            multiplier=multiplier,
            exit_price=exit_price,
        )
        for outcome, exit_price in (("YES", yes_exit), ("NO", no_exit))
        for index in range(2)
        for offset in ((0, -1, -2) if all_strata else (0,))
        for multiplier in ((1, 2) if all_strata else (1,))
    )
    calibration = fit_calibration(observations, period)
    return MarketContext(
        bundle,
        assessment,
        books[0],
        books[1],
        NOW,
        fees,
        rewards,
        calibration,
        Money.parse(25),
        True,
        False,
        True,
    )


@pytest.mark.parametrize(
    ("yes_exit", "no_exit", "action"),
    [
        ("0.43", "0.43", StrategyAction.QUOTE_PAIR),
        ("0.43", "0.37", StrategyAction.QUOTE_YES),
        ("0.37", "0.43", StrategyAction.QUOTE_NO),
        ("0.37", "0.37", StrategyAction.NO_QUOTE),
    ],
)
def test_all_actions_require_profitable_execution(
    yes_exit: str, no_exit: str, action: StrategyAction
) -> None:
    context = strategy_context(yes_exit=yes_exit, no_exit=no_exit)
    decision = DecisionEngine().evaluate(context)
    assert decision.action is action, decision.reasons
    assert len(decision.evaluations) == 49
    for evaluation in decision.evaluations:
        assert all(item.incentive_contribution == Money.parse(0) for item in evaluation.scenarios)


def test_candidate_grid_integer_ticks_postonly_bounded_and_asymmetric() -> None:
    context = strategy_context()
    config = StrategyConfig()
    candidates = generate_candidates(context, config)
    assert len(candidates) == config.maximum_candidates == 49
    assert len({item.identity for item in candidates}) == len(candidates)
    assert any(
        len(item.legs) == 2 and item.legs[0].quantity != item.legs[1].quantity
        for item in candidates
    )
    for candidate in candidates:
        for leg in candidate.legs:
            assert leg.price.value.is_integral_multiple_of(ExactDecimal.parse("0.01"))
            assert leg.price.value < ExactDecimal.parse("0.60")
            assert leg.quantity.value >= ExactDecimal(5)


@pytest.mark.parametrize(
    "case",
    [
        "data",
        "negative-risk",
        "settlement",
        "catalyst",
        "status",
        "asset",
        "calibration",
        "reward",
        "fee",
        "stale",
        "future",
        "cost-contradiction",
        "depth",
        "resolution",
        "capacity",
        "identity",
    ],
)
def test_fail_closed_eligibility(case: str) -> None:
    context = strategy_context()
    assert context.fee_rules is not None
    assert context.reward_rules is not None
    if case == "data":
        context = replace(context, yes_book=replace(context.yes_book, health=BookHealth.STALE))
    elif case == "negative-risk":
        context = replace(
            context,
            bundle=replace(context.bundle, market=replace(context.bundle.market, neg_risk=True)),
        )
    elif case == "settlement":
        context = replace(context, settlement_rules_verified=False)
    elif case == "catalyst":
        context = replace(context, catalyst_excluded=True)
    elif case == "status":
        from polymarket_pair_maker.core.health import MarketStatus

        context = replace(
            context,
            bundle=replace(
                context.bundle, market=replace(context.bundle.market, status=MarketStatus.CLOSED)
            ),
        )
    elif case == "asset":
        context = replace(
            context,
            fee_rules=replace(
                context.fee_rules, fee_asset_id=type(context.bundle.payout_asset_id)("unknown")
            ),
        )
    elif case == "calibration":
        context = replace(context, calibration=None)
    elif case == "reward":
        context = replace(context, reward_rules=None)
    elif case == "fee":
        context = replace(context, fee_rules=None)
    elif case in {"stale", "future"}:
        times = (
            replace(context.fee_rules.evidence.times, effective_to=NOW)
            if case == "stale"
            else replace(
                context.fee_rules.evidence.times, observed_at=UtcTimestamp(501_000_000_000)
            )
        )
        context = replace(
            context,
            fee_rules=replace(
                context.fee_rules, evidence=replace(context.fee_rules.evidence, times=times)
            ),
        )
    elif case == "cost-contradiction":
        context = replace(
            context,
            bundle=replace(
                context.bundle,
                cost_rule_set=replace(
                    context.bundle.cost_rule_set, rate=Rate.parse("0.07"), exponent=2
                ),
            ),
        )
    elif case == "depth":
        context = replace(context, yes_book=replace(context.yes_book, bids=()))
    elif case == "resolution":
        context = replace(
            context,
            bundle=replace(context.bundle, market=replace(context.bundle.market, end_time=None)),
        )
    elif case == "capacity":
        context = replace(context, risk_capacity=Money.parse(0))
    else:
        context = replace(context, yes_book=replace(context.yes_book, generation=2))
    decision = DecisionEngine().evaluate(context)
    assert decision.action is StrategyAction.NO_QUOTE
    assert decision.reasons
    assert not decision.evaluations


def test_no_quote_when_observed_samples_do_not_support_candidate_sizes_or_prices() -> None:
    context = strategy_context()
    assert context.reward_rules is not None
    context = replace(
        context, reward_rules=replace(context.reward_rules, minimum_size=Quantity.parse(6))
    )
    decision = DecisionEngine().evaluate(context)
    assert decision.action is StrategyAction.NO_QUOTE
    assert any(
        "CANDIDATE_OUTSIDE_CALIBRATION_SUPPORT" in item.reasons for item in decision.evaluations
    )


def test_hysteresis_retains_quote_and_safety_invalidations_cancel_immediately() -> None:
    context = strategy_context()
    engine = DecisionEngine()
    first = engine.evaluate(context)
    assert first.candidate is not None
    current = ActiveQuote(first.candidate, NOW)
    retained = engine.evaluate(context, current)
    assert retained.retained_existing
    assert retained.candidate == first.candidate
    unsafe = engine.evaluate(replace(context, catalyst_excluded=True), current)
    assert unsafe.cancel_immediately
    assert unsafe.action is StrategyAction.NO_QUOTE
    expired = engine.evaluate(context, replace(current, admitted_at=UtcTimestamp(400_000_000_000)))
    assert expired.cancel_immediately
    assert expired.action is StrategyAction.NO_QUOTE


def test_cache_is_bounded_and_invalidated_by_risk_and_calibration() -> None:
    context = strategy_context()
    engine = DecisionEngine(StrategyConfig(cache_capacity=64))
    first = engine.evaluate(context)
    assert engine.cache_misses == 49
    assert engine.evaluate(context) == first
    assert engine.cache_hits == 49
    constrained = engine.evaluate(replace(context, risk_capacity=Money.parse(1)))
    assert constrained.action is StrategyAction.NO_QUOTE
    assert engine.cached_evaluations == 64
    negative = engine.evaluate(strategy_context(yes_exit="0.37", no_exit="0.37"))
    assert negative.action is StrategyAction.NO_QUOTE
    assert engine.cached_evaluations == 64


def test_lowest_capital_breaks_identical_return_ties() -> None:
    context = strategy_context(all_strata=True)
    decision = DecisionEngine().evaluate(context)
    assert decision.candidate is not None
    assert all(leg.size_multiplier == 1 for leg in decision.candidate.legs)


def test_selection_is_bounded_and_never_switches_while_active_market_is_missing() -> None:
    context = strategy_context()
    engine = DecisionEngine()
    ranked = engine.evaluate_markets((context,))
    assert ranked.selected is not None
    assert ranked.selected.candidate is not None
    current = ActiveQuote(ranked.selected.candidate, NOW)
    missing = engine.evaluate_markets((), current)
    assert missing.selected is not None
    assert missing.selected.cancel_immediately
    with pytest.raises(ValueError, match="duplicate"):
        engine.evaluate_markets((context, context))
    with pytest.raises(ValueError, match="capacity"):
        engine.evaluate_markets((context,) * 9)


def test_market_ranking_is_order_invariant_and_keeps_exclusions() -> None:
    first = strategy_context(native_condition_id="0xfirst", yes_exit="0.41", no_exit="0.41")
    strongest = strategy_context(native_condition_id="0xsecond")
    excluded = replace(strategy_context(native_condition_id="0xthird"), event_risk_verified=False)
    engine = DecisionEngine()
    ranking = engine.evaluate_markets((first, strongest, excluded))
    assert ranking == engine.evaluate_markets((excluded, strongest, first))
    assert ranking.selected is not None
    assert ranking.selected.candidate is not None
    assert ranking.selected.candidate.market_id == strongest.bundle.market.market_id
    exclusions = dict(ranking.decisions)[excluded.bundle.market.market_id]
    assert "EVENT_RISK_EXCLUSION" in exclusions.reasons


def test_hysteresis_minimum_interval_then_economic_replacement() -> None:
    context = strategy_context()
    engine = DecisionEngine()
    first = engine.evaluate(context)
    single = next(
        item.candidate
        for item in first.evaluations
        if item.accepted and item.candidate.action is StrategyAction.QUOTE_YES
    )
    recent = ActiveQuote(single, NOW)
    assert engine.evaluate(context, recent).candidate == single
    older = replace(recent, admitted_at=UtcTimestamp(NOW.nanoseconds_since_epoch - 6_000_000_000))
    replacement = engine.evaluate(context, older)
    assert replacement.action is StrategyAction.QUOTE_PAIR
    assert not replacement.retained_existing
    wide_hysteresis = DecisionEngine(StrategyConfig(hysteresis_improvement=ExactDecimal.one()))
    held = wide_hysteresis.evaluate(context, older)
    assert held.candidate == single
    assert held.retained_existing


def test_reference_expiry_bypasses_previously_cached_profitable_decision() -> None:
    context = strategy_context()
    engine = DecisionEngine()
    quoted = engine.evaluate(context)
    assert quoted.candidate is not None
    assert context.fee_rules is not None
    expired = replace(
        context.fee_rules,
        evidence=replace(
            context.fee_rules.evidence,
            times=replace(context.fee_rules.evidence.times, effective_to=NOW),
        ),
    )
    blocked = engine.evaluate(
        replace(context, fee_rules=expired), ActiveQuote(quoted.candidate, NOW)
    )
    assert blocked.cancel_immediately
    assert "RULE_EXPIRED" in blocked.reasons
    assert engine.cache_hits == 0


def test_same_rank_uses_stable_identity_after_economics_exposure_and_capital() -> None:
    context = strategy_context()
    first = DecisionEngine().evaluate(replace(context, risk_capacity=Money.parse(2)))
    assert first.candidate is not None
    eligible = tuple(item for item in first.evaluations if item.accepted and item.candidate.legs)
    assert len(eligible) == 2
    assert eligible[0].score == eligible[1].score
    assert eligible[0].downside == eligible[1].downside
    assert first.candidate.identity == min(item.candidate.identity for item in eligible)


def test_shallow_good_bid_cannot_hide_extreme_exit_cost_in_deep_bad_bids() -> None:
    context = strategy_context()
    top = BookLevel(Price.parse("0.40"), Quantity.parse(1))
    bids = (top, BookLevel(Price.parse("0.01"), Quantity.parse(99)))
    yes = replace(context.yes_book, bids=bids, best_bid=top)
    no = replace(context.no_book, bids=bids, best_bid=top)
    assessment = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(5_000_000_000, 1_000_000_000)
    ).assess(
        bundle=context.bundle,
        proof=prove_binary_complement(context.bundle),
        books=(yes, no),
        observed_at=NOW,
        provenance=context.assessment.provenance,
    )
    decision = DecisionEngine().evaluate(
        replace(context, yes_book=yes, no_book=no, assessment=assessment)
    )
    assert decision.action is StrategyAction.NO_QUOTE
    assert any(
        "CURRENT_EXIT_COST_EXCEEDS_CALIBRATION" in item.reasons for item in decision.evaluations
    )


@pytest.mark.parametrize("offsets", [(), (0, 1, 2, 3), (0, 0), (4,), (True,)])
def test_invalid_grid_is_rejected(offsets: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="price"):
        StrategyConfig(price_offsets_ticks=offsets)
