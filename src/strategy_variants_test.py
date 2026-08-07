"""
strategy_variants_test.py
============================
Testes runnable-em-qualquer-lugar (sem MT5, sem output/) para as novas
famílias de estratégia em strategy_variants.py — mesmo padrão de
backtest_engine_test.py: séries pandas sintéticas em memória, imports
bare (script corrido a partir de src/, ver CLAUDE.md "Import
Organization").

Foco: (1) nenhuma variante introduz look-ahead bias (beta de Kalman e
percentil de volatilidade só podem depender do passado), (2) cada
variante produz trades/stats válidos e cost-aware, (3) o despacho por
strategy_type nunca diverge entre run_backtest_for_type() e chamar a
função da variante diretamente.

Uso:
    python -m pytest src/strategy_variants_test.py -x -q
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest_engine import run_hedge_backtest
from strategy_variants import (
    PARAM_RANGES_BY_TYPE,
    _rolling_vol_percentile,
    compute_signal_series_for_type,
    kalman_hedge_ratio,
    run_backtest_for_type,
    run_hedge_backtest_asymmetric_bands,
    run_hedge_backtest_kalman,
    run_hedge_backtest_vol_scaled_exit,
)


def _synthetic_pair(n: int = 2000, seed: int = 7) -> tuple[pd.Series, pd.Series]:
    """Mesma construção de backtest_engine_test.py::_synthetic_pair —
    spread mean-reverting por construção, para produzir trades
    reprodutíveis em qualquer variante."""
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 0.0005, n)) + 1.1000
    noise_a = rng.normal(0, 0.0002, n)
    noise_b = rng.normal(0, 0.0002, n)
    mean_revert = np.zeros(n)
    for i in range(1, n):
        mean_revert[i] = mean_revert[i - 1] * 0.9 + rng.normal(0, 0.0008)
    price_a = pd.Series(common + noise_a + mean_revert)
    price_b = pd.Series(common + noise_b)
    return price_a, price_b


KALMAN_PARAMS = {
    "entry_threshold": 1.5, "exit_threshold": 0.3, "min_correlation": 0.4,
    "max_hold_bars": 100, "corr_window": 150, "kalman_delta": 1e-4, "kalman_ve": 1e-3,
}

VOL_SCALED_EXIT_PARAMS = {
    "entry_threshold": 1.5, "exit_threshold": 0.3, "min_correlation": 0.4,
    "max_hold_bars": 100, "beta_window": 200, "corr_window": 150, "recalc_every": 50,
    "vol_lookback": 80, "vol_scale_min": 0.5, "vol_scale_max": 2.0,
}

ASYMMETRIC_PARAMS = {
    "entry_threshold_long": 1.5, "entry_threshold_short": 1.5, "exit_threshold": 0.3,
    "min_correlation": 0.4, "max_hold_bars": 100, "beta_window": 200,
    "corr_window": 150, "recalc_every": 50,
}

COST_PARAMS = {
    "spread_cost": 0.0005, "slippage_cost": 0.0002,
    "commission_per_lot": 5.0, "reference_lot_size": 1.0,
}


# ---------------------------------------------------------------------
# Causalidade (sem look-ahead bias)
# ---------------------------------------------------------------------

def test_kalman_hedge_ratio_is_causal():
    price_a, price_b = _synthetic_pair(n=500)
    full_beta = kalman_hedge_ratio(price_a, price_b)
    truncated_beta = kalman_hedge_ratio(price_a.iloc[:300], price_b.iloc[:300])
    # beta[t] para t < 300 não pode mudar só porque a série completa tem
    # mais 200 barras no futuro — prova de que o filtro é online/causal.
    assert np.allclose(full_beta[:300], truncated_beta, atol=1e-12)


def test_rolling_vol_percentile_is_causal():
    price, _ = _synthetic_pair(n=500)
    full_pct = _rolling_vol_percentile(price, lookback=50)
    truncated_pct = _rolling_vol_percentile(price.iloc[:300], lookback=50)
    valid = ~np.isnan(full_pct[:300]) & ~np.isnan(truncated_pct)
    assert valid.sum() > 0
    assert np.allclose(full_pct[:300][valid], truncated_pct[valid], atol=1e-9)


# ---------------------------------------------------------------------
# Variante Kalman: forma válida + custos reduzem o retorno
# ---------------------------------------------------------------------

def test_run_hedge_backtest_kalman_produces_valid_trades_and_stats():
    price_a, price_b = _synthetic_pair()
    result = run_hedge_backtest_kalman(price_a, price_b, KALMAN_PARAMS, cost_params=COST_PARAMS)
    assert "trades" in result and "stats" in result
    stats = result["stats"]
    assert stats["total_trades"] == len(result["trades"])
    for t in result["trades"]:
        assert t["direction"] in (1, -1)
        assert t["exit_reason"] in ("reversion", "correlation_breakdown", "time_stop")
        assert t["exit_bar"] > t["entry_bar"]


def test_run_hedge_backtest_kalman_with_costs_yields_lower_return_than_without():
    price_a, price_b = _synthetic_pair()
    no_cost = run_hedge_backtest_kalman(price_a, price_b, KALMAN_PARAMS, cost_params=None)
    with_cost = run_hedge_backtest_kalman(price_a, price_b, KALMAN_PARAMS, cost_params=COST_PARAMS)
    assert len(no_cost["trades"]) > 0
    assert with_cost["stats"]["total_return_r"] < no_cost["stats"]["total_return_r"]


# ---------------------------------------------------------------------
# Variante vol-scaled-exit: forma válida + stop de tempo respeita a escala
# ---------------------------------------------------------------------

def test_run_hedge_backtest_vol_scaled_exit_produces_valid_trades_and_stats():
    price_a, price_b = _synthetic_pair()
    result = run_hedge_backtest_vol_scaled_exit(
        price_a, price_b, VOL_SCALED_EXIT_PARAMS, cost_params=COST_PARAMS,
    )
    stats = result["stats"]
    assert stats["total_trades"] == len(result["trades"])

    max_hold_bars = VOL_SCALED_EXIT_PARAMS["max_hold_bars"]
    vol_scale_max = VOL_SCALED_EXIT_PARAMS["vol_scale_max"]
    upper_bound = int(round(max_hold_bars * vol_scale_max)) + 1
    for t in result["trades"]:
        if t["exit_reason"] == "time_stop":
            assert t["bars_held"] <= upper_bound


# ---------------------------------------------------------------------
# Variante asymmetric-bands: limiares distintos por lado são respeitados
# ---------------------------------------------------------------------

def test_run_hedge_backtest_asymmetric_bands_blocks_side_with_unreachable_threshold():
    price_a, price_b = _synthetic_pair()
    params = {**ASYMMETRIC_PARAMS, "entry_threshold_short": 50.0, "entry_threshold_long": 1.0}
    result = run_hedge_backtest_asymmetric_bands(price_a, price_b, params, cost_params=COST_PARAMS)
    assert len(result["trades"]) > 0
    # entry_threshold_short inatingível (50.0) -> nunca deve abrir o lado
    # short (direction=-1), só o lado long (direction=1).
    assert all(t["direction"] == 1 for t in result["trades"])


# ---------------------------------------------------------------------
# Despacho por strategy_type
# ---------------------------------------------------------------------

def test_run_backtest_for_type_zscore_matches_run_hedge_backtest_directly():
    price_a, price_b = _synthetic_pair()
    zscore_params = {
        "entry_threshold": 1.5, "exit_threshold": 0.3, "min_correlation": 0.4,
        "max_hold_bars": 100, "beta_window": 200, "corr_window": 150, "recalc_every": 50,
    }
    via_dispatch = run_backtest_for_type("zscore", price_a, price_b, zscore_params, cost_params=COST_PARAMS)
    direct = run_hedge_backtest(price_a, price_b, zscore_params, cost_params=COST_PARAMS)
    assert via_dispatch["trades"] == direct["trades"]
    assert via_dispatch["stats"] == direct["stats"]


def test_run_backtest_for_type_kalman_matches_variant_function_directly():
    price_a, price_b = _synthetic_pair()
    via_dispatch = run_backtest_for_type("kalman", price_a, price_b, KALMAN_PARAMS, cost_params=COST_PARAMS)
    direct = run_hedge_backtest_kalman(price_a, price_b, KALMAN_PARAMS, cost_params=COST_PARAMS)
    assert via_dispatch["trades"] == direct["trades"]


def test_run_backtest_for_type_unknown_type_raises():
    price_a, price_b = _synthetic_pair(n=50)
    with pytest.raises(ValueError):
        run_backtest_for_type("nao_existe", price_a, price_b, {})


def test_all_declared_strategy_types_have_param_ranges():
    from strategy_variants import STRATEGY_TYPES
    for strategy_type in STRATEGY_TYPES:
        assert strategy_type in PARAM_RANGES_BY_TYPE
        assert len(PARAM_RANGES_BY_TYPE[strategy_type]) > 0


# ---------------------------------------------------------------------
# compute_signal_series_for_type (visualização, dashboard.py)
# ---------------------------------------------------------------------

def test_compute_signal_series_for_type_zscore_matches_backtest_internals():
    price_a, price_b = _synthetic_pair()
    zscore_params = {
        "entry_threshold": 1.5, "exit_threshold": 0.3, "min_correlation": 0.4,
        "max_hold_bars": 100, "beta_window": 200, "corr_window": 150, "recalc_every": 50,
    }
    series = compute_signal_series_for_type("zscore", price_a, price_b, zscore_params)
    assert set(series) == {"zscore", "spread", "correlation"}
    assert len(series["zscore"]) == len(price_a)

    from backtest_engine import compute_rolling_beta, compute_zscore
    beta = compute_rolling_beta(price_a, price_b, 200, 50)
    expected_spread = (price_a - beta * price_b).values
    assert np.allclose(series["spread"], expected_spread, equal_nan=True)


def test_compute_signal_series_for_type_kalman_matches_backtest_internals():
    price_a, price_b = _synthetic_pair()
    series = compute_signal_series_for_type("kalman", price_a, price_b, KALMAN_PARAMS)
    assert len(series["spread"]) == len(price_a)

    beta = kalman_hedge_ratio(price_a, price_b,
                               delta=KALMAN_PARAMS["kalman_delta"], ve=KALMAN_PARAMS["kalman_ve"])
    expected_spread = price_a.values - beta * price_b.values
    assert np.allclose(series["spread"], expected_spread, equal_nan=True)


def test_compute_signal_series_for_type_unknown_type_still_uses_rolling_beta_default():
    # tipos que não sejam "kalman" (incluindo desconhecidos) usam o
    # despacho por defeito baseado em compute_rolling_beta — a função de
    # visualização nunca deve rebentar mesmo para um strategy_type não
    # registado em RUN_BACKTEST_BY_TYPE (é só um gráfico, não decide nada).
    price_a, price_b = _synthetic_pair(n=400)
    series = compute_signal_series_for_type("qualquer_coisa", price_a, price_b, VOL_SCALED_EXIT_PARAMS)
    assert len(series["zscore"]) == len(price_a)
