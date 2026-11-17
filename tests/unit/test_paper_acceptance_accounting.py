"""Task 3 adversarial regressions with independent minimal fixtures."""

from dataclasses import replace

import pytest

from polymarket_pair_maker.core.exact import Money, Price, Quantity
from polymarket_pair_maker.core.health import BookHealth
from polymarket_pair_maker.core.ids import (
    AssetId,
    ContractSemanticsVersionId,
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
)
from polymarket_pair_maker.paper.ledger import PaperLedger
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderId, PaperOrderIntent
from polymarket_pair_maker.risk.limits import RiskController
from polymarket_pair_maker.semantic import BinarySemanticProof, ProofStatus, RelationType
from polymarket_pair_maker.state.order_books import BookLevel, ImmutableOrderBookSnapshot


def _intent(name: str = "yes") -> PaperOrderIntent:
    return PaperOrderIntent(
        PaperOrderId(name),
        MarketId("market"),
        InstrumentId(name),
        "parent",
        Outcome.YES if name == "yes" else Outcome.NO,
        Price.parse("0.4"),
        Quantity.parse("3"),
        UtcTimestamp(0),
        UtcTimestamp(1000),
    )


def _book() -> ImmutableOrderBookSnapshot:
    bid = BookLevel(Price.parse("0.4"), Quantity.parse("5"))
    ask = BookLevel(Price.parse("0.8"), Quantity.parse("5"))
    return ImmutableOrderBookSnapshot(
        InstrumentId("yes"),
        MarketId("market"),
        1,
        BookHealth.HEALTHY,
        None,
        (bid,),
        (ask,),
        bid,
        ask,
        None,
        None,
        None,
        None,
        MarketVersionId("market-v1"),
        PrecisionRuleVersionId("precision-v1"),
        CostRuleSetVersionId("cost-v1"),
        UtcTimestamp(0),
        UtcTimestamp(0),
    )


def _ledger() -> PaperLedger:
    return PaperLedger(Money.parse("1000"), AssetId("pUSD"))


def _acquire(ledger: PaperLedger, name: str = "yes") -> None:
    intent = _intent(name)
    ledger.reserve_batch((intent,))
    ledger.apply_buy_fill(name, intent.order_id, intent.quantity, intent.price, Money.parse("0"))


def _proof() -> BinarySemanticProof:
    return BinarySemanticProof(
        RelationType.COMPLEMENTARY,
        ProofStatus.PROVEN,
        MarketId("market"),
        "condition",
        InstrumentId("yes"),
        InstrumentId("no"),
        AssetId("pUSD"),
        Money.parse("1"),
        ContractSemanticsVersionId("proof-v1"),
        ("synthetic-audit",),
        None,
    )


@pytest.mark.parametrize("capacity", ["events", "ledger"])
def test_pressure_failure_preserves_queue_and_all_accounted_state(capacity: str):
    ledger = PaperLedger(
        Money.parse("1000"), AssetId("pUSD"), maximum_records=1 if capacity == "ledger" else 100
    )
    simulator = PaperSimulator(
        ledger,
        RiskController(),
        ExecutionConfig(
            submission_latency_ns=10,
            cancellation_latency_ns=10,
            maximum_events=2 if capacity == "events" else 100,
            maximum_orders=2,
        ),
    )
    simulator.submit((_intent(),), {InstrumentId("yes"): _book()}, UtcTimestamp(0))
    simulator.advance(UtcTimestamp(10), {InstrumentId("yes"): _book()})
    if capacity == "ledger":
        ledger.estimate_incentives("record", rewards=Money.parse("0"), rebates=Money.parse("0"))
    before = (
        simulator.orders(),
        ledger.snapshot(),
        simulator.events(),
        simulator.pressure_results(),
    )
    pressure = ObservedPressure(
        "pressure",
        InstrumentId("yes"),
        Price.parse("0.4"),
        Quantity.parse("7"),
        UtcTimestamp(11),
        UtcTimestamp(11),
        PressureKind.SYNTHETIC_EXECUTION,
        "a" * 64,
    )
    with pytest.raises(ValueError, match="capacity"):
        simulator.process_pressure(pressure)
    assert simulator.risk.halted
    assert (
        simulator.orders(),
        ledger.snapshot(),
        simulator.events(),
        simulator.pressure_results(),
    ) == before


def test_untyped_settlement_outcome_cannot_destroy_winning_inventory():
    ledger = _ledger()
    _acquire(ledger)
    before = ledger.snapshot()
    with pytest.raises(TypeError, match="typed Outcome"):
        ledger.settle(
            "bad",
            _proof(),
            winning_outcome="YES",  # pyright: ignore[reportArgumentType]
            evidence_id="synthetic",
        )
    assert ledger.snapshot() == before
    ledger.settle("valid", _proof(), winning_outcome=Outcome.YES, evidence_id="synthetic")
    assert ledger.cash.to_canonical_string() == "1001.8"


@pytest.mark.parametrize("contradiction", ["market", "outcome"])
def test_complement_valuation_checks_held_position_identity(contradiction: str):
    ledger = _ledger()
    _acquire(ledger)
    _acquire(ledger, "no")
    proof = _proof()
    proof = (
        replace(proof, market_id=MarketId("wrong-market"))
        if contradiction == "market"
        else replace(
            proof, yes_instrument_id=InstrumentId("no"), no_instrument_id=InstrumentId("yes")
        )
    )
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="identity contradicts"):
        ledger.paired_settlement_value(proof)
    assert ledger.snapshot() == before


def test_executable_valuation_cannot_accept_fabricated_quote_proceeds():
    ledger = _ledger()
    _acquire(ledger)
    quote = ledger.quote_liquidation(
        _book(),
        Quantity.parse("3"),
        now=UtcTimestamp(1),
        maximum_book_age_ns=100,
        fee_evaluator=lambda _p, _q: Money.parse("0"),
    )
    with pytest.raises(ValueError, match="exact reconciliation"):
        ledger.executable_valuation(
            {InstrumentId("yes"): replace(quote, net_proceeds=Money.parse("1000000"))}
        )
    assert ledger.executable_valuation({InstrumentId("yes"): quote}).net_wealth == Money.parse(
        "1000"
    )
