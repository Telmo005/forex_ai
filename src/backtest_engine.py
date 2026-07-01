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
from sklearn.model_selection import TimeSeriesSplit


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

# Tamanho de contrato standard forex (1 lote = 100.000 unidades da divisa
# base) — convenção universal de mercado, não um placeholder pendente de
# calibração como os valores em DEFAULT_COST_PARAMS. Usado SÓ para
# converter commission_per_lot ($/lote) em unidades de preço por unidade
# negociada, antes de dividir por entry_std para exprimir a comissão em
# "R" (ver run_hedge_backtest, bloco de fecho de trade) — sem esta
# conversão, dividir diretamente $/lote por um desvio-padrão de spread
# mistura unidades incompatíveis (dólares vs. preço) e produz um
# commission_r sem sentido (ver Pitfall 2 do 01-RESEARCH.md desta fase).
STANDARD_LOT_CONTRACT_SIZE = 100_000

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
        reference_lot_size  (float) - reference_lot_size partilhado pelas duas pernas
                                       (ambas têm de assumir o mesmo tamanho de posição
                                       de referência — ver ValueError abaixo se diferirem)

    levanta:
        ValueError - se pair_a e pair_b tiverem reference_lot_size diferentes.
            commission_per_lot devolvido é a SOMA das duas pernas, e
            run_hedge_backtest() divide essa soma por um único
            reference_lot_size ao converter para "R" — se as pernas tivessem
            valores diferentes, dividir a soma por apenas um deles produziria
            um commission_r incorreto para a perna descartada (WR-04 do
            01-REVIEW.md). Hoje todos os valores em DEFAULT_COST_PARAMS usam
            1.0, por isso nunca diverge na prática — mas isto falha alto e
            cedo assim que uma calibração real (MT5 symbol_info()) introduzir
            valores diferentes por símbolo, em vez de silenciosamente
            calcular um valor errado.
    """
    cost_a = DEFAULT_COST_PARAMS.get(pair_a, DEFAULT_COST_PARAMS["_DEFAULT"])
    cost_b = DEFAULT_COST_PARAMS.get(pair_b, DEFAULT_COST_PARAMS["_DEFAULT"])

    if cost_a["reference_lot_size"] != cost_b["reference_lot_size"]:
        raise ValueError(
            f"reference_lot_size mismatch entre {pair_a} ({cost_a['reference_lot_size']}) "
            f"e {pair_b} ({cost_b['reference_lot_size']}) — a conversão de commission_r "
            "assume um único reference_lot_size partilhado pelas duas pernas."
        )

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
                        cost_params: dict | None = None,
                        score_start: int | None = None) -> dict:
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

    score_start (opcional, int | None):
        Indice posicional (relativo a price_a/price_b APOS o alinhamento
        interno feito abaixo, ou seja, bar 0 = primeira barra recebida) a
        partir do qual um trade pode ser CONTABILIZADO nas estatisticas
        devolvidas. Quando None (default, comportamento pre-existente),
        todos os trades encontrados a partir de start = max(beta_window,
        corr_window) sao contabilizados - e o caminho usado por
        strategy_generator.py e por uma validacao de janela unica.
        Quando fornecido (usado por walk_forward_validate(), fix do CR-01
        de 01-REVIEW.md), permite passar dados de AQUECIMENTO (historico
        real anterior ao fold de teste) concatenados antes da janela de
        teste, para que beta/z-score/correlacao sejam calculados
        causalmente com contexto real - mas so os trades cuja entry_bar
        cai dentro da janela de teste (>= score_start) entram nas stats
        devolvidas. Isto evita que barras de aquecimento sejam contadas
        como se fossem parte do periodo out-of-sample avaliado.
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
    # score_start define a partir de que barra um trade pode ser
    # CONTABILIZADO (ver docstring acima) — não altera `start`, que continua
    # a ser o corte de aquecimento das janelas rolling. Posições podem abrir
    # ainda durante o aquecimento (start <= i < score_start) e fechar dentro
    # da janela de teste; nesse caso o trade teria entry_bar < score_start e
    # é corretamente excluído das stats (evita contar um trade cuja entrada
    # não foi observada dentro do período out-of-sample avaliado).
    score_cutoff = start if score_start is None else max(start, score_start)

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
                    # Comissão é nativamente $/lote (não unidades de preço) —
                    # não pode ser dividida diretamente por entry_std sem
                    # antes converter para unidades de preço, ou o resultado
                    # mistura dólares com desvios-padrão de spread (bug de
                    # unidades). Usa-se o tamanho de contrato standard forex
                    # (STANDARD_LOT_CONTRACT_SIZE = 100_000 unidades da
                    # divisa base) para converter $/lote em unidades de
                    # preço por unidade negociada, exatamente como
                    # trade_tick_value faria via MT5 symbol_info() (ver
                    # Pitfall 2 do 01-RESEARCH.md desta fase) — só depois se
                    # divide por entry_std para obter "R", assumindo
                    # reference_lot_size como o tamanho de posição de
                    # referência (placeholder de validação; a camada de
                    # risco, ainda por construir, é quem decide o tamanho
                    # real).
                    reference_lot_size = cost_params.get("reference_lot_size", 1.0)
                    if entry_std and entry_std > 0 and reference_lot_size:
                        commission_price_units = (
                            cost_params.get("commission_per_lot", 0.0)
                            / reference_lot_size
                            / STANDARD_LOT_CONTRACT_SIZE
                        )
                        commission_r = commission_price_units / entry_std
                    else:
                        commission_r = 0.0
                    # IN-03 (01-REVIEW.md): {**cost_params, "commission_r": ...}
                    # sobrescreveria silenciosamente uma chave "commission_r"
                    # já presente em cost_params (ex. um futuro chamador que
                    # a pré-calcule) sem aviso nenhum. resolve_cost_params()
                    # nunca emite essa chave hoje, mas o contrato implícito
                    # (só este bloco decide commission_r, nunca o chamador)
                    # não estava documentado nem defendido — falha alto e
                    # cedo em vez de mascarar um valor pré-calculado.
                    assert "commission_r" not in cost_params, (
                        "cost_params já contém 'commission_r' — este bloco é o único "
                        "responsável por calculá-lo; um chamador não deve pré-computá-lo."
                    )
                    pnl_r = apply_transaction_costs(
                        pnl_r, entry_std, {**cost_params, "commission_r": commission_r},
                        position["direction"],
                    )
                if position["entry_bar"] >= score_cutoff:
                    # Só conta trades cuja ENTRADA ocorreu dentro da janela
                    # avaliada — uma posição aberta durante o aquecimento
                    # (entry_bar < score_cutoff) não foi realmente observada
                    # no período out-of-sample, mesmo que feche dentro dele.
                    trades.append({
                        "entry_bar": int(position["entry_bar"]),
                        "exit_bar": int(i),
                        "bars_held": int(bars_held),
                        "direction": int(position["direction"]),
                        "pnl_r": round(float(pnl_r), 4),
                        "exit_reason": exit_reason,
                    })
                position = None

    bars_scored = n - score_cutoff if score_start is not None else n
    stats = compute_stats(trades, bars_scored)
    return {"trades": trades, "stats": stats}


# --------------------------------------------------------------------------
# Estatísticas completas
# --------------------------------------------------------------------------

# Sentinela para profit_factor quando a amostra de trades não tem NENHUM
# trade perdedor (gross_loss == 0 mas gross_win > 0) — divisão por zero não
# é o comportamento desejado, mas um valor literal "real" de profit factor
# também não existe nesse caso. NÃO é um profit factor genuíno; consumidores
# (ex. dashboard.py) devem tratar este valor como "sem perdas na amostra" e
# excluí-lo de agregados tipo "melhor profit factor" (WR-03, 01-REVIEW.md).
PROFIT_FACTOR_NO_LOSSES_SENTINEL = 999.0


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
    # WR-03 (01-REVIEW.md): PROFIT_FACTOR_NO_LOSSES_SENTINEL (não um valor
    # literal "real" de profit factor) marca o caso de zero trades
    # perdedores na amostra — plausível para amostras pequenas perto do
    # min_trades=20. Mantido como sentinela numérico finito (em vez de
    # float("inf")) para que continue serializável em JSON sem ambiguidade
    # (json.dumps(float("inf")) produz "Infinity", que não é JSON válido
    # per RFC 8259, mesmo que o parser do Python o aceite). Consumidores
    # (dashboard.py) devem tratar este valor explicitamente como "sem
    # perdas na amostra", não como um profit factor de 999x real — ver
    # PROFIT_FACTOR_NO_LOSSES_SENTINEL usado em dashboard.py.
    profit_factor = (
        (gross_win / gross_loss) if gross_loss > 0
        else (PROFIT_FACTOR_NO_LOSSES_SENTINEL if gross_win > 0 else 0.0)
    )
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


# --------------------------------------------------------------------------
# Walk-forward: revalidação out-of-sample de parâmetros já fixos/aprovados
# --------------------------------------------------------------------------
#
# Isto é REVALIDAÇÃO, não reotimização (RESEARCH.md Assumption A3 / secção
# Anti-Patterns): os `params` chegam já fixos (escolhidos pelo laboratório e
# aprovados no dashboard, ou em teste); walk_forward_validate() NUNCA refita
# ou muta `params` por fold — o loop de mutação de strategy_generator.py
# está fora de âmbito aqui. Cada fold reusa o mesmo run_hedge_backtest()
# (já cost-aware desde o plan 01-02) para que o veredito out-of-sample seja
# sempre net-of-cost, sem wiring adicional.
#
# Janela ROLLING (não expandida/ancorada): TimeSeriesSplit tem por padrão
# uma janela de treino EXPANSÍVEL (anchored), o que não corresponde à tese
# deste projeto de regime de mercado dependente do tempo + scalping M5
# (RESEARCH.md Pitfall 3 / Assumption A2). `max_train_size` força janelas de
# treino ROLLING (tamanho fixo, desliza no tempo) em vez de ancoradas.
#
# NOTA (visível para revisão humana, ver 01-03-PLAN.md <objective>): a
# escolha rolling-vs-ancorada foi adotada da Assumption A2 do RESEARCH.md e
# NÃO foi reconfirmada explicitamente numa etapa discuss-phase — se esta
# assunção estiver errada para este projeto, `WALK_FORWARD_CONFIG["window_type"]`
# e `max_train_size` são o único ponto a alterar para passar a ancorado
# (max_train_size=None restaura o comportamento expansível default do
# sklearn).

WALK_FORWARD_CONFIG = {
    "n_splits": 5,            # nº de folds sequenciais out-of-sample
    "max_train_size": 5000,   # tamanho FIXO da janela de treino (barras) -> rolling, não ancorado
    "gap": 0,                 # sem gap treino/teste: params já fixos, não há refit que possa vazar informação através da fronteira (RESEARCH.md Standard Stack, nota sobre `gap`)
    "window_type": "rolling",  # documentado explicitamente para não masquerade como ancorado (Pitfall 3 / T-01-07)
}

FOLD_THRESHOLDS = {
    # Distinto do DEFAULT_THRESHOLDS["min_trades"] (=20) agregado — NÃO é
    # min_trades dividido pelo nº de folds (RESEARCH.md Assumption A4).
    # Um fold com 5000 barras de treino via WALK_FORWARD_CONFIG ainda deve
    # produzir uma amostra de trades mínima e estatisticamente defensável
    # por si só; 5-8 é a gama conservadora recomendada em RESEARCH.md
    # Pitfall 5 para não deixar passar folds de 2-3 trades (ruído, não
    # edge) só porque o agregado cumpre o limiar total. Escolhido 6 como
    # ponto médio conservador dessa gama.
    "min_trades_per_fold": 6,
}


def walk_forward_validate(price_a: pd.Series, price_b: pd.Series, params: dict,
                           cost_params: dict | None = None,
                           config: dict | None = None) -> dict:
    """Revalida `params` (já fixos/aprovados) em folds sequenciais rolling
    out-of-sample, usando o mesmo run_hedge_backtest() cost-aware do resto
    deste módulo (VALID-01 + VALID-02 combinados: nenhum fold é cost-blind).

    NÃO refita nem muta `params` entre folds — isto é revalidação, não
    reotimização (ver comentário de secção acima e RESEARCH.md Anti-Patterns).

    params esperados: os mesmos de run_hedge_backtest() (entry_threshold,
        exit_threshold, min_correlation, max_hold_bars, beta_window,
        corr_window, recalc_every opcional).

    cost_params (opcional, dict | None): repassado tal-e-qual a cada
        run_hedge_backtest() por fold — ver docstring de run_hedge_backtest.
        Passar sempre um dict populado (via resolve_cost_params) em
        qualquer chamada usada para aprovação/produção (CLAUDE.md regra 4).

    config (opcional, dict | None): sobrepõe WALK_FORWARD_CONFIG
        (n_splits, max_train_size, gap, window_type). Default None usa
        WALK_FORWARD_CONFIG tal como está.

    Gate por fold (RELAXADO): cada fold só precisa de retorno líquido de
    custos positivo (`total_return_r > 0`) e nº de trades >=
    FOLD_THRESHOLDS["min_trades_per_fold"] — expresso reusando
    validate_strategy() com um thresholds dict relaxado, para que exista
    UM único caminho de validação (RESEARCH.md "Don't Hand-Roll"), não dois
    divergentes.

    Gate agregado (COMPLETO): os trades out-of-sample de todos os folds são
    concatenados, compute_stats() roda sobre essa amostra agregada, e
    validate_strategy() aplica o DEFAULT_THRESHOLDS inteiro (profit factor,
    Sharpe, drawdown, min_trades) — não só o gate relaxado.

    overall_passed = True apenas se TODOS os folds passarem o gate relaxado
    E o agregado passar o DEFAULT_THRESHOLDS completo.

    devolve:
        {
            "fold_results": [
                {"fold": int, "stats": dict, "passed": bool, "reasons": list[str]},
                ...
            ],
            "aggregate_stats": dict,       # compute_stats() sobre trades concatenados
            "aggregate_passed": bool,      # validate_strategy(aggregate_stats, DEFAULT_THRESHOLDS)
            "aggregate_reasons": list[str],
            "overall_passed": bool,        # all(fold passed) AND aggregate_passed
            "window_type": "rolling",
        }
    """
    cfg = {**WALK_FORWARD_CONFIG, **(config or {})}

    n_common = min(len(price_a), len(price_b))
    price_a = price_a.iloc[-n_common:].reset_index(drop=True)
    price_b = price_b.iloc[-n_common:].reset_index(drop=True)

    tscv = TimeSeriesSplit(
        n_splits=cfg["n_splits"],
        max_train_size=cfg["max_train_size"],
        gap=cfg["gap"],
    )

    fold_thresholds = {
        # Gate relaxado: só retorno positivo + min_trades_per_fold — NÃO
        # herda min_profit_factor/min_sharpe/max_drawdown_r do
        # DEFAULT_THRESHOLDS (esses só se aplicam ao agregado, abaixo).
        "min_trades": FOLD_THRESHOLDS["min_trades_per_fold"],
        "min_profit_factor": 0.0,
        "min_sharpe": -np.inf,
        "max_drawdown_r": np.inf,
    }

    fold_results = []
    all_oos_trades: list[dict] = []

    for fold_i, (train_idx, test_idx) in enumerate(tscv.split(range(n_common))):
        # CR-01 fix (01-REVIEW.md): a janela de treino NÃO é usada para
        # refit (params já fixos) mas TEM de ser usada como contexto de
        # AQUECIMENTO causal — sem isto, beta/z-score/correlação
        # "cold-start" no bar 0 do fold de teste, sem lookback real, o que
        # contradiz a premissa de que este mecanismo espelha o workflow real
        # de recálculo rolling contínuo. Concatena-se train_idx + test_idx
        # (histórico real imediatamente anterior ao fold) e passa-se
        # score_start=len(train_idx) para que run_hedge_backtest() só
        # contabilize trades cuja entrada ocorre dentro da janela de teste,
        # mesmo que as janelas rolling já estejam "quentes" antes disso.
        combined_start = train_idx[0]
        combined_end = test_idx[-1] + 1
        combined_a = price_a.iloc[combined_start:combined_end].reset_index(drop=True)
        combined_b = price_b.iloc[combined_start:combined_end].reset_index(drop=True)
        test_start_local = test_idx[0] - combined_start

        result = run_hedge_backtest(
            combined_a, combined_b, params, cost_params=cost_params,
            score_start=test_start_local,
        )
        stats = result["stats"]
        passed, reasons = validate_strategy(stats, fold_thresholds)

        fold_results.append({
            "fold": fold_i,
            "stats": stats,
            "passed": passed,
            "reasons": reasons,
        })
        all_oos_trades.extend(result["trades"])

    aggregate_stats = compute_stats(all_oos_trades, n_common)
    aggregate_passed, aggregate_reasons = validate_strategy(aggregate_stats, DEFAULT_THRESHOLDS)

    overall_passed = all(f["passed"] for f in fold_results) and aggregate_passed

    return {
        "fold_results": fold_results,
        "aggregate_stats": aggregate_stats,
        "aggregate_passed": aggregate_passed,
        "aggregate_reasons": aggregate_reasons,
        "overall_passed": overall_passed,
        "window_type": cfg["window_type"],
    }
