"""Whole-side construction reuse must equal transparent per-level validation."""

import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal
from polymarket_pair_maker.core.ids import IngestId
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.normalization import polymarket as module
from polymarket_pair_maker.normalization.models import (
    BookSide,
    CanonicalBookLevel,
    CanonicalEvent,
    OrderBookSnapshotEvent,
)
from polymarket_pair_maker.venues.polymarket.schemas import NativeBookLevel

from .test_paper_strategy import strategy_context
from .test_raw_computation_cache import reference_levels

normalize_levels = cast(Callable[..., tuple[CanonicalBookLevel, ...]], vars(module)["_levels"])


def test_uncached_side_validation_constructs_zero_and_one_once(
    monkeypatch: pytest.MonkeyPatch,
):
    class DerivedNativeLevel(NativeBookLevel):
        pass

    values = tuple(
        NativeBookLevel(ExactDecimal(314000 + index, 6), ExactDecimal(17)) for index in range(100)
    )
    normalize_levels(values, side=BookSide.BID)
    # Unknown native subclasses bypass whole-side reuse while the original
    # independent per-level construction cache remains warm.
    derived = tuple(DerivedNativeLevel(value.price, value.size) for value in values)
    expected = reference_levels(derived, BookSide.BID)
    original_one, original_zero = ExactDecimal.one, ExactDecimal.zero
    calls = {"one": 0, "zero": 0}

    def one(_cls: type[ExactDecimal]) -> ExactDecimal:
        calls["one"] += 1
        return original_one()

    def zero(_cls: type[ExactDecimal]) -> ExactDecimal:
        calls["zero"] += 1
        return original_zero()

    monkeypatch.setattr(ExactDecimal, "one", classmethod(one))
    monkeypatch.setattr(ExactDecimal, "zero", classmethod(zero))
    assert normalize_levels(derived, side=BookSide.BID) == expected
    assert calls == {"one": 1, "zero": 1}


def test_identical_complete_side_does_not_repeat_per_level_lookup(
    monkeypatch: pytest.MonkeyPatch,
):
    values = tuple(
        NativeBookLevel(ExactDecimal(271000 + index, 6), ExactDecimal(23)) for index in range(100)
    )
    expected = reference_levels(values, BookSide.ASK)
    first = normalize_levels(values, side=BookSide.ASK)
    original = cast(Callable[..., object], vars(module)["_level_cache_key"])
    calls = 0

    def observed(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_level_cache_key", observed)
    equivalent = tuple(NativeBookLevel(level.price, level.size) for level in values)
    actual = normalize_levels(equivalent, side=BookSide.ASK)
    assert actual == expected
    assert actual is first
    # The actual-type bound audit does not need a per-level cache lookup key.
    assert calls == 0


@pytest.mark.parametrize("side", [BookSide.BID, BookSide.ASK])
@pytest.mark.parametrize(
    "pairs",
    [
        (("0.4", "5"), ("0.5", "0"), ("0.3", "7")),
        (("0", "5"), ("1", "7")),
        (("-0.1", "2"),),
        (("0.4", "-1"),),
        (("1.01", "2"),),
        (("0.4", "5"), ("0.40", "0")),
    ],
)
def test_side_reuse_preserves_complete_naive_validation(
    side: BookSide, pairs: tuple[tuple[str, str], ...]
):
    prime = (NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(5)),)
    normalize_levels(prime, side=side)
    values = tuple(
        NativeBookLevel(ExactDecimal.parse(price), ExactDecimal.parse(size))
        for price, size in pairs
    )
    try:
        expected = reference_levels(values, side)
    except module.NormalizationError as error:
        message, cause = str(error), type(error.__cause__)
        for _ in range(2):
            with pytest.raises(module.NormalizationError) as caught:
                normalize_levels(values, side=side)
            assert str(caught.value) == message
            assert type(caught.value.__cause__) is cause
    else:
        for _ in range(2):
            actual = normalize_levels(values, side=side)
            assert actual == expected
            assert canonical_json_bytes(actual) == canonical_json_bytes(expected)


def test_complete_changed_side_and_evicted_content_are_rebuilt():
    first = (
        NativeBookLevel(ExactDecimal(31, 2), ExactDecimal(11)),
        NativeBookLevel(ExactDecimal(32, 2), ExactDecimal(12)),
    )
    changed = (replace(first[0], size=ExactDecimal(13)), first[1])
    a = normalize_levels(first, side=BookSide.BID)
    for values in (changed, tuple(reversed(changed)), first):
        actual = normalize_levels(values, side=BookSide.BID)
        assert actual == reference_levels(values, BookSide.BID)
        assert canonical_json_bytes(actual) == canonical_json_bytes(
            reference_levels(values, BookSide.BID)
        )
    assert actual == a
    assert actual is not a
    assert module.normalization_side_cache_info()["currsize"] <= 2


def test_book_sides_and_equal_plain_strings_cannot_share_sorting_results():
    values = (
        NativeBookLevel(ExactDecimal(3, 1), ExactDecimal(1)),
        NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(2)),
    )
    bid = normalize_levels(values, side=BookSide.BID)
    ask = normalize_levels(values, side=BookSide.ASK)
    plain = normalize_levels(values, side=cast(BookSide, "BID"))
    assert tuple(level.price.value for level in bid) == (ExactDecimal(4, 1), ExactDecimal(3, 1))
    assert tuple(level.price.value for level in ask) == (ExactDecimal(3, 1), ExactDecimal(4, 1))
    assert plain == ask
    assert bid != plain
    assert module.normalization_side_cache_info()["currsize"] <= 2


def test_mutable_side_list_is_never_retained_as_a_successful_key():
    values = [NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(20))]
    before = module.normalization_side_cache_info()["bypasses"]
    for size in (20, 21, 20):
        values[0] = replace(values[0], size=ExactDecimal(size))
        actual = normalize_levels(cast(tuple[NativeBookLevel, ...], values), side=BookSide.BID)
        assert actual == reference_levels(tuple(values), BookSide.BID)
        assert actual[0].quantity.value == ExactDecimal(size)
    assert module.normalization_side_cache_info()["bypasses"] - before == 3


def test_unaudited_equal_numeric_subclass_cannot_skip_its_validation():
    class RejectedPrice(ExactDecimal):
        def __eq__(self, other: object) -> bool:
            return (
                isinstance(other, ExactDecimal)
                and self.coefficient == other.coefficient
                and self.scale == other.scale
            )

        def __gt__(self, other: object) -> bool:
            return True

    native = NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(5))
    normalize_levels((native,), side=BookSide.BID)
    derived = (replace(native, price=RejectedPrice(4, 1)),)
    assert derived == (native,)
    with pytest.raises(module.NormalizationError, match="exceeds one"):
        reference_levels(derived, BookSide.BID)
    with pytest.raises(module.NormalizationError, match="exceeds one"):
        normalize_levels(derived, side=BookSide.BID)


@pytest.mark.parametrize("count", [4096, 4097])
def test_actual_side_level_bound_retains_only_complete_bounded_sides(count: int):
    values = tuple(
        NativeBookLevel(ExactDecimal(index, 5), ExactDecimal(1)) for index in range(count)
    )
    expected = reference_levels(values, BookSide.ASK)
    before = module.normalization_side_cache_info()
    first = normalize_levels(values, side=BookSide.ASK)
    second = normalize_levels(
        tuple(NativeBookLevel(v.price, v.size) for v in values), side=BookSide.ASK
    )
    assert first == second == expected
    after = module.normalization_side_cache_info()
    assert after["currsize"] <= 2
    assert after["retained_native_levels"] <= 8192
    if count == 4096:
        assert second is first
        assert after["hits"] - before["hits"] == 1
    else:
        assert second is not first
        assert after["bypasses"] - before["bypasses"] == 2


@pytest.mark.parametrize(
    "value",
    [
        NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(2**513)),
        NativeBookLevel(ExactDecimal(4, 129), ExactDecimal(1)),
    ],
)
def test_direct_native_numeric_bounds_bypass_whole_side_reuse(value: NativeBookLevel):
    before = module.normalization_side_cache_info()["bypasses"]
    for _ in range(2):
        actual = normalize_levels((value,), side=BookSide.BID)
        assert actual == reference_levels((value,), BookSide.BID)
    assert module.normalization_side_cache_info()["bypasses"] - before == 2


def test_reused_side_preserves_each_market_and_raw_input_identity():
    normalizer = module.PolymarketNormalizer()
    events: list[CanonicalEvent] = []
    for name in ("first-market", "second-market"):
        bundle = strategy_context(native_condition_id=name).bundle
        payload = canonical_json_bytes(
            {
                "event_type": "book",
                "market": bundle.market.native_condition_id,
                "asset_id": bundle.instruments[0].native_token_id,
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.60", "size": "10"}],
                "timestamp": "500000",
                "hash": "opaque-unchanged-depth",
            }
        )
        context = module.NormalizationContext(
            IngestId(name),
            hashlib.sha256(payload).hexdigest(),
            UtcTimestamp(500_000_000_000),
            bundle,
        )
        (event,) = normalizer.normalize(payload, context)
        assert event.market_id == bundle.market.market_id
        assert event.instrument_id == bundle.instruments[0].instrument_id
        assert event.source_ingest_ids == (context.source_ingest_id,)
        assert event.source_payload_hashes == (context.source_payload_sha256,)
        events.append(event)
    first, second = events
    assert first.market_id != second.market_id
    assert first.canonical_event_id != second.canonical_event_id
    assert isinstance(first.payload, OrderBookSnapshotEvent)
    assert isinstance(second.payload, OrderBookSnapshotEvent)
    assert first.payload.bids is second.payload.bids
    assert first.payload.asks is second.payload.asks


@pytest.mark.parametrize("invalid", ["source", "missing_bundle", "condition", "token"])
def test_side_reuse_cannot_bypass_current_source_and_reference_checks(invalid: str):
    bundle = strategy_context(native_condition_id="current-reference-market").bundle
    frame = {
        "event_type": "book",
        "market": bundle.market.native_condition_id,
        "asset_id": bundle.instruments[0].native_token_id,
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.60", "size": "10"}],
        "timestamp": "500000",
        "hash": "opaque-unchanged-depth",
    }
    payload = canonical_json_bytes(frame)
    context = module.NormalizationContext(
        IngestId("primed"),
        hashlib.sha256(payload).hexdigest(),
        UtcTimestamp(500_000_000_000),
        bundle,
    )
    normalizer = module.PolymarketNormalizer()
    normalizer.normalize(payload, context)
    if invalid == "source":
        context = replace(context, source_payload_sha256="0" * 64)
        error = "SHA-256"
    elif invalid == "missing_bundle":
        context = replace(context, reference_bundle=None)
        error = "reference bundle"
    else:
        frame["market" if invalid == "condition" else "asset_id"] = "conflicting-identity"
        payload = canonical_json_bytes(frame)
        context = replace(context, source_payload_sha256=hashlib.sha256(payload).hexdigest())
        error = "condition" if invalid == "condition" else "token"
    with pytest.raises(module.NormalizationError, match=error):
        normalizer.normalize(payload, context)
