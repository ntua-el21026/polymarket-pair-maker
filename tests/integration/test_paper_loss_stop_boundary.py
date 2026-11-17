"""Independent acceptance regression: current losses precede new admission."""

from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Quantity
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.artifacts import PaperArtifacts
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_evidence, demo_market_context
from polymarket_pair_maker.paper.evidence import PressureInput
from polymarket_pair_maker.paper.execution import ObservedPressure, PressureKind, QueueScenario
from polymarket_pair_maker.paper.session import ScenarioSession
from polymarket_pair_maker.pipeline.market_view import CanonicalMarketView
from polymarket_pair_maker.semantic import prove_binary_complement


@pytest.mark.parametrize(("loss_limit", "expected_orders"), [("0.001", 2), ("25", 4)])
def test_current_fill_loss_is_checked_before_requoting(
    tmp_path: Path, loss_limit: str, expected_orders: int
) -> None:
    config = PaperConfig()
    config = replace(
        config, risk=replace(config.risk, maximum_session_loss=Money.parse(loss_limit))
    )
    context = demo_market_context(config)
    now = context.as_of
    fill_at = UtcTimestamp(now.nanoseconds_since_epoch + 200_000_000)
    pressures: list[PressureInput] = []
    for index, book in enumerate((context.yes_book, context.no_book)):
        bid = book.best_bid
        assert bid is not None
        pressures.append(
            PressureInput(
                2,
                ObservedPressure(
                    f"independent-risk-stop-{index}",
                    book.instrument_id,
                    bid.price,
                    Quantity(bid.quantity.value + ExactDecimal(5)),
                    fill_at,
                    fill_at,
                    PressureKind.SYNTHETIC_EXECUTION,
                    str(index + 1) * 64,
                ),
            )
        )
    evidence = replace(demo_evidence(), pressures=tuple(pressures))
    artifacts = PaperArtifacts(tmp_path / "out", maximum_bytes=config.maximum_output_bytes)
    session = ScenarioSession(
        config,
        QueueScenario.CONSERVATIVE,
        evidence,
        artifacts,
        source_run_id="independent-loss-stop",
    )
    view = CanonicalMarketView(
        context.bundle,
        prove_binary_complement(context.bundle),
        (context.yes_book, context.no_book),
        context.assessment,
        now,
        False,
    )
    market = context.bundle.market.market_id
    try:
        session.process(seq=0, now=now, views={market: view}, dirty=True, source_valid=True)
        ack_at = UtcTimestamp(now.nanoseconds_since_epoch + 100_000_000)
        session.process(seq=1, now=ack_at, views={market: view}, dirty=False, source_valid=True)
        assert len(session.simulator.orders()) == 2
        fresh = replace(
            view,
            assessment=replace(view.assessment, observed_at=fill_at, terminal_hash=""),
            observed_at=fill_at,
        )
        session.process(seq=2, now=fill_at, views={market: fresh}, dirty=True, source_valid=True)
        assert len(session.simulator.orders()) == expected_orders
        if loss_limit == "0.001":
            assert session.risk.halted_reason == "MAXIMUM_SESSION_LOSS"
            assert session.ledger.reserved_cash == ExactDecimal.zero()
            assert not session.simulator.active_orders()
        else:
            assert not session.risk.halted
            assert session.ledger.reserved_cash == ExactDecimal(4)
            assert len(session.simulator.active_orders()) == 2
    finally:
        artifacts.close_incomplete()
