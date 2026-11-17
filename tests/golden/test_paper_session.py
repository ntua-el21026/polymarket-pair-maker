"""Independent semantic golden; runtime and source identity are tested separately.

The expected cash is hand-derived: 2 YES shares at 0.40 cost 0.80; all remaining
buy reservations are cancelled with evidence. No fee or payout is invented.
The final invalid book values the unresolved tokens at explicit zero stress.
"""

import json
from pathlib import Path
from typing import cast

from polymarket_pair_maker.core.config import load_phase1_config
from polymarket_pair_maker.paper.config import load_paper_config
from polymarket_pair_maker.paper.demo import run_demo

REPO = Path(__file__).resolve().parents[2]
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


FIXTURE = Path(__file__).parent / "fixtures/paper_synthetic_session_v1.json"


def _amount(value: object) -> object:
    return _mapping(value)["value"]


def test_complete_synthetic_session_matches_independent_accounting_golden(tmp_path: Path):
    paper = load_paper_config(REPO / "config/paper.toml")
    phase = load_phase1_config(REPO / "config/phase1.toml")
    output = tmp_path / "synthetic"
    run_demo(paper, phase, output=output, code_commit=COMMIT)
    report = _json(output / "report.json")
    order_events = _rows(output / "orders.jsonl")
    ledger_rows = _rows(output / "ledger.jsonl")
    scenarios: dict[str, object] = {}
    for raw in _sequence(report["scenarios"]):
        scenario = _mapping(raw)
        identity = str(scenario["scenario"])
        ledger = _mapping(scenario["ledger"])
        assert scenario["outstanding_orders"] == []
        assert _amount(ledger["estimated_liquidity_rewards"]) == "0"
        assert _amount(ledger["estimated_maker_rebates"]) == "0"
        assert _amount(ledger["verified_payouts"]) == "0"
        assert scenario["verified_payments"] is None
        histories: dict[str, object] = {}
        for raw_order in _sequence(scenario["orders"]):
            order = _mapping(raw_order)
            intent = _mapping(order["intent"])
            assert order["state"] == "CANCELLED"
            assert _amount(intent["price"]) == "0.4"
            assert _amount(intent["quantity"]) == "5"
            rows = [
                row
                for row in order_events
                if _mapping(row["value"])["order_id"] == intent["order_id"]
            ]
            assert all(
                _mapping(row["value"])["classification"] == "SIMULATED_EXECUTION" for row in rows
            )
            histories[str(intent["outcome"])] = [
                [
                    row["ingest_seq"],
                    _mapping(row["value"])["kind"],
                    _amount(_mapping(row["value"])["quantity"]),
                ]
                for row in rows
            ]
        scenarios[identity] = {
            "cash": _amount(ledger["cash"]),
            "reserved": _amount(ledger["reserved_cash"]),
            "acquisition_basis": _amount(ledger["acquisition_basis"]),
            "realized_pnl": _amount(ledger["realized_trading_pnl"]),
            "liquidation_pnl": _amount(scenario["net_liquidation_pnl"]),
            "positions": [
                [
                    _mapping(position)["outcome"],
                    _amount(_mapping(position)["quantity"]),
                    _amount(_mapping(position)["acquisition_basis"]),
                ]
                for position in _sequence(ledger["positions"])
            ],
            "actions": scenario["decisions"],
            "fill_count": scenario["simulated_fill_count"],
            "histories": histories,
            "ledger_history": [
                [
                    row["ingest_seq"],
                    _amount(_mapping(row["value"])["cash"]),
                    _amount(_mapping(row["value"])["reserved_cash"]),
                    _amount(_mapping(row["value"])["acquisition_basis"]),
                ]
                for row in ledger_rows
                if row["scenario"] == identity
            ],
        }
    projection = {
        "schema_version": "ppm-paper-synthetic-golden-v1",
        "classification": report["classification"],
        "raw_record_count": report["raw_record_count"],
        "scenarios": scenarios,
    }
    assert projection == _json(FIXTURE)
    assert report["empirical_profitability"] == "UNVALIDATED"
    assert report["live_execution"] == "STRUCTURALLY_UNAVAILABLE"
    assert report["verified_payments"] is None
