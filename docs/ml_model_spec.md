# docs/ml_model_spec.md

Especificação para `src/ml_model.py` — a camada 1 (ver
`ARCHITECTURE.md`). Aprende a partir das features da camada 0 e produz
um sinal direcional com confiança, condicionado ao regime detetado.

## Princípio orientador

Começar simples (gradient boosting sobre features tabulares) antes de
deep learning. Um modelo de árvores bem validado, com walk-forward
honesto, costuma bater um Transformer mal validado em dados financeiros
ruidosos — a maior fonte de "alpha" aqui não é a arquitetura do modelo, é
a qualidade da validação e das features. Migrar para deep learning
(LSTM, Temporal Fusion Transformer) só depois de ter uma baseline sólida
e suficiente histórico de dados.

## Input (vem da camada 0)

`features_<SYMBOL>.parquet`, colunas: `log_ret`, `vol_fast`, `vol_slow`,
`vol_ratio`, `zscore_price`, `range_pct`, `regime`. Adicionar features
extra aqui à medida que se identificarem (ex.: features cross-pair tipo
spread de `hedge_candidates.csv`, hora do dia / sessão de mercado
ativa — Londres/NY/Tóquio importa muito para scalping).

## Definição do alvo (label) — ponto crítico

Não usar simplesmente "retorno do próximo candle" como alvo — é
extremamente ruidoso. Usar o método de triple barrier (padrão em
finanças quantitativas): para cada ponto no tempo, definir uma barreira
de take-profit, uma de stop-loss e uma barreira de tempo máximo; o
rótulo é qual barreira foi atingida primeiro (+1, -1, ou 0 se foi a
barreira de tempo). Isto alinha o alvo do modelo com a realidade de como
o trade vai ser fechado, em vez de prever um número de retorno abstrato.

## Validação — não negociável

Walk-forward obrigatório: dividir o histórico em janelas sequenciais
(ex.: treina em 3 meses, testa no mês seguinte, desliza a janela, repete
sobre todo o histórico disponível). Nunca fazer split aleatório
train/test em série temporal — isso gera "fuga de informação" (o modelo
vê o futuro durante o treino) e os resultados parecem bons mas não
generalizam. Reportar sempre a distribuição de métricas ao longo das
janelas (não só a média) — um modelo que vai bem em 8 de 10 janelas e
muito mal em 2 é mais arriscado do que um consistentemente medíocre.

## Métricas a reportar (além de accuracy)

Accuracy direcional sozinha engana, porque um modelo pode acertar a
direção mais vezes mas perder dinheiro se os erros forem em momentos de
maior tamanho de posição. Reportar também: Sharpe ratio do retorno
simulado já com custos de transação aplicados, drawdown máximo,
profit factor (lucro bruto / prejuízo bruto), e taxa de acerto
condicionada ao regime (o modelo pode ser bom só num regime e péssimo
noutro — isto é informação valiosa para o motor de hedge/risco saber
quando confiar menos no sinal).

## Output (formato sugerido)

```python
{
    "timestamp": ...,
    "symbol": "EURUSD",
    "direction": "up" | "down" | "flat",
    "probability": 0.62,        # confiança do modelo
    "regime": 1,                 # vindo da camada 0
    "regime_specific_accuracy": 0.58,  # histórico do modelo neste regime
}
```

## Sobre "não depender de indicadores tradicionais"

O modelo pode (e provavelmente deve) usar versões transformadas de
indicadores comuns como features de entrada — RSI, médias móveis, etc.
não são proibidos, são só mais uma feature entre várias. O que muda é
que o modelo aprende os pesos e interações entre todas as features
(incluindo as não-tradicionais como z-score do spread cross-pair e
regime) em vez de uma regra fixa tipo "RSI < 30 = compra". Vale incluir
RSI/MACD/Bollinger como features de input ao lado das features
adaptativas da camada 0 — deixar o modelo decidir a importância de cada
uma em vez de decidir isso manualmente à partida.

## Riscos específicos a vigiar

Overfitting é o risco nº1 dado quão ruidosos são dados de forex em
timeframes curtos — com features suficientes, é trivial conseguir um
modelo que parece ótimo no histórico e não generaliza nada. Sinais de
alerta: accuracy muito alta (>65-70%) num timeframe curto deve ser
tratada com suspeita, não celebrada, até ser validada out-of-sample de
forma rigorosa. Não-estacionariedade significa que o modelo vai degradar
com o tempo — definir desde já uma cadência de retraining (ex.: semanal)
e um critério automático de "o modelo deixou de funcionar" (ex.: queda
do profit factor em produção abaixo de um limiar) que dispara retraining
ou desativação do sinal.
