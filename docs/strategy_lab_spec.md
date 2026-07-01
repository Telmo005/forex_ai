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

## Modelo de custos de transação

`src/backtest_engine.py` expõe `DEFAULT_COST_PARAMS` (dict por símbolo),
`COST_MODEL_VERSION` ("placeholder-v1"), `resolve_cost_params(pair_a, pair_b)`
e `apply_transaction_costs()`. O modelo está totalmente ligado (plan 01-02
desta fase): `run_hedge_backtest(price_a, price_b, params, cost_params=...)`
subtrai spread, slippage e comissão de `pnl_r` no momento em que cada trade
fecha — dentro do próprio loop de simulação, não como ajuste posterior — e
`strategy_generator.run_strategy_lab` resolve `cost_params` por par e
passa-os em TODO candidato testado, para que nenhum caminho de aprovação
(gate manual do dashboard ou qualquer gate automático futuro) veja números
cost-blind (CLAUDE.md regra 4 / VALID-02).

**Componentes modelados** (CLAUDE.md regra 4 — custos entram sempre no
backtest):
- `spread_cost` — custo de spread, em unidades de preço (pips × point size
  do símbolo), não em pips crus, para compor diretamente com
  `spread = price_a - beta * price_b`.
- `slippage_cost` — slippage modelado, mesma unidade de preço.
- `commission_per_lot` — comissão USD por lote round-turn.
- `reference_lot_size` — tamanho de posição assumido (placeholder, ex.: 1
  lote standard) usado APENAS para exprimir a comissão em unidades "R"
  durante esta validação — não é o dimensionamento real de posição, que
  continua a ser responsabilidade exclusiva da camada de risco (secção
  "PnL em unidades R" acima). A conversão de $/lote para "R" passa primeiro
  por `STANDARD_LOT_CONTRACT_SIZE` (100.000 unidades da divisa base, a
  convenção universal de contrato forex) para obter unidades de preço, só
  depois dividindo por `entry_std` — dividir $/lote diretamente por
  `entry_std` misturaria dólares com desvios-padrão de preço.

**Origem dos valores atuais — placeholders, não dados reais.** Os valores em
`DEFAULT_COST_PARAMS` vêm de intervalos publicados de mercado para pares
forex major (~0.1-3 pips de spread, ~$2-7/lote de comissão round-turn,
~1-10 pips de slippage — ver `01-RESEARCH.md` desta fase), não da corretora
real do utilizador. `COST_MODEL_VERSION = "placeholder-v1"` marca esta
proveniência explicitamente e é persistido por estratégia em
`strategy_registry` (`cost_model_version`) e mostrado no dashboard, para que
qualquer estratégia testada com estes placeholders seja distinguível de uma
futura revalidação com custos reais da corretora. Cada entrada no dict tem
um comentário inline "placeholder" no código-fonte.

**Quando substituir.** Assim que existir uma ligação MT5 demo validada
(`data_pipeline.fetch_mt5`), os valores devem ser recalculados a partir de
`symbol_info().spread` / `.point` / `.trade_tick_value` reais da corretora,
e `COST_MODEL_VERSION` deve ser incrementado (ex.: `"broker-calibrated-v1"`)
para que qualquer estratégia já registada com o modelo antigo seja
distinguível de uma revalidada com custos reais.

`resolve_cost_params(pair_a, pair_b)` soma o custo das duas pernas do hedge
(cada perna paga o seu próprio spread/slippage/comissão) e devolve um único
dict pronto a passar para `run_hedge_backtest()`.

## Metodologia walk-forward (CLAUDE.md regra 2 e regra 3 — gatilho exato)

`src/backtest_engine.py` expõe `walk_forward_validate(price_a, price_b, params,
cost_params=None, config=None)` (plan 01-03 desta fase, VALID-01). É
**revalidação de parâmetros já fixos/aprovados**, nunca reotimização — os
`params` chegam do laboratório (ou de uma estratégia já ✅ Aprovada no
dashboard) e `walk_forward_validate()` nunca os refita nem muta por fold; o
loop de mutação de `strategy_generator.py` está fora de âmbito aqui.

**Tipo de janela: ROLLING, não ancorada/expansível.** `TimeSeriesSplit` do
scikit-learn tem por padrão uma janela de treino que só cresce (ancorada).
Isso não corresponde à tese deste projeto (regime de mercado dependente do
tempo, scalping M5) — dados de treino demasiado antigos deixam de ser
representativos do regime atual. `walk_forward_validate()` força
`max_train_size` para obter janelas de treino de tamanho FIXO que deslizam no
tempo (rolling), nunca ancoradas.

**Parâmetros fixos exatos** (`backtest_engine.WALK_FORWARD_CONFIG`):

| Parâmetro | Valor | Significado |
|---|---|---|
| `n_splits` | 5 | Nº de folds sequenciais out-of-sample |
| `max_train_size` | 5000 barras | Tamanho FIXO da janela de treino — garante rolling, não ancorado |
| `gap` | 0 | Sem gap treino/teste: os parâmetros já estão fixos, não há refit que possa vazar informação através da fronteira treino/teste |
| `window_type` | `"rolling"` | Documentado explicitamente para não mascarar como ancorado |

**Gate por fold (RELAXADO)** (`backtest_engine.FOLD_THRESHOLDS`): cada fold
individual só precisa de (a) retorno líquido de custos positivo
(`total_return_r > 0`) e (b) nº de trades >= `min_trades_per_fold` (= **6**).
Este valor é DISTINTO do `DEFAULT_THRESHOLDS["min_trades"]` (=20) agregado —
não é o agregado dividido pelo nº de folds. Um fold com 5000 barras de
treino ainda precisa de produzir uma amostra de trades minimamente
defensável por si só (6 é o ponto médio conservador da gama 5-8 recomendada
para não deixar passar folds de 2-3 trades — ruído, não edge — só porque o
total agregado cumpre o limiar). O gate relaxado é expresso reusando
`validate_strategy()` com um `thresholds` dict próprio (sem herdar
`min_profit_factor`/`min_sharpe`/`max_drawdown_r` do agregado) — existe um
único caminho de validação no código, não dois divergentes.

**Gate agregado (COMPLETO):** os trades out-of-sample de TODOS os folds são
concatenados, `compute_stats()` roda sobre essa amostra agregada, e
`validate_strategy()` aplica o `DEFAULT_THRESHOLDS` inteiro (profit factor
>= 1.2, Sharpe >= 0.15, drawdown <= 8.0R, min_trades >= 20) — os mesmos
critérios de aprovação inicial da estratégia, agora sobre dados
out-of-sample.

`overall_passed` só é `True` se TODOS os folds passarem o gate relaxado E o
agregado passar o `DEFAULT_THRESHOLDS` completo.

**Custos sempre presentes.** Cada fold chama o mesmo `run_hedge_backtest()`
cost-aware (plan 01-02) — `cost_params` é repassado tal-e-qual a cada fold,
nunca só ao primeiro ou só ao agregado, para que VALID-01 (walk-forward) e
VALID-02 (custos) nunca se percam um do outro: um veredito walk-forward é
sempre net-of-cost em todos os folds.

**`revalidated_on_real_data` é distinto de `wf_passed`** (CLAUDE.md regra 7).
`walk_forward_validate()` em si é agnóstico à origem dos dados — corre da
mesma forma sobre `price_a`/`price_b` sintéticos ou reais. `wf_passed`
(persistido via `strategy_registry.save_walk_forward_result()`) regista só
se o veredito passou, sem indicar a origem dos dados. `revalidated_on_real_data`
só deve ser marcado `True` depois de uma corrida CONFIRMADA contra dados
reais da corretora (`data_pipeline.py --mode mt5`, nunca `--mode synth`) —
nunca inferido a partir de `wf_passed`. Uma estratégia pode ter
`wf_passed=True` e `revalidated_on_real_data=False` (passou walk-forward só
em sintético) — o futuro gate de produção (camada 3, `hedge_engine.py`) deve
exigir AMBAS as flags verdadeiras antes de aceitar os parâmetros.

**Nota sobre a escolha rolling vs. ancorada.** Esta decisão foi adotada de
uma assunção de pesquisa (RESEARCH.md Assumption A2) e não foi reconfirmada
explicitamente numa etapa `discuss-phase` com o utilizador. Se esta
assunção estiver errada para este projeto, `WALK_FORWARD_CONFIG["max_train_size"]
= None` restaura o comportamento ancorado/expansível default do
scikit-learn (e `window_type` deve ser atualizado para refletir isso).

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
- ~~Não há ainda separação explícita treino/validação dentro do próprio
  backtest~~ — **RESOLVIDO nos plans 01-03 e 01-04 desta fase (VALID-01).**
  `backtest_engine.walk_forward_validate()` revalida parâmetros já
  fixos/aprovados em folds sequenciais rolling out-of-sample (ver secção
  "Metodologia walk-forward" acima), e `src/revalidate_walk_forward.py`
  (plan 01-04) é o ponto de entrada executável que corre esse mecanismo
  contra os dados de mercado já em disco para cada estratégia ✅ Aprovada,
  persistindo o veredito (`wf_passed`, `wf_fold_results`) via
  `save_walk_forward_result()`; o dashboard mostra o detalhe fold-a-fold e
  um badge de revalidação. O gap de metodologia está fechado — o que
  resta é um requisito de ELEGIBILIDADE PARA PRODUÇÃO, não de mecanismo:
  VALID-01 só é considerado satisfeito para uma estratégia quando
  `revalidated_on_real_data=True`, o que exige uma corrida CONFIRMADA
  contra dados reais (`data_pipeline.py --mode mt5`, nunca `--mode synth`
  ou fallback CSV-import). Uma estratégia com `wf_passed=True` mas
  `revalidated_on_real_data=False` NÃO está pronta para produção — ver
  "Metodologia walk-forward" acima e o registo de estado real-vs-fallback
  em `.planning/phases/01-walk-forward-cost-aware-validation/01-04-SUMMARY.md`.
- ~~O backtest não modela custos de transação~~ — **RESOLVIDO no plan
  01-02 desta fase (VALID-02).** `run_hedge_backtest()` subtrai spread,
  slippage e comissão de `pnl_r` no momento em que cada trade fecha (ver
  secção "Modelo de custos de transação" acima), `strategy_generator.py`
  resolve e passa `cost_params` para todo o candidato testado, e o
  dashboard mostra só métricas net-of-cost (sem toggle para ver números
  cost-blind). O gap que resta é de CALIBRAÇÃO, não de wiring: os valores
  em `DEFAULT_COST_PARAMS` continuam a ser placeholders (`COST_MODEL_VERSION
  = "placeholder-v1"`) pendentes de dados reais da corretora via
  `symbol_info()` — ver "Quando substituir" acima.
