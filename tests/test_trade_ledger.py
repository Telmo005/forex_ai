"""
test_trade_ledger.py
======================
Prova que `src/trade_ledger.py` regista aberturas/fechos corretamente,
calcula `pnl_r` como proxy de perna única de forma consistente, e nunca
confunde qual operação fechar quando há histórico de várias operações
para o mesmo par (sempre a mais recente ABERTA).
"""

from __future__ import annotations

import pytest

from src.trade_ledger import list_all_trades, list_closed_trades, record_trade_close, record_trade_open


def test_record_trade_open_then_close_computes_pnl_r_for_favorable_move(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                       entry_price_a=1.1000, stop_distance_price_units=0.0050)
    pnl_r = record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1050, exit_reason="reversion")
    assert pnl_r == pytest.approx(1.0)  # moveu exatamente 1x a distância de stop, a favor


def test_record_trade_close_negative_move_produces_negative_pnl_r(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                       entry_price_a=1.1000, stop_distance_price_units=0.0050)
    pnl_r = record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.0950, exit_reason="time_stop")
    assert pnl_r == pytest.approx(-1.0)


def test_record_trade_close_direction_minus_one_flips_sign(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    record_trade_open(db_path, "EURUSD", "AUDUSD", direction=-1,
                       entry_price_a=1.1000, stop_distance_price_units=0.0050)
    # preço subiu, mas direction=-1 (short spread) -> desfavorável -> pnl negativo
    pnl_r = record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1050, exit_reason="time_stop")
    assert pnl_r == pytest.approx(-1.0)


def test_record_trade_close_with_no_open_trade_returns_none(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    result = record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.10, exit_reason="reversion")
    assert result is None


def test_record_trade_close_uses_most_recent_open_trade(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                       entry_price_a=1.1000, stop_distance_price_units=0.0050)
    record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1050, exit_reason="reversion")

    record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                       entry_price_a=1.2000, stop_distance_price_units=0.0100)
    pnl_r = record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1900, exit_reason="time_stop")
    assert pnl_r == pytest.approx(-1.0)  # usa a 2ª operação (entry 1.2000), não a 1ª já fechada

    closed = list_closed_trades(db_path, "EURUSD", "AUDUSD")
    assert len(closed) == 2


def test_list_closed_trades_respects_limit_and_empty_db(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    assert list_closed_trades(db_path, "EURUSD", "AUDUSD") == []

    for i in range(5):
        record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                           entry_price_a=1.10, stop_distance_price_units=0.0050)
        record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1050, exit_reason="reversion")

    all_trades = list_closed_trades(db_path, "EURUSD", "AUDUSD")
    limited = list_closed_trades(db_path, "EURUSD", "AUDUSD", limit=2)
    assert len(all_trades) == 5
    assert len(limited) == 2


def test_list_all_trades_empty_db_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    assert list_all_trades(db_path) == []


def test_list_all_trades_includes_open_and_closed_across_pairs(tmp_path):
    db_path = str(tmp_path / "live_trades.db")

    record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                       entry_price_a=1.1000, stop_distance_price_units=0.0050)
    record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1050, exit_reason="reversion")
    record_trade_open(db_path, "USDJPY", "NZDUSD", direction=-1,
                       entry_price_a=150.00, stop_distance_price_units=0.50)

    trades = list_all_trades(db_path)
    assert len(trades) == 2
    statuses = {t["status"] for t in trades}
    assert statuses == {"closed", "open"}


def test_list_all_trades_respects_limit(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    for _ in range(4):
        record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                           entry_price_a=1.10, stop_distance_price_units=0.0050)
        record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=1.1050, exit_reason="reversion")

    assert len(list_all_trades(db_path)) == 4
    assert len(list_all_trades(db_path, limit=2)) == 2
