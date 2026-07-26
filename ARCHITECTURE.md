# ARCHITECTURE.md

Desenho completo do sistema. Cada camada é independente e testável
isoladamente — isto é deliberado: nenhuma camada de "inteligência" (ML)
deve ter controlo direto sobre execução sem passar pela camada de risco.

```
┌─────────────────────────────────────────────────────────────────┐
│ 0. DADOS              src/data_pipeline.py            [FEITO]    │
│    Ingestão (MT5/sintético) + features adaptativas +             │
│    deteção de candidatos a hedge (cointegração) +                 │
│    deteção de regime (HMM)                                       │
└──────────────────────────┬─────────────────────────────────────┘
                            │ features_*.parquet, hedge_candidates.csv
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ 0.5 LABORATÓRIO DE ESTRATÉGIAS                         [FEITO]   │
│     src/backtest_engine.py + strategy_registry.py +               │
│     strategy_generator.py + dashboard.py (UI)                     │
│     Gera variações de parâmetros para a lógica de hedge, testa    │
│     cada uma via backtest (sem lookahead), regista estatísticas   │
│     completas (win rate, profit factor, Sharpe, drawdown, nº de   │
│     trades, duração), aplica um gate de aprovação objetivo, e se  │
│     uma geração falhar, MUTA as melhores tentativas e tenta de    │
│     novo automaticamente. A UI (Streamlit) mostra cada estratégia │
│     testada, se passou ou falhou e porquê, com curva de equity e  │
│     histórico de trades — é aqui que validas se uma estratégia    │
│     lucrou ou não antes de promovê-la.                            │
│     Ver docs/strategy_lab_spec.md                                  │
└──────────────────────────┬─────────────────────────────────────┘
                            │ parâmetros validados (aprovados na UI)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. MODELO DE ML        src/ml_model.py            [POR FAZER]    │
│    Aprende representações a partir dos dados brutos/features.    │
│    Output: probabilidade direcional + confiança, por símbolo,    │
│    condicionado ao regime detetado na camada 0.                  │
│    Ver docs/ml_model_spec.md                                     │
└──────────────────────────┬─────────────────────────────────────┘
                            │ sinal direcional + confiança + regime
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. MOTOR DE HEDGE      src/hedge_engine.py        [FEITO]        │
│    Versão de PRODUÇÃO da lógica testada no laboratório (0.5),    │
│    usando os parâmetros validados na UI. Decide quando            │
│    abrir/ajustar/fechar pernas de cobertura entre pares          │
│    cointegrados, combinando o z-score do spread (camada 0) com   │
│    o sinal direcional (camada 1).                                 │
│    Ver docs/hedge_engine_spec.md                                 │
└──────────────────────────┬─────────────────────────────────────┘
                            │ ordens propostas (símbolo, lado, tamanho, motivo)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. MOTOR DE RISCO      src/risk_engine.py +       [FEITO]        │
│                        mql5/RiskGuard.mqh                        │
│    Camada determinística (NÃO-ML) que valida ou rejeita cada     │
│    ordem proposta: tamanho via Kelly fracionado, exposição       │
│    máxima por par/total, drawdown diário máximo, número máximo   │
│    de posições simultâneas, kill-switch.                         │
│    Ver docs/risk_engine_mql5_spec.md                             │
└──────────────────────────┬─────────────────────────────────────┘
                            │ ordens aprovadas
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. EXECUÇÃO (MQL5 EA)  mql5/ScalpingEA.mq5   [FEITO, NÃO TESTADO]│
│    Expert Advisor dentro do terminal MT5. Recebe ordens          │
│    aprovadas via src/signal_bridge.py (JSON Lines, Common\Files),│
│    executa, gere posições abertas, aplica stops. Continua a      │
│    aplicar as regras de risco LOCALMENTE (RiskGuard.mqh) mesmo   │
│    se a ligação ao processo Python cair (camada de segurança     │
│    final) — modo "só gestão" quando o heartbeat expira.          │
│    ESCRITO SEM COMPILAÇÃO/TESTE NUM TERMINAL MT5 REAL — ver      │
│    checklist pré-conta-real em docs/risk_engine_mql5_spec.md.    │
└─────────────────────────────────────────────────────────────────┘
```

## Porque esta separação em camadas

Um erro comum em projetos deste tipo é deixar um único modelo "fazer
tudo" — ler o mercado, decidir hedge, dimensionar posição e executar.
Isto é frágil por duas razões: (1) um modelo de ML pode aprender padrões
espúrios do histórico que não generalizam, e sem uma camada de risco
separada, esse erro vai direto para a conta real; (2) fica impossível de
depurar quando algo corre mal, porque não há fronteira clara entre
"o modelo errou a previsão" e "o sistema executou mal a previsão".

Com camadas separadas, cada uma pode ser testada e validada
isoladamente: o pipeline de dados já está validado (camada 0); o motor
de risco pode ser testado com ordens sintéticas sem nunca tocar no
modelo de ML; o EA pode ser testado em conta demo com ordens manuais
antes de receber sinais automáticos.

## Fluxo de decisão num trade típico

1. Pipeline deteta que EURUSD/GBPUSD está cointegrado e o z-score do
   spread está em 2.3 (camada 0).
2. Modelo de ML, dado o regime atual (lateral, alta confiança), estima
   que EURUSD tem 62% de probabilidade de reverter para baixo nos
   próximos N minutos (camada 1).
3. Motor de hedge combina os dois sinais: como o modelo já espera
   reversão (alinhado com o hedge estatístico), propõe abrir short em
   EURUSD + long em GBPUSD na proporção do hedge_ratio_beta (camada 2).
4. Motor de risco calcula o tamanho via Kelly fracionado, verifica que
   isto não excede a exposição máxima diária, aprova a ordem com stop
   loss e take profit definidos (camada 3).
5. EA executa as duas pernas na corretora, monitoriza o spread em tempo
   real, fecha a perna de hedge quando o z-score reverte para perto de
   0 ou quando o stop de tempo é atingido (camada 4).

## Como validar se uma estratégia funciona (camada 0.5)

Antes de qualquer parâmetro chegar à camada 2 de produção, passa pelo
laboratório: `python src/strategy_generator.py` gera dezenas de
variações de parâmetros, testa cada uma no histórico, e regista tudo em
`output/strategy_lab.db`. Corres `streamlit run dashboard.py` e vês, por
estratégia: se foi aprovada ou reprovada (e exatamente porquê), quantos
trades fez, durante quantas barras foi testada, win rate, profit factor,
Sharpe, drawdown máximo, e a curva de equity completa. Só estratégias
✅ Aprovadas (critérios em `docs/strategy_lab_spec.md`) valem a pena
levar a sério — e mesmo essas devem ser revalidadas em dados reais da
corretora antes de produção, não só nos dados sintéticos de teste.

## Decisões em aberto (atualizar à medida que se decide)

- [x] Comunicação Python -> MQL5: **ficheiro partilhado, JSON Lines**
      (`src/signal_bridge.py` escreve, `mql5/SignalBridge.mqh` lê) —
      decidido para a v1 por simplicidade/robustez; latência ~1 ciclo de
      polling (`PollingMillis`, default 500ms). Sem biblioteca de JSON
      genérica em nenhum dos dois lados (esquema fixo e plano, parser
      manual). Migrar para socket/ZeroMQ só se a latência se mostrar
      insuficiente em conta demo (ver docs/risk_engine_mql5_spec.md).
- [ ] Arquitetura do modelo de ML: começar com gradient boosting
      (LightGBM/XGBoost) sobre as features da camada 0 como baseline
      antes de ir para deep learning (LSTM/Transformer), que precisa de
      mais dados e é mais difícil de validar.
- [ ] Timeframe principal: M1 puro é mais difícil (ruído alto) — vale
      considerar M1 para execução mas M5 para o sinal de regime/direção.
