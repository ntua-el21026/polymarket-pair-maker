"""Strict, bounded decoding for explicitly selected PAPER configuration types.

No type name is loaded from input. The caller selects an audited dataclass;
unknown fields and implicit numeric coercions are rejected recursively.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Literal, Union, cast, get_args, get_origin, get_type_hints

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, Rate
from polymarket_pair_maker.core.ids import TypedId
from polymarket_pair_maker.core.time import UtcTimestamp


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def read_json(path: Path, *, maximum_bytes: int = 8_388_608) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError("paper input must be a regular file, not a symlink")
    with path.open("rb") as handle:
        raw = handle.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise ValueError("paper input exceeds byte limit")
    return cast(object, json.loads(raw, object_pairs_hook=_pairs))


def decode[T](kind: type[T], value: object) -> T:
    return cast(T, _decode(kind, value, depth=0))


@lru_cache(maxsize=128)
def _schema(kind: type[object]) -> tuple[frozenset[str], dict[str, object]]:
    """Cache fixed code-selected type metadata, never decoded input or values."""
    if not is_dataclass(kind):
        raise TypeError("paper schema metadata requires a dataclass")
    return (
        frozenset(field.name for field in fields(kind) if field.init),
        cast(dict[str, object], get_type_hints(kind)),
    )


def _decode(kind: object, value: object, *, depth: int) -> object:
    if depth > 32:
        raise ValueError("paper input nesting limit exceeded")
    origin = get_origin(kind)
    arguments = get_args(kind)
    if origin in (Union, UnionType):
        for alternative in arguments:
            try:
                return _decode(alternative, value, depth=depth + 1)
            except TypeError, ValueError:
                pass
        raise ValueError(f"value does not satisfy declared union {kind}")
    if kind is type(None):
        if value is not None:
            raise ValueError("expected null")
        return None
    if origin is Literal:
        if value not in arguments or isinstance(value, float):
            raise ValueError("unsupported literal")
        return value
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ValueError("expected bounded sequence")
        sequence = cast(list[object] | tuple[object, ...], value)
        if len(sequence) > 100_000:
            raise ValueError("expected bounded sequence")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_decode(arguments[0], item, depth=depth + 1) for item in sequence)
        if len(sequence) != len(arguments):
            raise ValueError("tuple length differs from schema")
        return tuple(
            _decode(item_type, item, depth=depth + 1)
            for item_type, item in zip(arguments, sequence, strict=True)
        )
    if kind is str or kind is int or kind is bool:
        if type(value) is not kind:
            raise ValueError(f"expected exact {kind} value")
        if isinstance(value, str) and len(value) > 8192:
            raise ValueError("paper text exceeds byte bound")
        return value
    if kind is ExactDecimal:
        if not isinstance(value, str) or len(value) > 128:
            raise ValueError("financial values require bounded decimal strings")
        parsed = ExactDecimal.parse(value)
        if parsed.scale > 24 or abs(parsed.coefficient) > 10**48:
            raise ValueError("financial value exceeds precision or magnitude bound")
        return parsed
    if kind is Price or kind is Quantity or kind is Money or kind is Rate:
        scalar = kind
        raw_scalar: object = value
        if isinstance(value, dict) and set(cast(dict[object, object], value)) == {"value"}:
            raw_scalar = cast(dict[str, object], value)["value"]
        return scalar(cast(ExactDecimal, _decode(ExactDecimal, raw_scalar, depth=depth + 1)))
    if kind is UtcTimestamp:
        if not isinstance(value, str):
            raise ValueError("timestamps require canonical UTC strings")
        return UtcTimestamp.from_iso8601(value)
    if isinstance(kind, type) and issubclass(kind, TypedId):
        if not isinstance(value, str):
            raise ValueError("typed identifiers require strings")
        return kind(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        if not isinstance(value, str):
            raise ValueError("enum values require strings")
        return kind(value)
    if isinstance(kind, type) and is_dataclass(kind):
        if not isinstance(value, dict):
            raise ValueError("expected an object for the declared paper schema")
        mapping = cast(dict[str, object], value)
        declared, hints = _schema(kind)
        if set(mapping) - declared:
            raise ValueError(f"unknown fields: {sorted(set(mapping) - declared)}")
        kwargs = {key: _decode(hints[key], item, depth=depth + 1) for key, item in mapping.items()}
        return kind(**kwargs)
    raise TypeError(f"unsupported declared paper input type: {kind}")
