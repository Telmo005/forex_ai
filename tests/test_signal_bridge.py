"""
test_signal_bridge.py
=======================
Prova que `src/signal_bridge.py` escreve exatamente os eventos
acionáveis (close_hedge sempre; open_hedge só quando
`risk_decision.approved`) num ficheiro JSON Lines plano, e nunca um
`RiskDecision.size_lots` sob a chave "lots" (só "risk_fraction" — ver
docstring do módulo sobre a distinção RISK-07).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.risk_engine import RiskDecision
from src.signal_bridge import write_heartbeat, write_signal_file


def _open_event(approved: bool) -> dict:
    return {
        "bar_index": 10, "pair_a": "EURUSD", "pair_b": "GBPUSD",
        "proposal": {"action": "open_hedge", "pair_a": "EURUSD", "pair_b": "GBPUSD",
                     "direction": 1, "trigger": "spread_zscore", "trigger_value": 2.1},
        "risk_decision": RiskDecision(approved=approved, size_lots=0.0125 if approved else 0.0,
                                       sl_price=1.0950, reject_reason=None if approved else "kill_switch"),
        "hedge_ratio": 1.057,
        "entry_price_a": 1.1000,
        "stop_distance_price_units": 0.0050,
        "new_position_exposure_pct": 0.0125,
        "aggregate_exposure_pct_after": 0.041,
    }


def _close_event() -> dict:
    return {
        "bar_index": 20, "pair_a": "EURUSD", "pair_b": "GBPUSD",
        "proposal": {"action": "close_hedge", "pair_a": "EURUSD", "pair_b": "GBPUSD",
                     "trigger": "reversion", "trigger_value": 0.1},
        "risk_decision": None,
    }


def test_write_signal_file_includes_approved_open_and_all_closes(tmp_path):
    path = str(tmp_path / "signal_queue.jsonl")
    n_written = write_signal_file([_open_event(approved=True), _close_event()], path)
    assert n_written == 2

    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["action"] == "open_hedge"
    assert records[0]["direction"] == 1
    assert records[0]["hedge_ratio"] == 1.057
    assert records[0]["risk_fraction"] == 0.0125
    assert records[0]["new_position_exposure_pct"] == 0.0125
    assert records[0]["aggregate_exposure_pct_after"] == 0.041
    assert "lots" not in records[0]
    assert records[1]["action"] == "close_hedge"
    assert records[1]["trigger"] == "reversion"


def test_write_signal_file_excludes_rejected_open_proposal(tmp_path):
    path = str(tmp_path / "signal_queue.jsonl")
    n_written = write_signal_file([_open_event(approved=False)], path)
    assert n_written == 0
    assert Path(path).read_text(encoding="utf-8") == ""


def test_write_signal_file_overwrites_previous_content(tmp_path):
    path = str(tmp_path / "signal_queue.jsonl")
    write_signal_file([_close_event(), _close_event()], path)
    assert len(Path(path).read_text(encoding="utf-8").strip().splitlines()) == 2

    write_signal_file([_close_event()], path)
    assert len(Path(path).read_text(encoding="utf-8").strip().splitlines()) == 1


def test_write_heartbeat_creates_readable_iso_timestamp(tmp_path):
    path = str(tmp_path / "heartbeat.flag")
    write_heartbeat(path)
    content = Path(path).read_text(encoding="utf-8")
    assert "T" in content  # formato ISO 8601


def test_write_signal_file_creates_parent_directories(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "signal_queue.jsonl")
    write_signal_file([_close_event()], path)
    assert Path(path).exists()
