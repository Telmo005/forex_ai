# docs/hedge_engine_spec.md

Especificação para `src/hedge_engine.py` — a camada 2 (ver
`ARCHITECTURE.md`). Consome o output da camada 0 (e, quando existir, o
sinal da camada 1) e produz ordens propostas. Não toca em dimensionamento
de risco nem em execução — isso é a camada 3/4.

## Input

- `output/hedge_candidates.csv` (gerado por `data_pipeline.py`): colunas
  `pair_a`, `pair_b`, `correlation`, `coint_pvalue`, `is_cointegrated`,
  `hedge_ratio_beta`, `spread_zscore`.
- `output/features_<SYMBOL>.parquet`: série temporal por símbolo, para
  recalcular o z-score do spread em tempo real (não só no snapshot do
  pipeline).
- (Futuro) sinal direcional da camada 1, por símbolo.

## Lógica de entrada (abrir hedge)

Abrir uma posição de hedge entre `pair_a` e `pair_b` quando, em
simultâneo:
1. `is_cointegrated == True` na janela mais recente (recalcular, não
   confiar num snapshot antigo — cointegração pode quebrar).
2. `abs(spread_zscore) >= ENTRY_THRESHOLD` (sugestão inicial: 2.0).
3. `correlation` ainda acima de um mínimo (sugestão: 0.6) — se a
   correlação já estiver a cair antes mesmo do z-score disparar, é sinal
   de que a relação está a degradar-se, não vale a pena entrar.

Direção das pernas: se `spread = price_a - beta * price_b` está acima da
média (`spread_zscore > 0`), a expectativa é reversão para baixo do
spread -> short em `pair_a`, long em `pair_b` (na proporção `beta`).
Se `spread_zscore < 0`, inverter.

## Lógica de saída (fechar/desfazer hedge)

Fechar a posição quando QUALQUER uma destas condições disparar:
1. **Reversão alcançada**: `abs(spread_zscore) <= EXIT_THRESHOLD`
   (sugestão: 0.3..0.5) — a divergência que motivou a entrada já se
   desfez.
2. **Quebra de correlação**: `correlation` recalculada cai abaixo de um
   mínimo (sugestão: 0.4) — a premissa do hedge deixou de ser válida,
   sair independentemente do z-score, mesmo com prejuízo controlado.
3. **Stop de tempo**: posição aberta há mais de `MAX_HOLD_BARS` (definir
   empiricamente por timeframe — para M5 scalping, algo como 50-100
   barras é um ponto de partida razoável, ajustar com backtest) sem
   reverter — sinal de que a tese não está a funcionar como esperado.
4. **Stop de risco**: definido pela camada 3 (motor de risco), não por
   aqui — este módulo só propõe, não tem autoridade de override sobre
   limites de risco.

## Output (formato sugerido)

Cada decisão deve ser um registo estruturado, não uma ordem direta de
execução:

```python
{
    "timestamp": ...,
    "action": "open_hedge" | "close_hedge",
    "pair_a": "EURUSD", "pair_b": "GBPUSD",
    "side_a": "sell", "side_b": "buy",
    "hedge_ratio": 1.057,
    "trigger": "spread_zscore",   # motivo, para log/auditoria
    "trigger_value": 2.34,
    "confidence_ml": None,        # preenchido quando a camada 1 existir
}
```

Este registo vai para o motor de risco (camada 3), que decide o tamanho
real e aprova ou rejeita.

## Coisas a evitar (erros comuns nesta lógica)

- Não usar o `spread_zscore` do snapshot de `hedge_candidates.csv` como
  se fosse em tempo real — ele é calculado no momento em que o pipeline
  correu. Em produção, recalcular a cada novo candle a partir do
  parquet/stream mais recente.
- Não tratar cointegração como uma propriedade permanente do par —
  reavaliar periodicamente (ex.: a cada N barras), porque relações entre
  moedas mudam com o tempo (mudanças de política monetária, etc.).
- Não confundir correlação alta com cointegração — dois pares podem
  estar altamente correlacionados no curto prazo sem serem cointegrados
  (sem reversão à média estatisticamente significativa), o que torna o
  hedge baseado só em correlação mais arriscado.

## Testes sugeridos antes de avançar para a camada 3

- Backtest walk-forward isolado deste módulo: dado o histórico completo
  de `features_*.parquet`, simular apenas a lógica de entrada/saída e
  medir quantas vezes o spread reverteu vs quantas vezes bateu o stop de
  tempo ou a quebra de correlação. Isto valida a lógica antes de
  qualquer dinheiro (real ou demo) estar envolvido.
