from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity, RoundingMode
from polymarket_pair_maker.economics import (
    ExactRatio,
    RewardScoreCache,
    estimate_reward_snapshot,
    fee_equivalent,
    instantaneous_share_bounds,
    maker_score,
    order_score,
)
from tests.unit.test_paper_economics import NOW, book, order, reward_rules


@given(a=st.integers(0, 10**10), b=st.integers(0, 10**10), midpoint=st.integers(10, 90))
def test_proposed_c_three_score_bound_for_nonnegative_sides(a: int, b: int, midpoint: int):
    result = maker_score(
        ExactRatio(a), ExactRatio(b), Price(ExactDecimal(midpoint, 2)), ExactDecimal(3)
    ).as_fraction()
    assert Fraction(a + b, 4) <= result <= Fraction(a + b, 2)


@given(
    scores=st.lists(
        st.tuples(st.integers(0, 10**6), st.integers(0, 10**6)), min_size=1, max_size=20
    ),
    own=st.integers(1, 10**6),
)
def test_wallet_independent_share_bound_contains_arbitrary_wallet_partition(
    scores: list[tuple[int, int]], own: int
):
    total_mass = sum(a + b for a, b in scores)
    total_maker_scores = sum(
        (max(min(Fraction(a), Fraction(b)), Fraction(max(a, b), 3)) for a, b in scores), Fraction(0)
    )
    actual = Fraction(own) / (own + total_maker_scores)
    result = instantaneous_share_bounds(
        own_score=ExactRatio(own),
        competitor_mass_upper=ExactRatio(total_mass),
        competitor_mass_lower=ExactRatio(total_mass),
        midpoint=Price.parse("0.5"),
        single_sided_divisor=ExactDecimal(3),
        complete_competitor_coverage=True,
        common_reference_snapshot=True,
        excludes_own_liquidity=True,
    )
    assert result.lower is not None
    assert result.upper is not None
    assert result.lower.as_fraction() <= actual <= result.upper.as_fraction()
    # Export precision cannot accidentally make the analytical lower bound optimistic.
    assert result.lower.to_decimal(rounding=RoundingMode.FLOOR).as_fraction() <= actual
    assert result.upper.to_decimal(rounding=RoundingMode.CEILING).as_fraction() >= actual


@given(
    spread=st.integers(1, 100),
    distance=st.integers(0, 100),
    quantity=st.integers(0, 10**8),
    multiplier=st.integers(1, 10),
)
def test_score_matches_independent_rational_expression(
    spread: int, distance: int, quantity: int, multiplier: int
):
    actual = order_score(
        maximum_spread_cents=ExactDecimal(spread),
        distance_cents=ExactDecimal(distance),
        quantity=Quantity.parse(quantity),
        multiplier=ExactDecimal(multiplier),
    )
    expected = Fraction(max(spread - distance, 0), spread) ** 2 * quantity * multiplier
    assert actual.as_fraction() == expected


@given(price=st.integers(0, 10000), quantity=st.integers(0, 10**7), rate=st.integers(0, 100))
def test_fee_curve_symmetry_and_exact_maximum(price: int, quantity: int, rate: int):
    p = Price(ExactDecimal(price, 4))
    opposite = Price(ExactDecimal(10000 - price, 4))
    q, r = Quantity.parse(quantity), ExactDecimal(rate, 2)
    fee = fee_equivalent(p, q, r)
    assert fee == fee_equivalent(opposite, q, r)
    assert fee.value <= fee_equivalent(Price.parse("0.5"), q, r).value


@given(
    numerator=st.integers(0, 10**20), denominator=st.integers(1, 10**12), scale=st.integers(0, 18)
)
def test_ratio_canonical_normalization_and_directed_rounding(
    numerator: int, denominator: int, scale: int
):
    exact = Fraction(numerator, denominator)
    ratio = ExactRatio(numerator, denominator)
    assert ratio == ExactRatio(numerator * 7, denominator * 7)
    assert ratio.to_decimal(scale=scale, rounding=RoundingMode.FLOOR).as_fraction() <= exact
    assert ratio.to_decimal(scale=scale, rounding=RoundingMode.CEILING).as_fraction() >= exact


@given(quantities=st.lists(st.integers(0, 1000), min_size=1, max_size=16), complete=st.booleans())
def test_incremental_reward_deltas_match_pure_recomputation_across_updates(
    quantities: list[int], complete: bool
):
    cache = RewardScoreCache(capacity=2, maximum_level_entries=8)
    no = book("NO", (), ())
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = reward_rules()
    for quantity in quantities:
        bids = (("0.49", "100"),) + ((("0.48", str(quantity)),) if quantity else ())
        yes = book("YES", bids, (("0.51", "100"), ("0.52", "100")))
        assert cache.estimate(yes, no, own, rules, NOW, depth_complete=complete) == (
            estimate_reward_snapshot(yes, no, own, rules, NOW, depth_complete=complete)
        )
        assert cache.cached_entries <= 2
        assert cache.cached_level_entries <= 4
