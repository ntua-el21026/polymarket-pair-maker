"""Independent integration regressions for evidence and transport boundaries."""

from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.config import load_phase1_config
from polymarket_pair_maker.core.exact import ExactDecimal
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.artifacts import PaperArtifacts
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import (
    demo_evidence,
    demo_market_context,
    materialize_demo_source,
)
from polymarket_pair_maker.paper.execution import QueueScenario
from polymarket_pair_maker.paper.orders import PaperOrderState
from polymarket_pair_maker.paper.replay import replay_paper
from polymarket_pair_maker.paper.session import ScenarioSession
from polymarket_pair_maker.pipeline.market_view import CanonicalMarketView
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state.order_books import BookHealth, InvalidationReason

ROOT = Path(__file__).resolve().parents[2]


def test_review_replay_rejects_unconsumed_pressure_evidence(tmp_path: Path):
    run_id = materialize_demo_source(tmp_path / "raw")
    evidence = demo_evidence()
    orphan = replace(evidence.pressures[0], ingest_seq=999_999)
    with pytest.raises(ValueError, match=r"pressure|Pressure"):
        replay_paper(
            tmp_path / "raw",
            run_id,
            load_phase1_config(ROOT / "config/phase1.toml"),
            PaperConfig(),
            output=tmp_path / "result",
            code_commit="0" * 40,
            evidence=replace(evidence, pressures=(orphan,)),
            synthetic=True,
        )


def test_review_disconnect_does_not_manufacture_cancellation_ack(tmp_path: Path):
    config = PaperConfig()
    context = demo_market_context(config)
    artifacts = PaperArtifacts(tmp_path / "output", maximum_bytes=config.maximum_output_bytes)
    session = ScenarioSession(
        config,
        QueueScenario.CONSERVATIVE,
        replace(demo_evidence(), pressures=()),
        artifacts,
        source_run_id="review-disconnect",
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
    try:
        session.process(seq=0, now=context.as_of, views=views, dirty=True, source_valid=True)
        assert session.simulator.orders()
        acknowledged = UtcTimestamp(
            context.as_of.nanoseconds_since_epoch + config.execution.submission_latency_ns
        )
        session.process(seq=1, now=acknowledged, views=views, dirty=False, source_valid=True)
        assert all(order.state is PaperOrderState.RESTING for order in session.simulator.orders())
        lost = replace(
            view,
            books=tuple(
                replace(
                    book,
                    health=BookHealth.INVALID,
                    invalidation_reason=InvalidationReason.DISCONNECT,
                )
                for book in view.books
            ),
        )
        now = UtcTimestamp(acknowledged.nanoseconds_since_epoch + 1)
        lost_views = {context.bundle.market.market_id: lost}
        session.process(seq=2, now=now, views=lost_views, dirty=True, source_valid=True)
        after_cancel_due = UtcTimestamp(
            now.nanoseconds_since_epoch + config.execution.cancellation_latency_ns
        )
        session.process(
            seq=3, now=after_cancel_due, views=lost_views, dirty=False, source_valid=True
        )
        assert all(order.state is PaperOrderState.UNKNOWN for order in session.simulator.orders())
        assert session.ledger.reserved_cash > ExactDecimal.zero()
    finally:
        artifacts.close_incomplete()
