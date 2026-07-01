"""
backtest_engine.py
===================
Simula a lógica de hedge estatístico (definida em
docs/hedge_engine_spec.md) sobre uma série histórica de dois pares, e
calcula estatísticas completas de performance.

Não há lookahead bias deliberado: o hedge ratio (beta) é recalculado
periodicamente usando só dados passados (`compute_rolling_beta`), e o
z-score/correlação usam janelas móveis causais (`pandas.rolling`, que só
olha para trás por definição).

PnL é expresso em "R" (múltiplos do desvio-padrão do spread no momento
da entrada), não em dinheiro real — isto isola a qualidade da LÓGICA de
entrada/saída do dimensionamento de posição, que é responsabilidade da
camada de risco (camada 3), não desta camada. 1.0 R = o spread reverteu
exatamente um desvio-padrão a favor da posição.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Beta dinâmico, z-score e correlação (todos causais, sem lookahead)
# --------------------------------------------------------------------------

def compute_rolling_beta(price_a: pd.Series, price_b: pd.Series,
                          window: int, recalc_every: int = 50) -> pd.Series:
    """Recalcula o hedge ratio (beta) periodicamente via OLS, usando
    apenas a janela de dados ANTERIOR ao ponto atual. Entre recálculos,
    mantém o último beta conhecido (forward-fill)."""
    n = len(price_a)
    betas = np.full(n, np.nan)
    a_vals = price_a.values
    b_vals = price_b.values

    for i in range(window, n, recalc_every):
        a_win = a_vals[i - window:i]
        b_win = b_vals[i - window:i]
        X = np.column_stack([np.ones(window), b_win])
        coef, *_ = np.linalg.lstsq(X, a_win, rcond=None)
        betas[i] = coef[1]

    betas_s = pd.Series(betas, index=price_a.index).ffill().bfill()
    return betas_s


def compute_zscore(spread: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    mean = spread.rolling(window).mean()
    std = spread.rolling(window).std()
    zscore = (spread - mean) / std
    return zscore, std


# --------------------------------------------------------------------------
# Simulação da estratégia de hedge
# --------------------------------------------------------------------------

def run_hedge_backtest(price_a: pd.Series, price_b: pd.Series, params: dict) -> dict:
    """Simula a lógica de entrada/saída de docs/hedge_engine_spec.md.

    params esperados:
        entry_threshold   (float) - |z| mínimo para abrir
        exit_threshold    (float) - |z| máximo para fechar por reversão
        min_correlation   (float) - correlação mínima para abrir/manter
        max_hold_bars     (int)   - stop de tempo
        beta_window       (int)   - janela do hedge ratio
        corr_window       (int)   - janela de correlação/z-score
        recalc_every       (int, opcional) - cadência de recálculo do beta
    """
    # Alinhamento POSICIONAL (não por label) entre as duas séries — evita
    # que diferenças de timestamp entre símbolos (ex.: gerados em
    # instantes ligeiramente diferentes, ou feriados específicos de um
    # instrumento) causem NaNs silenciosos por desalinhamento de índice
    # do pandas. Assume-se que ambas as séries têm a mesma cadência de
    # barras (mesmo timeframe) e comprimento comparável.
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

    for i in range(start, n):
        z = z_vals[i]
        corr = corr_vals[i]
        if np.isnan(z) or np.isnan(corr):
            continue

        if position is None:
            if abs(z) >= entry_threshold and corr >= min_correlation:
                position = {
                    "direction": -1 if z > 0 else 1,   # short spread se acima da média, long se abaixo
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
                entry_std = position["entry_std"]
                pnl_raw = position["direction"] * (spread_vals[i] - position["entry_spread"])
                pnl_r = pnl_raw / entry_std if entry_std and entry_std > 0 else 0.0
                trades.append({
                    "entry_bar": int(position["entry_bar"]),
                    "exit_bar": int(i),
                    "bars_held": int(bars_held),
                    "direction": int(position["direction"]),
                    "pnl_r": round(float(pnl_r), 4),
                    "exit_reason": exit_reason,
                })
                position = None

    stats = compute_stats(trades, n)
    return {"trades": trades, "stats": stats}


# --------------------------------------------------------------------------
# Estatísticas completas
# --------------------------------------------------------------------------

def compute_stats(trades: list[dict], n_bars: int) -> dict:
    base = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "sharpe_per_trade": 0.0, "total_return_r": 0.0, "max_drawdown_r": 0.0,
        "avg_hold_bars": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
        "bars_tested": n_bars,
    }
    if not trades:
        return base

    pnls = np.array([t["pnl_r"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    win_rate = len(wins) / len(pnls)
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    sharpe = (pnls.mean() / pnls.std()) if pnls.std() > 0 else 0.0

    equity = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    drawdown = running_max - equity
    max_dd = drawdown.max() if len(drawdown) else 0.0

    return {
        "total_trades": len(trades),
        "win_rate": round(float(win_rate), 3),
        "profit_factor": round(float(profit_factor), 3),
        "sharpe_per_trade": round(float(sharpe), 3),
        "total_return_r": round(float(equity[-1]), 3),
        "max_drawdown_r": round(float(max_dd), 3),
        "avg_hold_bars": round(float(np.mean([t["bars_held"] for t in trades])), 1),
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "bars_tested": n_bars,
    }


# --------------------------------------------------------------------------
# Gate de validação — critérios de "trader sénior" para aprovar uma estratégia
# --------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "min_trades": 20,          # abaixo disto, não é estatisticamente significativo
    "min_profit_factor": 1.2,
    "min_sharpe": 0.15,
    "max_drawdown_r": 8.0,
}


def validate_strategy(stats: dict, thresholds: dict | None = None) -> tuple[bool, list[str]]:
    th = thresholds or DEFAULT_THRESHOLDS
    reasons = []

    if stats["total_trades"] < th["min_trades"]:
        reasons.append(
            f"poucos trades ({stats['total_trades']} < {th['min_trades']}) "
            f"- amostra pequena demais para confiar no resultado"
        )
    if stats["profit_factor"] < th["min_profit_factor"]:
        reasons.append(
            f"profit factor insuficiente ({stats['profit_factor']} < {th['min_profit_factor']})"
        )
    if stats["sharpe_per_trade"] < th["min_sharpe"]:
        reasons.append(
            f"sharpe por trade insuficiente ({stats['sharpe_per_trade']} < {th['min_sharpe']})"
        )
    if stats["max_drawdown_r"] > th["max_drawdown_r"]:
        reasons.append(
            f"drawdown máximo excessivo ({stats['max_drawdown_r']}R > {th['max_drawdown_r']}R)"
        )
    if stats["total_return_r"] <= 0:
        reasons.append("retorno total não positivo no período testado")

    return (len(reasons) == 0), reasons
