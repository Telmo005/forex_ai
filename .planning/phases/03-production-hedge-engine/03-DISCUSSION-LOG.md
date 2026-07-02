# Phase 3: Production Hedge Engine - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-02
**Phase:** 3-production-hedge-engine
**Areas discussed:** Limiares numéricos de entrada/saída

---

## Área inicial: quais discutir

| Option | Description | Selected |
|--------|-------------|----------|
| Limiares numéricos de entrada/saída | ENTRY/EXIT_THRESHOLD, correlação mínima entrada/saída, MAX_HOLD_BARS | ✓ |
| Frequência de reavaliação da cointegração | De quanto em quanto tempo/barras re-testar | |
| Como escolher qual estratégia aprovada usar | Seleção entre múltiplas estratégias ✅ Aprovadas | |
| Nenhuma — usa os valores sugeridos no spec | Avança direto para investigação/planeamento | |

**User's choice:** "Limiares numéricos de entrada/saída"

---

## Entrada z-score (ENTRY_THRESHOLD)

| Option | Description | Selected |
|--------|-------------|----------|
| 2.0 (sugestão do spec) | Padrão comum em mean-reversion — 2 sigma | ✓ |
| 2.5 (mais conservador) | Menos sinais, mais extremos | |
| 1.5 (mais agressivo) | Mais sinais, mais ruído | |

**User's choice:** 2.0 (D-01)

## Correlação mínima de entrada (MIN_CORRELATION_ENTRY)

| Option | Description | Selected |
|--------|-------------|----------|
| 0.6 (sugestão do spec) | Correlação moderada-alta exigida | |
| 0.7 (mais rigoroso) | Só pares fortemente correlacionados | |
| 0.5 (mais permissivo) | Mais candidatos disponíveis | ✓ |

**User's choice:** 0.5 (D-02) — mais permissivo que a sugestão do spec, deliberadamente, para ter mais candidatos.

## Saída z-score por reversão (EXIT_THRESHOLD)

| Option | Description | Selected |
|--------|-------------|----------|
| 0.3 | Fecha assim que a reversão está quase completa | ✓ |
| 0.5 | Fecha mais cedo, com margem | |

**User's choice:** 0.3 (D-03)

## Correlação mínima de saída (MIN_CORRELATION_EXIT)

| Option | Description | Selected |
|--------|-------------|----------|
| 0.4 (sugestão do spec) | Sai quando a correlação cai abaixo deste valor | ✓ |
| 0.35 (mais tolerante) | Mais margem antes de considerar quebrada | |

**User's choice:** 0.4 (D-04)

## Stop de tempo (MAX_HOLD_BARS)

| Option | Description | Selected |
|--------|-------------|----------|
| 75 barras | Meio-termo do intervalo sugerido (~6h15 em M5) | ✓ |
| 50 barras (mais curto) | Sai mais depressa (~4h10) | |
| 100 barras (mais longo) | Mais tempo à reversão (~8h20) | |

**User's choice:** 75 barras (D-05)

---

## Continuar a discutir?

| Option | Description | Selected |
|--------|-------------|----------|
| Criar CONTEXT.md | Já tenho o suficiente — decide o resto | ✓ |
| Discutir mais áreas | Frequência de reteste / seleção de estratégia | |

**User's choice:** "Criar CONTEXT.md" — decidiu não discutir a frequência de reavaliação da cointegração nem a seleção entre múltiplas estratégias aprovadas, deixando essas duas à discrição do Claude.

---

## Claude's Discretion

- **D-06** Frequência de reavaliação da cointegração: 50 barras (aprox. 2 verificações por período típico de MAX_HOLD_BARS=75), citando docs/hedge_engine_spec.md e o princípio "nunca tratar cointegração como permanente".
- **D-07** Fonte de estratégias: só `status == "✅ Aprovada"` AND `wf_passed == 1` AND `revalidated_on_real_data == 1` (gate real de dados da Fase 1).
- **D-08** Seleção entre múltiplas estratégias aprovadas: v1 não implementa seleção por regime (REGIME-01/02 são v2) — todas as estratégias elegíveis propõem independentemente; risk_engine's max_concurrent_pairs=3 é o backstop natural. Empate no mesmo par: tie-break explícito e registado em log, não silencioso.

## Deferred Ideas

- Seleção de estratégia por regime (REGIME-01/REGIME-02) — v2
- Monitor de divergência ao vivo (DIVERGE-01) — v2
- Hedge ratio dinâmico via filtro de Kalman — refinamento futuro possível, não necessário para v1
