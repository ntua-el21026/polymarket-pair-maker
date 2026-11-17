from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.ids import (
    AssetId,
    CostRuleSetVersionId,
    InstrumentId,
    MarketId,
    MarketVersionId,
    PrecisionRuleVersionId,
)
from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics import (
    ExactRatio,
    FeeRules,
    IncentiveScenarios,
    MidpointConvention,
    RewardAllocation,
    RewardOrder,
    RewardRules,
    RewardSample,
    RewardScoreCache,
    RuleEvidence,
    estimate_daily_reward,
    estimate_maker_rebate,
    estimate_reward_snapshot,
    execution_fee,
    fee_equivalent,
    instantaneous_share_bounds,
    maker_score,
    order_score,
    payout_after_daily_threshold,
    reward_minimum_age_ns,
    score_own_orders,
    size_cutoff_adjusted_midpoint,
)
from polymarket_pair_maker.reference.models import BitemporalFields, LiquidityRole
from polymarket_pair_maker.state.order_books import (
    BookHealth,
    BookLevel,
    ImmutableOrderBookSnapshot,
)

DAY = 86_400_000_000_000
NOW = UtcTimestamp(DAY + 1_000_000_000)
ASSET = AssetId("SYNTHETIC-pUSD")
MARKET = MarketId("SYNTHETIC-market")


def evidence() -> RuleEvidence:
    return RuleEvidence(
        version="SYNTHETIC-rules-v1",
        protocol_version="SYNTHETIC-v2",
        market_id=MARKET,
        sources=("SYNTHETIC-unit-fixture",),
        source_sha256=canonical_sha256("SYNTHETIC-unit-fixture"),
        times=BitemporalFields(UtcTimestamp(0), UtcTimestamp(0), UtcTimestamp(DAY * 4)),
        synthetic=True,
    )


def reward_rules() -> RewardRules:
    return RewardRules(
        evidence(),
        Quantity.parse(100),
        ExactDecimal(3),
        ExactDecimal(1),
        ExactDecimal(3),
        0,
        MidpointConvention.SYNTHETIC_COMPLEMENTARY_CUMULATIVE_DEPTH,
        ASSET,
        Money.parse(1),
    )


def fee_rules(rate: str = "0.07") -> FeeRules:
    return FeeRules(
        evidence(),
        ExactDecimal.parse(rate),
        ExactDecimal(0),
        ASSET,
        Money.parse("0.00001"),
        RoundingMode.HALF_UP,
        True,
    )


def order(identity: str, outcome: str, side: str, price: str, quantity: str = "100") -> RewardOrder:
    return RewardOrder(identity, outcome, side, Price.parse(price), Quantity.parse(quantity), NOW)


def book(
    outcome: str,
    bids: tuple[tuple[str, str], ...] = (("0.49", "100"),),
    asks: tuple[tuple[str, str], ...] = (("0.51", "100"),),
) -> ImmutableOrderBookSnapshot:
    bid_levels = tuple(BookLevel(Price.parse(p), Quantity.parse(q)) for p, q in bids)
    ask_levels = tuple(BookLevel(Price.parse(p), Quantity.parse(q)) for p, q in asks)
    return ImmutableOrderBookSnapshot(
        InstrumentId(outcome),
        MARKET,
        1,
        BookHealth.HEALTHY,
        None,
        bid_levels,
        ask_levels,
        bid_levels[0] if bid_levels else None,
        ask_levels[0] if ask_levels else None,
        None,
        None,
        None,
        None,
        MarketVersionId("market-v1"),
        PrecisionRuleVersionId("precision-v1"),
        CostRuleSetVersionId("cost-v1"),
        NOW,
        NOW,
    )


def test_official_first_side_worked_example_uses_exact_repeating_scores():
    # Methodology Step 2, checked 2026-11-17: 100 at 1c, 200 at 2c, 100 at 1c.
    result = score_own_orders(
        (
            order("a", "YES", "BUY", "0.49"),
            order("b", "YES", "BUY", "0.48", "200"),
            order("c", "NO", "SELL", "0.51"),
        ),
        midpoint=Price.parse("0.5"),
        rules=reward_rules(),
        decision_at=NOW,
    )
    assert result.side_a == ExactRatio(1000, 9)
    assert result.side_b == ExactRatio(0)
    assert result.score == ExactRatio(1000, 27)
    assert b'"denominator":27' in canonical_json_bytes(result)


def test_official_second_side_numeric_example_and_documentation_label_contradiction():
    # Step 3 labels a YES bid and NO ask as second-side orders, contradicting
    # Equations 2/3. Regress the printed numeric expression independently;
    # actual order grouping follows those equations, never the erroneous labels.
    expression = sum(
        (
            order_score(
                maximum_spread_cents=ExactDecimal(3),
                distance_cents=ExactDecimal.parse(d),
                quantity=Quantity.parse(q),
                multiplier=ExactDecimal(1),
            ).as_fraction()
            for d, q in (("1.5", "100"), ("2", "100"), ("0.5", "200"))
        ),
        Fraction(0),
    )
    assert expression == Fraction(1575, 9)
    result = score_own_orders(
        (
            order("a", "YES", "BUY", "0.485"),
            order("b", "NO", "BUY", "0.48"),
            order("c", "NO", "SELL", "0.505", "200"),
        ),
        midpoint=Price.parse("0.5"),
        rules=reward_rules(),
        decision_at=NOW,
    )
    assert result.side_a == ExactRatio(1475, 9)
    assert result.side_b == ExactRatio(100, 9)


@pytest.mark.parametrize(
    ("midpoint", "expected"),
    [
        ("0.099999", 0),
        ("0.10", 3),
        ("0.90", 3),
        ("0.900001", 0),
    ],
)
def test_midpoint_inclusive_single_side_boundaries(midpoint: str, expected: int):
    assert maker_score(ExactRatio(9), ExactRatio(0), Price.parse(midpoint), ExactDecimal(3)) == (
        ExactRatio(expected)
    )


def test_versioned_divisor_and_in_game_multiplier_are_used():
    assert maker_score(ExactRatio(12), ExactRatio(0), Price.parse("0.5"), ExactDecimal(4)) == (
        ExactRatio(3)
    )
    assert order_score(
        maximum_spread_cents=ExactDecimal(3),
        distance_cents=ExactDecimal(0),
        quantity=Quantity.parse(100),
        multiplier=ExactDecimal(2),
    ) == ExactRatio(200)


@pytest.mark.parametrize("distance", ["3", "3.00001", "10"])
def test_spread_at_or_outside_boundary_has_zero_score(distance: str):
    assert order_score(
        maximum_spread_cents=ExactDecimal(3),
        distance_cents=ExactDecimal.parse(distance),
        quantity=Quantity.parse(100),
        multiplier=ExactDecimal(1),
    ) == ExactRatio(0)


@pytest.mark.parametrize("spread", ["0", "-1", "100.1"])
def test_invalid_spread_configuration_rejected(spread: str):
    with pytest.raises(ValueError, match="spread"):
        replace(reward_rules(), maximum_spread_cents=ExactDecimal.parse(spread))


def test_minimum_size_is_per_own_order_not_aggregate_l2():
    result = score_own_orders(
        (order("a", "YES", "BUY", "0.49", "50"), order("b", "YES", "BUY", "0.49", "50")),
        midpoint=Price.parse("0.5"),
        rules=reward_rules(),
        decision_at=NOW,
    )
    assert result.score == ExactRatio(0)


def test_own_order_age_unknown_young_and_exact_boundary():
    original = order("a", "YES", "BUY", "0.49")
    rules = replace(reward_rules(), minimum_age_ns=10)
    assert score_own_orders(
        (original,), midpoint=Price.parse("0.5"), rules=rules, decision_at=NOW
    ).score == ExactRatio(0)
    assert (
        score_own_orders(
            (replace(original, resting_since=None),),
            midpoint=Price.parse("0.5"),
            rules=rules,
            decision_at=NOW,
        ).score
        is None
    )
    eligible_at = UtcTimestamp(NOW.nanoseconds_since_epoch + 10)
    assert score_own_orders(
        (original,), midpoint=Price.parse("0.5"), rules=rules, decision_at=eligible_at
    ).score == ExactRatio(400, 27)


@pytest.mark.parametrize(
    ("category", "delay", "start", "expected"),
    [
        ("crypto", None, None, 0),
        ("finance", 100, None, 0),
        ("politics", None, None, None),
        ("politics", 10, None, 10),
        ("sports", 10, None, None),
        ("sports", 10, NOW.nanoseconds_since_epoch + 1, 0),
        ("sports", 10, NOW.nanoseconds_since_epoch, 10),
    ],
)
def test_official_minimum_age_exceptions(
    category: str, delay: int | None, start: int | None, expected: int | None
):
    assert (
        reward_minimum_age_ns(
            category=category,
            taker_delay_ns=delay,
            scheduled_game_start=None if start is None else UtcTimestamp(start),
            at=NOW,
        )
        == expected
    )


def test_real_reference_cannot_claim_undocumented_midpoint_algorithm():
    official = replace(evidence(), synthetic=False, protocol_version="v2")
    with pytest.raises(ValueError, match="synthetic midpoint"):
        replace(reward_rules(), evidence=official)
    rules = replace(reward_rules(), evidence=official, midpoint_convention=None)
    result = estimate_reward_snapshot(book("YES"), book("NO"), (), rules, NOW)
    assert result.adjusted_midpoint is None
    assert "SIZE_CUTOFF_MIDPOINT_CONVENTION_UNVERIFIED" in result.reasons


def test_synthetic_cutoff_midpoint_excludes_thin_top_and_includes_own_quotes():
    yes = book("YES", (("0.49", "1"), ("0.46", "100")))
    no = book("NO", (), ())
    before_hash = canonical_sha256((yes, no))
    initial = size_cutoff_adjusted_midpoint(yes, no, (), reward_rules(), NOW)
    changed = size_cutoff_adjusted_midpoint(
        yes, no, (order("own", "YES", "BUY", "0.48"),), reward_rules(), NOW
    )
    assert initial.midpoint == Price.parse("0.485")
    assert changed.midpoint == Price.parse("0.495")
    assert canonical_sha256((yes, no)) == before_hash


def test_hypothetical_no_buy_changes_complementary_midpoint():
    yes = book("YES", (("0.46", "100"),), (("0.55", "100"),))
    no = book("NO", (), ())
    changed = size_cutoff_adjusted_midpoint(
        yes, no, (order("own-no", "NO", "BUY", "0.48"),), reward_rules(), NOW
    )
    assert changed.cutoff_ask == Price.parse("0.52")
    assert changed.midpoint == Price.parse("0.49")


def test_missing_size_cutoff_depth_does_not_fall_back_to_bbo():
    result = size_cutoff_adjusted_midpoint(
        book("YES", (("0.49", "1"),), (("0.51", "1"),)),
        book("NO", (), ()),
        (),
        reward_rules(),
        NOW,
    )
    assert result.midpoint is None
    assert result.reasons == ("INSUFFICIENT_SIZE_CUTOFF_DEPTH",)


def test_public_depth_cannot_claim_rigorous_competitor_upper_bound():
    result = estimate_reward_snapshot(
        book("YES"), book("NO"), (order("own", "YES", "BUY", "0.49"),), reward_rules(), NOW
    )
    assert result.own_score == ExactRatio(400, 27)
    assert result.conservative_share is None
    assert result.competitor_side_mass_upper is None
    assert result.conservative_daily_income == Money.parse(0)
    assert result.verified_payment is None


def test_synthetic_complete_depth_bound_excludes_hypothetical_quotes_from_competitors():
    result = estimate_reward_snapshot(
        book("YES"),
        book("NO"),
        (order("own", "YES", "BUY", "0.49"),),
        reward_rules(),
        NOW,
        depth_complete=True,
    )
    assert result.competitor_side_mass_upper == ExactRatio(1600, 9)
    assert result.conservative_share == ExactDecimal(1).divide(
        ExactDecimal(7), scale=18, rounding=RoundingMode.FLOOR
    )
    assert "COMPETITOR_WALLET_AND_AGE_UNKNOWN" in result.reasons
    assert "RANDOM_SAMPLE_TIMES_UNKNOWN" in result.reasons


@pytest.mark.parametrize("failed_precondition", ["coverage", "snapshot", "own", "divisor", "tail"])
def test_share_bound_preconditions_fail_closed(failed_precondition: str):
    result = instantaneous_share_bounds(
        own_score=ExactRatio(10),
        competitor_mass_upper=ExactRatio(20),
        competitor_mass_lower=ExactRatio(20),
        midpoint=Price.parse("0.09" if failed_precondition == "tail" else "0.5"),
        single_sided_divisor=ExactDecimal(4 if failed_precondition == "divisor" else 3),
        complete_competitor_coverage=failed_precondition != "coverage",
        common_reference_snapshot=failed_precondition != "snapshot",
        excludes_own_liquidity=failed_precondition != "own",
    )
    assert result.lower is None
    assert result.reasons


def test_all_zero_reward_sample_is_undefined():
    result = instantaneous_share_bounds(
        own_score=ExactRatio(0),
        competitor_mass_upper=ExactRatio(0),
        competitor_mass_lower=None,
        midpoint=Price.parse("0.5"),
        single_sided_divisor=ExactDecimal(3),
        complete_competitor_coverage=True,
        common_reference_snapshot=True,
        excludes_own_liquidity=True,
    )
    assert result.lower is None
    assert result.reasons == ("ALL_ZERO_SAMPLING_STATE",)


@pytest.mark.parametrize(("price", "expected"), [("0.1", "0.63"), ("0.5", "1.75"), ("0.9", "0.63")])
def test_official_crypto_fee_table_examples(price: str, expected: str):
    result = execution_fee(
        Price.parse(price), Quantity.parse(100), LiquidityRole.TAKER, fee_rules(), NOW, ASSET
    )
    assert result.amount == Money.parse(expected)


@pytest.mark.parametrize(
    ("shares", "expected"), [("0.0005", "0"), ("0.001", "0.00002"), ("0.006", "0.00011")]
)
def test_explicit_synthetic_fee_rounding(shares: str, expected: str):
    assert execution_fee(
        Price.parse("0.5"), Quantity.parse(shares), LiquidityRole.TAKER, fee_rules(), NOW, ASSET
    ).amount == Money.parse(expected)


def test_maker_zero_fee_is_separate_from_unknown_taker_rounding():
    rules = replace(fee_rules(), rounding=None)
    assert execution_fee(
        Price.parse("0.5"), Quantity.parse(100), LiquidityRole.MAKER, rules, NOW, ASSET
    ).amount == Money.parse(0)
    result = execution_fee(
        Price.parse("0.5"), Quantity.parse(100), LiquidityRole.TAKER, rules, NOW, ASSET
    )
    assert result.amount is None
    assert result.reasons == ("FEE_ROUNDING_UNVERIFIED",)


def test_fee_asset_mismatch_and_future_reference_block():
    assert (
        execution_fee(
            Price.parse("0.5"),
            Quantity.parse(100),
            LiquidityRole.TAKER,
            fee_rules(),
            NOW,
            AssetId("USDC"),
        ).amount
        is None
    )
    future = replace(
        evidence(),
        times=BitemporalFields(
            UtcTimestamp(NOW.nanoseconds_since_epoch + 1), UtcTimestamp(0), UtcTimestamp(DAY * 4)
        ),
    )
    assert "RULE_NOT_YET_OBSERVED" in future.valid_at(NOW)
    assert "RULE_EXPIRED" in evidence().valid_at(UtcTimestamp(DAY * 4))


@pytest.mark.parametrize(("rate", "own"), [("0", "2"), ("0.07", "0")])
def test_fee_free_or_unfilled_makers_have_zero_rebate(rate: str, own: str):
    result = estimate_maker_rebate(
        own_executed_fee_equivalent=Money.parse(own),
        total_executed_fee_equivalent=None,
        rebate_pool=None,
        rules=fee_rules(rate),
        at=NOW,
        collateral_asset_id=ASSET,
    )
    assert result.estimated_accrued == Money.parse(0)
    assert result.verified_payment is None


def test_maker_rebate_uses_executed_fee_curve_share_and_never_realized_income():
    own = fee_equivalent(Price.parse("0.5"), Quantity.parse(100), ExactDecimal.parse("0.07"))
    result = estimate_maker_rebate(
        own_executed_fee_equivalent=own,
        total_executed_fee_equivalent=Money.parse("17.5"),
        rebate_pool=Money.parse("3.5"),
        rules=fee_rules(),
        at=NOW,
        collateral_asset_id=ASSET,
    )
    assert result.estimated_accrued == Money.parse("0.35")
    assert result.conservative_contribution == Money.parse(0)
    assert result.verified_payment is None


def test_missing_daily_rebate_competition_is_unknown():
    result = estimate_maker_rebate(
        own_executed_fee_equivalent=Money.parse(1),
        total_executed_fee_equivalent=None,
        rebate_pool=None,
        rules=fee_rules(),
        at=NOW,
        collateral_asset_id=ASSET,
    )
    assert result.estimated_accrued is None


@pytest.mark.parametrize(
    ("earnings", "expected"), [("0.4", "0"), ("0.99999", "0"), ("1", "1"), ("1.2", "1.2")]
)
def test_official_daily_payout_threshold_and_no_rollover(earnings: str, expected: str):
    assert payout_after_daily_threshold(Money.parse(earnings), Money.parse(1)) == Money.parse(
        expected
    )


def allocation() -> RewardAllocation:
    return RewardAllocation(
        evidence(), "allocation", ASSET, Money.parse(10), UtcTimestamp(0), UtcTimestamp(DAY * 3)
    )


def test_complete_synthetic_reward_epoch_is_idempotent_and_estimated_only():
    sample = RewardSample("sample", NOW, ExactRatio(1, 5))
    once = estimate_daily_reward(
        samples=(sample,),
        allocation=allocation(),
        day_start=UtcTimestamp(DAY),
        expected_sample_count=1,
        sampling_schedule_verified=True,
        payout_scope_verified=True,
        payout_minimum=Money.parse(1),
        decision_at=UtcTimestamp(DAY * 2),
    )
    duplicate = estimate_daily_reward(
        samples=(sample, sample),
        allocation=allocation(),
        day_start=UtcTimestamp(DAY),
        expected_sample_count=1,
        sampling_schedule_verified=True,
        payout_scope_verified=True,
        payout_minimum=Money.parse(1),
        decision_at=UtcTimestamp(DAY * 2),
    )
    assert once == duplicate
    assert once.estimated_accrued == Money.parse(2)
    assert once.estimated_payout == Money.parse(2)
    assert once.conservative_contribution == Money.parse(0)
    assert once.verified_payment is None


@pytest.mark.parametrize(("schedule", "scope"), [(False, True), (True, False)])
def test_unknown_sampling_or_payout_scope_never_claims_payment(schedule: bool, scope: bool):
    result = estimate_daily_reward(
        allocation=allocation(),
        day_start=UtcTimestamp(DAY),
        samples=(RewardSample("a", NOW, ExactRatio(1, 5)),),
        expected_sample_count=1,
        sampling_schedule_verified=schedule,
        payout_scope_verified=scope,
        payout_minimum=Money.parse(1),
        decision_at=UtcTimestamp(DAY * 2),
    )
    assert result.estimated_payout is None
    assert result.verified_payment is None


def test_contradictory_duplicate_reward_sample_rejected():
    with pytest.raises(ValueError, match="contradictory"):
        estimate_daily_reward(
            allocation=allocation(),
            day_start=UtcTimestamp(DAY),
            samples=(
                RewardSample("a", NOW, ExactRatio(1, 5)),
                RewardSample("a", NOW, ExactRatio(1, 4)),
            ),
            expected_sample_count=1,
            sampling_schedule_verified=True,
            payout_scope_verified=True,
            payout_minimum=Money.parse(1),
            decision_at=UtcTimestamp(DAY * 2),
        )


def test_incentive_scenarios_cannot_hide_negative_execution():
    assert IncentiveScenarios(
        Money.parse(3), Money.parse(1), Money.parse(0)
    ).conservative == Money.parse(0)
    with pytest.raises(ValueError, match="guaranteed"):
        IncentiveScenarios(Money.parse(3), Money.parse(1), Money.parse(1))


def test_reward_cache_is_bounded_and_invalidates_age_depth_and_rules():
    cache = RewardScoreCache(capacity=2)
    yes, no = book("YES"), book("NO")
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = replace(reward_rules(), minimum_age_ns=10)
    first = cache.estimate(yes, no, own, rules, NOW)
    assert first.own_score == ExactRatio(0)
    assert (
        cache.estimate(yes, no, own, rules, UtcTimestamp(NOW.nanoseconds_since_epoch + 9)) == first
    )
    assert cache.hits == 1
    eligible = cache.estimate(yes, no, own, rules, UtcTimestamp(NOW.nanoseconds_since_epoch + 10))
    assert eligible.own_score == ExactRatio(400, 27)
    changed_book = book("YES", (("0.48", "100"),), (("0.52", "100"),))
    cache.estimate(changed_book, no, own, rules, UtcTimestamp(NOW.nanoseconds_since_epoch + 10))
    assert cache.cached_entries == 2
    expired = cache.estimate(yes, no, own, rules, UtcTimestamp(DAY * 4))
    assert expired.own_score is None
    assert "RULE_EXPIRED" in expired.reasons
    assert cache.misses == 4


@pytest.mark.parametrize("capacity", [0, -1, 4097, True])
def test_reward_cache_rejects_unbounded_or_boolean_capacity(capacity: int):
    with pytest.raises(ValueError, match="capacity"):
        RewardScoreCache(capacity)


def test_reward_source_scope_and_unknown_parameters_fail_closed():
    yes, no = book("YES"), book("NO")
    for rules, expected in (
        (
            replace(reward_rules(), evidence=replace(evidence(), market_id=MarketId("other"))),
            "REWARD_RULE_MARKET_MISMATCH",
        ),
        (replace(reward_rules(), multiplier=None), "REWARD_MULTIPLIER_UNKNOWN"),
        (replace(reward_rules(), minimum_age_ns=None), "REWARD_MINIMUM_AGE_UNKNOWN"),
    ):
        result = estimate_reward_snapshot(yes, no, (), rules, NOW)
        assert result.own_score is None
        assert expected in result.reasons


def test_midpoint_rejects_duplicate_quote_id_and_crossed_books():
    own = order("own", "YES", "BUY", "0.49")
    duplicated = size_cutoff_adjusted_midpoint(
        book("YES"), book("NO"), (own, own), reward_rules(), NOW
    )
    assert "DUPLICATE_HYPOTHETICAL_ORDER_ID" in duplicated.reasons
    crossed = size_cutoff_adjusted_midpoint(
        book("YES", (("0.55", "100"),), (("0.51", "100"),)), book("NO"), (), reward_rules(), NOW
    )
    assert "CROSSED_REWARD_BOOK" in crossed.reasons


def test_incremental_reward_mass_rescores_only_changed_level_and_subtracts_removed_level():
    cache = RewardScoreCache()
    rules = reward_rules()
    yes = book("YES", (("0.49", "100"), ("0.48", "100")), (("0.51", "100"), ("0.52", "100")))
    no = book("NO", (), ())
    own = (order("own", "YES", "BUY", "0.49"),)
    first = cache.estimate(yes, no, own, rules, NOW, depth_complete=True)
    assert cache.cached_level_entries == 4
    assert cache.cached_level_misses == 4
    assert cache.cached_level_hits == 0
    changed = book("YES", (("0.49", "100"), ("0.48", "150")), (("0.51", "100"), ("0.52", "100")))
    second = cache.estimate(changed, no, own, rules, NOW, depth_complete=True)
    assert second == estimate_reward_snapshot(changed, no, own, rules, NOW, depth_complete=True)
    assert second.adjusted_midpoint == first.adjusted_midpoint
    assert cache.cached_level_misses == 5
    assert cache.cached_level_hits == 3
    assert cache.level_invalidations == 0
    assert first.competitor_side_mass_upper is not None
    assert second.competitor_side_mass_upper is not None
    assert (
        second.competitor_side_mass_upper.as_fraction()
        - first.competitor_side_mass_upper.as_fraction()
        == Fraction(50, 9)
    )
    removed = book("YES", (("0.49", "100"),), (("0.51", "100"), ("0.52", "100")))
    third = cache.estimate(removed, no, own, rules, NOW, depth_complete=True)
    assert third == estimate_reward_snapshot(removed, no, own, rules, NOW, depth_complete=True)
    assert cache.cached_level_entries == 3
    assert cache.cached_level_misses == 5
    assert cache.cached_level_hits == 6


def test_incremental_reward_mass_invalidates_for_changed_midpoint_or_rules():
    cache = RewardScoreCache()
    yes, no = book("YES"), book("NO", (), ())
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = reward_rules()
    initial = cache.estimate(yes, no, own, rules, NOW, depth_complete=True)
    moved_own = (order("own", "YES", "BUY", "0.50"),)
    moved = cache.estimate(yes, no, moved_own, rules, NOW, depth_complete=True)
    assert moved == estimate_reward_snapshot(yes, no, moved_own, rules, NOW, depth_complete=True)
    assert initial.adjusted_midpoint != moved.adjusted_midpoint
    assert cache.cached_level_misses == 4
    assert cache.cached_level_hits == 0
    assert cache.level_invalidations == 1
    new_rules = replace(rules, multiplier=ExactDecimal(2))
    updated = cache.estimate(yes, no, moved_own, new_rules, NOW, depth_complete=True)
    assert updated == estimate_reward_snapshot(
        yes, no, moved_own, new_rules, NOW, depth_complete=True
    )
    assert cache.cached_level_misses == 6
    assert cache.cached_level_hits == 0
    assert cache.level_invalidations == 2


def test_incremental_reward_cache_bound_bypasses_storage_without_changing_unknowns():
    cache = RewardScoreCache(maximum_level_entries=1)
    yes, no = book("YES"), book("NO")
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = reward_rules()
    result = cache.estimate(yes, no, own, rules, NOW)
    assert result == estimate_reward_snapshot(yes, no, own, rules, NOW)
    assert cache.cached_level_entries == 0
    assert result.competitor_side_mass_upper is None
    assert result.conservative_share is None
    assert "COMPETITOR_SCORE_UPPER_BOUND_UNAVAILABLE" in result.reasons


@pytest.mark.parametrize("capacity", [0, -1, 65537, True])
def test_incremental_reward_level_cache_requires_strict_capacity(capacity: int):
    with pytest.raises(ValueError, match="level cache bound"):
        RewardScoreCache(maximum_level_entries=capacity)
