"""Event-driven internal paper execution over accepted canonical snapshots.

L2 touches and depth changes never generate fills. Declared execution pressure
is consumed once, ahead-of-us liquidity first. Public L2 cannot establish queue
priority, trade direction, hidden liquidity or counterfactual market impact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.ids import InstrumentId
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.ledger import PaperLedger
from polymarket_pair_maker.paper.orders import (
    OrderEvent,
    OrderEventKind,
    PaperOrder,
    PaperOrderId,
    PaperOrderIntent,
    PaperOrderState,
    transition,
)
from polymarket_pair_maker.risk.limits import RiskController
from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot

ZERO = ExactDecimal.zero()


class QueueScenario(StrEnum):
    STRICT_PESSIMISTIC = "STRICT_PESSIMISTIC"
    CONSERVATIVE = "CONSERVATIVE"
    SENSITIVITY = "SENSITIVITY"


class PressureKind(StrEnum):
    OBSERVED_EXECUTION = "OBSERVED_EXECUTION"
    SYNTHETIC_EXECUTION = "SYNTHETIC_EXECUTION"
    INFERRED_PRESSURE = "INFERRED_PRESSURE"
    DEPTH_DECREASE = "DEPTH_DECREASE"
    PRICE_TOUCH = "PRICE_TOUCH"


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    submission_latency_ns: int = 100_000_000
    cancellation_latency_ns: int = 100_000_000
    scenario: QueueScenario = QueueScenario.CONSERVATIVE
    unknown_queue_buffer: Quantity = field(default_factory=lambda: Quantity(ZERO))
    inferred_pressure_fraction: ExactDecimal = field(default_factory=lambda: ExactDecimal(25, 2))
    maximum_events: int = 100_000
    maximum_orders: int = 10_000
    seed: int = 0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (
                self.submission_latency_ns,
                self.cancellation_latency_ns,
                self.maximum_events,
                self.maximum_orders,
                self.seed,
            )
        ):
            raise TypeError("execution time, capacity and seed values must be integers")
        if type(self.scenario) is not QueueScenario:
            raise TypeError("execution scenario must be typed")
        if min(self.submission_latency_ns, self.cancellation_latency_ns) < 0:
            raise ValueError("paper latencies cannot be negative")
        if not ZERO <= self.inferred_pressure_fraction <= ExactDecimal.one():
            raise ValueError("inferred pressure fraction must lie in [0,1]")
        if not 1 <= self.maximum_orders <= self.maximum_events <= 1_000_000:
            raise ValueError("paper order and event capacities must be positive and bounded")
        if isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("explicit nonnegative seed required")


@dataclass(frozen=True, slots=True)
class ObservedPressure:
    evidence_id: str
    instrument_id: InstrumentId
    price: Price
    quantity: Quantity
    occurred_at: UtcTimestamp
    available_at: UtcTimestamp
    kind: PressureKind
    source_hash: str

    def __post_init__(self) -> None:
        if type(self.kind) is not PressureKind:
            raise TypeError("execution pressure kind must be typed")
        if not self.evidence_id or len(self.evidence_id) > 256:
            raise ValueError("pressure evidence identity must be nonempty and bounded")
        if self.available_at < self.occurred_at:
            raise ValueError("pressure cannot be available before it occurred")
        if len(self.source_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_hash
        ):
            raise ValueError("pressure requires the source evidence SHA-256")
        if self.quantity.value < ZERO or self.price.value > ExactDecimal.one():
            raise ValueError("invalid binary execution-pressure values")


@dataclass(frozen=True, slots=True)
class PressureResult:
    evidence_id: str
    classification: PressureKind
    scenario: QueueScenario
    declared_quantity: Quantity
    usable_quantity: Quantity
    queue_consumed: Quantity
    simulated_filled: Quantity
    unused_quantity: Quantity
    reason: str | None


class PaperSimulator:
    """Single owner of orders, deduplication and latency; ledger owns all funds."""

    def __init__(
        self,
        ledger: PaperLedger,
        risk: RiskController,
        config: ExecutionConfig | None = None,
    ) -> None:
        self.ledger = ledger
        self.risk = risk
        self.config = config or ExecutionConfig()
        self._orders: dict[PaperOrderId, PaperOrder] = {}
        self._active: dict[PaperOrderId, None] = {}
        self._events: dict[str, OrderEvent] = {}
        self._event_log: list[OrderEvent] = []
        self._pressure: dict[str, tuple[str, PressureResult]] = {}
        self._liquidity_sources: set[str] = set()
        self._connected = True
        self._now: UtcTimestamp | None = None

    def orders(self) -> tuple[PaperOrder, ...]:
        return tuple(self._orders.values())

    def active_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(self._orders[order_id] for order_id in self._active)

    @property
    def event_count(self) -> int:
        return len(self._event_log)

    def events(self) -> tuple[OrderEvent, ...]:
        return tuple(self._event_log)

    def events_since(self, index: int) -> tuple[OrderEvent, ...]:
        if not 0 <= index <= len(self._event_log):
            raise ValueError("paper event cursor out of range")
        return tuple(self._event_log[index:])

    def pressure_results(self) -> tuple[PressureResult, ...]:
        return tuple(item[1] for item in self._pressure.values())

    @property
    def connected(self) -> bool:
        return self._connected

    def _clock(self, now: UtcTimestamp) -> None:
        if self._now is not None and now < self._now:
            self.risk.halt("NONMONOTONIC_PAPER_TIME")
            raise ValueError("paper availability time must be monotonic")
        self._now = now

    def _event_id(self, order_id: PaperOrderId, kind: OrderEventKind, now: UtcTimestamp) -> str:
        return canonical_sha256(("ppm-paper-event-v1", order_id, kind, now, len(self._events)))

    def apply_event(self, event: OrderEvent) -> bool:
        """Apply explicit simulated evidence. Duplicate IDs are content-checked first."""
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing != event:
                self.risk.halt("CONTRADICTORY_DUPLICATE_EVENT")
                raise ValueError("contradictory duplicate paper event")
            return False
        try:
            if len(self._events) >= self.config.maximum_events:
                raise ValueError("paper event capacity exhausted")
            self._clock(event.occurred_at)
            order = self._orders[event.order_id]
            updated = transition(order, event)
            if event.kind is OrderEventKind.FILL:
                if event.price is None:
                    raise ValueError("fill price required")
                self.ledger.apply_buy_fill(
                    event.event_id, event.order_id, event.quantity, event.price, event.fee
                )
            elif updated.terminal:
                self.ledger.release(event.order_id)
            self._orders[event.order_id] = updated
            if updated.terminal:
                self._active.pop(event.order_id, None)
            else:
                self._active[event.order_id] = None
            self._events[event.event_id] = event
            self._event_log.append(event)
            return True
        except ValueError, KeyError:
            self.risk.halt("PAPER_EVENT_INTEGRITY_FAILURE")
            raise

    def _emit(
        self,
        order: PaperOrder,
        kind: OrderEventKind,
        now: UtcTimestamp,
        *,
        reason: str | None = None,
        evidence_id: str | None = None,
    ) -> None:
        self.apply_event(
            OrderEvent(
                self._event_id(order.intent.order_id, kind, now),
                order.intent.order_id,
                kind,
                now,
                evidence_id=evidence_id,
                reason=reason,
            )
        )

    def _book_reason(
        self, intent: PaperOrderIntent, book: ImmutableOrderBookSnapshot | None, now: UtcTimestamp
    ) -> str | None:
        if book is None:
            return "MISSING_CANONICAL_BOOK"
        if book.instrument_id != intent.instrument_id or book.market_id != intent.market_id:
            return "CANONICAL_BOOK_IDENTITY_MISMATCH"
        if not book.is_fresh(now, max_age_ns=self.risk.limits.maximum_market_data_age_ns):
            return "STALE_OR_UNHEALTHY_BOOK"
        if book.best_ask is None or book.best_bid is None or not book.bids or not book.asks:
            return "MISSING_TWO_SIDED_DEPTH"
        if book.best_bid != book.bids[0] or book.best_ask != book.asks[0]:
            return "INCONSISTENT_CANONICAL_TOP"
        if book.best_bid.price.value >= book.best_ask.price.value:
            return "CROSSED_BOOK"
        if intent.price.value >= book.best_ask.price.value:
            return "POST_ONLY_WOULD_CROSS"
        return None

    def submit(
        self,
        intents: tuple[PaperOrderIntent, ...],
        books: Mapping[InstrumentId, ImmutableOrderBookSnapshot],
        now: UtcTimestamp,
        *,
        priority_known: bool = False,
    ) -> tuple[PaperOrder, ...]:
        self._clock(now)
        if type(priority_known) is not bool:
            raise TypeError("queue priority declaration must be boolean")
        if not 1 <= len(intents) <= 2:
            raise ValueError("paper submission must contain one or two buy legs")
        if not self._connected:
            raise ValueError("paper disconnected; resynchronization required")
        if (
            len(self._orders) + len(intents) > self.config.maximum_orders
            or len(self._events) + len(intents) > self.config.maximum_events
        ):
            self.risk.halt("PAPER_CAPACITY_EXHAUSTED")
            raise ValueError("paper session state capacity exhausted")
        for intent in intents:
            if intent.created_at != now or intent.order_id in self._orders:
                raise ValueError("submission requires a new identity created at decision time")
            reason = self._book_reason(intent, books.get(intent.instrument_id), now)
            if reason:
                raise ValueError(reason)
        decision = self.risk.reserve(self.ledger, intents)
        if not decision.allowed:
            raise ValueError(";".join(decision.reasons))
        for intent in intents:
            self._orders[intent.order_id] = PaperOrder(
                intent,
                priority_known=priority_known,
                ack_due_at=UtcTimestamp(
                    now.nanoseconds_since_epoch + self.config.submission_latency_ns
                ),
            )
            self._emit(self._orders[intent.order_id], OrderEventKind.SUBMIT, now)
        return tuple(self._orders[intent.order_id] for intent in intents)

    def request_cancel(
        self, order_id: PaperOrderId, now: UtcTimestamp, *, reason: str = "REPLACEMENT"
    ) -> None:
        self._clock(now)
        order = self._orders[order_id]
        if order.terminal or order.state is PaperOrderState.CANCEL_REQUESTED:
            return
        self._orders[order_id] = replace(
            order,
            cancel_due_at=UtcTimestamp(
                now.nanoseconds_since_epoch + self.config.cancellation_latency_ns
            ),
        )
        self._emit(self._orders[order_id], OrderEventKind.CANCEL_REQUEST, now, reason=reason)

    def safety_invalidate(self, now: UtcTimestamp, *, reason: str) -> None:
        """Safety cancellation bypasses any strategy replacement hysteresis."""
        self._clock(now)
        for order in self.active_orders():
            if not order.terminal:
                self.request_cancel(order.intent.order_id, now, reason=reason)

    def advance(
        self, now: UtcTimestamp, books: Mapping[InstrumentId, ImmutableOrderBookSnapshot]
    ) -> None:
        """Process due events in stable admission order at recorded availability time."""
        self._clock(now)
        for order in self.active_orders():
            if order.terminal:
                continue
            if now >= order.intent.expires_at:
                self._emit(order, OrderEventKind.EXPIRE, now, reason="CONTRACTUAL_EXPIRY")
                continue
            if not self._connected:
                continue
            if self.risk.halted and order.state is not PaperOrderState.CANCEL_REQUESTED:
                self.request_cancel(order.intent.order_id, now, reason="KILL_SWITCH")
                order = self._orders[order.intent.order_id]
            if order.state is PaperOrderState.CANCEL_REQUESTED:
                if order.cancel_due_at is not None and now >= order.cancel_due_at:
                    self._emit(order, OrderEventKind.CANCEL_ACK, now)
                continue
            if order.state is PaperOrderState.UNKNOWN:
                continue
            reason = self._book_reason(order.intent, books.get(order.intent.instrument_id), now)
            if order.state is PaperOrderState.PENDING_ACK:
                if order.ack_due_at is not None and now >= order.ack_due_at:
                    if reason:
                        self._emit(order, OrderEventKind.REJECT, now, reason=reason)
                    else:
                        book = books[order.intent.instrument_id]
                        ahead = sum(
                            (
                                level.quantity.value
                                for level in book.bids
                                if level.price == order.intent.price
                            ),
                            ZERO,
                        )
                        if not order.priority_known:
                            ahead += self.config.unknown_queue_buffer.value
                        self._orders[order.intent.order_id] = replace(
                            order, queue_ahead=Quantity(ahead)
                        )
                        self._emit(self._orders[order.intent.order_id], OrderEventKind.ACK, now)
            elif reason:
                self.request_cancel(order.intent.order_id, now, reason=reason)

    def mark_unknown(
        self, order_id: PaperOrderId, now: UtcTimestamp, *, cancellation: bool = False
    ) -> None:
        order = self._orders[order_id]
        kind = OrderEventKind.CANCEL_UNKNOWN if cancellation else OrderEventKind.ACK_UNKNOWN
        self._emit(order, kind, now, reason="SIMULATED_RESPONSE_UNCERTAINTY")

    def disconnect(self, now: UtcTimestamp, *, reason: str = "DISCONNECT") -> None:
        self._clock(now)
        self._connected = False
        for order in self.active_orders():
            if not order.terminal and order.state is not PaperOrderState.UNKNOWN:
                self._emit(order, OrderEventKind.DISCONNECT, now, reason=reason)

    def resynchronize(
        self,
        now: UtcTimestamp,
        books: Mapping[InstrumentId, ImmutableOrderBookSnapshot],
        *,
        evidence_id: str,
    ) -> None:
        """Restore data admission only. Unknown orders require individual reconciliation."""
        self._clock(now)
        if not evidence_id:
            raise ValueError("resynchronization requires validated canonical-state evidence")
        for order in self.active_orders():
            if not order.terminal:
                reason = self._book_reason(order.intent, books.get(order.intent.instrument_id), now)
                if reason:
                    raise ValueError(reason)
        self._connected = True

    def reconcile_resting(
        self,
        order_id: PaperOrderId,
        now: UtcTimestamp,
        *,
        evidence_id: str,
        queue_ahead: Quantity,
        priority_known: bool = False,
    ) -> None:
        if type(priority_known) is not bool:
            raise TypeError("queue priority declaration must be boolean")
        if not self._connected:
            raise ValueError("canonical state resynchronization required first")
        order = self._orders[order_id]
        self._emit(order, OrderEventKind.RECONCILE_RESTING, now, evidence_id=evidence_id)
        self._orders[order_id] = replace(
            self._orders[order_id],
            queue_ahead=queue_ahead,
            priority_known=priority_known,
            cancel_due_at=None,
        )

    def validate_pressure(self, pressure: ObservedPressure) -> None:
        """Reject unsupported delayed execution before any terminal capacity release.

        Latency is modeled at recorded boundaries. Rewinding an already processed
        acknowledgement would require a separate execution-time reconciliation
        contract. Retained chronology detects this uncertainty even after a
        caller has already advanced to a terminal order state.
        """
        if (
            pressure.available_at == pressure.occurred_at
            or pressure.kind in {PressureKind.PRICE_TOUCH, PressureKind.DEPTH_DECREASE}
            or not pressure.quantity.value
        ):
            return
        for order in self.orders():
            if (
                order.intent.instrument_id != pressure.instrument_id
                or order.intent.price != pressure.price
            ):
                continue
            if order.acknowledged_at is None or pressure.occurred_at < order.acknowledged_at:
                continue
            if pressure.occurred_at >= order.intent.expires_at:
                continue
            if (
                order.terminal
                and order.last_event_at is not None
                and pressure.occurred_at >= order.last_event_at
            ):
                continue
            self.risk.halt("DELAYED_EXECUTION_RECONCILIATION_UNAVAILABLE")
            raise ValueError("delayed execution pressure requires unavailable reconciliation")

    def process_pressure(self, pressure: ObservedPressure) -> PressureResult:
        """Consume one causal pressure budget once; never turn an L2 delta into a fill."""
        digest = canonical_sha256(pressure)
        prior = self._pressure.get(pressure.evidence_id)
        if prior is not None:
            if prior[0] != digest:
                self.risk.halt("CONTRADICTORY_PRESSURE_EVIDENCE")
                raise ValueError("contradictory duplicate pressure evidence")
            return prior[1]
        self.validate_pressure(pressure)
        self._clock(pressure.available_at)
        if len(self._pressure) >= self.config.maximum_events:
            self.risk.halt("PRESSURE_CAPACITY_EXHAUSTED")
            raise ValueError("pressure evidence capacity exhausted")
        usable = pressure.quantity.value
        reason: str | None = None
        if pressure.kind in {PressureKind.DEPTH_DECREASE, PressureKind.PRICE_TOUCH}:
            usable = ZERO
            reason = "L2_IS_NOT_EXECUTION_EVIDENCE"
        elif pressure.kind is PressureKind.INFERRED_PRESSURE:
            if self.config.scenario is QueueScenario.SENSITIVITY:
                usable *= self.config.inferred_pressure_fraction
                reason = "SENSITIVITY_ASSUMPTION_NOT_OBSERVED_EXECUTION"
            else:
                usable = ZERO
                reason = "UNVERIFIED_EXECUTION_PRESSURE"
        if not self._connected:
            usable = ZERO
            reason = "DISCONNECTED_UNRECONCILED_QUEUE"
        source_identity = canonical_sha256(
            (
                pressure.source_hash,
                pressure.instrument_id,
                pressure.price,
                pressure.occurred_at,
            )
        )
        if source_identity in self._liquidity_sources:
            usable = ZERO
            reason = "REUSED_SOURCE_LIQUIDITY"
        remaining = usable
        queue_used = ZERO
        fills = ZERO
        for order in self.active_orders():
            if not remaining:
                break
            if (
                order.intent.instrument_id != pressure.instrument_id
                or order.intent.price != pressure.price
            ):
                continue
            if order.state not in {
                PaperOrderState.RESTING,
                PaperOrderState.PARTIALLY_FILLED,
                PaperOrderState.CANCEL_REQUESTED,
            }:
                continue
            if (
                order.ack_due_at is None
                or order.acknowledged_at is None
                or pressure.occurred_at < order.acknowledged_at
                or pressure.available_at >= order.intent.expires_at
                or pressure.occurred_at < order.ack_due_at
                or pressure.occurred_at >= order.intent.expires_at
            ):
                continue
            # Received-late evidence cannot pretend we were already at this queue.
            if order.last_event_at is None or pressure.occurred_at < order.last_event_at:
                reason = "PRESSURE_PRECEDES_LAST_ORDER_TRANSITION"
                continue
            if (
                self.config.scenario is QueueScenario.STRICT_PESSIMISTIC
                and not order.priority_known
            ):
                reason = "UNKNOWN_QUEUE_PRIORITY"
                continue
            consumed = min(order.queue_ahead.value, remaining)
            remaining -= consumed
            queue_used += consumed
            queue_after = Quantity(order.queue_ahead.value - consumed)
            amount = min(order.remaining.value, remaining)
            if amount:
                event = OrderEvent(
                    canonical_sha256(
                        ("ppm-pressure-fill-v1", pressure.evidence_id, order.intent.order_id)
                    ),
                    order.intent.order_id,
                    OrderEventKind.FILL,
                    pressure.available_at,
                    Quantity(amount),
                    order.intent.price,
                    Money(ZERO),
                    pressure.evidence_id,
                    f"ASSUMED_{self.config.scenario.value}_FILL",
                )
                self.apply_event(event)
                remaining -= amount
                fills += amount
            # A rejected fill must leave its queue untouched. Event/ledger
            # capacity validation happens inside apply_event before this commit.
            self._orders[order.intent.order_id] = replace(
                self._orders[order.intent.order_id], queue_ahead=queue_after
            )
        result = PressureResult(
            pressure.evidence_id,
            pressure.kind,
            self.config.scenario,
            pressure.quantity,
            Quantity(usable),
            Quantity(queue_used),
            Quantity(fills),
            Quantity(remaining),
            reason,
        )
        self._pressure[pressure.evidence_id] = digest, result
        self._liquidity_sources.add(source_identity)
        return result
