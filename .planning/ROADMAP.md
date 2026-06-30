# Roadmap: Forex AI Scalping System

## Overview

This milestone takes the existing research stack — a data pipeline and strategy lab proven on synthetic data, with a human-gated dashboard — and builds the production layers needed to trade real capital safely on a demo account. The path runs in strict risk-decreasing order: first close the known validation gap (walk-forward, real transaction costs) so nothing downstream is built on illusory numbers; then build the deterministic risk engine, duplicated independently in Python and MQL5, before any execution logic exists; then the production hedge engine that proposes orders but never executes them directly; and finally the MQL5 Expert Advisor itself, which only goes live once every layer beneath it has been proven against synthetic adversarial conditions. Alerting is wired in alongside the EA's heartbeat and connection-loss handling, since that is where the failure signals it reports on originate. ML-driven signal generation, live regime switching, the automated validation gate, and the live divergence monitor are explicitly v2 — deferred until this v1 foundation is running stably, unattended, on a demo account.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Walk-Forward & Cost-Aware Validation** - The strategy lab validates candidates with realistic transaction costs and genuine out-of-sample walk-forward windows before anything downstream consumes its output.
- [ ] **Phase 2: Deterministic Risk Engine** - A Python + MQL5 risk engine enforces stop-loss, position sizing, exposure, drawdown, and kill-switch rules independently of any ML inference, proven against adversarial synthetic orders.
- [ ] **Phase 3: Production Hedge Engine** - The hedge engine proposes pairs-trade orders using only dashboard-approved parameters, continuously re-validating cointegration, and never executes orders itself.
- [ ] **Phase 4: MQL5 Expert Advisor & Alerting** - The EA executes risk-checked orders on a hedging-mode MT5 account, degrades safely on connection loss, and the system alerts the user when risk or connectivity thresholds are breached.

## Phase Details

### Phase 1: Walk-Forward & Cost-Aware Validation
**Goal**: Strategies cannot reach production approval based on synthetic-only or cost-blind backtests — every validated candidate has survived genuine walk-forward, cost-aware testing against real market history.
**Depends on**: Nothing (first phase)
**Requirements**: VALID-01, VALID-02
**Success Criteria** (what must be TRUE):
  1. A dashboard-approved strategy has been revalidated walk-forward, out-of-sample, on real (non-synthetic) market data before being eligible for production use.
  2. Every backtest run — manual or automated — reports performance net of modeled spread, slippage, and commission; no metric in the dashboard is cost-blind.
  3. The walk-forward methodology (window type, length, train/test gap, minimum trades per fold) is fixed and documented, and is applied identically to every candidate.
**Plans**: TBD

### Phase 2: Deterministic Risk Engine
**Goal**: No order — proposed by any current or future component — can reach the market without passing deterministic, non-ML risk checks enforced redundantly in both Python and MQL5.
**Depends on**: Phase 1
**Requirements**: RISK-01, RISK-02, RISK-03, RISK-04, RISK-05, RISK-06, RISK-07, RISK-08, RISK-09
**Success Criteria** (what must be TRUE):
  1. Every order submitted through the system carries a deterministic stop-loss and a position size capped at fractional Kelly (0.25x-0.5x), with no code path that bypasses either check.
  2. The system rejects or blocks an order that would breach per-pair/aggregate exposure limits, the maximum simultaneous position count, or an active daily/weekly drawdown circuit breaker.
  3. Creating the kill-switch file stops all new orders within one polling cycle, verified independently by both the Python side and the EA side.
  4. The EA independently re-verifies every risk limit before placing an order rather than trusting values received from Python, and this redundancy is demonstrated under a synthetic adversarial test suite (excessive lots, duplicate orders, invalid symbols).
  5. Static inspection confirms `risk_engine.py` and `RiskGuard.mqh` contain no import of, or branch on, output from `ml_model.py` or any ML inference path.
**Plans**: TBD

### Phase 3: Production Hedge Engine
**Goal**: A production hedge engine generates pairs-trade order proposals strictly from dashboard-approved strategies, with cointegration treated as a continuously re-verified property rather than a permanent one, and execution authority always deferred to the risk engine.
**Depends on**: Phase 2
**Requirements**: HEDGE-01, HEDGE-02, HEDGE-03
**Success Criteria** (what must be TRUE):
  1. `hedge_engine.py` only ever loads strategy parameters that carry a ✅ Approved status from the dashboard; there is no code path that accepts live-generated or unapproved parameters.
  2. `hedge_engine.py` produces order proposals that pass through the Phase 2 risk engine before reaching execution — it never places an order directly.
  3. A pair's cointegration is re-tested on a recurring schedule during live operation, and the hedge engine demonstrably exits or withholds a pair whose cointegration has broken down rather than treating the original test as permanent.
**Plans**: TBD

### Phase 4: MQL5 Expert Advisor & Alerting
**Goal**: A live MQL5 Expert Advisor executes risk-checked orders on a real (demo) hedging-mode MT5 account, degrades to a safe management-only mode whenever it loses contact with Python, and the user is alerted when risk or connectivity conditions warrant attention.
**Depends on**: Phase 3
**Requirements**: EA-01, EA-02, EA-03, EA-04, EA-05, ALERT-01
**Success Criteria** (what must be TRUE):
  1. The EA refuses to run multi-leg hedge logic unless it has confirmed the connected MT5 account is in hedging mode, not netting mode.
  2. Every order the EA places has its lot size and stop levels normalized to the broker's symbol specification, with no exceptions, and carries a mandatory stop-loss.
  3. When the heartbeat from Python expires, the EA switches to management-only mode — it continues to apply existing stops/time-based exits but opens no new positions and does not force-close existing ones.
  4. The EA independently checks the age of every signal file it reads (via timestamp) before acting on it, rather than trusting that Python's last write was recent.
  5. The user receives a Telegram/email alert when drawdown approaches its configured limit, when the EA stops unexpectedly, or when the Python-MQL5 connection drops.
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Walk-Forward & Cost-Aware Validation | 0/TBD | Not started | - |
| 2. Deterministic Risk Engine | 0/TBD | Not started | - |
| 3. Production Hedge Engine | 0/TBD | Not started | - |
| 4. MQL5 Expert Advisor & Alerting | 0/TBD | Not started | - |
