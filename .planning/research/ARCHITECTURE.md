# Architecture Research

**Domain:** Algorithmic forex scalping — Python research/ML/regime layer + MQL5/MT5 execution layer
**Researched:** 2026-06-30
**Confidence:** MEDIUM (web-corroborated patterns; no single canonical reference architecture exists for retail MQL5+Python systems — synthesized from MQL5 community sources, ZeroMQ bridge projects, and general algo-trading risk-architecture guidance, cross-checked against this project's own existing `ARCHITECTURE.md`/`docs/risk_engine_mql5_spec.md`)

## Standard Architecture

### System Overview

This project already has the correct shape for this domain — a strict pipeline from signal to execution with risk as a non-bypassable gate. The milestone in scope (regime-driven strategy switching + automated validation gate) slots into the existing 5-layer design without changing its boundaries. The diagram below is the existing design annotated with where the new capabilities attach.

```
┌─────────────────────────────────────────────────────────────────────┐
│ 0. DATA / REGIME           src/data_pipeline.py            [DONE]    │
│    MT5/synthetic ingestion, features, cointegration, HMM regime      │
│    NEW: regime detector must run continuously (live loop), not just  │
│    as a batch script — feeds Layer 0.5/2 a current regime label      │
└───────────────────┬───────────────────────────────────────────────┘
                     │ features parquet + regime label (live + batch)
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 0.5 STRATEGY LAB + REGISTRY        [DONE, extend]                    │
│    src/backtest_engine.py + strategy_registry.py +                   │
│    strategy_generator.py + dashboard.py                              │
│    NEW: automated validation gate (objective criteria, no human      │
│    click) runs as a background service/cron, promotes candidates     │
│    to "approved-for-regime-X" without bypassing the same thresholds  │
│    the manual dashboard gate uses                                    │
└───────────────────┬───────────────────────────────────────────────┘
                     │ approved params, tagged by regime
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. ML MODEL                         [NOT DONE]                       │
│    src/ml_model.py — directional signal + confidence, regime-aware   │
└───────────────────┬───────────────────────────────────────────────┘
                     │ direction + confidence + regime
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. HEDGE ENGINE (PRODUCTION) + STRATEGY SELECTOR   [NOT DONE]        │
│    src/hedge_engine.py                                               │
│    NEW responsibility: given current regime, pick the active         │
│    approved strategy for each pair (or zero exposure if none fit);   │
│    NEW: live-divergence monitor compares running stats of the        │
│    active strategy vs its backtest expectation, triggers strategy    │
│    swap or exposure cut when divergence crosses a threshold          │
└───────────────────┬───────────────────────────────────────────────┘
                     │ proposed orders (symbol, side, size, reason, strategy_id)
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. RISK ENGINE (DETERMINISTIC, NON-ML)             [NOT DONE]        │
│    src/risk_engine.py (Python side, pre-trade sizing/veto)           │
│    mql5/RiskGuard.mqh (MQL5 side, last-line-of-defense)              │
│    THIS IS THE BOUNDARY — see "Risk Engine Boundary" below           │
└───────────────────┬───────────────────────────────────────────────┘
                     │ approved orders (over IPC channel, see Data Flow)
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 4. EXECUTION (MQL5 EA)                             [NOT DONE]        │
│    mql5/ScalpingEA.mq5                                               │
│    Owns: order placement, position management, heartbeat watchdog,   │
│    local risk re-check, "manage-only" degraded mode if Python dies   │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Regime Detector (live) | Emit current regime label on a fixed cadence (e.g. every closed bar) | Same HMM/percentile-fallback logic as `data_pipeline.detect_regime`, run in a loop process instead of one-shot script |
| Strategy Registry (extended) | Persist approval status **per regime**, not just per pair | SQLite schema gains a `regime` column / composite key on existing `strategy_registry.py` table |
| Automated Validation Gate | Re-run the exact `validate_strategy()` thresholds on new candidates without a human click, promote to "approved" automatically | A scheduled job (cron/Windows Task Scheduler) calling the same gate function the dashboard uses — not a separate, looser gate |
| Strategy Selector | Map (pair, current regime) → active approved strategy_id, or "no exposure" | New function inside `hedge_engine.py`; reads registry, never reads model weights directly |
| Live Divergence Monitor | Compare rolling live PnL/win-rate of the active strategy against its backtest expectation; flag divergence | New module/function in `hedge_engine.py`, stateful per active strategy |
| Risk Engine (Python) | Pre-trade sizing (fractional Kelly), exposure/drawdown checks, kill-switch read | `src/risk_engine.py`, pure-Python deterministic rules, no ML imports |
| Risk Guard (MQL5) | Re-validate every order against the same limits, independently, before sending to broker | `mql5/RiskGuard.mqh`, included by the EA, no dependency on Python being alive |
| IPC Bridge | Carry approved-order messages from Python to EA and EA state/fills back to Python | File-drop (bootstrap) → ZeroMQ (production), see Data Flow |
| Execution EA | Place/manage orders, enforce stop-loss, run heartbeat watchdog, degrade gracefully | `mql5/ScalpingEA.mq5` |

## Recommended Project Structure

The existing `src/` and `mql5/` layout is correct; the milestone adds files, not new top-level structure:

```
src/
├── data_pipeline.py        # existing — gains a --mode live loop entry point for regime streaming
├── backtest_engine.py      # existing — validate_strategy() becomes the single source of truth, called by both dashboard and the new auto-gate
├── strategy_registry.py    # existing — schema extended with regime tagging
├── strategy_generator.py   # existing
├── auto_validator.py       # NEW — scheduled job wrapping backtest_engine.validate_strategy(); promotes candidates automatically
├── ml_model.py              # future layer 1
├── hedge_engine.py          # future layer 2 — gains strategy_selector + divergence_monitor submodules
├── risk_engine.py           # future layer 3 — Python-side deterministic rules
└── bridge/
    ├── file_bridge.py       # NEW — bootstrap IPC: write approved-order JSON, read EA state/fills
    └── zmq_bridge.py        # NEW (later) — production IPC if file latency proves insufficient

mql5/
├── ScalpingEA.mq5           # future layer 4 — order placement, position mgmt, heartbeat
├── RiskGuard.mqh            # future layer 3 (MQL5 side) — local re-validation, kill-switch check
└── Bridge.mqh               # NEW — file polling now, swappable for socket include later
```

### Structure Rationale

- **`src/auto_validator.py` as a separate module, not a dashboard fork:** keeps one gate definition (`backtest_engine.validate_strategy`) consumed by two callers (human dashboard, scheduled job). Prevents the "automated gate is secretly looser than the manual one" failure mode the project's own constraints explicitly forbid.
- **`src/bridge/` isolates the IPC mechanism behind a small interface** (`send_order()`, `read_fills()`, `heartbeat()`) so the project can start with file-based IPC and swap to ZeroMQ later without touching `hedge_engine.py` or `risk_engine.py`.
- **`mql5/RiskGuard.mqh` as an include, not inline EA code:** lets risk rules be unit-tested/reviewed in isolation and reused if a second EA (e.g. a manual-override panel) is ever added.

## Architectural Patterns

### Pattern 1: Single Gate Function, Multiple Callers

**What:** One function (`validate_strategy()`) encodes the approval thresholds. Both the human-facing dashboard and the new automated gate call it — never two divergent implementations of "is this strategy good enough."
**When to use:** Anytime an automated fast-path is added alongside an existing manual gate.
**Trade-offs:** Slightly less flexibility for the automated path to have different (e.g., stricter) thresholds — if that's wanted, parameterize the function, don't fork it.

**Example:**
```python
# src/auto_validator.py
from backtest_engine import validate_strategy, run_hedge_backtest, compute_stats
from strategy_registry import save_strategy, list_candidates

def auto_validate_pending(regime_label: str, thresholds=None):
    for candidate in list_candidates(status="pending", regime=regime_label):
        trades = run_hedge_backtest(candidate.params, candidate.data)
        stats = compute_stats(trades)
        result = validate_strategy(stats, thresholds)  # same gate as dashboard.py uses
        save_strategy(candidate.id, status=result.status, reasons=result.reasons)
```

### Pattern 2: Strategy Selector Keyed by Regime, Not Global "Best"

**What:** Layer 2 does not run "the best strategy"; it runs "the approved strategy registered for the current regime + pair," falling back to zero exposure if none is approved for that regime.
**When to use:** Whenever regime detection feeds strategy choice, to avoid a single strategy silently operating outside the regime it was validated for.
**Trade-offs:** Requires the registry to tag approvals by regime (schema change) and requires enough approved candidates per regime to avoid frequent "no strategy fits, go flat" gaps — acceptable, since "go flat" is the safe default, not a bug.

### Pattern 3: Divergence Monitor as a Circuit Breaker, Not a Retrain Trigger

**What:** When live performance of the active strategy diverges from backtest expectation beyond a threshold (e.g., rolling win-rate or PnL-in-R drops below a statistical band), the response is mechanical: switch to another approved strategy for the same regime, or cut exposure to zero. It is not a trigger to retrain or tweak parameters live.
**When to use:** Continuously, while any strategy is live.
**Trade-offs:** Simpler and safer than online adaptation, but requires having multiple pre-approved strategies per regime ready to swap to — otherwise divergence just means "go flat," which is acceptable per this project's stated philosophy (no online learning, no live strategy invention).

### Pattern 4: IPC Behind an Interface, File First, Socket Later

**What:** `hedge_engine.py`/`risk_engine.py` call `bridge.send_order(order)` and `bridge.read_fills()`; the concrete implementation (file polling vs ZeroMQ) is swappable.
**When to use:** From the start — even the bootstrap file-based version should be written behind this interface so migrating to sockets later is a one-file change.
**Trade-offs:** Tiny extra abstraction now, large savings when latency forces a migration.

## Data Flow

### Request Flow (Live Trading Cycle)

```
[Regime Detector, live loop]
    ↓ (regime label, every closed bar)
[Strategy Selector in hedge_engine.py] → looks up registry for (pair, regime) → active strategy_id
    ↓
[Hedge logic] → z-score + ML signal (optional) → proposed order
    ↓
[Divergence Monitor] → checks active strategy's live stats vs backtest expectation
    ↓ (if divergence) → swap strategy_id or force exposure = 0, loop back up
[risk_engine.py] → Kelly-fraction size, exposure check, drawdown check, kill-switch file check
    ↓ (approved order, IPC bridge)
[Bridge: file write or ZeroMQ PUSH] -----------> [Bridge: file poll or ZeroMQ PULL, MQL5 side]
    ↓
[RiskGuard.mqh] → re-validates size/exposure/stops independently
    ↓ (approved)
[ScalpingEA.mq5] → CTrade order send → MT5 broker
    ↓
[Fills / position state] -----------> (back over bridge) -----------> [Python: position tracker]
```

### Heartbeat / Failure Flow

```
[Python process, every cycle]
    ↓ writes heartbeat timestamp/sequence to bridge
[ScalpingEA.mq5, OnTimer or OnTick]
    ↓ checks time since last heartbeat
    ├── < threshold (e.g. 30s) → normal mode, accept new orders from bridge
    └── ≥ threshold → MANAGE-ONLY MODE:
            - stop accepting new orders from bridge
            - continue applying stops / time-stops on open positions
            - continue local RiskGuard checks
            - log/alert (e.g. push notification) that link is down
```

### Key Data Flows

1. **Regime → strategy selection:** the live regime label is the only new continuous signal threading through Layer 0 → Layer 2; everything downstream (selector, divergence monitor) is keyed off it.
2. **Approval → registry → selector:** the automated gate and the manual dashboard both write to the same `strategy_registry` table; the selector only ever reads rows with `status = approved`, regardless of which gate approved them.
3. **Order → risk → execution, always through the bridge, never direct:** `hedge_engine.py` and `risk_engine.py` never call MT5 directly (no Python `MetaTrader5` package order calls in production path) — every order crosses the bridge so the EA's independent RiskGuard re-check is never bypassed.
4. **Fills/state → Python, for divergence tracking:** the bridge must be bidirectional — EA reports fills/closed trades back so the divergence monitor has real, not assumed, live stats.

## Scaling Considerations

This is a single-trader, single-account system — "scaling" here means latency and reliability headroom, not user count.

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Bootstrap / demo validation | File-based bridge is sufficient (per `docs/risk_engine_mql5_spec.md`, already the documented plan) — polling every few hundred ms is acceptable while proving the end-to-end loop and the risk gate logic |
| Live demo, M1 scalping with tight targets | If round-trip latency from file polling (hundreds of ms–1s) measurably eats into edge on tight-target trades, migrate the bridge to ZeroMQ (microsecond-level round trips); only the `bridge/` module changes |
| Multiple concurrent hedge pairs / higher order frequency | Keep one EA instance per MT5 terminal/account; do not split risk-engine state across processes — a single Python risk_engine process and a single EA instance per account keeps drawdown/exposure accounting consistent and auditable |

### Scaling Priorities

1. **First bottleneck: IPC latency under real scalping conditions.** Validate empirically in demo before assuming file-based is "good enough" — measure actual round-trip time against the strategy's target hold time before deciding to invest in ZeroMQ.
2. **Second bottleneck: regime detector recompute cost on every bar.** If HMM refitting every bar becomes too slow for live cadence, refit on a slower cadence (e.g. every N bars) and only recompute the current-state inference per bar — this is a Layer 0 implementation detail, not an architecture change.

## Anti-Patterns

### Anti-Pattern 1: Automated Gate as a Looser Shadow of the Manual Gate

**What people do:** Build a separate, simpler scoring function for the "fast automated path" that doesn't enforce the same min-trades/profit-factor/Sharpe/drawdown thresholds as the dashboard gate, because it "just needs to be quick."
**Why it's wrong:** Directly violates this project's own constraint that no parameter reaches production without passing the same approval bar as the dashboard. Creates two classes of "approved" strategies with different real risk.
**Do this instead:** One `validate_strategy()` function, called by both the dashboard and the scheduled auto-validator (Pattern 1 above).

### Anti-Pattern 2: Strategy Switching as an ML Decision

**What people do:** Let the ML model's confidence score directly choose which strategy/pair to activate, treating strategy selection as just another inference output.
**Why it's wrong:** Same failure mode the project already flags for position sizing — a confidently wrong model picks a strategy outside its validated regime with no deterministic check in between.
**Do this instead:** Strategy selection reads only from the approved-strategies-by-regime registry; regime label (Layer 0) gates which strategies are even eligible, not the ML model's opinion of which is best.

### Anti-Pattern 3: Direct Python-to-Broker Order Calls Bypassing the EA

**What people do:** Since the Python `MetaTrader5` package can place orders directly against a running terminal, it's tempting to skip the EA/bridge entirely for "simple" orders.
**Why it's wrong:** Removes the EA's independent RiskGuard re-check and the heartbeat/degraded-mode safety net — exactly the redundancy `docs/risk_engine_mql5_spec.md` requires as the last line of defense.
**Do this instead:** Python's `MetaTrader5` package is fine for read-only data (Layer 0 already uses it for fetch), but order placement in the live path always goes through the bridge → EA → RiskGuard chain, never a direct Python `order_send()` call in production.

### Anti-Pattern 4: Treating Divergence as a Retrain Signal

**What people do:** When live performance diverges from backtest, trigger an online retrain or live parameter tweak to "adapt."
**Why it's wrong:** This is exactly the "continuous online learning" approach this project has explicitly ruled Out of Scope as less auditable/safe for scalping.
**Do this instead:** Divergence triggers a mechanical switch to a different pre-approved strategy or exposure-to-zero, per the project's own stated decision ("trocar entre estratégias pré-aprovadas, não inventar novas ao vivo").

### Anti-Pattern 5: File-Based Bridge Without a Heartbeat Just Because It's "Simple"

**What people do:** Implement the file-drop bridge for orders but skip the heartbeat/staleness check, assuming "if the file isn't there, the EA just won't trade."
**Why it's wrong:** A stale or partially-written file, or a Python process that hangs (not crashes) without producing new orders, looks identical to "no new orders right now" unless the EA can distinguish "no signal" from "signal source might be dead." Open positions also still need active management even when no new signal arrives.
**Do this instead:** Every bridge message includes a timestamp/sequence; the EA always checks staleness independent of whether a new order file is present, and falls into manage-only mode on timeout (per `docs/risk_engine_mql5_spec.md`'s own 30s heartbeat recommendation).

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| MT5 terminal (broker connection) | `MetaTrader5` Python package for data fetch (Layer 0, already in use); MQL5 native API inside the EA for execution | Python package and EA both connect to the *same* running terminal instance — terminal must stay open and logged in; account must be in `ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` for multi-leg hedge logic to work (already documented project constraint) |
| Broker symbol specs | `SYMBOL_VOLUME_MIN/MAX/STEP`, `SYMBOL_TRADE_STOPS_LEVEL` queried at order time in MQL5 | Already documented in `docs/risk_engine_mql5_spec.md`; must be re-checked every order, not cached indefinitely (broker can change specs) |
| Alerting (Telegram/email) | EA or a small Python watcher pushes notifications on kill-switch trigger, heartbeat timeout, drawdown approaching limit | Listed as a pre-real-money checklist item in `docs/risk_engine_mql5_spec.md`; implement as a thin notifier called from both the Python risk engine and the EA's degraded-mode entry point |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Layer 0 (regime) ↔ Layer 2 (selector) | In-process function call if same Python process; parquet/shared-memory if separate processes | Keep regime detection and strategy selection in the same long-running Python process to avoid adding a second IPC hop for a label that changes once per bar |
| Layer 0.5 (registry) ↔ Layer 2 (selector) | SQLite read, same machine | Read-only from Layer 2's perspective; only the dashboard and the automated gate write approval status |
| Layer 2/3 (Python) ↔ Layer 4 (EA) | File-drop bridge now; ZeroMQ bridge later, same interface | THE critical boundary for this milestone — see Risk Engine Boundary below |
| Layer 3 split: `risk_engine.py` ↔ `RiskGuard.mqh` | No live communication — both implement the *same rule set* independently, in two languages | This is intentional duplication, not a bug: the EA must be able to enforce limits even with zero Python connectivity |

## Risk Engine Boundary (milestone-specific guidance)

The question of "where should the risk engine boundary sit relative to the EA so risk rules remain enforceable even if Python crashes" has one correct answer for this architecture: **the boundary is duplicated, not single-sourced.**

- `src/risk_engine.py` is the **pre-trade** authority: it sizes orders (fractional Kelly), checks exposure/drawdown against the live account state it polls from MT5, and is the only place that reads the kill-switch file before approving an order. If Python is alive, nothing reaches the bridge without passing here first.
- `mql5/RiskGuard.mqh` is the **last-line-of-defense** authority, running inside the EA, with no dependency on Python being alive: it re-validates every incoming order (size within symbol limits, exposure not exceeded, stop-loss present) before calling `CTrade`, and it independently manages already-open positions (stop enforcement, time-stops, kill-switch file check) even when no new orders are arriving.
- The two must encode **the same numeric limits** (max exposure %, max drawdown %, max simultaneous positions) — not approximations of each other. Treat the limit values as configuration that both sides read from a shared source of truth (e.g., a config file both Python and the EA parse at startup, or values embedded in each approved-order message so the EA can cross-check rather than re-derive).
- On heartbeat timeout, `RiskGuard.mqh` does not need to *invent* new risk logic — it already has the full rule set resident locally (it was built to operate without Python). Timeout simply changes EA behavior from "accept new orders + manage" to "manage only," using the exact same position-management and stop-enforcement code paths it always uses.

This confirms (does not change) the design already captured in `docs/risk_engine_mql5_spec.md`. The one addition this milestone requires: the kill-switch and limit values must be readable by the EA independent of Python's liveness (a local file/config the EA reads itself, not a value only ever pushed by Python), otherwise "Python crashed" and "kill-switch is unreachable" become the same failure.

## Build Order Implications

Given risk-engine-independent-of-ML is a hard requirement, and the milestone adds regime-driven switching + automated validation on top of the existing Layer 0/0.5:

1. **Risk engine first (Layer 3), both sides, before anything in Layer 1/2 production code.** It can and should be built and tested with synthetic/adversarial orders (oversized, duplicate, invalid symbol) with no dependency on a real ML model or hedge logic — this matches the project's own stated priority ("motor de risco antes de capital real") and de-risks the hardest-to-retrofit boundary first.
2. **Bridge interface (file-based) next**, built and tested end-to-end with synthetic orders flowing Python → EA → (paper) fill, before any real strategy logic depends on it. This validates the heartbeat/degraded-mode behavior in isolation.
3. **Automated validation gate (extends Layer 0.5)** can be built in parallel with step 1/2 — it only depends on the existing `backtest_engine.validate_strategy()` and `strategy_registry.py`, both already done. Low risk, no dependency on Layers 1–4.
4. **Regime tagging in the registry + strategy selector (Layer 2 partial)** comes after the automated gate exists, since the selector needs regime-tagged approved rows to read.
5. **ML model (Layer 1)** can be developed and validated in parallel with steps 1–4 (it only needs Layer 0 features) but should not be wired into the live order path until Layers 3/4 are proven with synthetic orders.
6. **Hedge engine production logic + divergence monitor (Layer 2 full)** comes after Layer 3/4 are demo-tested, since it is the first component whose output (proposed orders) actually reaches the risk engine and the bridge.
7. **MQL5 EA full build (Layer 4)** — RiskGuard.mqh can and should be built/tested before ScalpingEA.mq5's order-placement logic is finalized, since RiskGuard is the safety net the EA depends on, not the other way around.
8. **Migration to ZeroMQ**, if needed, is a step 2 follow-up done in isolation (swap `bridge/file_bridge.py` for `bridge/zmq_bridge.py` and the MQL5 `Bridge.mqh` include) — it should never be the first IPC implementation attempted, since proving the risk logic and order flow correctness matters more than latency at this stage.

This order keeps the project's existing risk-first philosophy intact: nothing in the new milestone (regime switching, automated promotion) is allowed to shorten the path to real capital — it only changes which approved strategy is active, never whether the risk engine gets to veto it.

## Sources

- [MQL5-ZeroMQ — high-performance asynchronous messaging library for MQL5](https://github.com/ding9736/MQL5-ZeroMQ) — MEDIUM confidence (community library, cross-checked against multiple independent sources below)
- [ZeroMQ to MetaTrader Connectivity — Darwinex](https://www.darwinex.com/algorithmic-trading/zeromq-metatrader) — MEDIUM confidence (broker-published technical guide)
- [python3-mql5-zeromq-connector / dwx-zeromq-connector](https://github.com/miya779/python3-mql5-zeromq-connector) — MEDIUM confidence
- [How To Connect MT4/MT5 With Python Using ZeroMQ? — MQL5 Traders' Blogs](https://www.mql5.com/en/blogs/post/716643) — MEDIUM confidence
- [MetaTrader 5 and Python integration: receiving and sending data — MQL5 Articles](https://www.mql5.com/en/articles/5691) — MEDIUM confidence (official MQL5.com articles section)
- [MetaTrader 5 and Python integration using socket — MQL5 forum](https://www.mql5.com/en/forum/331936) — MEDIUM confidence
- [MetaTrader tick info access from MQL5 services to Python using sockets — MQL5 Articles](https://www.mql5.com/en/articles/18680) — MEDIUM confidence
- [Importance of Risk Management in Algorithmic Trading Software — Nurp](https://nurp.com/algorithmic-trading-blog/importance-of-risk-management-in-algorithmic-trading-software/) — MEDIUM confidence
- [Algo Kill-Switch Engineering — Stratzy](https://stratzy.in/blog/algo-kill-switch-engineering-how-smart-traders-protect-capital-in-volatile-markets/) — MEDIUM confidence
- [Bank of England PRA Supervisory Statement SS5/18 — Algorithmic Trading](https://www.bankofengland.co.uk/-/media/boe/files/prudential-regulation/supervisory-statement/2018/ss518) — HIGH confidence (regulatory primary source, institutional context but principles transfer)
- [Watchdog/Heartbeat EA for MT4 and MT5 — MQL5 Freelance job spec](https://www.mql5.com/en/job/228532) — LOW-MEDIUM confidence (community job posting, illustrative of common pattern, not authoritative)
- [WatchDog — An EA monitoring script — MQL5 Code Base](https://www.mql5.com/en/code/10615) — MEDIUM confidence (published code example)
- Project's own `ARCHITECTURE.md`, `docs/risk_engine_mql5_spec.md`, `.planning/codebase/ARCHITECTURE.md` — HIGH confidence (already-validated project decisions, used as the baseline this research confirms and extends)

---
*Architecture research for: algorithmic forex scalping system, Python + MQL5*
*Researched: 2026-06-30*
