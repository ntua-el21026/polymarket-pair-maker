"""Subset composition preserves full normalization, exact bytes and memo bounds."""

import hashlib
import json
from dataclasses import dataclass, field, is_dataclass, make_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, cast

import pytest

from polymarket_pair_maker.core import serialization as module
from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
from polymarket_pair_maker.core.ids import TypedId
from polymarket_pair_maker.core.serialization import (
    CanonicalMemo,
    CanonicalMemoized,
    CanonicalValue,
)
from polymarket_pair_maker.core.time import MonotonicTimestamp, UtcTimestamp
from polymarket_pair_maker.state.order_books import BookLevel
from tests.unit.test_snapshot_canonical_memo import book, encoded, reference

AUDITED = (BookLevel, Price, Quantity)


def original_subset(value: object, names: tuple[str, ...], schema: object) -> str:
    normalized = reference(value)
    assert isinstance(normalized, dict)
    selected = {name: normalized[name] for name in names}
    selected["schema"] = reference(schema)
    raw = json.dumps(
        selected, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Sample(CanonicalMemoized):
    z_first: object
    a_second: object
    levels: object
    hidden: object = field(default=None, metadata={"canonical_serialize": False})
    _canonical_memo: CanonicalMemo = field(
        default_factory=lambda: CanonicalMemo((Sample, *AUDITED)),
        init=False,
        repr=False,
        compare=False,
        hash=False,
        metadata={"canonical_serialize": False},
    )


def registered_values() -> tuple[BookLevel, ...]:
    values = tuple(book().bids)
    assert module.register_canonical_sequence(values, element_type=BookLevel, audited_types=AUDITED)
    return values


def test_subset_reuses_registered_bytes_without_rebuilding_deep_trees(
    monkeypatch: pytest.MonkeyPatch,
):
    sample = Sample("é", (1, None, True), registered_values())
    names = ("levels", "z_first")
    expected = original_subset(sample, names, "domain")
    loads = module.json.loads
    calls: list[tuple[Any, ...]] = []

    def observed(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return loads(*args, **kwargs)

    monkeypatch.setattr(module.json, "loads", observed)
    assert sample.canonical_subset_sha256(names, schema="domain") == expected
    assert not calls


@pytest.mark.parametrize(
    "names", [("levels",), ("a_second", "levels", "z_first"), (), ("levels", "levels")]
)
@pytest.mark.parametrize("primed", [False, True])
def test_subset_and_full_memo_match_original_bytes_for_every_selection(
    names: tuple[str, ...], primed: bool
):
    sample = Sample('a\n\\"é', (True, None, b"\x00\xff"), registered_values())
    if primed:
        assert module.canonical_json_bytes(sample) == encoded(sample)
    for schema in ("domain", "\u03b1", "other-domain"):
        assert sample.canonical_subset_sha256(names, schema=schema) == original_subset(
            sample, names, schema
        )
        assert module.canonical_json_bytes(sample) == encoded(sample)
        assert module.canonicalize(sample) == reference(sample)
    assert sample._canonical_memo.retained_bytes <= 65536  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("\ud800", 0.5),
        (10**5000, object()),
        (0.5, object()),
        ("\ud800", "normal"),
        ("normal", "\ud800"),
    ],
    ids=[
        "surrogate-float",
        "huge-unsupported",
        "float-unsupported",
        "excluded-surrogate",
        "selected-surrogate",
    ],
)
@pytest.mark.parametrize("names", [("z_first",), ("a_second",), ("levels",), ("missing",)])
def test_complete_normalization_and_encoding_error_order_matches_original(
    first: object, second: object, names: tuple[str, ...]
):
    sample = Sample(first, second, registered_values())
    expected = ""
    try:
        expected = original_subset(sample, names, "domain")
    except (TypeError, ValueError, OverflowError, KeyError) as error:
        failure = (type(error), str(error))
    else:
        failure = None
    if failure is None:
        assert sample.canonical_subset_sha256(names, schema="domain") == expected
    else:
        with pytest.raises(failure[0]) as caught:
            sample.canonical_subset_sha256(names, schema="domain")
        assert str(caught.value) == failure[1]


def test_mutable_replaced_and_registry_evicted_values_equal_original():
    values = registered_values()
    contents = ["old"]
    sample = Sample(contents, "second", values)
    names = ("z_first", "levels")
    before = sample.canonical_subset_sha256(names, schema="domain")
    assert before == original_subset(sample, names, "domain")
    contents.append("new")
    assert (
        sample.canonical_subset_sha256(names, schema="domain")
        == original_subset(sample, names, "domain")
        != before
    )
    immutable = replace(sample, z_first="immutable")
    changed = replace(immutable, levels=(replace(values[0], quantity=Quantity.parse("7.001")),))
    for candidate in (immutable, changed, immutable):
        assert candidate.canonical_subset_sha256(names, schema="domain") == original_subset(
            candidate, names, "domain"
        )
    for index in range(12):
        extra = (BookLevel(Price.parse("0.01"), Quantity.parse(str(index + 100))),)
        assert module.register_canonical_sequence(
            extra, element_type=BookLevel, audited_types=AUDITED
        )
    assert immutable.canonical_subset_sha256(names, schema="domain") == original_subset(
        immutable, names, "domain"
    )


def test_overridden_projection_keeps_its_original_mapping():
    @dataclass(frozen=True, slots=True, weakref_slot=True)
    class Overridden(CanonicalMemoized):
        value: str
        _canonical_memo: CanonicalMemo = field(
            default_factory=lambda: CanonicalMemo((Overridden,)),
            init=False,
            repr=False,
            compare=False,
            hash=False,
            metadata={"canonical_serialize": False},
        )

        def canonical_projection(self, *, allow_floats: bool = False) -> CanonicalValue:
            return {"renamed": self.value, "mode": allow_floats}

    value = Overridden("value")
    expected = hashlib.sha256(b'{"renamed":"value","schema":"domain"}').hexdigest()
    assert value.canonical_subset_sha256(("renamed",), schema="domain") == expected


def test_permissive_memo_cannot_hide_strict_normalization_failure():
    sample = Sample(0.5, "ok", registered_values())
    assert module.canonical_json_bytes(sample, allow_floats=True) == encoded(
        sample, allow_floats=True
    )
    with pytest.raises(TypeError, match="binary float"):
        sample.canonical_subset_sha256(("levels",), schema="domain")


def test_oversized_memo_remains_bounded_while_subset_succeeds():
    sample = Sample("é" * 40000, "selected", registered_values())
    for _ in range(2):
        assert sample.canonical_subset_sha256(
            ("a_second", "levels"), schema="domain"
        ) == original_subset(sample, ("a_second", "levels"), "domain")
    assert sample._canonical_memo.retained_bytes == 0  # pyright: ignore[reportPrivateUsage]


def test_schema_override_and_invalid_selected_name_keep_original_contract():
    @dataclass(frozen=True, slots=True, weakref_slot=True)
    class WithSchema(CanonicalMemoized):
        schema: str
        levels: tuple[BookLevel, ...]
        _canonical_memo: CanonicalMemo = field(
            default_factory=lambda: CanonicalMemo((WithSchema, *AUDITED)),
            init=False,
            repr=False,
            compare=False,
            hash=False,
            metadata={"canonical_serialize": False},
        )

    sample = WithSchema("input-schema", registered_values())
    assert sample.canonical_subset_sha256(
        ("schema", "levels"), schema="outer-schema"
    ) == original_subset(sample, ("schema", "levels"), "outer-schema")
    with pytest.raises(KeyError, match="missing"):
        sample.canonical_subset_sha256(("missing",), schema=cast(str, object()))


def test_memo_subset_rebinds_owner_and_rejects_unaudited_values():
    first = Sample("first", 1, registered_values())
    second = Sample("second", 2, registered_values())
    memo = first._canonical_memo  # pyright: ignore[reportPrivateUsage]
    for value in (first, second, first):
        assert memo.subset_sha256(value, ("z_first", "levels"), "domain") == original_subset(
            value, ("z_first", "levels"), "domain"
        )
    with pytest.raises(TypeError, match="audited owner"):
        memo.subset_sha256(object(), (), "domain")
    assert memo.retained_bytes == 0


def test_scalar_root_retains_original_canonical_dispatch_before_subset():
    @dataclass(frozen=True)
    class TextOwner(str, CanonicalMemoized):
        value: str
        _canonical_memo: CanonicalMemo = field(
            default_factory=lambda: CanonicalMemo((TextOwner,)),
            init=False,
            repr=False,
            compare=False,
            hash=False,
            metadata={"canonical_serialize": False},
        )

    value = TextOwner("x")
    assert value.canonical_cache_eligible()
    assert isinstance(module.canonicalize(value), str)
    with pytest.raises(TypeError, match="canonical subset requires a dataclass projection"):
        value.canonical_subset_sha256(("value",), schema="domain")


def scalar_root(base: type[Any], arguments: tuple[Any, ...]) -> Any:
    fields: list[Any] = [] if is_dataclass(base) else [("value", object)]
    fields.append(
        (
            "_canonical_memo",
            CanonicalMemo,
            field(
                default_factory=lambda: CanonicalMemo((owner,)),
                init=False,
                repr=False,
                compare=False,
                hash=False,
                metadata={"canonical_serialize": False},
            ),
        )
    )
    owner = make_dataclass(
        "ScalarOwner",
        fields,
        bases=(base, CanonicalMemoized),
        frozen=True,
        namespace={"__module__": base.__module__},
    )
    return owner(*arguments)


@pytest.mark.parametrize(
    ("base", "arguments", "names"),
    [
        (str, ("x",), ("value",)),
        (int, (2,), ("value",)),
        (float, (0.5,), ("value",)),
        (bytes, (b"x",), ("$bytes_base64",)),
        (ExactDecimal, (125, 2), ("coefficient",)),
        (TypedId, ("x",), ("value",)),
        (UtcTimestamp, (0,), ("nanoseconds_since_epoch",)),
        (MonotonicTimestamp, ("epoch", 2), ("nanoseconds",)),
        (type(Path()), ("x",), ("value",)),
        (Price, (ExactDecimal(2, 1),), ("value",)),
        (Quantity, (ExactDecimal(2),), ("value",)),
    ],
    ids=[
        "str",
        "int",
        "float",
        "bytes-dict",
        "decimal",
        "id",
        "UTC",
        "monotonic",
        "path",
        "price-fallback",
        "quantity-fallback",
    ],
)
def test_every_scalar_root_dispatch_preserves_original_subset_semantics(
    base: type[Any], arguments: tuple[Any, ...], names: tuple[str, ...]
):
    value = scalar_root(base, arguments)
    expected = ""
    try:
        projected = module.canonicalize(value)
        if not isinstance(projected, dict):
            raise module.CanonicalSerializationError(
                "canonical subset requires a dataclass projection"
            )
        selected = {name: projected[name] for name in names}
        selected["schema"] = "domain"
        expected = hashlib.sha256(
            json.dumps(
                selected, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError, KeyError, AttributeError) as error:
        failure = (type(error), str(error))
    else:
        failure = None
    if failure is None:
        assert value.canonical_subset_sha256(names, schema="domain") == expected
    else:
        with pytest.raises(failure[0]) as caught:
            value.canonical_subset_sha256(names, schema="domain")
        assert str(caught.value) == failure[1]


def test_enum_root_uses_original_enum_value_projection():
    @dataclass(frozen=True)  # noqa: RUF049 - deliberate overlapping dispatch regression
    class EnumOwner(CanonicalMemoized, Enum):
        MEMBER = "x"  # pyright: ignore[reportCallIssue]

    value = EnumOwner.MEMBER
    object.__setattr__(value, "_canonical_memo", CanonicalMemo((EnumOwner,)))
    assert value.canonical_cache_eligible()
    with pytest.raises(TypeError, match="canonical subset requires a dataclass projection"):
        value.canonical_subset_sha256((), schema="domain")
