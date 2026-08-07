"""
strategy_variants.py
======================
Famílias alternativas de lógica de hedge, além do molde padrão de
`backtest_engine.run_hedge_backtest()` (reversão de z-score, beta OLS
recalculado a cada N barras). Cada variante mantém a MESMA assinatura e
forma de retorno de `run_hedge_backtest()` — `(price_a, price_b, params,
cost_params=None, score_start=None) -> {"trades": [...], "stats": {...}}`
— para que `validate_strategy()`, `walk_forward_validate()` e
`strategy_registry.save_strategy()` funcionem sem nenhuma alteração.
Nenhuma variante reimplementa custos ou estatísticas — todas reusam
`compute_zscore`, `compute_rolling_beta`, `apply_transaction_costs` e
`compute_stats` de `backtest_engine.py`.

NUNCA usar a coluna "regime" de output/features_*.parquet aqui —
`data_pipeline.detect_regime()` ajusta o HMM sobre a série INTEIRA de
uma vez (`model.fit(obs)` sem janela rolante), portanto essa coluna tem
look-ahead bias. Qualquer proxy de volatilidade/regime usado numa
variante tem de ser causal (rolling, olhando só para trás) — ver
`_rolling_vol_percentile`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_engine import (
    STANDARD_LOT_CONTRACT_SIZE,
    apply_transaction_costs,
    compute_rolling_beta,
    compute_stats,
    compute_zscore,
)

STRATEGY_TYPES = ("kalman", "vol_scaled_exit", "asymmetric_bands")

PARAM_RANGES_KALMAN = {
    "entry_threshold": (1.3, 3.0),
    "exit_threshold": (0.1, 0.8),
    "min_correlation": (0.4, 0.75),
    "max_hold_bars": (30, 200),
    "corr_window": (100, 300),
    # Delta controla quão depressa o beta pode mudar (ver
    # .claude/skills/quant-finance-math/SKILL.md); ve é a variância de
    # observação assumida para o ruído do spread.
    "kalman_delta": (1e-5, 1e-2),
    "kalman_ve": (1e-4, 1e-1),
}
INT_PARAMS_KALMAN = {"max_hold_bars", "corr_window"}

PARAM_RANGES_VOL_SCALED_EXIT = {
    "entry_threshold": (1.3, 3.0),
    "exit_threshold": (0.1, 0.8),
    "min_correlation": (0.4, 0.75),
    "max_hold_bars": (30, 200),
    "beta_window": (200, 800),
    "corr_window": (100, 300),
    "vol_lookback": (50, 400),
    "vol_scale_min": (0.4, 0.9),
    "vol_scale_max": (1.1, 2.5),
}
INT_PARAMS_VOL_SCALED_EXIT = {"max_hold_bars", "beta_window", "corr_window", "vol_lookback"}

PARAM_RANGES_ASYMMETRIC_BANDS = {
    "entry_threshold_long": (1.3, 3.0),
    "entry_threshold_short": (1.3, 3.0),
    "exit_threshold": (0.1, 0.8),
    "min_correlation": (0.4, 0.75),
    "max_hold_bars": (30, 200),
    "beta_window": (200, 800),
    "corr_window": (100, 300),
}
INT_PARAMS_ASYMMETRIC_BANDS = {"max_hold_bars", "beta_window", "corr_window"}

PARAM_RANGES_BY_TYPE = {
    "kalman": PARAM_RANGES_KALMAN,
    "vol_scaled_exit": PARAM_RANGES_VOL_SCALED_EXIT,
    "asymmetric_bands": PARAM_RANGES_ASYMMETRIC_BANDS,
}
INT_PARAMS_BY_TYPE = {
    "kalman": INT_PARAMS_KALMAN,
    "vol_scaled_exit": INT_PARAMS_VOL_SCALED_EXIT,
    "asymmetric_bands": INT_PARAMS_ASYMMETRIC_BANDS,
}


# --------------------------------------------------------------------------
# Blocos partilhados pelas três variantes (evita triplicar a matemática de
# custos/fecho de trade — backtest_engine.run_hedge_backtest não é alterado)
# --------------------------------------------------------------------------

def kalman_hedge_ratio(price_a: pd.Series, price_b: pd.Series,
                        delta: float = 1e-4, ve: float = 1e-3) -> np.ndarray:
    """Beta dinâmico via filtro de Kalman (padrão exato de
    .claude/skills/quant-finance-math/SKILL.md, `kalman_hedge_ratio`) —
    causal por construção: beta[t] só depende de observações até t
    (random walk no beta, sem nenhuma janela futura). Guards de divisão
    por zero evitam NaN propagando-se silenciosamente caso alguma barra
    tenha preço 0 (não deveria acontecer em dados forex reais)."""
    a = price_a.values
    b = price_b.values
    n = len(a)
    beta = np.zeros(n)
    P = 1.0
    Q = delta / (1 - delta)
    beta[0] = a[0] / b[0] if b[0] else 0.0
    for t in range(1, n):
        beta_pred = beta[t - 1]
        P_pred = P + Q
        x = b[t]
        y = a[t]
        residual = y - beta_pred * x
        S = x * x * P_pred + ve
        K = (P_pred * x / S) if S else 0.0
        beta[t] = beta_pred + K * residual
        P = (1 - K * x) * P_pred
    return beta


def _rolling_vol_percentile(price: pd.Series, lookback: int) -> np.ndarray:
    """Percentil (0-1) da volatilidade ATUAL dentro da sua própria janela
    rolante de `lookback` barras — causal (só olha para trás), ao
    contrário da coluna "regime" de output/features_*.parquet (ver
    docstring do módulo). 1.0 = a barra mais volátil da janela; 0.0 = a
    mais calma."""
    ret = price.pct_change()
    vol = ret.rolling(lookback).std()
    pct_rank = vol.rolling(lookback).apply(
        lambda w: (w[-1] >= w).mean() if len(w) else np.nan, raw=True
    )
    return pct_rank.values


def _commission_r(cost_params: dict | None, entry_std: float) -> float:
    """Converte commission_per_lot ($/lote) para unidades 'R' — mesma
    fórmula usada inline em backtest_engine.run_hedge_backtest (via
    STANDARD_LOT_CONTRACT_SIZE + reference_lot_size), extraída aqui só
    para as variantes partilharem uma única implementação, sem alterar
    run_hedge_backtest (já testado, mantido intocado)."""
    if not cost_params or not entry_std or entry_std <= 0:
        return 0.0
    reference_lot_size = cost_params.get("reference_lot_size", 1.0)
    if not reference_lot_size:
        return 0.0
    commission_price_units = (
        cost_params.get("commission_per_lot", 0.0) / reference_lot_size / STANDARD_LOT_CONTRACT_SIZE
    )
    return commission_price_units / entry_std


def _close_trade_pnl_r(position: dict, spread_now: float, cost_params: dict | None) -> float:
    entry_std = position["entry_std"]
    pnl_raw = position["direction"] * (spread_now - position["entry_spread"])
    pnl_r = pnl_raw / entry_std if entry_std and entry_std > 0 else 0.0
    if cost_params is not None:
        commission_r = _commission_r(cost_params, entry_std)
        pnl_r = apply_transaction_costs(
            pnl_r, entry_std, {**cost_params, "commission_r": commission_r}, position["direction"],
        )
    return pnl_r


# --------------------------------------------------------------------------
# Variante 1: beta dinâmico via filtro de Kalman
# --------------------------------------------------------------------------

def run_hedge_backtest_kalman(price_a: pd.Series, price_b: pd.Series, params: dict,
                               cost_params: dict | None = None,
                               score_start: int | None = None) -> dict:
    """Mesma lógica de entrada/saída de `backtest_engine.run_hedge_backtest`
    (z-score de entrada/saída, correlação mínima, stop de tempo), mas com
    beta dinâmico via `kalman_hedge_ratio` em vez de OLS recalculado a
    cada N barras — isola a comparação a "beta suave vs. beta em
    degraus". params esperados: entry_threshold, exit_threshold,
    min_correlation, max_hold_bars, corr_window, kalman_delta, kalman_ve."""
    n_common = min(len(price_a), len(price_b))
    price_a = price_a.iloc[-n_common:].reset_index(drop=True)
    price_b = price_b.iloc[-n_common:].reset_index(drop=True)

    corr_window = int(params["corr_window"])
    entry_threshold = float(params["entry_threshold"])
    exit_threshold = float(params["exit_threshold"])
    min_correlation = float(params["min_correlation"])
    max_hold_bars = int(params["max_hold_bars"])
    kalman_delta = float(params.get("kalman_delta", 1e-4))
    kalman_ve = float(params.get("kalman_ve", 1e-3))

    n = len(price_a)
    beta = kalman_hedge_ratio(price_a, price_b, delta=kalman_delta, ve=kalman_ve)
    spread_vals = price_a.values - beta * price_b.values
    zscore, spread_std = compute_zscore(pd.Series(spread_vals), corr_window)
    rolling_corr = price_a.rolling(corr_window).corr(price_b)

    z_vals = zscore.values
    corr_vals = rolling_corr.values
    std_vals = spread_std.values

    trades = []
    position = None
    start = corr_window
    score_cutoff = start if score_start is None else max(start, score_start)

    for i in range(start, n):
        z = z_vals[i]
        corr = corr_vals[i]
        if np.isnan(z) or np.isnan(corr):
            continue

        if position is None:
            if abs(z) >= entry_threshold and corr >= min_correlation:
                position = {
                    "direction": -1 if z > 0 else 1,
                    "entry_bar": i,
                    "entry_spread": spread_vals[i],
                    "entry_std": std_vals[i],
                }
        else:
            bars_held = i - position["entry_bar"]
            exit_reason = None
            if abs(z) <= exit_threshold:
                exit_reason = "reversion"
            elif corr < min_correlation * 0.7:
                exit_reason = "correlation_breakdown"
            elif bars_held >= max_hold_bars:
                exit_reason = "time_stop"

            if exit_reason:
                pnl_r = _close_trade_pnl_r(position, spread_vals[i], cost_params)
                if position["entry_bar"] >= score_cutoff:
                    trades.append({
                        "entry_bar": int(position["entry_bar"]), "exit_bar": int(i),
                        "bars_held": int(bars_held), "direction": int(position["direction"]),
                        "pnl_r": round(float(pnl_r), 4), "exit_reason": exit_reason,
                    })
                position = None

    bars_scored = n - score_cutoff if score_start is not None else n
    stats = compute_stats(trades, bars_scored)
    return {"trades": trades, "stats": stats}


# --------------------------------------------------------------------------
# Variante 2: stop de tempo escalado por volatilidade causal
# --------------------------------------------------------------------------

def run_hedge_backtest_vol_scaled_exit(price_a: pd.Series, price_b: pd.Series, params: dict,
                                        cost_params: dict | None = None,
                                        score_start: int | None = None) -> dict:
    """Mesmo molde de `backtest_engine.run_hedge_backtest` (beta OLS em
    degraus), mas o stop de tempo (`max_hold_bars`) é escalado pela
    volatilidade CAUSAL do par no momento da ENTRADA (`_rolling_vol_percentile`
    — nunca a coluna "regime" pré-calculada, ver docstring do módulo):
    mais apertado quando a volatilidade recente está alta, mais largo
    quando está calma. O fator é fixado na entrada (não recalculado
    barra-a-barra), para que uma mudança de volatilidade depois de abrir
    a posição não altere retroativamente o stop de um trade já em curso.
    params esperados: os de run_hedge_backtest + vol_lookback,
    vol_scale_min, vol_scale_max."""
    n_common = min(len(price_a), len(price_b))
    price_a = price_a.iloc[-n_common:].reset_index(drop=True)
    price_b = price_b.iloc[-n_common:].reset_index(drop=True)

    beta_window = int(params["beta_window"])
    corr_window = int(params["corr_window"])
    recalc_every = int(params.get("recalc_every", 50))
    entry_threshold = float(params["entry_threshold"])
    exit_threshold = float(params["exit_threshold"])
    min_correlation = float(params["min_correlation"])
    max_hold_bars = int(params["max_hold_bars"])
    vol_lookback = int(params["vol_lookback"])
    vol_scale_min = float(params["vol_scale_min"])
    vol_scale_max = float(params["vol_scale_max"])

    n = len(price_a)
    beta = compute_rolling_beta(price_a, price_b, beta_window, recalc_every)
    spread = price_a - beta * price_b
    zscore, spread_std = compute_zscore(spread, corr_window)
    rolling_corr = price_a.rolling(corr_window).corr(price_b)
    vol_pct = _rolling_vol_percentile(price_a, vol_lookback)

    z_vals = zscore.values
    corr_vals = rolling_corr.values
    spread_vals = spread.values
    std_vals = spread_std.values

    trades = []
    position = None
    start = max(beta_window, corr_window, vol_lookback * 2)
    score_cutoff = start if score_start is None else max(start, score_start)

    for i in range(start, n):
        z = z_vals[i]
        corr = corr_vals[i]
        if np.isnan(z) or np.isnan(corr):
            continue

        if position is None:
            if abs(z) >= entry_threshold and corr >= min_correlation:
                pct = vol_pct[i]
                scale = (
                    vol_scale_max if np.isnan(pct)
                    else vol_scale_max - pct * (vol_scale_max - vol_scale_min)
                )
                position = {
                    "direction": -1 if z > 0 else 1,
                    "entry_bar": i,
                    "entry_spread": spread_vals[i],
                    "entry_std": std_vals[i],
                    "effective_max_hold": max(1, int(round(max_hold_bars * scale))),
                }
        else:
            bars_held = i - position["entry_bar"]
            exit_reason = None
            if abs(z) <= exit_threshold:
                exit_reason = "reversion"
            elif corr < min_correlation * 0.7:
                exit_reason = "correlation_breakdown"
            elif bars_held >= position["effective_max_hold"]:
                exit_reason = "time_stop"

            if exit_reason:
                pnl_r = _close_trade_pnl_r(position, spread_vals[i], cost_params)
                if position["entry_bar"] >= score_cutoff:
                    trades.append({
                        "entry_bar": int(position["entry_bar"]), "exit_bar": int(i),
                        "bars_held": int(bars_held), "direction": int(position["direction"]),
                        "pnl_r": round(float(pnl_r), 4), "exit_reason": exit_reason,
                    })
                position = None

    bars_scored = n - score_cutoff if score_start is not None else n
    stats = compute_stats(trades, bars_scored)
    return {"trades": trades, "stats": stats}


# --------------------------------------------------------------------------
# Variante 3: bandas de entrada assimétricas (skew direcional)
# --------------------------------------------------------------------------

def run_hedge_backtest_asymmetric_bands(price_a: pd.Series, price_b: pd.Series, params: dict,
                                         cost_params: dict | None = None,
                                         score_start: int | None = None) -> dict:
    """Mesmo molde de `backtest_engine.run_hedge_backtest` (beta OLS em
    degraus, saída idêntica), mas com limiares de entrada DISTINTOS para
    cada lado do spread (`entry_threshold_short` para z>0,
    `entry_threshold_long` para z<0) em vez de um único `entry_threshold`
    simétrico — deteta skew direcional na reversão à média do spread que
    o molde original não consegue capturar."""
    n_common = min(len(price_a), len(price_b))
    price_a = price_a.iloc[-n_common:].reset_index(drop=True)
    price_b = price_b.iloc[-n_common:].reset_index(drop=True)

    beta_window = int(params["beta_window"])
    corr_window = int(params["corr_window"])
    recalc_every = int(params.get("recalc_every", 50))
    entry_threshold_long = float(params["entry_threshold_long"])
    entry_threshold_short = float(params["entry_threshold_short"])
    exit_threshold = float(params["exit_threshold"])
    min_correlation = float(params["min_correlation"])
    max_hold_bars = int(params["max_hold_bars"])

    n = len(price_a)
    beta = compute_rolling_beta(price_a, price_b, beta_window, recalc_every)
    spread = price_a - beta * price_b
    zscore, spread_std = compute_zscore(spread, corr_window)
    rolling_corr = price_a.rolling(corr_window).corr(price_b)

    z_vals = zscore.values
    corr_vals = rolling_corr.values
    spread_vals = spread.values
    std_vals = spread_std.values

    trades = []
    position = None
    start = max(beta_window, corr_window)
    score_cutoff = start if score_start is None else max(start, score_start)

    for i in range(start, n):
        z = z_vals[i]
        corr = corr_vals[i]
        if np.isnan(z) or np.isnan(corr):
            continue

        if position is None:
            if z > 0 and z >= entry_threshold_short and corr >= min_correlation:
                position = {"direction": -1, "entry_bar": i,
                            "entry_spread": spread_vals[i], "entry_std": std_vals[i]}
            elif z < 0 and abs(z) >= entry_threshold_long and corr >= min_correlation:
                position = {"direction": 1, "entry_bar": i,
                            "entry_spread": spread_vals[i], "entry_std": std_vals[i]}
        else:
            bars_held = i - position["entry_bar"]
            exit_reason = None
            if abs(z) <= exit_threshold:
                exit_reason = "reversion"
            elif corr < min_correlation * 0.7:
                exit_reason = "correlation_breakdown"
            elif bars_held >= max_hold_bars:
                exit_reason = "time_stop"

            if exit_reason:
                pnl_r = _close_trade_pnl_r(position, spread_vals[i], cost_params)
                if position["entry_bar"] >= score_cutoff:
                    trades.append({
                        "entry_bar": int(position["entry_bar"]), "exit_bar": int(i),
                        "bars_held": int(bars_held), "direction": int(position["direction"]),
                        "pnl_r": round(float(pnl_r), 4), "exit_reason": exit_reason,
                    })
                position = None

    bars_scored = n - score_cutoff if score_start is not None else n
    stats = compute_stats(trades, bars_scored)
    return {"trades": trades, "stats": stats}


# --------------------------------------------------------------------------
# Despacho único por strategy_type — evita dois caminhos divergentes de
# "qual função corre este tipo" entre strategy_evolution.py,
# revalidate_walk_forward.py e qualquer consumidor futuro.
# --------------------------------------------------------------------------

RUN_BACKTEST_BY_TYPE = {
    "kalman": run_hedge_backtest_kalman,
    "vol_scaled_exit": run_hedge_backtest_vol_scaled_exit,
    "asymmetric_bands": run_hedge_backtest_asymmetric_bands,
}


def run_backtest_for_type(strategy_type: str, price_a: pd.Series, price_b: pd.Series, params: dict,
                           cost_params: dict | None = None, score_start: int | None = None) -> dict:
    """Despacha para a variante certa por `strategy_type`. `"zscore"` usa
    `backtest_engine.run_hedge_backtest` diretamente (molde original,
    intocado); qualquer outro valor usa `RUN_BACKTEST_BY_TYPE`. Import
    local de `run_hedge_backtest` (evita import circular: backtest_engine
    não precisa de conhecer strategy_variants)."""
    if strategy_type == "zscore":
        from backtest_engine import run_hedge_backtest
        return run_hedge_backtest(price_a, price_b, params, cost_params=cost_params, score_start=score_start)
    fn = RUN_BACKTEST_BY_TYPE.get(strategy_type)
    if fn is None:
        raise ValueError(f"strategy_type desconhecido: {strategy_type!r}")
    return fn(price_a, price_b, params, cost_params=cost_params, score_start=score_start)


# --------------------------------------------------------------------------
# Séries de sinal para VISUALIZAÇÃO (dashboard.py) — nunca reimplementa nem
# diverge da lógica de trade acima, só recalcula as mesmas séries causais
# sem o loop de simulação, para desenhar a linha de z-score + bandas.
# --------------------------------------------------------------------------

def compute_signal_series_for_type(strategy_type: str, price_a: pd.Series, price_b: pd.Series,
                                    params: dict) -> dict:
    """Recalcula a série de z-score/spread/correlação usada por
    `run_backtest_for_type`/variantes para o `strategy_type` dado — SÓ
    para visualização (`dashboard.py`), nunca para decidir uma entrada/
    saída real. Reusa exatamente as mesmas funções causais que a
    simulação de trade usa (`kalman_hedge_ratio` para `"kalman"`,
    `compute_rolling_beta` para todos os outros tipos, incluindo
    `"zscore"`), para que o gráfico nunca divirja do que o backtest real
    calculou.

    params esperados: os mesmos de cada variante (ver PARAM_RANGES_BY_TYPE
    / backtest_engine.run_hedge_backtest) — chaves em falta caem para os
    defaults já usados pelas próprias variantes (`corr_window=150`,
    `beta_window=200`, `recalc_every=50`).

    devolve:
        {"zscore": np.ndarray, "spread": np.ndarray, "correlation": np.ndarray}
    """
    n_common = min(len(price_a), len(price_b))
    price_a = price_a.iloc[-n_common:].reset_index(drop=True)
    price_b = price_b.iloc[-n_common:].reset_index(drop=True)

    corr_window = int(params.get("corr_window", 150))

    if strategy_type == "kalman":
        kalman_delta = float(params.get("kalman_delta", 1e-4))
        kalman_ve = float(params.get("kalman_ve", 1e-3))
        beta = kalman_hedge_ratio(price_a, price_b, delta=kalman_delta, ve=kalman_ve)
        spread = pd.Series(price_a.values - beta * price_b.values)
    else:
        beta_window = int(params.get("beta_window", 200))
        recalc_every = int(params.get("recalc_every", 50))
        beta = compute_rolling_beta(price_a, price_b, beta_window, recalc_every)
        spread = price_a - beta * price_b

    zscore, _ = compute_zscore(spread, corr_window)
    correlation = price_a.rolling(corr_window).corr(price_b)

    return {
        "zscore": zscore.values,
        "spread": spread.values,
        "correlation": correlation.values,
    }
