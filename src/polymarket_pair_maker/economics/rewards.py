"""Exact complementary-side reward mathematics with explicit missing evidence.

Official documentation checked 2026-11-17 names a size-cutoff-adjusted midpoint
without defining its reconstruction, aggregation or rounding. The only local
reconstruction convention below is explicitly SYNTHETIC. Real-market references
must leave it unavailable until an authoritative reproducible contract exists.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.ids import AssetId
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.state.order_books import BookHealth, ImmutableOrderBookSnapshot

from .reference import ExactRatio, RuleEvidence

_ZERO = ExactDecimal.zero()
_ONE = ExactDecimal.one()
_ZERO_MONEY = Money(_ZERO)


class MidpointConvention(StrEnum):
    SYNTHETIC_COMPLEMENTARY_CUMULATIVE_DEPTH = "SYNTHETIC_COMPLEMENTARY_CUMULATIVE_DEPTH"


@dataclass(frozen=True, slots=True)
class RewardRules:
    evidence: RuleEvidence
    minimum_size: Quantity
    maximum_spread_cents: ExactDecimal
    multiplier: ExactDecimal | None
    single_sided_divisor: ExactDecimal
    minimum_age_ns: int | None
    midpoint_convention: MidpointConvention | None
    reward_asset_id: AssetId
    daily_payout_minimum: Money

    def __post_init__(self) -> None:
        if self.minimum_size.value <= _ZERO:
            raise ValueError("qualifying reward minimum size must be positive")
        if not _ZERO < self.maximum_spread_cents <= ExactDecimal(100):
            raise ValueError("maximum spread must be expressed in cents in (0, 100]")
        if self.multiplier is not None and self.multiplier <= _ZERO:
            raise ValueError("reward multiplier must be positive when known")
        if self.single_sided_divisor < _ONE:
            raise ValueError("single-sided divisor must be at least one")
        if self.minimum_age_ns is not None and (
            type(self.minimum_age_ns) is not int or self.minimum_age_ns < 0
        ):
            raise ValueError("minimum order age must be nonnegative integer nanoseconds")
        if self.daily_payout_minimum.value <= _ZERO:
            raise ValueError("daily payout minimum must be positive")
        if self.midpoint_convention is not None and not self.evidence.synthetic:
            raise ValueError("synthetic midpoint reconstruction requires synthetic evidence")
        if (
            self.midpoint_convention is not None
            and type(self.midpoint_convention) is not MidpointConvention
        ):
            raise TypeError("midpoint convention must be a supported explicit convention")

    def eligibility_reasons(self, at: UtcTimestamp) -> tuple[str, ...]:
        reasons = list(self.evidence.valid_at(at))
        if self.multiplier is None:
            reasons.append("REWARD_MULTIPLIER_UNKNOWN")
        if self.minimum_age_ns is None:
            reasons.append("REWARD_MINIMUM_AGE_UNKNOWN")
        if self.midpoint_convention is None:
            reasons.append("SIZE_CUTOFF_MIDPOINT_CONVENTION_UNVERIFIED")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class RewardOrder:
    order_id: str
    outcome: str
    side: str
    price: Price
    quantity: Quantity
    resting_since: UtcTimestamp | None

    def __post_init__(self) -> None:
        if not self.order_id or self.outcome not in {"YES", "NO"}:
            raise ValueError("reward order requires identity and ordinary binary outcome")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("reward order side must be BUY or SELL")
        if self.price.value > _ONE or self.quantity.value <= _ZERO:
            raise ValueError("reward order requires probability price and positive quantity")

    @property
    def economic_side(self) -> str:
        return "A" if (self.outcome == "YES") == (self.side == "BUY") else "B"

    @property
    def yes_price(self) -> ExactDecimal:
        return self.price.value if self.outcome == "YES" else _ONE - self.price.value


@dataclass(frozen=True, slots=True)
class MidpointEstimate:
    midpoint: Price | None
    cutoff_bid: Price | None
    cutoff_ask: Price | None
    reasons: tuple[str, ...]


def order_score(
    *,
    maximum_spread_cents: ExactDecimal,
    distance_cents: ExactDecimal,
    quantity: Quantity,
    multiplier: ExactDecimal,
) -> ExactRatio:
    """S(v,s) times shares, exactly; at or beyond the spread boundary is zero."""

    if maximum_spread_cents <= _ZERO or distance_cents < _ZERO or multiplier <= _ZERO:
        raise ValueError("score requires positive max spread/multiplier and nonnegative distance")
    if distance_cents >= maximum_spread_cents:
        return ExactRatio(0)
    relative = (maximum_spread_cents - distance_cents).as_fraction() / (
        maximum_spread_cents.as_fraction()
    )
    return ExactRatio.from_fraction(
        relative * relative * quantity.value.as_fraction() * multiplier.as_fraction()
    )


def maker_score(
    side_a: ExactRatio,
    side_b: ExactRatio,
    midpoint: Price,
    single_sided_divisor: ExactDecimal,
) -> ExactRatio:
    if midpoint.value > _ONE or single_sided_divisor < _ONE:
        raise ValueError("invalid midpoint or single-sided divisor")
    a, b = side_a.as_fraction(), side_b.as_fraction()
    score = min(a, b)
    if ExactDecimal.parse("0.10") <= midpoint.value <= ExactDecimal.parse("0.90"):
        score = max(score, max(a, b) / single_sided_divisor.as_fraction())
    return ExactRatio.from_fraction(score)


def reward_minimum_age_ns(
    *,
    category: str,
    taker_delay_ns: int | None,
    scheduled_game_start: UtcTimestamp | None,
    at: UtcTimestamp,
) -> int | None:
    """Official age exceptions; absent delay/start evidence remains unknown."""

    if taker_delay_ns is not None and (type(taker_delay_ns) is not int or taker_delay_ns < 0):
        raise ValueError("taker delay must be nonnegative integer nanoseconds")
    if category in {"crypto", "finance"}:
        return 0
    if taker_delay_ns is None:
        return None
    if taker_delay_ns == 0:
        return 0
    if category == "sports":
        if scheduled_game_start is None:
            return None
        if at < scheduled_game_start:
            return 0
    return taker_delay_ns


def _economic_levels(
    yes_book: ImmutableOrderBookSnapshot, no_book: ImmutableOrderBookSnapshot
) -> tuple[tuple[str, ExactDecimal, Quantity], ...]:
    return (
        *(("A", level.price.value, level.quantity) for level in yes_book.bids),
        *(("B", level.price.value, level.quantity) for level in yes_book.asks),
        *(("B", _ONE - level.price.value, level.quantity) for level in no_book.bids),
        *(("A", _ONE - level.price.value, level.quantity) for level in no_book.asks),
    )


def _cutoff_price(
    levels: tuple[tuple[str, ExactDecimal, Quantity], ...],
    side: str,
    minimum: Quantity,
) -> Price | None:
    aggregated: dict[ExactDecimal, ExactDecimal] = {}
    for item_side, price, quantity in levels:
        if side == item_side:
            aggregated[price] = aggregated.get(price, _ZERO) + quantity.value
    total = _ZERO
    for price in sorted(aggregated, reverse=side == "A"):
        total += aggregated[price]
        if total >= minimum.value:
            return Price(price)
    return None


def size_cutoff_adjusted_midpoint(
    yes_book: ImmutableOrderBookSnapshot,
    no_book: ImmutableOrderBookSnapshot,
    own_orders: tuple[RewardOrder, ...],
    rules: RewardRules,
    decision_at: UtcTimestamp,
) -> MidpointEstimate:
    """Reconstruct only under the explicitly declared synthetic depth contract.

    The fixture convention aggregates complementary economic sides, walks best
    to worst until cumulative size reaches the cutoff, and averages those prices
    exactly without tick rounding. Hypothetical quotes participate in this walk,
    but never mutate the canonical observations. This is NOT an asserted venue
    algorithm; real references return an unavailable result.
    """

    reasons = list(rules.eligibility_reasons(decision_at))
    if len({order.order_id for order in own_orders}) != len(own_orders):
        reasons.append("DUPLICATE_HYPOTHETICAL_ORDER_ID")
    if len(own_orders) > 64:
        reasons.append("HYPOTHETICAL_ORDER_BOUND_EXCEEDED")
    if yes_book.instrument_id == no_book.instrument_id:
        reasons.append("DUPLICATE_COMPLEMENTARY_BOOK")
    for book in (yes_book, no_book):
        if book.market_id != rules.evidence.market_id:
            reasons.append("REWARD_RULE_MARKET_MISMATCH")
        if book.health is not BookHealth.HEALTHY:
            reasons.append("UNHEALTHY_REWARD_BOOK")
        if book.last_valid_update_time is None or book.last_valid_update_time > decision_at:
            reasons.append("REWARD_BOOK_TIME_INVALID")
        if any(
            level.price.value > _ONE or level.quantity.value <= _ZERO
            for level in (*book.bids, *book.asks)
        ):
            reasons.append("INVALID_REWARD_DEPTH")
        if (
            book.bids
            and book.asks
            and max(level.price.value for level in book.bids)
            >= min(level.price.value for level in book.asks)
        ):
            reasons.append("CROSSED_REWARD_BOOK")
    if reasons:
        return MidpointEstimate(None, None, None, tuple(dict.fromkeys(reasons)))
    levels = _economic_levels(yes_book, no_book) + tuple(
        (order.economic_side, order.yes_price, order.quantity) for order in own_orders
    )
    bid = _cutoff_price(levels, "A", rules.minimum_size)
    ask = _cutoff_price(levels, "B", rules.minimum_size)
    if bid is None or ask is None:
        return MidpointEstimate(None, bid, ask, ("INSUFFICIENT_SIZE_CUTOFF_DEPTH",))
    if bid.value >= ask.value:
        return MidpointEstimate(None, bid, ask, ("CROSSED_ADJUSTED_ECONOMIC_SIDES",))
    return MidpointEstimate(Price((bid.value + ask.value) / ExactDecimal(2)), bid, ask, ())


@dataclass(frozen=True, slots=True)
class SideScores:
    side_a: ExactRatio | None
    side_b: ExactRatio | None
    score: ExactRatio | None
    reasons: tuple[str, ...]


def score_own_orders(
    orders: tuple[RewardOrder, ...],
    *,
    midpoint: Price,
    rules: RewardRules,
    decision_at: UtcTimestamp,
) -> SideScores:
    """Side A = YES bids + NO asks; side B = YES asks + NO bids."""

    reasons = list(rules.evidence.valid_at(decision_at))
    if len({order.order_id for order in orders}) != len(orders):
        reasons.append("DUPLICATE_REWARD_ORDER")
    if rules.multiplier is None or rules.minimum_age_ns is None:
        reasons.append("REWARD_MULTIPLIER_OR_AGE_UNKNOWN")
    if any(order.resting_since is None for order in orders):
        reasons.append("OWN_ORDER_AGE_UNKNOWN")
    if reasons or rules.multiplier is None or rules.minimum_age_ns is None:
        return SideScores(None, None, None, tuple(reasons))
    a, b = Fraction(0), Fraction(0)
    for order in orders:
        if order.quantity.value < rules.minimum_size.value:
            continue
        if order.resting_since is None:
            raise ValueError("own order age must be explicit")
        age_ns = decision_at.nanoseconds_since_epoch - order.resting_since.nanoseconds_since_epoch
        if age_ns < rules.minimum_age_ns:
            continue
        distance = abs(order.yes_price - midpoint.value) * ExactDecimal(100)
        value = order_score(
            maximum_spread_cents=rules.maximum_spread_cents,
            distance_cents=distance,
            quantity=order.quantity,
            multiplier=rules.multiplier,
        ).as_fraction()
        if order.economic_side == "A":
            a += value
        else:
            b += value
    a_ratio, b_ratio = ExactRatio.from_fraction(a), ExactRatio.from_fraction(b)
    return SideScores(
        a_ratio,
        b_ratio,
        maker_score(a_ratio, b_ratio, midpoint, rules.single_sided_divisor),
        (),
    )


@dataclass(frozen=True, slots=True)
class ShareBounds:
    lower: ExactRatio | None
    upper: ExactRatio | None
    reasons: tuple[str, ...]


def instantaneous_share_bounds(
    *,
    own_score: ExactRatio,
    competitor_mass_upper: ExactRatio | None,
    competitor_mass_lower: ExactRatio | None,
    midpoint: Price,
    single_sided_divisor: ExactDecimal,
    complete_competitor_coverage: bool,
    common_reference_snapshot: bool,
    excludes_own_liquidity: bool,
) -> ShareBounds:
    """The proven c=3 middle-range bound, never a daily payout prediction.

    Wallet identity is unnecessary only when additive eligible side-score mass
    bounds are valid. A public truncated L2 snapshot is not complete coverage.
    """

    reasons: list[str] = []
    if single_sided_divisor != ExactDecimal(3):
        reasons.append("SHARE_BOUND_REQUIRES_C_EQUALS_THREE")
    if not ExactDecimal.parse("0.10") <= midpoint.value <= ExactDecimal.parse("0.90"):
        reasons.append("SHARE_BOUND_REQUIRES_MIDDLE_RANGE")
    if not complete_competitor_coverage or competitor_mass_upper is None:
        reasons.append("COMPETITOR_SCORE_UPPER_BOUND_UNAVAILABLE")
    if not common_reference_snapshot:
        reasons.append("SHARE_BOUND_REFERENCE_MISMATCH")
    if not excludes_own_liquidity:
        reasons.append("COMPETITOR_MASS_INCLUDES_OWN_QUOTES")
    if reasons or competitor_mass_upper is None:
        return ShareBounds(None, None, tuple(reasons))
    upper_mass = competitor_mass_upper.as_fraction()
    lower_mass = (
        Fraction(0) if competitor_mass_lower is None else (competitor_mass_lower.as_fraction())
    )
    if lower_mass > upper_mass:
        raise ValueError("competitor lower score mass exceeds upper bound")
    q = own_score.as_fraction()
    if not q:
        if not upper_mass:
            return ShareBounds(None, None, ("ALL_ZERO_SAMPLING_STATE",))
        return ShareBounds(ExactRatio(0), ExactRatio(0), ())
    return ShareBounds(
        ExactRatio.from_fraction(q / (q + upper_mass / 2)),
        ExactRatio.from_fraction(q / (q + lower_mass / 4)),
        (),
    )


@dataclass(frozen=True, slots=True)
class RewardEstimate:
    adjusted_midpoint: Price | None
    side_a: ExactRatio | None
    side_b: ExactRatio | None
    own_score: ExactRatio | None
    competitor_side_mass_upper: ExactRatio | None
    conservative_share: ExactDecimal | None
    optimistic_share: ExactDecimal | None
    reasons: tuple[str, ...]
    rule_version: str
    conservative_daily_income: Money = _ZERO_MONEY
    verified_payment: None = None


def estimate_reward_snapshot(
    yes_book: ImmutableOrderBookSnapshot,
    no_book: ImmutableOrderBookSnapshot,
    own_orders: tuple[RewardOrder, ...],
    rules: RewardRules,
    decision_at: UtcTimestamp,
    *,
    depth_complete: bool = False,
) -> RewardEstimate:
    return _evaluate_reward_snapshot(
        yes_book,
        no_book,
        own_orders,
        rules,
        decision_at,
        depth_complete=depth_complete,
        mass_evaluator=_uncached_competitor_mass,
    )


def _uncached_competitor_mass(
    yes_book: ImmutableOrderBookSnapshot,
    no_book: ImmutableOrderBookSnapshot,
    midpoint: Price,
    rules: RewardRules,
) -> Fraction:
    if rules.multiplier is None:
        raise ValueError("competitor scores require a known multiplier")
    mass = Fraction(0)
    for _, price, quantity in _economic_levels(yes_book, no_book):
        mass += order_score(
            maximum_spread_cents=rules.maximum_spread_cents,
            distance_cents=abs(price - midpoint.value) * ExactDecimal(100),
            quantity=quantity,
            multiplier=rules.multiplier,
        ).as_fraction()
    return mass


type _MassEvaluator = Callable[
    [ImmutableOrderBookSnapshot, ImmutableOrderBookSnapshot, Price, RewardRules], Fraction
]


def _evaluate_reward_snapshot(
    yes_book: ImmutableOrderBookSnapshot,
    no_book: ImmutableOrderBookSnapshot,
    own_orders: tuple[RewardOrder, ...],
    rules: RewardRules,
    decision_at: UtcTimestamp,
    *,
    depth_complete: bool,
    mass_evaluator: _MassEvaluator,
) -> RewardEstimate:
    midpoint = size_cutoff_adjusted_midpoint(yes_book, no_book, own_orders, rules, decision_at)
    if midpoint.midpoint is None:
        return RewardEstimate(
            None, None, None, None, None, None, None, midpoint.reasons, rules.evidence.version
        )
    own = score_own_orders(
        own_orders, midpoint=midpoint.midpoint, rules=rules, decision_at=decision_at
    )
    if own.score is None or rules.multiplier is None:
        return RewardEstimate(
            midpoint.midpoint,
            own.side_a,
            own.side_b,
            own.score,
            None,
            None,
            None,
            own.reasons,
            rules.evidence.version,
        )
    # Sum observed liquidity only. Treat every observed share as potentially
    # eligible to obtain a score-mass upper bound IF coverage is established.
    # Aggregate L2 cannot identify individual size/age eligibility or wallets.
    mass = mass_evaluator(yes_book, no_book, midpoint.midpoint, rules)
    upper = ExactRatio.from_fraction(mass) if depth_complete else None
    bounds = instantaneous_share_bounds(
        own_score=own.score,
        competitor_mass_upper=upper,
        competitor_mass_lower=None,
        midpoint=midpoint.midpoint,
        single_sided_divisor=rules.single_sided_divisor,
        complete_competitor_coverage=depth_complete,
        common_reference_snapshot=True,
        excludes_own_liquidity=True,
    )
    reasons = (*bounds.reasons, "COMPETITOR_WALLET_AND_AGE_UNKNOWN", "RANDOM_SAMPLE_TIMES_UNKNOWN")
    return RewardEstimate(
        midpoint.midpoint,
        own.side_a,
        own.side_b,
        own.score,
        upper,
        None if bounds.lower is None else bounds.lower.to_decimal(rounding=RoundingMode.FLOOR),
        None if bounds.upper is None else bounds.upper.to_decimal(rounding=RoundingMode.CEILING),
        reasons,
        rules.evidence.version,
    )


class RewardScoreCache:
    """Bounded exact-result memoization for immutable accepted book views.

    Reusing the same immutable snapshots avoids all depth rescans. New snapshots
    invalidate the entry without trusting caller-supplied hashes. Entries retain
    those objects, preventing Python object-ID reuse from causing a false hit.
    Rule validity and order-age eligibility are checked at every decision time;
    crossing an eligibility boundary invalidates the memoized score. Canonical
    book freshness and binary semantics remain the caller's prerequisite gate.

    On changed snapshots, competitor score mass updates by exact contribution
    deltas for changed economic price levels. Unchanged prices/quantities do not
    repeat quadratic scoring. Midpoint or rule changes invalidate all level
    contributions. Comparing accepted immutable level tuples does not create a
    second canonical book owner. Oversized books bypass this optional derived
    cache and retain the identical pure result without unbounded cache growth.
    """

    def __init__(self, capacity: int = 128, *, maximum_level_entries: int = 4096) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("reward cache capacity must be an integer in [1, 4096]")
        self.capacity = capacity
        if type(maximum_level_entries) is not int or not 1 <= maximum_level_entries <= 65_536:
            raise ValueError("reward level cache bound must be an integer in [1, 65536]")
        self.maximum_level_entries = maximum_level_entries
        self.hits = 0
        self.misses = 0
        self.cached_level_hits = 0
        self.cached_level_misses = 0
        self.level_invalidations = 0
        self._level_reference: str | None = None
        self._level_scores: dict[tuple[str, ExactDecimal], tuple[ExactDecimal, Fraction]] = {}
        self._competitor_mass = Fraction(0)
        self._entries: OrderedDict[
            tuple[int, int, str],
            tuple[ImmutableOrderBookSnapshot, ImmutableOrderBookSnapshot, RewardEstimate],
        ] = OrderedDict()

    @property
    def cached_entries(self) -> int:
        return len(self._entries)

    @property
    def cached_level_entries(self) -> int:
        return len(self._level_scores)

    def _incremental_competitor_mass(
        self,
        yes_book: ImmutableOrderBookSnapshot,
        no_book: ImmutableOrderBookSnapshot,
        midpoint: Price,
        rules: RewardRules,
    ) -> Fraction:
        if rules.multiplier is None:
            raise ValueError("competitor scores require a known multiplier")
        reference = canonical_sha256(
            (yes_book.instrument_id, no_book.instrument_id, midpoint, rules)
        )
        count = sum(
            len(levels)
            for levels in (
                yes_book.bids,
                yes_book.asks,
                no_book.bids,
                no_book.asks,
            )
        )
        if reference != self._level_reference or count > self.maximum_level_entries:
            if self._level_reference is not None:
                self.level_invalidations += 1
            self._level_scores.clear()
            self._competitor_mass = Fraction(0)
            self._level_reference = reference
        if count > self.maximum_level_entries:
            self.cached_level_misses += count
            return _uncached_competitor_mass(yes_book, no_book, midpoint, rules)
        quantities: dict[tuple[str, ExactDecimal], ExactDecimal] = {}
        for side, price, quantity in _economic_levels(yes_book, no_book):
            key = (side, price)
            quantities[key] = quantities.get(key, _ZERO) + quantity.value
        for key in self._level_scores.keys() - quantities.keys():
            self._competitor_mass -= self._level_scores.pop(key)[1]
        for key, quantity in quantities.items():
            previous = self._level_scores.get(key)
            if previous is not None and previous[0] == quantity:
                self.cached_level_hits += 1
                continue
            if previous is not None:
                self._competitor_mass -= previous[1]
            contribution = order_score(
                maximum_spread_cents=rules.maximum_spread_cents,
                distance_cents=abs(key[1] - midpoint.value) * ExactDecimal(100),
                quantity=Quantity(quantity),
                multiplier=rules.multiplier,
            ).as_fraction()
            self.cached_level_misses += 1
            self._level_scores[key] = (quantity, contribution)
            self._competitor_mass += contribution
        return self._competitor_mass

    def estimate(
        self,
        yes_book: ImmutableOrderBookSnapshot,
        no_book: ImmutableOrderBookSnapshot,
        own_orders: tuple[RewardOrder, ...],
        rules: RewardRules,
        decision_at: UtcTimestamp,
        *,
        depth_complete: bool = False,
    ) -> RewardEstimate:
        age_eligible = tuple(
            order.resting_since is not None
            and rules.minimum_age_ns is not None
            and decision_at.nanoseconds_since_epoch - order.resting_since.nanoseconds_since_epoch
            >= rules.minimum_age_ns
            for order in own_orders
        )
        reference_future = tuple(
            book.last_valid_update_time is None or book.last_valid_update_time > decision_at
            for book in (yes_book, no_book)
        )
        key = (
            id(yes_book),
            id(no_book),
            canonical_sha256(
                (
                    rules,
                    own_orders,
                    depth_complete,
                    age_eligible,
                    rules.eligibility_reasons(decision_at),
                    reference_future,
                )
            ),
        )
        cached = self._entries.get(key)
        if cached is not None:
            self.hits += 1
            self._entries.move_to_end(key)
            return cached[2]
        self.misses += 1
        result = _evaluate_reward_snapshot(
            yes_book,
            no_book,
            own_orders,
            rules,
            decision_at,
            depth_complete=depth_complete,
            mass_evaluator=self._incremental_competitor_mass,
        )
        self._entries[key] = (yes_book, no_book, result)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return result
