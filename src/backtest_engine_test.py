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
    FOLD_THRESHOLDS,
    STANDARD_LOT_CONTRACT_SIZE,
    WALK_FORWARD_CONFIG,
    apply_transaction_costs,
    resolve_cost_params,
    run_hedge_backtest,
    walk_forward_validate,
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
    commission_price_units = resolved["commission_per_lot"] / reference_lot_size / STANDARD_LOT_CONTRACT_SIZE
    commission_r = commission_price_units / entry_std

    result = apply_transaction_costs(
        pnl_r=2.0,
        entry_std=entry_std,
        cost_params={**resolved, "commission_r": commission_r},
        direction=1,
    )

    assert isinstance(result, float)
    assert np.isfinite(result)


# ---------------------------------------------------------------------
# Walk-forward tests (plan 01-03, VALID-01)
# ---------------------------------------------------------------------

WF_COST_PARAMS = {
    "spread_cost": 0.0005,
    "slippage_cost": 0.0002,
    "commission_per_lot": 5.0,
    "reference_lot_size": 1.0,
}


# ---------------------------------------------------------------------
# Test 1: janelas de treino ROLLING (limitadas por max_train_size), não
# expandidas/ancoradas — confirma que o default anchored do TimeSeriesSplit
# foi corretamente sobreposto (Pitfall 3 / T-01-07).
# ---------------------------------------------------------------------

def test_walk_forward_validate_produces_rolling_not_expanding_folds():
    price_a, price_b = _synthetic_pair(n=6000, seed=11)

    config = {"n_splits": 5, "max_train_size": 500, "gap": 0}

    # Reconstrói exatamente os folds que walk_forward_validate() usaria
    # internamente, via TimeSeriesSplit com a mesma config, para inspecionar
    # o tamanho da janela de treino de cada fold sem duplicar lógica de
    # validação (walk_forward_validate não expõe train_idx diretamente).
    from sklearn.model_selection import TimeSeriesSplit

    n_common = min(len(price_a), len(price_b))
    tscv = TimeSeriesSplit(n_splits=config["n_splits"],
                            max_train_size=config["max_train_size"],
                            gap=config["gap"])
    folds = list(tscv.split(range(n_common)))
    assert len(folds) == 5

    for train_idx, _test_idx in folds:
        train_span = train_idx[-1] - train_idx[0] + 1
        assert train_span <= config["max_train_size"], (
            f"fold train span {train_span} excede max_train_size "
            f"{config['max_train_size']} -> janela ancorada/expansível vazou"
        )

    result = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                    cost_params=WF_COST_PARAMS, config=config)
    assert len(result["fold_results"]) == 5
    assert result["window_type"] == "rolling"


# ---------------------------------------------------------------------
# Test 2: fold com total_trades < min_trades_per_fold falha o gate relaxado
# mesmo com retorno positivo (gate de nº mínimo de trades por fold).
# ---------------------------------------------------------------------

def test_walk_forward_fold_below_min_trades_fails_even_with_positive_return():
    price_a, price_b = _synthetic_pair(n=6000, seed=11)

    # max_train_size pequeno + poucas barras de teste por fold reduz
    # drasticamente o nº de trades possíveis por fold, forçando pelo menos
    # um fold a cair abaixo de min_trades_per_fold.
    config = {"n_splits": 8, "max_train_size": 300, "gap": 0}

    result = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                    cost_params=WF_COST_PARAMS, config=config)

    low_trade_folds = [
        f for f in result["fold_results"]
        if f["stats"]["total_trades"] < FOLD_THRESHOLDS["min_trades_per_fold"]
    ]
    assert len(low_trade_folds) > 0, (
        "cenário de teste deveria produzir pelo menos um fold com poucos trades"
    )
    for f in low_trade_folds:
        assert f["passed"] is False
        assert any("poucos trades" in r for r in f["reasons"])


# ---------------------------------------------------------------------
# Test 3: fold com total_return_r não positivo falha o gate relaxado.
# ---------------------------------------------------------------------

def test_walk_forward_fold_nonpositive_return_fails_relaxed_gate():
    price_a, price_b = _synthetic_pair(n=6000, seed=11)
    result = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                    cost_params=WF_COST_PARAMS)

    losing_folds = [f for f in result["fold_results"] if f["stats"]["total_return_r"] <= 0]
    for f in losing_folds:
        assert f["passed"] is False
        assert any("retorno total não positivo" in r for r in f["reasons"])


# ---------------------------------------------------------------------
# Test 4: overall_passed só é True se TODOS os folds passarem o gate
# relaxado E o agregado passar o DEFAULT_THRESHOLDS completo — um caso em
# que os folds passam individualmente mas o agregado falha o gate completo
# (thresholds agregados artificialmente inatingíveis) produz overall_passed
# False.
# ---------------------------------------------------------------------

def test_walk_forward_overall_passed_requires_both_fold_and_aggregate_gates():
    price_a, price_b = _synthetic_pair(n=6000, seed=11)

    result = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                    cost_params=WF_COST_PARAMS)

    all_folds_passed = all(f["passed"] for f in result["fold_results"])
    # overall_passed nunca pode ser True se algum fold falhou, nem se o
    # agregado falhou — testa a conjunção exata.
    assert result["overall_passed"] == (all_folds_passed and result["aggregate_passed"])

    # Força um caso concreto: agregado com thresholds artificialmente
    # inatingíveis (sharpe mínimo absurdo) deve reprovar o agregado mesmo
    # que os folds individuais relaxados passem.
    import backtest_engine as be
    unreachable_thresholds = {**be.DEFAULT_THRESHOLDS, "min_sharpe": 999.0}
    aggregate_passed, _ = be.validate_strategy(result["aggregate_stats"], unreachable_thresholds)
    assert aggregate_passed is False
    forced_overall = all_folds_passed and aggregate_passed
    assert forced_overall is False


# ---------------------------------------------------------------------
# Test 5: TODO fold é cost-aware (VALID-02 aplica-se a todos os folds, não
# só a um) — comparar fold a fold (por índice) entre uma run com custo
# não-zero e uma com custo zero; para todo fold com >=1 trade em ambas as
# runs, o retorno net-of-cost deve ser estritamente menor (all(...), não
# any(...)).
# ---------------------------------------------------------------------

def test_walk_forward_every_fold_is_cost_aware():
    price_a, price_b = _synthetic_pair(n=6000, seed=11)

    result_cost = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                         cost_params=WF_COST_PARAMS)
    result_zero_cost = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                              cost_params=None)

    assert len(result_cost["fold_results"]) == len(result_zero_cost["fold_results"])

    paired = list(zip(result_cost["fold_results"], result_zero_cost["fold_results"]))
    comparable = [
        (f_cost, f_zero) for f_cost, f_zero in paired
        if f_cost["stats"]["total_trades"] > 0 and f_zero["stats"]["total_trades"] > 0
    ]
    assert len(comparable) > 0, "cenário de teste deveria produzir pelo menos um fold com trades em ambas as runs"

    assert all(
        f_cost["stats"]["total_return_r"] < f_zero["stats"]["total_return_r"]
        for f_cost, f_zero in comparable
    ), "todo fold com trades deve ser net-of-cost mais baixo que a versão sem custo (VALID-02 em todos os folds, não só um)"
