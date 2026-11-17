"""Opt-in canonical byte composition exactly matches the original JSON encoder."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields, replace
from typing import cast

import pytest

from polymarket_pair_maker.core import serialization
from polymarket_pair_maker.core.exact import Price, Quantity
from polymarket_pair_maker.core.serialization import (
    CanonicalComposed,
    CanonicalMemo,
    CanonicalMemoized,
    CanonicalValue,
)
from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot
from tests.unit.test_snapshot_canonical_memo import book, encoded, reference


@dataclass(frozen=True)
class Composed(CanonicalComposed):
    z_declared_first: object
    a_declared_second: object
    hidden: object = field(default=None, metadata={"canonical_serialize": False})


@pytest.mark.parametrize("allow_floats", [False, True])
@pytest.mark.parametrize("wrapping", ["self", "tuple", "nested_tuple", "mapping"])
def test_composed_snapshots_and_mixed_values_keep_exact_original_bytes(
    allow_floats: bool, wrapping: str
):
    snapshot = book()
    composed = Composed(
        snapshot, ['\u03b1\n"\\', b"\x00\xff", Price.parse("0.4"), Quantity.parse("5")]
    )
    value = (
        composed
        if wrapping == "self"
        else ("prefix", composed, None)
        if wrapping == "tuple"
        else ((composed,), snapshot)
        if wrapping == "nested_tuple"
        else {"item": composed}
    )
    expected = encoded(value, allow_floats=allow_floats)
    for _ in range(3):
        assert serialization.canonical_json_bytes(value, allow_floats=allow_floats) == expected
        assert (
            serialization.canonical_sha256(value, allow_floats=allow_floats)
            == hashlib.sha256(expected).hexdigest()
        )
        assert serialization.canonicalize(value, allow_floats=allow_floats) == reference(
            value, allow_floats=allow_floats
        )


@pytest.mark.parametrize(
    ("first", "second", "allow_floats"),
    [
        ("\ud800", 0.5, False),
        ("\ud800", 10**5000, False),
        (10**5000, object(), False),
        (object(), 0.5, False),
        (0.5, object(), False),
        (float("nan"), 1, True),
        (float("inf"), 1, True),
        ("é", 0.5, True),
    ],
    ids=[
        "surrogate-then-float",
        "surrogate-then-int",
        "int-then-unsupported",
        "unsupported-then-float",
        "float-then-unsupported",
        "nan",
        "infinity",
        "float-opt-in",
    ],
)
def test_composition_preserves_whole_tree_error_type_message_and_priority(
    first: object, second: object, allow_floats: bool
):
    value = Composed(first, second)
    expected = b""
    try:
        expected = encoded(value, allow_floats=allow_floats)
    except (TypeError, ValueError, UnicodeError) as error:
        failure = (type(error), str(error))
    else:
        failure = None
    if failure is None:
        assert serialization.canonical_json_bytes(value, allow_floats=allow_floats) == expected
    else:
        with pytest.raises(failure[0]) as caught:
            serialization.canonical_json_bytes(value, allow_floats=allow_floats)
        assert str(caught.value) == failure[1]


def test_composed_field_metadata_remains_live_and_excluded_values_are_untouched():
    metadata = {"canonical_serialize": True}

    @dataclass
    class Mutable(CanonicalComposed):
        visible: object
        maybe: object = field(metadata=metadata)

    value = Mutable("x", "y")
    assert serialization.canonical_json_bytes(value) == encoded(value)
    metadata["canonical_serialize"] = False
    value.visible = "changed"
    value.maybe = object()
    assert serialization.canonical_json_bytes(value) == encoded(value) == b'{"visible":"changed"}'


def test_unknown_snapshot_subclass_keeps_original_projection_override():
    class Derived(ImmutableOrderBookSnapshot):
        def canonical_projection(self, *, allow_floats: bool = False) -> CanonicalValue:
            return {"overridden": allow_floats}

    snapshot = book()
    derived = Derived(
        **{item.name: getattr(snapshot, item.name) for item in fields(snapshot) if item.init}
    )
    assert not derived.canonical_cache_eligible()
    assert serialization.canonical_json_bytes(derived) == b'{"overridden":false}'
    assert serialization.canonical_json_bytes(Composed(derived, None)) == (
        b'{"a_declared_second":null,"z_declared_first":{"overridden":false}}'
    )


def test_oversized_and_mutable_memoized_payloads_keep_ordinary_fallback():
    @dataclass(frozen=True, slots=True, weakref_slot=True)
    class Large(CanonicalMemoized):
        value: object
        _canonical_memo: CanonicalMemo = field(
            default_factory=lambda: CanonicalMemo((Large,)),
            init=False,
            repr=False,
            compare=False,
            hash=False,
            metadata={"canonical_serialize": False},
        )

    large = Large("é" * 40_000)
    for _ in range(2):
        assert serialization.canonical_json_bytes(Composed(large, None)) == encoded(
            Composed(large, None)
        )
    assert large._canonical_memo.retained_bytes == 0  # pyright: ignore[reportPrivateUsage]
    contents = ["first"]
    mutable = Large(contents)
    assert not mutable.canonical_cache_eligible()
    before = serialization.canonical_json_bytes(mutable)
    contents.append("next")
    assert serialization.canonical_json_bytes(mutable) == encoded(mutable) != before
    float_value = replace(large, value=0.5)
    assert serialization.canonical_json_bytes(float_value, allow_floats=True) == encoded(
        float_value, allow_floats=True
    )
    with pytest.raises(TypeError, match="binary float"):
        serialization.canonical_json_bytes(float_value)


@pytest.mark.parametrize("flag", [1, "yes", 0, "", None])
def test_composed_nonboolean_float_modes_preserve_original_truthiness(flag: object):
    snapshot = replace(book(), last_trade_price=0.5)  # pyright: ignore[reportArgumentType]
    value = Composed(snapshot, None)
    mode = cast(bool, flag)  # Deliberately exercise the original dynamically typed call boundary.
    if flag:
        assert serialization.canonical_json_bytes(value, allow_floats=mode) == encoded(
            value, allow_floats=mode
        )
    else:
        with pytest.raises(TypeError, match="binary float"):
            serialization.canonical_json_bytes(value, allow_floats=mode)
    assert snapshot._canonical_memo.retained_bytes == 0  # pyright: ignore[reportPrivateUsage]


def test_stateful_normalization_failure_is_never_retried_or_hidden():
    class OneShot(Mapping[str, object]):
        visits = 0

        def __getitem__(self, key: str) -> object:
            return "ok"

        def __iter__(self) -> Iterator[str]:
            self.visits += 1
            if self.visits == 1:
                raise ValueError("first normalization failure")
            return iter(("value",))

        def __len__(self) -> int:
            return 1

    contents = OneShot()
    with pytest.raises(ValueError, match="first normalization failure"):
        serialization.canonical_json_bytes(Composed(contents, None))
    assert contents.visits == 1
