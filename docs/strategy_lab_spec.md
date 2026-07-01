# docs/strategy_lab_spec.md

Especificação do laboratório de estratégias — a camada que gera
variações de parâmetros para a lógica de hedge (camada 2), testa cada
uma via backtest, valida com critérios objetivos, e mostra tudo numa UI
para validação humana antes de qualquer parâmetro ser promovido para
produção (`hedge_engine.py` real).

## Porquê isto existe

A spec do motor de hedge (`docs/hedge_engine_spec.md`) define a LÓGICA
(quando entrar, quando sair) mas deixa em aberto os valores exatos dos
limiares (`entry_threshold`, `exit_threshold`, `max_hold_bars`, etc.).
Escolher esses valores à mão, "a olho", é como adivinhar. O laboratório
automatiza essa procura: gera combinações, testa-as todas no histórico,
e só os parâmetros que passam num conjunto de critérios objetivos
(definidos por um trader sénior, não por intuição) ficam disponíveis
para usar de verdade.

## Componentes

- `src/backtest_engine.py` — simula a lógica de hedge sobre uma série
  histórica de dois pares dado um conjunto de parâmetros, devolve a
  lista de trades e estatísticas completas. Sem lookahead bias: o hedge
  ratio (beta) é recalculado periodicamente só com dados passados, e o
  z-score/correlação usam janelas móveis causais.
- `src/strategy_registry.py` — persiste cada estratégia testada (SQLite,
  `output/strategy_lab.db`): parâmetros, estado (aprovada/reprovada),
  estatísticas completas, e o histórico de trades em JSON.
- `src/strategy_generator.py` — orquestra o loop: gera parâmetros
  aleatórios -> testa -> regista -> se uma geração tiver poucas/nenhumas
  aprovadas, muta os parâmetros das melhores tentativas (mesmo que
  reprovadas) para a geração seguinte. Repete por N gerações.
- `dashboard.py` — UI em Streamlit para validação humana: tabela de
  todas as estratégias com estado e estatísticas, filtros, e detalhe por
  estratégia (parâmetros, motivo de reprovação se aplicável, curva de
  equity, histórico de trades).

## PnL em unidades "R", não em dinheiro

O backtest mede PnL em múltiplos do desvio-padrão do spread no momento
da entrada ("R"), não em dinheiro real. Isto é deliberado: isola a
qualidade da LÓGICA de entrada/saída do dimensionamento de posição
(quantos lotes, qual % de risco por trade), que é responsabilidade da
camada de risco (camada 3) e não deveria estar acoplada à decisão de
"esta lógica funciona ou não". Quando a camada de risco existir,
consome o parâmetro `position_size_pct` próprio, não algo calculado
aqui.

## Critérios de aprovação (gate, em `backtest_engine.DEFAULT_THRESHOLDS`)

| Critério | Limiar default | Porquê |
|---|---|---|
| Nº mínimo de trades | 20 | Abaixo disto, qualquer resultado é ruído estatístico, não edge real |
| Profit factor mínimo | 1.2 | Lucro bruto tem de exceder claramente o prejuízo bruto, não só empatar |
| Sharpe por trade mínimo | 0.15 | Consistência do retorno, não só magnitude |
| Drawdown máximo | 8.0 R | Limite de "dor" aceitável durante o teste |
| Retorno total | > 0 | Óbvio, mas explícito |

Estes valores são um ponto de partida, não verdades absolutas — ajustar
em `DEFAULT_THRESHOLDS` à medida que se valida com mais dados (ex.: em
dados reais da corretora, pode fazer sentido ser mais exigente).

## Como correr

```bash
python src/data_pipeline.py --mode synth          # ou --mode mt5
python src/strategy_generator.py --max-pairs 4 --n-generations 2
streamlit run dashboard.py                          # abre a UI no browser
```

## Próximo passo depois do laboratório

Quando uma estratégia aparecer como ✅ Aprovada no dashboard com
estatísticas que fazem sentido (nº de trades suficiente, profit factor
e Sharpe consistentes, drawdown aceitável), os parâmetros dela
(visíveis no painel de detalhe) são o ponto de partida para configurar
`src/hedge_engine.py` em produção — mas ainda passam pelo motor de risco
(camada 3) antes de qualquer execução real. Validar SEMPRE em dados
reais da corretora (não só sintéticos) antes disso — dados sintéticos
servem para testar que a lógica e a UI funcionam, não substituem
validação no histórico real.

## Limitações conhecidas (a melhorar)

- A procura de parâmetros é aleatória + mutação simples, não uma técnica
  de otimização mais sofisticada (ex.: optimização bayesiana). Suficiente
  para começar, vale revisitar se a procura demorar demasiado a encontrar
  estratégias válidas.
- Não há ainda separação explícita treino/validação dentro do próprio
  backtest (o backtest todo é "in-sample" relativo ao período fornecido).
  Antes de qualquer execução real, correr o mesmo conjunto de parâmetros
  aprovados num período de dados COMPLETAMENTE separado (out-of-sample)
  que não foi usado durante a geração/seleção.
