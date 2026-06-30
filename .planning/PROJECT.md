# Forex AI Scalping System

## What This Is

Sistema de trading algorítmico autónomo para forex, focado em scalping via hedge estatístico entre pares correlacionados/cointegrados. Deteta o regime de mercado e adapta o comportamento em tempo real, gera e valida continuamente novas estratégias num laboratório offline, e troca automaticamente entre estratégias já aprovadas (ou reduz exposição) quando a performance ao vivo diverge do esperado. Execução real via Expert Advisor MQL5 ligado a MT5, com um motor de risco determinístico como última linha de defesa. Construído e usado por um único trader (o utilizador).

## Core Value

O sistema nunca deve negociar capital real com uma estratégia que não passou por validação objetiva (manual ou automática) — a sobrevivência do capital vem antes de qualquer otimização de retorno.

## Requirements

### Validated

- ✓ Pipeline de dados (modo sintético) — `src/data_pipeline.py`
- ✓ Motor de backtest sem lookahead — `src/backtest_engine.py`
- ✓ Registo de estratégias em SQLite — `src/strategy_registry.py`
- ✓ Laboratório de estratégias (gera → testa → reprova → muta → testa de novo) — `src/strategy_generator.py`
- ✓ Dashboard de validação com gate de aprovação objetivo (nº trades, profit factor, Sharpe, drawdown) — `dashboard.py`

### Active

- [ ] Ligação MT5 real validada (símbolos, timeframe, integridade dos dados) em conta demo
- [ ] Revalidação das estratégias do laboratório em histórico de mercado real (não só sintético)
- [ ] Motor de risco determinístico (Python + MQL5) — regras de stop-loss, drawdown máximo, exposição, independentes de qualquer modelo de ML
- [ ] Motor de hedge de produção (`hedge_engine.py`) usando apenas parâmetros ✅ Aprovados no dashboard
- [ ] Modelo de ML baseline (gradient boosting) para deteção de regime/sinal, não deep learning
- [ ] Deteção de regime de mercado em tempo real (HMM) que influencia qual estratégia aprovada está ativa
- [ ] Mecanismo de reação a divergência ao vivo: quando a estratégia em produção diverge do esperado (backtest), o sistema troca para outra estratégia já aprovada para o regime atual, ou reduz exposição a zero se nenhuma servir
- [ ] Gate de validação automática rápida (critérios objetivos, sem aprovação manual) para que novos candidatos gerados em background possam ser promovidos a produção mais depressa do que o ciclo manual do dashboard — mantém a mesma disciplina do gate manual, só muda quem/o quê aprova
- [ ] Backtest walk-forward out-of-sample das estratégias aprovadas antes de produção
- [ ] Expert Advisor em MQL5 (conta MT5 em modo hedging) — só depois de todas as camadas Python validadas em backtest

### Out of Scope

- Garantia de lucro contínuo ou "zero perdas" — estatisticamente impossível para qualquer sistema de trading real; o objetivo é perdas limitadas e geridas, não ausência de perdas
- Implantação ao vivo de estratégias recém-geradas sem qualquer validação (manual ou automática) — viola a Core Value deste projeto
- Aprendizagem online contínua (atualização de pesos a cada tick) — descartada a favor de retreino/revalidação periódica e troca entre estratégias pré-aprovadas, mais auditável e mais segura em scalping
- Deep learning como modelo de ML inicial — começa com gradient boosting (mais interpretável, menos dados necessários)
- Trading multi-conta ou multi-utilizador — sistema pessoal de um único trader

## Context

- Projeto brownfield: camadas 0 (dados) e 0.5 (laboratório de estratégias + dashboard) já construídas e testadas com dados sintéticos — ver `.planning/codebase/` para o mapeamento completo do código existente
- O utilizador tem conta demo MT5 disponível para testes, mas a ligação real ainda não foi validada
- Arquitetura em 5 camadas (dados / ML / hedge / risco / execução) documentada em `ARCHITECTURE.md`
- Decisões estatísticas já tomadas: Engle-Granger (não Johansen) para cointegração; HMM com fallback por percentil de volatilidade quando não converge
- Sem prazo fixo — prioridade é avançar pela ordem de risco decrescente (motor de risco antes de capital real, EA só depois de tudo validado em backtest)

## Constraints

- **Risco**: Toda a lógica de stop-loss, drawdown máximo e limite de exposição tem de existir como regra determinística (não-ML) no motor de risco/EA — nunca depender só de inferência de modelo
- **Validação**: Sempre walk-forward (janelas deslizantes no tempo), nunca split aleatório em série temporal
- **Custos**: Spread, slippage e comissão entram sempre no backtest — crítico em scalping
- **Conta MT5**: Tem de estar em modo hedging (não netting) para a lógica de hedge multi-perna funcionar — confirmar com a corretora antes de execução real
- **Produção**: Nenhum parâmetro de estratégia entra em `hedge_engine.py` sem primeiro passar por um gate de aprovação (manual no dashboard, ou automático com critérios objetivos equivalentes)
- **Conta real**: Nunca passar de demo para real sem confirmação explícita do utilizador, mesmo depois de validação exaustiva em demo

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Engle-Granger em vez de Johansen para cointegração | Mais simples, suficiente para pares (Johansen só compensa com 3+ séries) | ✓ Good |
| HMM com fallback por percentil de volatilidade | Evita que o pipeline pare quando o HMM não converge de forma estável | ✓ Good |
| Arquitetura em 5 camadas separadas (dados/ML/hedge/risco/execução) | Permite testar e validar cada camada isoladamente; risco nunca depende só de ML | ✓ Good |
| Reação a divergência ao vivo: trocar entre estratégias pré-aprovadas, não inventar novas ao vivo | "Nunca perder" é impossível; o realista é conter perdas rápido com opções já validadas | — Pending |
| Gate de validação automática rápida (híbrido) para novos candidatos do laboratório | O utilizador quer reação mais rápida que o ciclo manual do dashboard, sem abdicar do gate de segurança | — Pending |
| Modelo de ML baseline: gradient boosting, não deep learning | Mais interpretável, menos dados necessários, mais fácil de auditar | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-30 after initialization*
