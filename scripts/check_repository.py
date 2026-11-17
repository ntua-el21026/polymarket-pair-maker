"""Check active documentation links, package identity and preserved fixture bytes."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def check_repository() -> tuple[str, ...]:
    failures: list[str] = []
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    if project["name"] != "polymarket-pair-maker":
        failures.append("distribution name differs from the migration contract")
    if project["scripts"] != {"ppm": "polymarket_pair_maker.cli.main:main"}:
        failures.append("CLI entry point differs from the migration contract")
    if (ROOT / "src/multi_market_arbitrage").exists():
        failures.append("obsolete import namespace remains")
    if (ROOT / "src/polymarket_pair_maker/scanners").exists():
        failures.append("obsolete taker scanner remains")
    manifest = cast(
        dict[str, object], json.loads((ROOT / "tests/golden/fixture-integrity.json").read_text())
    )
    records = cast(list[dict[str, object]], manifest["original_fixtures"])
    records = [*records, cast(dict[str, object], manifest["compatibility_fixture"])]
    for record in records:
        relative = str(record["path"])
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing preserved fixture: {relative}")
        elif (
            path.stat().st_size != record["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
        ):
            failures.append(f"preserved fixture changed: {relative}")
    paper_inventory = cast(
        dict[str, object],
        json.loads((ROOT / "tests/golden/paper-fixture-integrity.json").read_text()),
    )
    for record in cast(list[dict[str, object]], paper_inventory["files"]):
        relative = str(record["path"])
        path = ROOT / relative
        if not path.is_file() or (
            path.stat().st_size != record["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
        ):
            failures.append(f"synthetic paper fixture changed: {relative}")
    historical = cast(
        dict[str, object], json.loads((ROOT / "docs/historical/documents.json").read_text())
    )
    for record in cast(list[dict[str, object]], historical["files"]):
        relative = str(record["preserved_path"])
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing historical document: {relative}")
        elif (
            path.stat().st_size != record["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
        ):
            failures.append(f"historical document changed: {relative}")
    frozen_documents = {
        str(record["preserved_path"])
        for record in cast(list[dict[str, object]], historical["files"])
    }
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for document in documents:
        # Only the five verbatim bodies use original-commit link interpretation.
        if document.relative_to(ROOT).as_posix() in frozen_documents:
            continue
        for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text()):
            target = raw.split(' "', maxsplit=1)[0].strip("<>")
            if urlsplit(target).scheme or target.startswith("#"):
                continue
            target_path = unquote(target.split("#", maxsplit=1)[0])
            if target_path and not (document.parent / target_path).exists():
                failures.append(f"broken local link in {document.relative_to(ROOT)}: {target}")
    return tuple(failures)


def main() -> int:
    failures = check_repository()
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print(
        "Package identity, active links, 15 preserved fixtures, four synthetic fixtures "
        "and five historical documents verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
