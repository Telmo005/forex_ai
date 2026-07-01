# Phase 2: Deterministic Risk Engine - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-01
**Phase:** 2-deterministic-risk-engine
**Areas discussed:** None selected — user chose the "use spec defaults" option

---

## Discussion Areas Presented

| Option | Description | Selected |
|--------|-------------|----------|
| Limites numéricos de risco | Kelly fraction, % drawdown diário/semanal, % exposição por par/total, máx. posições simultâneas | |
| Kill-switch e alertas | Ficheiro vs. botão no dashboard; canal de alerta (Telegram/email); gatilhos | |
| Heartbeat e comunicação Python↔EA | Confirmar timeout de 30s; confirmar ficheiro partilhado vs. socket | |
| Nenhuma — usa os valores sugeridos no spec e decide o resto | Avança diretamente para investigação/planeamento | ✓ |

**User's choice:** "Nenhuma — usa os valores sugeridos no spec e decide o resto"
**Notes:** User explicitly declined to walk through the numeric risk parameters individually, trusting Claude to pick concrete, conservative, well-documented defaults grounded in `docs/risk_engine_mql5_spec.md` and Phase 1's research findings — with the requirement (Claude's own design choice, not user-stated) that every numeric constant be named/documented/adjustable rather than hardcoded, so the user can retune later without needing another discussion session.

---

## Claude's Discretion

Since no area was discussed interactively, ALL concrete numeric decisions in CONTEXT.md were made at Claude's discretion, grounded in the existing spec document rather than invented from scratch:
- Kelly fraction: 0.25x (conservative end of spec's 0.25x-0.5x range)
- Daily/weekly/overall max drawdown: 3% / 8% / 20%
- Max exposure per pair / portfolio total: 5% / 15% (correlation-adjusted)
- Max simultaneous positions: 3 hedge pairs (6 legs)
- Kill-switch: file-based only (`KILL_SWITCH.flag`), no dashboard button this phase
- Alert channel: Telegram (primary), triggers at 80%-of-limit proximity
- Heartbeat timeout: 30s (spec's suggested value, confirmed as final)
- IPC mechanism: file-based (per spec recommendation, no socket/ZeroMQ this phase)
- Adversarial test scope: oversized lots, duplicate orders, invalid symbols, zero/negative lots, individual and combined limit breaches

## Deferred Ideas

- Dashboard kill-switch button (UI convenience) — not required by RISK-06, could be a future small addition
- Socket/ZeroMQ IPC upgrade — explicitly deferred by the spec itself
- Email alert channel alongside Telegram — can be added later without changing trigger logic
