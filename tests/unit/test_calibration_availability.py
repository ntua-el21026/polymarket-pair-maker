from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from polymarket_pair_maker.core.config import load_phase1_config
from polymarket_pair_maker.observability import MetricsSnapshot
from polymarket_pair_maker.pipeline.calibration import (
    _measurement,  # pyright: ignore[reportPrivateUsage]
)
from polymarket_pair_maker.pipeline.observe import ObservationSummary
from polymarket_pair_maker.research import (
    BoundedTreeUsage,
    CalibrationMeasurements,
    build_calibration_report,
    write_calibration_artifacts,
)

from .test_calibration import (
    _identity,  # pyright: ignore[reportPrivateUsage]
    _limits,  # pyright: ignore[reportPrivateUsage]
    _measurements,  # pyright: ignore[reportPrivateUsage]
)


def _pipeline_measurement(*, availability: SimpleNamespace) -> CalibrationMeasurements:
    config = load_phase1_config(Path(__file__).parents[2] / "config/phase1.toml")
    summary = cast(
        ObservationSummary,
        SimpleNamespace(
            websocket_observation_nanoseconds=180_000_000_000,
            runtime_health=SimpleNamespace(
                ingress=SimpleNamespace(
                    received=100,
                    received_bytes=100_000,
                    queue_high_water=0,
                    overflows=0,
                ),
                data_loss_count=0,
            ),
            journal_metrics=SimpleNamespace(
                durable=100,
                run_uncompressed_bytes=100_000,
                durability_groups=100,
                data_fsyncs=100,
                queue_high_water=0,
                writer_failures=0,
            ),
            processor=SimpleNamespace(
                canonical_events=100,
                eligibility_disposition_counts=(),
                rest_audit_matches=1,
                rest_audit_mismatches=0,
                rest_audit_raced=0,
                rest_audit_failed=0,
            ),
            analytical_outputs=SimpleNamespace(queue_high_water=0, research_eligible=True),
            health_evidence=SimpleNamespace(
                queue_high_water=0, file_bytes=10_000, gate1_evidence_eligible=True
            ),
            health_decision=SimpleNamespace(
                capture_healthy=True, research_eligible=True, gate1_eligible_so_far=True
            ),
            observability_metrics=MetricsSnapshot(counters=(), gauges=(), histograms=()),
            availability_evidence=availability,
        ),
    )
    return _measurement(
        config=config,
        summary=summary,
        storage_before=BoundedTreeUsage(0, 0),
        storage_after=BoundedTreeUsage(10, 100_000),
        analytical_before=BoundedTreeUsage(0, 0),
        analytical_after=BoundedTreeUsage(3, 10_000),
        cpu_time_ns=1_000_000,
        peak_rss_bytes=10_000_000,
    )


def test_calibration_refuses_projected_availability_cap_and_queue_threats() -> None:
    measurement = _pipeline_measurement(
        availability=SimpleNamespace(
            queue_high_water=58,
            file_bytes=500_000,
            accepted=50,
            written=50,
            rejected=0,
            completed=True,
            gate1_evidence_eligible=True,
            failure_type=None,
        )
    )

    report = build_calibration_report(_identity(), measurement, _limits())

    assert report.decision.passed is False
    assert {reason.value for reason in report.decision.reasons} == {
        "AVAILABILITY_FILE_LIMIT_THREATENED",
        "AVAILABILITY_INTERVAL_COUNT_LIMIT_THREATENED",
        "AVAILABILITY_QUEUE_NEAR_CAPACITY",
    }
    assert report.projection.to_record()["availability_bytes"] == 120_000_000
    assert report.projection.to_record()["availability_intervals"] == 12_000


def test_calibration_availability_disk_workload_precedes_safety_and_reserve() -> None:
    baseline = build_calibration_report(_identity(), _measurements(), _limits())
    measurement = replace(
        _measurements(),
        availability_bytes=500,
        availability_intervals_accepted=1,
        availability_intervals_written=1,
    )

    report = build_calibration_report(_identity(), measurement, _limits())

    assert report.decision.passed is True
    assert report.projection.to_record()["availability_bytes"] == 72_000
    assert (
        report.projection.total_required_disk_bytes - baseline.projection.total_required_disk_bytes
        == 90_000
    )
    insufficient = build_calibration_report(
        _identity(),
        measurement,
        _limits(free_disk_bytes=report.projection.total_required_disk_bytes - 1),
    )
    assert [reason.value for reason in insufficient.decision.reasons] == ["FREE_DISK_INSUFFICIENT"]


@pytest.mark.parametrize(
    ("measurement_changes", "limit_changes", "expected_reason"),
    [
        (
            {
                "availability_bytes": 1,
                "availability_intervals_accepted": 1,
                "availability_intervals_written": 1,
            },
            {"availability_maximum_file_bytes": 144},
            "AVAILABILITY_FILE_LIMIT_THREATENED",
        ),
        (
            {
                "availability_bytes": 1,
                "availability_intervals_accepted": 1,
                "availability_intervals_written": 1,
            },
            {"availability_maximum_intervals": 144},
            "AVAILABILITY_INTERVAL_COUNT_LIMIT_THREATENED",
        ),
        (
            {"availability_intervals_accepted": 2, "availability_intervals_written": 1},
            {},
            "AVAILABILITY_EVIDENCE_INCONSISTENT",
        ),
        ({"availability_intervals_rejected": 1}, {}, "AVAILABILITY_EVIDENCE_INCONSISTENT"),
        ({"availability_bytes": 1}, {}, "AVAILABILITY_EVIDENCE_INCONSISTENT"),
        ({"availability_writer_failure_count": 1}, {}, "AVAILABILITY_WRITER_FAILURE"),
    ],
)
def test_calibration_availability_exact_bounds_and_counter_failures(
    measurement_changes: dict[str, int],
    limit_changes: dict[str, int],
    expected_reason: str,
) -> None:
    report = build_calibration_report(
        _identity(), replace(_measurements(), **measurement_changes), _limits(**limit_changes)
    )

    assert report.decision.passed is False
    assert expected_reason in {reason.value for reason in report.decision.reasons}


@pytest.mark.parametrize("value", [None, True, -1, 1.5])
@pytest.mark.parametrize(
    "name",
    [
        "availability_bytes",
        "availability_intervals_accepted",
        "availability_intervals_written",
        "availability_intervals_rejected",
        "availability_writer_failure_count",
    ],
)
def test_calibration_refuses_unknown_or_invalid_availability_measurements(
    name: str, value: object
) -> None:
    with pytest.raises(ValueError, match="availability"):
        replace(_measurements(), **{name: value})


def test_calibration_refuses_missing_availability_queue_evidence() -> None:
    with pytest.raises(ValueError, match="availability"):
        replace(
            _measurements(),
            queues=tuple(queue for queue in _measurements().queues if queue.name != "availability"),
        )


@pytest.mark.parametrize(
    "changes",
    [{"completed": False}, {"gate1_evidence_eligible": False}, {"failure_type": "OSError"}],
)
def test_calibration_pipeline_retains_availability_writer_failure(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "queue_high_water": 0,
        "file_bytes": 0,
        "accepted": 0,
        "written": 0,
        "rejected": 0,
        "completed": True,
        "gate1_evidence_eligible": True,
        "failure_type": None,
    }
    values.update(changes)
    report = build_calibration_report(
        _identity(), _pipeline_measurement(availability=SimpleNamespace(**values)), _limits()
    )

    assert report.decision.passed is False
    assert "AVAILABILITY_WRITER_FAILURE" in {reason.value for reason in report.decision.reasons}


def test_calibration_availability_queue_below_ninety_percent_can_pass() -> None:
    report = build_calibration_report(
        _identity(),
        replace(
            _measurements(),
            queues=tuple(
                replace(queue, high_water=57) if queue.name == "availability" else queue
                for queue in _measurements().queues
            ),
        ),
        _limits(),
    )

    assert report.decision.passed is True


def test_calibration_v2_publication_cannot_be_labeled_as_legacy_v1(tmp_path: Path) -> None:
    import json

    report = build_calibration_report(_identity(), _measurements(), _limits())
    artifacts = write_calibration_artifacts(report, tmp_path / "current")
    record = json.loads(artifacts.report_json.read_text())
    bindings = json.loads(artifacts.bindings_json.read_text())

    assert record["schema_version"] == "ppm-pre-soak-calibration-v2"
    assert bindings["bindings_version"] == "phase1-pre-soak-calibration-bindings-v2"
    assert record["measurements"]["availability_intervals_written"] == 0
    assert record["limits"]["availability_maximum_intervals"] == 8_192
    with pytest.raises(ValueError, match="unsupported calibration report schema"):
        replace(report, schema_version="ppm-pre-soak-calibration-v1")
