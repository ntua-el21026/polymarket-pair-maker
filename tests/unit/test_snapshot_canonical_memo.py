"""Canonical memoization preserves the independent pre-optimization contract."""

from __future__ import annotations

import base64
import gc
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import Field, dataclass, field, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, cast
from weakref import ref

import pytest

from polymarket_pair_maker.core import serialization
from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
from polymarket_pair_maker.core.ids import TypedId
from polymarket_pair_maker.core.serialization import CanonicalValue
from polymarket_pair_maker.core.time import MonotonicTimestamp, UtcTimestamp
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_market_context
from polymarket_pair_maker.state.order_books import (
    BookHealth,
    BookLevel,
    InvalidationReason,
    models,
)


def reference(value: object, *, allow_floats: bool = False) -> CanonicalValue:
    """Transparent original recursive normalization, independent of new cache hooks."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not allow_floats:
            raise serialization.CanonicalSerializationError(
                "binary float canonicalization requires opt-in"
            )
        if not math.isfinite(value):
            raise serialization.CanonicalSerializationError(
                "non-finite floats are not canonical JSON"
            )
        return value
    if isinstance(value, ExactDecimal):
        return value.to_canonical_string()
    if isinstance(value, TypedId):
        return value.value
    if isinstance(value, UtcTimestamp):
        return value.to_iso8601()
    if isinstance(value, MonotonicTimestamp):
        return value.to_canonical_string()
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return reference(value.value, allow_floats=allow_floats)

    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: reference(getattr(value, f.name), allow_floats=allow_floats)
            for f in fields(value)
            if f.metadata.get("canonical_serialize", True)
        }
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        result: dict[str, CanonicalValue] = {}
        for key, item in mapping.items():
            assert isinstance(key, str)
            result[key] = reference(item, allow_floats=allow_floats)
        return result
    if isinstance(value, Sequence):
        return [reference(v, allow_floats=allow_floats) for v in cast(Sequence[object], value)]
    raise serialization.CanonicalSerializationError(
        f"unsupported canonical value type: {type(value).__module__}.{type(value).__qualname__}"
    )


def encoded(value: object, *, allow_floats: bool = False) -> bytes:
    return json.dumps(
        reference(value, allow_floats=allow_floats),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def book() -> models.ImmutableOrderBookSnapshot:
    return replace(demo_market_context(PaperConfig()).yes_book)


def test_fixed_field_discovery_is_cached_but_metadata_and_values_stay_live(
    monkeypatch: pytest.MonkeyPatch,
):
    metadata = {"canonical_serialize": True}

    @dataclass
    class Local:
        value: int
        hidden: int = field(default=2, metadata=metadata)

    original = serialization.fields
    calls: list[object] = []

    def observed(value: object) -> tuple[Field[Any], ...]:
        calls.append(value)
        assert is_dataclass(value)
        return original(value)

    monkeypatch.setattr(serialization, "fields", observed)
    value = Local(1)
    assert serialization.canonicalize(value) == {"value": 1, "hidden": 2}
    value.value = 3
    metadata["canonical_serialize"] = False
    assert serialization.canonicalize(value) == {"value": 3}
    assert len(calls) == 1


def test_snapshot_hashes_reuse_immutable_results_and_preserve_reference(
    monkeypatch: pytest.MonkeyPatch,
):
    value = book()
    full = hashlib.sha256(
        encoded({"schema": "mma-order-book-snapshot-v2", "snapshot": value})
    ).hexdigest()
    original = models.canonical_sha256
    calls: list[object] = []

    def observed(raw: object) -> str:
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(models, "canonical_sha256", observed)
    assert value.content_sha256() == value.content_sha256() == full
    expected_economic = hashlib.sha256(encoded(economic_mapping(value))).hexdigest()
    assert value.economic_content_sha256() == value.economic_content_sha256() == expected_economic
    # Full identity uses the generic wrapper; the economic subset encodes the
    # already-validated projection directly, without another recursive hash.
    assert len(calls) == 1


def test_snapshot_projection_reuses_encoding_and_returns_fresh_trees(
    monkeypatch: pytest.MonkeyPatch,
):
    value = book()
    expected = encoded(value)
    original = UtcTimestamp.to_iso8601
    calls: list[object] = []

    def observed(timestamp: UtcTimestamp) -> str:
        calls.append(timestamp)
        return original(timestamp)

    monkeypatch.setattr(UtcTimestamp, "to_iso8601", observed)
    assert serialization.canonical_json_bytes(value) == expected
    first = len(calls)
    assert first > 0
    projected = serialization.canonicalize(value)
    assert isinstance(projected, dict)
    cast(list[object], projected["bids"]).clear()
    assert serialization.canonical_json_bytes(value) == expected
    assert len(calls) == first


@pytest.mark.parametrize(
    "change", ["time", "reference", "levels", "health", "generation", "market"]
)
def test_replacement_has_cold_cache_and_exact_changed_identity(change: str):
    value = book()
    before = value.content_sha256()
    value.economic_content_sha256()
    assert value.last_valid_update_time is not None
    if change == "time":
        other = replace(
            value,
            last_valid_update_time=UtcTimestamp(
                value.last_valid_update_time.nanoseconds_since_epoch + 1
            ),
        )
    elif change == "reference":
        other = replace(
            value, market_version_id=type(value.market_version_id)("different-reference")
        )
    elif change == "levels":
        other = replace(
            value,
            bids=(BookLevel(Price.parse("0.39"), Quantity.parse("7")),),
            best_bid=BookLevel(Price.parse("0.39"), Quantity.parse("7")),
        )
    elif change == "health":
        other = replace(
            value,
            health=BookHealth.INVALID,
            invalidation_reason=InvalidationReason.REST_AUDIT_MISMATCH,
        )
    elif change == "generation":
        other = replace(value, generation=value.generation + 1)
    else:
        other = replace(value, market_id=type(value.market_id)("different-market"))
    assert (
        other.content_sha256()
        == hashlib.sha256(
            encoded({"schema": "mma-order-book-snapshot-v2", "snapshot": other})
        ).hexdigest()
        != before
    )
    assert serialization.canonical_json_bytes(other) == encoded(other)
    assert not other.is_fresh(UtcTimestamp(0), max_age_ns=5_000_000_000)


def test_mutable_malformed_snapshot_falls_back_and_observes_edits():
    value = book()
    bids = list(value.bids)
    malformed = replace(value, bids=bids)  # pyright: ignore[reportArgumentType]
    before = malformed.content_sha256()
    before_economic = malformed.economic_content_sha256()
    bids.append(BookLevel(Price.parse("0.2"), Quantity.parse("1")))
    assert malformed.content_sha256() != before
    assert malformed.economic_content_sha256() != before_economic
    assert serialization.canonical_json_bytes(malformed) == encoded(malformed)


def test_float_modes_cannot_cross_prime_strict_validation():
    value = replace(book(), last_trade_price=0.125)  # pyright: ignore[reportArgumentType]
    assert serialization.canonical_json_bytes(value, allow_floats=True) == encoded(
        value, allow_floats=True
    )
    with pytest.raises(TypeError, match="binary float"):
        serialization.canonical_json_bytes(value)
    assert serialization.canonical_json_bytes(value, allow_floats=True) == encoded(
        value, allow_floats=True
    )


def test_canonicalize_keeps_normalization_only_encoding_error_boundary():
    for invalid in ("\ud800", 10**5000):
        value = replace(book(), last_source_event_id=invalid)  # pyright: ignore[reportArgumentType]
        assert serialization.canonicalize(value) == reference(value)
        with pytest.raises((UnicodeError, ValueError)):
            serialization.canonical_json_bytes(value)


def test_snapshot_equality_repr_and_hash_ignore_memo_population():
    value = book()
    other = replace(value)
    before = (repr(value), hash(value))
    serialization.canonical_json_bytes(value)
    value.content_sha256()
    value.economic_content_sha256()
    assert value == other
    assert (repr(value), hash(value)) == before
    assert (
        serialization.canonical_json_bytes(value)
        == serialization.canonical_json_bytes(other)
        == encoded(other)
    )


def test_object_memo_bounds_and_oversized_fallback():
    from polymarket_pair_maker.core.provenance import Provenance
    from polymarket_pair_maker.core.serialization import CanonicalMemo
    from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot

    value = book()
    memo = CanonicalMemo((ImmutableOrderBookSnapshot, BookLevel, Price, Quantity, Provenance))
    assert memo.project(value, allow_floats=False) == reference(value)
    assert memo.project(value, allow_floats=True) == reference(value, allow_floats=True)
    assert 0 < memo.retained_bytes <= 65_536
    large = replace(value, bids=value.bids * 2000)
    oversized = CanonicalMemo((ImmutableOrderBookSnapshot, BookLevel, Price, Quantity, Provenance))
    assert oversized.project(large, allow_floats=False) == reference(large)
    assert oversized.project(large, allow_floats=False) == reference(large)
    assert oversized.retained_bytes == 0


def test_a_new_memo_after_eviction_recomputes_exact_bytes():
    from polymarket_pair_maker.core.provenance import Provenance
    from polymarket_pair_maker.core.serialization import CanonicalMemo
    from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot

    audited = (ImmutableOrderBookSnapshot, BookLevel, Price, Quantity, Provenance)
    value = book()
    first = CanonicalMemo(audited)
    expected = first.project(value, allow_floats=False)
    del first
    replacement = CanonicalMemo(audited)
    assert replacement.retained_bytes == 0
    assert replacement.project(value, allow_floats=False) == expected


def test_unknown_frozen_nested_value_is_not_assumed_immutable():
    @dataclass(frozen=True)
    class Unknown:
        mutable: list[int]

    nested = Unknown([1])
    value = replace(book(), last_source_provenance=nested)  # pyright: ignore[reportArgumentType]
    initial = value.content_sha256()
    nested.mutable.append(2)
    assert value.content_sha256() != initial
    assert serialization.canonical_json_bytes(value) == encoded(value)


def test_snapshot_subclass_does_not_inherit_automatic_memo_trust():
    class Derived(models.ImmutableOrderBookSnapshot):
        pass

    value = book()
    arguments = {f.name: getattr(value, f.name) for f in fields(value) if f.init}
    derived = Derived(**arguments)
    # The trusted tuple contains the concrete declared snapshot class only.
    from polymarket_pair_maker.core.serialization import CanonicalMemo

    memo = CanonicalMemo((models.ImmutableOrderBookSnapshot, BookLevel, Price, Quantity))
    assert not memo.eligible(derived)
    assert memo.project(derived, allow_floats=False) == reference(derived)
    assert memo.retained_bytes == 0


def test_diagnostic_trade_changes_full_identity_but_not_economic_identity():
    value = book()
    full = value.content_sha256()
    economic = value.economic_content_sha256()
    changed = replace(value, last_trade_price=Price.parse("0.52"))
    assert changed.content_sha256() != full
    assert changed.economic_content_sha256() == economic
    assert serialization.canonical_json_bytes(changed) == encoded(changed)


def test_snapshot_identity_cache_does_not_cache_freshness_or_scenario_values():
    value = book()
    identity = value.economic_content_sha256()
    assert value.last_valid_update_time is not None
    now = value.last_valid_update_time
    assert value.is_fresh(now, max_age_ns=5)
    assert not value.is_fresh(UtcTimestamp(now.nanoseconds_since_epoch + 6), max_age_ns=5)
    assert not value.is_fresh(UtcTimestamp(now.nanoseconds_since_epoch - 1), max_age_ns=5)
    assert value.economic_content_sha256() == identity
    context = demo_market_context(PaperConfig())
    one = replace(context, yes_book=value)
    two = replace(one, risk_capacity=type(one.risk_capacity).parse("1"))
    assert serialization.canonical_sha256(one) != serialization.canonical_sha256(two)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_float_rejection_survives_memo_modes(bad: float):
    value = replace(book(), last_trade_price=bad)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="binary float"):
        serialization.canonical_json_bytes(value)
    with pytest.raises(TypeError, match="non-finite"):
        serialization.canonical_json_bytes(value, allow_floats=True)


def test_high_depth_snapshot_and_updated_level_match_uncached_bytes_and_hashes():
    value = book()
    bids = tuple(
        BookLevel(Price(ExactDecimal(400 - i, 3)), Quantity(ExactDecimal(i + 1)))
        for i in range(250)
    )
    asks = tuple(
        BookLevel(Price(ExactDecimal(600 + i, 3)), Quantity(ExactDecimal(2 * i + 1)))
        for i in range(250)
    )
    deep = replace(value, bids=bids, asks=asks, best_bid=bids[0], best_ask=asks[0])
    assert serialization.canonical_json_bytes(deep) == encoded(deep)
    initial = deep.content_sha256()
    deep.economic_content_sha256()
    changed = replace(deep, bids=(replace(bids[0], quantity=Quantity.parse("999")), *bids[1:]))
    assert serialization.canonical_json_bytes(changed) == encoded(changed)
    assert (
        changed.content_sha256()
        == hashlib.sha256(
            encoded({"schema": "mma-order-book-snapshot-v2", "snapshot": changed})
        ).hexdigest()
        != initial
    )


def test_untyped_float_mode_flags_keep_original_behavior_without_unbounded_entries():
    from polymarket_pair_maker.core.provenance import Provenance
    from polymarket_pair_maker.core.serialization import CanonicalMemo
    from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot

    value = book()
    memo = CanonicalMemo((ImmutableOrderBookSnapshot, BookLevel, Price, Quantity, Provenance))
    for flag in ("yes", "another", 1):
        assert memo.project(value, allow_floats=flag) == reference(value, allow_floats=flag)  # pyright: ignore[reportArgumentType]
    assert memo.retained_bytes == 0


def test_cached_projection_preserves_original_primitive_enum_leaf_types():
    value = book()
    serialization.canonicalize(value)
    projected = serialization.canonicalize(value)
    assert isinstance(projected, dict)
    assert projected["health"] is value.health
    invalid = replace(
        value, health=BookHealth.INVALID, invalidation_reason=InvalidationReason.DISCONNECT
    )
    serialization.canonicalize(invalid)
    projected_invalid = serialization.canonicalize(invalid)
    assert isinstance(projected_invalid, dict)
    assert projected_invalid["health"] is invalid.health
    assert projected_invalid["invalidation_reason"] is invalid.invalidation_reason


def test_cached_nested_primitive_enums_preserve_exact_leaf_types():
    from enum import IntEnum, StrEnum

    from polymarket_pair_maker.core.serialization import CanonicalMemo

    class Label(StrEnum):
        READY = "ready"

    class Count(IntEnum):
        ONE = 1

    @dataclass(frozen=True)
    class Nested:
        values: tuple[tuple[Label, Count], ...]

    value = Nested(((Label.READY, Count.ONE),))
    memo = CanonicalMemo((Nested, Label, Count))
    assert memo.project(value, allow_floats=False) == reference(value)
    assert memo.retained_bytes > 0
    projected = memo.project(value, allow_floats=False)
    assert isinstance(projected, dict)
    rows = cast(list[list[object]], projected["values"])
    assert rows[0][0] is Label.READY
    assert rows[0][1] is Count.ONE


def test_reused_memo_cell_binds_exact_owner_and_resets_both_modes():
    from polymarket_pair_maker.core.ids import MarketId
    from polymarket_pair_maker.core.provenance import Provenance
    from polymarket_pair_maker.core.serialization import CanonicalMemo

    first = book()
    second = replace(
        first,
        market_id=MarketId("different-market"),
        health=BookHealth.INVALID,
        invalidation_reason=InvalidationReason.DISCONNECT,
    )
    memo = CanonicalMemo(
        (models.ImmutableOrderBookSnapshot, BookLevel, Price, Quantity, Provenance)
    )
    for value in (first, second, first):
        for mode in (False, True):
            assert memo.project(value, allow_floats=mode) == reference(value, allow_floats=mode)
            projected = memo.project(value, allow_floats=mode)
            assert isinstance(projected, dict)
            assert projected["health"] is value.health
        assert memo.retained_bytes == len(encoded(value)) * 2


def test_memo_does_not_retain_owner_and_unweakrefable_values_bypass_cache():
    from polymarket_pair_maker.core.serialization import CanonicalMemo

    @dataclass(frozen=True, slots=True)
    class Unweakrefable:
        count: int

    @dataclass(frozen=True, slots=True, weakref_slot=True)
    class WeakOwner:
        count: int

    value = WeakOwner(1)
    memo = CanonicalMemo((WeakOwner, Unweakrefable))
    assert memo.project(value, allow_floats=False) == {"count": 1}
    reference_to_owner = ref(value)
    del value
    gc.collect()
    assert reference_to_owner() is None
    replacement = WeakOwner(2)
    assert memo.project(replacement, allow_floats=False) == {"count": 2}
    unweakrefable = Unweakrefable(3)
    assert memo.project(unweakrefable, allow_floats=False) == {"count": 3}
    assert memo.retained_bytes == 0


def test_immutable_audit_uses_exact_financial_wrapper_guards(
    monkeypatch: pytest.MonkeyPatch,
):
    value = book()
    # Instrument the private work path that this optimization removes.
    original = serialization._dataclass_fields  # pyright: ignore[reportPrivateUsage]
    wrapped: list[type[object]] = []

    def observed(kind: type[object]) -> tuple[Field[Any], ...]:
        if kind in (Price, Quantity):
            wrapped.append(kind)
        return original(kind)

    monkeypatch.setattr(serialization, "_dataclass_fields", observed)
    assert value.canonical_cache_eligible()
    assert wrapped == []


def test_exact_financial_projection_avoids_generic_walk_without_changing_bytes(
    monkeypatch: pytest.MonkeyPatch,
):
    original = serialization._canonical_dataclass  # pyright: ignore[reportPrivateUsage]
    walked: list[object] = []

    def observed(value: object, *, allow_floats: bool) -> dict[str, CanonicalValue]:
        walked.append(value)
        return original(value, allow_floats=allow_floats)

    monkeypatch.setattr(serialization, "_canonical_dataclass", observed)
    for value in (Price.parse("0.00000123"), Quantity.parse("123456789.1200")):
        assert serialization.canonicalize(value) == reference(value)
        assert serialization.canonical_json_bytes(value) == encoded(value)
    assert walked == []


@pytest.mark.parametrize("kind", [Price, Quantity])
def test_financial_projection_keeps_live_metadata_and_malformed_value_fallback(
    kind: type[Price] | type[Quantity], monkeypatch: pytest.MonkeyPatch
):
    scalar = kind.parse("1.25")
    value_field = fields(kind)[0]
    monkeypatch.setattr(value_field, "metadata", {"canonical_serialize": False})
    assert serialization.canonicalize(scalar) == reference(scalar) == {}
    monkeypatch.undo()
    # Build an adversarial actual representation that constructors normally reject.
    malformed = object.__new__(kind)
    contents = ["first"]
    object.__setattr__(malformed, "value", contents)
    snapshot = replace(book(), last_trade_price=malformed)  # pyright: ignore[reportArgumentType]
    assert not snapshot.canonical_cache_eligible()
    first = serialization.canonical_json_bytes(snapshot)
    contents.append("second")
    assert serialization.canonical_json_bytes(snapshot) == encoded(snapshot) != first


def test_public_immutable_audit_requires_whitelisted_actual_frozen_graph():
    @dataclass(frozen=True)
    class Known:
        values: tuple[object, ...]

    @dataclass(frozen=True)
    class Unknown:
        value: int

    @dataclass
    class Mutable:
        value: int

    audit = serialization.is_canonically_immutable
    assert audit(Known((Price.parse("0.4"), Quantity.parse("5"))), audited_types=(Known,))
    assert audit((Known((1, "x")), None), audited_types=(Known,))
    assert not audit(Known(([],)), audited_types=(Known,))
    assert not audit(Known((Unknown(1),)), audited_types=(Known,))
    assert not audit(Mutable(1), audited_types=(Mutable,))


def test_direct_canonical_encoding_reuses_exact_bytes_without_json_tree_decode(
    monkeypatch: pytest.MonkeyPatch,
):
    value = book()
    expected = encoded(value)
    serialization.canonicalize(value)
    original = serialization.json.loads
    calls: list[object] = []

    def observed(payload: bytes) -> object:
        calls.append(payload)
        return original(payload)

    monkeypatch.setattr(serialization.json, "loads", observed)
    assert serialization.canonical_json_bytes(value) == expected
    assert calls == []


def economic_mapping(value: models.ImmutableOrderBookSnapshot) -> dict[str, object]:
    names = (
        "instrument_id",
        "market_id",
        "generation",
        "health",
        "invalidation_reason",
        "bids",
        "asks",
        "best_bid",
        "best_ask",
        "last_source_event_id",
        "last_source_provenance",
        "state_source_provenance",
        "market_version_id",
        "precision_rule_version_id",
        "cost_rule_set_version_id",
        "last_valid_update_time",
        "last_venue_timestamp",
        "supports_sequence_numbers",
        "supports_book_checksum",
        "venue_sequence",
        "validated_checksum",
    )
    return {
        "schema": "mma-order-book-economic-snapshot-v1",
        **{name: getattr(value, name) for name in names},
    }


def test_economic_hash_and_full_encoding_share_one_exact_level_projection(
    monkeypatch: pytest.MonkeyPatch,
):
    value = book()
    expected = hashlib.sha256(encoded(economic_mapping(value))).hexdigest()
    original = serialization._canonical_dataclass  # pyright: ignore[reportPrivateUsage]
    calls: list[BookLevel] = []

    def observed(item: object, *, allow_floats: bool) -> dict[str, CanonicalValue]:
        if isinstance(item, BookLevel):
            calls.append(item)
        return original(item, allow_floats=allow_floats)

    monkeypatch.setattr(serialization, "_canonical_dataclass", observed)
    assert value.economic_content_sha256() == expected
    projected_levels = len(calls)
    assert projected_levels > 0
    assert serialization.canonical_json_bytes(value) == encoded(value)
    assert len(calls) == projected_levels


@pytest.mark.parametrize("diagnostic", ["float", "unknown", "mutable", "timestamp"])
def test_economic_subset_keeps_original_ignored_diagnostic_failure_boundary(diagnostic: str):
    value = book()
    if diagnostic == "float":
        value = replace(value, last_trade_price=0.5)  # pyright: ignore[reportArgumentType]
    elif diagnostic == "unknown":
        value = replace(value, last_trade_price=object())  # pyright: ignore[reportArgumentType]
    elif diagnostic == "mutable":
        value = replace(value, last_trade_source_provenance=[])  # pyright: ignore[reportArgumentType]
    else:
        value = replace(value, last_trade_venue_timestamp=UtcTimestamp(10**100))
    assert (
        value.economic_content_sha256()
        == hashlib.sha256(encoded(economic_mapping(value))).hexdigest()
    )
