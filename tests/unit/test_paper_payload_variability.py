"""High-variability artifact evidence remains exact, bounded and reconstructible.

Economic variants here are declared synthetic serializer fixtures; they are not
new calibration estimates or changes to the strategy's decision policy.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from polymarket_pair_maker.core.exact import ExactDecimal, Money
from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonical_sha256
from polymarket_pair_maker.paper.artifacts import PaperArtifacts
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_market_context
from polymarket_pair_maker.paper.execution import QueueScenario
from polymarket_pair_maker.paper.normalization import ArtifactReconstructor, NormalizedArtifacts
from polymarket_pair_maker.strategy.engine import (
    CandidateEvaluation,
    DecisionEngine,
    MarketRanking,
    StrategyAction,
    StrategyDecision,
)


@pytest.fixture(scope="module")
def base_decision() -> StrategyDecision:
    config = PaperConfig()
    decision = DecisionEngine(config.strategy).evaluate(demo_market_context(config))
    assert len(decision.evaluations) == 49
    return decision


def _variant(base: StrategyDecision, marker: int, market_index: int = 0) -> StrategyDecision:
    market = MarketId(canonical_sha256(("SYNTHETIC-variable-payload-market", market_index)))
    delta = ExactDecimal(marker, 6)
    evaluations: list[CandidateEvaluation] = []
    for item in base.evaluations:
        candidate = replace(
            item.candidate,
            market_id=market,
            identity=canonical_sha256(
                ("ppm-candidate-v1", market, item.candidate.action, item.candidate.legs)
            ),
        )
        evaluations.append(
            replace(
                item,
                candidate=candidate,
                reasons=(*item.reasons, f"SYNTHETIC_ARTIFACT_VARIANT:{marker}"),
                scenarios=tuple(
                    replace(
                        scenario,
                        expected_execution_margin=Money(
                            scenario.expected_execution_margin.value + delta
                        ),
                        net=Money(scenario.net.value + delta),
                    )
                    for scenario in item.scenarios
                ),
                conservative_net=Money(item.conservative_net.value + delta),
                score=item.score + delta,
            )
        )
    selected = next(
        (
            item.candidate
            for item in evaluations
            if base.candidate is not None
            and item.candidate.action is base.candidate.action
            and item.candidate.legs == base.candidate.legs
        ),
        None,
    )
    return replace(
        base,
        candidate=selected,
        evaluations=tuple(evaluations),
        input_hash=canonical_sha256(("SYNTHETIC-variable-payload-input", market, marker)),
    )


def _ranking(decision: StrategyDecision) -> MarketRanking:
    market = decision.evaluations[0].candidate.market_id
    return MarketRanking(decision, ((market, decision),))


@pytest.mark.parametrize("cadence", [0, 4, 1])
def test_complete_variable_grids_survive_all_scenario_owners(
    base_decision: StrategyDecision, cadence: int
):
    boundaries = 16
    ownership_ids: list[str] = []
    for scenario in QueueScenario:
        rows: list[tuple[str, int, object]] = []
        writer = NormalizedArtifacts(
            scenario, lambda n, s, v, target=rows: target.append((n, s, v))
        )
        reader = ArtifactReconstructor(scenario)
        consumed = 0
        recovered = 0
        for seq in range(boundaries):
            decision = _variant(base_decision, 0 if cadence == 0 else seq // cadence)
            expected = _ranking(decision)
            rows.append(("selection.jsonl", seq, writer.ranking(seq, expected)))
            restored = None
            for row in rows[consumed:]:
                logical = reader.consume(*row)
                if row[0] == "economics.jsonl":
                    assert logical == decision.evaluations
                    assert canonical_json_bytes(logical) == canonical_json_bytes(
                        decision.evaluations
                    )
                    recovered += len(cast(tuple[object, ...], logical))
                elif row[0] == "selection.jsonl":
                    restored = logical
            assert restored == expected
            assert canonical_json_bytes(restored) == canonical_json_bytes(expected)
            reader.finish()
            consumed = len(rows)
        payloads = [
            cast(dict[str, object], value)
            for n, _s, value in rows
            if n == "evaluation-payloads.jsonl"
        ]
        assert len(payloads) == (1 if cadence == 0 else (boundaries - 1) // cadence + 1)
        assert recovered == boundaries * 49
        ownership_ids.append(cast(str, payloads[0]["payload_id"]))
    assert len(set(ownership_ids)) == 3


def test_payload_replacement_reappearance_has_distinct_publication_identity(
    base_decision: StrategyDecision,
):
    rows: list[tuple[str, int, object]] = []
    writer = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v, target=rows: target.append((n, s, v))
    )
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    consumed = 0
    for seq, marker in enumerate((0, 1, 0, 0)):
        expected = _ranking(_variant(base_decision, marker))
        rows.append(("selection.jsonl", seq, writer.ranking(seq, expected)))
        restored = [reader.consume(*row) for row in rows[consumed:]][-1]
        assert restored == expected
        reader.finish()
        consumed = len(rows)
    payloads = [cast(dict[str, object], v) for n, _s, v in rows if n == "evaluation-payloads.jsonl"]
    assert len(payloads) == 3
    assert payloads[0]["evaluations"] == payloads[2]["evaluations"]
    assert payloads[0]["payload_id"] != payloads[2]["payload_id"]
    assert payloads[0]["first_ingest_seq"] == 0
    assert payloads[2]["first_ingest_seq"] == 2


def test_retained_market_keys_and_fallback_remain_bounded_and_isolated(
    base_decision: StrategyDecision,
):
    rows: list[tuple[str, int, object]] = []
    writer = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v, target=rows: target.append((n, s, v))
    )
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    choices = tuple(
        (
            (decision := _variant(base_decision, 0, index)).evaluations[0].candidate.market_id,
            decision,
        )
        for index in range(32)
    )
    fallback = StrategyDecision(
        StrategyAction.NO_QUOTE,
        None,
        ("SYNTHETIC_FALLBACK",),
        (),
        False,
        False,
        canonical_sha256("synthetic-fallback"),
    )
    ranking = MarketRanking(None, choices)
    rows.append(("selection.jsonl", 0, writer.ranking(0, ranking, fallback=fallback)))
    restored = [reader.consume(*row) for row in rows][-1]
    reader.finish()
    assert restored == ranking
    assert (
        len(cast(dict[object, object], vars(writer)["_payloads"]))
        == len(cast(dict[object, object], vars(reader)["_payloads"]))
        == 33
    )
    extra = _variant(base_decision, 0, 33)
    with pytest.raises(ValueError, match="market capacity exhausted"):
        writer.ranking(1, _ranking(extra))
    assert len(cast(dict[object, object], vars(writer)["_payloads"])) == 33


def test_frequent_grids_grow_without_losing_candidate_evidence(base_decision: StrategyDecision):
    byte_counts: list[int] = []
    for frequent in (False, True):
        rows: list[tuple[str, int, object]] = []
        writer = NormalizedArtifacts(
            QueueScenario.CONSERVATIVE, lambda n, s, v, target=rows: target.append((n, s, v))
        )
        reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
        consumed = 0
        recovered = 0
        for seq in range(12):
            expected = _ranking(_variant(base_decision, seq if frequent else 0))
            rows.append(("selection.jsonl", seq, writer.ranking(seq, expected)))
            for row in rows[consumed:]:
                logical = reader.consume(*row)
                if row[0] == "economics.jsonl":
                    recovered += len(cast(tuple[object, ...], logical))
            reader.finish()
            consumed = len(rows)
        assert recovered == 12 * 49
        byte_counts.append(sum(len(canonical_json_bytes(value)) for _n, _s, value in rows))
    assert byte_counts[1] > byte_counts[0] * 4


def test_variable_payload_exhaustion_does_not_publish_or_append_partial_record(
    tmp_path: Path, base_decision: StrategyDecision
):
    # A small, explicitly configured fixture cap provides a fast regression.
    # The independent external stress uses the actual 268,435,456-byte bound.
    prototype: list[tuple[str, int, object]] = []
    initial = _ranking(_variant(base_decision, 0))
    probe = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v: prototype.append((n, s, v))
    )
    prototype.append(("selection.jsonl", 0, probe.ranking(0, initial)))
    first_bytes = sum(
        len(
            canonical_json_bytes(
                {
                    "scenario": QueueScenario.CONSERVATIVE,
                    "ingest_seq": seq,
                    "record_index": index,
                    "value": value,
                }
            )
        )
        + 1
        for index, (_name, seq, value) in enumerate(prototype)
    )
    artifacts = PaperArtifacts(tmp_path / "result", maximum_bytes=first_bytes + 1)
    index = 0

    def emit(name: str, seq: int, value: object) -> None:
        nonlocal index
        artifacts.append(
            name,
            {
                "scenario": QueueScenario.CONSERVATIVE,
                "ingest_seq": seq,
                "record_index": index,
                "value": value,
            },
        )
        index += 1

    writer = NormalizedArtifacts(QueueScenario.CONSERVATIVE, emit)
    try:
        emit("selection.jsonl", 0, writer.ranking(0, initial))
        assert artifacts.written == first_bytes
        before = artifacts.inventory()
        with pytest.raises(ValueError, match="byte budget exhausted"):
            writer.ranking(1, _ranking(_variant(base_decision, 1)))
        assert artifacts.written == first_bytes
        assert artifacts.inventory() == before
        assert not artifacts.destination.exists()
        assert not (artifacts.staging / "session-manifest.json").exists()
    finally:
        artifacts.close_incomplete()


def test_actual_per_record_bound_rejects_oversized_record_without_publication(tmp_path: Path):
    artifacts = PaperArtifacts(tmp_path / "oversized", maximum_bytes=268_435_456)
    try:
        with pytest.raises(ValueError, match="byte budget exhausted"):
            artifacts.append("synthetic-padding.jsonl", {"padding": "x" * 16_777_216})
        assert artifacts.written == 0
        assert artifacts.inventory() == ()
        assert not artifacts.destination.exists()
        assert not (artifacts.staging / "session-manifest.json").exists()
    finally:
        artifacts.close_incomplete()
