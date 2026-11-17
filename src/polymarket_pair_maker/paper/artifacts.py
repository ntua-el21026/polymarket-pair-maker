"""Bounded canonical artifacts with a separate completion manifest."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path
from typing import BinaryIO

from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonical_sha256


def fresh_output(path: Path, *, protected: tuple[Path, ...]) -> Path:
    if ".." in path.parts:
        raise ValueError("paper output cannot contain parent traversal")
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("paper output cannot contain a symlink")
    if absolute.exists() or not absolute.parent.is_dir():
        raise ValueError("paper output must be fresh below an existing directory")
    for source in protected:
        source = source.resolve()
        if absolute == source or absolute.is_relative_to(source) or source.is_relative_to(absolute):
            raise ValueError("paper output overlaps protected input evidence")
    return absolute


def runtime_identity() -> dict[str, object]:
    dependencies = sorted((str(item.metadata["Name"]), item.version) for item in distributions())
    return {
        "implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "gil_enabled": sys._is_gil_enabled(),  # pyright: ignore[reportPrivateUsage]
        "system": platform.system(),
        "machine": platform.machine(),
        "dependencies": dependencies,
    }


def source_identity() -> str:
    """Fingerprint installed package bytes as well as the explicitly recorded Git SHA."""
    package = Path(__file__).resolve().parents[1]
    files = sorted(
        path for path in package.rglob("*") if path.is_file() and path.suffix in {".py", ".json"}
    )
    return canonical_sha256(
        tuple(
            (path.relative_to(package).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in files
        )
    )


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    name: str
    bytes: int
    sha256: str


class PaperArtifacts:
    def __init__(self, destination: Path, *, maximum_bytes: int) -> None:
        self.destination = destination
        self.staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
        )
        self.maximum_bytes = maximum_bytes
        self.written = 0
        self._handles: dict[str, BinaryIO] = {}

    def append(self, name: str, value: object) -> None:
        self._write(name, canonical_json_bytes(value) + b"\n", append=True)

    def document(self, name: str, value: object) -> None:
        self._write(name, canonical_json_bytes(value) + b"\n", append=False)

    def _write(self, name: str, data: bytes, *, append: bool) -> None:
        if Path(name).name != name or not name.endswith((".json", ".jsonl")):
            raise ValueError("unsafe paper artifact name")
        if len(data) > 16_777_216 or self.written + len(data) > self.maximum_bytes:
            raise ValueError("paper artifact byte budget exhausted")
        handle = self._handles.get(name)
        if handle is None:
            handle = (self.staging / name).open("xb")
            self._handles[name] = handle
        elif not append:
            raise ValueError("paper document already written")
        handle.write(data)
        self.written += len(data)

    def inventory(self) -> tuple[ArtifactIdentity, ...]:
        for handle in self._handles.values():
            handle.flush()
        result: list[ArtifactIdentity] = []
        for path in sorted(self.staging.iterdir()):
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1_048_576):
                    hasher.update(chunk)
            result.append(ArtifactIdentity(path.name, path.stat().st_size, hasher.hexdigest()))
        return tuple(result)

    def publish(self) -> None:
        for handle in self._handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        self._handles.clear()
        if self.destination.exists() or self.destination.is_symlink():
            raise ValueError("paper destination appeared during execution")
        self.staging.rename(self.destination)

    def close_incomplete(self) -> None:
        """Keep the partial artifacts without creating a successful completion claim."""
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
