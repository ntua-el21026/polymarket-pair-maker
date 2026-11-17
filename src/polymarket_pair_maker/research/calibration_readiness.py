"""Offline, prior-only support checks; never empirical or economic acceptance.

Upstream report hashes are declarations requiring independent review, not proof
that an execution occurred. This module neither fits nor admits a calibration.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from polymarket_pair_maker.core.ids import CollectorRunId, MarketId, is_sha256_hex
from polymarket_pair_maker.core.provenance import RawSourceRef, merge_raw_source_refs
from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.journal.recovery import fsync_directory
from polymarket_pair_maker.paper.encoding import decode, read_json
from polymarket_pair_maker.reference import BitemporalFields
from polymarket_pair_maker.research.paper_calibration import (
    MARKOUT_HORIZONS_NS,
    CalibrationObservation,
    CalibrationSource,
)

DAY_NS = 86_400_000_000_000
SCHEMA_VERSION = "ppm-calibration-readiness-v1"
Qualification = Literal["PASS", "FAIL", "UNAVAILABLE", "SYNTHETIC_ONLY"]


class ExecutionAttribution(StrEnum):
    OBSERVED_EXECUTION = "OBSERVED_EXECUTION"
    INFERRED_L2 = "INFERRED_L2"
    SYNTHETIC_ONLY = "SYNTHETIC_ONLY"


@dataclass(frozen=True, slots=True)
class ReadinessSource:
    """Declarations for independent review.

    ``times`` dates the reference known/effective at quote formation, not the
    later journal completion. Report availability is checked at analysis time.
    """

    source_sha256: str
    collector_run_id: CollectorRunId
    completion_sha256: str
    collector_status: Qualification
    collector_report_sha256: str | None
    venue_status: Qualification
    venue_report_sha256: str | None
    market_id: MarketId
    parent_event_id: str
    category: str
    protocol_version: str
    times: BitemporalFields
    reports_available_at: UtcTimestamp | None = None

    def __post_init__(self) -> None:
        for value in (self.source_sha256, self.completion_sha256):
            if not is_sha256_hex(value):
                raise ValueError("source and completion require SHA-256 identities")
        for value in (self.collector_report_sha256, self.venue_report_sha256):
            if value is not None and not is_sha256_hex(value):
                raise ValueError("qualification report requires a SHA-256 identity")
        if not self.protocol_version or not self.parent_event_id or not self.category:
            raise ValueError("explicit protocol, parent and category identities required")
        for value in (self.collector_status, self.venue_status):
            if value not in {"PASS", "FAIL", "UNAVAILABLE", "SYNTHETIC_ONLY"}:
                raise ValueError("unknown qualification status")


@dataclass(frozen=True, slots=True)
class ReadinessTrial:
    observation: CalibrationObservation
    parent_event_id: str
    category: str
    protocol_version: str
    attribution: ExecutionAttribution
    raw_sources: tuple[RawSourceRef, ...]
    censored: bool = False
    exposure_ended_at: UtcTimestamp | None = None
    exposure_end_available_at: UtcTimestamp | None = None

    def __post_init__(self) -> None:
        if not self.parent_event_id or not self.category or not self.protocol_version:
            raise ValueError("trial requires parent/category/protocol identity")
        if type(self.attribution) is not ExecutionAttribution:
            raise ValueError("explicit execution attribution required")
        if not self.raw_sources:
            raise ValueError("trial requires raw provenance, including unfilled trials")
        merge_raw_source_refs(self.raw_sources)
        if type(self.censored) is not bool:
            raise ValueError("censoring must be explicit")
        if (self.exposure_ended_at is None) != (self.exposure_end_available_at is None):
            raise ValueError("exposure completion requires both event and availability times")
        if (
            self.exposure_ended_at is not None
            and self.exposure_end_available_at is not None
            and (
                self.exposure_ended_at < self.observation.finished_at
                or self.exposure_end_available_at < self.exposure_ended_at
            )
        ):
            raise ValueError("invalid terminal exposure chronology")


@dataclass(frozen=True, slots=True)
class CalibrationReadinessInput:
    schema_version: str
    source_kind: CalibrationSource
    market_ids: tuple[MarketId, ...]
    protocol_version: str
    population_start: UtcTimestamp
    population_end: UtcTimestamp
    known_at: UtcTimestamp
    maximum_order_or_holding_ns: int | None
    sources: tuple[ReadinessSource, ...]
    trials: tuple[ReadinessTrial, ...]


def _source_usable(
    source: ReadinessSource, row: ReadinessTrial, request: CalibrationReadinessInput
) -> bool:
    at = row.observation.quote_at
    times = source.times
    return (
        source.market_id == row.observation.market_id
        and source.market_id in request.market_ids
        and source.parent_event_id == row.parent_event_id
        and source.category == row.category
        and source.protocol_version == row.protocol_version == request.protocol_version
        and source.collector_status == source.venue_status == "PASS"
        and source.collector_report_sha256 is not None
        and source.venue_report_sha256 is not None
        and source.reports_available_at is not None
        and source.reports_available_at <= request.known_at
        and times.observed_at <= at
        and times.effective_from is not None
        and times.effective_from <= at
        and (times.effective_to is None or at < times.effective_to)
        and (times.superseded_at is None or at < times.superseded_at)
    )


def _validate(request: CalibrationReadinessInput) -> dict[str, ReadinessSource]:
    if request.schema_version != SCHEMA_VERSION:
        raise ValueError("unsupported calibration readiness schema")
    if type(request.source_kind) is not CalibrationSource or not request.protocol_version:
        raise ValueError("explicit source classification and protocol required")
    if not request.market_ids or len(set(request.market_ids)) != len(request.market_ids):
        raise ValueError("explicit unique market domain required")
    start = request.population_start.nanoseconds_since_epoch
    end = request.population_end.nanoseconds_since_epoch
    if start % DAY_NS or end % DAY_NS or end - start < 5 * DAY_NS:
        raise ValueError("population requires at least five declared complete UTC days")
    if request.known_at < request.population_end:
        raise ValueError("analysis cutoff precedes complete population interval")
    lifetime = request.maximum_order_or_holding_ns
    if lifetime is not None and (type(lifetime) is not int or lifetime < 0):
        raise ValueError("maximum outstanding-order or holding lifetime must be finite")
    if len(request.trials) > 100_000 or len(request.sources) > 100_000:
        raise ValueError("readiness batch exceeds bound")
    sources = {source.source_sha256: source for source in request.sources}
    if len(sources) != len(request.sources):
        raise ValueError("duplicate or conflicting source identity")
    market_parents: dict[MarketId, tuple[str, str]] = {}
    collector_bindings: dict[CollectorRunId, tuple[MarketId, str]] = {}
    for source in request.sources:
        identity = (source.parent_event_id, source.category)
        if market_parents.setdefault(source.market_id, identity) != identity:
            raise ValueError("conflicting market parent or category")
        binding = (source.market_id, source.completion_sha256)
        if collector_bindings.setdefault(source.collector_run_id, binding) != binding:
            raise ValueError("conflicting single-target collector run binding")
    seen: set[str] = set()
    parent_categories: dict[str, str] = {}
    for row in request.trials:
        observation = row.observation
        if observation.observation_id in seen:
            raise ValueError("duplicate or conflicting trial identity")
        seen.add(observation.observation_id)
        if observation.source_sha256 not in sources:
            raise ValueError("missing trial source declaration")
        if not request.population_start <= observation.quote_at < request.population_end:
            raise ValueError("trial outside preregistered population")
        category = parent_categories.setdefault(row.parent_event_id, row.category)
        if category != row.category:
            raise ValueError("conflicting parent category")
    merge_raw_source_refs(*(row.raw_sources for row in request.trials))
    return sources


def assess_readiness(request: CalibrationReadinessInput) -> dict[str, object]:
    """Count episode/parent/day support; dependence needs subsequent validation.

    Complete-day splits use declared bounds rather than observed outcome dates.
    A parent is assigned once, to its earliest chronological partition. Later
    partitions exclude that parent's rows, never move future outcomes backwards.
    """
    sources = _validate(request)
    start = request.population_start.nanoseconds_since_epoch
    days = (request.population_end.nanoseconds_since_epoch - start) // DAY_NS
    boundaries = (
        start,
        start + days * 60 // 100 * DAY_NS,
        start + days * 80 // 100 * DAY_NS,
        request.population_end.nanoseconds_since_epoch,
    )
    labels = ("calibration", "validation", "confirmation")
    parent_partition: dict[str, int] = {}
    ordered = sorted(
        request.trials, key=lambda r: (r.observation.quote_at, r.observation.observation_id)
    )
    partition_rows: list[list[ReadinessTrial]] = [[], [], []]
    excluded: dict[str, int] = {}
    strata: dict[tuple[str, int, int], list[ReadinessTrial]] = {}
    horizon_counts: dict[tuple[str, int, int], list[int]] = {}
    for row in ordered:
        observation = row.observation
        key = (observation.outcome, observation.offset_ticks, observation.size_multiplier)
        strata.setdefault(key, [])
        horizon_counts.setdefault(key, [0] * 4)
        at = observation.quote_at.nanoseconds_since_epoch
        partition = 0 if at < boundaries[1] else 1 if at < boundaries[2] else 2
        parent_partition.setdefault(row.parent_event_id, partition)
        reason: str | None = None
        if parent_partition[row.parent_event_id] != partition:
            reason = "PARENT_SPANS_PARTITIONS"
        elif request.maximum_order_or_holding_ns is None:
            reason = "UNKNOWN_MAXIMUM_HOLDING_LIFETIME"
        elif (
            partition
            and at < boundaries[partition] + 300_000_000_000 + request.maximum_order_or_holding_ns
        ):
            reason = "BOUNDARY_EMBARGO"
        elif row.censored:
            reason = "CENSORED_TRIAL"
        elif row.exposure_ended_at is None or row.exposure_end_available_at is None:
            reason = "UNKNOWN_TERMINAL_EXPOSURE"
        elif (
            row.exposure_ended_at.nanoseconds_since_epoch - at > request.maximum_order_or_holding_ns
            or observation.residence_ns > request.maximum_order_or_holding_ns
        ):
            reason = "EXCEEDS_DECLARED_LIFETIME"
        elif row.exposure_end_available_at.nanoseconds_since_epoch >= boundaries[partition + 1]:
            reason = "EXPOSURE_UNAVAILABLE_WITHIN_PARTITION"
        elif not _source_usable(sources[observation.source_sha256], row, request):
            reason = "UNQUALIFIED_OR_INVALID_ASOF_SOURCE"
        elif row.attribution is not ExecutionAttribution.OBSERVED_EXECUTION:
            reason = "UNVERIFIED_EXECUTION_ATTRIBUTION"
        elif observation.available_at.nanoseconds_since_epoch >= boundaries[partition + 1]:
            reason = "OUTCOME_UNAVAILABLE_WITHIN_PARTITION"
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        partition_rows[partition].append(row)
        if partition != 0:
            continue
        strata[key].append(row)
        for index, point in enumerate(observation.markouts):
            if (
                point.observed_at is not None
                and point.available_at is not None
                and observation.fill_at is not None
                and point.exit_price is not None
                and point.observed_at.nanoseconds_since_epoch
                <= observation.fill_at.nanoseconds_since_epoch + point.horizon_ns + 1_000_000_000
                and point.available_at.nanoseconds_since_epoch < boundaries[1]
                and point.available_at <= request.known_at
                and observation.liquidation_depth.value >= observation.filled_quantity.value
            ):
                horizon_counts[key][index] += 1
    support: list[dict[str, object]] = []
    for key, rows in sorted(strata.items()):
        parents = len({row.parent_event_id for row in rows})
        utc_days = len({row.observation.quote_at.nanoseconds_since_epoch // DAY_NS for row in rows})
        horizons = horizon_counts[key]
        support.append(
            {
                "outcome": key[0],
                "offset_ticks": key[1],
                "size_multiplier": key[2],
                "episodes": len(rows),
                "parents": parents,
                "days": utc_days,
                "filled_episodes": sum(row.observation.fill_at is not None for row in rows),
                "horizon_samples": horizons,
                "supported": len(rows) >= 100
                and parents >= 20
                and utc_days >= 10
                and min(horizons) >= 100,
            }
        )
    status = "INSUFFICIENT_EVIDENCE"
    if request.source_kind is CalibrationSource.SYNTHETIC:
        status = "SYNTHETIC_ONLY"
    elif support and all(row["supported"] for row in support):
        status = "READY_FOR_INDEPENDENT_REVIEW"
    return {
        "schema_version": SCHEMA_VERSION,
        "input_sha256": canonical_sha256(request),
        "status": status,
        "source_kind": request.source_kind.value,
        "market_ids": [market.value for market in request.market_ids],
        "protocol_version": request.protocol_version,
        "known_at": request.known_at.to_iso8601(),
        "source_sha256": sorted(sources),
        "trial_count": len(ordered),
        "markout_horizons_ns": list(MARKOUT_HORIZONS_NS),
        "embargo_ns": None
        if request.maximum_order_or_holding_ns is None
        else 300_000_000_000 + request.maximum_order_or_holding_ns,
        "partitions": [
            {
                "name": label,
                "start": UtcTimestamp(boundaries[index]).to_iso8601(),
                "end_exclusive": UtcTimestamp(boundaries[index + 1]).to_iso8601(),
                "episodes": len(partition_rows[index]),
                "parents": len({r.parent_event_id for r in partition_rows[index]}),
                "days": len(
                    {
                        r.observation.quote_at.nanoseconds_since_epoch // DAY_NS
                        for r in partition_rows[index]
                    }
                ),
            }
            for index, label in enumerate(labels)
        ],
        "excluded": dict(sorted(excluded.items())),
        "calibration_support": support,
        "confirmation_economic_status": "NOT_EVALUATED",
        "economic_estimates": None,
        "qualification_boundary": (
            "Declared report hashes and attribution require independent verification; "
            "readiness is never Gate C acceptance."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Bounded, network-free command. Invalid input never creates output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="fresh canonical JSON report")
    args = parser.parse_args(argv)
    try:
        request = decode(CalibrationReadinessInput, read_json(args.input))
        report = assess_readiness(request)
        content = canonical_json_bytes(report) + b"\n"
        with tempfile.TemporaryDirectory(prefix=".ppm-readiness-", dir=args.output.parent) as stage:
            temporary = Path(stage) / "report.json"
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Same-filesystem hard-link publication is atomic and cannot overwrite.
            os.link(temporary, args.output)
            fsync_directory(args.output.parent)
    except (ValueError, TypeError, OSError) as error:
        parser.exit(3, f"readiness input/output error: {error}\n")
    return 0 if report["status"] == "READY_FOR_INDEPENDENT_REVIEW" else 4


if __name__ == "__main__":
    raise SystemExit(main())
