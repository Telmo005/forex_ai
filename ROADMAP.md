# ROADMAP.md

## Estado atual (atualizar sempre que avançares)

| Camada | Componente | Estado | Ficheiro |
|---|---|---|---|
| 0 | Pipeline de dados | ✅ Feito e testado (modo sintético) | `src/data_pipeline.py` |
| 0 | Ligação MT5 real | ⏳ Por testar na corretora real | `src/data_pipeline.py --mode mt5` |
| 0.5 | Motor de backtest (sem lookahead) | ✅ Feito e testado | `src/backtest_engine.py` |
| 0.5 | Registo de estratégias (SQLite) | ✅ Feito e testado | `src/strategy_registry.py` |
| 0.5 | Gerador de estratégias (gera→testa→muta) | ✅ Feito e testado | `src/strategy_generator.py` |
| 0.5 | Dashboard de validação (UI) | ✅ Feito e testado | `dashboard.py` |
| 1 | Modelo de ML | ❌ Por fazer | `src/ml_model.py` |
| 2 | Motor de hedge (produção) | ❌ Por fazer | `src/hedge_engine.py` |
| 3 | Motor de risco (Python) | ❌ Por fazer | `src/risk_engine.py` |
| 3 | Motor de risco (MQL5) | ❌ Por fazer | `mql5/RiskGuard.mqh` |
| 4 | Expert Advisor | ❌ Por fazer | `mql5/ScalpingEA.mq5` |
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

## Próximos passos (por prioridade sugerida)

1. **Testar `data_pipeline.py --mode mt5` com a conta demo real** —
   confirmar nomes exatos dos símbolos na corretora, timeframe
   disponível, e que os dados batem certo com o terminal.
2. **Correr o laboratório de estratégias nos dados reais** (depois do
   passo 1) — os parâmetros validados em dados sintéticos são só prova
   de conceito, não usar em produção sem revalidar em histórico real.
3. **Motor de risco antes do modelo de ML** — é a camada de segurança,
   faz sentido existir antes de haver qualquer sinal automático a propor
   ordens. Ver `docs/risk_engine_mql5_spec.md`.
4. **Promover `hedge_engine.py` de produção** usando os parâmetros
   aprovados no dashboard — ver `docs/hedge_engine_spec.md`.
5. **Modelo de ML baseline** (gradient boosting, não deep learning) —
   ver `docs/ml_model_spec.md`.
6. **Backtest walk-forward out-of-sample** das estratégias aprovadas,
   num período de dados que não foi usado durante a geração/seleção.
7. **EA em MQL5** só depois de todas as camadas Python estarem validadas
   em backtest — a execução é a parte mais cara de errar.

## Notas para sessões futuras do Claude Code

Se estiveres a retomar este projeto depois de um intervalo, lê por esta
ordem: `CLAUDE.md` -> `ARCHITECTURE.md` -> esta tabela de estado -> o
`docs/*_spec.md` relevante ao componente em que vais trabalhar. Não
assumas que uma camada está pronta só porque o ficheiro existe —
confirma sempre o estado nesta tabela.
