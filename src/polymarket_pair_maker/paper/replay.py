"""Offline PAPER replay through the existing integrity-verified canonical pipeline."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from polymarket_pair_maker.core.config import Phase1Config, ReferenceInterpretation, ReplayClock
from polymarket_pair_maker.core.ids import CollectorRunId, MarketId
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.journal import JournalReader
from polymarket_pair_maker.paper.artifacts import (
    PaperArtifacts,
    fresh_output,
    runtime_identity,
    source_identity,
)
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.evidence import PaperEvidence
from polymarket_pair_maker.paper.execution import QueueScenario
from polymarket_pair_maker.paper.session import ScenarioSession
from polymarket_pair_maker.pipeline.market_view import CanonicalMarketView
from polymarket_pair_maker.pipeline.processor import CanonicalProcessor
from polymarket_pair_maker.reference import POLYMARKET_PUSD_ASSET_ID


def replay_paper(
    journal_root: Path,
    run_id: CollectorRunId,
    phase_config: Phase1Config,
    paper_config: PaperConfig,
    *,
    output: Path,
    code_commit: str,
    evidence: PaperEvidence | None = None,
    synthetic: bool = False,
    protected_inputs: tuple[Path, ...] = (),
) -> dict[str, object]:
    """Run three independent scenarios; completion never implies empirical acceptance."""
    if (
        phase_config.replay.clock is not ReplayClock.RECORDED_ORDER
        or phase_config.replay.reference_interpretation
        is not ReferenceInterpretation.AS_KNOWN_AT_TIME
        or not phase_config.eligibility.enabled
    ):
        raise ValueError("paper replay requires recorded-order, as-known, enabled data eligibility")
    if not journal_root.is_dir() or journal_root.is_symlink():
        raise ValueError("paper journal root must be a regular directory")
    destination = fresh_output(output, protected=(journal_root, *protected_inputs))
    reader = JournalReader(journal_root, run_id)
    heads = reader.completion_heads()
    if len(heads) != 1:
        raise ValueError("paper replay requires exactly one verified completed source run")
    head = heads[0]
    if evidence is not None:
        if (
            evidence.collector_run_id != run_id
            or evidence.raw_completion_sha256 != head.completion_sha256
        ):
            raise ValueError("paper evidence is not bound to this raw run completion")
        if (evidence.classification == "SYNTHETIC") != synthetic:
            raise ValueError("synthetic evidence is accepted only by the fixed synthetic demo")
    if synthetic and evidence is None:
        raise ValueError("synthetic demonstration requires its validated fixture evidence")
    artifacts = PaperArtifacts(destination, maximum_bytes=paper_config.maximum_output_bytes)
    sessions = tuple(
        ScenarioSession(paper_config, scenario, evidence, artifacts, source_run_id=run_id.value)
        for scenario in QueueScenario
    )
    processor = CanonicalProcessor(phase_config, source_journal_git_commit=head.git_commit)
    views: dict[MarketId, CanonicalMarketView] = {}
    now: UtcTimestamp | None = None
    count = 0
    input_valid = True
    manifest: dict[str, object] = {
        "schema_version": "ppm-paper-session-v1",
        "classification": "SYNTHETIC"
        if synthetic
        else "OBSERVED_MARKET_DATA_WITH_SIMULATED_EXECUTION",
        "git_commit": code_commit,
        "source_sha256": source_identity(),
        "runtime": runtime_identity(),
        "raw_run_id": run_id,
        "raw_completion_sha256": head.completion_sha256,
        "raw_terminal_manifest_sha256": head.terminal_manifest_sha256,
        "source_producer_git_commit": head.git_commit,
        "phase_config_hash": phase_config.config_hash,
        "paper_config_hash": paper_config.config_hash,
        "paper_configuration": paper_config,
        "collateral_asset_id": POLYMARKET_PUSD_ASSET_ID,
        "collateral_symbol": "pUSD",
        "economic_evidence_sha256": None if evidence is None else evidence.content_sha256,
        "reference_versions": ()
        if evidence is None
        else tuple(
            (
                item.market_id,
                item.risk_evidence.version,
                None if item.fee_rules is None else item.fee_rules.evidence.version,
                None if item.reward_rules is None else item.reward_rules.evidence.version,
            )
            for item in evidence.markets
        ),
        "calibrations": ()
        if evidence is None
        else tuple((item.market_id, item.calibration()) for item in evidence.markets),
        "collector_qualification": "UNAVAILABLE"
        if evidence is None
        else evidence.collector_qualification,
        "historical_six_hour_gate1": "FAIL",
        "simulation_assumptions": (
            "Independent scenario portfolios; never sum their wealth.",
            "L2 touches and reductions do not establish executions.",
            "Unknown queue priority; declared buffer; no counterfactual market impact.",
            "Latency events process at the next recorded boundary; no interpolated observations.",
            "No synthetic terminal cancellation; pending and unknown orders retain collateral.",
            "Missing liquidation depth and stale positions use explicit zero stress value.",
            "Estimated incentives never enter simulated cash; verified payments unavailable.",
        ),
        "seed": paper_config.execution.seed,
        "scenario_identity": tuple(QueueScenario),
    }
    try:
        for envelope in reader.iter_verified(include_partial=False):
            if count >= paper_config.maximum_input_records:
                raise ValueError("paper input record budget exhausted")
            if now is not None and envelope.received_wall_time < now:
                raise ValueError("recorded decision clock regressed; paper chronology unavailable")
            now = envelope.received_wall_time
            result = processor.process_replayed(envelope)
            input_valid = input_valid and result.success
            bundles = processor.current_bundles
            if len(bundles) > paper_config.strategy.maximum_markets:
                raise ValueError("source exceeds bounded paper market selection capacity")
            dirty = bool(result.eligibility_triggers) or len(bundles) != len(views)
            if dirty:
                views = {
                    bundle.market.market_id: view
                    for bundle in bundles
                    if (view := processor.market_view(bundle.market.market_id, observed_at=now))
                    is not None
                }
            for session in sessions:
                session.process(
                    seq=envelope.ingest_seq,
                    now=now,
                    views=views,
                    dirty=dirty,
                    source_valid=result.success,
                )
            count += 1
        if now is None:
            raise ValueError("paper source run has no observations")
        if reader.completion_heads() != heads:
            raise ValueError("raw run completion changed during paper replay")
        manifests = reader.manifests()
        manifest["raw_segments"] = tuple(
            {
                "manifest_sha256": item.manifest_sha256,
                "first_ingest_seq": item.first_ingest_seq,
                "last_ingest_seq": item.last_ingest_seq,
            }
            for item in manifests
        )
        reports = tuple(session.report(views, now) for session in sessions)
        processor_summary = processor.summary()
        report: dict[str, object] = {
            "schema_version": "ppm-paper-report-v1",
            "status": "COMPLETED" if input_valid else "BLOCKED_CANONICAL_INTEGRITY",
            "classification": manifest["classification"],
            "raw_record_count": count,
            "canonical_processor_terminal_hash": processor_summary.terminal_hash,
            "canonical_parse_failures": processor_summary.parse_failures,
            "scenarios": reports,
            "empirical_profitability": "UNVALIDATED",
            "live_execution": "STRUCTURALLY_UNAVAILABLE",
            "verified_payments": None,
        }
        result_hash = canonical_sha256((manifest, artifacts.inventory(), report))
        report["terminal_result_hash"] = result_hash
        artifacts.document("report.json", report)
        manifest.update(
            {
                "terminal_result_hash": result_hash,
                "status": report["status"],
                "artifacts": artifacts.inventory(),
            }
        )
        artifacts.document("session-manifest.json", manifest)
        artifacts.publish()
        return report
    except BaseException as error:
        with suppress(ValueError, OSError):
            artifacts.document(
                "failure.json",
                {
                    "schema_version": "ppm-paper-failure-v1",
                    "status": "INCOMPLETE",
                    "error_type": type(error).__name__,
                    "processed_records": count,
                    "orders_cancelled_by_interruption": False,
                    "scenario_ledgers": tuple(session.ledger.snapshot() for session in sessions),
                },
            )
        artifacts.close_incomplete()
        raise
