"""Fixed identities captured from the original package before migration.

These vectors protect persisted raw references from import namespace changes.
"""

import hashlib
from pathlib import Path

from polymarket_pair_maker.core.ids import (
    CanonicalEventId,
    InstrumentId,
    MarketId,
    OutcomeId,
    VenueId,
    deterministic_sha256,
)
from polymarket_pair_maker.ingestion import RawEventEnvelope


def test_historical_native_identity_vectors() -> None:
    venue = VenueId("polymarket")
    assert MarketId.from_native(venue, "condition-123").value == (
        "09358f092d2a4db752dd7ea9c0e12a312c8cb486b106a7056157a4f95ac0cf4f"
    )
    assert InstrumentId.from_native(venue, "token-123").value == (
        "41895f4934c64da3c5e0d6bfe5d51c8c2f3755f2db9dcb12ae4dca4f14a73862"
    )
    assert OutcomeId.from_native(venue, "yes-token").value == (
        "3f7946355019034fcc1ef47c6d8c4ea9af1d4736852d201616a4ea2a0803fa9c"
    )


def test_historical_mixed_typed_components_vector() -> None:
    assert (
        deterministic_sha256(
            "migration-vector",
            VenueId("polymarket"),
            MarketId("market-1"),
            InstrumentId("token-1"),
            "raw",
            b"bytes",
            7,
        )
        == "ac1fd687bf191553f744fe3bf569146afb47879ee621c06ba4b709f5aed05855"
    )


def test_historical_canonical_event_identity_vector() -> None:
    assert (
        CanonicalEventId.from_components(
            "event", VenueId("polymarket"), InstrumentId("token-1"), 42
        ).value
        == "10964b0c8374a24e695a584765461c8971eab43e08737e9f5d7f72d4552c25f9"
    )


def test_original_raw_envelope_decodes_and_reencodes_byte_exactly() -> None:
    # Produced by the original source at 3ece7fe, before namespace migration.
    fixture = Path(__file__).parents[1] / "golden/fixtures/historical_raw_envelope_v1.json"
    original = fixture.read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        "9ab91e2a73e0ea3a855c9287250bfde20f261a2f980909023b55005bbe7c6f0f"
    )
    restored = RawEventEnvelope.from_json_line(original)
    assert restored.to_json_line() == original
    assert restored.channel == "mma.control.connection"
    assert restored.received_wall_time.nanoseconds_since_epoch == 1_700_000_000_123_456_789
    assert restored.payload_bytes == b'{"kind":"migration-wire-compatibility"}'
