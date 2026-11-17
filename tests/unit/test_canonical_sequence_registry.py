"""Identity-proven immutable side encodings are bounded and lossless."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from time import sleep
from typing import cast

import pytest

from polymarket_pair_maker.core import serialization as module
from polymarket_pair_maker.core.exact import ExactDecimal, Price, Quantity
from polymarket_pair_maker.core.health import BookHealth
from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.time import MonotonicTimestamp, UtcTimestamp
from polymarket_pair_maker.state.order_books import BookLevel
from tests.unit.test_snapshot_canonical_memo import book, encoded, reference

AUDITED = (BookLevel, Price, Quantity)


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    entries: OrderedDict[int, object] = OrderedDict()
    monkeypatch.setattr(module, "_SEQUENCE_ENCODINGS", entries, raising=False)
    monkeypatch.setattr(module, "_sequence_encoding_bytes", 0, raising=False)


def test_registration_preserves_exact_bytes_fresh_trees_and_whitelist_policy():
    values = book().bids
    assert module.register_canonical_sequence(values, element_type=BookLevel, audited_types=AUDITED)
    for mode in (False, True):
        assert module.canonical_json_bytes(values, allow_floats=mode) == encoded(values)
    projected = module.canonicalize(values)
    assert projected == reference(values)
    assert isinstance(projected, list)
    projected.clear()
    assert module.canonicalize(values) == reference(values)
    assert module.is_canonically_immutable(values, audited_types=AUDITED)
    assert not module.is_canonically_immutable(values, audited_types=())
    assert not module.register_canonical_sequence(values, element_type=Price, audited_types=AUDITED)
    assert not module.register_canonical_sequence(values, element_type=BookLevel, audited_types=())


def test_registry_is_identity_keyed_even_when_identity_lookup_collides(
    monkeypatch: pytest.MonkeyPatch,
):
    first, second = ("first",), ("second",)

    def collision(_value: object) -> int:
        return 0

    monkeypatch.setattr(module, "id", collision, raising=False)
    assert module.register_canonical_sequence(first, element_type=str, audited_types=(str,))
    assert module.canonical_json_bytes(second) == encoded(second)
    assert module.register_canonical_sequence(second, element_type=str, audited_types=(str,))
    assert module.canonical_json_bytes(first) == encoded(first)
    assert module.canonical_json_bytes(second) == encoded(second)


def test_registry_eviction_and_reappearance_have_exact_original_results():
    values = [(f"sequence-{index}",) for index in range(9)]
    for value in values:
        assert module.register_canonical_sequence(value, element_type=str, audited_types=(str,))
    assert module.canonical_sequence_cache_info()["entries"] == 8
    assert module.canonical_json_bytes(values[0]) == encoded(values[0])
    assert module.register_canonical_sequence(values[0], element_type=str, audited_types=(str,))
    assert module.canonical_json_bytes(values[0]) == encoded(values[0])
    assert module.canonical_sequence_cache_info()["entries"] == 8


def test_actual_entry_count_and_byte_bounds_are_enforced():
    full = ("x",) * 4096
    assert module.register_canonical_sequence(full, element_type=str, audited_types=(str,))
    assert not module.register_canonical_sequence(
        (*full, "x"), element_type=str, audited_types=(str,)
    )
    assert not module.register_canonical_sequence(
        ("x" * 65536,), element_type=str, audited_types=(str,)
    )
    for index in range(8):
        value = (bytes((index,)) * 8000,) * 5
        assert module.register_canonical_sequence(value, element_type=bytes, audited_types=(bytes,))
    info = module.canonical_sequence_cache_info()
    assert info["entries"] <= 8
    assert info["encoded_bytes"] <= 262144
    assert info["largest_entry_bytes"] <= 65536
    assert info["retained_items"] <= 8 * 4096


def test_mutable_unknown_subclasses_enums_and_floats_never_gain_proof():
    @dataclass(frozen=True)
    class Unknown:
        value: object

    class Derived(BookLevel):
        pass

    for values, kind, policy in (
        ((Unknown([]),), Unknown, (Unknown,)),
        ((Unknown(1),), Unknown, ()),
        ((Derived(Price.parse("0.4"), Quantity.parse("5")),), BookLevel, AUDITED),
        ((BookHealth.HEALTHY,), BookHealth, (BookHealth,)),
        ((0.5,), float, (float,)),
    ):
        assert not module.register_canonical_sequence(
            values, element_type=kind, audited_types=policy
        )
    projected = module.canonicalize((BookHealth.HEALTHY,))
    assert isinstance(projected, list)
    assert projected[0] is BookHealth.HEALTHY
    assert module.canonical_json_bytes((0.5,), allow_floats=True) == encoded(
        (0.5,), allow_floats=True
    )
    with pytest.raises(TypeError, match="binary float"):
        module.canonical_json_bytes((0.5,))


def test_clearly_oversized_exact_values_bypass_before_encoding(monkeypatch: pytest.MonkeyPatch):
    # Construct the adversarial representation directly: Price's ordinary
    # constructor itself compares scales before optional registration runs.
    price = object.__new__(Price)
    object.__setattr__(price, "value", ExactDecimal(1, 100_000_000))
    values = (BookLevel(price, Quantity.parse("1")),)

    def forbidden(_value: object) -> bytes:
        pytest.fail("oversized optional registration reached encoding")

    monkeypatch.setattr(module, "_encode", forbidden)
    assert not module.register_canonical_sequence(
        values, element_type=BookLevel, audited_types=AUDITED
    )
    assert module.canonical_sequence_cache_info()["entries"] == 0


def test_new_content_and_market_references_keep_independent_identities():
    first = book()
    assert module.register_canonical_sequence(
        first.bids, element_type=BookLevel, audited_types=AUDITED
    )
    changed = (replace(first.bids[0], quantity=Quantity.parse("999")), *first.bids[1:])
    assert module.canonical_json_bytes(changed) == encoded(changed) != encoded(first.bids)
    second = replace(first, market_id=MarketId("different-market"))
    assert first.bids is second.bids
    assert first.content_sha256() != second.content_sha256()


def test_threaded_registration_eviction_and_reads_preserve_bounds_and_bytes():
    def worker(index: int) -> None:
        for iteration in range(40):
            values = (f"{index}-{iteration}",)
            assert module.register_canonical_sequence(
                values, element_type=str, audited_types=(str,)
            )
            sleep(0)
            assert module.canonical_json_bytes(values) == encoded(values)
            assert module.canonicalize(values) == reference(values)
            info = module.canonical_sequence_cache_info()
            assert info["entries"] <= 8
            assert info["encoded_bytes"] <= 262144

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(4)))


def test_registration_rejects_mutable_or_nonclass_policy_metadata():
    values = ("x",)
    for element, policy in (
        (object(), (str,)),
        (str, (object(),)),
        (str, ([str],)),
        (str, [str]),
    ):
        assert not module.register_canonical_sequence(
            values,
            element_type=cast(type[object], element),
            audited_types=cast(tuple[type[object], ...], policy),
        )
    assert module.canonical_sequence_cache_info()["entries"] == 0


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        0,
        -123,
        ExactDecimal(-1, 1),
        '\u03bc\n"',
        b"\x00\xff",
        UtcTimestamp(0),
        MonotonicTimestamp("epoch", 17),
        MarketId("market"),
        (None, False, UtcTimestamp(0)),
    ],
)
def test_registration_estimator_bounds_original_json_encoding(value: object):
    budget = [65536, 262144]
    assert module._registration_resources(value, (), budget)  # pyright: ignore[reportPrivateUsage]
    assert 262144 - budget[1] >= len(encoded(value))


def test_registered_proof_preserves_relative_audit_depth_limit():
    values = (BookLevel(Price.parse("0.4"), Quantity.parse("5")),)
    wrapped: object = values
    for _ in range(31):
        wrapped = (wrapped,)
    assert not module.is_canonically_immutable(wrapped, audited_types=AUDITED)
    assert module.register_canonical_sequence(values, element_type=BookLevel, audited_types=AUDITED)
    assert not module.is_canonically_immutable(wrapped, audited_types=AUDITED)
    within_boundary: object = values
    for _ in range(30):
        within_boundary = (within_boundary,)
    assert module.is_canonically_immutable(within_boundary, audited_types=AUDITED)


def test_registration_estimator_bounds_frozen_field_names_and_exclusions():
    @dataclass(frozen=True)
    class Values:
        first: object
        second: object
        hidden: object = field(metadata={"canonical_serialize": False})

    value = Values((None, False, UtcTimestamp(0)), "\u03bc\n", object())
    budget = [65536, 262144]
    assert module._registration_resources(value, (Values,), budget)  # pyright: ignore[reportPrivateUsage]
    assert 262144 - budget[1] >= len(encoded(value))


def test_nested_null_sequence_resource_cost_includes_every_separator(
    monkeypatch: pytest.MonkeyPatch,
):
    values = ((None,) * 60000,)
    budget = [65536, 1_000_000]
    assert module._registration_resources(values, (), budget)  # pyright: ignore[reportPrivateUsage]
    assert 1_000_000 - budget[1] >= len(encoded(values))

    def forbidden(_value: object) -> bytes:
        pytest.fail("oversized nested sequence reached optional encoding")

    monkeypatch.setattr(module, "_encode", forbidden)
    assert not module.register_canonical_sequence(values, element_type=tuple, audited_types=())


@pytest.mark.parametrize("atom", [None, False, True])
@pytest.mark.parametrize("count", [0, 1, 2, 10, 100])
def test_small_nested_sequence_resource_cost_bounds_original_json(atom: object, count: int):
    value = ((atom,) * count, (atom,))
    budget = [65536, 262144]
    assert module._registration_resources(value, (), budget)  # pyright: ignore[reportPrivateUsage]
    assert 262144 - budget[1] >= len(encoded(value))
