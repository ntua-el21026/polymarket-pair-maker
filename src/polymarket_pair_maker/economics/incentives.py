"""UTC-day incentive estimates, separate from simulated cash and payments."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from polymarket_pair_maker.core.exact import ExactDecimal, Money, RoundingMode
from polymarket_pair_maker.core.ids import AssetId
from polymarket_pair_maker.core.time import UtcTimestamp

from .reference import ExactRatio, RuleEvidence

_DAY_NS = 86_400_000_000_000
_MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class RewardAllocation:
    """Explicit half-open UTC allocation interval; end-date semantics validated upstream."""

    evidence: RuleEvidence
    allocation_id: str
    asset_id: AssetId
    daily_rate: Money
    starts_at: UtcTimestamp
    ends_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not self.allocation_id or self.daily_rate.value < ExactDecimal.zero():
            raise ValueError("allocation requires identity and nonnegative daily amount")
        if self.starts_at >= self.ends_at or any(
            value.nanoseconds_since_epoch % _DAY_NS for value in (self.starts_at, self.ends_at)
        ):
            raise ValueError("allocation must span complete UTC days with an exclusive end")


@dataclass(frozen=True, slots=True)
class RewardSample:
    sample_id: str
    sampled_at: UtcTimestamp
    own_normalized_share: ExactRatio

    def __post_init__(self) -> None:
        if not self.sample_id or self.own_normalized_share.as_fraction() > 1:
            raise ValueError("sample requires identity and a normalized share in [0, 1]")


@dataclass(frozen=True, slots=True)
class DailyIncentiveEstimate:
    day_start: UtcTimestamp
    estimated_accrued: Money | None
    estimated_payout: Money | None
    conservative_contribution: Money
    sample_count: int
    reasons: tuple[str, ...]
    verified_payment: None = None


def payout_after_daily_threshold(earnings: Money, minimum: Money) -> Money:
    """Apply the threshold to an already established complete day's earnings.

    This pure function does not aggregate markets, carry balances across days,
    infer a wallet scope, or claim a payment has occurred.
    """

    if earnings.value < ExactDecimal.zero() or minimum.value <= ExactDecimal.zero():
        raise ValueError("daily earnings must be nonnegative and threshold positive")
    return earnings if earnings.value >= minimum.value else Money.parse(0)


def estimate_daily_reward(
    *,
    allocation: RewardAllocation,
    day_start: UtcTimestamp,
    samples: tuple[RewardSample, ...],
    expected_sample_count: int,
    sampling_schedule_verified: bool,
    payout_scope_verified: bool,
    payout_minimum: Money,
    decision_at: UtcTimestamp,
) -> DailyIncentiveEstimate:
    """Normalize a complete sampled epoch, never extrapolate one L2 snapshot.

    Each supplied sample must already have the official all-maker normalization.
    Public books do not establish this or random offsets. Missing sampling or
    payout aggregation evidence therefore returns explicitly unknown results.
    Identical duplicate samples are idempotent; contradictions fail closed.
    """

    if day_start.nanoseconds_since_epoch % _DAY_NS:
        raise ValueError("reward day must begin at midnight UTC")
    if type(expected_sample_count) is not int or not 1 <= expected_sample_count <= 1440:
        raise ValueError("a UTC reward epoch requires 1 to 1440 expected samples")
    if len(samples) > 2880:
        raise ValueError("daily reward sample input bound exceeded")
    day_end = UtcTimestamp(day_start.nanoseconds_since_epoch + _DAY_NS)
    unique: dict[str, RewardSample] = {}
    minutes: dict[int, str] = {}
    for sample in samples:
        old = unique.get(sample.sample_id)
        if old is not None:
            if old != sample:
                raise ValueError("contradictory duplicate reward sample")
            continue
        if not day_start <= sample.sampled_at < day_end or sample.sampled_at > decision_at:
            raise ValueError("reward samples cannot cross UTC days or use future observations")
        minute = sample.sampled_at.nanoseconds_since_epoch // _MINUTE_NS
        if minute in minutes:
            raise ValueError("reward epoch cannot contain multiple samples in one minute")
        minutes[minute] = sample.sample_id
        unique[sample.sample_id] = sample
    reasons = list(allocation.evidence.valid_at(day_start))
    if allocation.evidence.times.observed_at > decision_at:
        reasons.append("ALLOCATION_NOT_KNOWN_AT_DECISION")
    if not allocation.starts_at <= day_start < allocation.ends_at:
        reasons.append("NO_ACTIVE_DAILY_ALLOCATION")
    if decision_at < day_end:
        reasons.append("UTC_REWARD_DAY_INCOMPLETE")
    if not sampling_schedule_verified:
        reasons.append("RANDOM_SAMPLE_SCHEDULE_AND_NORMALIZATION_UNKNOWN")
    if len(unique) != expected_sample_count:
        reasons.append("REWARD_EPOCH_SAMPLES_INCOMPLETE")
    zero = Money.parse(0)
    if reasons:
        return DailyIncentiveEstimate(day_start, None, None, zero, len(unique), tuple(reasons))
    average = (
        sum((sample.own_normalized_share.as_fraction() for sample in unique.values()), Fraction(0))
        / expected_sample_count
    )
    amount = Money(
        ExactRatio.from_fraction(average * allocation.daily_rate.value.as_fraction()).to_decimal(
            scale=18, rounding=RoundingMode.FLOOR
        )
    )
    payout = payout_after_daily_threshold(amount, payout_minimum) if payout_scope_verified else None
    reasons = ["ESTIMATED_INCENTIVE_NOT_VERIFIED_PAYMENT"]
    if not payout_scope_verified:
        reasons.append("DAILY_PAYOUT_AGGREGATION_SCOPE_UNVERIFIED")
    return DailyIncentiveEstimate(day_start, amount, payout, zero, len(unique), tuple(reasons))


@dataclass(frozen=True, slots=True)
class IncentiveScenarios:
    """Explicit sensitivity estimates; unverifiable incentives contribute zero conservatively."""

    optimistic: Money | None
    baseline: Money | None
    conservative: Money
    verified_payment: None = None

    def __post_init__(self) -> None:
        zero = ExactDecimal.zero()
        if self.conservative.value != zero:
            raise ValueError("paper incentives cannot establish positive guaranteed income")
        if any(
            value is not None and value.value < zero for value in (self.optimistic, self.baseline)
        ):
            raise ValueError("incentive scenarios cannot have negative estimates")
        if (
            self.baseline is not None
            and self.optimistic is not None
            and self.baseline.value > self.optimistic.value
        ):
            raise ValueError("baseline incentive exceeds optimistic scenario")
