"""Versioned maker/taker fees and distinct estimated executed-maker rebates."""

from __future__ import annotations

from dataclasses import dataclass

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.ids import AssetId
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.reference.models import CostStatus, LiquidityRole, ReferenceBundle

from .reference import RuleEvidence


@dataclass(frozen=True, slots=True)
class FeeRules:
    """C * rate * p * (1-p), supported for explicit dated V2 references.

    The documentation does not establish a tie-rounding mode or reconcile its
    USDC/pUSD descriptions. A loader must retain unknowns; it must not select
    HALF_UP or equate assets solely because an older implementation did so.
    """

    evidence: RuleEvidence
    taker_rate: ExactDecimal
    maker_rate: ExactDecimal
    fee_asset_id: AssetId
    fee_quantum: Money
    rounding: RoundingMode | None
    sub_quantum_zero: bool

    def __post_init__(self) -> None:
        zero, one = ExactDecimal.zero(), ExactDecimal.one()
        if not zero <= self.taker_rate <= one:
            raise ValueError("taker fee rate must lie in [0, 1]")
        if self.maker_rate != zero:
            raise ValueError("nonzero maker execution fees require a new supported formula")
        if self.fee_quantum.value <= zero:
            raise ValueError("fee quantum must be positive")
        if type(self.sub_quantum_zero) is not bool:
            raise TypeError("sub_quantum_zero must be boolean")
        if self.rounding is not None and type(self.rounding) is not RoundingMode:
            raise TypeError("fee rounding must be an explicit supported rounding mode")
        if self.evidence.protocol_version.startswith("SYNTHETIC") and not self.evidence.synthetic:
            raise ValueError("synthetic fee protocol requires synthetic evidence")

    def eligibility_reasons(
        self, at: UtcTimestamp, collateral_asset_id: AssetId
    ) -> tuple[str, ...]:
        reasons = list(self.evidence.valid_at(at))
        if self.evidence.protocol_version not in {"v2", "SYNTHETIC-v2"}:
            reasons.append("UNSUPPORTED_FEE_PROTOCOL")
        if self.fee_asset_id != collateral_asset_id:
            reasons.append("FEE_COLLATERAL_ASSET_MISMATCH")
        if self.rounding is None and self.taker_rate:
            reasons.append("FEE_ROUNDING_UNVERIFIED")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class FeeEstimate:
    amount: Money | None
    raw_amount: Money | None
    rule_version: str
    asset_id: AssetId
    reasons: tuple[str, ...]


def canonical_fee_reasons(
    bundle: ReferenceBundle,
    rules: FeeRules,
    at: UtcTimestamp,
    maximum_reference_age_ns: int,
) -> tuple[str, ...]:
    """Reconcile an offline fee rule with the current accepted canonical reference.

    The same pure gate serves admission, active-order safety and taker exits.
    A still-effective offline rule cannot override changed canonical fee terms.
    Historical foundation evaluation and rounding behavior remain untouched.
    """

    if type(maximum_reference_age_ns) is not int or maximum_reference_age_ns < 0:
        raise ValueError("maximum fee reference age must be nonnegative integer nanoseconds")
    reasons = list(rules.eligibility_reasons(at, bundle.payout_asset_id))
    if rules.evidence.market_id != bundle.market.market_id:
        reasons.append("ECONOMIC_REFERENCE_MARKET_MISMATCH")
    canonical_cost = bundle.cost_rule_set
    if (
        canonical_cost.status is not CostStatus.DETERMINATE
        or canonical_cost.rate is None
        or canonical_cost.rate.value != rules.taker_rate
        or canonical_cost.fee_asset_id != rules.fee_asset_id
        or canonical_cost.fee_quantum != rules.fee_quantum
        or canonical_cost.taker_only is not True
        or (bool(rules.taker_rate) and canonical_cost.exponent != 1)
    ):
        reasons.append("CANONICAL_FEE_REFERENCE_CONTRADICTION")
    if set(canonical_cost.applicable_instrument_ids) != {
        instrument.instrument_id for instrument in bundle.instruments
    }:
        reasons.append("CANONICAL_FEE_INSTRUMENT_SCOPE_MISMATCH")
    times = canonical_cost.times
    if (
        times.observed_at > at
        or times.effective_from is None
        or times.effective_from > at
        or (times.effective_to is not None and at >= times.effective_to)
        or (times.superseded_at is not None and at >= times.superseded_at)
    ):
        reasons.append("CANONICAL_REFERENCE_NOT_CURRENT")
    if at.nanoseconds_since_epoch - times.observed_at.nanoseconds_since_epoch > (
        maximum_reference_age_ns
    ):
        reasons.append("STALE_CANONICAL_REFERENCE")
    if at.nanoseconds_since_epoch - rules.evidence.times.observed_at.nanoseconds_since_epoch > (
        maximum_reference_age_ns
    ):
        reasons.append("STALE_ECONOMIC_REFERENCE")
    return tuple(dict.fromkeys(reasons))


def fee_equivalent(price: Price, quantity: Quantity, rate: ExactDecimal) -> Money:
    """Exact, unrounded fee-curve weight; not a maker charge or a rebate."""

    if price.value > ExactDecimal.one():
        raise ValueError("probability price exceeds one")
    if not ExactDecimal.zero() <= rate <= ExactDecimal.one():
        raise ValueError("fee rate must lie in [0, 1]")
    return Money(quantity.value * rate * price.value * (ExactDecimal.one() - price.value))


def execution_fee(
    price: Price,
    quantity: Quantity,
    role: LiquidityRole,
    rules: FeeRules,
    at: UtcTimestamp,
    collateral_asset_id: AssetId,
) -> FeeEstimate:
    reasons = list(rules.eligibility_reasons(at, collateral_asset_id))
    if role is LiquidityRole.MAKER and "FEE_ROUNDING_UNVERIFIED" in reasons:
        reasons.remove("FEE_ROUNDING_UNVERIFIED")
    if role is LiquidityRole.UNKNOWN:
        reasons.append("EXECUTION_ROLE_UNKNOWN")
    if price.value > ExactDecimal.one():
        reasons.append("PROBABILITY_PRICE_OUT_OF_RANGE")
    if reasons:
        return FeeEstimate(None, None, rules.evidence.version, rules.fee_asset_id, tuple(reasons))
    rate = rules.maker_rate if role is LiquidityRole.MAKER else rules.taker_rate
    raw = fee_equivalent(price, quantity, rate)
    if not raw.value or (rules.sub_quantum_zero and raw.value < rules.fee_quantum.value):
        rounded = ExactDecimal.zero()
    elif rules.rounding is None:
        # A fee-free reference needs no arbitrary rounding convention.
        raise ValueError("nonzero fee cannot be rounded without a validated rule")
    else:
        rounded = raw.value.quantize(rules.fee_quantum.value, rounding=rules.rounding)
    return FeeEstimate(Money(rounded), raw, rules.evidence.version, rules.fee_asset_id, ())


@dataclass(frozen=True, slots=True)
class RebateEstimate:
    estimated_accrued: Money | None
    conservative_contribution: Money
    reasons: tuple[str, ...]
    verified_payment: None = None


def estimate_maker_rebate(
    *,
    own_executed_fee_equivalent: Money,
    total_executed_fee_equivalent: Money | None,
    rebate_pool: Money | None,
    rules: FeeRules,
    at: UtcTimestamp,
    collateral_asset_id: AssetId,
) -> RebateEstimate:
    """Per-market daily allocation, explicitly excluding unfilled liquidity.

    The caller supplies fee-equivalent executed volume, not resting notional.
    The conservative contribution is zero because a paper fill is not an
    entitlement to a venue payment. Daily threshold application is separate.
    """

    zero = Money.parse(0)
    if own_executed_fee_equivalent.value < zero.value:
        raise ValueError("executed fee-equivalent cannot be negative")
    if total_executed_fee_equivalent is not None and (
        total_executed_fee_equivalent.value < own_executed_fee_equivalent.value
    ):
        raise ValueError("own executed volume cannot exceed total executed volume")
    if rebate_pool is not None and rebate_pool.value < zero.value:
        raise ValueError("rebate pool cannot be negative")
    reasons = rules.eligibility_reasons(at, collateral_asset_id)
    if reasons:
        return RebateEstimate(None, zero, reasons)
    if not rules.taker_rate or not own_executed_fee_equivalent.value:
        return RebateEstimate(zero, zero, ())
    if total_executed_fee_equivalent is None or rebate_pool is None:
        return RebateEstimate(None, zero, ("DAILY_REBATE_POOL_OR_COMPETITION_UNKNOWN",))
    amount = (own_executed_fee_equivalent.value * rebate_pool.value).divide(
        total_executed_fee_equivalent.value, scale=18, rounding=RoundingMode.FLOOR
    )
    return RebateEstimate(Money(amount), zero, ("SIMULATED_EXECUTION_NOT_PAYMENT",))
