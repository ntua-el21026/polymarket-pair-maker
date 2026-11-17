from dataclasses import replace

import pytest

from polymarket_pair_maker.core.exact import Price, Quantity
from polymarket_pair_maker.core.ids import InstrumentId, MarketId
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.orders import (
    OrderEvent,
    OrderEventKind,
    Outcome,
    PaperOrder,
    PaperOrderId,
    PaperOrderIntent,
    PaperOrderState,
    transition,
)


def _order() -> PaperOrder:
    return PaperOrder(
        PaperOrderIntent(
            PaperOrderId("test"),
            MarketId("market"),
            InstrumentId("yes"),
            "event",
            Outcome.YES,
            Price.parse("0.4"),
            Quantity.parse("10"),
            UtcTimestamp(0),
            UtcTimestamp(1000),
        ),
        ack_due_at=UtcTimestamp(10),
    )


def _event(kind: OrderEventKind, at: int = 10) -> OrderEvent:
    return OrderEvent(kind.value, PaperOrderId("test"), kind, UtcTimestamp(at))


def _resting() -> PaperOrder:
    return transition(
        transition(_order(), _event(OrderEventKind.SUBMIT, 0)), _event(OrderEventKind.ACK)
    )


def test_submission_and_ack_obey_latency():
    pending = transition(_order(), _event(OrderEventKind.SUBMIT, 0))
    assert pending.state is PaperOrderState.PENDING_ACK
    with pytest.raises(ValueError, match="latency"):
        transition(pending, _event(OrderEventKind.ACK, 9))
    assert transition(pending, _event(OrderEventKind.ACK)).state is PaperOrderState.RESTING


def test_cancel_request_partial_race_preserves_cancel_requested():
    order = transition(_resting(), _event(OrderEventKind.CANCEL_REQUEST, 20))
    event = OrderEvent(
        "fill",
        order.intent.order_id,
        OrderEventKind.FILL,
        UtcTimestamp(21),
        Quantity.parse("3"),
        Price.parse("0.4"),
        evidence_id="trade",
    )
    order = transition(order, event)
    assert order.state is PaperOrderState.CANCEL_REQUESTED
    assert order.remaining == Quantity.parse("7")
    order = replace(order, cancel_due_at=UtcTimestamp(30))
    with pytest.raises(ValueError, match="latency"):
        transition(order, _event(OrderEventKind.CANCEL_ACK, 29))
    assert transition(order, _event(OrderEventKind.CANCEL_ACK, 30)).terminal


def test_full_fill_then_new_event_is_contradictory():
    order = _resting()
    fill = OrderEvent(
        "fill",
        order.intent.order_id,
        OrderEventKind.FILL,
        UtcTimestamp(11),
        Quantity.parse("10"),
        Price.parse("0.39"),
        evidence_id="trade",
    )
    order = transition(order, fill)
    assert order.state is PaperOrderState.FILLED
    with pytest.raises(ValueError, match="terminal"):
        transition(order, _event(OrderEventKind.DISCONNECT, 12))


@pytest.mark.parametrize("kind", [OrderEventKind.ACK_UNKNOWN, OrderEventKind.DISCONNECT])
def test_unknown_retains_remaining_and_requires_reconciliation(kind: OrderEventKind):
    before = (
        transition(_order(), _event(OrderEventKind.SUBMIT, 0))
        if kind is OrderEventKind.ACK_UNKNOWN
        else _resting()
    )
    order = transition(before, _event(kind, 20))
    assert order.state is PaperOrderState.UNKNOWN
    assert order.remaining == Quantity.parse("10")
    with pytest.raises(ValueError, match="evidence"):
        transition(order, _event(OrderEventKind.RECONCILE_RESTING, 21))
    event = replace(_event(OrderEventKind.RECONCILE_RESTING, 21), evidence_id="resync")
    assert transition(order, event).state is PaperOrderState.RESTING


def test_unknown_cancel_response_requires_explicit_terminal_evidence():
    order = transition(_resting(), _event(OrderEventKind.CANCEL_REQUEST, 20))
    order = replace(order, cancel_due_at=UtcTimestamp(30))
    order = transition(order, _event(OrderEventKind.CANCEL_UNKNOWN, 30))
    assert order.state is PaperOrderState.UNKNOWN
    assert (
        transition(order, _event(OrderEventKind.CANCEL_ACK, 31)).state is PaperOrderState.CANCELLED
    )


def test_expiration_in_unknown_state_has_contractual_evidence():
    order = transition(_resting(), _event(OrderEventKind.DISCONNECT, 20))
    with pytest.raises(ValueError, match="contradictory"):
        transition(order, _event(OrderEventKind.EXPIRE, 999))
    assert transition(order, _event(OrderEventKind.EXPIRE, 1000)).state is PaperOrderState.EXPIRED


@pytest.mark.parametrize(("quantity", "price"), [("11", "0.4"), ("1", "0.41")])
def test_bad_fill_rejected_without_changing_order(quantity: str, price: str):
    order = _resting()
    fill = OrderEvent(
        "fill",
        order.intent.order_id,
        OrderEventKind.FILL,
        UtcTimestamp(11),
        Quantity.parse(quantity),
        Price.parse(price),
        evidence_id="trade",
    )
    with pytest.raises(ValueError, match=r"exceeds|violates"):
        transition(order, fill)
    assert order.remaining == Quantity.parse("10")


@pytest.mark.parametrize(("price", "quantity"), [("0", "1"), ("1", "1"), ("0.5", "0")])
def test_invalid_intent_values(price: str, quantity: str):
    with pytest.raises(ValueError, match=r"price|quantity"):
        replace(_order().intent, price=Price.parse(price), quantity=Quantity.parse(quantity))


def test_event_identity_time_and_classification_rejected():
    with pytest.raises(ValueError, match="identity"):
        transition(_order(), replace(_event(OrderEventKind.SUBMIT), order_id=PaperOrderId("other")))
    with pytest.raises(ValueError, match="precedes"):
        transition(_resting(), _event(OrderEventKind.DISCONNECT, 9))
    with pytest.raises(ValueError, match="verified"):
        replace(_event(OrderEventKind.SUBMIT), classification="VERIFIED")
    with pytest.raises(ValueError, match="non-fill"):
        replace(_event(OrderEventKind.SUBMIT), quantity=Quantity.parse("1"))
