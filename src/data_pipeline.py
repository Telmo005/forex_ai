"""
data_pipeline.py
=================
Camada 0 do sistema: ingestão de dados + deteção automática de relações
entre pares (candidatos a hedge) + deteção de regime de mercado.

Esta peça é a fundação. Tudo o resto (motor de hedge, modelo de ML,
motor de risco) consome o output daqui. Por isso ela já faz mais do
que "baixar candles": ela varre os pares disponíveis e calcula, sem
intervenção manual:

  1. Features de preço (retornos, volatilidade rolante, z-score) —
     substituem indicadores fixos tipo RSI por estatísticas adaptativas.
  2. Matriz de correlação rolante entre todos os pares.
  3. Teste de cointegração (Engle-Granger) par a par -> ranking de
     candidatos a hedge estatístico, com a razão de hedge (beta) já
     calculada via regressão.
  4. Deteção de regime de mercado via Hidden Markov Model (HMM) sobre
     retornos e volatilidade -> rótulo de regime (ex.: 0 = lateral/
     baixa vol, 1 = tendência/alta vol) que o resto do sistema usa
     para saber QUANDO uma estratégia de scalping é apropriada.

Funciona em dois modos:
  - MT5  : liga-se ao terminal MetaTrader5 (Windows, terminal aberto e
           logado na tua corretora) e puxa dados reais.
  - SYNTH: gera dados sintéticos correlacionados, para testares toda a
           lógica (cointegração, regime, etc.) sem depender do MT5.
           Útil para desenvolver em qualquer máquina antes de ligar à
           conta real.

Uso:
    python data_pipeline.py --mode synth
    python data_pipeline.py --mode mt5 --login 12345 --password "xxx" --server "Broker-Demo"
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm

try:
    from hmmlearn.hmm import GaussianHMM
    _HAS_HMM = True
except ImportError:
    _HAS_HMM = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("data_pipeline")


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    symbols: list[str] = field(default_factory=lambda: [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF"
    ])
    timeframe: str = "M5"          # M1, M5, M15, H1, etc. (mapeado para MT5 depois)
    n_bars: int = 20_000           # histórico a puxar por símbolo
    corr_window: int = 200         # janela da correlação rolante (em barras)
    coint_window: int = 1000       # janela usada para o teste de cointegração
    coint_pvalue_threshold: float = 0.05
    hmm_n_states: int = 3          # nº de regimes a detetar
    output_dir: str = "./output"


# --------------------------------------------------------------------------
# Camada de ingestão: MT5 real ou sintético
# --------------------------------------------------------------------------

TF_MAP_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}


def fetch_mt5(symbol: str, timeframe: str, n_bars: int,
               login: Optional[int] = None, password: Optional[str] = None,
               server: Optional[str] = None) -> pd.DataFrame:
    """Puxa histórico real via terminal MT5. Só funciona em Windows com o
    terminal instalado e a biblioteca MetaTrader5 (`pip install MetaTrader5`).
    """
    import MetaTrader5 as mt5  # import local: só é exigido neste modo

    if not mt5.initialize(login=login, password=password, server=server):
        raise RuntimeError(f"Falha ao inicializar MT5: {mt5.last_error()}")

    tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}")
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, n_bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Sem dados retornados para {symbol}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["time", "open", "high", "low", "close", "volume"]].set_index("time")


def fetch_synthetic(symbol: str, timeframe: str, n_bars: int, seed: int, beta: float,
                     market_factor: np.ndarray, regime_switch: np.ndarray,
                     idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Gera uma série sintética a partir de um fator de mercado PARTILHADO
    (mesmo array para todos os símbolos) mais ruído idiossincrático
    estacionário. Isto garante, por construção, que pares com beta != 0
    ficam cointegrados entre si: a combinação linear correta (via beta)
    cancela o fator comum não-estacionário e sobra só ruído estacionário.
    O índice de tempo (`idx`) também é PARTILHADO entre símbolos -- gerar
    um `pd.date_range(end=datetime.utcnow(), ...)` separadamente por
    símbolo introduz desalinhamento de timestamps entre séries (cada
    chamada tem um "agora" com microssegundos diferentes), o que quebra
    operações de pandas baseadas em índice (ex.: `a - beta * b`) de forma
    silenciosa, devolvendo NaN em vez de erro.
    """
    rng = np.random.default_rng(seed)

    # ruído idiossincrático ESTACIONÁRIO (não acumulado) -> preserva cointegração
    idio = rng.normal(0, 0.0015, n_bars)

    # drift de tendência ligado ao regime PARTILHADO, escalado por beta para
    # não introduzir uma componente I(1) extra e independente por símbolo
    drift = np.cumsum(regime_switch * beta * rng.normal(0.00002, 0.000005, n_bars))

    log_price = np.log(1.10) + beta * market_factor + idio + drift
    close = np.exp(log_price)

    noise = rng.normal(0, 0.00008, n_bars)
    high = close + np.abs(noise)
    low = close - np.abs(noise)
    open_ = close + rng.normal(0, 0.00005, n_bars)
    volume = rng.integers(50, 500, n_bars)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    return df


def load_all_symbols(cfg: PipelineConfig, mode: str, mt5_creds: dict) -> dict[str, pd.DataFrame]:
    data = {}
    # betas arbitrários só para a versão sintética criar estrutura de
    # correlação plausível entre os pares
    synth_betas = {
        "EURUSD": 1.0, "GBPUSD": 0.85, "USDJPY": -0.6, "AUDUSD": 0.7,
        "USDCAD": -0.5, "NZDUSD": 0.6, "USDCHF": -0.9,
    }

    if mode == "synth":
        # fator de mercado, regime e ÍNDICE DE TEMPO partilhados por todos os símbolos
        market_rng = np.random.default_rng(7)
        market_factor = np.cumsum(market_rng.normal(0, 0.0006, cfg.n_bars))
        regime_switch = (np.sin(np.linspace(0, 6 * np.pi, cfg.n_bars)) > 0).astype(float)
        minutes = TF_MAP_MINUTES.get(cfg.timeframe, 5)
        shared_idx = pd.date_range(end=datetime.utcnow(), periods=cfg.n_bars, freq=f"{minutes}min")

    for i, sym in enumerate(cfg.symbols):
        log.info(f"Carregando {sym} ({mode})...")
        if mode == "mt5":
            df = fetch_mt5(sym, cfg.timeframe, cfg.n_bars, **mt5_creds)
        else:
            beta = synth_betas.get(sym, 0.5)
            df = fetch_synthetic(sym, cfg.timeframe, cfg.n_bars, seed=42 + i, beta=beta,
                                  market_factor=market_factor, regime_switch=regime_switch,
                                  idx=shared_idx)
        data[sym] = df
    return data


# --------------------------------------------------------------------------
# Feature engineering (substitui indicadores fixos por estatísticas adaptativas)
# --------------------------------------------------------------------------

def engineer_features(df: pd.DataFrame, corr_window: int) -> pd.DataFrame:
    out = df.copy()
    out["log_ret"] = np.log(out["close"]).diff()
    out["vol_fast"] = out["log_ret"].rolling(20).std()
    out["vol_slow"] = out["log_ret"].rolling(corr_window).std()
    out["vol_ratio"] = out["vol_fast"] / out["vol_slow"]          # proxy de "compressão/expansão" de volatilidade
    out["zscore_price"] = (
        out["close"] - out["close"].rolling(corr_window).mean()
    ) / out["close"].rolling(corr_window).std()
    out["range_pct"] = (out["high"] - out["low"]) / out["close"]
    return out.dropna()


# --------------------------------------------------------------------------
# Deteção de candidatos a hedge: correlação + cointegração + hedge ratio
# --------------------------------------------------------------------------

def scan_hedge_candidates(data: dict[str, pd.DataFrame], cfg: PipelineConfig) -> pd.DataFrame:
    """Para cada par de símbolos, calcula:
      - correlação rolante (últimas `corr_window` barras)
      - teste de cointegração de Engle-Granger nas últimas `coint_window` barras
      - hedge ratio (beta) via OLS, para dimensionar a perna de cobertura

    Retorna um DataFrame ordenado pelos melhores candidatos (menor p-value,
    desde que abaixo do threshold).
    """
    rows = []
    closes = {s: data[s]["close"] for s in data}

    for sym_a, sym_b in combinations(data.keys(), 2):
        a = closes[sym_a].iloc[-cfg.coint_window:]
        b = closes[sym_b].iloc[-cfg.coint_window:]
        n = min(len(a), len(b))
        if n < cfg.coint_window // 2:
            continue
        a, b = a.iloc[-n:].reset_index(drop=True), b.iloc[-n:].reset_index(drop=True)

        try:
            score, pvalue, _ = coint(a, b)
        except Exception as e:
            log.warning(f"Cointegração falhou para {sym_a}/{sym_b}: {e}")
            continue

        corr = a.iloc[-cfg.corr_window:].corr(b.iloc[-cfg.corr_window:])

        # hedge ratio via OLS: a = alpha + beta * b
        X = sm.add_constant(b)
        model = sm.OLS(a, X).fit()
        beta = model.params.iloc[1]

        # spread atual e z-score do spread (gatilho de entrada/saída do hedge)
        spread = a - beta * b
        spread_z = (spread.iloc[-1] - spread.mean()) / spread.std()

        rows.append({
            "pair_a": sym_a, "pair_b": sym_b,
            "correlation": round(corr, 3),
            "coint_pvalue": round(pvalue, 4),
            "is_cointegrated": pvalue < cfg.coint_pvalue_threshold,
            "hedge_ratio_beta": round(beta, 4),
            "spread_zscore": round(spread_z, 3),
        })

    result = pd.DataFrame(rows).sort_values("coint_pvalue")
    return result.reset_index(drop=True)


# --------------------------------------------------------------------------
# Deteção de regime de mercado (HMM)
# --------------------------------------------------------------------------

def detect_regime(features: pd.DataFrame, n_states: int) -> pd.Series:
    """Treina um Gaussian HMM sobre [retorno, volatilidade] (normalizados)
    para rotular cada barra com um estado de regime. Sem HMM disponível ou
    em caso de falha numérica de convergência, cai para uma heurística
    simples baseada em percentis de volatilidade -- nunca deixa o pipeline
    parar por causa do regime detector.
    """
    raw_obs = features[["log_ret", "vol_fast"]].values
    obs = (raw_obs - raw_obs.mean(axis=0)) / (raw_obs.std(axis=0) + 1e-12)

    if _HAS_HMM:
        try:
            model = GaussianHMM(n_components=n_states, covariance_type="diag",
                                 n_iter=200, random_state=42, min_covar=1e-4)
            model.fit(obs)
            states = model.predict(obs)
            if not np.isnan(model.startprob_).any():
                return pd.Series(states, index=features.index, name="regime")
            log.warning("HMM convergiu para estado degenerado, a usar fallback de volatilidade.")
        except Exception as e:
            log.warning(f"HMM falhou ({e}), a usar fallback de volatilidade.")

    # fallback sem hmmlearn ou se o HMM não convergiu de forma estável
    vol = features["vol_fast"]
    bins = vol.quantile([0.33, 0.66]).values
    states = np.digitize(vol, bins)
    return pd.Series(states, index=features.index, name="regime")


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def run_pipeline(cfg: PipelineConfig, mode: str, mt5_creds: dict) -> None:
    os.makedirs(cfg.output_dir, exist_ok=True)

    raw = load_all_symbols(cfg, mode, mt5_creds)

    feature_sets = {}
    for sym, df in raw.items():
        feat = engineer_features(df, cfg.corr_window)
        feat["regime"] = detect_regime(feat, cfg.hmm_n_states)
        feature_sets[sym] = feat
        out_path = os.path.join(cfg.output_dir, f"features_{sym}.parquet")
        feat.to_parquet(out_path)
        log.info(f"{sym}: {len(feat)} barras processadas, regime atual = {feat['regime'].iloc[-1]} -> {out_path}")

    log.info("Varrendo candidatos a hedge (correlação + cointegração)...")
    hedge_table = scan_hedge_candidates(raw, cfg)
    hedge_path = os.path.join(cfg.output_dir, "hedge_candidates.csv")
    hedge_table.to_csv(hedge_path, index=False)

    log.info(f"\n{hedge_table.to_string(index=False)}")
    log.info(f"Tabela de candidatos a hedge guardada em {hedge_path}")

    n_coint = hedge_table["is_cointegrated"].sum()
    log.info(f"Pipeline concluído. {n_coint} par(es) cointegrado(s) encontrados de {len(hedge_table)} testados.")


def main():
    parser = argparse.ArgumentParser(description="Pipeline de dados forex_ai")
    parser.add_argument("--mode", choices=["mt5", "synth"], default="synth")
    parser.add_argument("--login", type=int, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--server", type=str, default=None)
    args = parser.parse_args()

    cfg = PipelineConfig()
    mt5_creds = {"login": args.login, "password": args.password, "server": args.server}
    run_pipeline(cfg, args.mode, mt5_creds)


if __name__ == "__main__":
    main()
