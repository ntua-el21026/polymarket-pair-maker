"""Reuse numeric validation without reusing observation state or provenance."""

import hashlib
import json
from dataclasses import dataclass, replace
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal
from polymarket_pair_maker.core.ids import IngestId
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.normalization import (
    CanonicalBookLevel,
    CanonicalEvent,
    NormalizationContext,
    OrderBookSnapshotEvent,
    PolymarketNormalizer,
)
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_market_context
from polymarket_pair_maker.state.order_books import BookStateError, CanonicalL2BookOwner


def events() -> tuple[CanonicalEvent, ...]:
    context = demo_market_context(PaperConfig())
    bundle = context.bundle
    raw = json.dumps(
        {
            "event_type": "book",
            "market": bundle.market.native_condition_id,
            "asset_id": bundle.instruments[0].native_token_id,
            "bids": [{"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.60", "size": "20"}],
            "timestamp": "1787198402000",
            "hash": "opaque",
        }
    ).encode()
    normalizer = PolymarketNormalizer()
    return tuple(
        normalizer.normalize(
            raw,
            NormalizationContext(
                IngestId(f"validation-reuse-{index}"),
                hashlib.sha256(raw).hexdigest(),
                UtcTimestamp(context.as_of.nanoseconds_since_epoch + index),
                bundle,
            ),
        )[0]
        for index in range(4)
    )


def test_identical_immutable_numeric_input_reuses_validated_side_objects():
    context = demo_market_context(PaperConfig())
    owner = CanonicalL2BookOwner(context.bundle, context.bundle.instruments[0].instrument_id)
    source = events()
    owner.apply_event(source[0])
    first = owner.snapshot()
    owner.apply_event(source[1])
    second = owner.snapshot()
    assert second.bids is first.bids
    assert second.asks is first.asks
    assert second.generation == first.generation + 1
    assert second.last_source_event_id != first.last_source_event_id
    assert second.state_source_provenance != first.state_source_provenance
    assert second.last_valid_update_time != first.last_valid_update_time


@pytest.mark.parametrize("mutable", [False, True])
def test_identity_hits_equal_complete_numeric_revalidation(mutable: bool):
    context = demo_market_context(PaperConfig())
    instrument = context.bundle.instruments[0].instrument_id
    cached = CanonicalL2BookOwner(context.bundle, instrument)
    reference = CanonicalL2BookOwner(context.bundle, instrument)
    for event in events():
        payload = event.payload
        assert isinstance(payload, OrderBookSnapshotEvent)
        bids = list(payload.bids)
        asks = list(payload.asks)
        uncached_payload = replace(
            payload,
            bids=cast(tuple[CanonicalBookLevel, ...], bids) if mutable else tuple(bids),
            asks=cast(tuple[CanonicalBookLevel, ...], asks) if mutable else tuple(asks),
        )
        reference_event = replace(event, payload=uncached_payload)
        assert cached.apply_event(event) == reference.apply_event(reference_event)
        assert cached.snapshot() == reference.snapshot()
        assert canonical_json_bytes(cached.snapshot()) == canonical_json_bytes(reference.snapshot())


def test_changed_tick_content_cannot_reuse_previous_numeric_validation():
    context = demo_market_context(PaperConfig())
    owner = CanonicalL2BookOwner(context.bundle, context.bundle.instruments[0].instrument_id)
    source = events()
    owner.apply_event(source[0])
    changed = replace(
        context.bundle, precision=replace(context.bundle.precision, tick_size=ExactDecimal(3, 2))
    )
    owner.apply_reference_bundle(changed)
    with pytest.raises(BookStateError, match="aligned"):
        owner.apply_event(source[1])
    assert owner.validation_cache_hits == 0


def test_numeric_cache_hit_does_not_skip_conflicting_source_rejection():
    context = demo_market_context(PaperConfig())
    owner = CanonicalL2BookOwner(context.bundle, context.bundle.instruments[0].instrument_id)
    source = events()
    owner.apply_event(source[0])
    conflicting = replace(
        source[1],
        source_ingest_ids=source[0].source_ingest_ids,
        source_payload_hashes=("a" * 64,),
    )
    with pytest.raises(BookStateError, match="conflict"):
        owner.apply_event(conflicting)
    assert owner.validation_cache_hits == 1


def test_equal_new_tick_object_requires_new_validation():
    context = demo_market_context(PaperConfig())
    owner = CanonicalL2BookOwner(context.bundle, context.bundle.instruments[0].instrument_id)
    source = events()
    owner.apply_event(source[0])
    before = owner.snapshot()
    tick = context.bundle.precision.tick_size
    assert tick is not None
    changed = replace(
        context.bundle,
        precision=replace(
            context.bundle.precision, tick_size=ExactDecimal(tick.coefficient, tick.scale)
        ),
    )
    owner.apply_reference_bundle(changed)
    owner.apply_event(source[1])
    assert owner.validation_cache_hits == 0
    assert owner.snapshot().bids == before.bids
    assert owner.snapshot().bids is not before.bids


def test_unaudited_native_level_subclass_cannot_install_validation_cache():
    @dataclass(frozen=True, slots=True)
    class Variant(CanonicalBookLevel):
        extra: str = "not part of numeric normalization"

    context = demo_market_context(PaperConfig())
    owner = CanonicalL2BookOwner(context.bundle, context.bundle.instruments[0].instrument_id)
    source = events()
    original = source[0].payload
    assert isinstance(original, OrderBookSnapshotEvent)
    payload = replace(
        original, bids=tuple(Variant(level.price, level.quantity) for level in original.bids)
    )
    for event in source:
        owner.apply_event(replace(event, payload=payload))
    assert owner.validation_cache_hits == 0


def test_mutable_source_side_is_revalidated_after_each_change():
    context = demo_market_context(PaperConfig())
    owner = CanonicalL2BookOwner(context.bundle, context.bundle.instruments[0].instrument_id)
    source = events()
    original = source[0].payload
    assert isinstance(original, OrderBookSnapshotEvent)
    levels = list(original.bids)
    payload = replace(original, bids=cast(tuple[CanonicalBookLevel, ...], levels))
    owner.apply_event(replace(source[0], payload=payload))
    levels.append(levels[0])
    with pytest.raises(BookStateError, match="duplicate"):
        owner.apply_event(replace(source[1], payload=payload))
    assert owner.validation_cache_hits == 0
