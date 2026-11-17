from dataclasses import replace

import pytest

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
from polymarket_pair_maker.semantic import BinarySemanticProof, ProofStatus, RelationType


def _intent(
    outcome: Outcome = Outcome.YES, price: str = "0.4", quantity: str = "10"
) -> PaperOrderIntent:
    return PaperOrderIntent(
        PaperOrderId(outcome.value),
        MarketId("m"),
        InstrumentId(outcome.value),
        "parent",
        outcome,
        Price.parse(price),
        Quantity.parse(quantity),
        UtcTimestamp(0),
        UtcTimestamp(10),
    )


def _proof() -> BinarySemanticProof:
    return BinarySemanticProof(
        RelationType.COMPLEMENTARY,
        ProofStatus.PROVEN,
        MarketId("m"),
        "condition",
        InstrumentId("YES"),
        InstrumentId("NO"),
        AssetId("pUSD"),
        Money.parse("1"),
        ContractSemanticsVersionId("v1"),
        ("verified-fixture",),
        None,
    )


def _paired() -> PaperLedger:
    ledger = PaperLedger(Money.parse("1000"), AssetId("pUSD"))
    yes, no = _intent(), _intent(Outcome.NO, "0.5")
    ledger.reserve_batch((yes, no))
    for intent in (yes, no):
        ledger.apply_buy_fill(
            intent.outcome.value, intent.order_id, intent.quantity, intent.price, Money.parse("0")
        )
    return ledger


def test_atomic_reservation_failure_leaves_ledger_unchanged():
    ledger = PaperLedger(Money.parse("5"), AssetId("pUSD"))
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="all potentially executable"):
        ledger.reserve_batch((_intent(), _intent(Outcome.NO)))
    assert ledger.snapshot() == before


def test_reserved_cash_cannot_be_reused():
    ledger = PaperLedger(Money.parse("5"), AssetId("pUSD"))
    ledger.reserve_batch((_intent(),))
    with pytest.raises(ValueError, match="insufficient"):
        ledger.reserve_batch((_intent(Outcome.NO),))
    assert ledger.available_cash == ExactDecimal(1)


def test_fee_reserve_partial_fill_and_price_improvement_exactly_reconcile():
    ledger = PaperLedger(Money.parse("10"), AssetId("pUSD"))
    intent = replace(_intent(), fee_reserve=Money.parse("0.1"))
    ledger.reserve_batch((intent,))
    ledger.apply_buy_fill(
        "f1", intent.order_id, Quantity.parse("3"), Price.parse("0.39"), Money.parse("0.03")
    )
    assert ledger.cash == ExactDecimal.parse("8.8")
    assert ledger.reserved_cash == ExactDecimal.parse("2.87")
    assert ledger.available_cash == ExactDecimal.parse("5.93")
    ledger.apply_buy_fill(
        "f2", intent.order_id, Quantity.parse("7"), Price.parse("0.4"), Money.parse("0.05")
    )
    assert not ledger.reserved_cash
    assert ledger.snapshot().fees_paid == Money.parse("0.08")
    assert ledger.snapshot().acquisition_basis == Money.parse("4.05")
    ledger.check_invariants()


def test_bad_fee_or_fill_is_atomic_and_duplicate_payload_checked():
    ledger = PaperLedger(Money.parse("10"), AssetId("pUSD"))
    intent = _intent()
    ledger.reserve_batch((intent,))
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="fee capacity"):
        ledger.apply_buy_fill(
            "f", intent.order_id, Quantity.parse("1"), intent.price, Money.parse("0.01")
        )
    assert ledger.snapshot() == before
    ledger.apply_buy_fill("f", intent.order_id, Quantity.parse("1"), intent.price, Money.parse("0"))
    with pytest.raises(ValueError, match="contradictory duplicate"):
        ledger.apply_buy_fill(
            "f", intent.order_id, Quantity.parse("2"), intent.price, Money.parse("0")
        )


def test_estimates_do_not_enter_cash_and_duplicate_accrual_is_idempotent():
    ledger = _paired()
    before = ledger.cash
    assert ledger.estimate_incentives("sample", rewards=Money.parse("10"), rebates=Money.parse("2"))
    assert not ledger.estimate_incentives(
        "sample", rewards=Money.parse("10"), rebates=Money.parse("2")
    )
    assert ledger.cash == before
    assert ledger.snapshot().verified_payouts == Money.parse("0")
    assert ledger.snapshot().estimated_liquidity_rewards == Money.parse("10")


@pytest.mark.parametrize("winner", [Outcome.YES, Outcome.NO])
def test_complete_set_alternative_valuation_and_settlement_never_double_count(winner: Outcome):
    ledger = _paired()
    valuation = ledger.paired_settlement_value(_proof())
    assert valuation.net_wealth == Money.parse("1001")
    assert ledger.cash == ExactDecimal(991)
    assert ledger.settle(
        "resolution", _proof(), winning_outcome=winner, evidence_id="synthetic-resolution"
    )
    assert not ledger.settle(
        "resolution", _proof(), winning_outcome=winner, evidence_id="synthetic-resolution"
    )
    assert ledger.cash == ExactDecimal(1001)
    assert not ledger.positions()
    assert ledger.paired_settlement_value(_proof()).net_wealth == Money.parse("1001")
    assert ledger.snapshot().realized_trading_pnl == Money.parse("1")


def test_unmatched_directional_stress_and_losing_settlement():
    ledger = PaperLedger(Money.parse("1000"), AssetId("pUSD"))
    intent = _intent()
    ledger.reserve_batch((intent,))
    ledger.apply_buy_fill("f", intent.order_id, intent.quantity, intent.price, Money.parse("0"))
    stress = ledger.paired_settlement_value(_proof())
    assert stress.net_wealth == Money.parse("996")
    assert stress.unresolved == (InstrumentId("YES"),)
    ledger.settle("resolution", _proof(), winning_outcome=Outcome.NO, evidence_id="synthetic")
    assert ledger.snapshot().realized_trading_pnl == Money.parse("-4")


def test_settlement_rejects_unproven_assets_and_pending_orders():
    ledger = _paired()
    with pytest.raises(ValueError, match="same-asset"):
        ledger.settle(
            "bad",
            replace(_proof(), payout_asset_id=AssetId("other")),
            winning_outcome=Outcome.YES,
            evidence_id="synthetic",
        )
    with pytest.raises(ValueError, match="resolution evidence"):
        ledger.settle("missing", _proof(), winning_outcome=Outcome.YES, evidence_id="")
    ledger.reserve_batch((_intent(),))
    with pytest.raises(ValueError, match="terminal reconciliation"):
        ledger.settle("pending", _proof(), winning_outcome=Outcome.YES, evidence_id="synthetic")


def test_mark_to_market_is_separate_from_cash_and_missing_mark_zero_stress():
    ledger = _paired()
    before = ledger.snapshot()
    value = ledger.mark_to_market({InstrumentId("YES"): Price.parse("0.6")})
    assert value.net_wealth == Money.parse("997")
    assert value.unresolved == (InstrumentId("NO"),)
    assert ledger.snapshot() == before


def test_ledger_record_capacity_fail_closed_without_mutating():
    ledger = PaperLedger(Money.parse("1000"), AssetId("pUSD"), maximum_records=1)
    intent = _intent()
    ledger.reserve_batch((intent,))
    ledger.apply_buy_fill(
        "one", intent.order_id, Quantity.parse("1"), intent.price, Money.parse("0")
    )
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="capacity"):
        ledger.apply_buy_fill(
            "two", intent.order_id, Quantity.parse("1"), intent.price, Money.parse("0")
        )
    assert ledger.snapshot() == before


def test_same_token_cannot_be_reserved_as_both_outcomes_atomically():
    ledger = PaperLedger(Money.parse("1000"), AssetId("pUSD"))
    yes = _intent()
    no = replace(_intent(Outcome.NO), instrument_id=yes.instrument_id)
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="both outcome legs"):
        ledger.reserve_batch((yes, no))
    assert ledger.snapshot() == before
