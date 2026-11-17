"""Bounded conservative adaptive pair making over accepted canonical books."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.health import MarketStatus
from polymarket_pair_maker.core.ids import InstrumentId, MarketId
from polymarket_pair_maker.core.serialization import (
    CanonicalComposed,
    canonical_json_bytes,
    canonical_sha256,
)
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics.fees import FeeRules, canonical_fee_reasons, execution_fee
from polymarket_pair_maker.economics.rewards import RewardRules
from polymarket_pair_maker.market_data import (
    BinaryBookEligibilityAssessment,
    EligibilityDisposition,
)
from polymarket_pair_maker.reference import ReferenceBundle
from polymarket_pair_maker.reference.models import LiquidityRole
from polymarket_pair_maker.research.paper_calibration import (
    CalibrationProfile,
    CalibrationSnapshot,
    CalibrationSource,
)
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot

ZERO = ExactDecimal.zero()
ONE = ExactDecimal.one()
DEFAULT_HYSTERESIS = ExactDecimal(1, 4)
DEFAULT_CAPITAL_CHARGE = ExactDecimal(1, 4)
DEFAULT_TAIL_CHARGE = ExactDecimal(1, 3)
DEFAULT_DOWNSIDE = Money(ExactDecimal(25))
DEFAULT_EXIT_DEPTH = Quantity(ExactDecimal(5))
ZERO_MONEY = Money(ZERO)
OBJECTIVE_VERSION = "ppm-robust-execution-return-on-capital-v1"


class StrategyAction(StrEnum):
    NO_QUOTE = "NO_QUOTE"
    QUOTE_YES = "QUOTE_YES"
    QUOTE_NO = "QUOTE_NO"
    QUOTE_PAIR = "QUOTE_PAIR"


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    price_offsets_ticks: tuple[int, ...] = (0, -1, -2)
    size_multipliers: tuple[int, ...] = (1, 2)
    maximum_markets: int = 8
    maximum_book_age_ns: int = 5_000_000_000
    minimum_time_to_resolution_ns: int = 3_600_000_000_000
    maximum_reference_age_ns: int = 86_400_000_000_000
    minimum_replace_interval_ns: int = 5_000_000_000
    maximum_quote_age_ns: int = 60_000_000_000
    hysteresis_improvement: ExactDecimal = DEFAULT_HYSTERESIS
    capital_charge_rate: ExactDecimal = DEFAULT_CAPITAL_CHARGE
    tail_charge_rate: ExactDecimal = DEFAULT_TAIL_CHARGE
    maximum_downside: Money = DEFAULT_DOWNSIDE
    minimum_exit_depth: Quantity = DEFAULT_EXIT_DEPTH
    cache_capacity: int = 256

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (
                self.maximum_markets,
                self.maximum_book_age_ns,
                self.minimum_time_to_resolution_ns,
                self.maximum_reference_age_ns,
                self.minimum_replace_interval_ns,
                self.maximum_quote_age_ns,
                self.cache_capacity,
            )
        ):
            raise TypeError("strategy counts and time bounds must be integers")
        if not 1 <= len(self.price_offsets_ticks) <= 3 or len(set(self.price_offsets_ticks)) != len(
            self.price_offsets_ticks
        ):
            raise ValueError("price grid requires one to three unique offsets")
        if any(
            type(value) is not int or not -3 <= value <= 3 for value in self.price_offsets_ticks
        ):
            raise ValueError("price offsets must be integer ticks in [-3, 3]")
        if not 1 <= len(self.size_multipliers) <= 2 or len(set(self.size_multipliers)) != len(
            self.size_multipliers
        ):
            raise ValueError("size grid requires one or two unique multipliers")
        if any(type(value) is not int or not 1 <= value <= 4 for value in self.size_multipliers):
            raise ValueError("size multipliers must be integers in [1, 4]")
        if not 1 <= self.maximum_markets <= 32 or not 1 <= self.cache_capacity <= 4096:
            raise ValueError("market/cache bounds exceeded")
        if (
            any(
                value < 0
                for value in (
                    self.maximum_book_age_ns,
                    self.minimum_time_to_resolution_ns,
                    self.maximum_reference_age_ns,
                    self.minimum_replace_interval_ns,
                )
            )
            or self.maximum_quote_age_ns <= 0
        ):
            raise ValueError("invalid strategy time bounds")
        if self.minimum_replace_interval_ns > self.maximum_quote_age_ns:
            raise ValueError("replacement interval cannot exceed quote lifetime")
        if (
            any(
                value < ZERO
                for value in (
                    self.hysteresis_improvement,
                    self.capital_charge_rate,
                    self.tail_charge_rate,
                )
            )
            or self.maximum_downside.value <= ZERO
            or self.minimum_exit_depth.value <= ZERO
        ):
            raise ValueError("invalid economic charges or risk bounds")

    @property
    def maximum_candidates(self) -> int:
        per_leg = len(self.price_offsets_ticks) * len(self.size_multipliers)
        return 1 + 2 * per_leg + per_leg * per_leg


@dataclass(frozen=True, slots=True)
class MarketContext(CanonicalComposed):
    bundle: ReferenceBundle
    assessment: BinaryBookEligibilityAssessment
    yes_book: ImmutableOrderBookSnapshot
    no_book: ImmutableOrderBookSnapshot
    as_of: UtcTimestamp
    fee_rules: FeeRules | None
    reward_rules: RewardRules | None
    calibration: CalibrationSnapshot | None
    risk_capacity: Money
    event_risk_verified: bool = False
    catalyst_excluded: bool = False
    settlement_rules_verified: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (
                self.event_risk_verified,
                self.catalyst_excluded,
                self.settlement_rules_verified,
            )
        ):
            raise TypeError("strategy evidence flags must be booleans")
        if self.risk_capacity.value < ZERO:
            raise ValueError("risk capacity cannot be negative")


@dataclass(frozen=True, slots=True)
class QuoteLeg:
    outcome: str
    instrument_id: InstrumentId
    price: Price
    quantity: Quantity
    offset_ticks: int
    size_multiplier: int


@dataclass(frozen=True, slots=True)
class Candidate:
    identity: str
    market_id: MarketId
    action: StrategyAction
    legs: tuple[QuoteLeg, ...]
    capital: Money


@dataclass(frozen=True, slots=True)
class ScenarioEconomics:
    name: str
    expected_execution_margin: Money
    maker_fees: Money
    exit_fees: Money
    liquidation_costs: Money
    capital_charge: Money
    tail_charge: Money
    net: Money
    incentive_contribution: Money = ZERO_MONEY


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate: Candidate
    accepted: bool
    reasons: tuple[str, ...]
    scenarios: tuple[ScenarioEconomics, ...]
    conservative_net: Money
    score: ExactDecimal
    downside: Money


@dataclass(frozen=True, slots=True)
class ActiveQuote:
    candidate: Candidate
    admitted_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    action: StrategyAction
    candidate: Candidate | None
    reasons: tuple[str, ...]
    evaluations: tuple[CandidateEvaluation, ...]
    cancel_immediately: bool
    retained_existing: bool
    input_hash: str
    objective_version: str = OBJECTIVE_VERSION


@dataclass(frozen=True, slots=True)
class MarketRanking:
    selected: StrategyDecision | None
    decisions: tuple[tuple[MarketId, StrategyDecision], ...]


def _gate(context: MarketContext, config: StrategyConfig) -> tuple[str, ...]:
    reasons: list[str] = []
    bundle, assessment, at = context.bundle, context.assessment, context.as_of
    if assessment.disposition is not EligibilityDisposition.ELIGIBLE:
        reasons.append("DATA_ELIGIBILITY_BLOCKED")
        reasons.extend(
            f"DATA:{reason.value}"
            for validation in assessment.validation_results
            for reason in validation.reason_codes
        )
    if assessment.market_id != bundle.market.market_id or assessment.observed_at != at:
        reasons.append("ASSESSMENT_IDENTITY_OR_TIME_MISMATCH")
    expected_hashes = tuple(
        sorted(
            (
                (book.instrument_id, book.economic_content_sha256())
                for book in (context.yes_book, context.no_book)
            ),
            key=lambda item: item[0].value,
        )
    )
    if expected_hashes != assessment.book_state_hashes:
        reasons.append("ACCEPTED_BOOK_IDENTITY_MISMATCH")
    proof = prove_binary_complement(bundle)
    if not proof.is_proven or (
        proof.yes_instrument_id != context.yes_book.instrument_id
        or proof.no_instrument_id != context.no_book.instrument_id
    ):
        reasons.append("UNSUPPORTED_BINARY_SEMANTICS")
    if bundle.payout_per_set.value != ONE:
        reasons.append("UNSUPPORTED_PAYOUT_PRECISION")
    if bundle.market.status is not MarketStatus.OPEN:
        reasons.append("UNSUPPORTED_MARKET_STATUS")
    if not context.settlement_rules_verified:
        reasons.append("SETTLEMENT_RULES_UNVERIFIED")
    if not context.event_risk_verified or context.catalyst_excluded:
        reasons.append("EVENT_RISK_EXCLUSION")
    end = bundle.market.end_time
    if (
        end is None
        or end.nanoseconds_since_epoch - at.nanoseconds_since_epoch
        < config.minimum_time_to_resolution_ns
    ):
        reasons.append("TIME_TO_RESOLUTION_UNACCEPTABLE")
    tick = bundle.precision.tick_size
    if tick is None or tick <= ZERO or not ONE.is_integral_multiple_of(tick):
        reasons.append("UNSUPPORTED_TICK_PRECISION")
    if (
        bundle.precision.minimum_order_size is None
        or bundle.precision.minimum_order_size.value <= ZERO
    ):
        reasons.append("MINIMUM_ORDER_SIZE_UNKNOWN")
    for book in (context.yes_book, context.no_book):
        if not book.is_fresh(at, max_age_ns=config.maximum_book_age_ns):
            reasons.append("STALE_OR_UNHEALTHY_BOOK")
        if book.best_bid is None or book.best_ask is None:
            reasons.append("TWO_SIDED_DEPTH_REQUIRED")
        elif book.best_bid.price.value >= book.best_ask.price.value:
            reasons.append("CROSSED_BOOK")
        if (
            sum((level.quantity.value for level in book.bids), ZERO)
            < config.minimum_exit_depth.value
        ):
            reasons.append("INSUFFICIENT_EXECUTABLE_EXIT_DEPTH")
        if (
            book.market_version_id,
            book.precision_rule_version_id,
            book.cost_rule_set_version_id,
        ) != (
            bundle.market.version_id,
            bundle.precision.version_id,
            bundle.cost_rule_set.version_id,
        ):
            reasons.append("BOOK_REFERENCE_MISMATCH")
    if context.fee_rules is None:
        reasons.append("FEE_REFERENCE_UNAVAILABLE")
    else:
        reasons.extend(
            canonical_fee_reasons(bundle, context.fee_rules, at, config.maximum_reference_age_ns)
        )
    for reference in (bundle.market, bundle.precision, bundle.cost_rule_set, *bundle.instruments):
        times = reference.times
        if (
            times.observed_at > at
            or times.effective_from is None
            or times.effective_from > at
            or (times.effective_to is not None and at >= times.effective_to)
            or (times.superseded_at is not None and at >= times.superseded_at)
        ):
            reasons.append("CANONICAL_REFERENCE_NOT_CURRENT")
        if (
            at.nanoseconds_since_epoch - times.observed_at.nanoseconds_since_epoch
            > config.maximum_reference_age_ns
        ):
            reasons.append("STALE_CANONICAL_REFERENCE")
    if context.reward_rules is None:
        reasons.append("REWARD_REFERENCE_UNAVAILABLE")
    else:
        reasons.extend(context.reward_rules.eligibility_reasons(at))
        if context.reward_rules.reward_asset_id != bundle.payout_asset_id:
            reasons.append("REWARD_COLLATERAL_ASSET_MISMATCH")
    for rules in (context.fee_rules, context.reward_rules):
        if rules is not None:
            if rules.evidence.market_id != bundle.market.market_id:
                reasons.append("ECONOMIC_REFERENCE_MARKET_MISMATCH")
            if (
                at.nanoseconds_since_epoch
                - rules.evidence.times.observed_at.nanoseconds_since_epoch
                > config.maximum_reference_age_ns
            ):
                reasons.append("STALE_ECONOMIC_REFERENCE")
    if context.calibration is None:
        reasons.append("HISTORICAL_CALIBRATION_UNAVAILABLE")
    else:
        reasons.extend(
            context.calibration.eligibility_reasons(at=at, market_id=bundle.market.market_id)
        )
        synthetic = context.calibration.period.source_kind is CalibrationSource.SYNTHETIC
        if any(
            rules is not None and rules.evidence.synthetic != synthetic
            for rules in (context.fee_rules, context.reward_rules)
        ):
            reasons.append("SYNTHETIC_EMPIRICAL_SOURCE_MISMATCH")
    if context.risk_capacity.value <= ZERO:
        reasons.append("RISK_CAPACITY_EXHAUSTED")
    return tuple(dict.fromkeys(reasons))


def _candidate(market_id: MarketId, legs: tuple[QuoteLeg, ...]) -> Candidate:
    action = (
        StrategyAction.NO_QUOTE
        if not legs
        else StrategyAction.QUOTE_PAIR
        if len(legs) == 2
        else StrategyAction.QUOTE_YES
        if legs[0].outcome == "YES"
        else StrategyAction.QUOTE_NO
    )
    return Candidate(
        identity=canonical_sha256(("ppm-candidate-v1", market_id, action, legs)),
        market_id=market_id,
        action=action,
        legs=legs,
        capital=Money(sum((leg.price.value * leg.quantity.value for leg in legs), ZERO)),
    )


def generate_candidates(context: MarketContext, config: StrategyConfig) -> tuple[Candidate, ...]:
    """At most 49 candidates, including abstention; asymmetric pairs are explicit."""
    tick = context.bundle.precision.tick_size
    minimum = context.bundle.precision.minimum_order_size
    if tick is None or minimum is None or context.reward_rules is None:
        return (_candidate(context.bundle.market.market_id, ()),)
    size = max(minimum.value, context.reward_rules.minimum_size.value)
    grids: list[tuple[QuoteLeg, ...]] = []
    for outcome, book in (("YES", context.yes_book), ("NO", context.no_book)):
        legs: list[QuoteLeg] = []
        if book.best_bid is not None and book.best_ask is not None:
            anchor = book.best_bid.price.value.floor_div(tick)
            for offset in sorted(config.price_offsets_ticks):
                price = tick * ExactDecimal(anchor + offset)
                if not ZERO < price < min(ONE, book.best_ask.price.value):
                    continue
                legs.extend(
                    QuoteLeg(
                        outcome,
                        book.instrument_id,
                        Price(price),
                        Quantity(size * ExactDecimal(multiplier)),
                        offset,
                        multiplier,
                    )
                    for multiplier in sorted(config.size_multipliers)
                )
        grids.append(tuple(legs))
    market_id = context.bundle.market.market_id
    candidates = [_candidate(market_id, ())]
    candidates.extend(_candidate(market_id, (leg,)) for grid in grids for leg in grid)
    candidates.extend(_candidate(market_id, (yes, no)) for yes in grids[0] for no in grids[1])
    return tuple(candidates)


def _downside(candidate: Candidate) -> Money:
    """Worst settlement loss over no, single and joint fills, ignoring rebates."""
    if not candidate.legs:
        return Money(ZERO)
    single = max(leg.price.value * leg.quantity.value for leg in candidate.legs)
    joint = ZERO
    if len(candidate.legs) == 2:
        joint = candidate.capital.value - min(leg.quantity.value for leg in candidate.legs)
    return Money(max(single, joint))


def _evaluate(
    candidate: Candidate,
    context: MarketContext,
    config: StrategyConfig,
    exit_depth: tuple[ExactDecimal, ExactDecimal],
    liquidation_values: dict[tuple[InstrumentId, Quantity], ExactDecimal],
) -> CandidateEvaluation:
    downside = _downside(candidate)
    if not candidate.legs:
        return CandidateEvaluation(candidate, True, (), (), Money(ZERO), ZERO, downside)
    reasons: list[str] = []
    if candidate.capital.value > context.risk_capacity.value:
        reasons.append("INSUFFICIENT_COLLATERAL_CAPACITY")
    if downside.value > config.maximum_downside.value:
        reasons.append("TAIL_EXPOSURE_EXCEEDED")
    profiles: list[CalibrationProfile] = []
    for leg in candidate.legs:
        profile = (
            None
            if context.calibration is None
            else context.calibration.profile(leg.outcome, leg.offset_ticks, leg.size_multiplier)
        )
        if profile is None:
            reasons.append(
                f"MISSING_CALIBRATION_STRATUM:{leg.outcome}:{leg.offset_ticks}:{leg.size_multiplier}"
            )
        else:
            profiles.append(profile)
            reasons.extend(profile.reasons)
            if profile.quote_quantity != leg.quantity or not (
                profile.minimum_quote_price.value
                <= leg.price.value
                <= profile.maximum_quote_price.value
            ):
                reasons.append("CANDIDATE_OUTSIDE_CALIBRATION_SUPPORT")
            if profile.minimum_liquidation_depth.value < leg.quantity.value:
                reasons.append("CALIBRATED_EXIT_DEPTH_INSUFFICIENT")
            current_exit_shortfall = (
                leg.price.value * leg.quantity.value
                - liquidation_values[(leg.instrument_id, leg.quantity)]
            )
            if current_exit_shortfall > (
                profile.maximum_liquidation_cost_per_share * leg.quantity.value
            ):
                reasons.append("CURRENT_EXIT_COST_EXCEEDS_CALIBRATION")
        if exit_depth[0 if leg.outcome == "YES" else 1] < leg.quantity.value:
            reasons.append("CURRENT_EXIT_DEPTH_INSUFFICIENT")
    if reasons or context.fee_rules is None:
        return CandidateEvaluation(
            candidate, False, tuple(dict.fromkeys(reasons)), (), Money(ZERO), ZERO, downside
        )
    scenarios: list[ScenarioEconomics] = []
    for scenario_index, name in enumerate(
        ("STRICT_EMPIRICAL", "BASELINE_EMPIRICAL", "SENSITIVITY_EMPIRICAL"), start=1
    ):
        margin, maker, exit_fees, exit_costs = ZERO, ZERO, ZERO, ZERO
        for leg, profile in zip(candidate.legs, profiles, strict=True):
            values = tuple(row[1:][scenario_index - 1] for row in profile.markouts)
            if any(value is None for value in values):
                reasons.append("MARKOUT_UNAVAILABLE")
                continue
            per_share_margin = min(value for value in values if value is not None)
            fractions = (
                profile.conservative_fill_fraction,
                profile.baseline_fill_fraction,
                profile.sensitivity_fill_fraction,
            )
            # Negative markouts use maximum observed fill fraction in the strict
            # scenario, so low fill assumptions never hide adverse-selection loss.
            fraction = fractions[scenario_index - 1]
            if scenario_index == 1 and per_share_margin < ZERO:
                fraction = profile.sensitivity_fill_fraction
            margin += per_share_margin * leg.quantity.value * fraction
            maker_estimate = execution_fee(
                leg.price,
                leg.quantity,
                LiquidityRole.MAKER,
                context.fee_rules,
                context.as_of,
                context.bundle.payout_asset_id,
            )
            # p(1-p) <= 1/4: full-quantity exit at the fee-curve maximum
            # conservatively bounds fees even when a predicted fill is partial.
            exit_estimate = execution_fee(
                Price(ExactDecimal(5, 1)),
                leg.quantity,
                LiquidityRole.TAKER,
                context.fee_rules,
                context.as_of,
                context.bundle.payout_asset_id,
            )
            if maker_estimate.amount is None or exit_estimate.amount is None:
                reasons.append("EXECUTION_COST_UNAVAILABLE")
                continue
            maker += maker_estimate.amount.value
            exit_fees += exit_estimate.amount.value
            exit_costs += profile.maximum_liquidation_cost_per_share * leg.quantity.value
        capital_charge = candidate.capital.value * config.capital_charge_rate
        tail_charge = downside.value * config.tail_charge_rate
        net = margin - maker - exit_fees - exit_costs - capital_charge - tail_charge
        scenarios.append(
            ScenarioEconomics(
                name,
                Money(margin),
                Money(maker),
                Money(exit_fees),
                Money(exit_costs),
                Money(capital_charge),
                Money(tail_charge),
                Money(net),
            )
        )
    conservative = min(scenario.net.value for scenario in scenarios)
    if conservative <= ZERO:
        reasons.append("NONPOSITIVE_CONSERVATIVE_EXECUTION_ECONOMICS")
    score = conservative.divide(candidate.capital.value, scale=18, rounding=RoundingMode.FLOOR)
    return CandidateEvaluation(
        candidate,
        not reasons,
        tuple(dict.fromkeys(reasons)),
        tuple(scenarios),
        Money(conservative),
        score,
        downside,
    )


def _rank(evaluation: CandidateEvaluation) -> tuple[ExactDecimal, ExactDecimal, ExactDecimal, str]:
    return (
        -evaluation.score,
        evaluation.downside.value,
        evaluation.candidate.capital.value,
        evaluation.candidate.identity,
    )


class DecisionEngine:
    """Owns bounded derived-score cache; canonical state and risk remain external."""

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = StrategyConfig() if config is None else config
        self._cache: OrderedDict[tuple[Candidate, str], CandidateEvaluation] = OrderedDict()
        self._candidate_cache: OrderedDict[tuple[object, ...], tuple[Candidate, ...]] = (
            OrderedDict()
        )
        self.cache_hits = 0
        self.cache_misses = 0
        self.candidate_cache_hits = 0
        self.candidate_cache_misses = 0
        self.context_cache_hits = 0
        self.context_cache_misses = 0
        self._context_cache: tuple[MarketId, bytes, object, str] | None = None

    @property
    def cached_evaluations(self) -> int:
        return len(self._cache)

    @property
    def cached_candidate_grids(self) -> int:
        return len(self._candidate_cache)

    def _candidates(self, context: MarketContext) -> tuple[Candidate, ...]:
        # This pure grid depends only on these immutable values. Admission,
        # freshness and reference checks run before this method on every call.
        tick = context.bundle.precision.tick_size
        quantities = (
            context.bundle.precision.minimum_order_size,
            None if context.reward_rules is None else context.reward_rules.minimum_size,
        )
        if not (
            type(self.config.price_offsets_ticks) is tuple
            and type(self.config.size_multipliers) is tuple
            and type(context.bundle.market.market_id) is MarketId
            and type(tick) is ExactDecimal
            and type(tick.coefficient) is int
            and type(tick.scale) is int
            and all(
                type(quantity) is Quantity
                and type(quantity.value) is ExactDecimal
                and type(quantity.value.coefficient) is int
                and type(quantity.value.scale) is int
                for quantity in quantities
            )
            and all(book.canonical_cache_eligible() for book in (context.yes_book, context.no_book))
        ):
            self.candidate_cache_misses += 1
            return generate_candidates(context, self.config)
        key = (
            context.bundle.market.market_id,
            context.bundle.precision.tick_size,
            context.bundle.precision.minimum_order_size,
            None if context.reward_rules is None else context.reward_rules.minimum_size,
            self.config.price_offsets_ticks,
            self.config.size_multipliers,
            context.yes_book.instrument_id,
            None if context.yes_book.best_bid is None else context.yes_book.best_bid.price,
            None if context.yes_book.best_ask is None else context.yes_book.best_ask.price,
            context.no_book.instrument_id,
            None if context.no_book.best_bid is None else context.no_book.best_bid.price,
            None if context.no_book.best_ask is None else context.no_book.best_ask.price,
        )
        while len(self._candidate_cache) > self.config.maximum_markets:
            self._candidate_cache.popitem(last=False)
        cached = self._candidate_cache.get(key)
        if cached is not None:
            self.candidate_cache_hits += 1
            self._candidate_cache.move_to_end(key)
            return cached
        self.candidate_cache_misses += 1
        result = generate_candidates(context, self.config)
        self._candidate_cache[key] = result
        if len(self._candidate_cache) > self.config.maximum_markets:
            self._candidate_cache.popitem(last=False)
        return result

    def _cached_evaluation(
        self,
        candidate: Candidate,
        context: MarketContext,
        context_hash: str,
        exit_depth: tuple[ExactDecimal, ExactDecimal],
        liquidation_values: dict[tuple[InstrumentId, Quantity], ExactDecimal],
    ) -> CandidateEvaluation:
        # Validity, health, source time and age are ALWAYS gated before cache use.
        # Formula inputs include complete reference/calibration content, not only IDs.
        # Internal lookup uses full immutable candidate equality, not a second
        # serialization/hash of its already established canonical identity.
        key = (candidate, context_hash)
        if key in self._cache:
            self.cache_hits += 1
            self._cache.move_to_end(key)
            return self._cache[key]
        self.cache_misses += 1
        value = _evaluate(candidate, context, self.config, exit_depth, liquidation_values)
        self._cache[key] = value
        if len(self._cache) > self.config.cache_capacity:
            self._cache.popitem(last=False)
        return value

    def _evaluation_context_hash(self, context: MarketContext) -> str:
        sides = tuple((book.bids, book.asks) for book in (context.yes_book, context.no_book))
        inputs = (
            self.config,
            context.fee_rules,
            context.calibration,
            context.risk_capacity,
            sides,
        )
        # Non-book values are encoded afresh, including malformed mutable data.
        # Only audited immutable depth may be retained for full-value equality.
        eligible = (
            all(book.canonical_cache_eligible() for book in (context.yes_book, context.no_book))
            and sum(len(side) for pair in sides for side in pair) <= 16_384
        )
        if not eligible:
            self._context_cache = None
            self.context_cache_misses += 1
            return canonical_sha256(inputs)
        nonbook = canonical_json_bytes(inputs[:4])
        key = (context.bundle.market.market_id, nonbook, sides)
        previous = self._context_cache
        if previous is not None and previous[:3] == key:
            self.context_cache_hits += 1
            return previous[3]
        self.context_cache_misses += 1
        result = canonical_sha256(inputs)
        self._context_cache = (*key, result) if len(nonbook) <= 65_536 else None
        return result

    def evaluate(
        self, context: MarketContext, current: ActiveQuote | None = None
    ) -> StrategyDecision:
        input_hash = canonical_sha256((OBJECTIVE_VERSION, self.config, context, current))
        reasons = _gate(context, self.config)
        if reasons:
            return StrategyDecision(
                StrategyAction.NO_QUOTE, None, reasons, (), current is not None, False, input_hash
            )
        candidates = self._candidates(context)
        context_hash = self._evaluation_context_hash(context)
        exit_depth = (
            sum((level.quantity.value for level in context.yes_book.bids), ZERO),
            sum((level.quantity.value for level in context.no_book.bids), ZERO),
        )
        # At most two sizes per outcome: aggregate the relevant executable bid
        # sweeps once, then share them across all price and paired candidates.
        liquidation_values: dict[tuple[InstrumentId, Quantity], ExactDecimal] = {}
        for candidate in candidates:
            for leg in candidate.legs:
                key = (leg.instrument_id, leg.quantity)
                if key in liquidation_values:
                    continue
                book = context.yes_book if leg.outcome == "YES" else context.no_book
                remaining, gross = leg.quantity.value, ZERO
                for level in book.bids:
                    quantity = min(remaining, level.quantity.value)
                    gross += quantity * level.price.value
                    remaining -= quantity
                    if not remaining:
                        break
                liquidation_values[key] = gross
        evaluations = tuple(
            self._cached_evaluation(
                candidate, context, context_hash, exit_depth, liquidation_values
            )
            for candidate in candidates
        )
        accepted = sorted(
            (
                evaluation
                for evaluation in evaluations
                if evaluation.accepted and evaluation.candidate.legs
            ),
            key=_rank,
        )
        best = accepted[0] if accepted else None
        if current is not None:
            age = (
                context.as_of.nanoseconds_since_epoch - current.admitted_at.nanoseconds_since_epoch
            )
            prior = next(
                (
                    item
                    for item in evaluations
                    if item.candidate == current.candidate and item.accepted
                ),
                None,
            )
            if (
                current.candidate.market_id != context.bundle.market.market_id
                or not 0 <= age < self.config.maximum_quote_age_ns
                or prior is None
            ):
                return StrategyDecision(
                    StrategyAction.NO_QUOTE,
                    None,
                    ("ACTIVE_QUOTE_SAFETY_INVALIDATION",),
                    evaluations,
                    True,
                    False,
                    input_hash,
                )
            if best is not None and (
                best.candidate == prior.candidate
                or age < self.config.minimum_replace_interval_ns
                or best.score <= prior.score + self.config.hysteresis_improvement
            ):
                return StrategyDecision(
                    prior.candidate.action,
                    prior.candidate,
                    ("QUOTE_RETAINED_BY_HYSTERESIS",),
                    evaluations,
                    False,
                    True,
                    input_hash,
                )
        if best is None:
            return StrategyDecision(
                StrategyAction.NO_QUOTE,
                None,
                ("NO_DEFENSIBLE_POSITIVE_CANDIDATE",),
                evaluations,
                current is not None,
                False,
                input_hash,
            )
        return StrategyDecision(
            best.candidate.action,
            best.candidate,
            ("POSITIVE_CONSERVATIVE_EXECUTION_ECONOMICS",),
            evaluations,
            False,
            False,
            input_hash,
        )

    def evaluate_markets(
        self, contexts: tuple[MarketContext, ...], current: ActiveQuote | None = None
    ) -> MarketRanking:
        if len(contexts) > self.config.maximum_markets:
            raise ValueError("bounded market selection capacity exceeded")
        if len({context.bundle.market.market_id for context in contexts}) != len(contexts):
            raise ValueError("duplicate selection market")
        instruments = tuple(
            instrument.instrument_id
            for context in contexts
            for instrument in context.bundle.instruments
        )
        if len(set(instruments)) != len(instruments):
            raise ValueError("selection markets share conflicting instrument identities")
        if len({context.as_of for context in contexts}) > 1:
            raise ValueError("selection requires a common decision timestamp")
        decisions = tuple(
            (
                context.bundle.market.market_id,
                self.evaluate(
                    context,
                    current
                    if current is not None
                    and current.candidate.market_id == context.bundle.market.market_id
                    else None,
                ),
            )
            for context in sorted(contexts, key=lambda item: item.bundle.market.market_id.value)
        )
        if current is not None:
            active = next(
                (
                    decision
                    for market_id, decision in decisions
                    if market_id == current.candidate.market_id
                ),
                None,
            )
            # Single active market: cancellation/reconciliation precedes switching.
            if active is not None:
                return MarketRanking(active, decisions)
            return MarketRanking(
                StrategyDecision(
                    StrategyAction.NO_QUOTE,
                    None,
                    ("ACTIVE_MARKET_NOT_OBSERVED",),
                    (),
                    True,
                    False,
                    canonical_sha256((self.config, current, contexts)),
                ),
                decisions,
            )
        ranked: list[
            tuple[tuple[ExactDecimal, ExactDecimal, ExactDecimal, str], StrategyDecision]
        ] = []
        for _, decision in decisions:
            if decision.candidate is not None:
                evaluation = next(
                    item for item in decision.evaluations if item.candidate == decision.candidate
                )
                ranked.append((_rank(evaluation), decision))
        ranked.sort(key=lambda item: item[0])
        return MarketRanking(ranked[0][1] if ranked else None, decisions)
