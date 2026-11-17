"""Immutable consumer boundary over the existing canonical book owner."""

from dataclasses import dataclass

from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.market_data import BinaryBookEligibilityAssessment
from polymarket_pair_maker.reference import ReferenceBundle
from polymarket_pair_maker.semantic import BinarySemanticProof
from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot


@dataclass(frozen=True, slots=True)
class CanonicalMarketView:
    """An as-known view; a blocked assessment is never an affirmative admission."""

    bundle: ReferenceBundle
    proof: BinarySemanticProof
    books: tuple[ImmutableOrderBookSnapshot, ...]
    assessment: BinaryBookEligibilityAssessment
    observed_at: UtcTimestamp
    audit_ineligible: bool
