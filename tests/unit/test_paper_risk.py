from dataclasses import replace

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity
from polymarket_pair_maker.core.ids import AssetId, InstrumentId, MarketId
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.paper.ledger import PaperLedger
from polymarket_pair_maker.paper.orders import Outcome, PaperOrderId, PaperOrderIntent
from polymarket_pair_maker.risk import RiskController, RiskLimits


def _intent(
    outcome: Outcome = Outcome.YES, *, quantity: str = "10", market: str = "m"
) -> PaperOrderIntent:
    return PaperOrderIntent(
        PaperOrderId(f"{market}-{outcome}"),
        MarketId(market),
        InstrumentId(f"{market}-{outcome}"),
        "parent",
        outcome,
        Price.parse("0.4"),
        Quantity.parse(quantity),
        UtcTimestamp(0),
        UtcTimestamp(10),
    )


def _ledger() -> PaperLedger:
    return PaperLedger(Money.parse("1000"), AssetId("pUSD"))


def test_pair_directional_risk_uses_independent_leg_fill_not_cancellation_assumption():
    risk = RiskController()
    intents = (_intent(quantity="26"), _intent(Outcome.NO, quantity="26"))
    decision = risk.assess(_ledger(), intents)
    assert decision.directional_inventory_bound == ExactDecimal(26)
    assert "DIRECTIONAL_INVENTORY_LIMIT" in decision.reasons
    assert not decision.allowed


def test_pair_capacity_is_atomic_when_market_limit_exceeded():
    ledger = _ledger()
    risk = RiskController(replace(RiskLimits(), maximum_market_commitment=Money.parse("7")))
    before = ledger.snapshot()
    decision = risk.reserve(ledger, (_intent(), _intent(Outcome.NO)))
    assert "MARKET_COMMITMENT_LIMIT" in decision.reasons
    assert ledger.snapshot() == before


def test_outstanding_orders_count_pending_uncertain_capacity():
    ledger = _ledger()
    ledger.reserve_batch((_intent(), _intent(Outcome.NO)))
    reasons = RiskController().assess(ledger, (_intent(),)).reasons
    assert "OUTSTANDING_ORDER_LIMIT" in reasons
    assert "ONE_BID_PER_OUTCOME" in reasons
    assert "DUPLICATE_ORDER_ID" in reasons


def test_one_market_at_a_time_including_inventory():
    ledger = _ledger()
    intent = _intent()
    ledger.reserve_batch((intent,))
    ledger.apply_buy_fill("f", intent.order_id, intent.quantity, intent.price, Money.parse("0"))
    reasons = RiskController().assess(ledger, (_intent(market="other"),)).reasons
    assert "ACTIVE_MARKET_LIMIT" in reasons


def test_session_loss_is_latched_and_cannot_be_cleared_by_recovery():
    ledger = _ledger()
    risk = RiskController()
    assert not risk.observe_wealth(ledger, Money.parse("975"))
    assert not risk.observe_wealth(ledger, Money.parse("1001"))
    assert risk.halted_reason == "MAXIMUM_SESSION_LOSS"
    assert not risk.assess(ledger, (_intent(),)).allowed


def test_parent_and_total_limits_consider_held_basis_and_pending_buys():
    ledger = _ledger()
    yes = _intent()
    ledger.reserve_batch((yes,))
    ledger.apply_buy_fill("f", yes.order_id, yes.quantity, yes.price, Money.parse("0"))
    limits = replace(
        RiskLimits(),
        maximum_market_commitment=Money.parse("5"),
        maximum_parent_commitment=Money.parse("5"),
        maximum_total_commitment=Money.parse("5"),
    )
    reasons = RiskController(limits).assess(ledger, (_intent(Outcome.NO),)).reasons
    assert "TOTAL_COMMITMENT_LIMIT" in reasons
    assert "PARENT_EVENT_COMMITMENT_LIMIT" in reasons
    assert "MARKET_COMMITMENT_LIMIT" in reasons


def test_quote_age_and_market_catalyst_exit_controls():
    risk = RiskController()
    intent = replace(_intent(), expires_at=UtcTimestamp(60_000_000_001))
    assert "MAXIMUM_QUOTE_AGE" in risk.assess(_ledger(), (intent,)).reasons
    reasons = risk.market_reasons(
        now=UtcTimestamp(0),
        resolution_at=UtcTimestamp(1),
        catalyst_excluded=True,
        executable_exit_depth=ExactDecimal(1),
        minimum_exit_depth=ExactDecimal(2),
    )
    assert reasons == ("RESOLUTION_TIME_RISK", "EVENT_CATALYST_EXCLUDED", "INSUFFICIENT_EXIT_DEPTH")


@pytest.mark.parametrize("markets", [0, 2, 100])
def test_one_market_configuration_bound(markets: int):
    with pytest.raises(ValueError, match="one market"):
        replace(RiskLimits(), maximum_active_markets=markets)


def test_invalid_limit_relationship_rejected():
    with pytest.raises(ValueError, match="market <= parent <= total"):
        replace(RiskLimits(), maximum_market_commitment=Money.parse("101"))
