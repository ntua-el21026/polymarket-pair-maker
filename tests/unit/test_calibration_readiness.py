from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import Quantity
from polymarket_pair_maker.core.ids import CollectorRunId, IngestId, MarketId
from polymarket_pair_maker.core.provenance import RawSourceRef
from polymarket_pair_maker.core.serialization import canonical_json_bytes
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.reference import BitemporalFields
from polymarket_pair_maker.research.calibration_readiness import (
    DAY_NS,
    CalibrationReadinessInput,
    ExecutionAttribution,
    ReadinessSource,
    ReadinessTrial,
    assess_readiness,
    main,
)
from polymarket_pair_maker.research.paper_calibration import CalibrationSource
from tests.unit.test_paper_strategy import calibration_observation


def readiness_input(*, synthetic: bool = False) -> CalibrationReadinessInput:
    market = MarketId("readiness-market")
    source = ReadinessSource(
        source_sha256="a" * 64,
        collector_run_id=CollectorRunId("readiness-run"),
        completion_sha256="b" * 64,
        collector_status="PASS",
        collector_report_sha256="c" * 64,
        venue_status="PASS",
        venue_report_sha256="d" * 64,
        market_id=market,
        parent_event_id="parent-0",
        category="category",
        protocol_version="declared-v1",
        times=BitemporalFields(UtcTimestamp(0), UtcTimestamp(0), UtcTimestamp(101 * DAY_NS)),
        reports_available_at=UtcTimestamp(101 * DAY_NS),
    )
    sources = tuple(
        replace(
            source,
            source_sha256=f"{index + 1:064x}",
            collector_run_id=CollectorRunId(f"readiness-run-{index}"),
            completion_sha256=f"{index + 1000:064x}",
            market_id=MarketId(f"readiness-market-{index}"),
            parent_event_id=f"parent-{index}",
        )
        for index in range(20)
    )
    rows: list[ReadinessTrial] = []
    for index in range(100):
        day = index % 20 + 1
        at = day * DAY_NS
        scoped = sources[index % 20]
        original = calibration_observation(scoped.market_id, "YES", index)
        shift = at - original.quote_at.nanoseconds_since_epoch
        observation = replace(
            original,
            source_sha256=scoped.source_sha256,
            quote_at=UtcTimestamp(at),
            finished_at=UtcTimestamp(at + 30_000_000_000),
            available_at=UtcTimestamp(at + 31_000_000_000),
            fill_at=UtcTimestamp(at + 10_000_000_000),
            markouts=tuple(
                replace(
                    point,
                    observed_at=UtcTimestamp(shift + point.observed_at.nanoseconds_since_epoch),
                    available_at=UtcTimestamp(shift + point.available_at.nanoseconds_since_epoch),
                )
                for point in original.markouts
                if point.observed_at is not None and point.available_at is not None
            ),
        )
        rows.append(
            ReadinessTrial(
                observation,
                f"parent-{index % 20}",
                "category",
                "declared-v1",
                ExecutionAttribution.SYNTHETIC_ONLY
                if synthetic
                else ExecutionAttribution.OBSERVED_EXECUTION,
                (RawSourceRef(IngestId(f"raw-{index}"), "e" * 64),),
                exposure_ended_at=UtcTimestamp(at + 50_000_000_000),
                exposure_end_available_at=UtcTimestamp(at + 51_000_000_000),
            )
        )
    return CalibrationReadinessInput(
        schema_version="ppm-calibration-readiness-v1",
        source_kind=(
            CalibrationSource.SYNTHETIC if synthetic else CalibrationSource.VERIFIED_OBSERVATIONS
        ),
        market_ids=tuple(scoped.market_id for scoped in sources),
        protocol_version="declared-v1",
        population_start=UtcTimestamp(0),
        population_end=UtcTimestamp(100 * DAY_NS),
        known_at=UtcTimestamp(101 * DAY_NS),
        maximum_order_or_holding_ns=60_000_000_000,
        sources=sources,
        trials=tuple(rows),
    )


def test_support_is_episode_parent_day_based_and_not_economic_acceptance() -> None:
    request = readiness_input()
    report = assess_readiness(request)
    assert report["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["confirmation_economic_status"] == "NOT_EVALUATED"
    assert report["calibration_support"] == [
        {
            "outcome": "YES",
            "offset_ticks": 0,
            "size_multiplier": 1,
            "episodes": 100,
            "parents": 20,
            "days": 20,
            "filled_episodes": 100,
            "horizon_samples": [100, 100, 100, 100],
            "supported": True,
        }
    ]
    reordered = assess_readiness(replace(request, trials=tuple(reversed(request.trials))))
    assert reordered.pop("input_sha256") != report["input_sha256"]
    assert reordered == {key: value for key, value in report.items() if key != "input_sha256"}


@pytest.mark.parametrize(
    "kind", [ExecutionAttribution.INFERRED_L2, ExecutionAttribution.SYNTHETIC_ONLY]
)
def test_inferred_and_synthetic_trials_never_qualify_real_support(
    kind: ExecutionAttribution,
) -> None:
    request = readiness_input()
    request = replace(
        request, trials=tuple(replace(row, attribution=kind) for row in request.trials)
    )
    report = assess_readiness(request)
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert cast(list[dict[str, object]], report["calibration_support"])[0]["episodes"] == 0


def test_synthetic_support_is_labelled_synthetic_only() -> None:
    assert assess_readiness(readiness_input(synthetic=True))["status"] == "SYNTHETIC_ONLY"


@pytest.mark.parametrize(
    "case", ["market", "protocol", "future-reference", "unknown-effective", "expired", "superseded"]
)
def test_scope_and_asof_fail_closed(case: str) -> None:
    request = readiness_input()
    source = request.sources[0]
    if case == "market":
        source = replace(source, market_id=MarketId("other"))
    elif case == "protocol":
        source = replace(source, protocol_version="other")
    elif case == "future-reference":
        source = replace(source, times=replace(source.times, observed_at=UtcTimestamp(50 * DAY_NS)))
    elif case == "unknown-effective":
        source = replace(source, times=replace(source.times, effective_from=None))
    elif case == "expired":
        source = replace(source, times=replace(source.times, effective_to=UtcTimestamp(DAY_NS)))
    else:
        source = replace(source, times=replace(source.times, superseded_at=UtcTimestamp(DAY_NS)))
    report = assess_readiness(replace(request, sources=(source, *request.sources[1:])))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert cast(list[dict[str, object]], report["calibration_support"])[0]["episodes"] == 95


@pytest.mark.parametrize(
    "case",
    [
        "duplicate-trial",
        "duplicate-source",
        "raw-conflict",
        "missing-source",
        "bad-version",
        "non-day-bounds",
    ],
)
def test_malformed_or_conflicting_input_is_rejected(case: str) -> None:
    request = readiness_input()
    if case == "duplicate-trial":
        request = replace(request, trials=(*request.trials, request.trials[0]))
    elif case == "duplicate-source":
        request = replace(request, sources=(*request.sources, request.sources[0]))
    elif case == "raw-conflict":
        row = replace(request.trials[1], raw_sources=(RawSourceRef(IngestId("raw-0"), "f" * 64),))
        request = replace(request, trials=(request.trials[0], row))
    elif case == "missing-source":
        request = replace(request, sources=())
    elif case == "bad-version":
        request = replace(request, schema_version="v999")
    else:
        request = replace(request, population_start=UtcTimestamp(1))
    with pytest.raises(ValueError, match=r"duplicate|conflicting|missing|schema|complete UTC"):
        assess_readiness(request)


def test_unavailable_lifetime_or_upstream_gate_blocks_support() -> None:
    request = readiness_input()
    for changed in (
        replace(request, maximum_order_or_holding_ns=None),
        replace(
            request,
            sources=tuple(replace(source, collector_status="FAIL") for source in request.sources),
        ),
        replace(
            request,
            sources=tuple(
                replace(source, venue_status="UNAVAILABLE") for source in request.sources
            ),
        ),
        replace(
            request,
            sources=tuple(
                replace(source, collector_report_sha256=None) for source in request.sources
            ),
        ),
    ):
        assert assess_readiness(changed)["status"] == "INSUFFICIENT_EVIDENCE"


def test_missing_or_unavailable_markouts_never_become_zeros() -> None:
    request = readiness_input()
    row = request.trials[0]
    observation = replace(
        row.observation,
        markouts=tuple(
            replace(p, observed_at=None, available_at=None, exit_price=None)
            for p in row.observation.markouts
        ),
    )
    report = assess_readiness(
        replace(request, trials=(replace(row, observation=observation), *request.trials[1:]))
    )
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert (
        cast(list[dict[str, object]], report["calibration_support"])[0]["horizon_samples"]
        == [99] * 4
    )


def test_empty_real_batch_has_explicit_unavailable_support() -> None:
    request = readiness_input()
    report = assess_readiness(replace(request, sources=(), trials=()))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["calibration_support"] == []
    assert report["economic_estimates"] is None


def move_trial(row: ReadinessTrial, at: int, *, identity: str) -> ReadinessTrial:
    observation = row.observation
    shift = at - observation.quote_at.nanoseconds_since_epoch

    def shifted(value: UtcTimestamp | None) -> UtcTimestamp | None:
        return None if value is None else UtcTimestamp(value.nanoseconds_since_epoch + shift)

    updated = replace(
        observation,
        observation_id=identity,
        quote_at=UtcTimestamp(at),
        finished_at=UtcTimestamp(observation.finished_at.nanoseconds_since_epoch + shift),
        available_at=UtcTimestamp(observation.available_at.nanoseconds_since_epoch + shift),
        fill_at=shifted(observation.fill_at),
        markouts=tuple(
            replace(
                point,
                observed_at=shifted(point.observed_at),
                available_at=shifted(point.available_at),
            )
            for point in observation.markouts
        ),
    )
    return replace(
        row,
        observation=updated,
        raw_sources=(RawSourceRef(IngestId(identity), "e" * 64),),
        exposure_ended_at=shifted(row.exposure_ended_at),
        exposure_end_available_at=shifted(row.exposure_end_available_at),
    )


def test_declared_day_splits_parent_exclusion_embargo_and_missing_horizon() -> None:
    request = readiness_input()
    first = request.trials[0]
    at_boundary = move_trial(first, 60 * DAY_NS, identity="boundary")
    at_boundary = replace(at_boundary, parent_event_id="new-validation-parent")
    late_parent = move_trial(first, 61 * DAY_NS, identity="same-parent-later-partition")
    late_markout = move_trial(first, 60 * DAY_NS - 100_000_000_000, identity="late-horizon")
    report = assess_readiness(
        replace(request, trials=(first, at_boundary, late_parent, late_markout))
    )
    assert report["excluded"] == {"BOUNDARY_EMBARGO": 1, "PARENT_SPANS_PARTITIONS": 1}
    support = cast(list[dict[str, object]], report["calibration_support"])[0]
    assert support["horizon_samples"] == [2, 2, 2, 1]


@pytest.mark.parametrize(
    "case", ["censored", "unknown-exposure", "long-exposure", "long-residence", "future-exposure"]
)
def test_terminal_exposure_must_be_observed_and_within_declared_lifetime(case: str) -> None:
    request = readiness_input()
    row = request.trials[0]
    if case == "censored":
        row = replace(row, censored=True)
    elif case == "unknown-exposure":
        row = replace(row, exposure_ended_at=None, exposure_end_available_at=None)
    elif case == "long-exposure":
        row = replace(
            row,
            exposure_ended_at=UtcTimestamp(DAY_NS + 61_000_000_000),
            exposure_end_available_at=UtcTimestamp(DAY_NS + 62_000_000_000),
        )
    elif case == "long-residence":
        row = replace(row, observation=replace(row.observation, residence_ns=61_000_000_000))
    else:
        row = replace(row, exposure_end_available_at=UtcTimestamp(60 * DAY_NS))
    report = assess_readiness(replace(request, trials=(row, *request.trials[1:])))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert cast(list[dict[str, object]], report["calibration_support"])[0]["episodes"] == 99


def test_offline_cli_is_deterministic_and_refuses_overwrite(tmp_path: Path) -> None:
    request = readiness_input(synthetic=True)
    source = tmp_path / "input.json"
    source.write_bytes(canonical_json_bytes(request))
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert main([str(source), "--output", str(first)]) == 4
    assert main([str(source), "--output", str(second)]) == 4
    assert first.read_bytes() == second.read_bytes()
    before = first.read_bytes()
    with pytest.raises(SystemExit, match="3"):
        main([str(source), "--output", str(first)])
    assert first.read_bytes() == before


@pytest.mark.parametrize(
    "payload", ['{"schema_version":"x","schema_version":"y"}', '{"unknown":1}', '{"truncated":']
)
def test_malformed_json_cli_publishes_nothing(tmp_path: Path, payload: str) -> None:
    source, output = tmp_path / "bad.json", tmp_path / "report.json"
    source.write_text(payload)
    with pytest.raises(SystemExit, match="3"):
        main([str(source), "--output", str(output)])
    assert not output.exists()


def test_one_market_cannot_manufacture_distinct_parent_events() -> None:
    request = readiness_input()
    conflicting = replace(request.sources[0], source_sha256="a" * 64, parent_event_id="fake-parent")
    with pytest.raises(ValueError, match="conflicting market parent"):
        assess_readiness(replace(request, sources=(*request.sources, conflicting)))


def test_unfilled_trial_stays_in_denominator_and_missing_markouts_stay_missing() -> None:
    request = readiness_input()
    row = request.trials[0]
    unfilled = replace(
        row.observation,
        fill_at=None,
        filled_quantity=Quantity.parse(0),
        queue_cleared=False,
        markouts=tuple(
            replace(point, observed_at=None, available_at=None, exit_price=None)
            for point in row.observation.markouts
        ),
    )
    report = assess_readiness(
        replace(request, trials=(replace(row, observation=unfilled), *request.trials[1:]))
    )
    support = cast(list[dict[str, object]], report["calibration_support"])[0]
    assert support["episodes"] == 100
    assert support["filled_episodes"] == 99
    assert support["horizon_samples"] == [99] * 4
    assert support["supported"] is False


def test_future_qualification_report_is_not_available_at_analysis_time() -> None:
    request = readiness_input()
    future = UtcTimestamp(request.known_at.nanoseconds_since_epoch + 1)
    report = assess_readiness(
        replace(
            request,
            sources=tuple(
                replace(source, reports_available_at=future) for source in request.sources
            ),
        )
    )
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["excluded"] == {"UNQUALIFIED_OR_INVALID_ASOF_SOURCE": 100}


def test_one_collector_run_cannot_claim_multiple_target_conditions() -> None:
    request = readiness_input()
    source = replace(request.sources[1], collector_run_id=request.sources[0].collector_run_id)
    with pytest.raises(ValueError, match="conflicting single-target collector"):
        assess_readiness(
            replace(request, sources=(request.sources[0], source, *request.sources[2:]))
        )
