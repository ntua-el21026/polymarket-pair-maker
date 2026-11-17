from dataclasses import replace

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Rate
from polymarket_pair_maker.core.ids import AssetId, MarketId
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics.fees import FeeRules, canonical_fee_reasons
from polymarket_pair_maker.reference import CostStatus, ReferenceBundle
from tests.unit.test_paper_strategy import strategy_context


def scoped_fees() -> tuple[ReferenceBundle, FeeRules, UtcTimestamp]:
    context = strategy_context()
    assert context.fee_rules is not None
    rate = ExactDecimal.parse("0.07")
    bundle = replace(
        context.bundle,
        cost_rule_set=replace(
            context.bundle.cost_rule_set,
            rate=Rate(rate),
            exponent=1,
        ),
    )
    return bundle, replace(context.fee_rules, taker_rate=rate), context.as_of


def test_matching_current_fee_reference_is_admissible():
    bundle, rules, now = scoped_fees()
    assert canonical_fee_reasons(bundle, rules, now, 10**15) == ()


@pytest.mark.parametrize("change", ["rate", "asset", "exponent", "quantum", "maker", "status"])
def test_current_canonical_fee_changes_override_old_offline_rule(change: str):
    bundle, rules, now = scoped_fees()
    cost = bundle.cost_rule_set
    if change == "rate":
        cost = replace(cost, rate=Rate.parse("0.05"))
    elif change == "asset":
        cost = replace(cost, fee_asset_id=AssetId("other-asset"))
    elif change == "exponent":
        cost = replace(cost, exponent=2)
    elif change == "quantum":
        cost = replace(cost, fee_quantum=Money.parse("0.0001"))
    elif change == "maker":
        cost = replace(cost, taker_only=False)
    else:
        cost = replace(cost, status=CostStatus.INDETERMINATE, reason="unknown update")
    result = canonical_fee_reasons(replace(bundle, cost_rule_set=cost), rules, now, 10**15)
    assert "CANONICAL_FEE_REFERENCE_CONTRADICTION" in result


@pytest.mark.parametrize("change", ["observed_future", "effective_future", "expired", "superseded"])
def test_canonical_fee_time_validity_is_enforced(change: str):
    bundle, rules, now = scoped_fees()
    times = bundle.cost_rule_set.times
    if change == "observed_future":
        times = replace(times, observed_at=UtcTimestamp(now.nanoseconds_since_epoch + 1))
    elif change == "effective_future":
        times = replace(times, effective_from=UtcTimestamp(now.nanoseconds_since_epoch + 1))
    elif change == "expired":
        times = replace(times, effective_to=now)
    else:
        times = replace(times, superseded_at=now)
    changed = replace(bundle, cost_rule_set=replace(bundle.cost_rule_set, times=times))
    assert "CANONICAL_REFERENCE_NOT_CURRENT" in canonical_fee_reasons(changed, rules, now, 10**15)


def test_canonical_and_offline_fee_reference_age_fail_independently():
    bundle, rules, now = scoped_fees()
    result = canonical_fee_reasons(bundle, rules, now, 0)
    assert "STALE_CANONICAL_REFERENCE" in result
    assert "STALE_ECONOMIC_REFERENCE" in result
    refreshed = replace(
        bundle,
        cost_rule_set=replace(
            bundle.cost_rule_set,
            times=replace(bundle.cost_rule_set.times, observed_at=now),
        ),
    )
    result = canonical_fee_reasons(refreshed, rules, now, 0)
    assert "STALE_CANONICAL_REFERENCE" not in result
    assert "STALE_ECONOMIC_REFERENCE" in result


def test_wrong_market_and_incomplete_instrument_scope_are_rejected():
    bundle, rules, now = scoped_fees()
    wrong_market = replace(rules, evidence=replace(rules.evidence, market_id=MarketId("other")))
    assert "ECONOMIC_REFERENCE_MARKET_MISMATCH" in canonical_fee_reasons(
        bundle, wrong_market, now, 10**15
    )
    missing_instrument = replace(
        bundle,
        cost_rule_set=replace(
            bundle.cost_rule_set,
            applicable_instrument_ids=(bundle.instruments[0].instrument_id,),
        ),
    )
    assert "CANONICAL_FEE_INSTRUMENT_SCOPE_MISMATCH" in canonical_fee_reasons(
        missing_instrument, rules, now, 10**15
    )


@pytest.mark.parametrize("age", [-1, True])
def test_reference_age_contract_rejects_negative_or_boolean(age: int):
    bundle, rules, now = scoped_fees()
    with pytest.raises(ValueError, match="reference age"):
        canonical_fee_reasons(bundle, rules, now, age)
