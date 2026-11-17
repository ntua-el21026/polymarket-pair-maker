"""Lossless normalized strategy artifacts and independently corrupted references."""

from dataclasses import replace
from typing import cast

import pytest

from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonicalize
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.demo import demo_market_context
from polymarket_pair_maker.paper.execution import QueueScenario
from polymarket_pair_maker.paper.normalization import ArtifactReconstructor, NormalizedArtifacts
from polymarket_pair_maker.strategy.engine import DecisionEngine, MarketRanking, StrategyDecision


def _sample() -> tuple[StrategyDecision, MarketId]:
    config = PaperConfig()
    decision = DecisionEngine(config.strategy).evaluate(demo_market_context(config))
    assert len(decision.evaluations) == 49
    market = decision.evaluations[0].candidate.market_id
    return decision, market


def _encode(ranking: MarketRanking) -> tuple[NormalizedArtifacts, list[tuple[str, int, object]]]:
    rows: list[tuple[str, int, object]] = []
    encoder = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v: rows.append((n, s, v))
    )
    value = encoder.ranking(7, ranking)
    rows.append(("selection.jsonl", 7, value))
    return encoder, rows


def test_complete_grid_round_trips_without_embedded_decision_or_selection_grid():
    decision, market = _sample()
    ranking = MarketRanking(decision, ((market, decision),))
    encoder, rows = _encode(ranking)
    encoder.decision(7, decision)
    assert [name for name, _, _ in rows] == [
        "evaluation-payloads.jsonl",
        "economics.jsonl",
        "decisions.jsonl",
        "selection.jsonl",
    ]
    assert b'"evaluations"' not in canonical_json_bytes(rows[2][2])
    assert b'"evaluations"' not in canonical_json_bytes(rows[3][2])
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    values = [reader.consume(*row) for row in rows]
    assert values[0] == decision.evaluations
    assert values[1] == decision.evaluations
    assert values[2] == decision
    assert values[3] == ranking
    assert canonical_json_bytes(values[3]) == canonical_json_bytes(ranking)
    assert isinstance(values[0], tuple)
    assert len(values[0]) == 49
    reader.finish()


@pytest.mark.parametrize("kind", ["empty", "no_selected", "synthetic_selected", "retained"])
def test_selection_edge_cases_round_trip(kind: str):
    decision, market = _sample()
    if kind == "empty":
        ranking = MarketRanking(None, ())
    elif kind == "no_selected":
        no_quote = replace(decision, candidate=None, action=decision.action.NO_QUOTE)
        ranking = MarketRanking(None, ((market, no_quote),))
    elif kind == "synthetic_selected":
        fallback = replace(
            decision, candidate=None, evaluations=(), action=decision.action.NO_QUOTE
        )
        ranking = MarketRanking(fallback, ())
    else:
        retained = replace(decision, evaluations=(), retained_existing=True)
        ranking = MarketRanking(retained, ((market, retained),))
    _, rows = _encode(ranking)
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    restored = [reader.consume(*row) for row in rows][-1]
    assert restored == ranking
    assert canonical_json_bytes(restored) == canonical_json_bytes(ranking)


def test_different_decisions_share_one_evaluation_set_and_distinct_decision_identity():
    decision, market = _sample()
    other = replace(decision, reasons=("DIFFERENT_SELECTION_REASON",))
    ranking = MarketRanking(other, ((market, decision),))
    _, rows = _encode(ranking)
    assert [n for n, _, _ in rows].count("economics.jsonl") == 1
    assert [n for n, _, _ in rows].count("decisions.jsonl") == 2
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    assert [reader.consume(*row) for row in rows][-1] == ranking


@pytest.mark.parametrize(
    "field", ["scenario", "ingest_seq", "market_id", "evaluation_set_id", "schema_version"]
)
def test_evaluation_record_corruption_rejected(field: str):
    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    row = cast(dict[str, object], canonicalize(rows[1][2]))
    row[field] = {
        "scenario": "SENSITIVITY",
        "ingest_seq": 8,
        "market_id": "f" * 64,
        "evaluation_set_id": "0" * 64,
        "schema_version": "unknown",
    }[field]
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    with pytest.raises((TypeError, ValueError)):
        reader.consume(rows[0][0], 7, row)


def test_missing_evaluation_reference_rejected():
    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    with pytest.raises(ValueError, match="evaluation"):
        reader.consume(*rows[2])


def test_duplicate_identity_rejected_even_when_identical():
    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    reader.consume(*rows[0])
    with pytest.raises(ValueError, match="duplicate"):
        reader.consume(*rows[0])


def test_encoder_and_decoder_reject_regressed_boundary():
    decision, market = _sample()
    encoder, rows = _encode(MarketRanking(decision, ((market, decision),)))
    with pytest.raises(ValueError, match="chronology"):
        encoder.decision(6, decision)
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    reader.consume(*rows[0])
    with pytest.raises(ValueError, match="chronology"):
        reader.consume(rows[0][0], 6, rows[0][2])


def test_market_identity_cannot_be_overridden():
    decision, _ = _sample()
    encoder = NormalizedArtifacts(QueueScenario.CONSERVATIVE, lambda _n, _s, _v: None)
    with pytest.raises(ValueError, match="market"):
        encoder.decision(0, decision, MarketId("f" * 64))


@pytest.mark.parametrize(
    "mode", ["evaluation_only", "no_selection", "orphan_decision", "orphan_evaluations"]
)
def test_boundary_closure_rejects_missing_or_orphan_records(mode: str):
    decision, market = _sample()
    rows: list[tuple[str, int, object]] = []
    encoder = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v: rows.append((n, s, v))
    )
    if mode == "orphan_decision":
        encoder.decision(7, replace(decision, reasons=("ORPHAN",)))
    if mode == "orphan_evaluations":
        encoder.decision(7, replace(decision, evaluations=()))
    value = encoder.ranking(7, MarketRanking(decision, ((market, decision),)))
    rows.append(("selection.jsonl", 7, value))
    if mode == "evaluation_only":
        rows = rows[:1]
    elif mode == "no_selection":
        rows = rows[:-1]
    elif mode == "orphan_evaluations":
        # Drop the first decision, leaving its empty authoritative set orphaned.
        rows.pop(2)
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    for row in rows:
        reader.consume(*row)
    with pytest.raises(ValueError, match=r"missing selection|unreferenced"):
        reader.finish()


def test_fallback_execution_reference_preserves_unselected_ranking():
    decision, market = _sample()
    fallback = replace(
        decision,
        candidate=None,
        evaluations=(),
        action=decision.action.NO_QUOTE,
        reasons=("NO_VALIDATED_ELIGIBLE_MARKET",),
    )
    ranking = MarketRanking(None, ((market, replace(decision, candidate=None)),))
    rows: list[tuple[str, int, object]] = []
    encoder = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v: rows.append((n, s, v))
    )
    value = encoder.ranking(7, ranking, fallback=fallback)
    assert value["selected_decision_id"] is None
    assert isinstance(value["executed_decision_id"], str)
    count = len(rows)
    assert encoder.decision(7, fallback) == value["executed_decision_id"]
    assert len(rows) == count
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    for row in rows:
        reader.consume(*row)
    assert reader.consume("selection.jsonl", 7, value) == ranking
    assert reader.decision(value["executed_decision_id"]) == fallback
    reader.finish()


def test_multiple_markets_exclusions_and_active_market_switch_rules_round_trip():
    from polymarket_pair_maker.strategy.engine import ActiveQuote
    from tests.unit.test_paper_strategy import NOW, strategy_context

    first = strategy_context(native_condition_id="normalized-first")
    second = strategy_context(native_condition_id="normalized-second")
    excluded = replace(
        strategy_context(native_condition_id="normalized-excluded"), event_risk_verified=False
    )
    engine = DecisionEngine()
    choice = engine.evaluate_markets((first, second, excluded))
    assert choice.selected is not None
    assert choice.selected.candidate is not None
    current = ActiveQuote(choice.selected.candidate, NOW)
    active_context = next(
        c for c in (first, second) if c.bundle.market.market_id == current.candidate.market_id
    )
    rankings = (
        choice,
        engine.evaluate_markets((first, second, excluded), current),
        engine.evaluate_markets((excluded,), current),
        engine.evaluate_markets((replace(active_context, event_risk_verified=False),), current),
    )
    for ranking in rankings:
        _, rows = _encode(ranking)
        reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
        restored = [reader.consume(*row) for row in rows][-1]
        assert restored == ranking
        assert canonical_json_bytes(restored) == canonical_json_bytes(ranking)
        reader.finish()


@pytest.mark.parametrize(
    "corruption", ["economic_value", "candidate_identity", "unknown_field", "missing_field"]
)
def test_tampered_evaluation_content_fails_closed(corruption: str):
    import copy

    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    value = copy.deepcopy(cast(dict[str, object], canonicalize(rows[0][2])))
    evaluations = cast(list[dict[str, object]], value["evaluations"])
    if corruption == "economic_value":
        evaluations[0]["score"] = "1000000"
    elif corruption == "candidate_identity":
        candidate = cast(dict[str, object], evaluations[0]["candidate"])
        candidate["identity"] = "f" * 64
    elif corruption == "unknown_field":
        value["unexpected"] = True
    else:
        value.pop("market_id")
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    with pytest.raises((ValueError, TypeError)):
        reader.consume("evaluation-payloads.jsonl", 7, value)


def test_changed_candidate_identity_rejected_even_with_rehashed_evaluation_set():
    import copy

    from polymarket_pair_maker.core.serialization import canonical_sha256

    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    value = copy.deepcopy(cast(dict[str, object], canonicalize(rows[0][2])))
    evaluations = cast(list[dict[str, object]], value["evaluations"])
    candidate = cast(dict[str, object], evaluations[0]["candidate"])
    candidate["identity"] = "f" * 64
    value["payload_id"] = canonical_sha256(
        {key: item for key, item in value.items() if key != "payload_id"}
    )
    with pytest.raises(ValueError, match="candidate identity"):
        ArtifactReconstructor(QueueScenario.CONSERVATIVE).consume(
            "evaluation-payloads.jsonl", 7, value
        )


def test_identical_consecutive_evaluation_payloads_are_stored_once():
    decision, market = _sample()
    rows: list[tuple[str, int, object]] = []
    encoder = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v: rows.append((n, s, v))
    )
    for seq in range(100):
        value = encoder.ranking(seq, MarketRanking(decision, ((market, decision),)))
        rows.append(("selection.jsonl", seq, value))
    assert sum(name == "evaluation-payloads.jsonl" for name, _, _ in rows) == 1
    assert sum(name == "economics.jsonl" for name, _, _ in rows) == 100
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    for name, seq, value in rows:
        restored = reader.consume(name, seq, value)
        if name == "selection.jsonl":
            assert restored == MarketRanking(decision, ((market, decision),))
    reader.finish()


def _changed_economics(decision: StrategyDecision) -> StrategyDecision:
    from polymarket_pair_maker.core.exact import ExactDecimal

    first, *others = decision.evaluations
    return replace(
        decision, evaluations=(replace(first, score=first.score + ExactDecimal.one()), *others)
    )


def _boundaries(decisions: tuple[StrategyDecision, ...]) -> list[tuple[str, int, object]]:
    rows: list[tuple[str, int, object]] = []
    encoder = NormalizedArtifacts(
        QueueScenario.CONSERVATIVE, lambda n, s, v: rows.append((n, s, v))
    )
    for seq, decision in enumerate(decisions):
        market = decision.evaluations[0].candidate.market_id
        value = encoder.ranking(seq, MarketRanking(decision, ((market, decision),)))
        rows.append(("selection.jsonl", seq, value))
    return rows


def test_changed_payload_is_republished_and_recurrence_has_a_new_identity():
    decision, _ = _sample()
    rows = _boundaries((decision, decision, _changed_economics(decision), decision))
    publications = [
        cast(dict[str, object], value)
        for name, _, value in rows
        if name == "evaluation-payloads.jsonl"
    ]
    assert len(publications) == 3
    assert [p["first_ingest_seq"] for p in publications] == [0, 2, 3]
    assert len({p["payload_id"] for p in publications}) == 3
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    for row in rows:
        reader.consume(*row)
    reader.finish()


def test_payload_reference_is_required_before_binding():
    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    with pytest.raises(ValueError, match="payload reference"):
        ArtifactReconstructor(QueueScenario.CONSERVATIVE).consume(*rows[1])


def test_retired_payload_cannot_be_reused_after_its_replacement():
    from polymarket_pair_maker.core.serialization import canonical_sha256

    decision, _ = _sample()
    changed = _changed_economics(decision)
    rows = _boundaries((decision, changed, changed))
    first_payload = cast(dict[str, object], rows[0][2])["payload_id"]
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    for name, seq, value in rows:
        if seq == 2:
            assert name == "economics.jsonl"
            modified = cast(dict[str, object], canonicalize(value))
            modified["payload_id"] = first_payload
            modified["evaluation_set_id"] = canonical_sha256(
                {k: v for k, v in modified.items() if k != "evaluation_set_id"}
            )
            with pytest.raises(ValueError, match="retired"):
                reader.consume(name, seq, modified)
            break
        reader.consume(name, seq, value)


@pytest.mark.parametrize(
    ("field", "value"), [("market_id", "f" * 64), ("scenario", "SENSITIVITY"), ("ingest_seq", 8)]
)
def test_rehashed_payload_binding_cannot_cross_scope(field: str, value: object):
    from polymarket_pair_maker.core.serialization import canonical_sha256

    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    reader.consume(*rows[0])
    modified = cast(dict[str, object], canonicalize(rows[1][2]))
    modified[field] = value
    modified["evaluation_set_id"] = canonical_sha256(
        {k: v for k, v in modified.items() if k != "evaluation_set_id"}
    )
    with pytest.raises(ValueError, match=r"scope|scenario|market|sequence"):
        reader.consume("economics.jsonl", 7, modified)


def test_payload_cannot_be_published_with_a_future_creation_boundary():
    from polymarket_pair_maker.core.serialization import canonical_sha256

    decision, market = _sample()
    _, rows = _encode(MarketRanking(decision, ((market, decision),)))
    modified = cast(dict[str, object], canonicalize(rows[0][2]))
    modified["first_ingest_seq"] = 8
    modified["payload_id"] = canonical_sha256(
        {k: v for k, v in modified.items() if k != "payload_id"}
    )
    with pytest.raises(ValueError, match="sequence"):
        ArtifactReconstructor(QueueScenario.CONSERVATIVE).consume(
            "evaluation-payloads.jsonl", 7, modified
        )


def test_unchanged_payload_cannot_be_redundantly_published_at_a_later_boundary():
    from polymarket_pair_maker.core.serialization import canonical_sha256

    decision, _ = _sample()
    rows = _boundaries((decision, decision))
    reader = ArtifactReconstructor(QueueScenario.CONSERVATIVE)
    for name, seq, value in rows:
        if seq == 1:
            break
        reader.consume(name, seq, value)
    modified = cast(dict[str, object], canonicalize(rows[0][2]))
    modified["first_ingest_seq"] = 1
    modified["payload_id"] = canonical_sha256(
        {k: v for k, v in modified.items() if k != "payload_id"}
    )
    with pytest.raises(ValueError, match="redundant"):
        reader.consume("evaluation-payloads.jsonl", 1, modified)
