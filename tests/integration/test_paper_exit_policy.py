"""Public session-path tests for withdrawal and executable paper exits."""

from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Quantity
from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.artifacts import PaperArtifacts
from polymarket_pair_maker.paper.config import PaperConfig, load_paper_config
from polymarket_pair_maker.paper.demo import demo_evidence, demo_market_context
from polymarket_pair_maker.paper.evidence import PaperEvidence
from polymarket_pair_maker.paper.execution import ObservedPressure, PressureKind, QueueScenario
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderState
from polymarket_pair_maker.paper.session import ScenarioSession
from polymarket_pair_maker.pipeline.market_view import CanonicalMarketView
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state.order_books import BookHealth, ImmutableOrderBookSnapshot

ROOT = Path(__file__).resolve().parents[2]


def _prepare(
    tmp_path: Path, expiry_kind: str = "calibration"
) -> tuple[
    ScenarioSession, PaperArtifacts, dict[MarketId, CanonicalMarketView], UtcTimestamp, UtcTimestamp
]:
    config = load_paper_config(ROOT / "config/paper.toml")
    context = demo_market_context(config)
    expiry = UtcTimestamp(context.as_of.nanoseconds_since_epoch + 200_000_000)
    evidence = demo_evidence()
    market = evidence.markets[0]
    if expiry_kind == "calibration":
        assert market.calibration_period is not None
        market = replace(
            market, calibration_period=replace(market.calibration_period, evaluation_end=expiry)
        )
    elif expiry_kind == "fee":
        assert market.fee_rules is not None
        fee = market.fee_rules
        market = replace(
            market,
            fee_rules=replace(
                fee,
                evidence=replace(
                    fee.evidence, times=replace(fee.evidence.times, effective_to=expiry)
                ),
            ),
        )
    elif expiry_kind == "reward":
        assert market.reward_rules is not None
        reward = market.reward_rules
        market = replace(
            market,
            reward_rules=replace(
                reward,
                evidence=replace(
                    reward.evidence, times=replace(reward.evidence.times, effective_to=expiry)
                ),
            ),
        )
    else:
        raise ValueError("unsupported test expiry")
    evidence = replace(evidence, markets=(market,), pressures=())
    return _admitted(tmp_path, config, evidence, context.as_of, expiry)


def _admitted(
    tmp_path: Path,
    config: PaperConfig,
    evidence: PaperEvidence,
    now: UtcTimestamp,
    expiry: UtcTimestamp,
) -> tuple[
    ScenarioSession, PaperArtifacts, dict[MarketId, CanonicalMarketView], UtcTimestamp, UtcTimestamp
]:
    context = demo_market_context(config)
    artifacts = PaperArtifacts(tmp_path / "output", maximum_bytes=config.maximum_output_bytes)
    session = ScenarioSession(
        config,
        QueueScenario.CONSERVATIVE,
        evidence,
        artifacts,
        source_run_id="synthetic-exit-policy",
    )
    view = CanonicalMarketView(
        context.bundle,
        prove_binary_complement(context.bundle),
        (context.yes_book, context.no_book),
        context.assessment,
        context.as_of,
        False,
    )
    views = {context.bundle.market.market_id: view}
    session.process(seq=0, now=now, views=views, dirty=True, source_valid=True)
    ack = UtcTimestamp(now.nanoseconds_since_epoch + config.execution.submission_latency_ns)
    session.process(seq=1, now=ack, views=views, dirty=False, source_valid=True)
    assert len(session.simulator.active_orders()) == 2
    assert all(
        order.state is PaperOrderState.RESTING for order in session.simulator.active_orders()
    )
    return session, artifacts, views, ack, expiry


def _buy_two(session: ScenarioSession, ack: UtcTimestamp) -> None:
    yes = next(
        order for order in session.simulator.active_orders() if order.intent.outcome is Outcome.YES
    )
    now = UtcTimestamp(ack.nanoseconds_since_epoch + 1)
    result = session.simulator.process_pressure(
        ObservedPressure(
            "synthetic-buy-two",
            yes.intent.instrument_id,
            yes.intent.price,
            Quantity(yes.queue_ahead.value + ExactDecimal(2)),
            now,
            now,
            PressureKind.SYNTHETIC_EXECUTION,
            "e" * 64,
        )
    )
    assert result.simulated_filled == Quantity.parse("2")
    assert session.ledger.cash == ExactDecimal.parse("999.2")


def _abstain_and_ack(
    session: ScenarioSession, views: dict[MarketId, CanonicalMarketView], expiry: UtcTimestamp
) -> UtcTimestamp:
    session.process(seq=2, now=expiry, views=views, dirty=True, source_valid=True)
    assert all(
        order.state is PaperOrderState.CANCEL_REQUESTED
        for order in session.simulator.active_orders()
    )
    assert session.ledger.reserved_cash == ExactDecimal.parse("3.2")
    return UtcTimestamp(
        expiry.nanoseconds_since_epoch + session.config.execution.cancellation_latency_ns
    )


def test_abstention_cancels_then_liquidates_with_exact_taker_fees_slippage_and_impact(
    tmp_path: Path,
):
    session, artifacts, views, ack, expiry = _prepare(tmp_path)
    try:
        _buy_two(session, ack)
        done = _abstain_and_ack(session, views, expiry)
        session.process(seq=3, now=done, views=views, dirty=False, source_valid=True)
        assert not session.simulator.active_orders()
        assert not session.ledger.positions()
        # 2*.4 gross - 2*.07*.4*.6 taker fee - 2*(.8*.001) exit charges.
        assert session.ledger.cash == ExactDecimal.parse("999.9648")
        assert session.ledger.snapshot().fees_paid == Money.parse("0.0336")
        assert session.ledger.snapshot().realized_trading_pnl == Money.parse("-0.0352")
        assert session.ledger.reserved_cash == ExactDecimal.zero()
        session.ledger.check_invariants()
    finally:
        artifacts.close_incomplete()


def test_partial_exit_remains_unresolved_and_cannot_reuse_same_bid_snapshot(tmp_path: Path):
    session, artifacts, views, ack, expiry = _prepare(tmp_path)
    try:
        _buy_two(session, ack)
        done = _abstain_and_ack(session, views, expiry)
        view = next(iter(views.values()))
        yes_id = view.proof.yes_instrument_id
        limited: list[ImmutableOrderBookSnapshot] = []
        for book in view.books:
            if book.instrument_id == yes_id:
                bid = replace(book.bids[0], quantity=Quantity.parse("1"))
                book = replace(book, bids=(bid,), best_bid=bid)
            limited.append(book)
        views = {view.bundle.market.market_id: replace(view, books=tuple(limited))}
        session.process(seq=3, now=done, views=views, dirty=False, source_valid=True)
        assert session.ledger.positions()[0].quantity == Quantity.parse("1")
        assert session.ledger.cash == ExactDecimal.parse("999.5824")
        before = session.ledger.snapshot()
        session.process(
            seq=4,
            now=UtcTimestamp(done.nanoseconds_since_epoch + 1),
            views=views,
            dirty=False,
            source_valid=True,
        )
        assert session.ledger.snapshot() == before
        assert session.ledger.positions()[0].quantity == Quantity.parse("1")
        # A newly observed book with identical bids is not replenishment of the
        # hypothetical liquidity already consumed by our partial exit.
        refreshed_at = UtcTimestamp(done.nanoseconds_since_epoch + 2)
        refreshed = {
            key: replace(
                current,
                books=tuple(
                    replace(
                        book,
                        last_valid_update_time=refreshed_at,
                        last_venue_timestamp=refreshed_at,
                        generation=book.generation + 1,
                    )
                    for book in current.books
                ),
            )
            for key, current in views.items()
        }
        session.process(seq=5, now=refreshed_at, views=refreshed, dirty=False, source_valid=True)
        assert session.ledger.snapshot() == before
        valuation = session.valuation(refreshed, refreshed_at)
        assert valuation["executable_wealth"] == Money.parse("999.5824")
    finally:
        artifacts.close_incomplete()


def test_stale_exit_depth_preserves_unresolved_inventory_after_cancellation(tmp_path: Path):
    session, artifacts, views, ack, expiry = _prepare(tmp_path)
    try:
        _buy_two(session, ack)
        done = _abstain_and_ack(session, views, expiry)
        views = {
            key: replace(
                view, books=tuple(replace(book, health=BookHealth.STALE) for book in view.books)
            )
            for key, view in views.items()
        }
        session.process(seq=3, now=done, views=views, dirty=False, source_valid=True)
        assert not session.simulator.active_orders()
        assert session.ledger.positions()[0].quantity == Quantity.parse("2")
        assert session.ledger.cash == ExactDecimal.parse("999.2")
        assert session.ledger.snapshot().fees_paid == Money.parse("0")
    finally:
        artifacts.close_incomplete()


@pytest.mark.parametrize("expiry_kind", ["fee", "reward", "calibration"])
def test_clock_only_reference_expiry_cancels_without_dirty_book_event(
    tmp_path: Path, expiry_kind: str
):
    session, artifacts, views, _ack, expiry = _prepare(tmp_path, expiry_kind)
    try:
        session.process(seq=2, now=expiry, views=views, dirty=False, source_valid=True)
        assert all(
            order.state is PaperOrderState.CANCEL_REQUESTED
            for order in session.simulator.active_orders()
        )
        assert session.ledger.reserved_cash == ExactDecimal(4)
        assert session.fail_closed
        assert all(
            event.reason
            for event in session.simulator.events()
            if event.kind.value == "CANCEL_REQUEST"
        )
    finally:
        artifacts.close_incomplete()
