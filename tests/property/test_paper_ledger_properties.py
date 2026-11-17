from hypothesis import given
from hypothesis import strategies as st

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.ids import (
    AssetId,
    ContractSemanticsVersionId,
    InstrumentId,
    MarketId,
)
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.ledger import PaperLedger
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderId, PaperOrderIntent
from polymarket_pair_maker.risk import RiskController
from polymarket_pair_maker.semantic import BinarySemanticProof, ProofStatus, RelationType


@given(
    yes_ticks=st.integers(1, 99),
    no_ticks=st.integers(1, 99),
    quantity=st.integers(1, 25),
    first_fill=st.integers(1, 25),
    yes_wins=st.booleans(),
)
def test_buy_pair_partial_fill_reserve_conservation_settlement_and_idempotency(
    yes_ticks: int, no_ticks: int, quantity: int, first_fill: int, yes_wins: bool
):
    ledger = PaperLedger(Money.parse("1000"), AssetId("pUSD"))
    intents = tuple(
        PaperOrderIntent(
            PaperOrderId(outcome.value),
            MarketId("m"),
            InstrumentId(outcome.value),
            "parent",
            outcome,
            Price(ExactDecimal(ticks, 2)),
            Quantity(ExactDecimal(quantity)),
            UtcTimestamp(0),
            UtcTimestamp(10),
        )
        for outcome, ticks in [(Outcome.YES, yes_ticks), (Outcome.NO, no_ticks)]
    )
    ledger.reserve_batch(intents)
    expected_cost = ExactDecimal(yes_ticks + no_ticks, 2) * ExactDecimal(quantity)
    assert ledger.reserved_cash == expected_cost
    assert RiskController().directional_bound(ledger) == ExactDecimal(quantity)
    for intent in intents:
        partial = Quantity(ExactDecimal(min(first_fill, quantity)))
        ledger.apply_buy_fill(
            intent.outcome.value, intent.order_id, partial, intent.price, Money.parse("0")
        )
        before = ledger.snapshot()
        assert not ledger.apply_buy_fill(
            intent.outcome.value, intent.order_id, partial, intent.price, Money.parse("0")
        )
        assert ledger.snapshot() == before
        remainder = intent.quantity.value - partial.value
        if remainder:
            ledger.apply_buy_fill(
                f"rest-{intent.outcome}",
                intent.order_id,
                Quantity(remainder),
                intent.price,
                Money.parse("0"),
            )
        ledger.check_invariants()
        assert ledger.available_cash >= ExactDecimal.zero()
    assert not ledger.reserved_cash
    proof = BinarySemanticProof(
        RelationType.COMPLEMENTARY,
        ProofStatus.PROVEN,
        MarketId("m"),
        "condition",
        InstrumentId("YES"),
        InstrumentId("NO"),
        AssetId("pUSD"),
        Money.parse("1"),
        ContractSemanticsVersionId("proof"),
        ("synthetic",),
        None,
    )
    expected = ExactDecimal(1000) - expected_cost + ExactDecimal(quantity)
    assert ledger.paired_settlement_value(proof).net_wealth == Money(expected)
    ledger.settle(
        "settled",
        proof,
        winning_outcome=Outcome.YES if yes_wins else Outcome.NO,
        evidence_id="synthetic",
    )
    assert ledger.cash == expected
    assert not ledger.positions()
    assert ledger.snapshot().realized_trading_pnl == Money(ExactDecimal(quantity) - expected_cost)
    ledger.check_invariants()
