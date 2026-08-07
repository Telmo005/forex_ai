# ROADMAP.md

## Estado atual (atualizar sempre que avançares)

| Camada | Componente | Estado | Ficheiro |
|---|---|---|---|
| 0 | Pipeline de dados | ✅ Feito e testado (modo sintético) | `src/data_pipeline.py` |
| 0 | Ligação MT5 real | ✅ Confirmado 2026-07-23 — 19.800 barras/símbolo, 1 par cointegrado (USDJPY/NZDUSD, p=0.044) | `src/data_pipeline.py --mode mt5` |
| 0.5 | Motor de backtest (sem lookahead) | ✅ Feito e testado | `src/backtest_engine.py` |
| 0.5 | Registo de estratégias (SQLite) | ✅ Feito e testado | `src/strategy_registry.py` |
| 0.5 | Gerador de estratégias (gera→testa→muta) | ✅ Feito e testado | `src/strategy_generator.py` |
| 0.5 | Dashboard de validação (UI) | ✅ Feito e testado | `dashboard.py` |
| 1 | Modelo de ML | ⚠️ Construído e validado 2026-07-29 — infraestrutura completa (triple barrier, walk-forward, LightGBM, 14 símbolos reais), mas **sem edge direcional demonstrado** (accuracy geral 80% vem só de acertar "sem movimento", a accuracy nas raras chamadas de direção é ~10-25%, ao nível do acaso). NÃO ligado ao motor de hedge — seria ruído. | `src/ml_model.py` |
| 2 | Motor de hedge (produção) | ✅ Feito e testado (25 testes, feed sintético via parquet) | `src/hedge_engine.py` |
| 3 | Motor de risco (Python) | ✅ Feito e testado | `src/risk_engine.py` |
| 3 | Motor de risco (MQL5) | ✅ Feito e testado (19/19 RiskGuardTests) | `mql5/RiskGuard.mqh` |
| 4 | Expert Advisor | ✅ Validado ponta-a-ponta em conta DEMO 2026-07-20 (abriu hedge EURUSD/GBPUSD com SL correto via sinal de teste) | `mql5/ScalpingEA.mq5` |
| 4 | Ponte de sinais Python->MQL5 | ✅ Feito e testado — SignalBridgeTests 22/22 em MetaEditor (2026-07-20) | `src/signal_bridge.py`, `mql5/SignalBridge.mqh` |
| 3.5 | Monitor de divergência (ao vivo vs validado) | ✅ Feito e testado 2026-07-30 — regista operações reais, compara com o walk-forward, aciona kill-switch se divergir mal (nunca aumenta risco) | `src/trade_ledger.py`, `src/divergence_monitor.py` |
| 4 | Alertas (ALERT-01, D-11) | ✅ Feito e testado 2026-07-30 — Telegram + email, kill-switch e drawdown≥80% do limite. **Precisa de configuração tua** (variáveis de ambiente) antes de enviar alguma coisa de verdade | `src/alerts.py` |
| 0.5 | Motor de descoberta evolutiva (busca ampliada + novas famílias) | ✅ Feito e testado 2026-07-31 — população/torneio/crossover/mutação/elitismo real (não só "mutar os 3 melhores"), 3 famílias novas (kalman, vol_scaled_exit, asymmetric_bands) além do molde zscore, diário de texto (`output/strategy_lab_journal.md`) com motivo de falha dominante por par+tipo | `src/strategy_evolution.py`, `src/strategy_variants.py` |
| 0.5 | Busca contínua em segundo plano | ✅ Feito e testado 2026-07-31 — loop indefinido (ciclos de busca evolutiva + revalidação walk-forward automática), nunca decide nada sozinho: só populam a base de dados; a elegibilidade continua a exigir os mesmos gates de sempre | `scripts/run_continuous_strategy_search.py` |
| 2/4 | Troca dinâmica de estratégia em produção (hot-swap) | ✅ Feito e testado 2026-07-31 — `run_hedge_loop()` fixa a estratégia no momento da ENTRADA (nunca troca a meio de um trade aberto) e recarrega `eligible_strategies` periodicamente; `select_strategy_for_pair()` ganhou `policy="best_oos_profit_factor"` (melhor profit factor out-of-sample, não só mais recente). **Critério inegociável**: só promove estratégias que já passam TODOS os gates existentes (in-sample + walk-forward + dados reais) — nunca um candidato ainda em busca | `src/hedge_engine.py`, `scripts/run_live_hedge_loop.py` (`--reload-every-bars`, `--selection-policy`) |
| 0.5 | Visualização da lógica da estratégia (dashboard) | ✅ Feito e testado 2026-07-31 — z-score do spread com bandas de entrada/saída tracejadas e pontos reais de entrada/saída, por estratégia | `dashboard.py`, `strategy_variants.compute_signal_series_for_type` |
| - | Backtest walk-forward out-of-sample | ❌ Por fazer | (revalidar estratégias aprovadas em período separado) |

## Histórico de decisões

- **2026-06-30**: Decidido usar Engle-Granger (não Johansen) para o
  teste de cointegração inicial — mais simples, suficiente para pares
  (Johansen só compensa quando se testam 3+ séries em conjunto).
- **2026-06-30**: HMM com fallback para classificação por percentil de
  volatilidade quando o HMM não converge de forma estável — evita que o
  pipeline pare por causa do detetor de regime.
- **2026-06-30**: Estrutura em 5 camadas separadas (dados / ML / hedge /
  risco / execução) em vez de um modelo monolítico — ver
  `ARCHITECTURE.md` para a justificação.
- **2026-06-30**: Adicionada camada 0.5 (laboratório de estratégias +
  UI) — gera/testa/valida parâmetros da lógica de hedge automaticamente,
  com gate de aprovação objetivo (nº de trades, profit factor, Sharpe,
  drawdown). PnL medido em "R" (múltiplos do desvio-padrão do spread na
  entrada), não em dinheiro — isola a lógica do dimensionamento de
  posição, que pertence à camada 3.
- **2026-06-30**: Corrigido bug de desalinhamento de índice nos dados
  sintéticos (cada símbolo gerava o seu próprio timestamp "agora" com
  microssegundos diferentes, causando NaN silencioso em `a - beta*b`).
  Agora todos os símbolos partilham o mesmo índice de tempo.
- **2026-07-20**: `src/hedge_engine.py` construído (camada 2, produção).
  Só propõe — nunca dimensiona nem executa; toda proposta passa por
  `risk_engine.evaluate_order()`. Cointegração é reverificada por bar
  (nunca reutiliza o snapshot de `hedge_candidates.csv`); a ordem de
  saída é fixa (quebra de correlação -> reversão -> stop de tempo), com a
  quebra de correlação a ter sempre prioridade. `recheck_cointegration`
  falha fechado (`is_cointegrated=False`) tanto em exceção do teste de
  Engle-Granger como em input degenerado (série quase-constante) que
  produziria um p-value numericamente inválido sem levantar exceção.
- **2026-07-23**: Bug encontrado e corrigido em `backtest_engine.walk_forward_validate()`
  ao revalidar contra dados reais pela primeira vez: `TimeSeriesSplit.split()`
  devolve índices `numpy.int64`, que se propagavam até `stats["bars_tested"]`
  e faziam `json.dumps(wf_fold_results)` falhar em
  `save_walk_forward_result()` ("Object of type int64 is not JSON
  serializable") — as 25 estratégias aprovadas na Camada 0.5 falhavam
  TODAS a revalidação por esta razão. Corrigido com `int()` explícito em
  `combined_start`/`combined_end`/`test_start_local`. Também confirmou o
  mecanismo de segurança a funcionar: a estratégia com profit factor
  suspeito de 1924 (sobreajuste) falhou corretamente `overall_passed`
  no walk-forward (drawdown agregado 9.17R > limite 8.0R).
- **2026-07-20**: Ponte Python->MQL5 (`src/signal_bridge.py` +
  `mql5/SignalBridge.mqh`) implementada como ficheiro partilhado, JSON
  Lines, sem biblioteca de JSON genérica (esquema fixo e plano, parser
  manual em poucas linhas). `RiskDecision.size_lots` é sempre escrito
  como `"risk_fraction"` (nunca `"lots"`) — a conversão para lotes
  concretos via tick_value/tick_size é responsabilidade do EA (RISK-07),
  nunca do Python. `mql5/ScalpingEA.mq5` escrito a seguir, consumindo
  esta ponte — **nunca compilado nem corrido num terminal MT5 real**,
  precisa de validação em MetaEditor + conta demo antes de qualquer uso
  (ver checklist em "Próximos passos" acima).
- **2026-07-29**: `src/ml_model.py` (Camada 1) construído seguindo
  `docs/ml_model_spec.md` à risca: rótulo triple-barrier (verbatim da
  skill quant-finance-math), LightGBM com `class_weight="balanced"`,
  walk-forward com `gap=TRIPLE_BARRIER_MAX_BARS` (evita fuga de
  informação do futuro através da fronteira treino/teste), accuracy
  condicionada ao regime, simulação de PnL líquida de custos por
  símbolo. Treinado e validado nos 14 símbolos com dados reais.
  **Resultado honesto**: sem edge direcional demonstrado — o modelo
  aprende bem a reconhecer períodos "sem movimento" (~85% dos bars,
  accuracy geral ~80% incluindo essas previsões), mas quando arrisca
  prever direção (subida/descida), acerta ao nível do acaso (~10-25%
  em problemas de 3 classes, esperado ~33%). Infraestrutura completa e
  testada (95 testes no total do projeto); a camada fica **não-ligada**
  ao motor de hedge de produção até mostrar edge genuíno — mesma
  disciplina de "nunca em produção sem validação objetiva" aplicada ao
  resto do projeto. Não foi um bug — confirmado via matriz de confusão
  (ver sessão): quando o modelo prevê uma direção, a taxa de acerto e a
  taxa de erro na direção oposta são aproximadamente iguais (ruído, não
  inversão sistemática).
- **2026-07-30**: Monitor de divergência (Camada 3.5) construído —
  `src/trade_ledger.py` (SQLite, regista abertura/fecho de cada
  operação REAL com `pnl_r` proxy de perna única) +
  `src/divergence_monitor.py` (compara janela rolante de trades ao
  vivo com as estatísticas out-of-sample da estratégia elegível;
  aciona o kill-switch, nunca aumenta risco, nunca fecha posições
  existentes, nunca reset automático). Ligado a
  `scripts/run_live_hedge_loop.py`: cada fecho real regista no ledger
  e dispara a verificação de divergência. Este é o mecanismo de
  "readaptação segura" acordado nesta sessão (reduz exposição
  automaticamente se o desempenho ao vivo divergir mal do validado —
  nunca o mecanismo de "recuperar perdas" explicitamente recusado).
  Bug real encontrado e corrigido no processo:
  `hedge_engine.propose_to_risk_engine()` usava sempre
  `risk_limits.KILL_SWITCH_PATH` ("KILL_SWITCH.flag", relativo ao cwd
  do processo Python) para o lado Python do kill-switch — nunca a
  pasta `Common\Files` real onde o EA (`RiskGuard.mqh`, FILE_COMMON) e
  agora o monitor de divergência o verificam/criam. Sem esta correção,
  um kill-switch acionado pelo monitor nunca seria visto pelo lado
  Python (só pelo MQL5), quebrando a garantia RISK-06/D-09 de
  verificação independente dos dois lados. Corrigido com um parâmetro
  `kill_switch_path` explícito em `propose_to_risk_engine()`, vinculado
  ao caminho real via `functools.partial` no driver ao vivo.
- **2026-07-30**: Alertas (ALERT-01/D-11) construídos —
  `src/alerts.py` (Telegram Bot API + email SMTP, configurado só por
  variáveis de ambiente, nunca hardcoded; qualquer falha de envio é
  engolida — nunca interrompe o loop de negociação). Ligado a: (1)
  `divergence_monitor.py` — alerta na transição para kill-switch
  acionado por divergência; (2) `run_live_hedge_loop.py` — alerta
  quando o drawdown diário/semanal/absoluto atinge 80% do limite
  (`RiskLimits.alert_threshold_pct_of_limit`, D-11, já existia como
  dado exposto desde a Fase 2 à espera desta fase), com histerese
  simples para não repetir o mesmo alerta enquanto oscila à volta do
  limiar. **Precisa de configuração do utilizador** (`TELEGRAM_BOT_TOKEN`/
  `TELEGRAM_CHAT_ID` ou `ALERT_SMTP_*`) antes de enviar alertas reais —
  sem isso, o sistema continua a funcionar normalmente, só regista um
  aviso único de "não configurado".

## Próximos passos (por prioridade sugerida)

1. ~~Testar `data_pipeline.py --mode mt5` com a conta demo real~~ — ✅
   feito 2026-07-23. Nomes de símbolos simples (`EURUSD` etc., sem
   sufixo) funcionaram diretamente nesta corretora. Bug encontrado e
   corrigido: `fetch_mt5()` passava `login=None` explicitamente a
   `mt5.initialize()`, que a biblioteca rejeita — corrigido para só
   incluir login/password/server no kwargs quando não são None,
   deixando `mt5.initialize()` ligar-se à sessão já autenticada no
   terminal aberto. Resultado: 19.800 barras/símbolo, 7 símbolos, **1
   par cointegrado** (USDJPY/NZDUSD, p=0.044, correlação -0.61, beta
   ~252.8 — beta grande é esperado, USDJPY cota ~150 vs NZDUSD ~0.6).
2. ~~Correr o laboratório de estratégias nos dados reais~~ — ✅ feito
   2026-07-23. Com 59.800 barras (7 meses M5), 2 pares cointegrados
   encontrados (GBPUSD/USDJPY, EURUSD/AUDUSD). Laboratório gerou 25
   estratégias "aprovadas" (in-sample) sobre EURUSD/AUDUSD, todas
   revalidadas via walk-forward out-of-sample com dados reais
   (`--real-data`). **Resultado: só 1 de 25 sobreviveu**
   (`81455d25`: entry_threshold=2.94, exit_threshold=0.59,
   min_correlation=0.69, max_hold_bars=180, beta_window=200,
   corr_window=250 — 5/5 folds passados, 7-10 trades/fold, retorno
   sempre positivo, drawdown 0-6.4R). As outras 24, incluindo uma com
   profit factor suspeito de 1924 (sinal de sobreajuste), foram
   corretamente reprovadas — o gate walk-forward funcionou como
   desenhado. **`hedge_engine.load_eligible_strategies()` devolve
   agora exatamente esta 1 estratégia** — primeira vez que o motor de
   hedge de produção tem algo genuinamente elegível.
   Bug encontrado e corrigido nesta revalidação:
   `walk_forward_validate()` deixava `numpy.int64` (de
   `TimeSeriesSplit`) vazar para `stats["bars_tested"]`, partindo
   `json.dumps` em `save_walk_forward_result()` — corrigido com `int()`
   explícito em `combined_start`/`combined_end`/`test_start_local`.
   **Gap de arquitetura encontrado e corrigido (2026-07-23)**:
   `run_hedge_loop()` avaliava entrada/saída com o `HEDGE_PARAMS`
   genérico do módulo (entry=2.0, min_correlation=0.5), NUNCA com os
   parâmetros específicos validados da estratégia aprovada (entry=2.94,
   min_correlation=0.69) — negociar com limiares nunca testados violava
   CLAUDE.md regra 7. Corrigido: `_hedge_params_from_strategy_record()`
   deriva os limiares por par diretamente do `params` da estratégia
   aprovada (min_correlation_exit = min_correlation*0.7, mesma proporção
   do disjuntor de correlação em `backtest_engine.run_hedge_backtest`);
   um par sem parâmetros válidos é ignorado (fail-closed), nunca cai
   para um default genérico.
   **Motor real ligado à ponte ao vivo (2026-07-23)**: adicionado
   `hedge_engine.live_feed()` (polling MT5 com aquecimento histórico +
   sincronização entre símbolos) e `on_event` callback em
   `run_hedge_loop()` (reage evento-a-evento, não só no fim do batch).
   Script condutor: `scripts/run_live_hedge_loop.py` — liga
   `run_hedge_loop()` real ao terminal MT5 e à ponte de sinais, com
   heartbeat numa thread separada. **Ainda não corrido** (precisa do
   terminal MT5 aberto e `ScalpingEA` a correr no gráfico) — próximo
   passo de validação.
- **2026-07-24**: Corrigido o gap de dimensionamento identificado no
  fim da sessão anterior: `revalidate_walk_forward.py` agora persiste
  a estrutura COMPLETA de `walk_forward_validate()` (`wf_result`
  inteiro, com `aggregate_stats`) em vez de só a lista de folds —
  `risk_engine.resolve_kelly_inputs()` já esperava esta forma desde a
  Fase 2 mas nunca a recebia, caindo silenciosamente para o proxy
  in-sample. `dashboard.py` atualizado para ler ambas as formas
  (compatibilidade com bases de dados antigas) e mostrar as
  estatísticas agregadas out-of-sample na UI. Confirmado: para a
  estratégia `81455d25`, tanto o valor antigo (incorreto,
  0.25x-Kelly=0.183 a partir de in-sample) como o novo (correto,
  0.25x-Kelly=0.147 a partir de out-of-sample) excedem o teto de 5%
  por par (D-06) — o size_lots efetivamente aprovado seria 0.05 em
  ambos os casos. A correção é real e necessária para a integridade
  do sistema, mas não alterou o risco de capital neste caso específico
  porque o teto de segurança já protegia.
- **2026-07-26**: Dois bugs encontrados e corrigidos no primeiro teste
  real de `scripts/run_live_hedge_loop.py` (mercado fechado, domingo):
  (1) `hedge_engine.live_feed()` nunca chamava `mt5.initialize()` antes
  de pedir dados, falhando com "(-10004, 'No IPC connection')" —
  corrigido com a mesma chamada sem argumentos já usada em
  `data_pipeline.fetch_mt5()`. (2) Mais importante: as barras de
  AQUECIMENTO (histórico só para preparar as janelas rolantes)
  disparavam `evaluate_hedge_signal` normalmente e os eventos
  resultantes eram escritos na ponte como se fossem decisões ao vivo —
  confirmado: o motor "revisitou" 2 pares de entrada/saída de
  sexta-feira e escreveu-os para o EA. Não abriu posição real só
  porque as 4 escritas ao ficheiro (~120ms) foram mais rápidas do que
  o EA consegue reler (PollingMillis=500ms) — sorte de timing, não uma
  garantia. Corrigido em `run_live_hedge_loop.py::on_event`: eventos
  com `bar_index < warmup_bars` são agora ignorados, nunca chegam à
  ponte.
3. ~~Motor de risco antes do modelo de ML~~ — ✅ feito (`src/risk_engine.py`
   + `mql5/RiskGuard.mqh`), determinístico, sem dependência de ML (RISK-09
   testado estruturalmente).
4. ~~Promover `hedge_engine.py` de produção~~ — ✅ feito: carrega só
   estratégias `status=="passed"` + `wf_passed` + `revalidated_on_real_data`
   (HEDGE-01), avalia entrada/saída com cointegração reverificada
   continuamente (HEDGE-03), e encaminha toda proposta para
   `risk_engine.evaluate_order` antes de abrir posição (HEDGE-02). Testado
   com feed de replay via parquet — **ainda não corrido contra dados MT5
   reais nem contra o dashboard com estratégias realmente aprovadas**, só
   com fixtures sintéticas em `tests/test_hedge_engine.py`.
5. **Modelo de ML baseline** (gradient boosting, não deep learning) —
   ver `docs/ml_model_spec.md`.
6. **Backtest walk-forward out-of-sample** das estratégias aprovadas,
   num período de dados que não foi usado durante a geração/seleção.
7. ~~EA em MQL5~~ — ⚠️ `mql5/ScalpingEA.mq5` está escrito e compila sem
   erros (confirma modo hedging no `OnInit`, lê `signal_queue.jsonl` via
   `mql5/SignalBridge.mqh`, reverifica localmente TODOS os limites de
   risco via `RiskGuard.mqh` antes de enviar qualquer ordem, dimensiona
   lotes a partir da fração de risco Kelly usando tick_value/tick_size
   reais — RISK-07 —, entra em modo "só gestão" se o heartbeat do Python
   expirar). **Checkpoint MetaEditor confirmado 2026-07-20**:
   `SignalBridgeTests.mq5` 22/22 e `RiskGuardTests.mq5` 19/19 — ambos
   PASSOU. Falta ainda, por esta ordem:
   - [x] Confirmar que a conta DEMO está em modo hedging — confirmado
     2026-07-20 (EA iniciou sem o erro fatal de `ACCOUNT_MARGIN_MODE`).
   - [x] Bug encontrado e corrigido em teste manual (2026-07-20):
     `IsHeartbeatStale()` usava `TimeCurrent()` (hora do SERVIDOR da
     corretora) para comparar contra `FILE_MODIFY_DATE` (hora LOCAL do
     sistema, mesma base do `signal_bridge.write_heartbeat()` em
     Python) — em fusos horários diferentes, o heartbeat parecia
     sempre "expirado" por horas de diferença, mesmo escrito segundos
     antes. Corrigido para `TimeLocal()`.
   - [x] Segundo bug encontrado e corrigido (2026-07-20): `OpenHedgeLeg`
     nunca registava sucesso (só falhas), e não havia proteção contra
     abrir o MESMO par duas vezes em simultâneo — sinais de teste
     repetidos empilhavam posições duplicadas em vez de serem
     rejeitados. Adicionado `IsHedgeTagOpen()` (rejeita com
     `already_open`) + log `"HEDGE ABERTO: ..."` em caso de sucesso.
   - [x] **Confirmado em conta DEMO (2026-07-20)**: sinal sintético via
     `scripts/write_test_signal.py --keep-alive` → EA abriu
     `HEDGE_EURUSD_GBPUSD` (compra EURUSD 0.02 lotes SL 1.13650 / venda
     GBPUSD 0.02 lotes SL 1.34843) corretamente, com dimensionamento via
     `ComputeLotFromRiskFraction` e stop-loss calculado localmente.
     Pipeline ponta-a-ponta (Python decide -> ponte -> EA reverifica
     risco -> executa) validado pela primeira vez.
   - [ ] Teste manual ponta-a-ponta em demo: escrever um
     `signal_queue.jsonl` sintético (via `signal_bridge.write_signal_file()`
     num script Python) diretamente na pasta `Common\Files` do terminal,
     anexar o EA a um gráfico em demo, e confirmar que abre as duas
     pernas com lote/stop corretos — antes de ligar ao loop real de
     `hedge_engine.run_hedge_loop()`.
   - [ ] Só depois seguir o checklist completo pré-conta-real de
     `docs/risk_engine_mql5_spec.md` (4-6 semanas em demo com
     monitorização diária, kill-switch confirmado, alertas configurados).

## Notas para sessões futuras do Claude Code

Se estiveres a retomar este projeto depois de um intervalo, lê por esta
ordem: `CLAUDE.md` -> `ARCHITECTURE.md` -> esta tabela de estado -> o
`docs/*_spec.md` relevante ao componente em que vais trabalhar. Não
assumas que uma camada está pronta só porque o ficheiro existe —
confirma sempre o estado nesta tabela.
