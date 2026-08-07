"""
test_ml_model.py
==================
Prova as propriedades de segurança/correção de `src/ml_model.py`:

- `triple_barrier_labels` rotula corretamente TP/SL/barreira de tempo,
  e nunca inventa um rótulo para os últimos `max_bars` pontos (sem
  janela futura completa).
- `simulate_signal_pnl`/`compute_ml_stats` nunca contam um bar "flat"
  (pred==0) como trade, e o custo de spread é sempre subtraído.
- `train_walk_forward` corre ponta-a-ponta sobre um dataset sintético
  pequeno (fold refeito a cada iteração, nunca split aleatório).
- `train_final_model` + `predict_signal` fazem um ciclo completo
  treino -> guardar -> carregar -> prever sem erro.

Uso:
    python -m pytest tests/test_ml_model.py -q
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml_model import (
    FEATURE_COLUMNS,
    add_time_features,
    compute_ml_stats,
    predict_signal,
    simulate_signal_pnl,
    train_final_model,
    train_walk_forward,
    triple_barrier_labels,
)


# ---------------------------------------------------------------------
# triple_barrier_labels
# ---------------------------------------------------------------------

def test_triple_barrier_labels_detects_take_profit_first():
    prices = pd.Series([100.0, 100.05, 100.2, 100.05, 100.0])  # sobe 0.2% no bar 2
    labels = triple_barrier_labels(prices, tp_pct=0.0015, sl_pct=0.0015, max_bars=3)
    assert labels.iloc[0] == 1.0


def test_triple_barrier_labels_detects_stop_loss_first():
    prices = pd.Series([100.0, 99.95, 99.8, 99.95, 100.0])  # desce 0.2% no bar 2
    labels = triple_barrier_labels(prices, tp_pct=0.0015, sl_pct=0.0015, max_bars=3)
    assert labels.iloc[0] == -1.0


def test_triple_barrier_labels_time_barrier_when_neither_hit():
    prices = pd.Series([100.0, 100.02, 100.01, 100.02, 100.0])  # nunca sai de +-0.15%
    labels = triple_barrier_labels(prices, tp_pct=0.0015, sl_pct=0.0015, max_bars=3)
    assert labels.iloc[0] == 0.0


def test_triple_barrier_labels_last_max_bars_are_nan():
    prices = pd.Series(np.linspace(100.0, 101.0, 10))
    labels = triple_barrier_labels(prices, tp_pct=0.0015, sl_pct=0.0015, max_bars=4)
    assert labels.iloc[-4:].isna().all()
    assert labels.iloc[:-4].notna().all()


# ---------------------------------------------------------------------
# add_time_features
# ---------------------------------------------------------------------

def test_add_time_features_adds_hour_and_day_columns():
    idx = pd.date_range("2026-01-05", periods=5, freq="5min")  # 2026-01-05 é segunda-feira
    df = pd.DataFrame({"close": [1.0] * 5}, index=idx)
    out = add_time_features(df)
    assert "hour_of_day" in out.columns
    assert "day_of_week" in out.columns
    assert out["day_of_week"].iloc[0] == 0  # segunda-feira


# ---------------------------------------------------------------------
# simulate_signal_pnl / compute_ml_stats
# ---------------------------------------------------------------------

def test_simulate_signal_pnl_skips_flat_predictions():
    preds = pd.Series([0.0, 1.0, -1.0], index=[0, 1, 2])
    actual = pd.Series([1.0, 1.0, 1.0], index=[0, 1, 2])
    entry_prices = pd.Series([1.10, 1.10, 1.10], index=[0, 1, 2])
    pnl = simulate_signal_pnl(preds, actual, entry_prices, "EURUSD")
    assert len(pnl) == 2  # só os dois bars com pred != 0


def test_simulate_signal_pnl_correct_prediction_is_positive_net_of_cost():
    preds = pd.Series([1.0], index=[0])
    actual = pd.Series([1.0], index=[0])  # acertou a barreira TP
    entry_prices = pd.Series([1.10], index=[0])
    pnl = simulate_signal_pnl(preds, actual, entry_prices, "EURUSD")
    assert len(pnl) == 1
    assert 0.0 < pnl[0] < 1.0  # 1R bruto menos custo, ainda positivo mas < 1R


def test_simulate_signal_pnl_wrong_prediction_is_negative():
    preds = pd.Series([1.0], index=[0])
    actual = pd.Series([-1.0], index=[0])  # previu subida, bateu SL
    entry_prices = pd.Series([1.10], index=[0])
    pnl = simulate_signal_pnl(preds, actual, entry_prices, "EURUSD")
    assert pnl[0] < -1.0  # -1R bruto menos custo (custo agrava a perda)


def test_simulate_signal_pnl_time_barrier_is_near_zero_but_costs_apply():
    preds = pd.Series([1.0], index=[0])
    actual = pd.Series([0.0], index=[0])  # bateu barreira de tempo
    entry_prices = pd.Series([1.10], index=[0])
    pnl = simulate_signal_pnl(preds, actual, entry_prices, "EURUSD")
    assert pnl[0] < 0.0  # 0R bruto menos custo -> ligeiramente negativo
    assert pnl[0] > -0.5


def test_compute_ml_stats_accuracy_only_over_traded_bars():
    preds = pd.Series([0.0, 1.0, -1.0, 1.0])
    actual = pd.Series([1.0, 1.0, 1.0, -1.0])   # bar 0 (flat) ignorado; dos 3 negociados: 1 acerto (idx 1), 2 erros
    regimes = pd.Series([0, 0, 1, 1])
    stats = compute_ml_stats([0.5, -0.5, -0.5], preds, actual, regimes)
    assert stats["accuracy"] == pytest.approx(1 / 3)


def test_compute_ml_stats_regime_conditioned_accuracy():
    preds = pd.Series([1.0, 1.0, 1.0, 1.0])
    actual = pd.Series([1.0, 1.0, -1.0, -1.0])  # regime 0: 2/2 acertos; regime 1: 0/2
    regimes = pd.Series([0, 0, 1, 1])
    stats = compute_ml_stats([1.0, 1.0, -1.0, -1.0], preds, actual, regimes)
    assert stats["regime_accuracy"]["0"] == pytest.approx(1.0)
    assert stats["regime_accuracy"]["1"] == pytest.approx(0.0)


def test_compute_ml_stats_no_trades_returns_zeroed_baseline():
    preds = pd.Series([0.0, 0.0])
    actual = pd.Series([1.0, -1.0])
    regimes = pd.Series([0, 0])
    stats = compute_ml_stats([], preds, actual, regimes)
    assert stats["total_trades"] == 0
    assert stats["sharpe_per_trade"] == 0.0


# ---------------------------------------------------------------------
# train_walk_forward / train_final_model / predict_signal — ponta-a-ponta
# ---------------------------------------------------------------------

def _synthetic_features_dataset(n: int = 2000, seed: int = 7) -> pd.DataFrame:
    """Constrói um DataFrame no formato de `features_<SYMBOL>.parquet`
    (mesmas colunas produzidas por data_pipeline.engineer_features +
    detect_regime), com volatilidade suficiente para gerar as 3 classes
    do rótulo triple-barrier."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="5min")

    # vol calibrada empiricamente para produzir as 3 classes do rótulo
    # triple-barrier em proporções razoáveis (nem só TP/SL, nem só
    # barreira de tempo) — ver verificação em desenvolvimento deste teste
    log_ret = rng.normal(0, 0.0004, n)
    close = 1.10 * np.exp(np.cumsum(log_ret))

    df = pd.DataFrame({"close": close}, index=idx)
    df["log_ret"] = np.log(df["close"]).diff().fillna(0.0)
    df["vol_fast"] = df["log_ret"].rolling(20).std().bfill()
    df["vol_slow"] = df["log_ret"].rolling(100).std().bfill()
    df["vol_ratio"] = (df["vol_fast"] / df["vol_slow"]).fillna(1.0)
    df["zscore_price"] = ((df["close"] - df["close"].rolling(100).mean())
                           / df["close"].rolling(100).std()).fillna(0.0)
    df["range_pct"] = 0.0005
    df["regime"] = (rng.random(n) > 0.5).astype(int)
    return df


def test_train_walk_forward_runs_end_to_end(tmp_path):
    df = _synthetic_features_dataset(n=2000)
    features_path = tmp_path / "features_EURUSD.parquet"
    df.to_parquet(features_path)

    small_config = {"n_splits": 2, "max_train_size": 500, "gap": 20}
    result = train_walk_forward("EURUSD", str(features_path), config=small_config)

    assert result["symbol"] == "EURUSD"
    assert result["aggregate_stats"] is not None
    assert "accuracy" in result["aggregate_stats"]
    assert "regime_accuracy" in result["aggregate_stats"]


def test_train_final_model_and_predict_signal_round_trip(tmp_path):
    df = _synthetic_features_dataset(n=1000)
    features_path = tmp_path / "features_EURUSD.parquet"
    df.to_parquet(features_path)

    model_dir = tmp_path / "models"
    import src.ml_model as ml_model
    original_dir = ml_model.MODEL_OUTPUT_DIR
    ml_model.MODEL_OUTPUT_DIR = str(model_dir)
    try:
        model_path = train_final_model("EURUSD", str(features_path))
        assert (model_dir / "EURUSD.txt").exists()

        latest_row = add_time_features(df).iloc[-1]
        signal = predict_signal("EURUSD", latest_row, model_path=model_path,
                                 regime_accuracy={"0": 0.55, "1": 0.48})
        assert signal["symbol"] == "EURUSD"
        assert signal["direction"] in ("up", "down", "flat")
        assert 0.0 <= signal["probability"] <= 1.0
        assert signal["regime"] in (0, 1)
    finally:
        ml_model.MODEL_OUTPUT_DIR = original_dir
