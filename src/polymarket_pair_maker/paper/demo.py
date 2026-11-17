"""Committed SYNTHETIC journal, assumptions and chronological calibration fixture."""

from __future__ import annotations

import base64
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from polymarket_pair_maker.core.config import Phase1Config, load_phase1_config
from polymarket_pair_maker.core.exact import Money
from polymarket_pair_maker.core.ids import CollectorRunId
from polymarket_pair_maker.journal import JournalReader
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.encoding import decode, read_json
from polymarket_pair_maker.paper.evidence import PaperEvidence, load_paper_evidence
from polymarket_pair_maker.paper.replay import replay_paper
from polymarket_pair_maker.pipeline.processor import CanonicalProcessor
from polymarket_pair_maker.strategy.engine import MarketContext

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True, slots=True)
class DemoFile:
    name: str
    bytes: int
    sha256: str
    base64: str


@dataclass(frozen=True, slots=True)
class DemoArchive:
    schema_version: str
    classification: str
    run_id: str
    files: tuple[DemoFile, ...]


def materialize_demo_source(root: Path) -> CollectorRunId:
    """Verify each committed file before creating a fresh local raw input copy."""
    archive = decode(DemoArchive, read_json(FIXTURES / "synthetic-source.json"))
    if archive.schema_version != "ppm-synthetic-source-v1" or archive.classification != "SYNTHETIC":
        raise ValueError("unsupported synthetic source fixture")
    if not 1 <= len(archive.files) <= 8 or len({item.name for item in archive.files}) != len(
        archive.files
    ):
        raise ValueError("synthetic source file inventory is invalid")
    verified: list[tuple[str, bytes]] = []
    for item in archive.files:
        if Path(item.name).name != item.name or not item.name.endswith((".json", ".jsonl.zst")):
            raise ValueError("unsafe synthetic source artifact name")
        payload = base64.b64decode(item.base64, validate=True)
        if len(payload) != item.bytes or hashlib.sha256(payload).hexdigest() != item.sha256:
            raise ValueError("synthetic source fixture integrity failure")
        verified.append((item.name, payload))
    root.mkdir(exist_ok=False)
    for name, payload in verified:
        with (root / name).open("xb") as handle:
            handle.write(payload)
    run_id = CollectorRunId(archive.run_id)
    # Reader validates the completion inventory, compressed bytes and envelope IDs.
    if sum(1 for _ in JournalReader(root, run_id).iter_verified()) != 8:
        raise ValueError("synthetic source record inventory differs")
    return run_id


def demo_evidence() -> PaperEvidence:
    evidence = load_paper_evidence(FIXTURES / "synthetic-evidence.json")
    actual = hashlib.sha256((FIXTURES / "synthetic-assumptions.json").read_bytes()).hexdigest()
    for market in evidence.markets:
        if market.risk_evidence.source_sha256 != actual:
            raise ValueError("synthetic assumption source hash differs")
        if any(item.source_sha256 != actual for item in market.calibration_observations):
            raise ValueError("synthetic calibration source hash differs")
    return evidence


def run_demo(
    paper_config: PaperConfig,
    phase_config: Phase1Config,
    *,
    output: Path,
    code_commit: str,
    protected_inputs: tuple[Path, ...] = (),
) -> dict[str, object]:
    if paper_config.evidence_file is not None:
        raise ValueError("demo uses only its committed synthetic evidence, not external references")
    evidence = demo_evidence()
    with tempfile.TemporaryDirectory(prefix="ppm-synthetic-source-") as temporary:
        root = Path(temporary) / "raw"
        run_id = materialize_demo_source(root)
        return replay_paper(
            root,
            run_id,
            phase_config,
            paper_config,
            output=output,
            code_commit=code_commit,
            evidence=evidence,
            synthetic=True,
            protected_inputs=(FIXTURES, *protected_inputs),
        )


def demo_market_context(paper_config: PaperConfig) -> MarketContext:
    """Validated positive fixture for tests/benchmarks; setup is outside measurement."""
    evidence = demo_evidence()
    market = evidence.markets[0]
    config_path = Path(__file__).resolve().parents[3] / "config/phase1.toml"
    processor = CanonicalProcessor(load_phase1_config(config_path))
    with tempfile.TemporaryDirectory(prefix="ppm-demo-context-") as temporary:
        root = Path(temporary) / "raw"
        run_id = materialize_demo_source(root)
        for envelope in JournalReader(root, run_id).iter_verified():
            processor.process_replayed(envelope)
            view = processor.market_view(market.market_id, observed_at=envelope.received_wall_time)
            if view is None or view.assessment.disposition.value != "ELIGIBLE":
                continue
            books = {book.instrument_id: book for book in view.books}
            yes_id, no_id = view.proof.yes_instrument_id, view.proof.no_instrument_id
            if yes_id is None or no_id is None:
                raise ValueError("eligible synthetic fixture lacks proven instruments")
            return MarketContext(
                view.bundle,
                view.assessment,
                books[yes_id],
                books[no_id],
                view.observed_at,
                market.fee_rules,
                market.reward_rules,
                market.calibration(),
                Money(paper_config.risk.maximum_market_commitment.value),
                market.event_risk_verified,
                market.catalyst_excluded,
                market.settlement_rules_verified,
            )
    raise ValueError("synthetic fixture lacks a positive data-eligible state")
