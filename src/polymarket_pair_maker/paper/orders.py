"""Typed, immutable internal paper-order lifecycle.

Events are simulated contract evidence, never exchange execution reports. The
simulator owns deduplication and accounting; this pure transition rejects any
contradictory history before either owner is mutated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.ids import InstrumentId, MarketId, TypedId
from polymarket_pair_maker.core.time import UtcTimestamp


class PaperOrderId(TypedId):
    """A deterministic identity with no venue-order interpretation."""


class Outcome(StrEnum):
    YES = "YES"
    NO = "NO"


class PaperOrderState(StrEnum):
    CREATED = "CREATED"
    PENDING_ACK = "PENDING_ACK"
    RESTING = "RESTING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset(
    {
        PaperOrderState.FILLED,
        PaperOrderState.CANCELLED,
        PaperOrderState.EXPIRED,
        PaperOrderState.REJECTED,
    }
)


class OrderEventKind(StrEnum):
    SUBMIT = "SUBMIT"
    ACK = "ACK"
    ACK_UNKNOWN = "ACK_UNKNOWN"
    REJECT = "REJECT"
    FILL = "FILL"
    CANCEL_REQUEST = "CANCEL_REQUEST"
    CANCEL_ACK = "CANCEL_ACK"
    CANCEL_UNKNOWN = "CANCEL_UNKNOWN"
    EXPIRE = "EXPIRE"
    DISCONNECT = "DISCONNECT"
    RECONCILE_RESTING = "RECONCILE_RESTING"


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    order_id: PaperOrderId
    market_id: MarketId
    instrument_id: InstrumentId
    parent_event_id: str
    outcome: Outcome
    price: Price
    quantity: Quantity
    created_at: UtcTimestamp
    expires_at: UtcTimestamp
    fee_reserve: Money = field(default_factory=lambda: Money(ExactDecimal.zero()))

    def __post_init__(self) -> None:
        if type(self.outcome) is not Outcome:
            raise TypeError("paper outcome must be a typed Outcome")
        if not self.parent_event_id or len(self.parent_event_id) > 256:
            raise ValueError("parent event identity must be nonempty and bounded")
        if not ExactDecimal.zero() < self.price.value < ExactDecimal.one():
            raise ValueError("passive binary buy price must lie strictly between zero and one")
        if self.quantity.value <= ExactDecimal.zero():
            raise ValueError("paper quantity must be positive")
        if self.fee_reserve.value < ExactDecimal.zero():
            raise ValueError("fee reserve cannot be negative")
        if self.expires_at <= self.created_at:
            raise ValueError("expiry must follow creation")

    @property
    def commitment(self) -> ExactDecimal:
        return self.price.value * self.quantity.value + self.fee_reserve.value


@dataclass(frozen=True, slots=True)
class PaperOrder:
    intent: PaperOrderIntent
    state: PaperOrderState = PaperOrderState.CREATED
    filled: Quantity = field(default_factory=lambda: Quantity(ExactDecimal.zero()))
    last_event_at: UtcTimestamp | None = None
    queue_ahead: Quantity = field(default_factory=lambda: Quantity(ExactDecimal.zero()))
    priority_known: bool = False
    ack_due_at: UtcTimestamp | None = None
    acknowledged_at: UtcTimestamp | None = None
    cancel_due_at: UtcTimestamp | None = None

    @property
    def remaining(self) -> Quantity:
        return Quantity(self.intent.quantity.value - self.filled.value)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class OrderEvent:
    event_id: str
    order_id: PaperOrderId
    kind: OrderEventKind
    occurred_at: UtcTimestamp
    quantity: Quantity = field(default_factory=lambda: Quantity(ExactDecimal.zero()))
    price: Price | None = None
    fee: Money = field(default_factory=lambda: Money(ExactDecimal.zero()))
    evidence_id: str | None = None
    reason: str | None = None
    classification: str = "SIMULATED_EXECUTION"

    def __post_init__(self) -> None:
        if type(self.kind) is not OrderEventKind:
            raise TypeError("paper event kind must be typed")
        if not self.event_id or len(self.event_id) > 256:
            raise ValueError("paper event identity must be nonempty and bounded")
        if self.evidence_id is not None and (not self.evidence_id or len(self.evidence_id) > 256):
            raise ValueError("paper evidence identity must be nonempty and bounded")
        if self.reason is not None and len(self.reason) > 512:
            raise ValueError("paper event reason exceeds bounded diagnostic length")
        if self.classification != "SIMULATED_EXECUTION":
            raise ValueError("paper event cannot claim to be a verified exchange execution")
        if self.fee.value < ExactDecimal.zero():
            raise ValueError("paper fee cannot be negative")
        if self.kind is OrderEventKind.FILL:
            if self.quantity.value <= ExactDecimal.zero() or self.price is None:
                raise ValueError("fill requires positive quantity and exact price")
            if not self.evidence_id:
                raise ValueError("fill requires explicit simulation evidence identity")
        elif self.quantity.value or self.price is not None or self.fee.value:
            raise ValueError("non-fill event cannot carry fill values")


def transition(order: PaperOrder, event: OrderEvent) -> PaperOrder:
    """Apply one unique contract event, or fail without mutating prior state."""

    if event.order_id != order.intent.order_id:
        raise ValueError("paper event order identity mismatch")
    if event.occurred_at < order.intent.created_at or (
        order.last_event_at is not None and event.occurred_at < order.last_event_at
    ):
        raise ValueError("paper event precedes order history")
    if order.terminal:
        raise ValueError("new event contradicts a terminal order")
    state = order.state
    kind = event.kind
    target: PaperOrderState
    filled = order.filled
    acknowledged_at = order.acknowledged_at
    if kind is OrderEventKind.SUBMIT and state is PaperOrderState.CREATED:
        target = PaperOrderState.PENDING_ACK
    elif kind is OrderEventKind.ACK and state is PaperOrderState.PENDING_ACK:
        if order.ack_due_at is not None and event.occurred_at < order.ack_due_at:
            raise ValueError("acknowledgement precedes submission latency")
        target = PaperOrderState.RESTING
        acknowledged_at = event.occurred_at
    elif (
        kind is OrderEventKind.REJECT
        and state in {PaperOrderState.CREATED, PaperOrderState.PENDING_ACK, PaperOrderState.UNKNOWN}
        and not order.filled.value
        and order.acknowledged_at is None
    ):
        target = PaperOrderState.REJECTED
    elif (
        (kind is OrderEventKind.ACK_UNKNOWN and state is PaperOrderState.PENDING_ACK)
        or (kind is OrderEventKind.DISCONNECT and state is not PaperOrderState.CREATED)
        or (kind is OrderEventKind.CANCEL_UNKNOWN and state is PaperOrderState.CANCEL_REQUESTED)
    ):
        target = PaperOrderState.UNKNOWN
    elif kind is OrderEventKind.CANCEL_REQUEST and state in {
        PaperOrderState.PENDING_ACK,
        PaperOrderState.RESTING,
        PaperOrderState.PARTIALLY_FILLED,
        PaperOrderState.UNKNOWN,
    }:
        target = PaperOrderState.CANCEL_REQUESTED
    elif kind is OrderEventKind.CANCEL_ACK and state in {
        PaperOrderState.CANCEL_REQUESTED,
        PaperOrderState.UNKNOWN,
    }:
        if order.cancel_due_at is None or event.occurred_at < order.cancel_due_at:
            raise ValueError("cancel acknowledgement lacks elapsed request latency")
        target = PaperOrderState.CANCELLED
    elif kind is OrderEventKind.EXPIRE and event.occurred_at >= order.intent.expires_at:
        target = PaperOrderState.EXPIRED
    elif kind is OrderEventKind.RECONCILE_RESTING and state is PaperOrderState.UNKNOWN:
        if not event.evidence_id:
            raise ValueError("resynchronization requires explicit evidence")
        if order.ack_due_at is not None and event.occurred_at < order.ack_due_at:
            raise ValueError("reconciliation precedes submission latency")
        acknowledged_at = event.occurred_at
        target = PaperOrderState.PARTIALLY_FILLED if filled.value else PaperOrderState.RESTING
    elif kind is OrderEventKind.FILL and state in {
        PaperOrderState.RESTING,
        PaperOrderState.PARTIALLY_FILLED,
        PaperOrderState.CANCEL_REQUESTED,
        PaperOrderState.UNKNOWN,
    }:
        if event.occurred_at >= order.intent.expires_at:
            raise ValueError("fill at or after contractual expiry")
        if order.ack_due_at is not None and event.occurred_at < order.ack_due_at:
            raise ValueError("fill precedes submission latency")
        if event.quantity.value > order.remaining.value:
            raise ValueError("fill exceeds remaining quantity")
        if event.price is None or event.price.value > order.intent.price.value:
            raise ValueError("buy fill violates limit price")
        filled = Quantity(filled.value + event.quantity.value)
        target = (
            PaperOrderState.FILLED
            if filled == order.intent.quantity
            else PaperOrderState.CANCEL_REQUESTED
            if state is PaperOrderState.CANCEL_REQUESTED
            else PaperOrderState.UNKNOWN
            if state is PaperOrderState.UNKNOWN
            else PaperOrderState.PARTIALLY_FILLED
        )
    else:
        raise ValueError(f"contradictory paper transition: {state.value}/{kind.value}")
    return replace(
        order,
        state=target,
        filled=filled,
        last_event_at=event.occurred_at,
        acknowledged_at=acknowledged_at,
    )
