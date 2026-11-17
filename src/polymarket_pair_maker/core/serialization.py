"""Deterministic canonical JSON serialization and content hashing."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import Field, dataclass, fields, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Protocol, cast
from weakref import ReferenceType, ref

from .exact import ExactDecimal, Price, Quantity
from .ids import TypedId
from .time import MonotonicTimestamp, UtcTimestamp

type CanonicalScalar = bool | int | float | str | None
type CanonicalValue = CanonicalScalar | list[CanonicalValue] | dict[str, CanonicalValue]


class CanonicalSerializationError(TypeError):
    """Raised when an object cannot be serialized without hidden semantics."""


class DuplicateJsonFieldError(ValueError):
    """A JSON object contains a repeated field, even if its values agree."""


class _FragmentEncodingFailure(Exception):
    """A validated fragment needs original whole-tree encoding error ordering."""


class _DataclassParameters(Protocol):
    frozen: bool


@lru_cache(maxsize=128)
def _dataclass_fields(kind: type[object]) -> tuple[Field[Any], ...]:
    """Retain fixed field discovery, reading values and metadata on every use."""
    if not is_dataclass(kind):
        raise CanonicalSerializationError("canonical fields require a dataclass")
    return fields(kind)


type _LeafPath = tuple[str | int, ...]
type _EnumLeaf = tuple[_LeafPath, CanonicalScalar]


@dataclass(frozen=True, slots=True)
class _SequenceEncoding:
    values: tuple[object, ...]
    element_type: type[object]
    audited_types: tuple[type[object], ...]
    maximum_depth: int
    encoded: bytes


_SEQUENCE_ENCODINGS: OrderedDict[int, _SequenceEncoding] = OrderedDict()
_sequence_encoding_bytes = 0
_SEQUENCE_LOCK = RLock()
_MAX_SEQUENCE_ENCODINGS = 8
_MAX_SEQUENCE_ITEMS = 4096
_MAX_SEQUENCE_BYTES = 65536
_MAX_SEQUENCE_TOTAL_BYTES = 262144


def _registered_sequence(
    value: object,
    *,
    audited_types: tuple[type[object], ...] | None = None,
    maximum_depth: int | None = None,
) -> _SequenceEncoding | None:
    if type(value) is not tuple:
        return None
    key = id(cast(object, value))
    with _SEQUENCE_LOCK:
        previous = _SEQUENCE_ENCODINGS.get(key)
        if previous is None or previous.values is not value:
            return None
        if maximum_depth is not None and previous.maximum_depth > maximum_depth:
            return None
        if audited_types is not None and not all(
            any(kind is allowed for allowed in audited_types) for kind in previous.audited_types
        ):
            return None
        _SEQUENCE_ENCODINGS.move_to_end(key)
        return previous


def _registration_resources(
    value: object, audited: tuple[type[object], ...], budget: list[int], depth: int = 0
) -> bool:
    # Bound optional proof/encoding work before formatting a giant exact scalar.
    # The byte budget is a conservative upper bound on temporary JSON size.
    budget[0] -= 1
    budget[1] -= 2
    if len(budget) > 2:
        budget[2] = max(budget[2], depth)
    if depth > 32 or budget[0] < 0 or budget[1] < 0:
        return False
    if value is None:
        budget[1] -= 2
        return budget[1] >= 0
    if type(value) is bool:
        budget[1] -= 3
        return budget[1] >= 0
    if type(value) is int:
        if value.bit_length() > 512:
            return False
        budget[1] -= 2 + value.bit_length() * 30103 // 100000
        return budget[1] >= 0
    if type(value) is str:
        budget[1] -= 6 * len(value)
        return len(value) <= 8192 and budget[1] >= 0
    if type(value) is bytes:
        budget[1] -= 4 * ((len(value) + 2) // 3) + 32
        return len(value) <= 8192 and budget[1] >= 0
    if type(value) is ExactDecimal:
        if not (
            type(value.coefficient) is int
            and type(value.scale) is int
            and value.coefficient.bit_length() <= 512
            and 0 <= value.scale <= 128
        ):
            return False
        digits = 1 + value.coefficient.bit_length() * 30103 // 100000
        budget[1] -= max(digits, value.scale + 1) + 2
        return budget[1] >= 0
    if isinstance(value, TypedId):
        return _registration_resources(value.value, audited, budget, depth + 1)
    if type(value) is UtcTimestamp:
        budget[1] -= 32
        return _registration_resources(value.nanoseconds_since_epoch, audited, budget, depth + 1)
    if type(value) is MonotonicTimestamp:
        return _registration_resources(value.epoch_id, audited, budget, depth + 1) and (
            _registration_resources(value.nanoseconds, audited, budget, depth + 1)
        )
    if isinstance(value, Enum):
        return _registration_resources(value.value, audited, budget, depth + 1)
    if type(value) is tuple:
        budget[1] -= max(0, len(cast(tuple[object, ...], value)) - 1)
        if budget[1] < 0:
            return False
        return all(
            _registration_resources(item, audited, budget, depth + 1)
            for item in cast(tuple[object, ...], value)
        )
    if type(value) not in audited or not is_dataclass(value) or isinstance(value, type):
        return False
    for item in _dataclass_fields(type(value)):
        if item.metadata.get("canonical_serialize", True):
            # Key quotes/escaping, colon and a conservative field separator.
            budget[1] -= 6 * len(item.name) + 4
            if not _registration_resources(getattr(value, item.name), audited, budget, depth + 1):
                return False
    return True


def register_canonical_sequence(
    values: object,
    *,
    element_type: type[object],
    audited_types: tuple[type[object], ...],
) -> bool:
    """Optionally retain one strictly validated immutable sequence by identity.

    This is an optimization boundary: unsupported, oversized or failed values
    return False and keep their ordinary serializer/validation behavior.
    """
    global _sequence_encoding_bytes
    if (
        type(values) is not tuple
        or len(cast(tuple[object, ...], values)) > _MAX_SEQUENCE_ITEMS
        or type(audited_types) is not tuple
        or len(audited_types) > 32
        or not isinstance(cast(object, element_type), type)
        or any(not isinstance(cast(object, kind), type) for kind in audited_types)
    ):
        return False
    sequence = cast(tuple[object, ...], values)
    previous = _registered_sequence(sequence)
    if previous is not None and (
        previous.element_type is element_type
        and len(previous.audited_types) == len(audited_types)
        and all(
            left is right for left, right in zip(previous.audited_types, audited_types, strict=True)
        )
    ):
        return True
    if any(type(item) is not element_type for item in sequence):
        return False
    leaves: list[_EnumLeaf] = []
    budget = [65536, 262144, 0]
    try:
        if not _registration_resources(sequence, audited_types, budget):
            return False
        if not _immutable(sequence, audited_types, set(), [], leaves) or leaves:
            return False
        encoded = _encode(canonicalize(sequence))
    except TypeError, ValueError, OverflowError, RecursionError:
        return False
    if len(encoded) > _MAX_SEQUENCE_BYTES:
        return False
    key = id(sequence)
    released: list[_SequenceEncoding] = []
    with _SEQUENCE_LOCK:
        replaced = _SEQUENCE_ENCODINGS.pop(key, None)
        if replaced is not None:
            _sequence_encoding_bytes -= len(replaced.encoded)
            released.append(replaced)
        _SEQUENCE_ENCODINGS[key] = _SequenceEncoding(
            sequence, element_type, audited_types, budget[2], encoded
        )
        _sequence_encoding_bytes += len(encoded)
        while (
            len(_SEQUENCE_ENCODINGS) > _MAX_SEQUENCE_ENCODINGS
            or _sequence_encoding_bytes > _MAX_SEQUENCE_TOTAL_BYTES
        ):
            _, evicted = _SEQUENCE_ENCODINGS.popitem(last=False)
            _sequence_encoding_bytes -= len(evicted.encoded)
            released.append(evicted)
    # Keep evicted roots alive until all metadata is coherent and unlocked.
    # Audited custom classes may have a reentrant destructor.
    return True


def canonical_sequence_cache_info() -> dict[str, int]:
    """Report retained proof roots and bytes without traversing shared graphs."""
    with _SEQUENCE_LOCK:
        return {
            "entries": len(_SEQUENCE_ENCODINGS),
            "encoded_bytes": _sequence_encoding_bytes,
            "retained_items": sum(len(entry.values) for entry in _SEQUENCE_ENCODINGS.values()),
            "largest_entry_bytes": max(
                (len(entry.encoded) for entry in _SEQUENCE_ENCODINGS.values()), default=0
            ),
            "maximum_entries": _MAX_SEQUENCE_ENCODINGS,
            "maximum_items_per_entry": _MAX_SEQUENCE_ITEMS,
            "maximum_bytes_per_entry": _MAX_SEQUENCE_BYTES,
            "maximum_encoded_bytes": _MAX_SEQUENCE_TOTAL_BYTES,
        }


def _immutable_financial_scalar(value: Price | Quantity) -> bool:
    exact = value.value
    return (
        type(exact) is ExactDecimal and type(exact.coefficient) is int and type(exact.scale) is int
    )


def _immutable(
    value: object,
    audited: tuple[type[object], ...],
    active: set[int],
    path: list[str | int],
    leaves: list[_EnumLeaf],
) -> bool:
    if len(path) > 32:
        return False
    if type(value) in (type(None), bool, int, float, str, bytes):
        return True
    if isinstance(value, TypedId):
        return type(value).__module__ == TypedId.__module__ and type(value.value) is str
    if type(value) is ExactDecimal:
        return type(value.coefficient) is int and type(value.scale) is int
    if type(value) is Price or type(value) is Quantity:
        return _immutable_financial_scalar(value)
    if type(value) is UtcTimestamp:
        return type(value.nanoseconds_since_epoch) is int
    if type(value) is MonotonicTimestamp:
        return type(value.epoch_id) is str and type(value.nanoseconds) is int
    if isinstance(value, Enum):
        if not (
            (type(value) in audited or type(value).__module__.startswith("polymarket_pair_maker."))
            and type(value.value) in (bool, int, str)
        ):
            return False
        if isinstance(value, (bool, int, str)):
            if len(leaves) >= 64:
                return False
            leaves.append((tuple(path), value))
        return True
    if type(value) is tuple:
        if (
            _registered_sequence(
                cast(object, value), audited_types=audited, maximum_depth=32 - len(path)
            )
            is not None
        ):
            return True
        for index, item in enumerate(cast(tuple[object, ...], value)):
            path.append(index)
            immutable = _immutable(item, audited, active, path, leaves)
            path.pop()
            if not immutable:
                return False
        return True
    if type(value) not in audited or not is_dataclass(value) or isinstance(value, type):
        return False
    parameters: object = getattr(type(value), "__dataclass_params__", None)
    if (
        parameters is None
        or not cast(_DataclassParameters, parameters).frozen
        or id(value) in active
    ):
        return False
    active.add(id(value))
    try:
        for item in _dataclass_fields(type(value)):
            if not item.metadata.get("canonical_serialize", True):
                continue
            path.append(item.name)
            immutable = _immutable(getattr(value, item.name), audited, active, path, leaves)
            path.pop()
            if not immutable:
                return False
        return True
    finally:
        active.remove(id(value))


def _restore_enum_leaves(
    projected: CanonicalValue, leaves: tuple[_EnumLeaf, ...]
) -> CanonicalValue:
    for path, value in leaves:
        if not path:
            projected = value
            continue
        parent = projected
        for key in path[:-1]:
            # Separate branches retain the key/container type correlation.
            if isinstance(key, int) and isinstance(parent, list):  # noqa: SIM114
                parent = parent[key]
            elif isinstance(key, str) and isinstance(parent, dict):
                parent = parent[key]
            else:
                raise CanonicalSerializationError("canonical memo leaf path mismatch")
        final = path[-1]
        if isinstance(final, int) and isinstance(parent, list):  # noqa: SIM114
            parent[final] = value
        elif isinstance(final, str) and isinstance(parent, dict):
            parent[final] = value
        else:
            raise CanonicalSerializationError("canonical memo leaf path mismatch")
    return projected


def is_canonically_immutable(value: object, *, audited_types: tuple[type[object], ...]) -> bool:
    """Audit actual supported immutable values without encoding or retaining them."""
    return _immutable(value, audited_types, set(), [], [])


class CanonicalMemo:
    """Object-owned exact encodings for explicitly audited immutable dataclasses.

    At most two mode entries and 64 KiB total are retained. Oversized or mutable
    values use the original recursive path; no input or verification is omitted.
    A weak owner identity binds the cell; reusing it resets all retained state.
    At most 64 primitive-enum leaf paths of depth at most 32 retain original types.
    """

    __slots__ = ("_audited", "_eligible", "_encoded", "_enum_leaves", "_owner", "_unmemoized_modes")
    maximum_bytes = 65_536

    def __init__(self, audited: tuple[type[object], ...]) -> None:
        self._audited = audited
        self._eligible: bool | None = None
        self._encoded: dict[bool, bytes] = {}
        self._unmemoized_modes: set[bool] = set()
        self._owner: ReferenceType[object] | None = None
        self._enum_leaves: tuple[_EnumLeaf, ...] = ()

    def eligible(self, value: object) -> bool:
        if self._owner is None or self._owner() is not value:
            self._eligible = None
            self._encoded.clear()
            self._unmemoized_modes.clear()
            self._enum_leaves = ()
            try:
                self._owner = ref(value)
            except TypeError:
                self._owner = None
                return False
        if self._eligible is None:
            leaves: list[_EnumLeaf] = []
            self._eligible = type(value) in self._audited and _immutable(
                value, self._audited, set(), [], leaves
            )
            self._enum_leaves = tuple(leaves) if self._eligible else ()
        return self._eligible

    @property
    def retained_bytes(self) -> int:
        """Exact encoded-byte retention, excluding fixed Python object overhead."""
        return sum(map(len, self._encoded.values()))

    def project(self, value: object, *, allow_floats: bool) -> CanonicalValue:
        if type(allow_floats) is not bool or not self.eligible(value):
            return _canonical_dataclass(value, allow_floats=allow_floats)
        previous = self._encoded.get(allow_floats)
        if previous is not None:
            # canonicalize returns independent mutable trees, never memo aliases.
            return _restore_enum_leaves(
                cast(CanonicalValue, json.loads(previous)), self._enum_leaves
            )
        normalized = _canonical_dataclass(value, allow_floats=allow_floats)
        if allow_floats in self._unmemoized_modes:
            return normalized
        try:
            encoded = _encode(normalized)
        except ValueError, UnicodeError:
            # Preserve canonicalize's normalization-only error boundary. Its
            # caller may still reject this tree during actual JSON/UTF-8 encoding.
            self._unmemoized_modes.add(allow_floats)
            return normalized
        if len(encoded) + self.retained_bytes <= self.maximum_bytes:
            self._encoded[allow_floats] = encoded
        else:
            self._unmemoized_modes.add(allow_floats)
        return normalized

    def encoding(self, value: object, *, allow_floats: bool) -> bytes:
        if type(allow_floats) is bool and self.eligible(value):
            previous = self._encoded.get(allow_floats)
            if previous is not None:
                return previous
            normalized = self.project(value, allow_floats=allow_floats)
            previous = self._encoded.get(allow_floats)
            return previous if previous is not None else _encode(normalized)
        return _encode(_canonical_dataclass(value, allow_floats=allow_floats))


class CanonicalMemoized:
    """Explicit opt-in; each dataclass supplies its own excluded memo field."""

    __slots__ = ()
    _canonical_memo: CanonicalMemo

    def canonical_cache_eligible(self) -> bool:
        """Audit actual immutable fields for optional exact computation reuse."""
        return self._canonical_memo.eligible(self)

    def canonical_projection(self, *, allow_floats: bool = False) -> CanonicalValue:
        return self._canonical_memo.project(self, allow_floats=allow_floats)

    def canonical_encoding(self, *, allow_floats: bool = False) -> bytes:
        return self._canonical_memo.encoding(self, allow_floats=allow_floats)

    def canonical_subset_sha256(self, field_names: tuple[str, ...], *, schema: str) -> str:
        """Hash an explicit subset of a fully validated canonical projection."""
        projected = canonicalize(self)
        if not isinstance(projected, dict):
            raise CanonicalSerializationError("canonical subset requires a dataclass projection")
        selected = {name: projected[name] for name in field_names}
        selected["schema"] = canonicalize(schema)
        return hashlib.sha256(_encode(selected)).hexdigest()


class CanonicalComposed:
    """Dataclass opt-in for exact composition of independently encoded fields."""

    __slots__ = ()


def _canonical_dataclass(value: object, *, allow_floats: bool) -> dict[str, CanonicalValue]:
    return {
        item.name: canonicalize(getattr(value, item.name), allow_floats=allow_floats)
        for item in _dataclass_fields(type(value))
        if item.metadata.get("canonical_serialize", True)
    }


def unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate fields before a JSON decoder discards their identity."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonFieldError("duplicate JSON field")
        result[key] = value
    return result


def canonicalize(value: object, *, allow_floats: bool = False) -> CanonicalValue:
    """Convert supported values to deterministic JSON-compatible values.

    Binary floats are rejected by default. Operational configuration and metrics
    may opt in explicitly; non-finite floats are never accepted.
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not allow_floats:
            raise CanonicalSerializationError("binary float canonicalization requires opt-in")
        if not math.isfinite(value):
            raise CanonicalSerializationError("non-finite floats are not canonical JSON")
        return value
    if isinstance(value, ExactDecimal):
        return value.to_canonical_string()
    if (type(value) is Price or type(value) is Quantity) and _immutable_financial_scalar(value):
        # These concrete wrappers have the single inherited canonical value
        # field. Keep its metadata live while avoiding recursive dataclass work.
        item = _dataclass_fields(type(value))[0]
        return (
            {item.name: value.value.to_canonical_string()}
            if item.metadata.get("canonical_serialize", True)
            else {}
        )
    if isinstance(value, TypedId):
        return value.value
    if isinstance(value, UtcTimestamp):
        return value.to_iso8601()
    if isinstance(value, MonotonicTimestamp):
        return value.to_canonical_string()
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return canonicalize(value.value, allow_floats=allow_floats)
    if is_dataclass(value) and not isinstance(value, type):
        if isinstance(value, CanonicalMemoized):
            return value.canonical_projection(allow_floats=allow_floats)
        return _canonical_dataclass(value, allow_floats=allow_floats)
    if isinstance(value, Mapping):
        canonical_mapping: dict[str, CanonicalValue] = {}
        mapping = cast(Mapping[object, object], value)
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise CanonicalSerializationError("canonical mapping keys must be strings")
            canonical_mapping[key] = canonicalize(item, allow_floats=allow_floats)
        return canonical_mapping
    if isinstance(value, Sequence):
        registered = _registered_sequence(cast(object, value))
        if registered is not None:
            return cast(CanonicalValue, json.loads(registered.encoded))
        sequence = cast(Sequence[object], value)
        return [canonicalize(item, allow_floats=allow_floats) for item in sequence]
    raise CanonicalSerializationError(
        f"unsupported canonical value type: {type(value).__module__}.{type(value).__qualname__}"
    )


def canonical_json_bytes(value: object, *, allow_floats: bool = False) -> bytes:
    try:
        return _canonical_encoding(value, allow_floats=allow_floats)
    except _FragmentEncodingFailure:
        # Encoding fields separately can discover a surrogate or oversized int
        # before a later normalization error. Preserve the original whole-tree
        # error ordering on these exceptional paths.
        pass
    return _encode(canonicalize(value, allow_floats=allow_floats))


def _encode_fragment(normalized: CanonicalValue) -> bytes:
    try:
        return _encode(normalized)
    except ValueError, UnicodeError:
        raise _FragmentEncodingFailure from None


def _canonical_encoding(value: object, *, allow_floats: bool, fragment: bool = False) -> bytes:
    registered = _registered_sequence(value)
    if registered is not None:
        return registered.encoded
    if isinstance(value, CanonicalMemoized) and is_dataclass(value) and not isinstance(value, type):
        if value.canonical_cache_eligible():
            if fragment:
                try:
                    return value.canonical_encoding(allow_floats=allow_floats)
                except ValueError, UnicodeError:
                    # An audited immutable owner cannot hide a changing failure.
                    raise _FragmentEncodingFailure from None
            return value.canonical_encoding(allow_floats=allow_floats)
        normalized = canonicalize(cast(object, value), allow_floats=allow_floats)
        return _encode_fragment(normalized) if fragment else _encode(normalized)
    if isinstance(value, CanonicalComposed) and is_dataclass(value) and not isinstance(value, type):
        # Normalize/encode in declaration order, as the original dataclass walk
        # does; sort only the final JSON keys.
        encoded = [
            (
                item.name,
                _canonical_encoding(
                    getattr(value, item.name), allow_floats=allow_floats, fragment=True
                ),
            )
            for item in _dataclass_fields(type(value))
            if item.metadata.get("canonical_serialize", True)
        ]
        return b"{" + b",".join(_encode(key) + b":" + part for key, part in sorted(encoded)) + b"}"
    if type(value) is tuple and any(
        isinstance(item, (CanonicalComposed, CanonicalMemoized))
        for item in cast(tuple[object, ...], value)
    ):
        return (
            b"["
            + b",".join(
                _canonical_encoding(item, allow_floats=allow_floats, fragment=True)
                for item in cast(tuple[object, ...], value)
            )
            + b"]"
        )
    normalized = canonicalize(cast(object, value), allow_floats=allow_floats)
    return _encode_fragment(normalized) if fragment else _encode(normalized)


def _encode(normalized: CanonicalValue) -> bytes:
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_string(value: object, *, allow_floats: bool = False) -> str:
    return canonical_json_bytes(value, allow_floats=allow_floats).decode("utf-8")


def canonical_sha256(value: object, *, allow_floats: bool = False) -> str:
    return hashlib.sha256(canonical_json_bytes(value, allow_floats=allow_floats)).hexdigest()


__all__ = [
    "CanonicalComposed",
    "CanonicalMemo",
    "CanonicalMemoized",
    "CanonicalSerializationError",
    "CanonicalValue",
    "DuplicateJsonFieldError",
    "canonical_json_bytes",
    "canonical_json_string",
    "canonical_sequence_cache_info",
    "canonical_sha256",
    "canonicalize",
    "is_canonically_immutable",
    "register_canonical_sequence",
    "unique_json_object",
]
