"""Dated economic evidence and serializable exact rational diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from polymarket_pair_maker.core.exact import ExactDecimal, RoundingMode
from polymarket_pair_maker.core.ids import MarketId
from polymarket_pair_maker.core.ids.types import is_sha256_hex
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.reference.models import BitemporalFields


@dataclass(frozen=True, slots=True)
class RuleEvidence:
    """A market-scoped reference, with both knowledge and effective validity.

    ``source_sha256`` identifies the retained source or synthetic fixture bytes,
    not the URL. Unknown details stay explicit even when other fields are known.
    """

    version: str
    protocol_version: str
    market_id: MarketId
    sources: tuple[str, ...]
    source_sha256: str
    times: BitemporalFields
    synthetic: bool = False
    unresolved_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.version or not self.protocol_version or not self.sources:
            raise ValueError("economic rules require version, protocol and source provenance")
        if any(not source.strip() for source in self.sources):
            raise ValueError("source references cannot be blank")
        if not is_sha256_hex(self.source_sha256):
            raise ValueError("economic source fingerprint must be a SHA-256")
        if type(self.synthetic) is not bool:
            raise TypeError("synthetic must be a boolean")
        if any(not reason.strip() for reason in self.unresolved_requirements):
            raise ValueError("unresolved requirements cannot be blank")

    def valid_at(self, at: UtcTimestamp) -> tuple[str, ...]:
        reasons = list(self.unresolved_requirements)
        if self.times.observed_at > at:
            reasons.append("RULE_NOT_YET_OBSERVED")
        if self.times.effective_from is None:
            reasons.append("RULE_EFFECTIVE_START_UNKNOWN")
        elif self.times.effective_from > at:
            reasons.append("RULE_NOT_YET_EFFECTIVE")
        if self.times.effective_to is None:
            reasons.append("RULE_VALIDITY_END_UNKNOWN")
        elif at >= self.times.effective_to:
            reasons.append("RULE_EXPIRED")
        if self.times.superseded_at is not None and at >= self.times.superseded_at:
            reasons.append("RULE_SUPERSEDED")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class ExactRatio:
    """Exact nonnegative score/share; never truncate a repeating reward ratio.

    Financial inputs and cash outputs use ExactDecimal. Two integers preserve
    intermediate rational scores in canonical artifacts without binary floats.
    """

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if type(self.numerator) is not int or type(self.denominator) is not int:
            raise TypeError("ratio components must be integers")
        if self.numerator < 0 or self.denominator <= 0:
            raise ValueError("score ratio must be nonnegative with positive denominator")
        value = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", value.numerator)
        object.__setattr__(self, "denominator", value.denominator)

    @classmethod
    def from_fraction(cls, value: Fraction) -> ExactRatio:
        return cls(value.numerator, value.denominator)

    @classmethod
    def from_decimal(cls, value: ExactDecimal) -> ExactRatio:
        return cls.from_fraction(value.as_fraction())

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_decimal(self, *, scale: int = 18, rounding: RoundingMode) -> ExactDecimal:
        return ExactDecimal(self.numerator).divide(
            ExactDecimal(self.denominator), scale=scale, rounding=rounding
        )
