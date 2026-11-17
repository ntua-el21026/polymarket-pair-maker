from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
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


@given(
    chunks=st.lists(st.integers(1, 10), min_size=1, max_size=30), cancel_index=st.integers(0, 30)
)
def test_arbitrary_partial_fills_and_cancel_races_preserve_quantity(
    chunks: list[int], cancel_index: int
):
    size = sum(chunks)
    intent = PaperOrderIntent(
        PaperOrderId("p"),
        MarketId("m"),
        InstrumentId("yes"),
        "event",
        Outcome.YES,
        Price.parse("0.5"),
        Quantity(ExactDecimal(size)),
        UtcTimestamp(0),
        UtcTimestamp(1000),
    )
    order = PaperOrder(intent, ack_due_at=UtcTimestamp(1))
    order = transition(
        order, OrderEvent("submit", intent.order_id, OrderEventKind.SUBMIT, UtcTimestamp(0))
    )
    order = transition(
        order, OrderEvent("ack", intent.order_id, OrderEventKind.ACK, UtcTimestamp(1))
    )
    total = ExactDecimal.zero()
    for index, amount in enumerate(chunks):
        now = UtcTimestamp(index + 2)
        if index == cancel_index:
            order = replace(order, cancel_due_at=UtcTimestamp(999))
            order = transition(
                order, OrderEvent("cancel", intent.order_id, OrderEventKind.CANCEL_REQUEST, now)
            )
        order = transition(
            order,
            OrderEvent(
                f"fill-{index}",
                intent.order_id,
                OrderEventKind.FILL,
                now,
                Quantity(ExactDecimal(amount)),
                intent.price,
                evidence_id=f"trade-{index}",
            ),
        )
        total += ExactDecimal(amount)
        assert order.remaining.value + order.filled.value == ExactDecimal(size)
        assert order.filled.value == total
        if cancel_index <= index < len(chunks) - 1:
            assert order.state is PaperOrderState.CANCEL_REQUESTED
    assert order.state is PaperOrderState.FILLED
    assert order.remaining.value == ExactDecimal.zero()
    with pytest.raises(ValueError, match="terminal"):
        transition(
            order,
            OrderEvent("cancelled", intent.order_id, OrderEventKind.CANCEL_ACK, UtcTimestamp(999)),
        )
