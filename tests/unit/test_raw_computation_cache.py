"""Pure raw-decimal and level reuse preserves independent parsing semantics."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.normalization import polymarket as normalizer
from polymarket_pair_maker.normalization.models import BookSide, CanonicalBookLevel
from polymarket_pair_maker.venues.polymarket import schemas
from polymarket_pair_maker.venues.polymarket.errors import PolymarketSchemaError
from polymarket_pair_maker.venues.polymarket.schemas import NativeBookLevel


def reference_levels(
    values: tuple[NativeBookLevel, ...], side: BookSide
) -> tuple[CanonicalBookLevel, ...]:
    """Original transparent constructor and per-side checks, without cache hooks."""
    result: list[CanonicalBookLevel] = []
    prices: set[ExactDecimal] = set()
    for level in values:
        try:
            price, quantity = Price(level.price), Quantity(level.size)
        except (TypeError, ValueError) as error:
            raise normalizer.NormalizationError(
                f"invalid {side.value} book level: {error}"
            ) from error
        if price.value > ExactDecimal.one():
            raise normalizer.NormalizationError("prediction-market book price exceeds one")
        if price.value in prices:
            raise normalizer.NormalizationError("snapshot contains duplicate prices on one side")
        prices.add(price.value)
        if quantity.value != ExactDecimal.zero():
            result.append(CanonicalBookLevel(price, quantity))
    return tuple(sorted(result, key=lambda row: row.price.value, reverse=side is BookSide.BID))


def levels(*pairs: tuple[str, str]) -> tuple[NativeBookLevel, ...]:
    return tuple(NativeBookLevel(ExactDecimal.parse(p), ExactDecimal.parse(q)) for p, q in pairs)


def test_repeated_native_decimal_text_is_parsed_once(monkeypatch: pytest.MonkeyPatch):
    original = ExactDecimal.parse
    calls: list[object] = []

    def observed(_cls: type[ExactDecimal], value: object) -> ExactDecimal:
        if value == "0.45678123":
            calls.append(value)
        return original(value)

    monkeypatch.setattr(ExactDecimal, "parse", classmethod(observed))
    for path in ("$.bids[0].price", "$.asks[4].price"):
        assert schemas._decimal("0.45678123", path) == original("0.45678123")  # pyright: ignore[reportPrivateUsage]
    assert calls == ["0.45678123"]


def test_repeated_normalized_level_is_constructed_once(monkeypatch: pytest.MonkeyPatch):
    original = normalizer.CanonicalBookLevel
    calls: list[tuple[Price, Quantity]] = []

    def observed(*, price: Price, quantity: Quantity) -> CanonicalBookLevel:
        calls.append((price, quantity))
        return original(price, quantity)

    monkeypatch.setattr(normalizer, "CanonicalBookLevel", observed)
    values = levels(("0.123987", "17"))
    for side in (BookSide.BID, BookSide.ASK, BookSide.BID):
        actual = normalizer._levels(values, side=side)  # pyright: ignore[reportPrivateUsage]
        assert actual == reference_levels(values, side)
        assert canonical_json_bytes(actual) == canonical_json_bytes(reference_levels(values, side))
    assert len(calls) == 1


@pytest.mark.parametrize("value", [0.4, True, None, "NaN", "inf", "1e5", "1" * 129])
def test_decimal_admission_and_error_paths_remain_live(value: object):
    # Prime successful content first, then challenge a different actual representation.
    assert schemas._decimal("0.4", "$.prime") == ExactDecimal(4, 1)  # pyright: ignore[reportPrivateUsage]
    for path in ("$.first", "$.second"):
        with pytest.raises(PolymarketSchemaError) as caught:
            schemas._decimal(value, path)  # pyright: ignore[reportPrivateUsage]
        assert str(caught.value).startswith(path)


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
def test_normalized_levels_equal_original_checks(
    side: BookSide, pairs: tuple[tuple[str, str], ...]
):
    values = levels(*pairs)
    expected: tuple[CanonicalBookLevel, ...] = ()
    try:
        expected = reference_levels(values, side)
    except normalizer.NormalizationError as expected_error:
        failure = (str(expected_error), type(expected_error.__cause__))
    else:
        failure = None
    if failure is not None:
        for _ in range(2):
            with pytest.raises(normalizer.NormalizationError) as actual_error:
                normalizer._levels(values, side=side)  # pyright: ignore[reportPrivateUsage]
            assert str(actual_error.value) == failure[0]
            assert type(actual_error.value.__cause__) is failure[1]
    else:
        for _ in range(2):
            actual = normalizer._levels(values, side=side)  # pyright: ignore[reportPrivateUsage]
            assert actual == expected
            assert canonical_json_bytes(actual) == canonical_json_bytes(expected)


def test_complete_native_book_parse_and_level_normalization_match_uncached_reference():
    path = Path(__file__).parents[1] / "golden/fixtures/polymarket_rest_book_yes.json"
    payload = path.read_bytes()
    first = schemas.parse_rest_book(payload)
    second = schemas.parse_rest_book(payload)
    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    raw = cast(dict[str, list[dict[str, str]]], json.loads(payload))
    for name, side in (("bids", BookSide.BID), ("asks", BookSide.ASK)):
        # Independent direct decimal construction mirrors the pre-cache native values.
        values = tuple(
            NativeBookLevel(ExactDecimal.parse(item["price"]), ExactDecimal.parse(item["size"]))
            for item in raw[name]
        )
        assert getattr(first, name) == values
        assert normalizer._levels(values, side=side) == reference_levels(values, side)  # pyright: ignore[reportPrivateUsage]
    changed = replace(first.bids[0], size=first.bids[0].size + ExactDecimal.one())
    assert normalizer._levels((changed,), side=BookSide.BID) != normalizer._levels(  # pyright: ignore[reportPrivateUsage]
        (first.bids[0],), side=BookSide.BID
    )


def test_decimal_and_level_caches_evict_and_recompute_exactly():
    schemas._exact_decimal_text.cache_clear()  # pyright: ignore[reportPrivateUsage]
    normalizer._LEVEL_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
    first = normalizer._levels(levels(("0.4", "1")), side=BookSide.BID)[0]  # pyright: ignore[reportPrivateUsage]
    for index in range(2, 4098):
        text = str(index)
        assert schemas._decimal(text, "$.size") == ExactDecimal(index)  # pyright: ignore[reportPrivateUsage]
        values = (NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(index)),)
        assert normalizer._levels(values, side=BookSide.BID) == reference_levels(  # pyright: ignore[reportPrivateUsage]
            values, BookSide.BID
        )
    assert schemas._exact_decimal_text.cache_info().currsize == 4096  # pyright: ignore[reportPrivateUsage]
    assert normalizer.normalization_level_cache_info()["currsize"] == 4096
    returned = normalizer._levels(levels(("0.4", "1")), side=BookSide.BID)[0]  # pyright: ignore[reportPrivateUsage]
    assert returned == first
    assert returned is not first


def test_oversized_and_unknown_exact_representations_bypass_level_cache():
    class Derived(ExactDecimal):
        pass

    normalizer._LEVEL_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
    for value in (
        NativeBookLevel(ExactDecimal(4, 1), ExactDecimal(2**513)),
        NativeBookLevel(ExactDecimal(4, 130), ExactDecimal.one()),
        NativeBookLevel(Derived(4, 1), ExactDecimal.one()),
    ):
        values = (value,)
        assert normalizer._levels(values, side=BookSide.BID) == reference_levels(  # pyright: ignore[reportPrivateUsage]
            values, BookSide.BID
        )
    assert normalizer.normalization_level_cache_info()["currsize"] == 0
    for values in (
        levels(("0.4", "0")),
        levels(("1.1", "2")),
        levels(("0.4", "-1")),
        (NativeBookLevel(0.4, ExactDecimal.one()),),  # pyright: ignore[reportArgumentType]
    ):
        try:
            reference_levels(values, BookSide.BID)
        except normalizer.NormalizationError as expected:
            message = str(expected)
        else:
            message = None
        if message is not None:
            with pytest.raises(normalizer.NormalizationError) as caught:
                normalizer._levels(values, side=BookSide.BID)  # pyright: ignore[reportPrivateUsage]
            assert str(caught.value) == message
        else:
            assert normalizer._levels(values, side=BookSide.BID) == ()  # pyright: ignore[reportPrivateUsage]
    assert normalizer.normalization_level_cache_info()["currsize"] == 0
