"""Current documented public Polymarket capabilities."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolymarketCapabilities:
    """Documented public protocol guarantees, independent of observed fields."""

    supports_sequence_numbers: bool
    supports_book_checksum: bool
    supports_dynamic_tick_size: bool
    supports_rest_book_audit: bool
    supports_market_lifecycle_events: bool


POLYMARKET_CAPABILITIES = PolymarketCapabilities(
    # The public market channel documents timestamps and opaque hashes, but no
    # continuity-bearing venue sequence number or reproducible book checksum.
    supports_sequence_numbers=False,
    supports_book_checksum=False,
    supports_dynamic_tick_size=True,
    supports_rest_book_audit=True,
    supports_market_lifecycle_events=True,
)

__all__ = ["POLYMARKET_CAPABILITIES"]
