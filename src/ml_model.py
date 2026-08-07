"""
ml_model.py
============
Camada 1 (ver ARCHITECTURE.md / docs/ml_model_spec.md) — sinal
direcional com confiança, condicionado ao regime da Camada 0. Gradient
boosting (LightGBM) sobre features tabulares — nunca deep learning
nesta baseline (docs/ml_model_spec.md "Princípio orientador": um
modelo de árvores bem validado costuma bater um Transformer mal
validado em dados financeiros ruidosos).

Alvo (label): "triple barrier" (López de Prado), implementado
VERBATIM a partir de `.claude/skills/quant-finance-math/SKILL.md` —
NUNCA "retorno do próximo candle" (ruidoso demais, não alinha com como
um trade real fecha). tp_pct/sl_pct são percentagens fixas de preço
(não múltiplos de volatilidade) precisamente porque são invariantes à
escala do símbolo — 0.15% é a mesma distância relativa em EURUSD
(~1.10) e USDJPY (~150), sem precisar de normalizar por preço.

RISK-09 (herdado de risk_engine.py/hedge_engine.py): este módulo NUNCA
decide dimensionamento nem risco — produz só um sinal (direção +
probabilidade), que o motor de hedge (Camada 2) pode usar como
informação adicional; qualquer ordem continua sujeita ao motor de
risco (Camada 3) determinístico, sem exceção.

Validação — walk-forward, nunca split aleatório (mesma disciplina de
backtest_engine.walk_forward_validate): TimeSeriesSplit com janela de
treino ROLLING (max_train_size fixo), refeito a cada fold — ao
contrário da revalidação de estratégias fixas, aqui o MODELO é
retreinado por fold, porque é o próprio modelo que está a ser validado,
não um conjunto de parâmetros já fixos.

Sinal de alerta (docs/ml_model_spec.md "Riscos específicos"): accuracy
direcional acima de OVERFIT_ACCURACY_WARNING num timeframe curto é
tratada com suspeita, nunca celebrada sem confirmação adicional — é
registada como aviso explícito, nunca escondida.

Uso:
    python src/ml_model.py --symbols EURUSD,AUDUSD,GBPUSD
    python src/ml_model.py --symbols EURUSD --features-dir output
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

# Permite `python src/ml_model.py` diretamente (sys.path[0] fica em "src/"
# nesse caso, onde "src" não se resolve como pacote) — mesmo padrão de
# scripts/run_live_hedge_loop.py. Sob pytest (`from src.ml_model import
# ...`), o rootdir já está em sys.path e este insert é inofensivo (path
# duplicado, sem efeito).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest_engine import DEFAULT_COST_PARAMS

log = logging.getLogger("ml_model")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ============================================================================
# Constantes (triple barrier + walk-forward) — nunca "magic numbers" soltos
# ============================================================================

TRIPLE_BARRIER_TP_PCT = 0.0015   # take-profit: 0.15% do preço de entrada (~15 pips em EURUSD)
TRIPLE_BARRIER_SL_PCT = 0.0015   # stop-loss: simétrico ao TP (payoff_ratio ~1:1 por desenho do rótulo)
TRIPLE_BARRIER_MAX_BARS = 20     # barreira de tempo: 20 barras M5 (~100 min) sem TP/SL -> rótulo 0

ML_WALK_FORWARD_CONFIG = {
    "n_splits": 5,             # mesma convenção de backtest_engine.WALK_FORWARD_CONFIG
    "max_train_size": 5000,    # janela de treino ROLLING (fixa), não ancorada/expansível
    "gap": TRIPLE_BARRIER_MAX_BARS,  # gap >= max_bars: o rótulo do último bar de treino já
                                     # "olha" max_bars à frente: sem este gap, esse rótulo
                                     # dependeria de preços dentro da janela de teste (fuga de
                                     # informação do futuro através da fronteira treino/teste)
}

# accuracy direcional acima disto num timeframe curto é suspeita, não celebração
# (docs/ml_model_spec.md "Riscos específicos a vigiar")
OVERFIT_ACCURACY_WARNING = 0.65

# Nenhum fold deve confiar num modelo treinado com poucas amostras de cada classe
MIN_TRAIN_SAMPLES_PER_CLASS = 30

FEATURE_COLUMNS = [
    "log_ret", "vol_fast", "vol_slow", "vol_ratio", "zscore_price", "range_pct",
    "regime", "hour_of_day", "day_of_week",
]

MODEL_OUTPUT_DIR = "output/ml_models"

# Mapeamento label <-> classe LightGBM (LightGBM multiclass exige 0..n-1, não -1/0/1)
_LABEL_TO_CLASS = {-1.0: 0, 0.0: 1, 1.0: 2}
_CLASS_TO_LABEL = {v: k for k, v in _LABEL_TO_CLASS.items()}


# ============================================================================
# Rotulagem: triple barrier (VERBATIM da skill quant-finance-math)
# ============================================================================


def triple_barrier_labels(prices: pd.Series, tp_pct: float = TRIPLE_BARRIER_TP_PCT,
                           sl_pct: float = TRIPLE_BARRIER_SL_PCT,
                           max_bars: int = TRIPLE_BARRIER_MAX_BARS) -> pd.Series:
    """Para cada ponto t, olha para a frente até `max_bars` e devolve:
    +1 se o take-profit foi atingido primeiro, -1 se o stop-loss foi
    atingido primeiro, 0 se nem um nem outro dentro de `max_bars`
    (barreira de tempo). Implementação verbatim de
    `.claude/skills/quant-finance-math/SKILL.md`.

    Os últimos `max_bars` pontos da série ficam com rótulo NaN — não há
    janela futura completa disponível para os rotular (nunca inventar
    um rótulo a partir de dados incompletos).

    params:
        prices (pd.Series)  - série de preços de fecho, ordenada no tempo
        tp_pct, sl_pct (float) - distância percentual das barreiras (fixa,
            não escalada por volatilidade — invariante à escala de preço
            do símbolo, ver docstring do módulo)
        max_bars (int)       - barreira de tempo máxima, em nº de barras

    devolve:
        pd.Series - rótulo por bar (+1/-1/0), NaN nos últimos `max_bars`
            pontos (sem janela futura completa)
    """
    values = prices.to_numpy()
    n = len(values)
    labels = np.full(n, np.nan)

    for t in range(n - max_bars):
        entry = values[t]
        window = values[t + 1: t + 1 + max_bars]
        tp_level = entry * (1 + tp_pct)
        sl_level = entry * (1 - sl_pct)
        hit_tp = np.where(window >= tp_level)[0]
        hit_sl = np.where(window <= sl_level)[0]
        first_tp = hit_tp[0] if len(hit_tp) else np.inf
        first_sl = hit_sl[0] if len(hit_sl) else np.inf
        if first_tp < first_sl:
            labels[t] = 1.0
        elif first_sl < first_tp:
            labels[t] = -1.0
        else:
            labels[t] = 0.0

    return pd.Series(labels, index=prices.index, name="label")


# ============================================================================
# Preparação de features (reusa a Camada 0, acrescenta hora/dia da semana)
# ============================================================================


def add_time_features(features: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta `hour_of_day`/`day_of_week` a partir do índice
    DatetimeIndex — sessão de mercado ativa (Londres/NY/Tóquio) importa
    para scalping (docs/ml_model_spec.md, secção Input). Não modifica
    `features` no lugar (devolve uma cópia)."""
    out = features.copy()
    out["hour_of_day"] = out.index.hour
    out["day_of_week"] = out.index.dayofweek
    return out


def load_symbol_dataset(features_path: str) -> pd.DataFrame:
    """Carrega `output/features_<SYMBOL>.parquet`, acrescenta features de
    tempo e o rótulo triple-barrier. Descarta linhas sem rótulo (fim da
    série) ou sem alguma feature (início da série, janelas rolantes da
    Camada 0 ainda "frias")."""
    raw = pd.read_parquet(features_path)
    df = add_time_features(raw)
    df["label"] = triple_barrier_labels(df["close"])
    return df.dropna(subset=["label", *FEATURE_COLUMNS])


# ============================================================================
# Simulação de PnL líquida de custos (para os folds de validação)
# ============================================================================


def _cost_in_r(symbol: str, entry_price: float, tp_pct: float,
               cost_params: dict | None = None) -> float:
    """Converte o custo round-trip (spread + slippage; comissão omitida
    aqui por simplicidade — é pequena face ao spread num único símbolo,
    ao contrário do par duplo de hedge) para unidades de "R", onde
    1R = tp_pct (a mesma unidade do rótulo triple-barrier), mesma
    convenção de custos-sempre-líquidos do resto do projeto (regra 4).
    """
    cost = cost_params or DEFAULT_COST_PARAMS.get(symbol, DEFAULT_COST_PARAMS["_DEFAULT"])
    cost_price_units = cost["spread_cost"] + cost["slippage_cost"]
    cost_fraction_of_price = cost_price_units / entry_price
    return cost_fraction_of_price / tp_pct


def simulate_signal_pnl(preds: pd.Series, actual_labels: pd.Series, entry_prices: pd.Series,
                         symbol: str, tp_pct: float = TRIPLE_BARRIER_TP_PCT,
                         sl_pct: float = TRIPLE_BARRIER_SL_PCT,
                         cost_params: dict | None = None) -> list[float]:
    """PnL simulado em "R" (1R = tp_pct), líquido de custos, para os
    bars onde o modelo previu uma direção (`pred != 0`) — bars "flat"
    (o modelo abstém-se) nunca entram na simulação, porque não geram
    proposta de trade nenhuma.

    Ganho/perda por trade determinado pela barreira REAL atingida
    (`actual_labels`), não pela previsão em si — mesma disciplina de
    "líquido de custos, nunca otimista" do resto do projeto:
        pred == actual (acertou a barreira prevista) -> +tp_pct/sl_pct (em R, 1.0)
        actual == 0 (bateu a barreira de tempo)        -> 0.0 (nem ganhou nem perdeu)
        pred != actual e actual != 0 (bateu a barreira oposta) -> -1.0 (em R)
    Custo de spread/slippage subtraído de cada trade, sempre.

    devolve:
        list[float] - um valor de PnL em R por trade simulado (lista
            vazia se o modelo nunca previu uma direção nesta amostra)
    """
    pnl = []
    for idx in preds.index:
        pred = preds.loc[idx]
        if pred == 0.0:
            continue
        actual = actual_labels.loc[idx]
        entry_price = entry_prices.loc[idx]
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        if actual == 0.0:
            gross_r = 0.0
        elif pred == actual:
            gross_r = 1.0
        else:
            gross_r = -1.0

        cost_r = _cost_in_r(symbol, entry_price, tp_pct, cost_params)
        pnl.append(gross_r - cost_r)
    return pnl


def compute_ml_stats(pnl_r: list[float], preds: pd.Series, actual_labels: pd.Series,
                      regimes: pd.Series) -> dict:
    """Estatísticas do sinal, incluindo accuracy CONDICIONADA AO REGIME
    (docs/ml_model_spec.md: "o modelo pode ser bom só num regime e
    péssimo noutro"). Accuracy direcional aqui é calculada só sobre os
    bars em que o modelo efetivamente propôs uma direção (`pred != 0`)
    — bars "flat" não são nem acerto nem erro, são abstenção.
    """
    traded_mask = preds != 0.0
    n_traded = int(traded_mask.sum())

    if n_traded == 0:
        accuracy = 0.0
    else:
        accuracy = float((preds[traded_mask] == actual_labels[traded_mask]).mean())

    regime_accuracy: dict[str, float] = {}
    for regime_value in sorted(regimes.unique()):
        regime_mask = traded_mask & (regimes == regime_value)
        if regime_mask.sum() == 0:
            continue
        regime_accuracy[str(int(regime_value))] = float(
            (preds[regime_mask] == actual_labels[regime_mask]).mean()
        )

    arr = np.array(pnl_r, dtype=float)
    total_trades = len(arr)
    if total_trades == 0:
        return {
            "total_trades": 0, "accuracy": accuracy, "regime_accuracy": regime_accuracy,
            "sharpe_per_trade": 0.0, "profit_factor": 0.0, "total_return_r": 0.0,
            "max_drawdown_r": 0.0, "win_rate": 0.0,
        }

    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())

    cumulative = np.cumsum(arr)
    running_max = np.maximum.accumulate(cumulative)
    max_drawdown_r = float((running_max - cumulative).max())

    return {
        "total_trades": total_trades,
        "accuracy": accuracy,
        "regime_accuracy": regime_accuracy,
        "win_rate": round(float((arr > 0).mean()), 3),
        "sharpe_per_trade": round(float(arr.mean() / arr.std()), 3) if arr.std() > 0 else 0.0,
        "profit_factor": round(float(gross_win / gross_loss), 3) if gross_loss > 0 else 999.0,
        "total_return_r": round(float(cumulative[-1]), 3),
        "max_drawdown_r": round(max_drawdown_r, 3),
    }


# ============================================================================
# Treino walk-forward (refita o modelo a cada fold — é o modelo em si
# que está a ser validado, ao contrário de backtest_engine.walk_forward_validate)
# ============================================================================


def train_walk_forward(symbol: str, features_path: str,
                        config: dict | None = None,
                        cost_params: dict | None = None) -> dict:
    """Treina e valida um classificador LightGBM (3 classes: -1/0/+1)
    via walk-forward rolling sobre o histórico de `symbol`. Refita o
    modelo em CADA fold — nunca reusa um modelo treinado numa janela
    para prever fora do fold seguinte sem retreinar primeiro (fuga de
    informação do futuro).

    devolve:
        dict com "fold_results" (lista por fold) e "aggregate_stats"
        (estatísticas sobre os trades simulados de TODOS os folds
        concatenados) — mesma forma de backtest_engine.walk_forward_validate
        para consistência entre os dois mecanismos de validação do projeto.
    """
    cfg = {**ML_WALK_FORWARD_CONFIG, **(config or {})}
    df = load_symbol_dataset(features_path)

    X = df[FEATURE_COLUMNS]
    y = df["label"]

    tscv = TimeSeriesSplit(n_splits=cfg["n_splits"], max_train_size=cfg["max_train_size"], gap=cfg["gap"])

    fold_results = []
    all_pnl: list[float] = []
    all_preds: list[pd.Series] = []
    all_actual: list[pd.Series] = []
    all_regimes: list[pd.Series] = []

    for fold_i, (train_idx, test_idx) in enumerate(tscv.split(X)):
        y_train = y.iloc[train_idx]
        class_counts = y_train.map(_LABEL_TO_CLASS).value_counts()
        if (class_counts < MIN_TRAIN_SAMPLES_PER_CLASS).any() or len(class_counts) < 3:
            log.warning(
                "train_walk_forward(%s): fold %d com classes desequilibradas/em falta "
                "(contagens=%s) — fold ignorado (amostra insuficiente para confiar no modelo)",
                symbol, fold_i, class_counts.to_dict(),
            )
            continue

        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train_mapped = y_train.map(_LABEL_TO_CLASS)

        model = lgb.LGBMClassifier(n_estimators=100, max_depth=5, random_state=42, verbose=-1, class_weight="balanced")
        model.fit(X_train, y_train_mapped)

        preds_mapped = model.predict(X_test)
        preds = pd.Series(preds_mapped, index=X_test.index).map(_CLASS_TO_LABEL)
        actual = y.iloc[test_idx]
        regimes_test = df["regime"].iloc[test_idx]
        entry_prices = df["close"].iloc[test_idx]

        pnl_r = simulate_signal_pnl(preds, actual, entry_prices, symbol, cost_params=cost_params)
        stats = compute_ml_stats(pnl_r, preds, actual, regimes_test)

        if stats["accuracy"] > OVERFIT_ACCURACY_WARNING:
            log.warning(
                "train_walk_forward(%s): fold %d com accuracy %.1f%% (> %.0f%%) — "
                "suspeito de sobreajuste num timeframe curto, tratar com desconfiança, "
                "não celebrar (docs/ml_model_spec.md).",
                symbol, fold_i, stats["accuracy"] * 100, OVERFIT_ACCURACY_WARNING * 100,
            )

        fold_results.append({"fold": fold_i, "stats": stats})
        all_pnl.extend(pnl_r)
        all_preds.append(preds)
        all_actual.append(actual)
        all_regimes.append(regimes_test)

    if not fold_results:
        log.warning("train_walk_forward(%s): nenhum fold com amostra suficiente — sem validação possível.", symbol)
        return {"symbol": symbol, "fold_results": [], "aggregate_stats": None}

    aggregate_stats = compute_ml_stats(
        all_pnl, pd.concat(all_preds), pd.concat(all_actual), pd.concat(all_regimes),
    )

    return {"symbol": symbol, "fold_results": fold_results, "aggregate_stats": aggregate_stats}


# ============================================================================
# Modelo final (treinado sobre todo o histórico disponível) + inferência
# ============================================================================


def train_final_model(symbol: str, features_path: str) -> str:
    """Treina o modelo de produção sobre TODO o histórico disponível
    (não é validação — a validação já foi feita em train_walk_forward)
    e guarda-o em disco. Chamado só depois de a validação walk-forward
    ter sido revista (mesmo gate de "nunca em produção sem validação
    objetiva" do resto do projeto, CLAUDE.md regra 7 — aplicado aqui
    por convenção de workflow, não imposto por código nesta função).

    devolve:
        (str) - caminho do ficheiro do modelo guardado

    levanta:
        ValueError - se faltar alguma das 3 classes de rótulo (-1/0/+1)
            no histórico disponível. Sem esta verificação, o LightGBM
            muda silenciosamente para classificação BINÁRIA quando só 2
            classes estão presentes, o que quebra a suposição de
            `predict_signal()` de que o modelo devolve sempre 3
            probabilidades — falhar aqui, cedo e alto, é preferível a um
            erro de indexação confuso mais tarde na inferência.
    """
    df = load_symbol_dataset(features_path)
    X = df[FEATURE_COLUMNS]
    y = df["label"].map(_LABEL_TO_CLASS)

    class_counts = y.value_counts()
    if len(class_counts) < 3:
        raise ValueError(
            f"train_final_model({symbol}): só {len(class_counts)} classe(s) de rótulo "
            f"presente(s) no histórico (contagens={class_counts.to_dict()}) — "
            "impossível treinar um modelo de 3 classes fiável. Precisa de mais histórico "
            "ou de barreiras (TRIPLE_BARRIER_TP_PCT/SL_PCT) menos extremas."
        )

    model = lgb.LGBMClassifier(n_estimators=100, max_depth=5, random_state=42, verbose=-1, class_weight="balanced")
    model.fit(X, y)

    os.makedirs(MODEL_OUTPUT_DIR, exist_ok=True)
    model_path = os.path.join(MODEL_OUTPUT_DIR, f"{symbol}.txt")
    model.booster_.save_model(model_path)
    return model_path


def predict_signal(symbol: str, latest_row: pd.Series, model_path: str | None = None,
                    regime_accuracy: dict | None = None) -> dict:
    """Produz o sinal direcional no formato de
    docs/ml_model_spec.md ("Output"). `latest_row` deve conter todas as
    `FEATURE_COLUMNS` (tipicamente a última linha de
    `add_time_features(features_df)`).

    devolve:
        dict - {"symbol", "direction" ("up"|"down"|"flat"), "probability",
            "regime", "regime_specific_accuracy"} — este último vem de
            `regime_accuracy` (calculado por `train_walk_forward`), não
            recalculado aqui; None se não fornecido.
    """
    model_path = model_path or os.path.join(MODEL_OUTPUT_DIR, f"{symbol}.txt")
    booster = lgb.Booster(model_file=model_path)

    X = latest_row[FEATURE_COLUMNS].to_frame().T
    probs = np.atleast_1d(np.asarray(booster.predict(X))[0])  # [P(classe 0=-1), P(classe 1=0), P(classe 2=+1)]

    if probs.shape[0] != 3:
        # Guard defensivo (RISK-09-style fail-closed): um modelo guardado
        # sem as 3 classes (ex.: treinado antes da verificação de
        # train_final_model existir) devolveria uma forma inesperada —
        # nunca interpretar isso silenciosamente como se fosse uma das
        # 3 classes.
        raise ValueError(
            f"predict_signal({symbol}): modelo em {model_path} devolveu {probs.shape[0]} "
            "probabilidade(s), esperadas 3 (classes -1/0/+1) — modelo incompatível ou corrompido."
        )

    predicted_class = int(np.argmax(probs))
    predicted_label = _CLASS_TO_LABEL[predicted_class]
    direction = {1.0: "up", -1.0: "down", 0.0: "flat"}[predicted_label]

    regime = int(latest_row["regime"])
    regime_acc = None
    if regime_accuracy is not None:
        regime_acc = regime_accuracy.get(str(regime))

    return {
        "symbol": symbol,
        "direction": direction,
        "probability": round(float(probs[predicted_class]), 4),
        "regime": regime,
        "regime_specific_accuracy": regime_acc,
    }


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, required=True,
                         help="Lista de símbolos separada por vírgula (ex.: EURUSD,AUDUSD)")
    parser.add_argument("--features-dir", type=str, default="output")
    parser.add_argument("--report-path", type=str, default="output/ml_model_report.json")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    report = {}

    for symbol in symbols:
        features_path = os.path.join(args.features_dir, f"features_{symbol}.parquet")
        if not os.path.exists(features_path):
            log.warning("Ficheiro de features não encontrado para %s: %s — a saltar.", symbol, features_path)
            continue

        log.info("A validar (walk-forward) %s...", symbol)
        result = train_walk_forward(symbol, features_path)
        report[symbol] = result

        agg = result["aggregate_stats"]
        if agg is None:
            log.info("%s: sem validação possível (amostra insuficiente).", symbol)
            continue
        log.info(
            "%s: %d trades simulados, accuracy=%.1f%%, win_rate=%.1f%%, profit_factor=%.2f, "
            "sharpe/trade=%.3f, retorno_total=%.2fR, drawdown_max=%.2fR",
            symbol, agg["total_trades"], agg["accuracy"] * 100, agg["win_rate"] * 100,
            agg["profit_factor"], agg["sharpe_per_trade"], agg["total_return_r"], agg["max_drawdown_r"],
        )

        log.info("A treinar modelo final (todo o histórico) para %s...", symbol)
        model_path = train_final_model(symbol, features_path)
        log.info("Modelo guardado em %s", model_path)

    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("Relatório completo guardado em %s", args.report_path)


if __name__ == "__main__":
    main()
