"""Independent corruptions of normalized completed paper evidence fail closed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from polymarket_pair_maker.core.config import load_phase1_config
from polymarket_pair_maker.core.serialization import canonical_json_bytes, canonical_sha256
from polymarket_pair_maker.paper import verification
from polymarket_pair_maker.paper.config import load_paper_config
from polymarket_pair_maker.paper.demo import run_demo
from polymarket_pair_maker.paper.verification import (
    iter_reconstructed_records,
    verify_paper_artifacts,
)
from polymarket_pair_maker.strategy.engine import (
    CandidateEvaluation,
    MarketRanking,
    StrategyDecision,
)

REPO = Path(__file__).parents[2]


@pytest.fixture
def completed(tmp_path: Path) -> Path:
    output = tmp_path / "complete"
    run_demo(
        load_paper_config(REPO / "config/paper.toml"),
        load_phase1_config(REPO / "config/phase1.toml"),
        output=output,
        code_commit="a" * 40,
    )
    return output


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_bytes()))


def _rewrite(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _refresh_completion(root: Path) -> None:
    """Rehash the outer container, leaving inner normalization invariants to verifier."""
    manifest = _load(root / "session-manifest.json")
    report = _load(root / "report.json")
    inventory: list[dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        if path.name in {"session-manifest.json", "report.json"}:
            continue
        raw = path.read_bytes()
        inventory.append(
            {"name": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    base = {
        key: value
        for key, value in manifest.items()
        if key not in {"terminal_result_hash", "status", "artifacts"}
    }
    report.pop("terminal_result_hash")
    terminal = canonical_sha256((base, inventory, report))
    report["terminal_result_hash"] = terminal
    _rewrite(root / "report.json", report)
    raw = (root / "report.json").read_bytes()
    inventory.append(
        {"name": "report.json", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    )
    manifest["artifacts"] = sorted(inventory, key=lambda row: row["name"])
    manifest["terminal_result_hash"] = terminal
    _rewrite(root / "session-manifest.json", manifest)


def test_completed_normalization_reconstructs_typed_economics(completed: Path):
    verified = verify_paper_artifacts(completed)
    assert verified.schema_version == "ppm-paper-session-v2"
    assert verified.raw_record_count == 8
    assert verified.scenario_state_chains_verified
    records = list(iter_reconstructed_records(completed))
    decisions = [value for name, _scenario, _seq, value in records if name == "decisions.jsonl"]
    rankings = [value for name, _scenario, _seq, value in records if name == "selection.jsonl"]
    grids = [value for name, _scenario, _seq, value in records if name == "economics.jsonl"]
    assert decisions
    assert all(isinstance(value, StrategyDecision) for value in decisions)
    assert rankings
    assert all(isinstance(value, MarketRanking) for value in rankings)
    assert grids
    assert all(
        isinstance(value, tuple)
        and all(isinstance(item, CandidateEvaluation) for item in cast(tuple[object, ...], value))
        for value in grids
    )
    assert any(len(cast(tuple[object, ...], value)) == 49 for value in grids)


@pytest.mark.parametrize(
    "case",
    [
        "missing_file",
        "extra_file",
        "wrong_inventory_bytes",
        "wrong_inventory_hash",
        "duplicate_inventory",
        "invalid_manifest_version",
        "invalid_report_version",
        "truncated_file",
        "truncated_record",
        "duplicate_json_key",
        "invalid_row_version",
        "cross_scenario",
        "incorrect_sequence",
        "incorrect_record_index",
        "modified_economics",
        "modified_candidate_identity",
        "missing_evaluation_ref",
        "selected_decision_ref",
        "duplicate_conflicting_identity",
        "cross_market",
        "state_chain",
        "unexpected_row_field",
        "configuration_hash",
        "completed_with_parse_failure",
    ],
)
def test_corrupted_normalized_evidence_fails_closed(completed: Path, case: str):
    manifest_path = completed / "session-manifest.json"
    economics = completed / "economics.jsonl"
    decisions = completed / "decisions.jsonl"
    selection = completed / "selection.jsonl"
    if case == "missing_file":
        economics.unlink()
    elif case == "extra_file":
        (completed / "unmanifested.jsonl").write_bytes(b"{}\n")
    elif case in {"wrong_inventory_bytes", "wrong_inventory_hash", "duplicate_inventory"}:
        manifest = _load(manifest_path)
        if case == "wrong_inventory_bytes":
            manifest["artifacts"][0]["bytes"] += 1
        elif case == "wrong_inventory_hash":
            manifest["artifacts"][0]["sha256"] = "0" * 64
        else:
            manifest["artifacts"].append(manifest["artifacts"][0])
        _rewrite(manifest_path, manifest)
    elif case == "invalid_manifest_version":
        manifest = _load(manifest_path)
        manifest["schema_version"] = "ppm-paper-session-v999"
        _rewrite(manifest_path, manifest)
    elif case == "configuration_hash":
        manifest = _load(manifest_path)
        manifest["paper_configuration"]["execution"]["seed"] = 99
        _rewrite(manifest_path, manifest)
        _refresh_completion(completed)
    elif case in {"invalid_report_version", "state_chain", "completed_with_parse_failure"}:
        report = _load(completed / "report.json")
        if case == "completed_with_parse_failure":
            report["canonical_parse_failures"] = 1
        elif case == "state_chain":
            report["scenarios"][0]["state_chain_sha256"] = "0" * 64
        else:
            report["schema_version"] = "ppm-paper-report-v999"
        _rewrite(completed / "report.json", report)
        _refresh_completion(completed)
    elif case in {"truncated_file", "truncated_record", "duplicate_json_key"}:
        raw = economics.read_bytes()
        if case == "truncated_file":
            raw = raw[:-1]
        elif case == "truncated_record":
            raw = raw[:100] + b"\n"
        else:
            raw = b'{"scenario":"STRICT_PESSIMISTIC",' + raw[1:]
        economics.write_bytes(raw)
        _refresh_completion(completed)
    else:
        path = selection if case == "selected_decision_ref" else decisions
        if case in {"modified_economics", "modified_candidate_identity", "cross_market"}:
            path = completed / "evaluation-payloads.jsonl"
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        row = (
            next(item for item in rows if item["value"]["evaluations"])
            if case in {"modified_economics", "modified_candidate_identity"}
            else rows[-1]
        )
        value = row["value"]
        if case == "invalid_row_version":
            value["schema_version"] = "unsupported"
        elif case == "cross_scenario":
            value["scenario"] = (
                "CONSERVATIVE" if row["scenario"] != "CONSERVATIVE" else "SENSITIVITY"
            )
        elif case == "incorrect_sequence":
            row["ingest_seq"] += 1
        elif case == "incorrect_record_index":
            row["record_index"] += 1
        elif case == "unexpected_row_field":
            row["extra"] = "invalid"
        elif case == "cross_market":
            value["market_id"] = "f" * 64
        elif case == "modified_economics":
            value["evaluations"][0]["conservative_net"]["value"] = "999"
        elif case == "modified_candidate_identity":
            value["evaluations"][0]["candidate"]["identity"] = "e" * 64
        elif case == "missing_evaluation_ref":
            value["evaluation_set_id"] = "d" * 64
        elif case == "selected_decision_ref":
            value["selected_decision_id"] = "d" * 64
        elif case == "duplicate_conflicting_identity":
            row["value"]["reasons"] = ["FORGED"]
            rows.insert(-1, row.copy())
        path.write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in rows))
        _refresh_completion(completed)
    with pytest.raises((ValueError, TypeError)):
        verify_paper_artifacts(completed)


def test_legacy_completed_schema_retains_explicit_compatibility_boundary(completed: Path):
    records = list(iter_reconstructed_records(completed))
    streams: dict[str, list[dict[str, object]]] = {}
    for name, scenario, seq, value in records:
        if name == "evaluation-payloads.jsonl":
            continue
        streams.setdefault(name, []).append(
            {"scenario": scenario, "ingest_seq": seq, "value": value}
        )
    (completed / "evaluation-payloads.jsonl").unlink()
    for name, rows in streams.items():
        (completed / name).write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))
    manifest = _load(completed / "session-manifest.json")
    manifest["schema_version"] = "ppm-paper-session-v1"
    manifest.pop("artifact_schema_version")
    manifest.pop("state_chain_version")
    _rewrite(completed / "session-manifest.json", manifest)
    report = _load(completed / "report.json")
    report["schema_version"] = "ppm-paper-report-v1"
    _rewrite(completed / "report.json", report)
    _refresh_completion(completed)
    verified = verify_paper_artifacts(completed)
    assert verified.schema_version == "ppm-paper-session-v1"
    assert not verified.scenario_state_chains_verified
    legacy = list(iter_reconstructed_records(completed))
    original = sorted(
        (name, scenario.value, seq, canonical_sha256(value))
        for name, scenario, seq, value in records
        if name != "evaluation-payloads.jsonl"
    )
    rebuilt = sorted(
        (name, scenario.value, seq, canonical_sha256(value))
        for name, scenario, seq, value in legacy
    )
    assert rebuilt == original


def test_missing_completion_or_symlink_cannot_qualify(tmp_path: Path, completed: Path):
    (completed / "session-manifest.json").unlink()
    with pytest.raises(ValueError, match="regular file"):
        verify_paper_artifacts(completed)
    linked = tmp_path / "linked"
    linked.symlink_to(completed, target_is_directory=True)
    with pytest.raises(ValueError, match="regular directory"):
        verify_paper_artifacts(linked)


@pytest.mark.parametrize("artifact", ["report.json", "session-manifest.json", "extra.jsonl"])
def test_changes_during_verification_fail_closed(
    completed: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
):
    original = verification._normalized_records  # pyright: ignore[reportPrivateUsage]

    def changed(
        root: Path,
        container: verification._Container,  # pyright: ignore[reportPrivateUsage]
    ) -> Iterator[verification.LogicalRecord]:
        yield from original(root, container)
        target = root / artifact
        target.write_bytes(target.read_bytes() + b" " if target.exists() else b"{}\n")

    monkeypatch.setattr(verification, "_normalized_records", changed)
    with pytest.raises(ValueError, match="changed during verification"):
        verify_paper_artifacts(completed)
