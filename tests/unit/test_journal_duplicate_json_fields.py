"""Journal JSON rejects ambiguous objects before schema and identity validation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.ids import CollectorRunId, VenueId, WriterId
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.ingestion import RawEnvelopeValidationError, RawEventEnvelope
from polymarket_pair_maker.journal.models import (
    JournalCorruptionError,
    RunCompletionHead,
    SegmentManifest,
)
from polymarket_pair_maker.journal.reader import (
    load_run_completion_head,
    load_segment_manifest,
)

HISTORICAL_ENVELOPE = Path(__file__).parents[1] / "golden/fixtures/historical_raw_envelope_v1.json"


def _segment() -> SegmentManifest:
    return SegmentManifest(
        journal_format_version="raw-journal-manifest-v1",
        writer_id=WriterId("duplicate-field-writer"),
        collector_run_id=CollectorRunId("duplicate-field-run"),
        venue_id=VenueId("polymarket"),
        stream="market",
        first_ingest_seq=0,
        last_ingest_seq=0,
        start_time=UtcTimestamp(1),
        end_time=UtcTimestamp(1),
        event_count=1,
        uncompressed_bytes=100,
        compressed_bytes=80,
        payload_digest="a" * 64,
        segment_digest="b" * 64,
        previous_segment_digest=None,
        writer_software_version="test-v1",
        git_commit="c" * 40,
        created_at=UtcTimestamp(2),
    )


def _completion() -> RunCompletionHead:
    return RunCompletionHead(
        completion_version="raw-journal-run-completion-v1",
        writer_id=WriterId("duplicate-field-writer"),
        collector_run_id=CollectorRunId("duplicate-field-run"),
        venue_id=VenueId("polymarket"),
        stream="market",
        expected_segment_count=1,
        expected_event_count=1,
        expected_uncompressed_bytes=100,
        max_uncompressed_bytes_per_segment=1000,
        max_uncompressed_bytes_per_run=2000,
        first_ingest_seq=0,
        last_ingest_seq=0,
        terminal_manifest_sha256=_segment().manifest_sha256,
        writer_software_version="test-v1",
        git_commit="c" * 40,
        completed_at=UtcTimestamp(3),
    )


@pytest.mark.parametrize("kind", ["segment", "completion"])
@pytest.mark.parametrize("first_count", [1, 999])
def test_manifest_rejects_equal_and_conflicting_duplicate_fields(
    tmp_path: Path, kind: str, first_count: int
) -> None:
    record = _segment().to_record() if kind == "segment" else _completion().to_record()
    key = "event_count" if kind == "segment" else "expected_event_count"
    valid = canonical_json_bytes(record)
    ambiguous = f'{{"{key}":{first_count},'.encode() + valid[1:]
    path = tmp_path / "manifest.json"
    path.write_bytes(ambiguous)
    load = load_segment_manifest if kind == "segment" else load_run_completion_head
    with pytest.raises(JournalCorruptionError, match="duplicate JSON field"):
        load(path)
    assert path.read_bytes() == ambiguous


@pytest.mark.parametrize("first_sequence", [7, 999])
def test_envelope_rejects_equal_and_conflicting_duplicate_fields(first_sequence: int) -> None:
    raw = HISTORICAL_ENVELOPE.read_bytes()
    ambiguous = f'{{"ingest_seq":{first_sequence},'.encode() + raw[1:]
    with pytest.raises(RawEnvelopeValidationError, match="duplicate JSON field"):
        RawEventEnvelope.from_json_line(ambiguous)


@pytest.mark.parametrize("first_port", [443, 80])
def test_envelope_rejects_nested_duplicate_transport_metadata(first_port: int) -> None:
    raw = HISTORICAL_ENVELOPE.read_bytes()
    ambiguous = raw.replace(
        b'"transport_metadata_allowlist":{}',
        f'"transport_metadata_allowlist":{{"remote_port":{first_port},"remote_port":443}}'.encode(),
    )
    assert ambiguous != raw
    with pytest.raises(RawEnvelopeValidationError, match="duplicate JSON field"):
        RawEventEnvelope.from_json_line(ambiguous)


@pytest.mark.parametrize("kind", ["segment", "completion"])
def test_valid_manifest_retains_its_identity(tmp_path: Path, kind: str) -> None:
    expected = _segment() if kind == "segment" else _completion()
    path = tmp_path / "manifest.json"
    raw = canonical_json_bytes(expected.to_record())
    path.write_bytes(raw)
    actual = load_segment_manifest(path) if kind == "segment" else load_run_completion_head(path)
    assert actual == expected
    assert path.read_bytes() == raw


def test_historical_envelope_remains_byte_identical() -> None:
    raw = HISTORICAL_ENVELOPE.read_bytes()
    assert RawEventEnvelope.from_json_line(raw).to_json_line() == raw


def test_raw_payload_json_is_preserved_without_envelope_interpretation() -> None:
    payload = b'{"price":"0.4","price":"0.5"}'
    original = RawEventEnvelope.from_json_line(HISTORICAL_ENVELOPE.read_bytes())
    envelope = replace(
        original, payload_bytes=payload, payload_sha256=hashlib.sha256(payload).hexdigest()
    )
    assert RawEventEnvelope.from_json_line(envelope.to_json_line()) == envelope
