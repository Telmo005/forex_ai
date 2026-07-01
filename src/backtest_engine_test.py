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
    PROFIT_FACTOR_NO_LOSSES_SENTINEL,
    STANDARD_LOT_CONTRACT_SIZE,
    WALK_FORWARD_CONFIG,
    apply_transaction_costs,
    compute_stats,
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
# Test 6 (WR-04, 01-REVIEW.md): resolve_cost_params() levanta ValueError se
# as duas pernas tiverem reference_lot_size diferentes, em vez de descartar
# silenciosamente o valor de uma delas (bug hoje mascarado porque todas as
# entradas em DEFAULT_COST_PARAMS usam 1.0).
# ---------------------------------------------------------------------

def test_resolve_cost_params_raises_on_reference_lot_size_mismatch():
    import backtest_engine as be

    original = be.DEFAULT_COST_PARAMS
    patched = {
        **original,
        "EURUSD": {**original["EURUSD"], "reference_lot_size": 1.0},
        "GBPUSD": {**original["GBPUSD"], "reference_lot_size": 0.5},
    }
    be.DEFAULT_COST_PARAMS = patched
    try:
        try:
            be.resolve_cost_params("EURUSD", "GBPUSD")
            assert False, "deveria ter levantado ValueError por reference_lot_size divergente"
        except ValueError as exc:
            assert "reference_lot_size" in str(exc)
    finally:
        be.DEFAULT_COST_PARAMS = original


def test_resolve_cost_params_same_reference_lot_size_still_works():
    # Caminho normal (hoje sempre 1.0/1.0) continua a funcionar sem levantar.
    resolved = resolve_cost_params("EURUSD", "USDJPY")
    assert resolved["reference_lot_size"] == 1.0


# ---------------------------------------------------------------------
# Test (WR-03, 01-REVIEW.md): compute_stats() usa a sentinela nomeada
# PROFIT_FACTOR_NO_LOSSES_SENTINEL (não um "999.0" mágico solto no código)
# quando não há nenhum trade perdedor na amostra.
# ---------------------------------------------------------------------

def test_compute_stats_uses_named_sentinel_when_no_losing_trades():
    trades_all_wins = [
        {"pnl_r": 1.0, "bars_held": 10},
        {"pnl_r": 2.0, "bars_held": 12},
    ]
    stats = compute_stats(trades_all_wins, n_bars=100)
    assert stats["profit_factor"] == PROFIT_FACTOR_NO_LOSSES_SENTINEL

    trades_mixed = [
        {"pnl_r": 1.0, "bars_held": 10},
        {"pnl_r": -0.5, "bars_held": 8},
    ]
    stats_mixed = compute_stats(trades_mixed, n_bars=100)
    assert stats_mixed["profit_factor"] < PROFIT_FACTOR_NO_LOSSES_SENTINEL
    assert abs(stats_mixed["profit_factor"] - 2.0) < 1e-9


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


# ---------------------------------------------------------------------
# CR-01 (01-REVIEW.md): walk_forward_validate() deixou de descartar
# train_idx — cada fold agora recebe o histórico de treino real como
# aquecimento causal antes da janela de teste avaliada. Os testes abaixo
# provam (1) que run_hedge_backtest() com score_start só contabiliza
# trades cuja entrada cai dentro da janela avaliada, (2) que o aquecimento
# vindo de train_idx efetivamente aquece beta/z-score mais cedo do que um
# cold-start no bar 0 do fold, e (3) que os folds continuam a não contar
# barras de aquecimento em duplicado nas stats agregadas.
# ---------------------------------------------------------------------

def test_run_hedge_backtest_score_start_excludes_trades_entered_before_cutoff():
    price_a, price_b = _synthetic_pair(n=2000, seed=7)

    result_full = run_hedge_backtest(price_a, price_b, DEFAULT_PARAMS, cost_params=None)
    assert result_full["stats"]["total_trades"] > 0

    # Escolhe um score_start a meio da série testada — qualquer trade cuja
    # entry_bar caia antes disso NÃO pode aparecer no resultado com
    # score_start, mesmo que a mesma barra 0 continue a ser usada para
    # aquecer beta/z-score/correlação (a janela rolling em si é idêntica).
    cutoff = 1000
    result_scored = run_hedge_backtest(
        price_a, price_b, DEFAULT_PARAMS, cost_params=None, score_start=cutoff,
    )

    assert len(result_scored["trades"]) > 0, (
        "cenário de teste deveria produzir pelo menos um trade após o cutoff"
    )
    assert all(t["entry_bar"] >= cutoff for t in result_scored["trades"]), (
        "nenhum trade com entry_bar < score_start pode ser contabilizado"
    )
    # Confirma que trades antes do cutoff existiam na run completa (senão o
    # teste não provaria que algo foi de facto excluído).
    trades_before_cutoff_full = [t for t in result_full["trades"] if t["entry_bar"] < cutoff]
    assert len(trades_before_cutoff_full) > 0, (
        "cenário de teste deveria ter trades antes do cutoff na run sem score_start"
    )
    # bars_tested no resultado com score_start reflete só a janela avaliada.
    assert result_scored["stats"]["bars_tested"] == len(price_a) - cutoff


def test_run_hedge_backtest_score_start_none_is_equivalent_to_full_scoring():
    price_a, price_b = _synthetic_pair(n=2000, seed=7)

    result_default = run_hedge_backtest(price_a, price_b, DEFAULT_PARAMS, cost_params=None)
    result_explicit_none = run_hedge_backtest(
        price_a, price_b, DEFAULT_PARAMS, cost_params=None, score_start=None,
    )
    assert result_default["trades"] == result_explicit_none["trades"]
    assert result_default["stats"] == result_explicit_none["stats"]


def test_walk_forward_validate_warms_up_beta_zscore_from_train_idx_not_cold_start():
    """Prova o núcleo do fix CR-01: um fold recebe contexto de aquecimento
    real (train_idx), pelo que trades podem ocorrer MUITO cedo dentro da
    janela de teste (antes de max(beta_window, corr_window) barras terem
    decorrido desde o INÍCIO do teste) — algo impossível no comportamento
    anterior (bug), que tratava bar 0 do fold de teste como início de toda
    a história e exigia max(beta_window, corr_window) barras "perdidas" no
    início de cada fold antes de qualquer trade poder abrir."""
    price_a, price_b = _synthetic_pair(n=6000, seed=11)

    # beta_window/corr_window bem maiores que o tamanho do fold de teste:
    # sob o bug antigo (cold start no bar 0 do teste), NENHUM trade seria
    # possível em fold algum, porque `start = max(beta_window, corr_window)`
    # excederia sempre o nº de barras do fold de teste.
    config = {"n_splits": 8, "max_train_size": 2000, "gap": 0}
    params = {**DEFAULT_PARAMS, "beta_window": 800, "corr_window": 300}

    result = walk_forward_validate(price_a, price_b, params,
                                    cost_params=WF_COST_PARAMS, config=config)

    from sklearn.model_selection import TimeSeriesSplit

    n_common = min(len(price_a), len(price_b))
    tscv = TimeSeriesSplit(n_splits=config["n_splits"],
                            max_train_size=config["max_train_size"],
                            gap=config["gap"])
    test_fold_sizes = [len(test_idx) for _, test_idx in tscv.split(range(n_common))]

    # Confirma a premissa do teste: pelo menos um fold de teste é mais
    # pequeno que beta_window, o que tornaria impossível qualquer trade sob
    # o comportamento antigo (cold-start), mas é possível agora com
    # aquecimento vindo de train_idx.
    assert any(size < params["beta_window"] for size in test_fold_sizes), (
        "cenário de teste deveria ter pelo menos um fold de teste menor que beta_window"
    )

    total_trades = sum(f["stats"]["total_trades"] for f in result["fold_results"])
    assert total_trades > 0, (
        "com aquecimento real de train_idx, deve haver trades mesmo com "
        "folds de teste menores que beta_window/corr_window — o comportamento "
        "antigo (cold start por fold) tornaria isto impossível"
    )


def test_walk_forward_validate_does_not_double_count_warmup_bars_across_folds():
    """Cada trade contabilizado num fold tem entry_bar dentro da janela de
    teste desse fold (nunca dentro do respetivo aquecimento) — logo o total
    de trades agregado é exatamente a soma dos trades por fold, sem overlap
    introduzido pela sobreposição de aquecimento entre folds consecutivos
    (o aquecimento de um fold pode reutilizar barras já testadas por um
    fold anterior, mas essas barras nunca são recontadas como teste)."""
    price_a, price_b = _synthetic_pair(n=6000, seed=11)
    config = {"n_splits": 5, "max_train_size": 1500, "gap": 0}

    result = walk_forward_validate(price_a, price_b, DEFAULT_PARAMS,
                                    cost_params=WF_COST_PARAMS, config=config)

    per_fold_total = sum(f["stats"]["total_trades"] for f in result["fold_results"])
    assert result["aggregate_stats"]["total_trades"] == per_fold_total
