"""Authoritative exact paper cash, reservations, positions and exit accounting.

Estimated incentives never enter cash. Cost-basis allocation rounds toward zero
at 18 decimals and retains every residual in the remaining position; the final
sale consumes the residual exactly. This display/allocation convention cannot
create or destroy cash or aggregate basis.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.ids import AssetId, InstrumentId, MarketId
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderId, PaperOrderIntent
from polymarket_pair_maker.semantic import BinarySemanticProof
from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot

ZERO = ExactDecimal.zero()
ONE = ExactDecimal.one()
type FeeEvaluator = Callable[[Price, Quantity], Money]


@dataclass(frozen=True, slots=True)
class Reservation:
    intent: PaperOrderIntent
    remaining: Quantity
    remaining_fee: Money

    @property
    def amount(self) -> ExactDecimal:
        return self.intent.price.value * self.remaining.value + self.remaining_fee.value


@dataclass(frozen=True, slots=True)
class Position:
    market_id: MarketId
    instrument_id: InstrumentId
    parent_event_id: str
    outcome: Outcome
    quantity: Quantity
    acquisition_basis: Money

    @property
    def average_basis(self) -> Price | None:
        if not self.quantity.value:
            return None
        return Price(
            self.acquisition_basis.value.divide(
                self.quantity.value, scale=18, rounding=RoundingMode.HALF_EVEN
            )
        )


@dataclass(frozen=True, slots=True)
class ExitLeg:
    price: Price
    quantity: Quantity
    fee: Money


@dataclass(frozen=True, slots=True)
class LiquidationQuote:
    instrument_id: InstrumentId
    source_book_hash: str
    quoted_at: UtcTimestamp
    requested: Quantity
    legs: tuple[ExitLeg, ...]
    unfilled: Quantity
    gross_proceeds: Money
    fees: Money
    slippage_cost: Money
    impact_cost: Money
    net_proceeds: Money
    stress_value_of_unfilled: Money = field(default_factory=lambda: Money(ZERO))
    consumed_depth: tuple[tuple[str, ExactDecimal], ...] = ()

    def __post_init__(self) -> None:
        quantity = sum((leg.quantity.value for leg in self.legs), ZERO)
        gross = sum((leg.price.value * leg.quantity.value for leg in self.legs), ZERO)
        fees = sum((leg.fee.value for leg in self.legs), ZERO)
        if any(
            leg.quantity.value <= ZERO
            or leg.fee.value < ZERO
            or leg.fee.value > leg.price.value * leg.quantity.value
            for leg in self.legs
        ):
            raise ValueError("invalid liquidation leg quantity or fee")
        if (
            quantity + self.unfilled.value != self.requested.value
            or gross != self.gross_proceeds.value
            or fees != self.fees.value
            or self.net_proceeds.value
            != gross - fees - self.slippage_cost.value - self.impact_cost.value
            or min(self.net_proceeds.value, self.slippage_cost.value, self.impact_cost.value) < ZERO
        ):
            raise ValueError("liquidation quote fails exact reconciliation")

    @property
    def filled(self) -> Quantity:
        return Quantity(self.requested.value - self.unfilled.value)


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    starting_cash: Money
    cash: Money
    available_cash: Money
    reserved_cash: Money
    pending_order_exposure: Money
    positions: tuple[Position, ...]
    reservations: tuple[Reservation, ...]
    trading_cash_flows: Money
    realized_trading_pnl: Money
    fees_paid: Money
    estimated_liquidity_rewards: Money
    estimated_maker_rebates: Money
    verified_payouts: Money
    acquisition_basis: Money
    net_wealth_at_acquisition_basis: Money


@dataclass(frozen=True, slots=True)
class Valuation:
    inventory_value: Money
    net_wealth: Money
    unrealized_pnl: Money
    unresolved: tuple[InstrumentId, ...]
    valuation_method: str


def liquidation_quote(
    book: ImmutableOrderBookSnapshot,
    quantity: Quantity,
    *,
    now: UtcTimestamp,
    maximum_book_age_ns: int,
    fee_evaluator: FeeEvaluator,
    slippage_fraction: ExactDecimal = ZERO,
    impact_fraction: ExactDecimal = ZERO,
    consumed_depth: Mapping[str, ExactDecimal] | None = None,
) -> LiquidationQuote:
    """Sweep actual bid depth once; unfilled tokens retain explicit zero stress value."""

    if not book.is_fresh(now, max_age_ns=maximum_book_age_ns):
        raise ValueError("liquidation requires a fresh healthy canonical book")
    if not ZERO <= slippage_fraction <= ONE or not ZERO <= impact_fraction <= ONE:
        raise ValueError("exit cost fractions must lie in [0,1]")
    if slippage_fraction + impact_fraction > ONE:
        raise ValueError("combined exit cost fractions exceed gross proceeds")
    used_depth = {} if consumed_depth is None else dict(consumed_depth)
    if any(amount < ZERO for amount in used_depth.values()):
        raise ValueError("consumed exit depth cannot be negative")
    remaining = quantity.value
    gross = ZERO
    fees = ZERO
    legs: list[ExitLeg] = []
    previous: ExactDecimal | None = None
    for level in book.bids:
        if previous is not None and level.price.value >= previous:
            raise ValueError("bid depth must be strictly descending with unique prices")
        previous = level.price.value
        if not ZERO <= level.price.value <= ONE or level.quantity.value <= ZERO:
            raise ValueError("invalid binary bid depth")
        available = max(ZERO, level.quantity.value - used_depth.get(str(level.price), ZERO))
        size = min(remaining, available)
        if not size:
            continue
        fee = fee_evaluator(level.price, Quantity(size))
        proceeds = level.price.value * size
        if fee.value < ZERO or fee.value > proceeds:
            raise ValueError("invalid liquidation fee or denomination")
        legs.append(ExitLeg(level.price, Quantity(size), fee))
        remaining -= size
        gross += proceeds
        fees += fee.value
    slippage = gross * slippage_fraction
    impact = gross * impact_fraction
    net = gross - fees - slippage - impact
    if net < ZERO:
        raise ValueError("liquidation costs exceed executable proceeds")
    return LiquidationQuote(
        book.instrument_id,
        book.economic_content_sha256(),
        now,
        quantity,
        tuple(legs),
        Quantity(remaining),
        Money(gross),
        Money(fees),
        Money(slippage),
        Money(impact),
        Money(net),
        consumed_depth=tuple(sorted(used_depth.items())),
    )


class PaperLedger:
    """Single mutable owner. Every batch validates fully before committing changes."""

    def __init__(
        self, starting_cash: Money, collateral_asset_id: AssetId, *, maximum_records: int = 100_000
    ) -> None:
        if starting_cash.value <= ZERO or not 1 <= maximum_records <= 1_000_000:
            raise ValueError("positive starting cash and bounded record capacity required")
        self.starting_cash = starting_cash
        self.collateral_asset_id = collateral_asset_id
        self.maximum_records = maximum_records
        self._cash = starting_cash.value
        self._reservations: dict[PaperOrderId, Reservation] = {}
        self._positions: dict[InstrumentId, Position] = {}
        self._records: dict[str, str] = {}
        self._exit_depth_used: dict[tuple[str, str], ExactDecimal] = {}
        self._cash_flows = ZERO
        self._realized = ZERO
        self._fees = ZERO
        self._reward_estimates = ZERO
        self._rebate_estimates = ZERO

    @property
    def cash(self) -> ExactDecimal:
        return self._cash

    @property
    def reserved_cash(self) -> ExactDecimal:
        return sum((item.amount for item in self._reservations.values()), ZERO)

    @property
    def available_cash(self) -> ExactDecimal:
        return self._cash - self.reserved_cash

    @property
    def total_committed(self) -> ExactDecimal:
        return self.reserved_cash + sum(
            (item.acquisition_basis.value for item in self._positions.values()), ZERO
        )

    def positions(self) -> tuple[Position, ...]:
        return tuple(sorted(self._positions.values(), key=lambda item: item.instrument_id.value))

    def reservations(self) -> tuple[Reservation, ...]:
        return tuple(
            sorted(self._reservations.values(), key=lambda item: item.intent.order_id.value)
        )

    def position(self, instrument_id: InstrumentId) -> Position | None:
        return self._positions.get(instrument_id)

    def market_committed(self, market_id: MarketId) -> ExactDecimal:
        return sum(
            (
                item.amount
                for item in self._reservations.values()
                if item.intent.market_id == market_id
            ),
            ZERO,
        ) + sum(
            (
                item.acquisition_basis.value
                for item in self._positions.values()
                if item.market_id == market_id
            ),
            ZERO,
        )

    def parent_committed(self, parent_event_id: str) -> ExactDecimal:
        return sum(
            (
                item.amount
                for item in self._reservations.values()
                if item.intent.parent_event_id == parent_event_id
            ),
            ZERO,
        ) + sum(
            (
                item.acquisition_basis.value
                for item in self._positions.values()
                if item.parent_event_id == parent_event_id
            ),
            ZERO,
        )

    def _new_record(self, event_id: str, payload: object) -> bool:
        if not event_id or len(event_id) > 256:
            raise ValueError("ledger event identity must be nonempty and bounded")
        digest = canonical_sha256(payload)
        prior = self._records.get(event_id)
        if prior is not None:
            if prior != digest:
                raise ValueError("contradictory duplicate ledger event")
            return False
        if len(self._records) >= self.maximum_records:
            raise ValueError("ledger record capacity exhausted")
        return True

    def _record(self, event_id: str, payload: object) -> None:
        self._records[event_id] = canonical_sha256(payload)
        self.check_invariants()

    def reserve_batch(self, intents: tuple[PaperOrderIntent, ...]) -> None:
        if not intents or len(intents) > 2:
            raise ValueError("reserve one or two buy legs atomically")
        ids = tuple(item.order_id for item in intents)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate order identity within reservation batch")
        if len({item.instrument_id for item in intents}) != len(intents):
            raise ValueError("one instrument cannot back both outcome legs")
        for intent in intents:
            for outstanding in self._reservations.values():
                other = outstanding.intent
                if other.instrument_id == intent.instrument_id and (
                    other.market_id,
                    other.outcome,
                    other.parent_event_id,
                ) != (intent.market_id, intent.outcome, intent.parent_event_id):
                    raise ValueError("reserved instrument identity contradicts new intent")
        if any(item in self._reservations for item in ids):
            raise ValueError("order already has reserved capacity")
        if len(self._reservations) + len(intents) > self.maximum_records:
            raise ValueError("reservation capacity exhausted")
        required = sum((item.commitment for item in intents), ZERO)
        if required > self.available_cash:
            raise ValueError("insufficient collateral for all potentially executable legs")
        for intent in intents:
            prior = self._positions.get(intent.instrument_id)
            if prior is not None and (prior.market_id, prior.outcome, prior.parent_event_id) != (
                intent.market_id,
                intent.outcome,
                intent.parent_event_id,
            ):
                raise ValueError("instrument identity contradicts held position")
        self._reservations.update(
            {item.order_id: Reservation(item, item.quantity, item.fee_reserve) for item in intents}
        )
        self.check_invariants()

    def release(self, order_id: PaperOrderId) -> None:
        """Called only after the lifecycle owner has validated terminal evidence."""
        self._reservations.pop(order_id, None)
        self.check_invariants()

    def apply_buy_fill(
        self, event_id: str, order_id: PaperOrderId, quantity: Quantity, price: Price, fee: Money
    ) -> bool:
        payload = ("BUY", order_id, quantity, price, fee)
        if not self._new_record(event_id, payload):
            return False
        reservation = self._reservations.get(order_id)
        if reservation is None:
            raise ValueError("buy fill requires outstanding collateral reservation")
        intent = reservation.intent
        if not ZERO < quantity.value <= reservation.remaining.value:
            raise ValueError("fill quantity exceeds reserved remaining size")
        if (
            price.value > intent.price.value
            or not ZERO <= fee.value <= reservation.remaining_fee.value
        ):
            raise ValueError("fill exceeds reserved limit price or fee capacity")
        cost = price.value * quantity.value + fee.value
        prior = self._positions.get(intent.instrument_id)
        if prior is None:
            if len(self._positions) >= self.maximum_records:
                raise ValueError("position capacity exhausted")
            prior = Position(
                intent.market_id,
                intent.instrument_id,
                intent.parent_event_id,
                intent.outcome,
                Quantity(ZERO),
                Money(ZERO),
            )
        remaining = reservation.remaining.value - quantity.value
        if remaining:
            self._reservations[order_id] = replace(
                reservation,
                remaining=Quantity(remaining),
                remaining_fee=Money(reservation.remaining_fee.value - fee.value),
            )
        else:
            del self._reservations[order_id]
        self._positions[intent.instrument_id] = replace(
            prior,
            quantity=Quantity(prior.quantity.value + quantity.value),
            acquisition_basis=Money(prior.acquisition_basis.value + cost),
        )
        self._cash -= cost
        self._cash_flows -= cost
        self._fees += fee.value
        self._record(event_id, payload)
        return True

    def _removed_basis(self, position: Position, quantity: ExactDecimal) -> ExactDecimal:
        if quantity == position.quantity.value:
            return position.acquisition_basis.value
        return (position.acquisition_basis.value * quantity).divide(
            position.quantity.value, scale=18, rounding=RoundingMode.DOWN
        )

    def _reduce_position(self, position: Position, quantity: ExactDecimal) -> ExactDecimal:
        basis = self._removed_basis(position, quantity)
        remaining = position.quantity.value - quantity
        if remaining:
            self._positions[position.instrument_id] = replace(
                position,
                quantity=Quantity(remaining),
                acquisition_basis=Money(position.acquisition_basis.value - basis),
            )
        else:
            del self._positions[position.instrument_id]
        return basis

    def _consumed_for(self, instrument_id: InstrumentId) -> dict[str, ExactDecimal]:
        return {
            price: amount
            for (instrument, price), amount in self._exit_depth_used.items()
            if instrument == instrument_id.value
        }

    def quote_liquidation(
        self,
        book: ImmutableOrderBookSnapshot,
        quantity: Quantity,
        *,
        now: UtcTimestamp,
        maximum_book_age_ns: int,
        fee_evaluator: FeeEvaluator,
        slippage_fraction: ExactDecimal = ZERO,
        impact_fraction: ExactDecimal = ZERO,
    ) -> LiquidationQuote:
        """Quote only unconsumed displayed capacity at each instrument and price.

        Consumed quantities persist for the session. Metadata, freshness, ask
        updates and other bid prices cannot replenish them. A visible size
        increase permits only the excess above cumulative prior consumption.
        This is deliberately conservative in the absence of verified queue
        replenishment and counterfactual market-impact observations.
        """
        return liquidation_quote(
            book,
            quantity,
            now=now,
            maximum_book_age_ns=maximum_book_age_ns,
            fee_evaluator=fee_evaluator,
            slippage_fraction=slippage_fraction,
            impact_fraction=impact_fraction,
            consumed_depth=self._consumed_for(book.instrument_id),
        )

    def apply_liquidation(
        self,
        event_id: str,
        quote: LiquidationQuote,
        book: ImmutableOrderBookSnapshot,
    ) -> bool:
        """Execute a quoted sweep; consumed per-price liquidity cannot fund another exit."""
        payload = ("LIQUIDATION", quote)
        if not self._new_record(event_id, payload):
            return False
        if (
            quote.source_book_hash != book.economic_content_sha256()
            or quote.instrument_id != book.instrument_id
        ):
            raise ValueError("liquidation quote does not bind this canonical depth")
        position = self._positions.get(quote.instrument_id)
        if position is None or quote.requested.value > position.quantity.value:
            raise ValueError("liquidation exceeds held inventory")
        if quote.filled.value <= ZERO:
            raise ValueError("no executable exit liquidity")
        depth = {str(level.price): level.quantity.value for level in book.bids}
        usage: dict[tuple[str, str], ExactDecimal] = {}
        gross = ZERO
        fees = ZERO
        quantity = ZERO
        for leg in quote.legs:
            key = (quote.instrument_id.value, str(leg.price))
            used = usage.get(key, self._exit_depth_used.get(key, ZERO)) + leg.quantity.value
            if (
                used > depth.get(str(leg.price), ZERO)
                or leg.quantity.value <= ZERO
                or leg.fee.value < ZERO
            ):
                raise ValueError("liquidation reuses or exceeds observed bid liquidity")
            usage[key] = used
            gross += leg.price.value * leg.quantity.value
            fees += leg.fee.value
            quantity += leg.quantity.value
        if (
            quantity != quote.filled.value
            or gross != quote.gross_proceeds.value
            or fees != quote.fees.value
            or quote.net_proceeds.value
            != gross - fees - quote.slippage_cost.value - quote.impact_cost.value
            or min(quote.net_proceeds.value, quote.slippage_cost.value, quote.impact_cost.value)
            < ZERO
        ):
            raise ValueError("liquidation quote fails exact reconciliation")
        if len(self._exit_depth_used.keys() | usage.keys()) > self.maximum_records:
            raise ValueError("exit-depth evidence capacity exhausted")
        basis = self._reduce_position(position, quantity)
        self._exit_depth_used.update(usage)
        self._cash += quote.net_proceeds.value
        self._cash_flows += quote.net_proceeds.value
        self._realized += quote.net_proceeds.value - basis
        self._fees += fees
        self._record(event_id, payload)
        return True

    def settle(
        self,
        event_id: str,
        proof: BinarySemanticProof,
        *,
        winning_outcome: Outcome,
        evidence_id: str,
    ) -> bool:
        """Explicit simulated resolved-outcome contract; closing a book never calls this."""
        if type(winning_outcome) is not Outcome:
            raise TypeError("settlement outcome must be a typed Outcome")
        payload = ("SETTLEMENT", proof, winning_outcome, evidence_id)
        if not self._new_record(event_id, payload):
            return False
        self._validate_proof(proof)
        if not evidence_id:
            raise ValueError("settlement needs an explicit resolution evidence identity")
        if any(item.intent.market_id == proof.market_id for item in self._reservations.values()):
            raise ValueError("settlement requires terminal reconciliation of every order")
        positions = tuple(
            item for item in self._positions.values() if item.market_id == proof.market_id
        )
        payout = ZERO
        basis = ZERO
        for position in positions:
            expected = (
                proof.yes_instrument_id
                if position.outcome is Outcome.YES
                else proof.no_instrument_id
            )
            if expected != position.instrument_id:
                raise ValueError("settlement position contradicts complement proof")
            if position.outcome is winning_outcome:
                payout += position.quantity.value * proof.payout_per_complete_set.value
            basis += position.acquisition_basis.value
        for position in positions:
            del self._positions[position.instrument_id]
        self._cash += payout
        self._cash_flows += payout
        self._realized += payout - basis
        self._record(event_id, payload)
        return True

    def estimate_incentives(self, event_id: str, *, rewards: Money, rebates: Money) -> bool:
        payload = ("ESTIMATED_INCENTIVES", rewards, rebates)
        if not self._new_record(event_id, payload):
            return False
        if min(rewards.value, rebates.value) < ZERO:
            raise ValueError("incentive estimates cannot be negative")
        self._reward_estimates += rewards.value
        self._rebate_estimates += rebates.value
        self._record(event_id, payload)
        return True

    def _validate_proof(self, proof: BinarySemanticProof) -> None:
        if not proof.is_proven or proof.payout_asset_id != self.collateral_asset_id:
            raise ValueError("verified same-asset binary complement proof required")
        for instrument_id, outcome in (
            (proof.yes_instrument_id, Outcome.YES),
            (proof.no_instrument_id, Outcome.NO),
        ):
            position = self._positions.get(instrument_id) if instrument_id is not None else None
            if position is not None and (
                position.market_id != proof.market_id or position.outcome is not outcome
            ):
                raise ValueError("held position identity contradicts complement proof")

    def mark_to_market(self, marks: Mapping[InstrumentId, Price]) -> Valuation:
        value = ZERO
        unresolved: list[InstrumentId] = []
        basis = ZERO
        for position in self.positions():
            basis += position.acquisition_basis.value
            mark = marks.get(position.instrument_id)
            if mark is None:
                unresolved.append(position.instrument_id)
            else:
                if mark.value > ONE:
                    raise ValueError("binary mark exceeds payout unit")
                value += mark.value * position.quantity.value
        return Valuation(
            Money(value),
            Money(self._cash + value),
            Money(value - basis),
            tuple(unresolved),
            "MARK_TO_MARKET_MISSING_ZERO_STRESS",
        )

    def paired_settlement_value(self, proof: BinarySemanticProof) -> Valuation:
        """Matched pairs at proved payout; all unmatched tokens at zero stress.

        This is an alternative terminal stress valuation, never an addition to
        mark-to-market or liquidation value and never realized cash.
        """
        self._validate_proof(proof)
        yes = self._positions.get(proof.yes_instrument_id) if proof.yes_instrument_id else None
        no = self._positions.get(proof.no_instrument_id) if proof.no_instrument_id else None
        matched = min(yes.quantity.value if yes else ZERO, no.quantity.value if no else ZERO)
        value = matched * proof.payout_per_complete_set.value
        basis = sum((item.acquisition_basis.value for item in self._positions.values()), ZERO)
        unresolved = tuple(
            item.instrument_id
            for item in self.positions()
            if item.market_id != proof.market_id or item.quantity.value > matched
        )
        return Valuation(
            Money(value),
            Money(self._cash + value),
            Money(value - basis),
            unresolved,
            "MATCHED_SETTLEMENT_UNMATCHED_ZERO_STRESS",
        )

    def executable_valuation(self, quotes: Mapping[InstrumentId, LiquidationQuote]) -> Valuation:
        value = ZERO
        basis = ZERO
        unresolved: list[InstrumentId] = []
        for position in self.positions():
            basis += position.acquisition_basis.value
            quote = quotes.get(position.instrument_id)
            if quote is None:
                unresolved.append(position.instrument_id)
                continue
            if (
                quote.instrument_id != position.instrument_id
                or quote.requested != position.quantity
            ):
                raise ValueError("exit valuation must cover exactly the held inventory")
            if quote.consumed_depth != tuple(
                sorted(self._consumed_for(position.instrument_id).items())
            ):
                raise ValueError("exit valuation uses stale or unadjusted consumed depth")
            value += quote.net_proceeds.value
            if quote.unfilled.value:
                unresolved.append(position.instrument_id)
        return Valuation(
            Money(value),
            Money(self._cash + value),
            Money(value - basis),
            tuple(unresolved),
            "EXECUTABLE_DEPTH_UNFILLED_ZERO_STRESS",
        )

    def snapshot(self) -> LedgerSnapshot:
        basis = sum((item.acquisition_basis.value for item in self._positions.values()), ZERO)
        return LedgerSnapshot(
            self.starting_cash,
            Money(self._cash),
            Money(self.available_cash),
            Money(self.reserved_cash),
            Money(self.reserved_cash),
            self.positions(),
            self.reservations(),
            Money(self._cash_flows),
            Money(self._realized),
            Money(self._fees),
            Money(self._reward_estimates),
            Money(self._rebate_estimates),
            Money(ZERO),
            Money(basis),
            Money(self._cash + basis),
        )

    def check_invariants(self) -> None:
        basis = sum((item.acquisition_basis.value for item in self._positions.values()), ZERO)
        if self.available_cash < ZERO or self.reserved_cash < ZERO:
            raise ValueError("negative available or reserved paper collateral")
        if self._cash != self.starting_cash.value + self._cash_flows:
            raise ValueError("paper cash conservation failed")
        if self._cash + basis != self.starting_cash.value + self._realized:
            raise ValueError("paper wealth and acquisition-basis conservation failed")
        if any(
            item.quantity.value <= ZERO or item.acquisition_basis.value < ZERO
            for item in self._positions.values()
        ):
            raise ValueError("invalid held inventory or basis")
