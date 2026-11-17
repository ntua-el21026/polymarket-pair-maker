from dataclasses import replace

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.health import BookHealth
from polymarket_pair_maker.core.ids import (
    AssetId,
    CostRuleSetVersionId,
    InstrumentId,
    MarketId,
    MarketVersionId,
    PrecisionRuleVersionId,
)
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.execution import (
    ExecutionConfig,
    ObservedPressure,
    PaperSimulator,
    PressureKind,
    QueueScenario,
)
from polymarket_pair_maker.paper.ledger import PaperLedger, liquidation_quote
from polymarket_pair_maker.paper.orders import (
    OrderEvent,
    OrderEventKind,
    Outcome,
    PaperOrderId,
    PaperOrderIntent,
    PaperOrderState,
)
from polymarket_pair_maker.risk import RiskController
from polymarket_pair_maker.state.order_books import BookLevel, ImmutableOrderBookSnapshot


def _book(
    instrument: str = "yes", *, now: int = 0, bid: str = "0.4", ask: str = "0.6", depth: str = "5"
) -> ImmutableOrderBookSnapshot:
    bids = (BookLevel(Price.parse(bid), Quantity.parse(depth)),)
    asks = (BookLevel(Price.parse(ask), Quantity.parse("100")),)
    return ImmutableOrderBookSnapshot(
        InstrumentId(instrument),
        MarketId("market"),
        1,
        BookHealth.HEALTHY,
        None,
        bids,
        asks,
        bids[0],
        asks[0],
        None,
        None,
        None,
        None,
        MarketVersionId("market-v1"),
        PrecisionRuleVersionId("precision-v1"),
        CostRuleSetVersionId("fee-v1"),
        UtcTimestamp(now),
        UtcTimestamp(now),
    )


def _intent(instrument: str = "yes", *, now: int = 0, quantity: str = "10") -> PaperOrderIntent:
    return PaperOrderIntent(
        PaperOrderId(instrument),
        MarketId("market"),
        InstrumentId(instrument),
        "event",
        Outcome.YES if instrument == "yes" else Outcome.NO,
        Price.parse("0.4"),
        Quantity.parse(quantity),
        UtcTimestamp(now),
        UtcTimestamp(now + 60_000_000_000),
    )


def _sim(
    scenario: QueueScenario = QueueScenario.CONSERVATIVE, *, priority: bool = False
) -> PaperSimulator:
    sim = PaperSimulator(
        PaperLedger(Money.parse("1000"), AssetId("pUSD")),
        RiskController(),
        ExecutionConfig(submission_latency_ns=10, cancellation_latency_ns=10, scenario=scenario),
    )
    sim.submit(
        (_intent(),), {InstrumentId("yes"): _book()}, UtcTimestamp(0), priority_known=priority
    )
    sim.advance(UtcTimestamp(10), {InstrumentId("yes"): _book()})
    return sim


def _pressure(
    identity: str = "trade",
    *,
    at: int = 11,
    quantity: str = "7",
    kind: PressureKind = PressureKind.SYNTHETIC_EXECUTION,
) -> ObservedPressure:
    return ObservedPressure(
        identity,
        InstrumentId("yes"),
        Price.parse("0.4"),
        Quantity.parse(quantity),
        UtcTimestamp(at),
        UtcTimestamp(at),
        kind,
        "a" * 64,
    )


def test_queue_depleted_before_partial_fill_with_exact_cash():
    sim = _sim()
    result = sim.process_pressure(_pressure())
    assert result.queue_consumed == Quantity.parse("5")
    assert result.simulated_filled == Quantity.parse("2")
    assert sim.orders()[0].state is PaperOrderState.PARTIALLY_FILLED
    assert sim.ledger.cash == ExactDecimal.parse("999.2")
    assert sim.ledger.reserved_cash == ExactDecimal.parse("3.2")
    assert sim.ledger.available_cash == ExactDecimal(996)


@pytest.mark.parametrize(
    "kind", [PressureKind.DEPTH_DECREASE, PressureKind.PRICE_TOUCH, PressureKind.INFERRED_PRESSURE]
)
def test_nonexecution_observations_never_fill_conservative(kind: PressureKind):
    sim = _sim()
    result = sim.process_pressure(_pressure(quantity="100", kind=kind))
    assert not result.simulated_filled.value
    assert sim.orders()[0].queue_ahead == Quantity.parse("5")
    assert not sim.ledger.positions()


def test_strict_priority_unknown_abstains_even_with_execution_pressure():
    sim = _sim(QueueScenario.STRICT_PESSIMISTIC)
    result = sim.process_pressure(_pressure(quantity="100"))
    assert result.reason == "UNKNOWN_QUEUE_PRIORITY"
    assert not result.simulated_filled.value


def test_strict_known_priority_still_consumes_visible_queue():
    sim = _sim(QueueScenario.STRICT_PESSIMISTIC, priority=True)
    assert sim.process_pressure(_pressure()).simulated_filled == Quantity.parse("2")


def test_sensitivity_only_uses_explicit_discounted_inference():
    sim = _sim(QueueScenario.SENSITIVITY)
    result = sim.process_pressure(_pressure(quantity="28", kind=PressureKind.INFERRED_PRESSURE))
    assert result.usable_quantity == Quantity.parse("7")
    assert result.simulated_filled == Quantity.parse("2")
    assert result.reason == "SENSITIVITY_ASSUMPTION_NOT_OBSERVED_EXECUTION"


def test_duplicate_pressure_and_fill_are_idempotent_and_not_reusable():
    sim = _sim()
    pressure = _pressure()
    result = sim.process_pressure(pressure)
    snapshot = sim.ledger.snapshot()
    assert sim.process_pressure(pressure) == result
    assert not sim.apply_event(sim.events()[-1])
    assert sim.ledger.snapshot() == snapshot
    sim.process_pressure(_pressure("second", at=12, quantity="1"))
    assert sim.orders()[0].filled == Quantity.parse("3")
    with pytest.raises(ValueError, match="contradictory duplicate"):
        sim.process_pressure(replace(pressure, quantity=Quantity.parse("9")))
    assert sim.risk.halted


def test_fill_after_cancel_request_before_ack_keeps_capacity():
    sim = _sim()
    sim.request_cancel(PaperOrderId("yes"), UtcTimestamp(11))
    assert sim.ledger.reserved_cash == ExactDecimal(4)
    sim.process_pressure(_pressure(at=12))
    assert sim.orders()[0].state is PaperOrderState.CANCEL_REQUESTED
    assert sim.ledger.reserved_cash == ExactDecimal.parse("3.2")
    sim.advance(UtcTimestamp(20), {InstrumentId("yes"): _book()})
    assert sim.ledger.reserved_cash == ExactDecimal.parse("3.2")
    sim.advance(UtcTimestamp(21), {InstrumentId("yes"): _book()})
    assert sim.orders()[0].state is PaperOrderState.CANCELLED
    assert not sim.ledger.reserved_cash
    assert sim.ledger.available_cash == ExactDecimal.parse("999.2")


def test_unknown_cancel_does_not_auto_release_on_advance():
    sim = _sim()
    sim.request_cancel(PaperOrderId("yes"), UtcTimestamp(11))
    sim.mark_unknown(PaperOrderId("yes"), UtcTimestamp(12), cancellation=True)
    sim.advance(UtcTimestamp(100), {InstrumentId("yes"): _book()})
    assert sim.orders()[0].state is PaperOrderState.UNKNOWN
    assert sim.ledger.reserved_cash == ExactDecimal(4)
    sim.apply_event(
        OrderEvent(
            "reconciled-cancel", PaperOrderId("yes"), OrderEventKind.CANCEL_ACK, UtcTimestamp(101)
        )
    )
    assert not sim.ledger.reserved_cash


def test_unknown_submission_remains_pending_exposure_until_reconciled():
    sim = PaperSimulator(
        PaperLedger(Money.parse("1000"), AssetId("pUSD")),
        RiskController(),
        ExecutionConfig(submission_latency_ns=10),
    )
    sim.submit((_intent(),), {InstrumentId("yes"): _book()}, UtcTimestamp(0))
    sim.mark_unknown(PaperOrderId("yes"), UtcTimestamp(5))
    sim.advance(UtcTimestamp(20), {InstrumentId("yes"): _book()})
    assert sim.orders()[0].state is PaperOrderState.UNKNOWN
    assert sim.ledger.reserved_cash == ExactDecimal(4)
    sim.reconcile_resting(
        PaperOrderId("yes"), UtcTimestamp(21), evidence_id="resync", queue_ahead=Quantity.parse("5")
    )
    assert sim.orders()[0].state is PaperOrderState.RESTING


def test_disconnect_and_data_resync_do_not_reconcile_orders_implicitly():
    sim = _sim()
    sim.disconnect(UtcTimestamp(11))
    assert not sim.connected
    assert sim.orders()[0].state is PaperOrderState.UNKNOWN
    assert not sim.process_pressure(_pressure(at=12, quantity="100")).simulated_filled.value
    sim.resynchronize(UtcTimestamp(13), {InstrumentId("yes"): _book()}, evidence_id="canonical")
    assert sim.connected
    assert sim.orders()[0].state is PaperOrderState.UNKNOWN
    assert sim.ledger.reserved_cash == ExactDecimal(4)
    sim.reconcile_resting(
        PaperOrderId("yes"),
        UtcTimestamp(14),
        evidence_id="state-proof",
        queue_ahead=Quantity.parse("5"),
    )
    assert sim.process_pressure(
        _pressure("after-resync", at=15)
    ).simulated_filled == Quantity.parse("2")


def test_stale_book_requests_immediate_paper_cancel():
    sim = _sim()
    sim.advance(UtcTimestamp(5_000_000_001), {InstrumentId("yes"): _book()})
    assert sim.orders()[0].state is PaperOrderState.CANCEL_REQUESTED
    assert sim.events()[-1].reason == "STALE_OR_UNHEALTHY_BOOK"
    assert sim.ledger.reserved_cash == ExactDecimal(4)


def test_post_only_is_rechecked_after_submission_latency():
    sim = PaperSimulator(
        PaperLedger(Money.parse("1000"), AssetId("pUSD")),
        RiskController(),
        ExecutionConfig(submission_latency_ns=10),
    )
    sim.submit((_intent(),), {InstrumentId("yes"): _book()}, UtcTimestamp(0))
    sim.advance(UtcTimestamp(10), {InstrumentId("yes"): _book(bid="0.2", ask="0.3")})
    assert sim.orders()[0].state is PaperOrderState.REJECTED
    assert not sim.ledger.reserved_cash


def test_atomic_pair_reservation_and_both_legs_can_fill():
    sim = PaperSimulator(
        PaperLedger(Money.parse("1000"), AssetId("pUSD")),
        RiskController(),
        ExecutionConfig(submission_latency_ns=10),
    )
    books = {InstrumentId(item): _book(item) for item in ("yes", "no")}
    sim.submit((_intent(), _intent("no")), books, UtcTimestamp(0))
    assert sim.ledger.reserved_cash == ExactDecimal(8)
    sim.advance(UtcTimestamp(10), books)
    sim.process_pressure(_pressure(quantity="15"))
    sim.process_pressure(
        replace(_pressure("no-trade", at=12, quantity="15"), instrument_id=InstrumentId("no"))
    )
    assert all(order.state is PaperOrderState.FILLED for order in sim.orders())
    assert sim.ledger.cash == ExactDecimal(992)
    assert len(sim.ledger.positions()) == 2
    sim.ledger.check_invariants()


def test_expiry_after_interruption_is_contractual_not_fabricated_cancel():
    sim = _sim()
    sim.disconnect(UtcTimestamp(11))
    sim.advance(UtcTimestamp(60_000_000_000), {})
    assert sim.orders()[0].state is PaperOrderState.EXPIRED
    assert sim.events()[-1].reason == "CONTRACTUAL_EXPIRY"
    assert not sim.ledger.reserved_cash


def test_capacity_exhaustion_halts_and_retains_collateral():
    sim = _sim()
    sim.config = replace(sim.config, maximum_events=2, maximum_orders=1)
    with pytest.raises(ValueError, match="capacity"):
        sim.request_cancel(PaperOrderId("yes"), UtcTimestamp(11))
    assert sim.risk.halted
    assert sim.ledger.reserved_cash == ExactDecimal(4)


def test_hard_integrity_latches_stop_with_outstanding_exposure():
    sim = _sim()
    with pytest.raises(ValueError, match="contradictory"):
        sim.apply_event(
            OrderEvent("bad-ack", PaperOrderId("yes"), OrderEventKind.ACK, UtcTimestamp(11))
        )
    assert sim.risk.halted
    sim.advance(UtcTimestamp(12), {InstrumentId("yes"): _book()})
    assert sim.orders()[0].state is PaperOrderState.CANCEL_REQUESTED
    assert sim.ledger.reserved_cash == ExactDecimal(4)


def test_pressure_cannot_fill_before_ack_or_retroactively_before_order():
    sim = _sim()
    delayed = replace(_pressure(), occurred_at=UtcTimestamp(5))
    assert not sim.process_pressure(delayed).simulated_filled.value
    assert sim.ledger.reserved_cash == ExactDecimal(4)


def test_events_since_cursor_is_incremental():
    sim = _sim()
    assert sim.events_since(1) == sim.events()[1:]
    assert sim.events_since(2) == ()
    with pytest.raises(ValueError, match="cursor"):
        sim.events_since(3)


def test_liquidation_sweeps_real_depth_fees_and_keeps_unfilled_inventory():
    sim = _sim()
    sim.process_pressure(_pressure(quantity="15"))
    book = _book(bid="0.5", depth="3", now=12)

    def fee(price: Price, quantity: Quantity) -> Money:
        return Money(price.value * quantity.value * ExactDecimal.parse("0.01"))

    quote = liquidation_quote(
        book,
        Quantity.parse("10"),
        now=UtcTimestamp(12),
        maximum_book_age_ns=100,
        fee_evaluator=fee,
        slippage_fraction=ExactDecimal.parse("0.01"),
    )
    assert quote.unfilled == Quantity.parse("7")
    assert quote.net_proceeds == Money.parse("1.47")
    assert sim.ledger.apply_liquidation("exit", quote, book)
    assert not sim.ledger.apply_liquidation("exit", quote, book)
    assert sim.ledger.cash == ExactDecimal.parse("997.47")
    position = sim.ledger.position(InstrumentId("yes"))
    assert position is not None
    assert position.quantity == Quantity.parse("7")
    quote2 = liquidation_quote(
        book, Quantity.parse("7"), now=UtcTimestamp(12), maximum_book_age_ns=100, fee_evaluator=fee
    )
    with pytest.raises(ValueError, match="reuses"):
        sim.ledger.apply_liquidation("exit-reused", quote2, book)
    sim.ledger.check_invariants()


def test_liquidation_missing_depth_remains_explicit_unresolved():
    sim = _sim()
    sim.process_pressure(_pressure(quantity="15"))
    book = replace(_book(now=12), bids=(), best_bid=None)
    quote = liquidation_quote(
        book,
        Quantity.parse("10"),
        now=UtcTimestamp(12),
        maximum_book_age_ns=100,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    assert quote.filled == Quantity.parse("0")
    value = sim.ledger.executable_valuation({InstrumentId("yes"): quote})
    assert value.unresolved == (InstrumentId("yes"),)
    assert value.net_wealth == Money.parse("996")
    with pytest.raises(ValueError, match="no executable"):
        sim.ledger.apply_liquidation("exit", quote, book)


def test_renamed_execution_evidence_cannot_reuse_same_source_liquidity():
    sim = _sim()
    pressure = _pressure()
    sim.process_pressure(pressure)
    result = sim.process_pressure(replace(pressure, evidence_id="renamed"))
    assert result.reason == "REUSED_SOURCE_LIQUIDITY"
    assert not result.simulated_filled.value
    assert sim.orders()[0].filled == Quantity.parse("2")


def test_cancel_while_submission_pending_does_not_invent_queue_priority():
    sim = PaperSimulator(
        PaperLedger(Money.parse("1000"), AssetId("pUSD")),
        RiskController(),
        ExecutionConfig(submission_latency_ns=10, cancellation_latency_ns=20),
    )
    sim.submit((_intent(),), {InstrumentId("yes"): _book()}, UtcTimestamp(0))
    sim.request_cancel(PaperOrderId("yes"), UtcTimestamp(5))
    result = sim.process_pressure(_pressure(at=15, quantity="100"))
    assert not result.simulated_filled.value
    assert sim.orders()[0].state is PaperOrderState.CANCEL_REQUESTED
    assert sim.ledger.reserved_cash == ExactDecimal(4)


def test_acknowledged_order_cannot_later_be_rejected_as_never_admitted():
    sim = _sim()
    sim.disconnect(UtcTimestamp(11))
    with pytest.raises(ValueError, match="contradictory"):
        sim.apply_event(
            OrderEvent(
                "reject-after-ack", PaperOrderId("yes"), OrderEventKind.REJECT, UtcTimestamp(12)
            )
        )
    assert sim.ledger.reserved_cash == ExactDecimal(4)


def test_only_active_orders_participate_in_incremental_processing():
    sim = _sim()
    assert len(sim.active_orders()) == 1
    sim.process_pressure(_pressure(quantity="100"))
    assert sim.active_orders() == ()
    assert len(sim.orders()) == 1
    assert sim.event_count == 3


def test_delayed_cancel_race_preflight_halts_before_collateral_release():
    sim = _sim()
    sim.request_cancel(PaperOrderId("yes"), UtcTimestamp(11))
    late = replace(_pressure(at=12), available_at=UtcTimestamp(22))
    with pytest.raises(ValueError, match="unavailable reconciliation"):
        sim.validate_pressure(late)
    assert sim.risk.halted_reason == "DELAYED_EXECUTION_RECONCILIATION_UNAVAILABLE"
    assert sim.orders()[0].state is PaperOrderState.CANCEL_REQUESTED
    assert sim.ledger.reserved_cash == ExactDecimal(4)
    assert not sim.ledger.positions()


def test_delayed_pressure_after_terminal_ack_cannot_claim_successful_reconciliation():
    sim = _sim()
    sim.request_cancel(PaperOrderId("yes"), UtcTimestamp(11))
    sim.advance(UtcTimestamp(21), {InstrumentId("yes"): _book()})
    late = replace(_pressure(at=12), available_at=UtcTimestamp(22))
    with pytest.raises(ValueError, match="unavailable reconciliation"):
        sim.process_pressure(late)
    assert sim.risk.halted_reason == "DELAYED_EXECUTION_RECONCILIATION_UNAVAILABLE"
    assert not sim.pressure_results()


def test_delayed_l2_evidence_does_not_trigger_execution_reconciliation():
    sim = _sim()
    delayed = replace(
        _pressure(at=11, kind=PressureKind.DEPTH_DECREASE), available_at=UtcTimestamp(12)
    )
    assert not sim.process_pressure(delayed).simulated_filled.value
    assert not sim.risk.halted


def test_consumed_exit_liquidity_never_reappears_in_executable_wealth():
    ledger = PaperLedger(Money.parse("1000"), AssetId("pUSD"))
    intent = _intent(quantity="120")
    ledger.reserve_batch((intent,))
    ledger.apply_buy_fill(
        "buy120", intent.order_id, intent.quantity, intent.price, Money.parse("0")
    )
    book = _book(depth="60", now=1)
    quote = ledger.quote_liquidation(
        book,
        intent.quantity,
        now=UtcTimestamp(1),
        maximum_book_age_ns=10,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    ledger.apply_liquidation("sell60", quote, book)
    assert ledger.cash == ExactDecimal(976)
    remaining = Quantity.parse("60")
    refreshed = replace(
        book,
        last_valid_update_time=UtcTimestamp(2),
        last_venue_timestamp=UtcTimestamp(2),
        generation=2,
    )
    second = ledger.quote_liquidation(
        refreshed,
        remaining,
        now=UtcTimestamp(2),
        maximum_book_age_ns=10,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    assert second.filled == Quantity.parse("0")
    assert second.unfilled == remaining
    assert ledger.executable_valuation({intent.instrument_id: second}).net_wealth == Money.parse(
        "976"
    )
    unadjusted = liquidation_quote(
        refreshed,
        remaining,
        now=UtcTimestamp(2),
        maximum_book_age_ns=10,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    with pytest.raises(ValueError, match="consumed depth"):
        ledger.executable_valuation({intent.instrument_id: unadjusted})
    risk = RiskController()
    risk.limits = replace(risk.limits, maximum_session_loss=Money.parse("10"))
    assert not risk.observe_wealth(
        ledger, ledger.executable_valuation({intent.instrument_id: second}).net_wealth
    )
    assert risk.halted_reason == "MAXIMUM_SESSION_LOSS"


def test_only_visible_size_above_permanent_consumption_admits_additional_exit():
    sim = _sim()
    sim.process_pressure(_pressure(quantity="15"))
    book = _book(depth="3", now=12)
    quote = sim.ledger.quote_liquidation(
        book,
        Quantity.parse("10"),
        now=UtcTimestamp(12),
        maximum_book_age_ns=100,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    sim.ledger.apply_liquidation("exit3", quote, book)
    bid = replace(book.bids[0], quantity=Quantity.parse("5"))
    increased = replace(book, bids=(bid,), best_bid=bid, last_valid_update_time=UtcTimestamp(13))
    second = sim.ledger.quote_liquidation(
        increased,
        Quantity.parse("7"),
        now=UtcTimestamp(13),
        maximum_book_age_ns=100,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    assert second.filled == Quantity.parse("2")
    assert second.unfilled == Quantity.parse("5")
    sim.ledger.apply_liquidation("exit2", second, increased)
    assert sim.ledger.positions()[0].quantity == Quantity.parse("5")
    sim.ledger.check_invariants()
