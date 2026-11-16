"""Deterministic binary book admission; no quoting or profit evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from polymarket_pair_maker.core.ids import InstrumentId, MarketId, ValidatorId, ValidatorVersion
from polymarket_pair_maker.core.provenance import Provenance, merge_raw_source_refs
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.core.validation import (
    ValidationReason,
    ValidationReasonCode,
    ValidationResult,
    ValidationStatus,
)
from polymarket_pair_maker.reference import MarketStatus, ReferenceBundle, ReferenceStatus
from polymarket_pair_maker.semantic import BinarySemanticProof, prove_binary_complement
from polymarket_pair_maker.state.order_books import BookHealth, ImmutableOrderBookSnapshot

_VALIDATOR_VERSION = ValidatorVersion.from_components(
    "ValidatorVersion", "binary-book-eligibility-v1"
)
_SEMANTIC_VALIDATOR = ValidatorId.from_components("ValidatorId", "v5-binary-complement")
_REFERENCE_VALIDATOR = ValidatorId.from_components("ValidatorId", "v4-reference")
_BOOK_VALIDATOR = ValidatorId.from_components("ValidatorId", "v3-book-state")
_COHERENCE_VALIDATOR = ValidatorId.from_components("ValidatorId", "v8-coherence")


class EligibilityDisposition(StrEnum):
    BLOCKED = "BLOCKED"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True, slots=True)
class BinaryBookEligibilityConfig:
    maximum_book_age_ns: int
    maximum_interleg_skew_ns: int

    def __post_init__(self) -> None:
        if self.maximum_book_age_ns < 0 or self.maximum_interleg_skew_ns < 0:
            raise ValueError("eligibility time bounds must be non-negative")
        if self.maximum_interleg_skew_ns > self.maximum_book_age_ns:
            raise ValueError("inter-leg skew cannot exceed maximum book age")

    @classmethod
    def from_values(
        cls, *, maximum_book_age_seconds: float, maximum_interleg_skew_seconds: float
    ) -> Self:
        if maximum_book_age_seconds < 0 or maximum_interleg_skew_seconds < 0:
            raise ValueError("eligibility second values must be non-negative")
        return cls(
            maximum_book_age_ns=int(maximum_book_age_seconds * 1_000_000_000),
            maximum_interleg_skew_ns=int(maximum_interleg_skew_seconds * 1_000_000_000),
        )


@dataclass(frozen=True, slots=True)
class BinaryBookEligibilityAssessment:
    """Bounded immutable evidence about admission of one binary book pair.

    ELIGIBLE establishes data prerequisites only. Strategy selection, rewards,
    fills, inventory and execution remain outside this foundation.
    """

    market_id: MarketId
    disposition: EligibilityDisposition
    observed_at: UtcTimestamp
    validation_results: tuple[ValidationResult, ...]
    book_state_hashes: tuple[tuple[InstrumentId, str], ...]
    provenance: Provenance
    terminal_hash: str

    schema_version: str = "ppm-binary-book-eligibility-v1"

    def __post_init__(self) -> None:
        if self.schema_version != "ppm-binary-book-eligibility-v1":
            raise ValueError("unsupported book eligibility schema")
        if len(self.validation_results) != 4:
            raise ValueError("book eligibility requires all four prerequisite validations")
        expected_disposition = (
            EligibilityDisposition.ELIGIBLE
            if all(result.status is ValidationStatus.PASS for result in self.validation_results)
            else EligibilityDisposition.BLOCKED
        )
        if self.disposition is not expected_disposition:
            raise ValueError("eligibility disposition contradicts prerequisite validations")
        expected = canonical_sha256(
            {
                "schema_version": self.schema_version,
                "market_id": self.market_id,
                "disposition": self.disposition,
                "observed_at": self.observed_at,
                "validation_results": self.validation_results,
                "book_state_hashes": self.book_state_hashes,
                "provenance": self.provenance,
            }
        )
        if self.terminal_hash and self.terminal_hash != expected:
            raise ValueError("book eligibility terminal hash does not match canonical content")
        object.__setattr__(self, "terminal_hash", expected)

    @classmethod
    def create(
        cls,
        *,
        market_id: MarketId,
        disposition: EligibilityDisposition,
        observed_at: UtcTimestamp,
        validation_results: tuple[ValidationResult, ...],
        book_state_hashes: tuple[tuple[InstrumentId, str], ...],
        provenance: Provenance,
    ) -> Self:
        return cls(
            market_id=market_id,
            disposition=disposition,
            observed_at=observed_at,
            validation_results=validation_results,
            book_state_hashes=book_state_hashes,
            provenance=provenance,
            terminal_hash="",
        )


def _passed(
    validator_id: ValidatorId,
    *,
    subject: object,
    observed_at: UtcTimestamp,
    evidence: tuple[tuple[str, str], ...] = (),
    provenance: Provenance,
) -> ValidationResult:
    return ValidationResult.passed(
        validator_id=validator_id,
        validator_version=_VALIDATOR_VERSION,
        subject_id=str(subject),
        observed_at=observed_at,
        evidence=evidence,
        provenance=provenance,
    )


def _blocked(
    validator_id: ValidatorId,
    *,
    subject: object,
    observed_at: UtcTimestamp,
    status: ValidationStatus,
    code: ValidationReasonCode,
    message: str,
    evidence: tuple[tuple[str, str], ...] = (),
    provenance: Provenance,
) -> ValidationResult:
    reason = ValidationReason(code=code, message=message, evidence=evidence)
    factory = (
        ValidationResult.failed
        if status is ValidationStatus.FAIL
        else ValidationResult.indeterminate
    )
    return factory(
        validator_id=validator_id,
        validator_version=_VALIDATOR_VERSION,
        subject_id=str(subject),
        observed_at=observed_at,
        reasons=(reason,),
        provenance=provenance,
    )


def _all_pass(results: tuple[ValidationResult, ...]) -> bool:
    return all(result.status is ValidationStatus.PASS for result in results)


class BinaryBookEligibilityEvaluator:
    """Apply the retained reference, semantics, freshness and book integrity gates."""

    def __init__(self, config: BinaryBookEligibilityConfig) -> None:
        self.config = config

    def assess(
        self,
        *,
        bundle: ReferenceBundle,
        proof: BinarySemanticProof,
        books: tuple[ImmutableOrderBookSnapshot, ...],
        observed_at: UtcTimestamp,
        provenance: Provenance,
        book_state_hashes: Mapping[InstrumentId, str] | None = None,
    ) -> BinaryBookEligibilityAssessment:
        sources = tuple(
            source
            for book in books
            if (source := book.state_source_provenance or book.last_source_provenance) is not None
        )
        provenance = Provenance.from_raw_source_refs(
            merge_raw_source_refs(
                provenance.raw_source_refs, *(source.raw_source_refs for source in sources)
            ),
            reference_version_ids=tuple(
                dict.fromkeys(
                    (
                        *provenance.reference_version_ids,
                        *bundle.current_version_ids,
                        proof.semantics_version_id,
                    )
                )
            ),
            config_hash=provenance.config_hash,
            code_version=provenance.code_version,
            attributes=provenance.attributes,
        )
        validations = (
            self._validate_semantics(bundle, proof, observed_at, provenance),
            self._validate_reference(bundle, observed_at, provenance),
            self._validate_books(bundle, books, observed_at, provenance),
            self._validate_coherence(books, observed_at, provenance, bundle.market.market_id),
        )
        hashes = tuple(
            sorted(
                (
                    (
                        book.instrument_id,
                        book.economic_content_sha256()
                        if book_state_hashes is None
                        else book_state_hashes[book.instrument_id],
                    )
                    for book in books
                ),
                key=lambda item: item[0].value,
            )
        )
        return BinaryBookEligibilityAssessment.create(
            market_id=bundle.market.market_id,
            disposition=EligibilityDisposition.ELIGIBLE
            if _all_pass(validations)
            else EligibilityDisposition.BLOCKED,
            observed_at=observed_at,
            validation_results=validations,
            book_state_hashes=hashes,
            provenance=provenance,
        )

    def _validate_semantics(
        self,
        bundle: ReferenceBundle,
        proof: BinarySemanticProof,
        observed_at: UtcTimestamp,
        provenance: Provenance,
    ) -> ValidationResult:
        instruments = tuple(sorted(bundle.instruments, key=lambda item: item.outcome_index))
        proof_matches = (
            len(instruments) == 2
            and proof.market_id == bundle.market.market_id
            and proof.native_condition_id == bundle.market.native_condition_id
            and proof.yes_instrument_id == instruments[0].instrument_id
            and proof.no_instrument_id == instruments[1].instrument_id
        )
        if not proof_matches or not proof.is_proven or proof != prove_binary_complement(bundle):
            negative_risk = any(
                value is True
                for value in (
                    bundle.market.neg_risk,
                    bundle.market.neg_risk_augmented,
                    bundle.market.neg_risk_other,
                )
            )
            return _blocked(
                _SEMANTIC_VALIDATOR,
                subject=bundle.market.market_id,
                observed_at=observed_at,
                status=ValidationStatus.FAIL,
                code=(
                    ValidationReasonCode.NEGATIVE_RISK_UNSUPPORTED
                    if negative_risk
                    else ValidationReasonCode.SEMANTIC_AMBIGUITY
                ),
                message=proof.reason or "binary complement proof does not match the market",
                provenance=provenance,
            )
        if (
            proof.payout_asset_id != bundle.payout_asset_id
            or proof.payout_per_complete_set != bundle.payout_per_set
        ):
            return _blocked(
                _SEMANTIC_VALIDATOR,
                subject=bundle.market.market_id,
                observed_at=observed_at,
                status=ValidationStatus.FAIL,
                code=ValidationReasonCode.UNPROVEN_ECONOMIC_RELATION,
                message="semantic proof payout differs from current reference",
                provenance=provenance,
            )
        return _passed(
            _SEMANTIC_VALIDATOR,
            subject=bundle.market.market_id,
            observed_at=observed_at,
            evidence=(("semantics_version_id", proof.semantics_version_id.value),),
            provenance=provenance,
        )

    def _validate_reference(
        self,
        bundle: ReferenceBundle,
        observed_at: UtcTimestamp,
        provenance: Provenance,
    ) -> ValidationResult:
        if bundle.market.status is not MarketStatus.OPEN:
            return _blocked(
                _REFERENCE_VALIDATOR,
                subject=bundle.market.market_id,
                observed_at=observed_at,
                status=(
                    ValidationStatus.INDETERMINATE
                    if bundle.market.status is MarketStatus.UNKNOWN
                    else ValidationStatus.FAIL
                ),
                code=ValidationReasonCode.MARKET_NOT_OPEN,
                message=f"market status is {bundle.market.status.value}",
                provenance=provenance,
            )
        if (
            bundle.market.active is not True
            or bundle.market.closed is not False
            or bundle.market.archived is not False
            or bundle.market.accepting_orders is not True
            or bundle.market.enable_order_book is not True
        ):
            return _blocked(
                _REFERENCE_VALIDATOR,
                subject=bundle.market.market_id,
                observed_at=observed_at,
                status=ValidationStatus.INDETERMINATE,
                code=ValidationReasonCode.UNKNOWN_REFERENCE_DATA,
                message="market lifecycle/order-book eligibility is not explicitly current",
                provenance=provenance,
            )
        if bundle.precision.status is not ReferenceStatus.VERIFIED:
            return _blocked(
                _REFERENCE_VALIDATOR,
                subject=bundle.market.market_id,
                observed_at=observed_at,
                status=ValidationStatus.INDETERMINATE,
                code=ValidationReasonCode.UNKNOWN_PRECISION,
                message=bundle.precision.reason or "precision is not verified",
                provenance=provenance,
            )
        return _passed(
            _REFERENCE_VALIDATOR,
            subject=bundle.market.market_id,
            observed_at=observed_at,
            evidence=(("market_version_id", bundle.market.version_id.value),),
            provenance=provenance,
        )

    def _validate_books(
        self,
        bundle: ReferenceBundle,
        books: tuple[ImmutableOrderBookSnapshot, ...],
        observed_at: UtcTimestamp,
        provenance: Provenance,
    ) -> ValidationResult:
        expected = {item.instrument_id for item in bundle.instruments}
        actual = {book.instrument_id for book in books}
        if len(books) != 2 or actual != expected or len(actual) != len(books):
            return _blocked(
                _BOOK_VALIDATOR,
                subject=bundle.market.market_id,
                observed_at=observed_at,
                status=ValidationStatus.FAIL,
                code=ValidationReasonCode.UNKNOWN_INSTRUMENT,
                message="book instruments do not exactly match the binary reference",
                provenance=provenance,
            )
        tick = bundle.tick_size
        for book in books:
            if book.health is not BookHealth.HEALTHY or book.last_valid_update_time is None:
                return _blocked(
                    _BOOK_VALIDATOR,
                    subject=book.instrument_id,
                    observed_at=observed_at,
                    status=ValidationStatus.FAIL,
                    code=ValidationReasonCode.UNHEALTHY_BOOK,
                    message=f"book health is {book.health.value}",
                    provenance=provenance,
                )
            if not book.is_fresh(observed_at, max_age_ns=self.config.maximum_book_age_ns):
                return _blocked(
                    _BOOK_VALIDATOR,
                    subject=book.instrument_id,
                    observed_at=observed_at,
                    status=ValidationStatus.FAIL,
                    code=ValidationReasonCode.STALE_BOOK,
                    message="book exceeds the configured freshness bound",
                    provenance=provenance,
                )
            if (
                book.market_id != bundle.market.market_id
                or book.market_version_id != bundle.market.version_id
                or book.precision_rule_version_id != bundle.precision.version_id
                or book.cost_rule_set_version_id != bundle.cost_rule_set.version_id
            ):
                return _blocked(
                    _BOOK_VALIDATOR,
                    subject=book.instrument_id,
                    observed_at=observed_at,
                    status=ValidationStatus.FAIL,
                    code=ValidationReasonCode.REFERENCE_VERSION_MISMATCH,
                    message="book reference linkage is not current",
                    provenance=provenance,
                )
            source_provenance = book.state_source_provenance or book.last_source_provenance
            has_raw_provenance = (
                source_provenance is not None
                and bool(source_provenance.source_ingest_ids)
                and bool(source_provenance.source_payload_hashes)
            )
            if book.generation <= 0 or (
                book.last_source_event_id is None and not has_raw_provenance
            ):
                return _blocked(
                    _BOOK_VALIDATOR,
                    subject=book.instrument_id,
                    observed_at=observed_at,
                    status=ValidationStatus.FAIL,
                    code=ValidationReasonCode.UNHEALTHY_BOOK,
                    message="book lacks an initialized source event/generation",
                    provenance=provenance,
                )
            if tick is None or any(
                not level.price.value.is_integral_multiple_of(tick)
                for level in (*book.bids, *book.asks)
            ):
                return _blocked(
                    _BOOK_VALIDATOR,
                    subject=book.instrument_id,
                    observed_at=observed_at,
                    status=ValidationStatus.FAIL,
                    code=ValidationReasonCode.PRECISION_VIOLATION,
                    message="book contains a price outside the current tick grid",
                    provenance=provenance,
                )
        return _passed(
            _BOOK_VALIDATOR,
            subject=bundle.market.market_id,
            observed_at=observed_at,
            evidence=(("book_count", str(len(books))),),
            provenance=provenance,
        )

    def _validate_coherence(
        self,
        books: tuple[ImmutableOrderBookSnapshot, ...],
        observed_at: UtcTimestamp,
        provenance: Provenance,
        subject: object,
    ) -> ValidationResult:
        if len(books) != 2 or not books[0].coherent_with(
            books[1], max_receive_skew_ns=self.config.maximum_interleg_skew_ns
        ):
            return _blocked(
                _COHERENCE_VALIDATOR,
                subject=subject,
                observed_at=observed_at,
                status=ValidationStatus.FAIL,
                code=ValidationReasonCode.INTERLEG_SKEW_EXCEEDED,
                message="joint input books exceed the receive-time coherence bound",
                provenance=provenance,
            )
        return _passed(
            _COHERENCE_VALIDATOR,
            subject=subject,
            observed_at=observed_at,
            provenance=provenance,
        )
