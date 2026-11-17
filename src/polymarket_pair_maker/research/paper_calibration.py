"""Prior-only, bounded empirical estimates for PAPER decisions.

These descriptive sample statistics are scenario assumptions, not population
confidence bounds. No synthetic sample can qualify a real-world calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.ids import MarketId, is_sha256_hex
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp

MARKOUT_HORIZONS_NS = (1_000_000_000, 10_000_000_000, 60_000_000_000, 300_000_000_000)
ESTIMATOR_VERSION = "ppm-prior-empirical-scenarios-v1"
ZERO = ExactDecimal.zero()
ONE = ExactDecimal.one()


class CalibrationSource(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    VERIFIED_OBSERVATIONS = "VERIFIED_OBSERVATIONS"


@dataclass(frozen=True, slots=True)
class CalibrationPeriod:
    market_id: MarketId
    start: UtcTimestamp
    end: UtcTimestamp
    evaluation_start: UtcTimestamp
    evaluation_end: UtcTimestamp
    source_hashes: tuple[str, ...]
    source_kind: CalibrationSource

    def __post_init__(self) -> None:
        if not self.start < self.end < self.evaluation_start < self.evaluation_end:
            raise ValueError(
                "calibration and evaluation periods must be disjoint and chronological"
            )
        if not self.source_hashes or any(not is_sha256_hex(h) for h in self.source_hashes):
            raise ValueError("calibration requires explicit SHA-256 source hashes")
        if len(set(self.source_hashes)) != len(self.source_hashes):
            raise ValueError("duplicate calibration sources")
        if type(self.source_kind) is not CalibrationSource:
            raise TypeError("calibration source kind must be explicit")


@dataclass(frozen=True, slots=True)
class MarkoutObservation:
    """Gross executable bid-sweep VWAP for the trial's filled quantity.

    Fees and incremental counterfactual impact remain separate costs. A top of
    book or midpoint with insufficient quantity is not an executable markout.
    The source fixture/verified observation producer must establish that depth.
    """

    horizon_ns: int
    observed_at: UtcTimestamp | None
    available_at: UtcTimestamp | None
    exit_price: Price | None

    def __post_init__(self) -> None:
        if self.horizon_ns not in MARKOUT_HORIZONS_NS:
            raise ValueError("unsupported markout horizon")
        values = (self.observed_at, self.available_at, self.exit_price)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("a missing markout has no price or timestamps")
        if (
            self.observed_at is not None
            and self.available_at is not None
            and self.available_at < self.observed_at
        ):
            raise ValueError("markout cannot be available before observation")
        if self.exit_price is not None and self.exit_price.value > ONE:
            raise ValueError("markout exit probability price exceeds one")


@dataclass(frozen=True, slots=True)
class ExecutablePriceObservation:
    observed_at: UtcTimestamp
    available_at: UtcTimestamp
    price: Price

    def __post_init__(self) -> None:
        if self.available_at < self.observed_at:
            raise ValueError("price availability precedes observation")
        if self.price.value > ONE:
            raise ValueError("executable probability price exceeds one")


def join_markouts(
    *,
    fill_at: UtcTimestamp,
    prices: tuple[ExecutablePriceObservation, ...],
    known_at: UtcTimestamp,
    maximum_lateness_ns: int = 1_000_000_000,
) -> tuple[MarkoutObservation, ...]:
    """First post-horizon observation, bounded lateness, available by known_at.

    Never backfill a missing horizon with an earlier quote or an unavailable
    future observation. The caller scopes observations to one instrument.
    """
    if maximum_lateness_ns < 0 or len(prices) > 100_000:
        raise ValueError("invalid markout join bounds")
    seen: dict[tuple[UtcTimestamp, UtcTimestamp], Price] = {}
    for point in prices:
        key = (point.observed_at, point.available_at)
        if key in seen and seen[key] != point.price:
            raise ValueError("contradictory same-time executable observations")
        seen[key] = point.price
    ordered = sorted(prices, key=lambda point: (point.observed_at, point.available_at))
    result: list[MarkoutObservation] = []
    for horizon in MARKOUT_HORIZONS_NS:
        target = fill_at.nanoseconds_since_epoch + horizon
        point = next(
            (
                point
                for point in ordered
                if target
                <= point.observed_at.nanoseconds_since_epoch
                <= target + maximum_lateness_ns
                and point.available_at <= known_at
            ),
            None,
        )
        result.append(
            MarkoutObservation(horizon, None, None, None)
            if point is None
            else MarkoutObservation(horizon, point.observed_at, point.available_at, point.price)
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    """A completed quote trial and independently available future markouts.

    ``available_at`` dates the trial outcome; each horizon has its own later
    availability. Liquidation cost is incremental slippage/impact per share,
    excluding the sweep already represented by exit_price and excluding fees.
    """

    observation_id: str
    source_sha256: str
    market_id: MarketId
    outcome: str
    offset_ticks: int
    size_multiplier: int
    quote_at: UtcTimestamp
    finished_at: UtcTimestamp
    available_at: UtcTimestamp
    fill_at: UtcTimestamp | None
    quote_price: Price
    quote_quantity: Quantity
    filled_quantity: Quantity
    queue_cleared: bool
    markouts: tuple[MarkoutObservation, ...]
    liquidation_depth: Quantity
    liquidation_cost_per_share: ExactDecimal
    residence_ns: int
    cancel_requests: int
    committed_capital: Money
    capital_capacity: Money

    def __post_init__(self) -> None:
        if not self.observation_id or not is_sha256_hex(self.source_sha256):
            raise ValueError("observation identity and source hash are required")
        if not ZERO < self.quote_price.value < ONE:
            raise ValueError("calibration quote requires an interior probability price")
        if self.outcome not in {"YES", "NO"} or not -3 <= self.offset_ticks <= 3:
            raise ValueError("unsupported calibration stratum")
        if not 1 <= self.size_multiplier <= 4:
            raise ValueError("unsupported calibration size multiplier")
        if not self.quote_at <= self.finished_at <= self.available_at:
            raise ValueError("invalid observation chronology")
        if (
            not self.quote_quantity.value > ZERO
            or self.filled_quantity.value > self.quote_quantity.value
        ):
            raise ValueError("filled quantity must be within positive quote quantity")
        if bool(self.filled_quantity.value) != (self.fill_at is not None):
            raise ValueError("fill time is required exactly when a fill exists")
        if self.fill_at is not None and not self.quote_at <= self.fill_at <= self.finished_at:
            raise ValueError("fill time is outside quote residence")
        if tuple(item.horizon_ns for item in self.markouts) != MARKOUT_HORIZONS_NS:
            raise ValueError("all four markout horizons, including missing entries, are required")
        for point in self.markouts:
            if point.observed_at is not None:
                if self.fill_at is None:
                    raise ValueError("unfilled quotes cannot have post-fill markouts")
                if point.observed_at.nanoseconds_since_epoch < (
                    self.fill_at.nanoseconds_since_epoch + point.horizon_ns
                ):
                    raise ValueError("markout precedes its declared horizon")
        if (
            self.residence_ns < 0
            or self.cancel_requests < 0
            or self.liquidation_cost_per_share < ZERO
        ):
            raise ValueError("negative calibration costs or durations")
        if not ZERO <= self.committed_capital.value <= self.capital_capacity.value:
            raise ValueError("invalid capital utilization")
        if self.capital_capacity.value <= ZERO:
            raise ValueError("capital capacity must be positive")


def _mean(values: tuple[ExactDecimal, ...], *, rounding: RoundingMode) -> ExactDecimal:
    return sum(values, ZERO).divide(ExactDecimal(len(values)), scale=18, rounding=rounding)


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    outcome: str
    offset_ticks: int
    size_multiplier: int
    samples: int
    filled_samples: int
    queue_clearing_frequency: ExactDecimal
    conservative_fill_fraction: ExactDecimal
    baseline_fill_fraction: ExactDecimal
    sensitivity_fill_fraction: ExactDecimal
    fill_probability: ExactDecimal
    # Each horizon stores (ns, conservative, baseline, sensitivity); missing is None.
    markouts: tuple[tuple[int, ExactDecimal | None, ExactDecimal | None, ExactDecimal | None], ...]
    adverse_selection_frequency: ExactDecimal | None
    minimum_liquidation_depth: Quantity
    maximum_liquidation_cost_per_share: ExactDecimal
    mean_residence_ns: int
    cancellation_churn: ExactDecimal
    capital_utilization: ExactDecimal
    reasons: tuple[str, ...]
    quote_quantity: Quantity
    minimum_quote_price: Price
    maximum_quote_price: Price


@dataclass(frozen=True, slots=True)
class CalibrationSnapshot:
    period: CalibrationPeriod
    profiles: tuple[CalibrationProfile, ...]
    observations_sha256: str
    minimum_samples: int
    maximum_markout_lateness_ns: int = 1_000_000_000
    estimator_version: str = ESTIMATOR_VERSION

    def __post_init__(self) -> None:
        if self.estimator_version != ESTIMATOR_VERSION:
            raise ValueError("unsupported calibration estimator version")
        if not is_sha256_hex(self.observations_sha256) or not 2 <= self.minimum_samples <= 10_000:
            raise ValueError("invalid calibration fingerprint or sample requirement")
        if self.maximum_markout_lateness_ns < 0:
            raise ValueError("invalid calibration markout lateness")
        keys = tuple(
            (profile.outcome, profile.offset_ticks, profile.size_multiplier)
            for profile in self.profiles
        )
        if len(keys) != len(set(keys)) or len(keys) > 56:
            raise ValueError("duplicate or excessive calibration strata")

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(self)

    def eligibility_reasons(self, *, at: UtcTimestamp, market_id: MarketId) -> tuple[str, ...]:
        reasons: list[str] = []
        if market_id != self.period.market_id:
            reasons.append("CALIBRATION_MARKET_MISMATCH")
        if not self.period.evaluation_start <= at < self.period.evaluation_end:
            reasons.append("CALIBRATION_OUTSIDE_EVALUATION_PERIOD")
        if not self.profiles:
            reasons.append("CALIBRATION_UNAVAILABLE")
        return tuple(reasons)

    def profile(
        self, outcome: str, offset_ticks: int, size_multiplier: int
    ) -> CalibrationProfile | None:
        return next(
            (
                profile
                for profile in self.profiles
                if (profile.outcome, profile.offset_ticks, profile.size_multiplier)
                == (outcome, offset_ticks, size_multiplier)
            ),
            None,
        )


def _profile(
    observations: tuple[CalibrationObservation, ...], minimum_samples: int
) -> CalibrationProfile:
    first = observations[0]
    fractions = tuple(
        item.filled_quantity.value.divide(
            item.quote_quantity.value, scale=18, rounding=RoundingMode.FLOOR
        )
        for item in observations
    )
    upper_fractions = tuple(
        item.filled_quantity.value.divide(
            item.quote_quantity.value, scale=18, rounding=RoundingMode.CEILING
        )
        for item in observations
    )
    filled = tuple(item for item in observations if item.fill_at is not None)
    reasons: list[str] = []
    if len(observations) < minimum_samples:
        reasons.append("INSUFFICIENT_PRIOR_TRIALS")
    if len(filled) < minimum_samples:
        reasons.append("INSUFFICIENT_PRIOR_FILLED_TRIALS")
    if any(item.quote_quantity != first.quote_quantity for item in observations):
        reasons.append("INCONSISTENT_CALIBRATION_QUOTE_SIZE")
    markouts: list[tuple[int, ExactDecimal | None, ExactDecimal | None, ExactDecimal | None]] = []
    adverse = 0
    for index, horizon in enumerate(MARKOUT_HORIZONS_NS):
        values = tuple(
            point.exit_price.value - item.quote_price.value
            for item in filled
            if (point := item.markouts[index]).exit_price is not None
        )
        if len(values) != len(filled) or len(values) < minimum_samples:
            markouts.append((horizon, None, None, None))
            reasons.append(f"MISSING_MARKOUT_{horizon}NS")
        else:
            markouts.append(
                (horizon, min(values), _mean(values, rounding=RoundingMode.FLOOR), max(values))
            )
    for item in filled:
        if any(
            point.exit_price is not None and point.exit_price.value < item.quote_price.value
            for point in item.markouts
        ):
            adverse += 1
    count = ExactDecimal(len(observations))
    return CalibrationProfile(
        outcome=first.outcome,
        offset_ticks=first.offset_ticks,
        size_multiplier=first.size_multiplier,
        samples=len(observations),
        filled_samples=len(filled),
        queue_clearing_frequency=ExactDecimal(
            sum(item.queue_cleared for item in observations)
        ).divide(count, scale=18, rounding=RoundingMode.FLOOR),
        conservative_fill_fraction=min(fractions),
        baseline_fill_fraction=_mean(fractions, rounding=RoundingMode.FLOOR),
        sensitivity_fill_fraction=max(upper_fractions),
        fill_probability=ExactDecimal(len(filled)).divide(
            count, scale=18, rounding=RoundingMode.FLOOR
        ),
        markouts=tuple(markouts),
        adverse_selection_frequency=(
            None
            if not filled or any(row[1] is None for row in markouts)
            else ExactDecimal(adverse).divide(
                ExactDecimal(len(filled)), scale=18, rounding=RoundingMode.CEILING
            )
        ),
        minimum_liquidation_depth=Quantity(
            min(item.liquidation_depth.value for item in observations)
        ),
        maximum_liquidation_cost_per_share=max(
            item.liquidation_cost_per_share for item in observations
        ),
        mean_residence_ns=sum(item.residence_ns for item in observations) // len(observations),
        cancellation_churn=ExactDecimal(sum(item.cancel_requests for item in observations)).divide(
            count, scale=18, rounding=RoundingMode.CEILING
        ),
        capital_utilization=_mean(
            tuple(
                item.committed_capital.value.divide(
                    item.capital_capacity.value, scale=18, rounding=RoundingMode.CEILING
                )
                for item in observations
            ),
            rounding=RoundingMode.CEILING,
        ),
        reasons=tuple(reasons),
        quote_quantity=first.quote_quantity,
        minimum_quote_price=Price(min(item.quote_price.value for item in observations)),
        maximum_quote_price=Price(max(item.quote_price.value for item in observations)),
    )


def fit_calibration(
    observations: tuple[CalibrationObservation, ...],
    period: CalibrationPeriod,
    *,
    minimum_samples: int = 2,
    maximum_markout_lateness_ns: int = 1_000_000_000,
) -> CalibrationSnapshot:
    """Freeze chronological prior observations; reject leakage, never filter it away."""
    if not 2 <= minimum_samples <= 10_000 or len(observations) > 100_000:
        raise ValueError("calibration sample bounds exceeded")
    if maximum_markout_lateness_ns < 0:
        raise ValueError("negative markout lateness")
    if len({item.observation_id for item in observations}) != len(observations):
        raise ValueError("duplicate calibration observation")
    groups: dict[tuple[str, int, int], list[CalibrationObservation]] = {}
    for item in observations:
        if item.market_id != period.market_id or item.source_sha256 not in period.source_hashes:
            raise ValueError("calibration source or market mismatch")
        if not period.start <= item.quote_at <= item.available_at <= period.end:
            raise ValueError("observation falls outside prior calibration period")
        if any(
            point.available_at is not None and point.available_at > period.end
            for point in item.markouts
        ):
            raise ValueError("future markout leaks across calibration cutoff")
        for point in item.markouts:
            if (
                point.observed_at is not None
                and item.fill_at is not None
                and (
                    point.observed_at.nanoseconds_since_epoch
                    - item.fill_at.nanoseconds_since_epoch
                    - point.horizon_ns
                    > maximum_markout_lateness_ns
                )
            ):
                raise ValueError("markout exceeds allowed horizon lateness")
        groups.setdefault((item.outcome, item.offset_ticks, item.size_multiplier), []).append(item)
    profiles = tuple(
        _profile(tuple(sorted(groups[key], key=lambda item: item.observation_id)), minimum_samples)
        for key in sorted(groups)
    )
    return CalibrationSnapshot(
        period=period,
        profiles=profiles,
        observations_sha256=canonical_sha256(
            tuple(sorted(observations, key=lambda item: item.observation_id))
        ),
        minimum_samples=minimum_samples,
        maximum_markout_lateness_ns=maximum_markout_lateness_ns,
    )
