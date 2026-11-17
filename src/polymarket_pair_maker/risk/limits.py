"""Conservative bounded portfolio admission; outstanding buys may all execute."""

from __future__ import annotations

from dataclasses import dataclass, field

from polymarket_pair_maker.core.exact import ExactDecimal, Money
from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.ledger import PaperLedger
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderIntent

ZERO = ExactDecimal.zero()


@dataclass(frozen=True, slots=True)
class RiskLimits:
    maximum_market_commitment: Money = field(default_factory=lambda: Money(ExactDecimal(25)))
    maximum_parent_commitment: Money = field(default_factory=lambda: Money(ExactDecimal(50)))
    maximum_total_commitment: Money = field(default_factory=lambda: Money(ExactDecimal(100)))
    maximum_directional_inventory: ExactDecimal = field(default_factory=lambda: ExactDecimal(25))
    maximum_session_loss: Money = field(default_factory=lambda: Money(ExactDecimal(25)))
    maximum_active_markets: int = 1
    maximum_active_orders: int = 2
    maximum_quote_age_ns: int = 60_000_000_000
    maximum_market_data_age_ns: int = 5_000_000_000
    minimum_time_to_resolution_ns: int = 3_600_000_000_000

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (
                self.maximum_active_markets,
                self.maximum_active_orders,
                self.maximum_quote_age_ns,
                self.maximum_market_data_age_ns,
                self.minimum_time_to_resolution_ns,
            )
        ):
            raise TypeError("risk capacity and nanosecond values must be integers")
        if (
            not ZERO
            < self.maximum_market_commitment.value
            <= self.maximum_parent_commitment.value
            <= self.maximum_total_commitment.value
        ):
            raise ValueError("require positive market <= parent <= total capital limits")
        if self.maximum_directional_inventory <= ZERO or self.maximum_session_loss.value <= ZERO:
            raise ValueError("directional inventory and loss limits must be positive")
        if self.maximum_active_markets != 1 or not 1 <= self.maximum_active_orders <= 2:
            raise ValueError("initial paper runtime supports one market and at most two bids")
        if (
            min(
                self.maximum_quote_age_ns,
                self.maximum_market_data_age_ns,
                self.minimum_time_to_resolution_ns,
            )
            <= 0
        ):
            raise ValueError("risk time bounds must be positive")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    incremental_commitment: Money
    directional_inventory_bound: ExactDecimal


class RiskController:
    """A stop is latched for the session; recovery cannot silently clear it."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.halted_reason: str | None = None

    @property
    def halted(self) -> bool:
        return self.halted_reason is not None

    def halt(self, reason: str) -> None:
        if not reason or len(reason) > 512:
            raise ValueError("risk halt reason must be nonempty and bounded")
        if self.halted_reason is None:
            self.halted_reason = reason

    def observe_wealth(self, ledger: PaperLedger, net_liquidation_wealth: Money) -> bool:
        if (
            ledger.starting_cash.value - net_liquidation_wealth.value
            >= self.limits.maximum_session_loss.value
        ):
            self.halt("MAXIMUM_SESSION_LOSS")
        return not self.halted

    def assess(self, ledger: PaperLedger, intents: tuple[PaperOrderIntent, ...]) -> RiskDecision:
        reasons: list[str] = []
        if self.halted:
            reasons.append(f"KILL_SWITCH:{self.halted_reason}")
        if not intents or len(intents) > 2:
            return RiskDecision(False, (*reasons, "INVALID_BATCH_SIZE"), Money(ZERO), ZERO)
        existing = ledger.reservations()
        incremental = sum((item.commitment for item in intents), ZERO)
        if incremental > ledger.available_cash:
            reasons.append("INSUFFICIENT_COLLATERAL")
        if ledger.total_committed + incremental > self.limits.maximum_total_commitment.value:
            reasons.append("TOTAL_COMMITMENT_LIMIT")
        if len(existing) + len(intents) > self.limits.maximum_active_orders:
            reasons.append("OUTSTANDING_ORDER_LIMIT")
        if len({item.order_id for item in intents}) != len(intents) or any(
            item.order_id == reservation.intent.order_id
            for item in intents
            for reservation in existing
        ):
            reasons.append("DUPLICATE_ORDER_ID")
        slots = [(item.intent.market_id, item.intent.outcome) for item in existing]
        slots.extend((item.market_id, item.outcome) for item in intents)
        if len(set(slots)) != len(slots):
            reasons.append("ONE_BID_PER_OUTCOME")
        markets = {item.market_id for item in intents}
        markets.update(item.intent.market_id for item in existing)
        # Inventory keeps a market active until resolved or exited.
        markets.update(item.market_id for item in ledger.positions())
        if len(markets) > self.limits.maximum_active_markets:
            reasons.append("ACTIVE_MARKET_LIMIT")
        for market_id in sorted({item.market_id for item in intents}, key=lambda item: item.value):
            addition = sum(
                (item.commitment for item in intents if item.market_id == market_id), ZERO
            )
            if (
                ledger.market_committed(market_id) + addition
                > self.limits.maximum_market_commitment.value
            ):
                reasons.append("MARKET_COMMITMENT_LIMIT")
        for parent in sorted({item.parent_event_id for item in intents}):
            addition = sum(
                (item.commitment for item in intents if item.parent_event_id == parent), ZERO
            )
            if (
                ledger.parent_committed(parent) + addition
                > self.limits.maximum_parent_commitment.value
            ):
                reasons.append("PARENT_EVENT_COMMITMENT_LIMIT")
        if any(
            item.expires_at.nanoseconds_since_epoch - item.created_at.nanoseconds_since_epoch
            > self.limits.maximum_quote_age_ns
            for item in intents
        ):
            reasons.append("MAXIMUM_QUOTE_AGE")
        direction_bound = self.directional_bound(ledger, intents)
        if direction_bound > self.limits.maximum_directional_inventory:
            reasons.append("DIRECTIONAL_INVENTORY_LIMIT")
        reasons = list(dict.fromkeys(reasons))
        return RiskDecision(not reasons, tuple(reasons), Money(incremental), direction_bound)

    def directional_bound(
        self, ledger: PaperLedger, intents: tuple[PaperOrderIntent, ...] = ()
    ) -> ExactDecimal:
        """Worst unresolved token count over independent fill/no-fill leg outcomes."""
        held: dict[MarketId, dict[Outcome, ExactDecimal]] = {}
        pending: dict[MarketId, dict[Outcome, ExactDecimal]] = {}
        for position in ledger.positions():
            sides = held.setdefault(position.market_id, {Outcome.YES: ZERO, Outcome.NO: ZERO})
            sides[position.outcome] += position.quantity.value
        for reservation in ledger.reservations():
            intent = reservation.intent
            sides = pending.setdefault(intent.market_id, {Outcome.YES: ZERO, Outcome.NO: ZERO})
            sides[intent.outcome] += reservation.remaining.value
        for intent in intents:
            sides = pending.setdefault(intent.market_id, {Outcome.YES: ZERO, Outcome.NO: ZERO})
            sides[intent.outcome] += intent.quantity.value
        bound = ZERO
        for market in held.keys() | pending.keys():
            inventory = held.get(market, {Outcome.YES: ZERO, Outcome.NO: ZERO})
            outstanding = pending.get(market, {Outcome.YES: ZERO, Outcome.NO: ZERO})
            difference = inventory[Outcome.YES] - inventory[Outcome.NO]
            bound += max(
                abs(difference + outstanding[Outcome.YES]),
                abs(difference - outstanding[Outcome.NO]),
            )
        return bound

    def reserve(self, ledger: PaperLedger, intents: tuple[PaperOrderIntent, ...]) -> RiskDecision:
        decision = self.assess(ledger, intents)
        if decision.allowed:
            ledger.reserve_batch(intents)
        return decision

    def market_reasons(
        self,
        *,
        now: UtcTimestamp,
        resolution_at: UtcTimestamp | None,
        catalyst_excluded: bool,
        executable_exit_depth: ExactDecimal,
        minimum_exit_depth: ExactDecimal,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if (
            resolution_at is None
            or resolution_at.nanoseconds_since_epoch - now.nanoseconds_since_epoch
            < self.limits.minimum_time_to_resolution_ns
        ):
            reasons.append("RESOLUTION_TIME_RISK")
        if catalyst_excluded:
            reasons.append("EVENT_CATALYST_EXCLUDED")
        if minimum_exit_depth <= ZERO or executable_exit_depth < minimum_exit_depth:
            reasons.append("INSUFFICIENT_EXIT_DEPTH")
        return tuple(reasons)
