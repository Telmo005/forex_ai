---
name: quant-finance-math
description: Fórmulas, testes estatísticos e padrões de código Python para matemática quantitativa aplicada a forex - cointegração (Engle-Granger), filtro de Kalman para hedge ratio dinâmico, Hidden Markov Models para deteção de regime, Kelly criterion para dimensionamento de posição, e triple barrier labeling. Usa sempre esta skill ao escrever ou alterar qualquer lógica de hedge estatístico, deteção de regime, dimensionamento de posição, ou validação de modelo neste projeto - mesmo que o pedido não mencione explicitamente "matemática" ou "estatística".
---

# Quant Finance Math

Referência de implementação para a matemática usada nas camadas 0-3 do
projeto (ver `ARCHITECTURE.md` na raiz). Usa o glossário em
`docs/glossary.md` para definições conceptuais; esta skill foca-se em
código e fórmulas prontas a aplicar.

## Teste de cointegração (Engle-Granger)

```python
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm

score, pvalue, _ = coint(series_a, series_b)
# pvalue < 0.05 -> evidência de cointegração

X = sm.add_constant(series_b)
model = sm.OLS(series_a, X).fit()
beta = model.params.iloc[1]   # hedge ratio
spread = series_a - beta * series_b
zscore = (spread.iloc[-1] - spread.mean()) / spread.std()
```

Cuidados: usar sempre uma janela móvel (não o histórico inteiro) para
recalcular periodicamente — a relação de cointegração pode quebrar ao
longo do tempo. Para 3+ séries simultaneamente, usar teste de Johansen
(`statsmodels.tsa.vector_ar.vecm.coint_johansen`) em vez de pares
individuais de Engle-Granger.

## Filtro de Kalman para hedge ratio dinâmico

Em vez de recalcular o beta via OLS numa janela fixa (que muda
abruptamente entre janelas), o filtro de Kalman atualiza o beta de
forma suave a cada nova observação:

```python
import numpy as np

def kalman_hedge_ratio(series_a, series_b, delta=1e-4, ve=1e-3):
    """Beta dinâmico via filtro de Kalman (random walk no beta).
    delta controla quão rápido o beta pode mudar; ve é a variância de
    observação (ruído do spread)."""
    n = len(series_a)
    beta = np.zeros(n)
    P = 1.0          # variância da estimativa do beta
    Q = delta / (1 - delta)   # variância de processo

    beta[0] = series_a.iloc[0] / series_b.iloc[0]
    for t in range(1, n):
        # predição
        beta_pred = beta[t-1]
        P_pred = P + Q

        # atualização
        x = series_b.iloc[t]
        y = series_a.iloc[t]
        residual = y - beta_pred * x
        S = x**2 * P_pred + ve
        K = P_pred * x / S    # ganho de Kalman

        beta[t] = beta_pred + K * residual
        P = (1 - K * x) * P_pred

    return beta
```

Usar `pykalman` ou `filterpy` em produção em vez de reimplementar à mão
(o código acima é para entender a mecânica) — `filterpy.kalman.KalmanFilter`
dá mais controlo sobre matrizes de covariância multivariadas se mais
tarde se quiser estender para hedge entre 3+ pares.

## HMM para deteção de regime

```python
from hmmlearn.hmm import GaussianHMM
import numpy as np

# SEMPRE normalizar antes de treinar o HMM - escalas pequenas
# (retornos ~1e-4) causam instabilidade numérica e convergência
# degenerada (startprob_ com NaN).
obs = features[["log_ret", "vol_fast"]].values
obs_norm = (obs - obs.mean(axis=0)) / (obs.std(axis=0) + 1e-12)

model = GaussianHMM(n_components=3, covariance_type="diag",
                     n_iter=200, random_state=42, min_covar=1e-4)
model.fit(obs_norm)
regimes = model.predict(obs_norm)

# verificar convergência antes de confiar no output
assert not np.isnan(model.startprob_).any(), "HMM convergiu para estado degenerado"
```

Ter sempre um fallback (ex.: classificação por percentil de
volatilidade) para quando o HMM não converge de forma estável — ver
implementação em `src/data_pipeline.py::detect_regime` como referência.

## Kelly criterion (dimensionamento de posição)

```python
def kelly_fraction(win_rate: float, payoff_ratio: float, fraction: float = 0.3) -> float:
    """payoff_ratio = ganho médio / perda média (ex.: 1.5 = ganha 1.5x
    o que perde, em média). fraction = fração do Kelly completo a usar
    (0.25-0.5 é o intervalo típico em uso real, nunca 1.0)."""
    b = payoff_ratio
    p = win_rate
    q = 1 - p
    f_star = (b * p - q) / b
    return max(0.0, f_star * fraction)   # nunca negativo (nunca apostar contra o próprio edge)
```

Os parâmetros `win_rate` e `payoff_ratio` devem vir de validação
walk-forward out-of-sample, nunca do backtest in-sample (que
sobrestima sistematicamente o edge real).

## Triple barrier labeling (para treino do modelo de ML)

```python
import numpy as np

def triple_barrier_labels(prices, tp_pct: float, sl_pct: float, max_bars: int):
    """Para cada ponto t, olha para a frente até max_bars e devolve:
    +1 se TP atingido primeiro, -1 se SL atingido primeiro, 0 se nem
    um nem outro dentro de max_bars (barreira de tempo)."""
    n = len(prices)
    labels = np.zeros(n)
    for t in range(n - max_bars):
        entry = prices[t]
        window = prices[t+1 : t+1+max_bars]
        tp_level = entry * (1 + tp_pct)
        sl_level = entry * (1 - sl_pct)
        hit_tp = np.where(window >= tp_level)[0]
        hit_sl = np.where(window <= sl_level)[0]
        first_tp = hit_tp[0] if len(hit_tp) else np.inf
        first_sl = hit_sl[0] if len(hit_sl) else np.inf
        if first_tp < first_sl:
            labels[t] = 1
        elif first_sl < first_tp:
            labels[t] = -1
        # senão fica 0 (barreira de tempo)
    return labels
```

## Walk-forward split (validação)

```python
def walk_forward_splits(n_samples: int, train_size: int, test_size: int, step: int):
    """Gera (train_idx, test_idx) sequenciais sem sobreposição temporal.
    NUNCA usar train_test_split/KFold aleatório em dados de série
    temporal financeira - introduz fuga de informação do futuro."""
    start = 0
    while start + train_size + test_size <= n_samples:
        train_idx = range(start, start + train_size)
        test_idx = range(start + train_size, start + train_size + test_size)
        yield train_idx, test_idx
        start += step
```

## Erros comuns a vigiar nesta área

Confundir correlação com cointegração (ver `docs/glossary.md`).
Calcular métricas de validação (accuracy, Sharpe) sobre o conjunto de
treino em vez do conjunto de teste out-of-sample. Esquecer de incluir
custos de transação no cálculo de Sharpe/profit factor em backtest.
Usar Kelly completo (fraction=1.0) em produção — implica drawdowns que
a maioria das pessoas não tolera, mesmo sendo matematicamente "ótimo".
