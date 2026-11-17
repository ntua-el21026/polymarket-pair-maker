"""Versioned PAPER-only configuration; no transport or account configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from polymarket_pair_maker.core.exact import ExactDecimal, Money
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.paper.encoding import decode
from polymarket_pair_maker.paper.execution import ExecutionConfig
from polymarket_pair_maker.risk.limits import RiskLimits
from polymarket_pair_maker.strategy.engine import StrategyConfig


@dataclass(frozen=True, slots=True)
class PaperConfig:
    schema_version: str = "ppm-paper-config-v1"
    code_version: str = "ppm-paper-v1"
    starting_capital: Money = field(default_factory=lambda: Money(ExactDecimal(1000)))
    maximum_input_records: int = 100_000
    maximum_output_bytes: int = 268_435_456
    maximum_ledger_records: int = 100_000
    evidence_file: str | None = None
    inventory_exit_policy: str = "ON_ABSTENTION_OR_STOP_V1"
    exit_slippage_fraction: ExactDecimal = field(default_factory=lambda: ExactDecimal(1, 3))
    exit_impact_fraction: ExactDecimal = field(default_factory=lambda: ExactDecimal(1, 3))
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    def __post_init__(self) -> None:
        if self.schema_version != "ppm-paper-config-v1" or self.code_version != "ppm-paper-v1":
            raise ValueError("unsupported paper configuration or implementation version")
        if self.inventory_exit_policy != "ON_ABSTENTION_OR_STOP_V1":
            raise ValueError("unsupported inventory exit policy")
        if self.starting_capital.value < self.risk.maximum_total_commitment.value:
            raise ValueError("starting capital must cover configured total commitment")
        if not 1 <= self.maximum_input_records <= 1_000_000:
            raise ValueError("paper input record budget is outside [1, 1000000]")
        if not 1024 <= self.maximum_output_bytes <= 1_073_741_824:
            raise ValueError("paper output byte budget is outside [1024, 1073741824]")
        if not 1 <= self.maximum_ledger_records <= 1_000_000:
            raise ValueError("paper ledger record budget is outside [1, 1000000]")
        if (
            self.strategy.maximum_quote_age_ns != self.risk.maximum_quote_age_ns
            or self.strategy.maximum_book_age_ns != self.risk.maximum_market_data_age_ns
            or self.strategy.minimum_time_to_resolution_ns
            != self.risk.minimum_time_to_resolution_ns
        ):
            raise ValueError("strategy and risk safety time limits must agree")
        if self.execution.submission_latency_ns >= self.risk.maximum_quote_age_ns:
            raise ValueError("submission latency must be shorter than quote lifetime")
        zero, one = ExactDecimal.zero(), ExactDecimal.one()
        if not zero <= self.exit_slippage_fraction <= one:
            raise ValueError("exit slippage fraction must lie in [0,1]")
        if not zero <= self.exit_impact_fraction <= one:
            raise ValueError("exit impact fraction must lie in [0,1]")
        if self.exit_slippage_fraction + self.exit_impact_fraction > one:
            raise ValueError("combined exit costs exceed gross proceeds")
        if self.evidence_file is not None:
            path = Path(self.evidence_file)
            if not self.evidence_file or path.is_absolute() or ".." in path.parts:
                raise ValueError("paper evidence path must be a relative logical reference")

    @property
    def config_hash(self) -> str:
        return canonical_sha256(self)


def load_paper_config(path: Path) -> PaperConfig:
    if path.is_symlink() or not path.is_file():
        raise ValueError("paper configuration must be a regular file")
    if path.stat().st_size > 65_536:
        raise ValueError("paper configuration exceeds byte budget")
    return decode(PaperConfig, tomllib.loads(path.read_text(encoding="utf-8")))
