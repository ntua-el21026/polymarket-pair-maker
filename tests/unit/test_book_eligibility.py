from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_pair_maker.core.exact import Price, Quantity
from polymarket_pair_maker.core.health import BookHealth, MarketStatus
from polymarket_pair_maker.core.ids import CanonicalEventId, IngestId, InstrumentId, MarketVersionId
from polymarket_pair_maker.core.provenance import Provenance
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.core.validation import ValidationReasonCode, ValidationStatus
from polymarket_pair_maker.market_data import (
    BinaryBookEligibilityAssessment,
    BinaryBookEligibilityConfig,
    BinaryBookEligibilityEvaluator,
    EligibilityDisposition,
)
from polymarket_pair_maker.reference import (
    PolymarketReferenceBuilder,
    ReferenceBundle,
    ReferenceStatus,
)
from polymarket_pair_maker.semantic import prove_binary_complement
from polymarket_pair_maker.state import BookLevel, ImmutableOrderBookSnapshot
from polymarket_pair_maker.venues.polymarket.schemas import (
    parse_clob_market_info,
    parse_gamma_markets,
    parse_rest_book,
)

FIXTURES = Path(__file__).parents[1] / "golden" / "fixtures"
NOW = UtcTimestamp(2_000_000_000)


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _bundle() -> ReferenceBundle:
    gamma = parse_gamma_markets(_raw("polymarket_gamma_market_keyset.json")).markets[0]
    clob = parse_clob_market_info(
        _raw("polymarket_clob_market_info.json"), condition_id=gamma.condition_id
    )
    rest = parse_rest_book(_raw("polymarket_rest_book_yes.json"))
    return PolymarketReferenceBuilder().build(
        gamma=gamma,
        clob=clob,
        rest_book=rest,
        observed_at=UtcTimestamp(1_000_000_000),
    )


def _provenance() -> Provenance:
    hashes = (hashlib.sha256(b"yes").hexdigest(), hashlib.sha256(b"no").hexdigest())
    return Provenance(
        source_ingest_ids=(IngestId("yes-ingest"), IngestId("no-ingest")),
        source_payload_hashes=hashes,
        code_version="test-v1",
    )


def _level(price: str, quantity: str) -> BookLevel:
    return BookLevel(Price.parse(price), Quantity.parse(quantity))


def _books(
    bundle: ReferenceBundle,
    *,
    health: BookHealth = BookHealth.HEALTHY,
    last_update: UtcTimestamp = NOW,
    yes_asks: tuple[BookLevel, ...] | None = None,
    no_asks: tuple[BookLevel, ...] | None = None,
) -> tuple[ImmutableOrderBookSnapshot, ImmutableOrderBookSnapshot]:
    provenance = _provenance()
    asks = (
        yes_asks or (_level("0.70", "20"), _level("0.80", "10")),
        no_asks or (_level("0.70", "15"), _level("0.80", "20")),
    )
    result: list[ImmutableOrderBookSnapshot] = []
    for index, instrument in enumerate(bundle.instruments):
        result.append(
            ImmutableOrderBookSnapshot(
                instrument_id=instrument.instrument_id,
                market_id=bundle.market.market_id,
                generation=1,
                health=health,
                invalidation_reason=None,
                bids=(_level("0.40", "20"),),
                asks=asks[index],
                best_bid=_level("0.40", "20"),
                best_ask=asks[index][0],
                last_trade_price=None,
                last_source_event_id=CanonicalEventId(f"event-{index}"),
                last_source_provenance=provenance,
                state_source_provenance=provenance,
                market_version_id=bundle.market.version_id,
                precision_rule_version_id=bundle.precision.version_id,
                cost_rule_set_version_id=bundle.cost_rule_set.version_id,
                last_valid_update_time=last_update,
                last_venue_timestamp=last_update,
            )
        )
    return result[0], result[1]


def _assess(
    bundle: ReferenceBundle,
    books: tuple[ImmutableOrderBookSnapshot, ...],
    *,
    observed_at: UtcTimestamp = NOW,
) -> BinaryBookEligibilityAssessment:
    evaluator = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(
            maximum_book_age_ns=1_000_000_000,
            maximum_interleg_skew_ns=100_000_000,
        )
    )
    return evaluator.assess(
        bundle=bundle,
        proof=prove_binary_complement(bundle),
        books=books,
        observed_at=observed_at,
        provenance=_provenance(),
    )


def _reasons(result: BinaryBookEligibilityAssessment) -> set[ValidationReasonCode]:
    return {
        reason for validation in result.validation_results for reason in validation.reason_codes
    }


def test_eligible_data_is_independent_of_complete_set_taker_profit() -> None:
    bundle = _bundle()
    books = _books(bundle)
    assert books[0].best_ask is not None
    assert books[1].best_ask is not None
    assert books[0].best_ask.price.value + books[1].best_ask.price.value > Quantity.parse("1").value
    result = _assess(bundle, books)
    assert result.disposition is EligibilityDisposition.ELIGIBLE
    assert len(result.validation_results) == 4
    assert all(
        validation.status is ValidationStatus.PASS for validation in result.validation_results
    )
    assert len(result.terminal_hash) == 64
    assert result == _assess(bundle, books)
    assert result.provenance.raw_source_refs == _provenance().raw_source_refs


@pytest.mark.parametrize(
    ("book_age_ns", "expected"),
    [
        (999_999_999, EligibilityDisposition.ELIGIBLE),
        (1_000_000_001, EligibilityDisposition.BLOCKED),
        (-1, EligibilityDisposition.BLOCKED),
    ],
)
def test_book_age_and_future_time_fail_closed(
    book_age_ns: int, expected: EligibilityDisposition
) -> None:
    bundle = _bundle()
    books = _books(bundle, last_update=UtcTimestamp(NOW.nanoseconds_since_epoch - book_age_ns))
    result = _assess(bundle, books)
    assert result.disposition is expected
    if expected is EligibilityDisposition.BLOCKED:
        assert ValidationReasonCode.STALE_BOOK in _reasons(result)


@pytest.mark.parametrize(
    "case", ["missing", "duplicate", "wrong-reference", "unhealthy", "no-source", "skew"]
)
def test_invalid_book_pair_is_blocked(case: str) -> None:
    bundle = _bundle()
    yes, no = _books(bundle)
    books: tuple[ImmutableOrderBookSnapshot, ...] = (yes, no)
    if case == "missing":
        books = (yes,)
    elif case == "duplicate":
        books = (yes, yes)
    elif case == "wrong-reference":
        books = (replace(yes, market_version_id=MarketVersionId("obsolete")), no)
    elif case == "unhealthy":
        books = (replace(yes, health=BookHealth.STALE), no)
    elif case == "no-source":
        books = (
            replace(
                yes,
                last_source_event_id=None,
                last_source_provenance=None,
                state_source_provenance=None,
            ),
            no,
        )
    else:
        books = (replace(yes, last_valid_update_time=UtcTimestamp(1_800_000_000)), no)
    result = _assess(bundle, books)
    assert result.disposition is EligibilityDisposition.BLOCKED
    assert _reasons(result)


@pytest.mark.parametrize("case", ["negative-risk", "closed", "unknown-precision"])
def test_unsupported_reference_is_blocked(case: str) -> None:
    bundle = _bundle()
    if case == "negative-risk":
        bundle = replace(bundle, market=replace(bundle.market, neg_risk=True))
    elif case == "closed":
        bundle = replace(bundle, market=replace(bundle.market, status=MarketStatus.CLOSED))
    else:
        bundle = replace(
            bundle,
            precision=replace(
                bundle.precision, status=ReferenceStatus.INDETERMINATE, reason="unverified"
            ),
        )
    result = _assess(bundle, _books(bundle))
    assert result.disposition is EligibilityDisposition.BLOCKED
    assert _reasons(result)


def test_config_rejects_invalid_coherence_bounds() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        BinaryBookEligibilityConfig(maximum_book_age_ns=-1, maximum_interleg_skew_ns=0)
    with pytest.raises(ValueError, match="cannot exceed"):
        BinaryBookEligibilityConfig(maximum_book_age_ns=1, maximum_interleg_skew_ns=2)
    with pytest.raises(ValueError, match="non-negative"):
        BinaryBookEligibilityConfig.from_values(
            maximum_book_age_seconds=-0.0000000001, maximum_interleg_skew_seconds=0.0
        )


def test_assessment_rejects_contradictory_disposition_and_tampered_hash() -> None:
    bundle = _bundle()
    result = _assess(bundle, _books(bundle))
    with pytest.raises(ValueError, match="contradicts"):
        replace(result, disposition=EligibilityDisposition.BLOCKED)
    with pytest.raises(ValueError, match="terminal hash"):
        replace(result, terminal_hash="0" * 64)


def test_foreign_instrument_in_supplied_binary_proof_fails_closed() -> None:
    bundle = _bundle()
    evaluator = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(
            maximum_book_age_ns=1_000_000_000,
            maximum_interleg_skew_ns=100_000_000,
        )
    )
    forged = replace(prove_binary_complement(bundle), yes_instrument_id=InstrumentId("foreign"))
    result = evaluator.assess(
        bundle=bundle,
        proof=forged,
        books=_books(bundle),
        observed_at=NOW,
        provenance=_provenance(),
    )
    assert result.disposition is EligibilityDisposition.BLOCKED
    assert ValidationReasonCode.SEMANTIC_AMBIGUITY in _reasons(result)


@pytest.mark.parametrize(
    "case", ["negative-risk", "unknown-negative-risk", "augmented", "outcome-name"]
)
def test_stale_binary_proof_cannot_admit_changed_current_semantics(case: str) -> None:
    bundle = _bundle()
    old_proof = prove_binary_complement(bundle)
    if case == "negative-risk":
        changed = replace(bundle, market=replace(bundle.market, neg_risk=True))
    elif case == "unknown-negative-risk":
        changed = replace(bundle, market=replace(bundle.market, neg_risk=None))
    elif case == "augmented":
        changed = replace(bundle, market=replace(bundle.market, neg_risk_augmented=True))
    else:
        changed = replace(
            bundle,
            instruments=tuple(
                replace(
                    instrument, outcome_name="Unrelated" if instrument.outcome_index == 0 else "No"
                )
                for instrument in bundle.instruments
            ),
        )
    evaluator = BinaryBookEligibilityEvaluator(
        BinaryBookEligibilityConfig(
            maximum_book_age_ns=1_000_000_000,
            maximum_interleg_skew_ns=100_000_000,
        )
    )
    # Establish the ordinary reference before replacing its current semantics.
    ordinary = evaluator.assess(
        bundle=bundle,
        proof=old_proof,
        books=_books(bundle),
        observed_at=NOW,
        provenance=_provenance(),
    )
    assert ordinary.disposition is EligibilityDisposition.ELIGIBLE
    result = evaluator.assess(
        bundle=changed,
        proof=old_proof,
        books=_books(changed),
        observed_at=NOW,
        provenance=_provenance(),
    )
    assert result.disposition is EligibilityDisposition.BLOCKED
    assert ValidationReasonCode.SEMANTIC_AMBIGUITY in _reasons(result) or (
        ValidationReasonCode.NEGATIVE_RISK_UNSUPPORTED in _reasons(result)
    )
