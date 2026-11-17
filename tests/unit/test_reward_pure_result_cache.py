"""Repeated exact reward inputs may reuse a result across immutable snapshots."""

from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
from polymarket_pair_maker.core.ids import CanonicalEventId, MarketId
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics import rewards as module
from polymarket_pair_maker.economics.rewards import RewardEstimate, RewardScoreCache
from polymarket_pair_maker.state.order_books import (
    BookHealth,
    BookLevel,
    ImmutableOrderBookSnapshot,
)

from .test_paper_economics import DAY, NOW, book, order, reward_rules


def test_metadata_only_snapshots_do_not_repeat_exact_reward_evaluation(
    monkeypatch: pytest.MonkeyPatch,
):
    yes, no = book("YES"), book("NO")
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = reward_rules()
    expected = module.estimate_reward_snapshot(yes, no, own, rules, NOW)
    original = cast(Callable[..., RewardEstimate], vars(module)["_evaluate_reward_snapshot"])
    calls = 0

    def observed(*args: object, **kwargs: object) -> RewardEstimate:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_evaluate_reward_snapshot", observed)
    cache = RewardScoreCache()
    for generation in (1, 2, 3):
        actual = cache.estimate(
            replace(yes, generation=generation),
            replace(no, generation=generation),
            own,
            rules,
            NOW,
        )
        assert actual == expected
        assert canonical_json_bytes(actual) == canonical_json_bytes(expected)
    assert calls == 1
    assert cache.hits == 0
    assert cache.misses == 3
    assert cache.pure_cache_hits == 2
    assert cache.pure_cache_misses == 1
    assert cache.cached_pure_entries == 1


@pytest.mark.parametrize("metadata", ["trade", "source", "past_update", "venue_time"])
def test_unread_snapshot_metadata_preserves_complete_reward_result(metadata: str):
    yes, no = book("YES"), book("NO")
    own, rules = (order("own", "YES", "BUY", "0.49"),), reward_rules()
    cache = RewardScoreCache()
    initial = cache.estimate(yes, no, own, rules, NOW, depth_complete=True)
    if metadata == "trade":
        changed = replace(yes, last_trade_price=Price.parse("0.01"))
    elif metadata == "source":
        changed = replace(yes, last_source_event_id=CanonicalEventId("different-source"))
    elif metadata == "past_update":
        changed = replace(yes, last_valid_update_time=UtcTimestamp(0))
    else:
        changed = replace(yes, last_venue_timestamp=UtcTimestamp(0))
    actual = cache.estimate(changed, replace(no), own, rules, NOW, depth_complete=True)
    assert actual == initial
    assert actual == module.estimate_reward_snapshot(
        changed, no, own, rules, NOW, depth_complete=True
    )
    assert canonical_json_bytes(actual) == canonical_json_bytes(initial)
    assert cache.pure_cache_hits == 1


@pytest.mark.parametrize(
    "change",
    [
        "instrument",
        "market",
        "health_string",
        "future_update",
        "depth_quantity",
        "depth_price",
        "order",
        "duplicate_order",
        "rule_version",
        "depth_coverage",
    ],
)
def test_every_read_reward_input_invalidates_pure_reuse(change: str):
    yes = book("YES", (("0.49", "100"), ("0.48", "100")))
    no = book("NO")
    own, rules = (order("own", "YES", "BUY", "0.49"),), reward_rules()
    cache = RewardScoreCache()
    initial = cache.estimate(yes, no, own, rules, NOW, depth_complete=True)
    depth_complete = True
    changed = replace(yes)
    if change == "instrument":
        changed = replace(yes, instrument_id=no.instrument_id)
    elif change == "market":
        changed = replace(yes, market_id=MarketId("other-market"))
    elif change == "health_string":
        # StrEnum equality must not defeat the evaluator's identity check.
        changed = replace(yes, health=cast(BookHealth, "HEALTHY"))
    elif change == "future_update":
        changed = replace(yes, last_valid_update_time=UtcTimestamp(NOW.nanoseconds_since_epoch + 1))
    elif change == "depth_quantity":
        changed = replace(
            yes,
            bids=(yes.bids[0], replace(yes.bids[1], quantity=Quantity.parse("200"))),
        )
    elif change == "depth_price":
        changed = replace(yes, bids=(yes.bids[0], replace(yes.bids[1], price=Price.parse("0.47"))))
    elif change == "order":
        own = (replace(own[0], price=Price.parse("0.48")),)
    elif change == "duplicate_order":
        own = (*own, own[0])
    elif change == "rule_version":
        rules = replace(rules, evidence=replace(rules.evidence, version="other-version"))
    else:
        depth_complete = False
    actual = cache.estimate(changed, replace(no), own, rules, NOW, depth_complete=depth_complete)
    expected = module.estimate_reward_snapshot(
        changed, no, own, rules, NOW, depth_complete=depth_complete
    )
    assert actual == expected
    assert canonical_json_bytes(actual) == canonical_json_bytes(expected)
    assert actual != initial
    assert cache.pure_cache_hits == 0
    assert cache.pure_cache_misses == 2
    if change == "health_string":
        assert "UNHEALTHY_REWARD_BOOK" in actual.reasons
    if change == "depth_quantity":
        assert initial.competitor_side_mass_upper is not None
        assert actual.competitor_side_mass_upper is not None
        assert (
            actual.competitor_side_mass_upper.as_fraction()
            - initial.competitor_side_mass_upper.as_fraction()
        ) == ExactDecimal(100).as_fraction() / 9


def test_time_boundaries_are_rechecked_before_pure_reuse():
    yes, no = book("YES"), book("NO")
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = replace(reward_rules(), minimum_age_ns=10)
    cache = RewardScoreCache()
    for elapsed in (0, 9, 10):
        at = UtcTimestamp(NOW.nanoseconds_since_epoch + elapsed)
        actual = cache.estimate(replace(yes), replace(no), own, rules, at)
        assert actual == module.estimate_reward_snapshot(yes, no, own, rules, at)
        assert actual.own_score is not None
        assert (actual.own_score.numerator == 0) == (elapsed < 10)
    expired = cache.estimate(replace(yes), replace(no), own, rules, UtcTimestamp(DAY * 4))
    assert expired.own_score is None
    assert "RULE_EXPIRED" in expired.reasons
    assert cache.pure_cache_hits == 1
    assert cache.pure_cache_misses == 3


def test_replaced_pure_cell_reappearance_recomputes_without_scenario_sharing():
    yes, no = book("YES"), book("NO")
    other = book("YES", (("0.48", "100"),), (("0.52", "100"),))
    rules = reward_rules()
    first, second = RewardScoreCache(capacity=1), RewardScoreCache(capacity=1)
    for current in (yes, other, replace(yes)):
        actual = first.estimate(current, no, (), rules, NOW)
        assert actual == module.estimate_reward_snapshot(current, no, (), rules, NOW)
        assert first.cached_pure_entries == 1
        assert first.cached_entries == 1
        assert second.cached_entries == 0
        assert second.cached_pure_entries == 0
    assert first.pure_cache_misses == 3
    assert first.pure_cache_hits == 0


def test_mutable_book_graph_bypasses_and_clears_new_pure_cell():
    yes, no = book("YES"), book("NO")
    rules = reward_rules()
    cache = RewardScoreCache(capacity=1)
    cache.estimate(yes, no, (), rules, NOW)
    levels = list(yes.bids)
    mutable = replace(yes, bids=cast(tuple[BookLevel, ...], levels))
    for quantity in ("100", "200"):
        levels[0] = replace(levels[0], quantity=Quantity.parse(quantity))
        # Distinct snapshots keep this focused on the new cross-snapshot path.
        changed = replace(mutable)
        actual = cache.estimate(changed, no, (), rules, NOW, depth_complete=True)
        assert actual == module.estimate_reward_snapshot(
            changed, no, (), rules, NOW, depth_complete=True
        )
        assert cache.cached_pure_entries == 0
    assert cache.pure_cache_bypasses == 2


def test_unknown_snapshot_subclass_uses_original_exact_path():
    class UnknownSnapshot(ImmutableOrderBookSnapshot):
        pass

    yes, no = book("YES"), book("NO")
    unknown = UnknownSnapshot(
        **{
            name: getattr(yes, name)
            for name, field in yes.__dataclass_fields__.items()
            if field.init
        }
    )
    cache = RewardScoreCache()
    actual = cache.estimate(unknown, no, (), reward_rules(), NOW)
    assert actual == module.estimate_reward_snapshot(unknown, no, (), reward_rules(), NOW)
    assert cache.cached_pure_entries == 0
    assert cache.pure_cache_bypasses == 1


@pytest.mark.parametrize("extra", [0, 1])
def test_actual_pure_level_bound_accepts_boundary_and_bypasses_excess(extra: int):
    yes, no = book("YES"), book("NO")
    # An unavailable multiplier avoids unnecessary score work while all levels
    # still pass the real midpoint validation and immutable-graph guard.
    rules = replace(reward_rules(), multiplier=None)
    count = 16_384 + extra
    yes = replace(yes, bids=(yes.bids[0],) * (count - 3))
    cache = RewardScoreCache()
    for current in (yes, replace(yes)):
        actual = cache.estimate(current, no, (), rules, NOW)
        assert actual == module.estimate_reward_snapshot(current, no, (), rules, NOW)
        assert "REWARD_MULTIPLIER_UNKNOWN" in actual.reasons
    assert cache.cached_pure_entries == (1 if extra == 0 else 0)
    assert cache.pure_cache_hits == (1 if extra == 0 else 0)
    assert cache.pure_cache_bypasses == (0 if extra == 0 else 2)


def test_oversized_nonbook_evidence_cannot_remain_in_pure_cell():
    yes, no = book("YES"), book("NO")
    rules = reward_rules()
    cache = RewardScoreCache()
    cache.estimate(yes, no, (), rules, NOW)
    huge = replace(rules, evidence=replace(rules.evidence, sources=("x" * 65_536,)))
    for current in (replace(yes), replace(yes)):
        assert cache.estimate(current, no, (), huge, NOW) == module.estimate_reward_snapshot(
            current, no, (), huge, NOW
        )
        assert cache.cached_pure_entries == 0
    assert cache.pure_cache_bypasses == 2


def test_mutable_rule_version_cannot_alias_a_new_snapshot_cached_result():
    yes, no = book("YES"), book("NO")
    version = ["old-version"]
    rules = reward_rules()
    malformed = replace(rules, evidence=replace(rules.evidence, version=cast(str, version)))
    cache = RewardScoreCache(capacity=1)
    first = cache.estimate(yes, no, (), malformed, NOW)
    assert cache.cached_pure_entries == 0
    version[0] = "changed-version"
    assert cast(list[str], cast(object, first.rule_version)) == ["changed-version"]
    fresh = replace(rules, evidence=replace(rules.evidence, version=cast(str, ["old-version"])))
    changed = replace(yes)
    actual = cache.estimate(changed, replace(no), (), fresh, NOW)
    assert actual == module.estimate_reward_snapshot(changed, no, (), fresh, NOW)
    assert cast(list[str], cast(object, actual.rule_version)) == ["old-version"]
    assert cache.cached_pure_entries == 0
    assert cache.pure_cache_bypasses == 2


def test_unaudited_numeric_subclass_cannot_hide_changed_reward_arithmetic():
    class StatefulMultiplier(ExactDecimal):
        __slots__ = ("factor",)
        factor: int

        def as_fraction(self) -> Fraction:
            return super().as_fraction() * self.factor

    yes, no = book("YES"), book("NO")
    own = (order("own", "YES", "BUY", "0.49"),)
    rules = reward_rules()
    cache = RewardScoreCache(capacity=1)
    results: list[RewardEstimate] = []
    for factor in (1, 2):
        multiplier = StatefulMultiplier(1)
        object.__setattr__(multiplier, "factor", factor)
        changed_rules = replace(rules, multiplier=multiplier)
        changed = replace(yes)
        expected = module.estimate_reward_snapshot(changed, no, own, changed_rules, NOW)
        actual = cache.estimate(changed, replace(no), own, changed_rules, NOW)
        assert actual == expected
        results.append(actual)
    assert results[0] != results[1]
    assert cache.cached_pure_entries == 0
    assert cache.pure_cache_bypasses == 2


@pytest.mark.parametrize("field", ["version", "unresolved_reason"])
def test_equal_canonical_enum_and_string_inputs_keep_exact_output_types(field: str):
    yes, no = book("YES"), book("NO")
    rules = reward_rules()
    if field == "version":
        tagged = replace(rules, evidence=replace(rules.evidence, version=BookHealth.HEALTHY))
        plain = replace(rules, evidence=replace(rules.evidence, version="HEALTHY"))
    else:
        tagged = replace(
            rules, evidence=replace(rules.evidence, unresolved_requirements=(BookHealth.HEALTHY,))
        )
        plain = replace(
            rules, evidence=replace(rules.evidence, unresolved_requirements=("HEALTHY",))
        )
    assert canonical_json_bytes(tagged) == canonical_json_bytes(plain)
    cache = RewardScoreCache(capacity=1)
    cache.estimate(yes, no, (), tagged, NOW)
    changed = replace(yes)
    actual = cache.estimate(changed, replace(no), (), plain, NOW)
    expected = module.estimate_reward_snapshot(changed, no, (), plain, NOW)
    assert actual == expected
    if field == "version":
        assert type(actual.rule_version) is str
    else:
        assert type(actual.reasons[0]) is str
    assert cache.pure_cache_hits == 0
