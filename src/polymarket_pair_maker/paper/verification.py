"""Bounded verification and lossless reconstruction of completed paper artifacts.

Version 2 exposes owner record order, so all stream records participate in the
verified per-scenario state chain. Historical version 1 retains its original
container hash and typed payload interpretation; it has no cross-stream record
ordinal from which an independent reader could recover its state-chain order.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from polymarket_pair_maker.core.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    unique_json_object,
)
from polymarket_pair_maker.paper.artifacts import ArtifactIdentity
from polymarket_pair_maker.paper.encoding import decode
from polymarket_pair_maker.paper.execution import QueueScenario
from polymarket_pair_maker.paper.normalization import ArtifactReconstructor
from polymarket_pair_maker.strategy.engine import (
    CandidateEvaluation,
    MarketRanking,
    StrategyDecision,
)

MAXIMUM_OUTPUT_BYTES = 268_435_456
MAXIMUM_RECORD_BYTES = 16_777_216
_STREAMS = frozenset(
    {
        "decisions.jsonl",
        "economics.jsonl",
        "evaluation-payloads.jsonl",
        "selection.jsonl",
        "orders.jsonl",
        "ledger.jsonl",
        "risk.jsonl",
        "pressure.jsonl",
        "liquidations.jsonl",
        "incentives.jsonl",
    }
)
_NORMALIZED = frozenset(
    {"decisions.jsonl", "economics.jsonl", "selection.jsonl", "evaluation-payloads.jsonl"}
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "classification",
        "git_commit",
        "source_sha256",
        "runtime",
        "raw_run_id",
        "raw_completion_sha256",
        "raw_terminal_manifest_sha256",
        "source_producer_git_commit",
        "phase_config_hash",
        "paper_config_hash",
        "paper_configuration",
        "collateral_asset_id",
        "collateral_symbol",
        "economic_evidence_sha256",
        "reference_versions",
        "calibrations",
        "collector_qualification",
        "historical_six_hour_gate1",
        "simulation_assumptions",
        "seed",
        "scenario_identity",
        "raw_segments",
        "terminal_result_hash",
        "status",
        "artifacts",
    }
)
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "classification",
        "raw_record_count",
        "canonical_processor_terminal_hash",
        "canonical_parse_failures",
        "scenarios",
        "empirical_profitability",
        "live_execution",
        "verified_payments",
        "terminal_result_hash",
    }
)
_SCENARIOS = tuple(QueueScenario)
_SCENARIO_ORDINAL = {item: index for index, item in enumerate(_SCENARIOS)}
type LogicalRecord = tuple[str, QueueScenario, int, object]


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    schema_version: str
    raw_record_count: int
    artifact_bytes: int
    artifact_count: int
    record_count: int
    logical_record_count: int
    terminal_result_hash: str
    scenario_state_chains_verified: bool


@dataclass(frozen=True, slots=True)
class _Container:
    version: int
    manifest: dict[str, object]
    report: dict[str, object]
    inventory: tuple[ArtifactIdentity, ...]
    first_seq: int
    last_seq: int
    total_bytes: int
    manifest_sha256: str


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("paper artifact requires a JSON object")
    return cast(dict[str, object], value)


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("paper artifact requires an exact bounded integer")
    return value


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("paper artifact requires a bounded JSON array")
    if len(cast(list[object], value)) > 100_000:
        raise ValueError("paper artifact requires a bounded JSON array")
    return cast(list[object], value)


def _read_canonical(raw: bytes) -> dict[str, object]:
    if not raw.endswith(b"\n") or len(raw) > MAXIMUM_RECORD_BYTES:
        raise ValueError("truncated or oversized canonical paper record")
    try:
        value: object = json.loads(raw, object_pairs_hook=unique_json_object)
        if canonical_json_bytes(value) + b"\n" != raw:
            raise ValueError("paper artifact is not canonical JSON")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid canonical paper JSON") from exc
    return _mapping(value)


def _document(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("paper artifact must be a regular file")
    with path.open("rb") as handle:
        return _read_canonical(handle.read(MAXIMUM_RECORD_BYTES + 1))


def _file_identity(path: Path) -> ArtifactIdentity:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1_048_576):
            digest.update(block)
    return ArtifactIdentity(path.name, path.stat().st_size, digest.hexdigest())


def _container(root: Path) -> _Container:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("paper result must be a regular directory")
    paths = tuple(sorted(root.iterdir()))
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("paper output inventory contains a non-regular file")
    total = sum(path.stat().st_size for path in paths)
    if total > MAXIMUM_OUTPUT_BYTES:
        raise ValueError("paper output exceeds cumulative byte bound")
    manifest = _document(root / "session-manifest.json")
    schema = manifest.get("schema_version")
    if schema == "ppm-paper-session-v1":
        version = 1
        expected_fields = _MANIFEST_FIELDS
    elif schema == "ppm-paper-session-v2":
        version = 2
        expected_fields = _MANIFEST_FIELDS | {"artifact_schema_version", "state_chain_version"}
        if (
            manifest.get("artifact_schema_version") != "ppm-paper-artifacts-v2"
            or manifest.get("state_chain_version") != "ppm-paper-state-chain-v2"
        ):
            raise ValueError("unsupported normalized artifact or state-chain version")
    else:
        raise ValueError("unsupported paper session schema version")
    if set(manifest) != set(expected_fields):
        raise ValueError("paper manifest fields differ from its declared schema")
    report = _document(root / "report.json")
    if (
        set(report) != set(_REPORT_FIELDS)
        or report.get("schema_version") != f"ppm-paper-report-v{version}"
    ):
        raise ValueError("paper report fields or version differ from session schema")
    status = report.get("status")
    if status not in {"COMPLETED", "BLOCKED_CANONICAL_INTEGRITY"} or manifest["status"] != status:
        raise ValueError("paper completion status is absent or inconsistent")
    if manifest["classification"] != report["classification"]:
        raise ValueError("paper classification differs across completion documents")
    if manifest["scenario_identity"] != [item.value for item in _SCENARIOS]:
        raise ValueError("paper scenario inventory is incomplete or reordered")
    parse_failures = _integer(report["canonical_parse_failures"])
    if status == "COMPLETED" and parse_failures:
        raise ValueError("completed paper report contains canonical parse failures")
    count = _integer(report["raw_record_count"], minimum=1)
    configuration = _mapping(manifest["paper_configuration"])
    if canonical_sha256(configuration) != manifest["paper_config_hash"]:
        raise ValueError("paper configuration hash mismatch")
    maximum = _integer(configuration.get("maximum_output_bytes"), minimum=1)
    if maximum > MAXIMUM_OUTPUT_BYTES or total > maximum:
        raise ValueError("paper output violates its declared cumulative budget")
    inventory = tuple(decode(ArtifactIdentity, item) for item in _sequence(manifest["artifacts"]))
    names = tuple(item.name for item in inventory)
    if names != tuple(sorted(set(names))) or "report.json" not in names:
        raise ValueError("paper inventory is duplicate, reordered or missing report")
    expected_names = {"session-manifest.json", *names}
    if {path.name for path in paths} != expected_names:
        raise ValueError("paper output differs from exact manifest inventory")
    allowed_streams = _STREAMS if version == 2 else _STREAMS - {"evaluation-payloads.jsonl"}
    if set(names) - (allowed_streams | {"report.json"}):
        raise ValueError("paper inventory contains an unsupported artifact")
    for expected in inventory:
        if _file_identity(root / expected.name) != expected:
            raise ValueError("paper artifact byte count or SHA-256 mismatch")
    base_manifest = {
        key: value
        for key, value in manifest.items()
        if key not in {"terminal_result_hash", "status", "artifacts"}
    }
    base_report = {key: value for key, value in report.items() if key != "terminal_result_hash"}
    pre_report = tuple(item for item in inventory if item.name != "report.json")
    terminal = canonical_sha256((base_manifest, pre_report, base_report))
    if report["terminal_result_hash"] != terminal or manifest["terminal_result_hash"] != terminal:
        raise ValueError("paper terminal result hash mismatch")
    ranges = [_mapping(item) for item in _sequence(manifest["raw_segments"])]
    if not ranges:
        raise ValueError("paper raw segment provenance is empty")
    first = _integer(ranges[0].get("first_ingest_seq"))
    last = first - 1
    for segment in ranges:
        start = _integer(segment.get("first_ingest_seq"))
        end = _integer(segment.get("last_ingest_seq"))
        if start != last + 1 or end < start:
            raise ValueError("paper source segment chronology is inconsistent")
        last = end
    if count != last - first + 1:
        raise ValueError("paper processed count differs from raw segment provenance")
    return _Container(
        version,
        manifest,
        report,
        inventory,
        first,
        last,
        total,
        hashlib.sha256(canonical_json_bytes(manifest) + b"\n").hexdigest(),
    )


def _unchanged(root: Path, container: _Container) -> None:
    paths = tuple(sorted(root.iterdir()))
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("paper output changed during verification")
    if {path.name for path in paths} != {
        "session-manifest.json",
        *(item.name for item in container.inventory),
    }:
        raise ValueError("paper output inventory changed during verification")
    for item in container.inventory:
        if _file_identity(root / item.name) != item:
            raise ValueError("paper artifact changed during verification")
    if (
        hashlib.sha256((root / "session-manifest.json").read_bytes()).hexdigest()
        != container.manifest_sha256
    ):
        raise ValueError("paper completion manifest changed during verification")


def _next_record(handle: BinaryIO, name: str) -> dict[str, object] | None:
    raw = handle.readline(MAXIMUM_RECORD_BYTES + 1)
    if not raw:
        return None
    row = _read_canonical(raw)
    if set(row) != {"scenario", "ingest_seq", "record_index", "value"}:
        raise ValueError(f"normalized record envelope fields differ: {name}")
    return row


def _key(row: dict[str, object]) -> tuple[int, int, int]:
    scenario = QueueScenario(row["scenario"])
    return (
        _integer(row["ingest_seq"]),
        _SCENARIO_ORDINAL[scenario],
        _integer(row["record_index"]),
    )


def _normalized_records(root: Path, container: _Container) -> Iterator[LogicalRecord]:
    handles: list[BinaryIO] = []
    heap: list[tuple[tuple[int, int, int], int, str, dict[str, object]]] = []
    previous: dict[str, tuple[int, int, int]] = {}
    indices = dict.fromkeys(_SCENARIOS, 0)
    chains = {item: canonical_sha256(("ppm-paper-state-chain-v2", item)) for item in _SCENARIOS}
    reconstructors = {item: ArtifactReconstructor(item) for item in _SCENARIOS}
    try:
        for item in container.inventory:
            if not item.name.endswith(".jsonl"):
                continue
            handle = (root / item.name).open("rb")
            handles.append(handle)
            row = _next_record(handle, item.name)
            if row is not None:
                heapq.heappush(heap, (_key(row), len(handles) - 1, item.name, row))
        while heap:
            key, handle_index, name, row = heapq.heappop(heap)
            if name in previous and key <= previous[name]:
                raise ValueError("paper stream chronology is duplicate or regressed")
            previous[name] = key
            seq, scenario_index, index = key
            if not container.first_seq <= seq <= container.last_seq:
                raise ValueError("paper record lies outside raw chronology")
            scenario = _SCENARIOS[scenario_index]
            if index != indices[scenario]:
                raise ValueError("paper scenario record order is missing or conflicting")
            indices[scenario] += 1
            chains[scenario] = canonical_sha256((chains[scenario], name, row))
            value: object = row["value"]
            if name in _NORMALIZED:
                value = reconstructors[scenario].consume(name, seq, value)
            yield name, scenario, seq, value
            following = _next_record(handles[handle_index], name)
            if following is not None:
                heapq.heappush(heap, (_key(following), handle_index, name, following))
        for reconstructor in reconstructors.values():
            reconstructor.finish()
        reports = [_mapping(item) for item in _sequence(container.report["scenarios"])]
        if [item.get("scenario") for item in reports] != [item.value for item in _SCENARIOS]:
            raise ValueError("paper scenario reports differ from manifest inventory")
        for scenario, report in zip(_SCENARIOS, reports, strict=True):
            if report.get("state_chain_sha256") != chains[scenario]:
                raise ValueError("paper scenario state-chain hash mismatch")
    finally:
        for handle in handles:
            handle.close()


def _legacy_records(root: Path, container: _Container) -> Iterator[LogicalRecord]:
    for item in container.inventory:
        if not item.name.endswith(".jsonl"):
            continue
        previous: dict[QueueScenario, int] = {}
        with (root / item.name).open("rb") as handle:
            while raw := handle.readline(MAXIMUM_RECORD_BYTES + 1):
                row = _read_canonical(raw)
                if set(row) != {"scenario", "ingest_seq", "value"}:
                    raise ValueError("legacy paper envelope differs from its schema")
                scenario = QueueScenario(row["scenario"])
                seq = _integer(row["ingest_seq"])
                if not container.first_seq <= seq <= container.last_seq or seq < previous.get(
                    scenario, seq
                ):
                    raise ValueError("legacy paper stream chronology is invalid")
                previous[scenario] = seq
                value = row["value"]
                if item.name == "decisions.jsonl":
                    value = decode(StrategyDecision, value)
                elif item.name == "selection.jsonl":
                    value = decode(MarketRanking, value)
                elif item.name == "economics.jsonl":
                    value = tuple(decode(CandidateEvaluation, entry) for entry in _sequence(value))
                yield item.name, scenario, seq, value


def iter_reconstructed_records(root: Path) -> Iterator[LogicalRecord]:
    """Yield verified logical values; exhaust iteration to verify terminal chains.

    Container bytes are checked before the first yield. A caller requiring an
    all-or-nothing result must use :func:`verify_paper_artifacts` first. Memory is
    bounded by one record per stream and the current scenario decision boundary.
    """
    container = _container(root)
    if container.version == 2:
        yield from _normalized_records(root, container)
    else:
        yield from _legacy_records(root, container)
    _unchanged(root, container)


def verify_paper_artifacts(root: Path) -> ArtifactVerification:
    """Verify every container byte, record, normalized relationship and state chain."""
    container = _container(root)
    records = (
        _normalized_records(root, container)
        if container.version == 2
        else _legacy_records(root, container)
    )
    count = 0
    logical = 0
    for name, _scenario, _seq, _value in records:
        count += 1
        logical += name in _NORMALIZED
    _unchanged(root, container)
    return ArtifactVerification(
        str(container.manifest["schema_version"]),
        _integer(container.report["raw_record_count"], minimum=1),
        container.total_bytes,
        len(container.inventory),
        count,
        logical,
        str(container.report["terminal_result_hash"]),
        container.version == 2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = verify_paper_artifacts(args.output)
    print(canonical_json_bytes(result).decode())


if __name__ == "__main__":
    main()
