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
# Modelo de custos de transação (spread + slippage + comissão)
# --------------------------------------------------------------------------
#
# Valores placeholder pendentes de calibração com dados reais da corretora
# (symbol_info().spread/point/trade_tick_value via MT5, ver
# data_pipeline.fetch_mt5). Documentado em docs/strategy_lab_spec.md secção
# "Modelo de custos de transacao". Fontes: intervalos publicados de mercado
# para pares major (~0.1-3 pips spread, ~$2-7/lote comissão round-turn,
# ~1-10 pips slippage) — ver 01-RESEARCH.md Pitfall 1.
#
# Unidades: spread_cost/slippage_cost já em UNIDADES DE PREÇO (não pips) —
# isto é, pips * point size do símbolo — para compor diretamente com
# `spread = price_a - beta * price_b` usado no resto deste módulo.
# commission_per_lot é em USD por lote round-turn; reference_lot_size é a
# assunção de tamanho de posição usada SÓ para exprimir a comissão em "R"
# durante esta validação (a camada de risco, ainda não construída, é quem
# decide o tamanho de posição real — ver Pitfall 2 do RESEARCH.md).

COST_MODEL_VERSION = "placeholder-v1"  # bump sempre que os valores abaixo forem recalibrados com dados reais

DEFAULT_COST_PARAMS: dict[str, dict[str, float]] = {
    # EURUSD: par mais líquido, spread tipicamente o mais apertado do mercado
    "EURUSD": {
        "spread_cost": 0.00010,       # placeholder: ~1.0 pip (pendente symbol_info() real)
        "slippage_cost": 0.00003,     # placeholder: ~0.3 pip
        "commission_per_lot": 3.5,    # placeholder: USD por lote round-turn
        "reference_lot_size": 1.0,    # placeholder: 1 lote standard, só para converter comissão em R
    },
    "GBPUSD": {
        "spread_cost": 0.00015,       # placeholder: ~1.5 pips
        "slippage_cost": 0.00004,     # placeholder: ~0.4 pip
        "commission_per_lot": 4.0,    # placeholder
        "reference_lot_size": 1.0,    # placeholder
    },
    "USDJPY": {
        "spread_cost": 0.012,         # placeholder: ~1.2 pips (JPY tem point size diferente, 0.01)
        "slippage_cost": 0.004,       # placeholder: ~0.4 pip
        "commission_per_lot": 3.5,    # placeholder
        "reference_lot_size": 1.0,    # placeholder
    },
    "AUDUSD": {
        "spread_cost": 0.00016,       # placeholder: ~1.6 pips
        "slippage_cost": 0.00005,     # placeholder: ~0.5 pip
        "commission_per_lot": 4.5,    # placeholder
        "reference_lot_size": 1.0,    # placeholder
    },
    "USDCAD": {
        "spread_cost": 0.00018,       # placeholder: ~1.8 pips
        "slippage_cost": 0.00005,     # placeholder: ~0.5 pip
        "commission_per_lot": 4.5,    # placeholder
        "reference_lot_size": 1.0,    # placeholder
    },
    "NZDUSD": {
        "spread_cost": 0.00022,       # placeholder: ~2.2 pips (par menos líquido)
        "slippage_cost": 0.00007,     # placeholder: ~0.7 pip
        "commission_per_lot": 5.0,    # placeholder
        "reference_lot_size": 1.0,    # placeholder
    },
    "USDCHF": {
        "spread_cost": 0.00020,       # placeholder: ~2.0 pips
        "slippage_cost": 0.00006,     # placeholder: ~0.6 pip
        "commission_per_lot": 5.0,    # placeholder
        "reference_lot_size": 1.0,    # placeholder
    },
    "_DEFAULT": {
        "spread_cost": 0.00030,       # placeholder: ~3.0 pips — assunção conservadora para símbolo desconhecido
        "slippage_cost": 0.00010,     # placeholder: ~1.0 pip
        "commission_per_lot": 7.0,    # placeholder: teto superior do intervalo publicado
        "reference_lot_size": 1.0,    # placeholder
    },
}


def resolve_cost_params(pair_a: str, pair_b: str) -> dict:
    """Combina o custo round-trip das duas pernas do hedge (pair_a + pair_b)
    num único dict pronto a consumir por run_hedge_backtest() (wiring feito
    em plan 01-02, não aqui).

    params esperados (por símbolo, em DEFAULT_COST_PARAMS):
        spread_cost         (float) - custo de spread em unidades de preço (pips * point)
        slippage_cost       (float) - slippage modelado em unidades de preço
        commission_per_lot  (float) - comissão USD por lote round-turn
        reference_lot_size  (float) - lote assumido só para exprimir comissão em R

    devolve:
        spread_cost         (float) - soma das duas pernas
        slippage_cost       (float) - soma das duas pernas
        commission_per_lot  (float) - soma das duas pernas (cada perna paga a sua comissão)
        reference_lot_size  (float) - reference_lot_size da perna A (ambas as pernas
                                       assumem o mesmo tamanho de posição de referência)
    """
    cost_a = DEFAULT_COST_PARAMS.get(pair_a, DEFAULT_COST_PARAMS["_DEFAULT"])
    cost_b = DEFAULT_COST_PARAMS.get(pair_b, DEFAULT_COST_PARAMS["_DEFAULT"])

    return {
        "spread_cost": cost_a["spread_cost"] + cost_b["spread_cost"],
        "slippage_cost": cost_a["slippage_cost"] + cost_b["slippage_cost"],
        "commission_per_lot": cost_a["commission_per_lot"] + cost_b["commission_per_lot"],
        "reference_lot_size": cost_a["reference_lot_size"],
    }


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


def apply_transaction_costs(pnl_r: float, entry_std: float, cost_params: dict,
                             direction: int) -> float:
    """Subtrai o custo round-trip modelado (spread + slippage + comissão)
    do pnl_r bruto de um trade, no momento em que fecha.

    Os custos de spread/slippage chegam em unidades de preço (a mesma
    unidade de `spread = price_a - beta * price_b`) e são convertidos para
    "R" dividindo por `entry_std` — o mesmo desvio-padrão do spread na
    entrada que já normaliza `pnl_r` no resto deste módulo (ver docstring
    do módulo). A comissão já deve chegar pré-convertida para "R" na chave
    `commission_r` (ver Pitfall 2 do 01-RESEARCH.md desta fase: comissão é
    nativamente $/lote, não spread-std-dev, por isso a conversão para R
    exige uma assunção de tamanho de posição de referência, feita pelo
    chamador antes de invocar esta função — não aqui).

    `direction` (1 = long spread, -1 = short spread) não altera o sinal do
    custo: custos de transação são sempre um dreno de PnL, independente do
    lado da posição; o parâmetro existe para compatibilidade futura (ex.
    custos assimétricos por lado) e não é usado na fórmula atual.

    cost_params esperados:
        spread_cost    (float) - custo de spread round-trip, em unidades de preço
        slippage_cost  (float) - slippage modelado, em unidades de preço
        commission_r   (float) - comissão já convertida para unidades "R"
    """
    if not entry_std or entry_std <= 0:
        return pnl_r  # não é possível normalizar; mantém pnl_r inalterado
    total_cost_price_units = cost_params.get("spread_cost", 0.0) + cost_params.get("slippage_cost", 0.0)
    cost_r = total_cost_price_units / entry_std
    return pnl_r - cost_r - cost_params.get("commission_r", 0.0)


# --------------------------------------------------------------------------
# Simulação da estratégia de hedge
# --------------------------------------------------------------------------

def run_hedge_backtest(price_a: pd.Series, price_b: pd.Series, params: dict,
                        cost_params: dict | None = None) -> dict:
    """Simula a lógica de entrada/saída de docs/hedge_engine_spec.md.

    params esperados:
        entry_threshold   (float) - |z| mínimo para abrir
        exit_threshold    (float) - |z| máximo para fechar por reversão
        min_correlation   (float) - correlação mínima para abrir/manter
        max_hold_bars     (int)   - stop de tempo
        beta_window       (int)   - janela do hedge ratio
        corr_window       (int)   - janela de correlação/z-score
        recalc_every       (int, opcional) - cadência de recálculo do beta

    cost_params (opcional, dict | None):
        Custos de transação (spread, slippage, comissão) a subtrair de
        cada trade em pnl_r, via apply_transaction_costs(). Chaves
        esperadas (ver resolve_cost_params() acima):
            spread_cost         (float) - custo de spread round-trip, em unidades de preço
            slippage_cost       (float) - slippage modelado, em unidades de preço
            commission_per_lot  (float) - comissão USD por lote round-turn
            reference_lot_size  (float) - lote assumido para exprimir comissão em R
        Quando None, nenhum custo é subtraído (path cost-blind) — reservado
        a uso interno/debug; NUNCA deve ser o caminho usado por
        strategy_generator.py, dashboard.py ou qualquer gate de aprovação
        (CLAUDE.md regra 4 / VALID-02).
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
                if cost_params is not None:
                    # Comissão é nativamente $/lote (não spread-std-dev) —
                    # converte-se para "R" relativo ao entry_std deste trade,
                    # assumindo reference_lot_size como o tamanho de posição
                    # de referência (placeholder de validação, ver Pitfall 2
                    # do 01-RESEARCH.md; a camada de risco, ainda por
                    # construir, é quem decide o tamanho real).
                    reference_lot_size = cost_params.get("reference_lot_size", 1.0)
                    if entry_std and entry_std > 0 and reference_lot_size:
                        commission_r = cost_params.get("commission_per_lot", 0.0) / reference_lot_size / entry_std
                    else:
                        commission_r = 0.0
                    pnl_r = apply_transaction_costs(
                        pnl_r, entry_std, {**cost_params, "commission_r": commission_r},
                        position["direction"],
                    )
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
