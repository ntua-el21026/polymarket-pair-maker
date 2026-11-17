"""Repeated fixed-schema decoding keeps exact validation and bounded metadata."""

from dataclasses import dataclass

import pytest

from polymarket_pair_maker.paper import encoding


@dataclass(frozen=True)
class CacheProbe:
    enabled: bool
    count: int


def test_fixed_schema_metadata_is_reused_without_caching_input(monkeypatch: pytest.MonkeyPatch):
    original = encoding.get_type_hints
    calls: list[object] = []

    def observed(kind: object) -> dict[str, object]:
        calls.append(kind)
        return original(kind)

    monkeypatch.setattr(encoding, "get_type_hints", observed)
    assert encoding.decode(CacheProbe, {"enabled": True, "count": 1}) == CacheProbe(True, 1)
    assert encoding.decode(CacheProbe, {"enabled": False, "count": 2}) == CacheProbe(False, 2)
    with pytest.raises(ValueError, match="exact"):
        encoding.decode(CacheProbe, {"enabled": 1, "count": 2})
    with pytest.raises(ValueError, match="unknown fields"):
        encoding.decode(CacheProbe, {"enabled": True, "count": 2, "extra": 3})
    assert calls == [CacheProbe]
