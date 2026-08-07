"""
test_divergence_monitor.py
============================
Prova as propriedades de segurança de `src/divergence_monitor.py`:

- Nunca julga com amostra pequena (min_trades_before_check).
- Deteta profit_factor ao vivo abaixo do mínimo absoluto,
  independentemente da baseline.
- Deteta win_rate ao vivo muito abaixo do validado.
- Aciona o kill-switch (cria o ficheiro) só quando há divergência real,
  nunca o remove sozinho, e nunca fecha posições existentes (o módulo
  não tem sequer acesso a posições — só cria um ficheiro).
"""

from __future__ import annotations

from pathlib import Path

from src.divergence_monitor import (
    DIVERGENCE_THRESHOLDS,
    check_divergence,
    compute_live_stats,
    enforce_kill_switch_if_diverging,
)
from src.trade_ledger import record_trade_close, record_trade_open


def _seed_trades(db_path: str, pnl_values: list[float]) -> None:
    """Semeia o ledger com trades fechados tendo exatamente os pnl_r
    dados — usa stop_distance_price_units=1.0 e entry_price_a=0.0 para
    que pnl_r == exit_price_a (direction=1), simplificando o teste."""
    for pnl in pnl_values:
        record_trade_open(db_path, "EURUSD", "AUDUSD", direction=1,
                           entry_price_a=0.0, stop_distance_price_units=1.0)
        record_trade_close(db_path, "EURUSD", "AUDUSD", exit_price_a=pnl, exit_reason="reversion")


# ---------------------------------------------------------------------
# compute_live_stats
# ---------------------------------------------------------------------

def test_compute_live_stats_none_when_no_trades(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    assert compute_live_stats(db_path, "EURUSD", "AUDUSD") is None


def test_compute_live_stats_basic_win_rate_and_profit_factor(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    _seed_trades(db_path, [1.0, 1.0, -1.0])  # 2 wins, 1 loss
    stats = compute_live_stats(db_path, "EURUSD", "AUDUSD")
    assert stats["total_trades"] == 3
    assert stats["win_rate"] == 2 / 3
    assert stats["profit_factor"] == 2.0  # gross_win=2, gross_loss=1


# ---------------------------------------------------------------------
# check_divergence
# ---------------------------------------------------------------------

def test_check_divergence_no_trigger_with_insufficient_sample():
    live_stats = {"total_trades": 3, "win_rate": 0.0, "profit_factor": 0.1, "total_return_r": -3.0}
    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    triggered, reason = check_divergence(live_stats, baseline)
    assert triggered is False
    assert reason is None


def test_check_divergence_none_live_stats_never_triggers():
    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    triggered, reason = check_divergence(None, baseline)
    assert triggered is False


def test_check_divergence_triggers_on_low_profit_factor():
    live_stats = {"total_trades": 15, "win_rate": 0.4, "profit_factor": 0.5, "total_return_r": -10.0}
    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    triggered, reason = check_divergence(live_stats, baseline)
    assert triggered is True
    assert "profit_factor" in reason


def test_check_divergence_triggers_on_low_win_rate_vs_baseline():
    live_stats = {"total_trades": 15, "win_rate": 0.2, "profit_factor": 1.5, "total_return_r": 2.0}
    baseline = {"win_rate": 0.7, "profit_factor": 2.0}  # 0.2 < 0.7*0.5=0.35 -> dispara
    triggered, reason = check_divergence(live_stats, baseline)
    assert triggered is True
    assert "win_rate" in reason


def test_check_divergence_no_trigger_when_performance_healthy():
    live_stats = {"total_trades": 15, "win_rate": 0.65, "profit_factor": 1.8, "total_return_r": 20.0}
    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    triggered, reason = check_divergence(live_stats, baseline)
    assert triggered is False


# ---------------------------------------------------------------------
# enforce_kill_switch_if_diverging
# ---------------------------------------------------------------------

def test_enforce_kill_switch_creates_file_when_diverging(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    kill_switch_path = str(tmp_path / "KILL_SWITCH.flag")
    _seed_trades(db_path, [-1.0] * 15)  # 15 perdas seguidas -> profit_factor 0.0

    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    triggered = enforce_kill_switch_if_diverging(db_path, "EURUSD", "AUDUSD", baseline, kill_switch_path)

    assert triggered is True
    assert Path(kill_switch_path).exists()
    content = Path(kill_switch_path).read_text(encoding="utf-8")
    assert "divergence_monitor" in content


def test_enforce_kill_switch_does_nothing_when_healthy(tmp_path):
    db_path = str(tmp_path / "live_trades.db")
    kill_switch_path = str(tmp_path / "KILL_SWITCH.flag")
    _seed_trades(db_path, [1.0] * 10 + [-0.3] * 5)  # bom desempenho

    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    triggered = enforce_kill_switch_if_diverging(db_path, "EURUSD", "AUDUSD", baseline, kill_switch_path)

    assert triggered is False
    assert not Path(kill_switch_path).exists()


def test_enforce_kill_switch_never_removes_existing_file(tmp_path):
    """Uma vez acionado, o kill-switch só é removido manualmente — este
    módulo nunca o apaga, mesmo que uma chamada seguinte não dispare
    divergência (ex.: já não há trades suficientes na janela)."""
    db_path = str(tmp_path / "live_trades.db")
    kill_switch_path = str(tmp_path / "KILL_SWITCH.flag")
    Path(kill_switch_path).write_text("já ativo por outro motivo", encoding="utf-8")

    baseline = {"win_rate": 0.7, "profit_factor": 2.0}
    enforce_kill_switch_if_diverging(db_path, "EURUSD", "AUDUSD", baseline, kill_switch_path)

    assert Path(kill_switch_path).exists()
    assert Path(kill_switch_path).read_text(encoding="utf-8") == "já ativo por outro motivo"
