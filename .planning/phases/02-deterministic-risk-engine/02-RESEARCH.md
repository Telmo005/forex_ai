# Phase 2: Deterministic Risk Engine - Research

**Researched:** 2026-07-01
**Domain:** Deterministic (non-ML) trading risk management — Python risk engine + MQL5 redundant risk guard, paired by design
**Confidence:** MEDIUM-HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Position sizing**
- **D-01:** Kelly fraction = **0.25x** (fractional Kelly), at the conservative end of the spec's suggested 0.25x-0.5x range. Rationale: this is the first implementation of real risk logic touching (eventually) real capital — start conservative, loosen later only with demonstrated stable performance.
- **D-02:** Kelly inputs (win-rate, payoff ratio) come from a strategy's own backtest/walk-forward stats already persisted in `strategy_registry.py` (from Phase 1) — not re-derived independently.

**Drawdown circuit breaker**
- **D-03:** Daily max drawdown = **3%** of account equity → blocks all new orders until next trading day (does not close existing positions).
- **D-04:** Weekly max drawdown = **8%** of account equity → same block behavior, resets next week.
- **D-05:** Absolute/overall max drawdown = **20%** of account equity → hard kill-switch equivalent (treated as a standing kill condition, not just a new-orders block), requires manual reset/confirmation to resume.

**Exposure limits**
- **D-06:** Max exposure per individual pair = **5%** of account equity (risk-weighted, not notional).
- **D-07:** Max total portfolio exposure = **15%** of account equity, correlation-adjusted per the spec (positions in correlated pairs count more toward this limit than uncorrelated ones — exact correlation-weighting formula is a planner/researcher decision, not a business decision).

**Position count**
- **D-08:** Max simultaneous open positions = **3 concurrent hedge pairs (6 legs)**. Rationale: this is a personal single-trader system in its first risk-engine iteration — cap kept low and simple, can be raised later once demo-proven.

**Kill-switch**
- **D-09:** File-based only for this phase (`KILL_SWITCH.flag`, per spec) — checked by both `risk_engine.py` and (in Phase 4) the EA. No dashboard button in this phase.

**Alerts**
- **D-10:** Alert channel = **Telegram** (bot token + chat ID config) as the primary channel for this phase.
- **D-11:** Alert triggers (per ALERT-01): drawdown approaching limit (defined as reaching 80% of whichever drawdown limit — daily/weekly/overall — is closest to breach), EA/risk-engine process stopping unexpectedly, and Python↔MQL5 connection loss. Exact 80%-of-limit threshold is Claude's discretion.

**Heartbeat / Python↔MQL5 communication**
- **D-12:** Heartbeat timeout = **30 seconds**, exactly as suggested in the spec.
- **D-13:** Communication mechanism = **file-based** (shared JSON/CSV in `MQL5/Files/`), per the spec's explicit recommendation to start simple. This phase does not need to build the IPC bridge itself, but the risk engine's interface (approve/reject/size decision) must be shaped to work with this mechanism.

**Adversarial testing scope (RISK-08)**
- **D-14:** "Synthetic adversarial orders" for testing must include at minimum: oversized lot sizes (beyond broker max), duplicate order submission, invalid/unknown symbols, negative or zero lot sizes, and orders that would breach each of the limits above (drawdown, exposure, position count) individually and in combination.

### Claude's Discretion
- Exact correlation-weighting formula for portfolio exposure aggregation (D-07) — left to research/planning, since the spec states the principle ("correlated pairs count more") but not the exact math.
- Exact 80%-of-limit alert threshold implementation detail (D-11).
- Whether risk_engine.py exposes its decision as a function call, a small local API, or a file-based interface — implementation detail for the planner, constrained only by "must work with the file-based bridge eventually" (D-13).

### Deferred Ideas (OUT OF SCOPE)
- Dashboard kill-switch button (UI convenience alongside the file-based kill-switch) — noted as a nice-to-have, not required by RISK-06; could be added in a later phase.
- Socket/ZeroMQ IPC upgrade — explicitly deferred by the spec itself until file-based latency proves insufficient; not this phase's concern.
- Email alert channel (alongside Telegram) — can be added later without changing alert-trigger logic (D-10).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-------------------|
| RISK-01 | Sistema aplica stop-loss determinístico em toda ordem, independente de qualquer inferência de ML | Pattern 1/2 (pure-function risk core) + `.claude/skills/mql5-trading-ea/SKILL.md`'s mandatory-SL-before-send pattern; Architecture Diagram shows SL computed Python-side, enforced/verified MQL5-side regardless of what's received |
| RISK-02 | Sistema dimensiona posições via Kelly fracionário (0.25x–0.5x, nunca Kelly completo) | Standard Stack + Code Examples: `kelly_fraction()` pattern from quant-finance-math skill, fraction fixed at 0.25 per D-01; Pitfall 4 on out-of-sample input sourcing |
| RISK-03 | Sistema impõe limites de exposição máxima por par e agregada, ajustados por correlação entre pares | Don't Hand-Roll + Code Examples: correlation-adjusted exposure aggregation (variance-scaling approach recommended); Open Question 1 flags the formula choice for planner confirmation |
| RISK-04 | Sistema impõe disjuntor de drawdown máximo diário/semanal que bloqueia novas ordens até reset | Pattern 1 (`check_drawdown_breaker` pure function, both languages); Pitfall 5 on correct "block new orders only" semantics |
| RISK-05 | Sistema limita o número máximo de posições simultâneas abertas | Architectural Responsibility Map + Pattern 1 (position-count check, duplicated both sides) |
| RISK-06 | Utilizador consegue parar instantaneamente todas as novas ordens via kill-switch (ficheiro), verificado tanto pelo lado Python como pelo EA | Code Examples (kill-switch check, both languages); Architectural Responsibility Map marks this as dual-primary (no single owner tier); Pitfall 5 on correct semantics |
| RISK-07 | EA verifica de forma redundante e independente os limites de risco antes de cada ordem, sem confiar cegamente no que o Python envia | Architecture Diagram + Anti-Patterns ("Trusting the Python-computed size/exposure/drawdown values in MQL5") |
| RISK-08 | Motor de risco (Python + MQL5) é testado contra ordens sintéticas adversariais (lotes excessivos, ordens duplicadas, símbolos inválidos) antes de qualquer uso com dados reais | Pattern 2 (standalone MQL5 Script test runner) directly answers the phase's flagged research gap; Security Domain's "Known Threat Patterns" table maps D-14's adversarial cases to concrete input-validation mitigations |
| RISK-09 | `risk_engine.py` e `RiskGuard.mqh` nunca importam nem ramificam com base em saída de `ml_model.py` — garantido estruturalmente, não só por convenção | Pitfall 1 (structural static-check recommendation); Architectural Responsibility Map's "ML-independence enforcement" row marks this as a structural, not runtime, concern |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Rule 1 (Risco nunca depende só do modelo de ML):** All stop-loss, max-drawdown, and exposure-limit logic must exist as deterministic rules in the risk engine/EA, independent of ML inference — this is the entire premise of this phase (RISK-09) and is treated as a hard, structurally-enforced constraint throughout this research (see Pitfall 1, Architectural Responsibility Map).
- **Rule 2 (Validação é sempre walk-forward):** Never accept a random train/test split on time-series data. Applies indirectly to this phase via Pitfall 4 — Kelly inputs must come from walk-forward-validated stats in `strategy_registry.py`, not raw in-sample backtest numbers.
- **Rule 3 (Gatilho matemático exato para hedge):** Every hedge strategy must cite its exact mathematical trigger — not directly this phase's concern (Phase 3), but the analogous discipline is applied here to risk-limit formulas (Pitfall 3: name and document the correlation-adjustment formula, don't leave it implicit).
- **Rule 4 (Custos de transação sempre no backtest):** Not directly applicable to this phase (no backtesting performed here), but the Kelly payoff-ratio input this phase consumes from `strategy_registry.py` is itself already cost-aware per Phase 1's work — no action needed here beyond consuming that data correctly (Pitfall 4).
- **Rule 5 (Conta MT5 em modo hedging):** `RiskGuard.mqh`'s test Script pattern (Pattern 2) should include a test case exercising the hedging-mode check pattern from `.claude/skills/mql5-trading-ea/SKILL.md`, even though the full account-mode check itself is more fully wired in Phase 4's EA.
- **Rule 6 (Testar em demo antes de real):** This entire phase's existence — and its RISK-08 adversarial test suite — is a direct instantiation of this rule; nothing in this phase touches a real account.
- **Rule 7 (Nenhum parâmetro entra em produção sem aprovação no dashboard):** Directly connects to Pitfall 4 — the win_rate/payoff_ratio Kelly inputs must come from an *approved* (dashboard-gated) strategy's persisted stats, not an arbitrary or unapproved strategy row in `strategy_registry.py`.

## Summary

This phase builds the single most safety-critical component of the whole system: `src/risk_engine.py` (Python, pre-trade authority) and `mql5/RiskGuard.mqh` (MQL5, last-line-of-defense authority). Both sides must independently enforce identical numeric limits — fractional Kelly position sizing (0.25x), per-pair/portfolio exposure caps (5%/15%, correlation-adjusted), daily/weekly/absolute drawdown circuit breakers (3%/8%/20%), a max position count (3 pairs/6 legs), and a file-based kill-switch — with zero import of or branch on any ML output. All numeric values are already locked in `02-CONTEXT.md` (D-01 through D-14); this research does not re-litigate those numbers, it focuses on *how* to implement them correctly and *how to test MQL5 code in isolation*, which is the genuine open gap for this phase (the full EA and IPC bridge don't exist yet, so `RiskGuard.mqh` needs its own standalone test harness).

The dominant technical finding is that MQL5 has no built-in unit-testing framework, and the two community frameworks that exist (MQLUnit, MTUnit) are lightly maintained and still lean on the Strategy Tester or external watcher tools. The proven, low-dependency pattern instead is **architectural**: keep every risk decision in `RiskGuard.mqh` as a pure function (fixed inputs -> fixed output, no `AccountInfoDouble`/`OrderSend`/chart calls inside the decision logic itself), and drive those pure functions from a small standalone `.mq5` Script with a lightweight custom assertion helper. This script runs instantly in MetaEditor (right-click Run, or drag onto any chart) with no Strategy Tester, no historical data, and no live terminal required — which directly satisfies this phase's requirement that `RiskGuard.mqh` be "testable/callable in isolation." The same pure-function separation should be mirrored on the Python side (`risk_engine.py` decision functions take explicit state as arguments, no hidden I/O), which also makes `RISK-08`'s synthetic adversarial test suite trivial to write with `pytest`.

**Primary recommendation:** Build `risk_engine.py` and `RiskGuard.mqh` as two independent implementations of the same documented rule set (shared constants documented in both places, generated from the same source-of-truth table in this research and `02-CONTEXT.md` — never a Python module imported into MQL5, since that's impossible, and never MQL5 calling back into Python for a decision, since that defeats the redundancy requirement). Use `pytest` for the Python side (already implied by project conventions) and the pure-function-plus-Script pattern for `RiskGuard.mqh`. Do not add `pyzmq` or the IPC bridge in this phase — the interface only needs to be *shaped* for eventual file-based exchange (a dict of `{approved: bool, size_lots: float, sl_price: float, reject_reason: str|None}` matches this cleanly and mirrors the JSON the file bridge will carry in Phase 3/4).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Kelly-fraction position sizing | API/Backend (`risk_engine.py`) | Execution (`RiskGuard.mqh` re-verifies resulting lot size against account equity) | Sizing needs backtest-derived win-rate/payoff data (`strategy_registry.py`), only available Python-side; EA re-checks the *result* (lot within sane bounds), not the Kelly math itself |
| Exposure limit enforcement (per-pair, aggregate) | API/Backend | Execution (redundant, independent recomputation) | Both sides must independently compute current exposure from live position state — Python from its own tracked state, EA from `PositionsTotal()`/`PositionGetDouble()` — never trusting a number passed across the bridge |
| Drawdown circuit breaker (daily/weekly/absolute) | API/Backend | Execution (redundant) | Same duplication logic: Python computes from its own equity high-water-mark tracking; EA computes independently from `AccountInfoDouble(ACCOUNT_EQUITY)` history it maintains itself |
| Max simultaneous position count | API/Backend | Execution (redundant) | Trivial count check, must exist on both sides per RISK-07 |
| Kill-switch (file-based) | API/Backend AND Execution (dual-primary, not primary/secondary) | — | RISK-06 explicitly requires independent verification by *both* sides — this is the one capability where there is no single "owner," by design |
| Stop-loss attachment | Execution (`RiskGuard.mqh`/future EA) | API/Backend (Python includes SL in the proposed-order payload) | Final stop-loss placement happens at order-send time in MQL5; Python's role is to *compute* the SL price and pass it, but MQL5 must refuse to send without one regardless of what it receives |
| Alerting (Telegram) | API/Backend (`risk_engine.py` or a small `alerts.py`) | — | No MQL5-side alerting needed this phase — EA-down/connection-loss alerts are Phase 4 scope (the EA doesn't exist yet); this phase only needs the Python-side trigger for drawdown-approaching-limit |
| Heartbeat timeout interface (shape only) | API/Backend (writes timestamp) | Execution (reads/ages the timestamp) | The interface must be *shaped* for this exchange now, but the actual bridge/EA polling loop is Phase 3/4 |
| ML-independence enforcement | Both (structural) | — | Neither tier may import or branch on `ml_model.py`; verified by static inspection (grep/AST check), not a runtime tier at all |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python (stdlib) `dataclasses` | 3.10+ (already project baseline; 3.12.10 confirmed installed) [VERIFIED: local environment] | `RiskLimits`/`AccountState`/`OrderProposal`/`RiskDecision` config/value objects | Matches existing `PipelineConfig` pattern in `data_pipeline.py`; zero new dependency |
| `pytest` | 9.1.1 confirmed installed [VERIFIED: local environment] | Synthetic adversarial test suite (RISK-08) | Already present in this environment; standard Python test runner, no reason to introduce anything else |
| `sqlite3` (stdlib) | n/a | Persisting drawdown high-water-mark / kill-switch state if needed across restarts | Mirrors `strategy_registry.py`'s existing idempotent-migration pattern exactly — reuse, don't reinvent |
| MQL5 (built-in, MetaEditor/MetaTrader 5 terminal) | Terminal-bundled | `RiskGuard.mqh` risk logic + standalone test Script | No alternative; this is the only language the EA runs in |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `python-telegram-bot` | 22.8 (PyPI, confirmed current) [VERIFIED: pip index versions] — package itself has shipped continuously since 2015 (100+ releases) [CITED: pypi.org/pypi/python-telegram-bot/json] | Sends Telegram alerts per D-10/D-11/ALERT-01 | Only if alert-sending is implemented in this phase rather than deferred; a minimal `requests.post` to the Telegram Bot API HTTP endpoint is a viable zero-dependency alternative if the planner prefers not to add a new package this early |
| `json` (stdlib) | n/a | Kill-switch/heartbeat file format, drawdown state snapshot | Matches spec's own recommendation (`docs/risk_engine_mql5_spec.md`) of JSON for file-based exchange |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `python-telegram-bot` | Raw `requests.post` to `api.telegram.org/bot<token>/sendMessage` | Zero new dependency, ~5 lines of code; loses convenience wrappers (async handlers, retries) that this phase doesn't need since it's fire-and-forget alerting, not a full bot. **Recommended for this phase** given D-10 only needs outbound one-way alerts, not an interactive bot. |
| MQLUnit / MTUnit (community MQL5 test frameworks) | Hand-rolled pure-function + Script pattern (TestLite.mqh-style) | Community frameworks add setup friction (Strategy Tester dependency, external watcher .exe) for marginal benefit at this phase's scale (a handful of risk functions); the hand-rolled pattern is simpler, has zero external tooling, and is the pattern MQL5's own official articles recommend |
| SQLite for risk-engine state | Flat JSON file for drawdown HWM / kill-switch state | JSON is simpler and matches the kill-switch/heartbeat file format already chosen (D-13); SQLite only clearly wins if the planner wants transactional guarantees around concurrent read/write — worth flagging as a discretion point for the planner, not decided here |

**Installation:**
```bash
pip install python-telegram-bot==22.8   # optional — see "Alternatives Considered"; requests (already likely available) is a valid zero-dep substitute
```

**Version verification:** `python-telegram-bot` confirmed current at 22.8 via `pip index versions python-telegram-bot` [VERIFIED: pip index versions, run 2026-07-01]. No other new packages are required for this phase — `risk_engine.py`'s core logic needs no third-party library beyond what's already in `requirements.txt` (pandas/numpy not even strictly required for the risk math itself, though may be convenient for correlation matrix lookups already produced by Layer 0).

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| python-telegram-bot | PyPI | First release 2015-07-08 (~11 years); latest 22.8 released 2026-06-12 [VERIFIED: pypi.org/pypi/python-telegram-bot/json] | Not returned by registry lookup (`weeklyDownloads: null`) | https://github.com/python-telegram-bot/python-telegram-bot (official docs site python-telegram-bot.org confirmed) | seam reported [SUS] ("too-new", "unknown-downloads") — **overridden below** | Approved, with override rationale documented |

**Package legitimacy gate override rationale:** The `gsd-tools query package-legitimacy check` seam flagged `python-telegram-bot` as `SUS` with reasons `too-new` and `unknown-downloads`. Investigation shows this is a false positive: the seam's "too-new" signal is measuring the *most recent release* (22.8, published 2026-06-12), not the package's actual age. Direct query of the PyPI JSON API shows the package's *first* release was 2015-07-08 — over a decade of continuous releases (100+ versions across the 1.x through 22.x series), with an official GitHub org (`python-telegram-bot/python-telegram-bot`) and dedicated docs site. This is one of the most widely used Telegram bot libraries in the Python ecosystem. **Disposition: Approved for use**, but the planner should still add a `checkpoint:human-verify` before this specific install as a defense-in-depth measure, since the automated gate cannot currently distinguish "long-lived package with a recent point release" from "brand-new package" — this is a known limitation of the seam's age heuristic, not a signal about this specific package's trustworthiness.

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** python-telegram-bot — see override rationale above; planner must still gate the install behind `checkpoint:human-verify` per protocol, despite the override, since the seam's raw verdict was SUS.

## Architecture Patterns

### System Architecture Diagram

```
                    ┌───────────────────────────────┐
                    │  Layer 2: hedge_engine.py       │
                    │  (future, Phase 3)              │
                    │  emits: proposed order           │
                    │  {pair_a, pair_b, direction,     │
                    │   entry_price, win_rate,         │
                    │   payoff_ratio}                  │
                    └───────────────┬───────────────┘
                                    │ proposed order (function call
                                    │ or dict — in-process this phase)
                                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  risk_engine.py  (THIS PHASE — Python pre-trade tier)   │
        │                                                          │
        │  1. Kill-switch check ─── KILL_SWITCH.flag exists? ──► REJECT
        │  2. Drawdown circuit breaker check                       │
        │     (daily 3% / weekly 8% / absolute 20% HWM)  ──► REJECT/BLOCK
        │  3. Position count check (max 3 pairs / 6 legs) ──► REJECT
        │  4. Exposure check (5% per-pair, 15% aggregate,          │
        │     correlation-adjusted)                       ──► REJECT
        │  5. Kelly-fractional position sizing                     │
        │     (0.25x fractional Kelly from win_rate/payoff_ratio    │
        │      persisted in strategy_registry.py)                  │
        │  6. Compute deterministic stop-loss price                 │
        │  7. Alert trigger check (80% of nearest drawdown limit)   │
        │     ──► Telegram alert (side effect, does not block order)│
        │                                                          │
        │  OUTPUT: RiskDecision                                   │
        │  {approved: bool, size_lots: float, sl_price: float,     │
        │   reject_reason: str|None}                               │
        └───────────────────────┬─────────────────────────────────┘
                                 │ shaped for eventual file-based
                                 │ exchange (Phase 3/4 IPC bridge)
                                 │ — NOT built in this phase
                                 ▼
        ┌───────────────────────────────────────────────────────┐
        │  RiskGuard.mqh  (THIS PHASE — MQL5 last-line-of-defense) │
        │  Called from a standalone test Script THIS PHASE;        │
        │  called from ScalpingEA.mq5 in Phase 4 (not built yet)    │
        │                                                          │
        │  Re-verifies INDEPENDENTLY, trusting nothing from Python: │
        │  1. Kill-switch file check (own FileIsExist call)         │
        │  2. Hedging-mode account check                            │
        │  3. Exposure recompute from PositionsTotal()/              │
        │     PositionGetDouble() (own state, not Python's)          │
        │  4. Drawdown recompute from AccountInfoDouble(EQUITY)       │
        │     history it tracks itself                               │
        │  5. Lot/stop normalization against SYMBOL_VOLUME_*/         │
        │     SYMBOL_TRADE_STOPS_LEVEL                                │
        │  6. Mandatory stop-loss presence check                      │
        │                                                          │
        │  OUTPUT: bool (pass/reject) + Print() reason log          │
        └───────────────────────────────────────────────────────┘

        Structural constraint enforced across BOTH boxes above:
        NEITHER may import or branch on ml_model.py output (RISK-09) —
        verified by static grep/AST scan, not by a runtime component.
```

### Recommended Project Structure
```
src/
├── risk_engine.py           # NEW this phase — pre-trade Python risk authority
├── risk_limits.py           # NEW this phase (optional split) — named constant tables
│                             #   (RISK_LIMITS, DRAWDOWN_THRESHOLDS, etc.) mirroring
│                             #   backtest_engine.py's DEFAULT_COST_PARAMS convention
├── alerts.py                 # NEW this phase (optional split) — Telegram alert sender
├── strategy_registry.py      # EXISTING — risk_engine.py reads win_rate/payoff_ratio from here
tests/
├── test_risk_engine.py       # NEW this phase — RISK-08 synthetic adversarial suite (pytest)
mql5/
├── RiskGuard.mqh             # NEW this phase — pure risk-check functions, no order/account
│                             #   calls buried inside decision logic (or isolate those into
│                             #   thin wrapper functions the pure functions don't call directly)
├── Tests/
│   └── RiskGuardTests.mq5    # NEW this phase — standalone Script test runner
│   └── TestLite.mqh          # NEW this phase — minimal assertion helper (AssertTrue, etc.)
docs/
├── risk_engine_mql5_spec.md  # EXISTING — canonical spec, already read
```

### Pattern 1: Pure-Function Risk Decision Core (both languages)
**What:** Every risk-check function takes explicit inputs (account state, proposed order, limits config) and returns a decision — no hidden reads of global account state, no I/O, no randomness.
**When to use:** All Kelly sizing, exposure, drawdown, and position-count checks in both `risk_engine.py` and `RiskGuard.mqh`.
**Example:**
```python
# Source: pattern inferred from project's existing backtest_engine.py convention
# (compute_stats, validate_strategy are already pure — this phase extends that
# convention to risk logic) + MQL5 TestLite.mqh separation pattern [CITED: mql5.com/en/articles/19154]
from dataclasses import dataclass

@dataclass
class AccountState:
    equity: float
    daily_start_equity: float
    weekly_start_equity: float
    absolute_hwm: float
    open_positions: list[dict]   # [{pair_a, pair_b, exposure_pct, correlation_cluster_id}, ...]

@dataclass
class RiskLimits:
    kelly_fraction: float = 0.25              # D-01
    max_pair_exposure_pct: float = 0.05       # D-06
    max_aggregate_exposure_pct: float = 0.15  # D-07
    daily_drawdown_pct: float = 0.03          # D-03
    weekly_drawdown_pct: float = 0.08         # D-04
    absolute_drawdown_pct: float = 0.20       # D-05
    max_concurrent_pairs: int = 3             # D-08

def check_drawdown_breaker(state: AccountState, limits: RiskLimits) -> tuple[bool, str | None]:
    """Pure function: no file I/O, no side effects — testable with plain dataclasses."""
    daily_dd = (state.daily_start_equity - state.equity) / state.daily_start_equity
    weekly_dd = (state.weekly_start_equity - state.equity) / state.weekly_start_equity
    absolute_dd = (state.absolute_hwm - state.equity) / state.absolute_hwm
    if absolute_dd >= limits.absolute_drawdown_pct:
        return False, "absolute_drawdown_kill_switch"
    if daily_dd >= limits.daily_drawdown_pct:
        return False, "daily_drawdown_breaker"
    if weekly_dd >= limits.weekly_drawdown_pct:
        return False, "weekly_drawdown_breaker"
    return True, None
```
```mql5
// Source: pattern from TradeMathCore.mqh separation [CITED: mql5.com/en/articles/19154]
// Pure function — takes explicit doubles, no AccountInfoDouble() call inside.
// The EA (Phase 4) is responsible for gathering the real values and passing them in;
// this phase only needs RiskGuard.mqh + a Script that calls it with fixed test values.
bool CheckDrawdownBreaker(double equity, double dailyStartEquity,
                           double weeklyStartEquity, double absoluteHWM,
                           double dailyDDPct, double weeklyDDPct, double absoluteDDPct,
                           string &rejectReason)
{
    double dailyDD = (dailyStartEquity - equity) / dailyStartEquity;
    double weeklyDD = (weeklyStartEquity - equity) / weeklyStartEquity;
    double absoluteDD = (absoluteHWM - equity) / absoluteHWM;

    if(absoluteDD >= absoluteDDPct) { rejectReason = "absolute_drawdown_kill_switch"; return false; }
    if(dailyDD >= dailyDDPct)       { rejectReason = "daily_drawdown_breaker";        return false; }
    if(weeklyDD >= weeklyDDPct)     { rejectReason = "weekly_drawdown_breaker";       return false; }
    return true;
}
```

### Pattern 2: Standalone MQL5 Test Script (no EA, no Strategy Tester, no live terminal)
**What:** A `.mq5` Script (not an Expert Advisor) with `OnStart()` as its entry point, run directly in MetaEditor or by dragging onto any chart — calls the pure functions in `RiskGuard.mqh` with fixed adversarial test inputs and prints pass/fail.
**When to use:** This is the answer to the phase's flagged research gap — how to test `RiskGuard.mqh` before the full EA exists.
**Example:**
```mql5
// Source: pattern from MQL5 Scripts + TestLite.mqh assertion style [CITED: mql5.com/en/articles/19154]
#include <RiskGuard.mqh>
#include "TestLite.mqh"   // minimal AssertTrue/AssertEqualsInt/AssertNearDouble helpers

void OnStart()
{
    CTestLite test("RiskGuardTests");

    // D-14 adversarial case: absolute drawdown breached -> must reject
    string reason;
    bool passed = CheckDrawdownBreaker(
        /*equity*/ 8000.0, /*dailyStart*/ 9800.0, /*weeklyStart*/ 9500.0,
        /*absoluteHWM*/ 10000.0, /*dailyDDPct*/ 0.03, /*weeklyDDPct*/ 0.08,
        /*absoluteDDPct*/ 0.20, reason);
    test.AssertTrue(!passed, "20% drawdown from HWM must reject order");
    test.AssertStringEquals("absolute_drawdown_kill_switch", reason);

    // D-14 adversarial case: oversized lot must be rejected/clamped by normalization
    double normalized = NormalizeLot("EURUSD", 999999.0);
    double maxLot = SymbolInfoDouble("EURUSD", SYMBOL_VOLUME_MAX);
    test.AssertNearDouble(maxLot, normalized, 0.0001, "oversized lot must clamp to broker max");

    test.PrintSummary();   // reports pass/fail counts, no Strategy Tester needed
}
```

### Anti-Patterns to Avoid
- **Risk logic reading global state inside the decision function:** If `RiskGuard.mqh`'s risk-check functions call `AccountInfoDouble()`/`PositionsTotal()` directly instead of receiving values as parameters, they become untestable outside a live terminal — this is exactly the gap this phase must close. Keep account-state gathering in a thin wrapper the EA (Phase 4) will call; keep the pure decision functions parameter-driven.
- **Trusting the Python-computed size/exposure/drawdown values in MQL5:** RISK-07 explicitly forbids this. `RiskGuard.mqh` must recompute exposure/drawdown from its own view of account/position state, never from a number embedded in the file the bridge writes — the whole point of duplication is that Python's number could be stale, wrong, or the process could be compromised.
- **A single shared "risk constants" Python module imported by both sides:** Impossible for MQL5 to import a `.py` file directly. Do not attempt any code-sharing trick (e.g. code generation from one source into the other) that adds complexity for marginal benefit at this project's scale — hand-write both, keep the numeric values synchronized via the shared spec/CONTEXT.md as the source of truth, and add a code-review checklist item (not an automated codegen step) to verify they match after any change.
- **Coupling Kelly-fraction sizing to a "confidence" score:** Per CLAUDE.md rule 1 and RISK-09, `risk_engine.py` must never branch on anything resembling ML confidence. Kelly inputs (win_rate, payoff_ratio) come only from `strategy_registry.py`'s persisted backtest/walk-forward stats (D-02) — frozen at strategy-approval time, not recomputed live from any model output.
- **Testing MQL5 exclusively via the Strategy Tester:** The Strategy Tester requires historical data feeds and is built for full EA backtesting, not for testing a handful of pure risk functions. It's slower to iterate and adds friction. Prefer the Script-based pattern above as the primary/fast path.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Kelly fraction formula | Custom derivation of the Kelly formula from scratch | `kelly_fraction()` pattern already documented in `.claude/skills/quant-finance-math/SKILL.md` (`f_star = (b*p - q) / b`, clamped at 0, multiplied by `fraction`) | Already vetted project pattern; re-deriving risks a sign/edge-case bug (e.g. forgetting to floor at 0.0 when edge is negative) |
| MQL5 unit-test framework from scratch | A full custom test-runner framework with fixtures, mocks, discovery, etc. | The minimal TestLite.mqh-style assertion helper (a few dozen lines: AssertTrue/AssertEqualsInt/AssertNearDouble/AssertStringEquals + a summary counter) | At this phase's scale (a handful of risk functions), a full framework (MQLUnit/MTUnit) adds Strategy-Tester or external-watcher dependencies for no real benefit; the minimal helper is easier to audit and has zero external dependency |
| SQLite migration pattern for new risk-engine persisted state | A new bespoke migration mechanism | `strategy_registry.py`'s existing `migrate_add_*_columns()` + `PRAGMA table_info()` idempotent pattern | Already established, already proven in this exact codebase (Phase 1), zero reason to diverge |
| Correlation-adjusted exposure aggregation math | A bespoke, unreviewed formula invented ad hoc | The standard practitioner approximation researched here: either (a) portfolio-variance-based scaling (`effective_exposure = raw_exposure * (1 + avg_pairwise_correlation)`), or (b) per-cluster exposure caps for pairs with pairwise correlation above a threshold (e.g. 0.7) — pick one explicitly in planning, do not leave it implicit | The spec (`docs/risk_engine_mql5_spec.md`) states the *principle* ("correlated pairs count more") but not the formula; multiple independent sources converge on these two equivalent approaches — inventing a third, unreviewed formula for capital-risking logic is exactly the kind of "decide the math ourselves" anti-pattern CLAUDE.md and the quant-finance-math skill warn against |
| Telegram alerting from scratch (raw HTTP signing, retries, rate-limit handling) | Hand-rolled `requests` wrapper with full retry/backoff logic | Either `python-telegram-bot`'s `Bot.send_message()` (if the dependency is accepted) or a minimal single-call `requests.post` to `sendMessage` (if not) — but not a hand-rolled retry/rate-limit framework | Telegram's Bot API is simple enough that either option is a few lines; building custom retry/backoff logic for what is a nice-to-have alert (not the safety-critical path itself) is disproportionate effort for this phase |

**Key insight:** This phase's danger is not "the math is hard" (Kelly and drawdown formulas are simple and already documented in the project's own skill file) — it's **letting two independent implementations of the same simple math drift apart**, or **accidentally making the MQL5 side untestable by burying pure logic inside functions that also read live account state**. Both are solved by the same discipline: pure functions, explicit parameters, one documented source of truth for the numeric constants.

## Runtime State Inventory

Not applicable — this is a greenfield phase. `risk_engine.py`, `RiskGuard.mqh`, and the entire `mql5/` risk-testing scaffolding are new files with no prior runtime state, stored data, or OS-registered state to migrate. Confirmed via `.planning/codebase/STRUCTURE.md` and direct listing (`mql5/` currently contains no files — only referenced as a placeholder path in `ARCHITECTURE.md`).

## Common Pitfalls

### Pitfall 1: Risk logic silently coupling to ML confidence
**What goes wrong:** A future PR adds `if ml_confidence > 0.8: skip_exposure_check()` somewhere near risk logic, because "the model seems reliable here."
**Why it happens:** Gradual scope creep, usually well-intentioned ("just this one edge case"), not a deliberate architecture violation.
**How to avoid:** RISK-09 requires this be enforced *structurally*, not just by convention. Add a lightweight static check (grep for `import ml_model` or `from ml_model` in `risk_engine.py`, and grep for `ml_model` string references in `RiskGuard.mqh`) as part of this phase's own verification step — not deferred to a future lint pass. Kelly inputs (win_rate, payoff_ratio) must be frozen values from `strategy_registry.py`, never live model output.
**Warning signs:** Any `if`/`elif` branch in risk-check functions whose condition variable name contains "confidence", "signal", "probability", or "ml_" anywhere near exposure/drawdown/sizing logic.

### Pitfall 2: MQL5 risk functions become untestable because they read live state internally
**What goes wrong:** `RiskGuard.mqh` functions call `AccountInfoDouble(ACCOUNT_EQUITY)` or `PositionsTotal()` directly inside the decision logic, which means they can only be exercised inside a running terminal with a real (or demo) account connected — exactly what this phase needs to avoid, since the full EA doesn't exist yet.
**Why it happens:** It's the "natural" way to write MQL5 EA code when you're used to writing the whole EA at once; the pure-function discipline is an explicit choice, not the MQL5 default style.
**How to avoid:** Structure `RiskGuard.mqh` as pure functions taking explicit `double`/`string`/`bool` parameters (see Pattern 1/2 above); put any live-state-gathering in separate thin functions that Phase 4's EA will call, not this phase's test target.
**Warning signs:** Any function signature in `RiskGuard.mqh` with zero parameters, or that calls `AccountInfoDouble`/`SymbolInfoDouble`/`PositionGetDouble` directly rather than receiving those values as arguments.

### Pitfall 3: Correlation-adjusted exposure formula invented without documentation
**What goes wrong:** D-07 requires correlation-weighting but leaves the exact formula to research/planning; if the planner picks something ad hoc without documenting the choice, a future re-read of the code can't tell if the formula is intentional or a bug.
**Why it happens:** The spec states the principle, not the math — easy to improvise something plausible-looking under time pressure.
**How to avoid:** Pick one of the two researched, named approaches (variance-scaling `(1 + avg_pairwise_correlation)` factor, or cluster-cap-by-correlation-threshold) explicitly in the plan, name it as a constant (mirroring `backtest_engine.py`'s `WALK_FORWARD_CONFIG` documentation convention), and cite this research file in the code comment.
**Warning signs:** A magic-number correlation adjustment factor in code with no comment explaining which of the two approaches it implements.

### Pitfall 4: Kelly fraction computed from in-sample (not out-of-sample) stats
**What goes wrong:** `risk_engine.py` reads `win_rate`/`profit_factor`-derived payoff ratio from a strategy's raw backtest stats instead of its walk-forward-validated (`wf_passed=1`) out-of-sample stats.
**Why it happens:** `strategy_registry.py` stores both the original backtest columns (`win_rate`, `profit_factor`, etc. from `save_strategy()`) and the separate walk-forward columns (`wf_passed`, `wf_fold_results` from `save_walk_forward_result()`) — it's easy to read the wrong column.
**How to avoid:** D-02 requires Kelly inputs come from "a strategy's own backtest/walk-forward stats already persisted" — per the quant-finance-math skill's own warning ("os parâmetros win_rate e payoff_ratio devem vir de validação walk-forward out-of-sample, nunca do backtest in-sample"), prefer `wf_fold_results`'s aggregate out-of-sample stats when `wf_passed=1` and `revalidated_on_real_data=1` are both true (per the existing gate note in `strategy_registry.py`), falling back to raw backtest stats only with an explicit, logged warning if walk-forward data isn't yet available for that strategy.
**Warning signs:** `risk_engine.py` querying `strategies.win_rate`/`strategies.profit_factor` directly without checking `wf_passed`/`revalidated_on_real_data` first.

### Pitfall 5: Kill-switch treated as "stop everything" instead of "stop new orders"
**What goes wrong:** Implementing the kill-switch to force-close all open positions immediately, which (per REQUIREMENTS.md's own "Out of Scope" section) can realize an asymmetric loss on one leg of a hedge.
**Why it happens:** "Kill switch" sounds like it should kill everything; the correct, narrower semantics (block new orders only) require reading the spec carefully.
**How to avoid:** RISK-06 and D-09 both specify the kill-switch blocks *new* orders — existing positions continue to be managed normally (stops still apply). This matches the project's explicit out-of-scope note: "Fechar todas as posições instantaneamente em qualquer desconexão... usa-se antes o modo 'apenas gestão'."
**Warning signs:** Any code path where kill-switch detection triggers a close-all/liquidate-all action rather than simply refusing new `RiskDecision.approved = True` results.

## Code Examples

### Kelly fraction (verbatim project pattern)
```python
# Source: .claude/skills/quant-finance-math/SKILL.md (already in this repo)
def kelly_fraction(win_rate: float, payoff_ratio: float, fraction: float = 0.25) -> float:
    """payoff_ratio = ganho médio / perda média. fraction = 0.25 per D-01 (this phase)."""
    b = payoff_ratio
    p = win_rate
    q = 1 - p
    f_star = (b * p - q) / b
    return max(0.0, f_star * fraction)   # never negative — never bet against your own edge
```

### Exposure aggregation with correlation adjustment (variance-scaling approach)
```python
# Source: pattern synthesized from correlation-adjusted exposure research (WebSearch,
# cross-checked across 2+ independent sources) — pick this OR the cluster-cap approach,
# document the choice explicitly in the plan (see Pitfall 3 above)
def aggregate_exposure_pct(open_positions: list[dict], correlation_matrix: dict) -> float:
    """open_positions: [{'pair_a': str, 'pair_b': str, 'exposure_pct': float}, ...]
    correlation_matrix: {(pair_a, pair_b): float} lookup from Layer 0 output.
    Returns the correlation-adjusted aggregate exposure to compare against
    RiskLimits.max_aggregate_exposure_pct (D-07, 15%)."""
    raw_sum = sum(p["exposure_pct"] for p in open_positions)
    if len(open_positions) < 2:
        return raw_sum
    pairs = [(a, b) for i, a in enumerate(open_positions) for b in open_positions[i+1:]]
    corrs = [
        abs(correlation_matrix.get((a["pair_a"], b["pair_a"]), 0.0))
        for a, b in pairs
    ]
    avg_corr = sum(corrs) / len(corrs) if corrs else 0.0
    return raw_sum * (1 + avg_corr)
```

### Kill-switch check (file-based, per D-09/D-13)
```python
# Source: pattern synthesized from file-based kill-switch research (WebSearch)
# + docs/risk_engine_mql5_spec.md's own recommendation
import os

KILL_SWITCH_PATH = "KILL_SWITCH.flag"   # exact path convention TBD by planner —
                                          # must be readable from both Python cwd and
                                          # MQL5's FILE_COMMON scope (shared folder)

def is_kill_switch_active(path: str = KILL_SWITCH_PATH) -> bool:
    return os.path.exists(path)
```
```mql5
// Source: .claude/skills/mql5-trading-ea/SKILL.md (already in this repo)
if(FileIsExist("KILL_SWITCH.flag", FILE_COMMON))
{
    Print("KILL SWITCH ATIVO: nenhuma ordem nova será executada.");
    return false;
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Testing MQL5 logic only via Strategy Tester + historical data | Separating pure trading-math into isolated `.mqh` classes + standalone `.mq5` Script test runners (TestLite.mqh pattern) | Documented in MQL5's own official articles as of the current article series (article #19154, part II of a debugging/profiling series) — this is the current recommended community practice, not a deprecated one | Enables testing `RiskGuard.mqh` in isolation before the EA/IPC bridge exist, directly closing this phase's flagged research gap |

**Deprecated/outdated:** Nothing in this domain is deprecated — Kelly criterion, drawdown circuit breakers, and file-based kill-switches are all long-standing, still-current risk-management techniques with no newer replacement paradigm. The only genuinely evolving piece is MQL5 tooling ecosystem maturity (community test frameworks remain immature/lightly maintained relative to mainstream language ecosystems — this is a persistent gap, not a recent regression).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The correlation-adjusted exposure formula (`(1 + avg_pairwise_correlation)` scaling factor) is the right choice vs. the cluster-cap alternative | Code Examples, Don't Hand-Roll | If the planner's chosen formula behaves unexpectedly (e.g. over-penalizes 3+ correlated positions vs. pairwise), exposure limits could be too strict or too loose relative to D-07's intent — low real-money risk since this is Phase 2 (demo-only, pre-real-account), but could require rework in a later phase |
| A2 | `python-telegram-bot` is legitimate despite the automated legitimacy-gate's `SUS` verdict | Package Legitimacy Audit | If the override reasoning is wrong (unlikely given the direct PyPI JSON evidence of an 11-year release history), a compromised or unexpected package could be installed — mitigated by the recommended `checkpoint:human-verify` gate before install regardless of the override |
| A3 | SQLite vs. flat JSON for persisting risk-engine state (drawdown HWM, kill-switch status across restarts) is a planner discretion point, not yet decided | Alternatives Considered | If the planner picks JSON but the state needs transactional guarantees (e.g. concurrent read from an alert-checker thread), could hit race conditions; low risk at this phase's single-process, single-trader scale |
| A4 | MQLUnit/MTUnit are not worth adopting vs. a hand-rolled TestLite.mqh-style pattern | Don't Hand-Roll | If the hand-rolled assertion helper turns out to need more features (test discovery, fixtures) as the risk-check suite grows, some rework may be needed later — low risk given the phase's narrow scope (a handful of risk functions, not a large EA) |

**If this table is empty:** N/A — see entries above.

## Open Questions

1. **Exact correlation-weighting formula for D-07 (variance-scaling vs. cluster-cap)**
   - What we know: Both approaches are standard, documented practitioner patterns (see Code Examples / Don't Hand-Roll); the spec states the principle but not the formula (explicitly left to planner/research per D-07's own text).
   - What's unclear: Which one better fits this project's specific pair-count (max 3 concurrent pairs / 6 legs per D-08) and correlation matrix shape (Engle-Granger cointegrated pairs from Layer 0, which by construction already tend to be correlated within a pair but not necessarily across pairs).
   - Recommendation: The planner should pick the variance-scaling approach (`(1 + avg_pairwise_correlation)`) as the primary recommendation — it degrades gracefully to `raw_sum` when there's only 0-1 positions open (no correlation to adjust for), and it's simpler to unit-test than a cluster-cap approach requiring a threshold + grouping step, while still satisfying "correlated pairs count more toward the limit."

2. **Where exactly should the kill-switch file live (path shared between Python cwd and MQL5's FILE_COMMON)?**
   - What we know: `docs/risk_engine_mql5_spec.md` names it `KILL_SWITCH.flag`; MQL5's `FileIsExist(..., FILE_COMMON)` reads from the terminal's shared "Common\Files" folder, not an arbitrary path.
   - What's unclear: The exact filesystem path Python should write to so both sides see the same file — this is inherently tied to the Phase 3/4 IPC bridge's shared folder convention, which doesn't exist yet.
   - Recommendation: For this phase, Python's `is_kill_switch_active()` can accept any path as a parameter (testable in isolation with a temp file); the *real* shared path only needs to be finalized when Phase 3's file-bridge module is built. Do not block this phase on resolving the exact path — keep it configurable.

3. **Should `python-telegram-bot` be installed this phase, or should alerting be stubbed/deferred?**
   - What we know: D-10/D-11 lock Telegram as the channel and define the trigger conditions; ALERT-01 itself is tagged as Phase 4 in REQUIREMENTS.md's traceability table, but D-11 says the 80%-of-limit *drawdown* trigger is this phase's concern (the EA-down and connection-loss triggers are Phase 4, since the EA/bridge don't exist yet).
   - What's unclear: Whether the planner should build the actual Telegram-sending code now (drawdown-approaching-limit alert only) or stub the alert interface and defer real sending to Phase 4 when all three trigger conditions can be wired together.
   - Recommendation: Build the drawdown-approaching-limit alert now (it's fully testable in isolation with synthetic account state, no bridge/EA dependency), using the zero-dependency `requests.post` approach from "Alternatives Considered" rather than adding `python-telegram-bot` — defers the package-installation decision without blocking the phase's own requirements.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.10+ | `risk_engine.py` | check confirmed | 3.12.10 [VERIFIED: local environment] | — |
| pytest | RISK-08 synthetic adversarial test suite | check confirmed | 9.1.1 [VERIFIED: local environment] | — |
| MetaTrader5 (pip package) | Not required this phase (only for `--mode mt5` data fetch, Layer 0) | check confirmed (present, not needed here) | 5.0.5735 [VERIFIED: local environment] | N/A — this phase's tests use synthetic account state, no live MT5 connection needed |
| MetaEditor / MT5 terminal (for compiling/running `.mq5`/`.mqh`) | `RiskGuard.mqh` + standalone test Script | Not verified in this session (Windows GUI app, cannot probe via shell) | — | If unavailable in the execution environment, `RiskGuard.mqh` and its test Script can still be *written* and reviewed as source, but cannot be compiled/run until a Windows machine with MT5/MetaEditor installed executes them — flag this explicitly as a manual verification step for the human user, consistent with CLAUDE.md's Windows-only platform requirement |
| `python-telegram-bot` (optional) | D-10 alerting, only if planner chooses this path over `requests` | Not installed; confirmed available on PyPI at 22.8 | 22.8 [VERIFIED: pip index versions] | `requests.post` to Telegram Bot API HTTP endpoint (stdlib-adjacent, near-zero new dependency) |

**Missing dependencies with no fallback:**
- MetaEditor/MT5 terminal availability for actually compiling and running `RiskGuard.mqh` + its test Script — this is a Windows GUI application (matches CLAUDE.md's platform requirement) that cannot be probed from this shell session; the planner should include an explicit manual-verification task ("open in MetaEditor, compile with F7, run the test Script") rather than assuming CI-style automated compilation is available.

**Missing dependencies with fallback:**
- `python-telegram-bot` — not installed, but `requests` (near-universally available) is a viable substitute for this phase's simple one-way alert use case.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No | This phase has no user-facing authentication surface — it's an internal risk-decision module, not exposed externally |
| V3 Session Management | No | No session concept in this phase |
| V4 Access Control | Partial — yes | The kill-switch file itself is a de facto access-control mechanism (its presence/absence gates order flow); ensure the file path is not writable by anything other than the trader's own local process/filesystem — no remote write path exists in this phase's scope, so this is a "keep it that way" control rather than a new one to build |
| V5 Input Validation | Yes | Every value crossing from `hedge_engine.py`'s proposed order (or, in this phase's synthetic tests, adversarial test inputs) into `risk_engine.py`/`RiskGuard.mqh` must be validated: symbol must be a known/valid instrument, lot size must be positive and finite, price/SL values must be sane (non-negative, not NaN/Inf) — this is exactly what RISK-08's adversarial test suite (D-14: oversized lots, negative/zero lots, invalid symbols, duplicate orders) is designed to exercise |
| V6 Cryptography | No | No secrets requiring cryptographic storage in this phase's core risk logic; the Telegram bot token (if implemented) is a credential, not a cryptographic primitive — see below |
| V9 Communications | Partial — yes | If Telegram alerting is implemented, the bot token must not be hard-coded in source or committed to git — use an environment variable or a local untracked config file (consistent with the project's existing pattern of passing MT5 credentials via CLI args rather than hard-coding, per `.claude/CLAUDE.md`'s "Configuration" section) |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| Adversarial/malformed order input (oversized lots, negative/zero lots, invalid symbols, duplicate submissions) — this is RISK-08's explicit scope | Tampering / Denial of Service | Input validation at the `risk_engine.py` boundary (reject non-finite/negative/zero lot sizes, validate symbol against a known-instrument allowlist, deduplicate by an idempotency key such as a proposed-order hash+timestamp) — exactly the synthetic adversarial test suite this phase must build (D-14) |
| Kill-switch file spoofing or accidental deletion mid-session | Tampering | Since the kill-switch is presence-based (file exists = blocked), the only failure mode is *false absence* (file deleted/missing when it should exist) — not a spoofing risk in the traditional sense since there's no remote attacker in this single-trader local system; the main real risk is the trader's own filesystem tooling accidentally cleaning up the flag file — document the exact path clearly so it isn't swept by a generic "clean temp files" script |
| Telegram bot token leakage via commit/log | Information Disclosure | Never commit the token; never log the full token string (log only "alert sent" / "alert failed", not request payloads containing the token) — consistent with the project's existing "no `.env` used, credentials passed at runtime" convention, though a local untracked `.env`/config file for the bot token is a reasonable and common exception worth allowing here since CLI-arg-passing a Telegram token isn't practical for a background alerting call |
| Race condition between Python writing risk-state and EA reading it (relevant once the file-based bridge exists in Phase 3/4) | Tampering / Denial of Service | Out of this phase's direct scope (the bridge isn't built yet), but the risk-decision *interface* shape chosen now (a dict/JSON with `approved`/`size_lots`/`sl_price`/`reject_reason`) should avoid any field whose partial/interrupted write could be misread as valid (e.g. avoid multi-step numeric fields; write the whole JSON object atomically once the bridge is built) — flag this for Phase 3/4 planning, not a Phase 2 build item |

## Sources

### Primary (HIGH confidence)
- `docs/risk_engine_mql5_spec.md` — canonical, already-written project spec for this exact phase [VERIFIED: local file, read in full]
- `.claude/skills/quant-finance-math/SKILL.md` — Kelly criterion formula/code pattern [VERIFIED: local file, read in full]
- `.claude/skills/mql5-trading-ea/SKILL.md` — MQL5 hedging-mode check, lot/stop normalization, kill-switch file check, heartbeat pattern [VERIFIED: local file, read in full]
- `src/strategy_registry.py`, `src/backtest_engine.py` — established idempotent-migration and named-constant-table conventions to mirror [VERIFIED: local file, read in full]
- `.planning/phases/02-deterministic-risk-engine/02-CONTEXT.md` — locked numeric decisions D-01 through D-14 [VERIFIED: local file, read in full]
- Local environment probes (`python --version`, `pytest --version`, `pip index versions python-telegram-bot`, PyPI JSON API for release history) [VERIFIED: command output, this session]

### Secondary (MEDIUM confidence)
- MQL5.com official article on debugging/profiling/testing MQL5 code (TestLite.mqh/TradeMathCore.mqh separation pattern) — cross-checked against community forum discussion of the same gap (single-entry-point limitation, MQLUnit/MTUnit's Strategy-Tester dependency) [CITED: mql5.com/en/articles/19154, mql5.com/en/forum/360627]
- Correlation-adjusted exposure/Kelly aggregation formulas — cross-checked across 2+ independent WebSearch sources describing the same variance-scaling and cluster-cap approaches [tagged MEDIUM per classify-confidence seam, cross-source corroboration]
- Fractional Kelly (quarter-/half-Kelly) as industry-standard practitioner adjustment — cross-checked across 3+ independent sources, consistent with the project's own already-written skill file [tagged MEDIUM]
- File-based kill-switch polling pattern for trading bots — cross-checked across multiple sources, consistent with the project's own spec's recommendation [tagged MEDIUM]

### Tertiary (LOW confidence)
- MTUnit GitHub repository maintenance status (could not confirm a specific last-updated date; only 3 commits visible) — flagged as not recommended for adoption partly *because of* this uncertainty, not despite it
- `python-telegram-bot` PyPI weekly-download count — registry lookup returned `null`; package legitimacy overridden on the strength of release-history evidence instead (see Package Legitimacy Audit)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new required dependencies beyond stdlib; the one optional new package (`python-telegram-bot`) was directly verified against the PyPI registry and its release history
- Architecture: HIGH — the risk-engine-boundary pattern (Python pre-trade / MQL5 last-line-of-defense, duplicated not shared) was already established in Phase 1's project-level research (`.planning/research/SUMMARY.md`) and confirmed unchanged here; this phase adds the specific implementation pattern for making the MQL5 side testable in isolation
- Pitfalls: HIGH — pitfalls 1, 4, and 5 are drawn directly from the project's own existing documentation (ARCHITECTURE.md anti-patterns, strategy_registry.py docstrings, REQUIREMENTS.md out-of-scope notes); pitfalls 2 and 3 are this research's own synthesis, cross-checked against official MQL5 documentation and multiple independent correlation-formula sources

**Research date:** 2026-07-01
**Valid until:** 2026-08-01 (30 days — risk-management math and MQL5 testing patterns are stable domains; re-verify `python-telegram-bot` version if not installed within 30 days, and re-check MQLUnit/MTUnit maintenance status if adoption is reconsidered later)
