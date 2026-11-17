"""Explicit offline rules, calibration and collector-qualification declarations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polymarket_pair_maker.core.ids import CollectorRunId, MarketId, is_sha256_hex
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.economics.fees import FeeRules
from polymarket_pair_maker.economics.reference import RuleEvidence
from polymarket_pair_maker.economics.rewards import RewardRules
from polymarket_pair_maker.paper.encoding import decode, read_json
from polymarket_pair_maker.paper.execution import ObservedPressure, PressureKind
from polymarket_pair_maker.research.paper_calibration import (
    CalibrationObservation,
    CalibrationPeriod,
    CalibrationSnapshot,
    CalibrationSource,
    fit_calibration,
)


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    market_id: MarketId
    risk_evidence: RuleEvidence
    event_risk_verified: bool
    catalyst_excluded: bool
    settlement_rules_verified: bool
    fee_rules: FeeRules | None
    reward_rules: RewardRules | None
    calibration_period: CalibrationPeriod | None
    calibration_observations: tuple[CalibrationObservation, ...] = ()
    minimum_calibration_samples: int = 2

    def __post_init__(self) -> None:
        if self.risk_evidence.market_id != self.market_id:
            raise ValueError("risk evidence market differs from declared scope")
        if self.calibration_period is None and self.calibration_observations:
            raise ValueError("calibration observations require a declared period")
        if self.calibration_period is not None:
            if self.calibration_period.market_id != self.market_id:
                raise ValueError("calibration market differs from declared scope")
            self.calibration()

    def calibration(self) -> CalibrationSnapshot | None:
        if self.calibration_period is None:
            return None
        return fit_calibration(
            self.calibration_observations,
            self.calibration_period,
            minimum_samples=self.minimum_calibration_samples,
        )


@dataclass(frozen=True, slots=True)
class PressureInput:
    ingest_seq: int
    pressure: ObservedPressure

    def __post_init__(self) -> None:
        if self.ingest_seq < 0:
            raise ValueError("pressure ingest boundary must be nonnegative")


@dataclass(frozen=True, slots=True)
class PaperEvidence:
    schema_version: str
    classification: str
    collector_run_id: CollectorRunId
    raw_completion_sha256: str
    collector_qualification: str
    qualification_source_sha256: str | None
    markets: tuple[MarketEvidence, ...]
    pressures: tuple[PressureInput, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != "ppm-paper-evidence-v1":
            raise ValueError("unsupported paper evidence schema")
        if self.classification not in {"SYNTHETIC", "VERIFIED_OBSERVATIONS"}:
            raise ValueError("paper evidence classification is required")
        if not is_sha256_hex(self.raw_completion_sha256):
            raise ValueError("paper evidence must identify raw completion SHA-256")
        if not 1 <= len(self.markets) <= 32 or len(self.pressures) > 100_000:
            raise ValueError("paper evidence exceeds market/pressure bounds")
        if len({item.market_id for item in self.markets}) != len(self.markets):
            raise ValueError("duplicate paper market evidence")
        if self.collector_qualification not in {"PASS", "FAIL", "UNAVAILABLE", "SYNTHETIC_ONLY"}:
            raise ValueError("unsupported collector qualification outcome")
        if self.collector_qualification == "PASS" and (
            self.qualification_source_sha256 is None
            or not is_sha256_hex(self.qualification_source_sha256)
        ):
            raise ValueError("collector PASS requires separately declared evidence hash")
        if self.classification == "SYNTHETIC":
            if self.collector_qualification != "SYNTHETIC_ONLY":
                raise ValueError("synthetic data cannot establish collector acceptance")
        else:
            if self.collector_qualification == "SYNTHETIC_ONLY":
                raise ValueError("observed data cannot claim synthetic qualification")
            for item in self.markets:
                rules = (
                    item.risk_evidence,
                    *(
                        rule.evidence
                        for rule in (item.fee_rules, item.reward_rules)
                        if rule is not None
                    ),
                )
                if any(rule.synthetic for rule in rules):
                    raise ValueError("synthetic economic references cannot qualify observed data")
                if item.calibration_period is not None and (
                    item.calibration_period.source_kind is CalibrationSource.SYNTHETIC
                ):
                    raise ValueError("synthetic calibration cannot qualify observed data")
            if any(
                item.pressure.kind is PressureKind.SYNTHETIC_EXECUTION for item in self.pressures
            ):
                raise ValueError("synthetic execution cannot be attached to observed data")
        if tuple(sorted(self.pressures, key=lambda item: item.ingest_seq)) != self.pressures:
            raise ValueError("pressure input boundaries must follow recorded ingest order")

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(self)


def load_paper_evidence(path: Path) -> PaperEvidence:
    return decode(PaperEvidence, read_json(path))
