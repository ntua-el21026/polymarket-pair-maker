"""The paper artifact contract is versioned independently of economic policy."""

import json
from pathlib import Path

import pytest

from polymarket_pair_maker.core.config import load_phase1_config
from polymarket_pair_maker.paper.config import load_paper_config
from polymarket_pair_maker.paper.demo import run_demo

REPO = Path(__file__).resolve().parents[2]


def test_published_session_uses_normalized_versioned_streams(tmp_path: Path):
    output = tmp_path / "normalized"
    run_demo(
        load_paper_config(REPO / "config/paper.toml"),
        load_phase1_config(REPO / "config/phase1.toml"),
        output=output,
        code_commit="a" * 40,
    )
    manifest = json.loads((output / "session-manifest.json").read_bytes())
    assert manifest["schema_version"] == "ppm-paper-session-v2"
    assert manifest["artifact_schema_version"] == "ppm-paper-artifacts-v2"
    assert manifest["state_chain_version"] == "ppm-paper-state-chain-v2"
    for name in ("decisions.jsonl", "selection.jsonl"):
        rows = [json.loads(line) for line in (output / name).read_bytes().splitlines()]
        assert rows
        assert all("evaluations" not in row["value"] for row in rows)
        assert all(type(row["record_index"]) is int for row in rows)


def test_failed_completion_verification_cannot_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A completion record alone cannot bypass relational verification.
    from polymarket_pair_maker.paper import verification

    def reject(_path: Path):
        raise ValueError("independent completion rejection")

    monkeypatch.setattr(verification, "verify_paper_artifacts", reject)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="independent completion rejection"):
        run_demo(
            load_paper_config(REPO / "config/paper.toml"),
            load_phase1_config(REPO / "config/phase1.toml"),
            output=output,
            code_commit="a" * 40,
        )
    assert not output.exists()
    partial = next(tmp_path.glob(".rejected-*"))
    assert json.loads((partial / "failure.json").read_bytes())["status"] == "INCOMPLETE"
    assert not (partial / "session-manifest.json").exists()
