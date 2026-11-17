from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Quantity, RoundingMode
from polymarket_pair_maker.research.paper_calibration import fit_calibration
from polymarket_pair_maker.strategy import (
    DecisionEngine,
    StrategyAction,
    StrategyConfig,
    generate_candidates,
)
from tests.unit.test_paper_strategy import calibration_observation, strategy_context


@settings(max_examples=30)
@given(
    offsets=st.lists(st.integers(-3, 3), min_size=1, max_size=3, unique=True),
    sizes=st.lists(st.integers(1, 4), min_size=1, max_size=2, unique=True),
)
def test_bounded_integer_tick_grid_never_crosses(offsets: list[int], sizes: list[int]) -> None:
    context = strategy_context()
    config = StrategyConfig(price_offsets_ticks=tuple(offsets), size_multipliers=tuple(sizes))
    candidates = generate_candidates(context, config)
    assert len(candidates) <= config.maximum_candidates <= 49
    assert len({candidate.identity for candidate in candidates}) == len(candidates)
    for candidate in candidates:
        assert candidate.capital.value == sum(
            (leg.price.value * leg.quantity.value for leg in candidate.legs), ExactDecimal.zero()
        )
        assert all(
            ExactDecimal.zero() < leg.price.value < ExactDecimal.parse("0.60")
            for leg in candidate.legs
        )
        assert all(
            leg.price.value.is_integral_multiple_of(ExactDecimal.parse("0.01"))
            for leg in candidate.legs
        )


@settings(max_examples=25)
@given(capital_cents=st.integers(0, 1000))
def test_quote_never_exceeds_capacity_and_incentives_never_fund_it(capital_cents: int) -> None:
    context = replace(strategy_context(), risk_capacity=Money(ExactDecimal(capital_cents, 2)))
    decision = DecisionEngine().evaluate(context)
    if decision.candidate is not None:
        assert decision.candidate.capital.value <= context.risk_capacity.value
        chosen = next(item for item in decision.evaluations if item.candidate == decision.candidate)
        assert chosen.conservative_net.value > ExactDecimal.zero()
        assert all(
            scenario.incentive_contribution == Money.parse(0) for scenario in chosen.scenarios
        )
    else:
        assert decision.action is StrategyAction.NO_QUOTE


@settings(max_examples=30)
@given(first_fill=st.integers(1, 5), second_fill=st.integers(1, 5))
def test_empirical_fraction_bounds_partial_fills_exactly(first_fill: int, second_fill: int) -> None:
    context = strategy_context()
    assert context.calibration is not None
    observations = tuple(
        replace(
            calibration_observation(context.bundle.market.market_id, "YES", index),
            filled_quantity=Quantity.parse(fill),
        )
        for index, fill in enumerate((first_fill, second_fill))
    )
    profile = fit_calibration(observations, context.calibration.period).profiles[0]
    assert profile.conservative_fill_fraction == ExactDecimal(min(first_fill, second_fill)).divide(
        ExactDecimal(5), scale=18, rounding=RoundingMode.FLOOR
    )
    assert (
        ExactDecimal.zero()
        <= profile.conservative_fill_fraction
        <= profile.baseline_fill_fraction
        <= profile.sensitivity_fill_fraction
        <= ExactDecimal.one()
    )
