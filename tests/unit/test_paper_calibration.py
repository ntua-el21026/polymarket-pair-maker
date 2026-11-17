from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.research.paper_calibration import (
    MARKOUT_HORIZONS_NS,
    ExecutablePriceObservation,
    MarkoutObservation,
    fit_calibration,
    join_markouts,
)
from tests.unit.test_paper_strategy import calibration_observation, strategy_context


def test_prior_only_statistics_exact_and_distinguish_partial_fill_frequency() -> None:
    context = strategy_context()
    assert context.calibration is not None
    first = calibration_observation(context.bundle.market.market_id, "YES", 0)
    second = replace(
        calibration_observation(context.bundle.market.market_id, "YES", 1),
        filled_quantity=Quantity.parse(2),
        queue_cleared=False,
        cancel_requests=0,
    )
    snapshot = fit_calibration((first, second), context.calibration.period)
    profile = snapshot.profiles[0]
    assert profile.queue_clearing_frequency == ExactDecimal.parse("0.5")
    assert profile.fill_probability == ExactDecimal.one()
    assert profile.conservative_fill_fraction == ExactDecimal.parse("0.4")
    assert profile.baseline_fill_fraction == ExactDecimal.parse("0.7")
    assert profile.sensitivity_fill_fraction == ExactDecimal.one()
    assert profile.cancellation_churn == ExactDecimal.parse("0.5")
    assert profile.capital_utilization == ExactDecimal.parse("0.02")
    assert profile.mean_residence_ns == 30_000_000_000
    assert profile.adverse_selection_frequency == ExactDecimal.zero()
    assert all(row[1] == ExactDecimal.parse("0.03") for row in profile.markouts)
    assert snapshot == fit_calibration((second, first), context.calibration.period)


def test_missing_future_markout_stays_missing_not_zero() -> None:
    points = (
        ExecutablePriceObservation(
            UtcTimestamp(1_000_000_000), UtcTimestamp(1_000_000_000), Price.parse("0.43")
        ),
    )
    joined = join_markouts(
        fill_at=UtcTimestamp(0), prices=points, known_at=UtcTimestamp(2_000_000_000)
    )
    assert joined[0].exit_price == Price.parse("0.43")
    assert all(point.exit_price is None for point in joined[1:])
    context = strategy_context()
    assert context.calibration is not None
    observations = tuple(
        replace(
            calibration_observation(context.bundle.market.market_id, "YES", index),
            markouts=(
                MarkoutObservation(MARKOUT_HORIZONS_NS[0], None, None, None),
                *calibration_observation(context.bundle.market.market_id, "YES", index).markouts[
                    1:
                ],
            ),
        )
        for index in range(2)
    )
    profile = fit_calibration(observations, context.calibration.period).profiles[0]
    assert profile.markouts[0][1:] == (None, None, None)
    assert profile.adverse_selection_frequency is None
    assert "MISSING_MARKOUT_1000000000NS" in profile.reasons


def test_join_uses_no_early_or_unavailable_price() -> None:
    points = (
        ExecutablePriceObservation(
            UtcTimestamp(999_999_999), UtcTimestamp(999_999_999), Price.parse("0.9")
        ),
        ExecutablePriceObservation(
            UtcTimestamp(1_000_000_000), UtcTimestamp(9_000_000_000), Price.parse("0.8")
        ),
        ExecutablePriceObservation(
            UtcTimestamp(1_100_000_000), UtcTimestamp(1_200_000_000), Price.parse("0.43")
        ),
    )
    result = join_markouts(
        fill_at=UtcTimestamp(0), prices=points, known_at=UtcTimestamp(2_000_000_000)
    )
    assert result[0].exit_price == Price.parse("0.43")
    assert result[0].observed_at == UtcTimestamp(1_100_000_000)
    assert result == join_markouts(
        fill_at=UtcTimestamp(0),
        prices=tuple(reversed(points)),
        known_at=UtcTimestamp(2_000_000_000),
    )


@pytest.mark.parametrize(
    "case",
    [
        "period",
        "future-trial",
        "future-markout",
        "duplicate",
        "source",
        "early-markout",
        "late-markout",
    ],
)
def test_leakage_and_invalid_provenance_are_rejected(case: str) -> None:
    context = strategy_context()
    assert context.calibration is not None
    period = context.calibration.period
    observation = calibration_observation(context.bundle.market.market_id, "YES", 0)
    if case == "period":
        with pytest.raises(ValueError, match="disjoint"):
            replace(period, evaluation_start=period.end)
        return
    if case == "early-markout":
        with pytest.raises(ValueError, match="precedes"):
            replace(
                observation,
                markouts=(
                    replace(observation.markouts[0], observed_at=observation.fill_at),
                    *observation.markouts[1:],
                ),
            )
        return
    observations = (observation,)
    if case == "future-trial":
        observations = (replace(observation, available_at=period.evaluation_start),)
    elif case == "future-markout":
        observations = (
            replace(
                observation,
                markouts=(
                    *observation.markouts[:-1],
                    replace(observation.markouts[-1], available_at=period.evaluation_start),
                ),
            ),
        )
    elif case == "duplicate":
        observations = (observation, observation)
    elif case == "source":
        observations = (replace(observation, source_sha256="0" * 64),)
    elif case == "late-markout":
        observations = (
            replace(
                observation,
                markouts=(
                    replace(
                        observation.markouts[0],
                        observed_at=UtcTimestamp(110_000_000_000),
                        available_at=UtcTimestamp(110_000_000_000),
                    ),
                    *observation.markouts[1:],
                ),
            ),
        )
    with pytest.raises(ValueError, match=r"period|future|duplicate|source|lateness"):
        fit_calibration(observations, period)


def test_no_fill_trial_counts_against_empirical_fill_estimate() -> None:
    context = strategy_context()
    assert context.calibration is not None
    observations = tuple(
        calibration_observation(context.bundle.market.market_id, "YES", index) for index in range(3)
    )
    unfilled = replace(
        observations[2],
        filled_quantity=Quantity.parse(0),
        fill_at=None,
        queue_cleared=False,
        markouts=tuple(
            MarkoutObservation(horizon, None, None, None) for horizon in MARKOUT_HORIZONS_NS
        ),
    )
    profile = fit_calibration((*observations[:2], unfilled), context.calibration.period).profiles[0]
    assert profile.conservative_fill_fraction == ExactDecimal.zero()
    assert profile.fill_probability == ExactDecimal.parse("0.666666666666666666")
    assert not profile.reasons


def test_missing_history_returns_explicit_unavailable_snapshot() -> None:
    context = strategy_context()
    assert context.calibration is not None
    empty = fit_calibration((), context.calibration.period)
    assert empty.eligibility_reasons(
        at=context.as_of, market_id=context.bundle.market.market_id
    ) == ("CALIBRATION_UNAVAILABLE",)


def test_ambiguous_same_time_quotes_are_rejected() -> None:
    first = ExecutablePriceObservation(UtcTimestamp(1), UtcTimestamp(2), Price.parse("0.4"))
    with pytest.raises(ValueError, match="contradictory"):
        join_markouts(
            fill_at=UtcTimestamp(0),
            prices=(first, replace(first, price=Price.parse("0.5"))),
            known_at=UtcTimestamp(3),
        )


def test_adverse_fill_fraction_rounds_up_for_repeating_decimal() -> None:
    context = strategy_context()
    assert context.calibration is not None
    observations = tuple(
        replace(
            calibration_observation(context.bundle.market.market_id, "YES", index),
            quote_quantity=Quantity.parse(3),
            filled_quantity=Quantity.parse(1),
        )
        for index in range(2)
    )
    profile = fit_calibration(observations, context.calibration.period).profiles[0]
    assert profile.conservative_fill_fraction == ExactDecimal.parse("0.333333333333333333")
    assert profile.sensitivity_fill_fraction == ExactDecimal.parse("0.333333333333333334")
