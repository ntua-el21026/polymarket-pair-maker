"""Clock-only reference invalidation keeps active PAPER exposure reserved."""

from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Quantity
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics.reference import RuleEvidence
from polymarket_pair_maker.paper.artifacts import PaperArtifacts
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_evidence, demo_market_context
from polymarket_pair_maker.paper.evidence import PressureInput
from polymarket_pair_maker.paper.execution import ObservedPressure, PressureKind, QueueScenario
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderState
from polymarket_pair_maker.paper.session import ScenarioSession
from polymarket_pair_maker.pipeline.market_view import CanonicalMarketView
from polymarket_pair_maker.semantic import prove_binary_complement


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("economic_max_age", "STALE_ECONOMIC_REFERENCE"),
        ("canonical_max_age", "STALE_CANONICAL_REFERENCE"),
        ("canonical_effective_to", "CANONICAL_REFERENCE_NOT_CURRENT"),
        ("canonical_superseded_at", "CANONICAL_REFERENCE_NOT_CURRENT"),
    ],
)
def test_clock_only_reference_invalidation_cancels_before_releasing_collateral(
    tmp_path: Path, case: str, expected_reason: str
):
    config = PaperConfig()
    context = demo_market_context(config)
    now = context.as_of
    expiry = UtcTimestamp(now.nanoseconds_since_epoch + 200_000_000)
    evidence = replace(demo_evidence(), pressures=())
    market = evidence.markets[0]
    bundle = context.bundle

    if case == "economic_max_age":
        age = (
            now.nanoseconds_since_epoch
            - market.risk_evidence.times.observed_at.nanoseconds_since_epoch
            + 200_000_000
        )
        config = replace(config, strategy=replace(config.strategy, maximum_reference_age_ns=age))
    elif case == "canonical_max_age":
        # Only the canonical precision reference expires: economic references
        # remain within their age bound at the later recorded boundary.
        config = replace(
            config, strategy=replace(config.strategy, maximum_reference_age_ns=300_000_000)
        )
        bundle = replace(
            bundle,
            precision=replace(
                bundle.precision,
                times=replace(
                    bundle.precision.times,
                    observed_at=UtcTimestamp(now.nanoseconds_since_epoch - 100_000_000),
                ),
            ),
        )

        def fresh(reference: RuleEvidence) -> RuleEvidence:
            return replace(reference, times=replace(reference.times, observed_at=now))

        assert market.fee_rules is not None
        assert market.reward_rules is not None
        market = replace(
            market,
            risk_evidence=fresh(market.risk_evidence),
            fee_rules=replace(market.fee_rules, evidence=fresh(market.fee_rules.evidence)),
            reward_rules=replace(market.reward_rules, evidence=fresh(market.reward_rules.evidence)),
        )
        evidence = replace(evidence, markets=(market,))
    else:
        times = bundle.precision.times
        times = (
            replace(times, effective_to=expiry)
            if case == "canonical_effective_to"
            else replace(times, superseded_at=expiry)
        )
        bundle = replace(bundle, precision=replace(bundle.precision, times=times))

    artifacts = PaperArtifacts(tmp_path / "output", maximum_bytes=config.maximum_output_bytes)
    session = ScenarioSession(
        config,
        QueueScenario.CONSERVATIVE,
        evidence,
        artifacts,
        source_run_id=f"synthetic-clock-reference-{case}",
    )
    view = CanonicalMarketView(
        bundle,
        prove_binary_complement(bundle),
        (context.yes_book, context.no_book),
        context.assessment,
        now,
        False,
    )
    views = {bundle.market.market_id: view}
    try:
        session.process(seq=0, now=now, views=views, dirty=True, source_valid=True)
        assert len(session.simulator.active_orders()) == 2
        ack = UtcTimestamp(now.nanoseconds_since_epoch + config.execution.submission_latency_ns)
        session.process(seq=1, now=ack, views=views, dirty=False, source_valid=True)
        assert all(
            order.state is PaperOrderState.RESTING for order in session.simulator.active_orders()
        )
        decision_count = sum(session.decisions.values())
        invalid_at = UtcTimestamp(expiry.nanoseconds_since_epoch + 1)
        session.process(seq=2, now=invalid_at, views=views, dirty=False, source_valid=True)

        assert len(session.simulator.active_orders()) == 2
        assert all(
            order.state is PaperOrderState.CANCEL_REQUESTED
            for order in session.simulator.active_orders()
        )
        assert session.ledger.reserved_cash == ExactDecimal(4)
        assert session.fail_closed[expected_reason] == 1
        assert sum(session.decisions.values()) == decision_count
        if case == "canonical_max_age":
            assert "STALE_ECONOMIC_REFERENCE" not in session.fail_closed
    finally:
        artifacts.close_incomplete()


@pytest.mark.parametrize("fee_state", ["economic_expired", "canonical_expired", "valid"])
def test_execution_pressure_requires_current_fee_evidence_before_any_fill(
    tmp_path: Path, fee_state: str
):
    config = PaperConfig()
    context = demo_market_context(config)
    now = context.as_of
    pressure_at = UtcTimestamp(now.nanoseconds_since_epoch + 200_000_000)
    evidence = demo_evidence()
    market = evidence.markets[0]
    bundle = context.bundle
    assert market.fee_rules is not None
    if fee_state == "economic_expired":
        fee = market.fee_rules
        market = replace(
            market,
            fee_rules=replace(
                fee,
                evidence=replace(
                    fee.evidence, times=replace(fee.evidence.times, effective_to=pressure_at)
                ),
            ),
        )
    elif fee_state == "canonical_expired":
        bundle = replace(
            bundle,
            cost_rule_set=replace(
                bundle.cost_rule_set,
                times=replace(bundle.cost_rule_set.times, effective_to=pressure_at),
            ),
        )
    bid = context.yes_book.best_bid
    assert bid is not None
    pressure = ObservedPressure(
        "synthetic-fee-expiry-boundary",
        context.yes_book.instrument_id,
        bid.price,
        Quantity(bid.quantity.value + ExactDecimal(2)),
        pressure_at,
        pressure_at,
        PressureKind.SYNTHETIC_EXECUTION,
        "e" * 64,
    )
    evidence = replace(evidence, markets=(market,), pressures=(PressureInput(2, pressure),))
    artifacts = PaperArtifacts(tmp_path / "output", maximum_bytes=config.maximum_output_bytes)
    session = ScenarioSession(
        config,
        QueueScenario.CONSERVATIVE,
        evidence,
        artifacts,
        source_run_id=f"synthetic-pressure-fee-{fee_state}",
    )
    view = CanonicalMarketView(
        bundle,
        prove_binary_complement(bundle),
        (context.yes_book, context.no_book),
        context.assessment,
        now,
        False,
    )
    views = {bundle.market.market_id: view}
    try:
        session.process(seq=0, now=now, views=views, dirty=True, source_valid=True)
        ack = UtcTimestamp(now.nanoseconds_since_epoch + config.execution.submission_latency_ns)
        session.process(seq=1, now=ack, views=views, dirty=False, source_valid=True)
        assert len(session.simulator.active_orders()) == 2
        assert all(
            order.state is PaperOrderState.RESTING for order in session.simulator.active_orders()
        )
        yes = next(
            order
            for order in session.simulator.active_orders()
            if order.intent.outcome is Outcome.YES
        )
        assert yes.intent.price == pressure.price
        assert yes.queue_ahead == bid.quantity
        before = session.ledger.snapshot()

        if fee_state == "valid":
            session.process(seq=2, now=pressure_at, views=views, dirty=False, source_valid=True)
            assert not session.risk.halted
            assert session.ledger.positions()[0].quantity == Quantity.parse("2")
            assert session.ledger.cash == ExactDecimal.parse("999.2")
            assert session.ledger.reserved_cash == ExactDecimal.parse("3.2")
            assert session.simulator.pressure_results()[0].simulated_filled == Quantity.parse("2")
        else:
            with pytest.raises(
                ValueError, match="fee reference unavailable; reservations retained"
            ):
                session.process(seq=2, now=pressure_at, views=views, dirty=False, source_valid=True)
            assert session.risk.halted_reason == "EXECUTION_FEE_REFERENCE_UNAVAILABLE"
            assert session.ledger.snapshot() == before
            assert session.ledger.reserved_cash == ExactDecimal(4)
            assert not session.ledger.positions()
            assert not session.simulator.pressure_results()
            assert all(
                order.state is PaperOrderState.CANCEL_REQUESTED
                for order in session.simulator.active_orders()
            )
            assert not any(event.kind.value == "FILL" for event in session.simulator.events())
    finally:
        artifacts.close_incomplete()
