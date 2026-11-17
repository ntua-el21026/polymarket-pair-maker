"""Versioned, lossless references for complete strategy evaluation evidence.

Only the serialization boundary changes. Producer and reconstructor retain a
bounded latest payload per market, plus the current boundary's references.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from typing import Literal, cast

from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.serialization import canonical_sha256, canonicalize
from polymarket_pair_maker.paper.encoding import decode
from polymarket_pair_maker.paper.execution import QueueScenario
from polymarket_pair_maker.strategy.engine import (
    Candidate,
    CandidateEvaluation,
    MarketRanking,
    StrategyAction,
    StrategyDecision,
)

ARTIFACT_SCHEMA = "ppm-paper-artifacts-v2"
type EmitRecord = Callable[[str, int, object], None]
type LogicalValue = tuple[CandidateEvaluation, ...] | StrategyDecision | MarketRanking


@dataclass(frozen=True, slots=True)
class EvaluationPayloadRecord:
    schema_version: Literal["ppm-paper-evaluation-payload-v2"]
    scenario: QueueScenario
    first_ingest_seq: int
    market_id: MarketId | None
    payload_id: str
    evaluations: tuple[CandidateEvaluation, ...]


@dataclass(frozen=True, slots=True)
class EvaluationSetRecord:
    schema_version: Literal["ppm-paper-evaluations-v2"]
    scenario: QueueScenario
    ingest_seq: int
    market_id: MarketId | None
    evaluation_set_id: str
    payload_id: str


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    schema_version: Literal["ppm-paper-decision-v2"]
    scenario: QueueScenario
    ingest_seq: int
    market_id: MarketId | None
    decision_id: str
    action: StrategyAction
    selected_candidate_id: str | None
    detached_candidate: Candidate | None
    reasons: tuple[str, ...]
    cancel_immediately: bool
    retained_existing: bool
    input_hash: str
    objective_version: str
    evaluation_set_id: str


@dataclass(frozen=True, slots=True)
class SelectionRecord:
    schema_version: Literal["ppm-paper-selection-v2"]
    scenario: QueueScenario
    ingest_seq: int
    selection_id: str
    decisions: tuple[tuple[MarketId, str], ...]
    selected_decision_id: str | None
    executed_decision_id: str | None


def _identity(value: object, identity_field: str) -> str:
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("artifact identity requires a declared record")
    return canonical_sha256(
        {
            field.name: getattr(value, field.name)
            for field in fields(value)
            if field.name != identity_field
        }
    )


def _candidate(candidate: Candidate, market: MarketId | None) -> None:
    if candidate.market_id != market:
        raise ValueError("candidate belongs to a different market")
    expected = canonical_sha256(
        ("ppm-candidate-v1", candidate.market_id, candidate.action, candidate.legs)
    )
    if candidate.identity != expected:
        raise ValueError("candidate identity hash mismatch")


def _market(decision: StrategyDecision, explicit: MarketId | None) -> MarketId | None:
    markets = {item.candidate.market_id for item in decision.evaluations}
    if decision.candidate is not None:
        markets.add(decision.candidate.market_id)
    if len(markets) > 1 or (explicit is not None and markets - {explicit}):
        raise ValueError("decision evaluation market mismatch")
    return explicit if explicit is not None else next(iter(markets), None)


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], canonicalize(value))


class NormalizedArtifacts:
    """Emit each scoped complete evaluation set and compact decision once."""

    def __init__(self, scenario: QueueScenario, emit: EmitRecord) -> None:
        self.scenario = scenario
        self.emit = emit
        self._seq = -1
        self._evaluations: set[str] = set()
        self._decisions: dict[str, StrategyDecision] = {}
        self._executed: str | None = None
        self._payloads: dict[MarketId | None, EvaluationPayloadRecord] = {}
        self._current_payloads: dict[str, EvaluationPayloadRecord] = {}

    def _boundary(self, seq: int) -> None:
        if type(seq) is not int or seq < 0 or seq < self._seq:
            raise ValueError("normalized artifact chronology regressed")
        if seq != self._seq:
            self._seq = seq
            self._evaluations.clear()
            self._decisions.clear()
            self._executed = None
            self._current_payloads.clear()

    def decision(
        self, seq: int, decision: StrategyDecision, market_id: MarketId | None = None
    ) -> str:
        self._boundary(seq)
        if market_id is None:
            if self._executed is not None and self._decisions[self._executed] == decision:
                return self._executed
            existing = [key for key, value in self._decisions.items() if value == decision]
            if len(existing) == 1:
                return existing[0]
        market = _market(decision, market_id)
        if len(decision.evaluations) > 49:
            raise ValueError("evaluation grid exceeds the declared strategy bound")
        payload = next(
            (
                item
                for item in self._current_payloads.values()
                if item.market_id == market and item.evaluations == decision.evaluations
            ),
            None,
        )
        if payload is None:
            latest = self._payloads.get(market)
            if latest is not None and latest.evaluations == decision.evaluations:
                payload = latest
        if payload is None:
            identities: set[str] = set()
            for item in decision.evaluations:
                _candidate(item.candidate, market)
                if item.candidate.identity in identities:
                    raise ValueError("duplicate candidate identity in evaluation grid")
                identities.add(item.candidate.identity)
            if market not in self._payloads and len(self._payloads) >= 33:
                raise ValueError("evaluation payload market capacity exhausted")
            new_payload = EvaluationPayloadRecord(
                "ppm-paper-evaluation-payload-v2",
                self.scenario,
                seq,
                market,
                "",
                decision.evaluations,
            )
            payload = EvaluationPayloadRecord(
                new_payload.schema_version,
                new_payload.scenario,
                new_payload.first_ingest_seq,
                new_payload.market_id,
                _identity(new_payload, "payload_id"),
                new_payload.evaluations,
            )
            self.emit("evaluation-payloads.jsonl", seq, _mapping(payload))
            self._payloads[market] = payload
        self._current_payloads[payload.payload_id] = payload
        if decision.candidate is not None:
            _candidate(decision.candidate, market)
        detached = decision.candidate
        if detached is not None and any(
            item.candidate == detached for item in decision.evaluations
        ):
            detached = None
        evaluation = EvaluationSetRecord(
            "ppm-paper-evaluations-v2", self.scenario, seq, market, "", payload.payload_id
        )
        evaluation_id = _identity(evaluation, "evaluation_set_id")
        record = DecisionRecord(
            "ppm-paper-decision-v2",
            self.scenario,
            seq,
            market,
            "",
            decision.action,
            None if decision.candidate is None else decision.candidate.identity,
            detached,
            decision.reasons,
            decision.cancel_immediately,
            decision.retained_existing,
            decision.input_hash,
            decision.objective_version,
            evaluation_id,
        )
        identity = _identity(record, "decision_id")
        if identity in self._decisions:
            if self._decisions[identity] != decision:
                raise ValueError("conflicting normalized decision identity")
            return identity
        if len(self._evaluations) >= 128 or len(self._decisions) >= 128:
            raise ValueError("normalized boundary capacity exhausted")
        if evaluation_id not in self._evaluations:
            payload = _mapping(evaluation)
            payload["evaluation_set_id"] = evaluation_id
            self.emit("economics.jsonl", seq, payload)
            self._evaluations.add(evaluation_id)
        payload = _mapping(record)
        payload["decision_id"] = identity
        self.emit("decisions.jsonl", seq, payload)
        self._decisions[identity] = decision
        return identity

    def ranking(
        self,
        seq: int,
        ranking: MarketRanking,
        *,
        fallback: StrategyDecision | None = None,
    ) -> dict[str, object]:
        self._boundary(seq)
        if len(ranking.decisions) > 32 or len({m for m, _ in ranking.decisions}) != len(
            ranking.decisions
        ):
            raise ValueError("selection markets must be unique and bounded")
        if fallback is not None and (
            ranking.selected is not None
            or fallback.action is not StrategyAction.NO_QUOTE
            or fallback.candidate is not None
        ):
            raise ValueError("selection fallback must be an unselected NO_QUOTE decision")
        decisions = tuple(
            (market, self.decision(seq, decision, market)) for market, decision in ranking.decisions
        )
        selected: str | None = None
        if ranking.selected is not None:
            selected = next(
                (
                    ref
                    for (_, ref), (_, value) in zip(decisions, ranking.decisions, strict=True)
                    if value == ranking.selected
                ),
                None,
            )
            if selected is None:
                selected = self.decision(seq, ranking.selected)
        executed = selected
        if fallback is not None:
            executed = self.decision(seq, fallback)
        self._executed = executed
        record = SelectionRecord(
            "ppm-paper-selection-v2", self.scenario, seq, "", decisions, selected, executed
        )
        payload = _mapping(record)
        payload["selection_id"] = _identity(record, "selection_id")
        return payload


class ArtifactReconstructor:
    """Resolve one scenario's ordered normalized values into exact typed objects."""

    def __init__(self, scenario: QueueScenario) -> None:
        self.scenario = scenario
        self._seq = -1
        self._evaluations: dict[str, EvaluationSetRecord] = {}
        self._decisions: dict[str, tuple[DecisionRecord, StrategyDecision]] = {}
        self._selection_seen = False
        self._evaluation_references: set[str] = set()
        self._evaluation_values: dict[str, tuple[CandidateEvaluation, ...]] = {}
        self._payloads: dict[MarketId | None, EvaluationPayloadRecord] = {}
        self._current_payloads: dict[str, EvaluationPayloadRecord] = {}
        self._published_payloads: set[str] = set()
        self._payload_references: set[str] = set()
        self._decision_references: set[str] = set()

    def finish(self) -> None:
        """Reject orphaned evidence at a boundary or at end of file."""
        if self._published_payloads - self._payload_references:
            raise ValueError("unreferenced evaluation payload")
        if self._evaluations or self._decisions:
            if not self._selection_seen:
                raise ValueError("missing selection for normalized boundary")
            if self._evaluation_references != self._evaluations.keys():
                raise ValueError("unreferenced evaluation evidence")
            if self._decision_references != self._decisions.keys():
                raise ValueError("unreferenced decision evidence")

    def _boundary(self, seq: int) -> None:
        if type(seq) is not int or seq < 0 or seq < self._seq:
            raise ValueError("normalized artifact chronology regressed")
        if seq != self._seq:
            self.finish()
            self._seq = seq
            self._evaluations.clear()
            self._decisions.clear()
            self._selection_seen = False
            self._evaluation_references.clear()
            self._evaluation_values.clear()
            self._current_payloads.clear()
            self._published_payloads.clear()
            self._payload_references.clear()
            self._decision_references.clear()

    def evaluation(self, identity: str) -> tuple[CandidateEvaluation, ...]:
        if identity not in self._evaluations:
            raise ValueError("missing evaluation reference")
        return self._evaluation_values[identity]

    def decision(self, identity: str) -> StrategyDecision:
        if identity not in self._decisions:
            raise ValueError("missing decision reference")
        return self._decisions[identity][1]

    def consume(self, name: str, seq: int, value: object) -> LogicalValue:
        self._boundary(seq)
        if name == "evaluation-payloads.jsonl":
            payload = decode(EvaluationPayloadRecord, canonicalize(value))
            self._scope(payload.scenario, payload.first_ingest_seq, seq)
            if payload.payload_id != _identity(payload, "payload_id"):
                raise ValueError("evaluation payload hash mismatch")
            if payload.payload_id in self._current_payloads:
                raise ValueError("duplicate evaluation payload identity")
            if (
                self._selection_seen
                or len(payload.evaluations) > 49
                or len(self._current_payloads) >= 128
            ):
                raise ValueError("invalid evaluation payload order or capacity")
            if payload.market_id not in self._payloads and len(self._payloads) >= 33:
                raise ValueError("evaluation payload market capacity exhausted")
            prior = self._payloads.get(payload.market_id)
            if prior is not None and prior.evaluations == payload.evaluations:
                raise ValueError("redundant unchanged evaluation payload")
            seen: set[str] = set()
            for evaluation in payload.evaluations:
                _candidate(evaluation.candidate, payload.market_id)
                if evaluation.candidate.identity in seen:
                    raise ValueError("duplicate candidate identity in evaluation grid")
                seen.add(evaluation.candidate.identity)
            self._exact(value, payload)
            self._payloads[payload.market_id] = payload
            self._current_payloads[payload.payload_id] = payload
            self._published_payloads.add(payload.payload_id)
            return payload.evaluations
        if name == "economics.jsonl":
            record = decode(EvaluationSetRecord, canonicalize(value))
            self._scope(record.scenario, record.ingest_seq, seq)
            if record.evaluation_set_id != _identity(record, "evaluation_set_id"):
                raise ValueError("evaluation set hash mismatch")
            if record.evaluation_set_id in self._evaluations:
                raise ValueError("duplicate evaluation identity")
            if self._selection_seen or len(self._evaluations) >= 128:
                raise ValueError("invalid evaluation order or capacity")
            resolved = self._current_payloads.get(record.payload_id)
            if resolved is None:
                latest = self._payloads.get(record.market_id)
                if latest is not None and latest.payload_id == record.payload_id:
                    resolved = latest
            if resolved is None:
                raise ValueError("missing or retired evaluation payload reference")
            if resolved.market_id != record.market_id:
                raise ValueError("cross-market evaluation payload reference")
            if resolved.scenario is not record.scenario or resolved.first_ingest_seq > seq:
                raise ValueError("cross-scenario or future evaluation payload reference")
            self._exact(value, record)
            self._evaluations[record.evaluation_set_id] = record
            self._evaluation_values[record.evaluation_set_id] = resolved.evaluations
            self._current_payloads[resolved.payload_id] = resolved
            self._payload_references.add(resolved.payload_id)
            return resolved.evaluations
        if name == "decisions.jsonl":
            decision_record = decode(DecisionRecord, canonicalize(value))
            self._scope(decision_record.scenario, decision_record.ingest_seq, seq)
            if decision_record.decision_id != _identity(decision_record, "decision_id"):
                raise ValueError("decision hash mismatch")
            if decision_record.decision_id in self._decisions:
                raise ValueError("duplicate decision identity")
            if self._selection_seen or len(self._decisions) >= 128:
                raise ValueError("invalid decision order or capacity")
            evaluations = self.evaluation(decision_record.evaluation_set_id)
            evaluation_record = self._evaluations[decision_record.evaluation_set_id]
            if evaluation_record.market_id != decision_record.market_id:
                raise ValueError("cross-market evaluation reference")
            candidate = decision_record.detached_candidate
            if candidate is not None:
                _candidate(candidate, decision_record.market_id)
                if any(item.candidate == candidate for item in evaluations):
                    raise ValueError("redundant detached candidate")
            elif decision_record.selected_candidate_id is not None:
                candidate = next(
                    (
                        item.candidate
                        for item in evaluations
                        if item.candidate.identity == decision_record.selected_candidate_id
                    ),
                    None,
                )
                if candidate is None:
                    raise ValueError("missing selected candidate reference")
            if (
                None if candidate is None else candidate.identity
            ) != decision_record.selected_candidate_id:
                raise ValueError("selected candidate identity mismatch")
            logical = StrategyDecision(
                decision_record.action,
                candidate,
                decision_record.reasons,
                evaluations,
                decision_record.cancel_immediately,
                decision_record.retained_existing,
                decision_record.input_hash,
                decision_record.objective_version,
            )
            self._exact(value, decision_record)
            self._evaluation_references.add(decision_record.evaluation_set_id)
            self._decisions[decision_record.decision_id] = (decision_record, logical)
            return logical
        if name == "selection.jsonl":
            selection = decode(SelectionRecord, canonicalize(value))
            self._scope(selection.scenario, selection.ingest_seq, seq)
            if selection.selection_id != _identity(selection, "selection_id"):
                raise ValueError("selection hash mismatch")
            if self._selection_seen:
                raise ValueError("duplicate selection identity")
            if len(selection.decisions) > 32 or len({m for m, _ in selection.decisions}) != len(
                selection.decisions
            ):
                raise ValueError("duplicate or excessive selection markets")
            decisions: list[tuple[MarketId, StrategyDecision]] = []
            for market, reference in selection.decisions:
                decision = self.decision(reference)
                if self._decisions[reference][0].market_id != market:
                    raise ValueError("cross-market selection reference")
                decisions.append((market, decision))
            selected = (
                None
                if selection.selected_decision_id is None
                else self.decision(selection.selected_decision_id)
            )
            if selection.selected_decision_id is not None:
                if selection.executed_decision_id != selection.selected_decision_id:
                    raise ValueError("inconsistent selected/executed decision reference")
            elif selection.executed_decision_id is not None:
                fallback = self.decision(selection.executed_decision_id)
                if fallback.action is not StrategyAction.NO_QUOTE or fallback.candidate is not None:
                    raise ValueError("invalid executed fallback decision")
            self._exact(value, selection)
            self._decision_references.update(reference for _, reference in selection.decisions)
            self._decision_references.update(
                ref
                for ref in (selection.selected_decision_id, selection.executed_decision_id)
                if ref is not None
            )
            self._selection_seen = True
            return MarketRanking(selected, tuple(decisions))
        raise ValueError("unsupported normalized artifact stream")

    def _scope(self, scenario: QueueScenario, recorded_seq: int, seq: int) -> None:
        if scenario is not self.scenario or type(recorded_seq) is not int or recorded_seq != seq:
            raise ValueError("normalized artifact scenario or sequence mismatch")

    @staticmethod
    def _exact(value: object, record: object) -> None:
        if canonicalize(value) != canonicalize(record):
            raise ValueError("normalized schema is not exact canonical data")
