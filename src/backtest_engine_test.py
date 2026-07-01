"""
backtest_engine_test.py
========================
Testes runnable-em-qualquer-lugar (sem MT5, sem output/) para o modelo de
custos de transação adicionado em backtest_engine.py (plan 01-02, VALID-02).

Usa apenas séries pandas sintéticas em memória — não depende de
output/features_*.parquet nem de uma ligação MT5.

Uso:
    python -m pytest src/backtest_engine_test.py -x -q
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_engine import (
    apply_transaction_costs,
    resolve_cost_params,
    run_hedge_backtest,
)


def _synthetic_pair(n: int = 2000, seed: int = 7) -> tuple[pd.Series, pd.Series]:
    """Gera duas séries de preços cointegradas o suficiente para produzir
    trades reprodutíveis num backtest (spread mean-reverting por construção)."""
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 0.0005, n)) + 1.1000
    noise_a = rng.normal(0, 0.0002, n)
    noise_b = rng.normal(0, 0.0002, n)
    # Componente mean-reverting no spread para gerar entradas/saídas.
    mean_revert = np.zeros(n)
    for i in range(1, n):
        mean_revert[i] = mean_revert[i - 1] * 0.9 + rng.normal(0, 0.0008)
    price_a = pd.Series(common + noise_a + mean_revert)
    price_b = pd.Series(common + noise_b)
    return price_a, price_b


DEFAULT_PARAMS = {
    "entry_threshold": 1.5,
    "exit_threshold": 0.3,
    "min_correlation": 0.4,
    "max_hold_bars": 100,
    "beta_window": 200,
    "corr_window": 150,
    "recalc_every": 50,
}


# ---------------------------------------------------------------------
# Test 1: apply_transaction_costs subtrai spread+slippage (em R) e comissão
# ---------------------------------------------------------------------

def test_apply_transaction_costs_subtracts_spread_slippage_and_commission():
    result = apply_transaction_costs(
        pnl_r=2.0,
        entry_std=1.0,
        cost_params={"spread_cost": 0.3, "slippage_cost": 0.1, "commission_r": 0.05},
        direction=1,
    )
    assert abs(result - 1.55) < 1e-9


# ---------------------------------------------------------------------
# Test 2: entry_std <= 0 devolve pnl_r inalterado (guarda de normalização)
# ---------------------------------------------------------------------

def test_apply_transaction_costs_nonpositive_entry_std_returns_unchanged():
    result = apply_transaction_costs(
        pnl_r=2.0,
        entry_std=0.0,
        cost_params={"spread_cost": 0.3, "slippage_cost": 0.1, "commission_r": 0.05},
        direction=1,
    )
    assert result == 2.0

    result_negative = apply_transaction_costs(
        pnl_r=2.0,
        entry_std=-1.0,
        cost_params={"spread_cost": 0.3, "slippage_cost": 0.1, "commission_r": 0.05},
        direction=-1,
    )
    assert result_negative == 2.0


# ---------------------------------------------------------------------
# Test 3: cost_params vazio/ausente (defaults 0.0) devolve pnl_r inalterado
# ---------------------------------------------------------------------

def test_apply_transaction_costs_empty_cost_params_returns_unchanged():
    result = apply_transaction_costs(pnl_r=1.234, entry_std=1.0, cost_params={}, direction=1)
    assert abs(result - 1.234) < 1e-9


# ---------------------------------------------------------------------
# Test 4: cost_params não-nulos reduzem total_return_r vs. cost_params=None
# ---------------------------------------------------------------------

def test_run_hedge_backtest_with_costs_yields_lower_total_return_than_without():
    price_a, price_b = _synthetic_pair()

    cost_params = {
        "spread_cost": 0.0005,
        "slippage_cost": 0.0002,
        "commission_per_lot": 5.0,
        "reference_lot_size": 1.0,
    }

    result_no_cost = run_hedge_backtest(price_a, price_b, DEFAULT_PARAMS, cost_params=None)
    result_with_cost = run_hedge_backtest(price_a, price_b, DEFAULT_PARAMS, cost_params=cost_params)

    assert result_no_cost["stats"]["total_trades"] > 0, "o cenário sintético deve produzir trades"
    assert result_with_cost["stats"]["total_return_r"] < result_no_cost["stats"]["total_return_r"]

    # Confirma que a forma dos dicts de trade se mantém inalterada.
    for trade in result_with_cost["trades"]:
        assert set(trade.keys()) == {
            "entry_bar", "exit_bar", "bars_held", "direction", "pnl_r", "exit_reason",
        }
        assert round(trade["pnl_r"], 4) == trade["pnl_r"]


# ---------------------------------------------------------------------
# Test 5: contrato de dados resolve_cost_params() [01-01] -> apply_transaction_costs() [01-02]
# ---------------------------------------------------------------------

def test_resolve_cost_params_output_is_consumable_by_apply_transaction_costs():
    resolved = resolve_cost_params("EURUSD", "GBPUSD")

    # As chaves documentadas em resolve_cost_params() devem estar todas presentes.
    assert set(resolved.keys()) == {
        "spread_cost", "slippage_cost", "commission_per_lot", "reference_lot_size",
    }

    entry_std = 0.0012  # desvio-padrão de spread plausível de teste
    reference_lot_size = resolved.get("reference_lot_size", 1.0)
    commission_r = resolved["commission_per_lot"] / reference_lot_size / entry_std

    result = apply_transaction_costs(
        pnl_r=2.0,
        entry_std=entry_std,
        cost_params={**resolved, "commission_r": commission_r},
        direction=1,
    )

    assert isinstance(result, float)
    assert np.isfinite(result)
