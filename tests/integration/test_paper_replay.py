from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

import httpx
import pytest

from polymarket_pair_maker.cli.main import EXIT_CONFIGURATION, EXIT_DATA_INVALID, EXIT_OK, main
from polymarket_pair_maker.core.config import Phase1Config, load_phase1_config
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.journal import JournalCorruptionError, JournalReader
from polymarket_pair_maker.paper.artifacts import fresh_output
from polymarket_pair_maker.paper.config import PaperConfig, load_paper_config
from polymarket_pair_maker.paper.demo import demo_evidence, materialize_demo_source, run_demo
from polymarket_pair_maker.paper.replay import replay_paper
from polymarket_pair_maker.pipeline.processor import CanonicalProcessor
from polymarket_pair_maker.replay import replay_journal

REPO = Path(__file__).resolve().parents[2]
PHASE = REPO / "config/phase1.toml"
PAPER = REPO / "config/paper.toml"
COMMIT = "a" * 40


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _json(path: Path) -> dict[str, object]:
    return _mapping(json.loads(path.read_bytes()))


def _rows(path: Path) -> list[dict[str, object]]:
    return [_mapping(json.loads(line)) for line in path.read_bytes().splitlines()]


def _configs() -> tuple[PaperConfig, Phase1Config]:
    return load_paper_config(PAPER), load_phase1_config(PHASE)


def _snapshots(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


def _no_network(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("offline PAPER path attempted network access")


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(httpx.Client, "send", _no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", _no_network)


def test_cli_demo_twice_byte_identical_without_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    outputs = (tmp_path / "one", tmp_path / "two")
    for output in outputs:
        assert (
            main(
                [
                    "paper",
                    "demo",
                    "--config",
                    str(PHASE),
                    "--paper-config",
                    str(PAPER),
                    "--output",
                    str(output),
                ]
            )
            == EXIT_OK
        )
        response = _mapping(json.loads(capsys.readouterr().out))
        assert response["classification"] == "SYNTHETIC"
        assert response["raw_record_count"] == 8
    assert _snapshots(outputs[0]) == _snapshots(outputs[1])
    manifest = _json(outputs[0] / "session-manifest.json")
    for value in _sequence(manifest["artifacts"]):
        item = _mapping(value)
        name = str(item["name"])
        payload = (outputs[0] / name).read_bytes()
        assert len(payload) == item["bytes"]
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]


def test_public_journal_cli_replay_is_guarded_and_preserves_source_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    before = _snapshots(source)
    output = tmp_path / "replayed"
    assert (
        main(
            [
                "paper",
                "replay",
                str(source),
                "--run-id",
                run_id.value,
                "--config",
                str(PHASE),
                "--paper-config",
                str(PAPER),
                "--output",
                str(output),
            ]
        )
        == EXIT_OK
    )
    assert (
        _mapping(json.loads(capsys.readouterr().out))["classification"]
        == "OBSERVED_MARKET_DATA_WITH_SIMULATED_EXECUTION"
    )
    assert _snapshots(source) == before
    report = _json(output / "report.json")
    for value in _sequence(report["scenarios"]):
        scenario = _mapping(value)
        assert scenario["orders"] == []
        assert scenario["simulated_fill_count"] == 0
        assert "COLLECTOR_QUALIFICATION_UNAVAILABLE_OR_FAILED" in _mapping(
            scenario["abstention_reasons"]
        )
        assert _mapping(_mapping(scenario["ledger"])["cash"])["value"] == "1000"


def test_paper_replay_and_market_view_preserve_original_processor_terminal_hash(tmp_path: Path):
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    paper, phase = _configs()
    expected = replay_journal(source, run_id, phase)
    actual = replay_paper(source, run_id, phase, paper, output=tmp_path / "out", code_commit=COMMIT)
    assert actual["canonical_processor_terminal_hash"] == expected.processor.terminal_hash
    reader = JournalReader(source, run_id)
    processor = CanonicalProcessor(
        phase, source_journal_git_commit=reader.completion_heads()[0].git_commit
    )
    for envelope in reader.iter_verified():
        processor.process_replayed(envelope)
        before = processor.summary()
        book_hashes = tuple(book.content_sha256() for book in processor.snapshots())
        for bundle in processor.current_bundles:
            view = processor.market_view(
                bundle.market.market_id, observed_at=envelope.received_wall_time
            )
            assert view is not None
            assert view.bundle.market.times.observed_at <= envelope.received_wall_time
            assert view.bundle.precision.times.observed_at <= envelope.received_wall_time
        assert processor.summary() == before
        assert tuple(book.content_sha256() for book in processor.snapshots()) == book_hashes
    assert processor.summary().terminal_hash == expected.processor.terminal_hash


@pytest.mark.parametrize("mutation", ["corrupt", "incomplete", "completion_hash"])
def test_corrupt_or_incomplete_journal_blocks_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
):
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    if mutation == "corrupt":
        path = next(source.glob("*.zst"))
        payload = path.read_bytes()
        path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    elif mutation == "incomplete":
        next(source.glob("*.completed.json")).unlink()
    else:
        path = next(source.glob("*.completed.json"))
        payload = _json(path)
        payload["expected_event_count"] = 999
        path.write_text(json.dumps(payload))
    output = tmp_path / "blocked"
    status = main(
        [
            "paper",
            "replay",
            str(source),
            "--run-id",
            run_id.value,
            "--config",
            str(PHASE),
            "--paper-config",
            str(PAPER),
            "--output",
            str(output),
        ]
    )
    assert status in (EXIT_DATA_INVALID, EXIT_CONFIGURATION)
    assert _mapping(json.loads(capsys.readouterr().out))["status"] == "BLOCKED"
    assert not output.exists()
    assert not list(tmp_path.glob(".blocked-*/session-manifest.json"))


@pytest.mark.parametrize(
    "case", ["existing", "child", "traversal", "symlink", "symlink_parent", "absent_parent"]
)
def test_unsafe_output_paths_fail_before_overwrite(tmp_path: Path, case: str):
    source = tmp_path / "raw"
    source.mkdir()
    (source / "evidence").write_bytes(b"preserve me")
    if case == "existing":
        output = tmp_path / "existing"
        output.mkdir()
    elif case == "child":
        output = source / "derived"
    elif case == "traversal":
        output = tmp_path / "unused" / ".." / "derived"
    elif case == "symlink":
        output = tmp_path / "linked"
        output.symlink_to(source, target_is_directory=True)
    elif case == "symlink_parent":
        linked = tmp_path / "linked"
        linked.symlink_to(source, target_is_directory=True)
        output = linked / "derived"
    else:
        output = tmp_path / "missing" / "derived"
    before = _snapshots(source)
    with pytest.raises(ValueError, match=r"fresh|overlap|traversal|symlink"):
        fresh_output(output, protected=(source,))
    assert _snapshots(source) == before


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('starting_capital = "1000"', "starting_capital = 1000.0"),
        ("seed = 0", "seed = true"),
        ("seed = 0", "unexpected = 0"),
        ('starting_capital = "1000"', 'starting_capital = "1e3"'),
        ('scenario = "CONSERVATIVE"', 'scenario = "LIVE"'),
    ],
)
def test_paper_config_rejects_coercion_unknown_fields_and_live_scenario(
    tmp_path: Path, old: str, new: str
):
    path = tmp_path / "invalid.toml"
    path.write_text(PAPER.read_text().replace(old, new))
    with pytest.raises(
        (ValueError, TypeError), match=r"decimal|exact|unknown|invalid|QueueScenario"
    ):
        load_paper_config(path)


def test_future_economic_reference_abstains_without_lookahead(tmp_path: Path):
    paper, phase = _configs()
    evidence = demo_evidence()
    market = evidence.markets[0]
    assert market.fee_rules is not None
    future = UtcTimestamp.from_iso8601("2026-11-18T12:00:00Z")
    fee_evidence = replace(
        market.fee_rules.evidence,
        times=replace(market.fee_rules.evidence.times, observed_at=future),
    )
    future_market = replace(market, fee_rules=replace(market.fee_rules, evidence=fee_evidence))
    evidence = replace(evidence, markets=(future_market,))
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    output = tmp_path / "future"
    replay_paper(
        source,
        run_id,
        phase,
        paper,
        output=output,
        code_commit=COMMIT,
        evidence=evidence,
        synthetic=True,
    )
    report = _json(output / "report.json")
    for value in _sequence(report["scenarios"]):
        scenario = _mapping(value)
        assert scenario["orders"] == []
        assert any(
            "RULE_NOT_YET_OBSERVED" in reason for reason in _mapping(scenario["abstention_reasons"])
        )


@pytest.mark.parametrize("maximum_records", [4, 6])
def test_interrupted_replay_retains_reservations_and_does_not_fabricate_cancel(
    tmp_path: Path, maximum_records: int
):
    paper, phase = _configs()
    paper = replace(paper, maximum_input_records=maximum_records)
    output = tmp_path / "interrupted"
    with pytest.raises(ValueError, match="input record budget exhausted"):
        run_demo(paper, phase, output=output, code_commit=COMMIT)
    assert not output.exists()
    partial = next(tmp_path.glob(".interrupted-*"))
    assert not (partial / "session-manifest.json").exists()
    failure = _json(partial / "failure.json")
    assert failure["orders_cancelled_by_interruption"] is False
    assert failure["processed_records"] == maximum_records
    for value in _sequence(failure["scenario_ledgers"]):
        ledger = _mapping(value)
        assert _mapping(ledger["reserved_cash"])["value"] in {"4", "3.6"}
        assert len(_sequence(ledger["reservations"])) == 2
    assert all(
        _mapping(row["value"])["kind"] not in {"CANCEL_ACK", "EXPIRE"}
        for row in _rows(partial / "orders.jsonl")
    )


def test_config_and_reference_fingerprints_change_result_identity(tmp_path: Path):
    paper, phase = _configs()
    first = run_demo(paper, phase, output=tmp_path / "first", code_commit=COMMIT)
    second = run_demo(
        replace(paper, execution=replace(paper.execution, seed=1)),
        phase,
        output=tmp_path / "second",
        code_commit=COMMIT,
    )
    assert first["terminal_result_hash"] != second["terminal_result_hash"]
    first_manifest = _json(tmp_path / "first/session-manifest.json")
    second_manifest = _json(tmp_path / "second/session-manifest.json")
    assert first_manifest["paper_config_hash"] != second_manifest["paper_config_hash"]
    assert first_manifest["raw_completion_sha256"] == second_manifest["raw_completion_sha256"]
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    evidence = demo_evidence()
    changed_market = replace(
        evidence.markets[0],
        risk_evidence=replace(evidence.markets[0].risk_evidence, version="synthetic-risk-v2"),
    )
    changed = replace(evidence, markets=(changed_market,))
    replay_paper(
        source,
        run_id,
        phase,
        paper,
        output=tmp_path / "changed",
        code_commit=COMMIT,
        evidence=changed,
        synthetic=True,
    )
    changed_manifest = _json(tmp_path / "changed/session-manifest.json")
    assert (
        changed_manifest["economic_evidence_sha256"] != first_manifest["economic_evidence_sha256"]
    )
    assert changed_manifest["terminal_result_hash"] != first_manifest["terminal_result_hash"]


def test_evidence_cannot_cross_raw_run_identity_or_observed_synthetic_boundary(tmp_path: Path):
    paper, phase = _configs()
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    with pytest.raises(ValueError, match="bound to this raw run"):
        replay_paper(
            source,
            run_id,
            phase,
            paper,
            output=tmp_path / "bad",
            code_commit=COMMIT,
            evidence=replace(demo_evidence(), raw_completion_sha256="f" * 64),
            synthetic=True,
        )
    with pytest.raises(ValueError, match="fixed synthetic demo"):
        replay_paper(
            source,
            run_id,
            phase,
            paper,
            output=tmp_path / "observed",
            code_commit=COMMIT,
            evidence=demo_evidence(),
        )


def test_raw_source_hash_changes_are_rejected_not_repaired(tmp_path: Path):
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    before = canonical_sha256(_snapshots(source))
    completion = next(source.glob("*.completed.json"))
    payload = _json(completion)
    payload["completion_sha256"] = "f" * 64
    completion.write_text(json.dumps(payload))
    after = canonical_sha256(_snapshots(source))
    assert before != after
    with pytest.raises(JournalCorruptionError, match=r"hash|SHA|sha"):
        JournalReader(source, run_id).completion_heads()
    assert canonical_sha256(_snapshots(source)) == after


def test_delayed_execution_preflight_preserves_reservations_in_failed_session(tmp_path: Path):
    paper, phase = _configs()
    evidence = demo_evidence()
    first, second = evidence.pressures
    delayed = replace(
        second,
        ingest_seq=7,
        pressure=replace(
            second.pressure,
            occurred_at=UtcTimestamp.from_iso8601("2026-11-17T12:00:00.500Z"),
            available_at=UtcTimestamp.from_iso8601("2026-11-17T12:00:00.600Z"),
        ),
    )
    source = tmp_path / "raw"
    run_id = materialize_demo_source(source)
    with pytest.raises(ValueError, match="unavailable reconciliation"):
        replay_paper(
            source,
            run_id,
            phase,
            paper,
            output=tmp_path / "delayed",
            code_commit=COMMIT,
            evidence=replace(evidence, pressures=(first, delayed)),
            synthetic=True,
        )
    staging = next(tmp_path.glob(".delayed-*"))
    failure = _json(staging / "failure.json")
    assert failure["orders_cancelled_by_interruption"] is False
    assert all(
        _mapping(_mapping(item)["reserved_cash"])["value"] in {"4", "3.6"}
        for item in _sequence(failure["scenario_ledgers"])
    )
    assert all(
        _mapping(row["value"])["kind"] != "CANCEL_ACK" for row in _rows(staging / "orders.jsonl")
    )
