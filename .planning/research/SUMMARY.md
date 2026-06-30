# Project Research Summary

**Project:** Forex AI — algorithmic scalping system (Python research/ML/hedge stack + MQL5 execution)
**Domain:** Algorithmic/quant forex trading — statistical hedge engine, ML-driven regime detection, deterministic risk management, MT5 execution
**Researched:** 2026-06-30
**Confidence:** MEDIUM

## Executive Summary

This milestone takes an already-working research stack (data pipeline + strategy lab with human-gated validation, both proven on synthetic data) and builds the production layers needed to trade real capital safely: a deterministic risk engine (Python + MQL5, duplicated by design), a production hedge engine with regime-aware strategy switching, a baseline ML signal model (LightGBM), an automated fast-track validation gate, and the MQL5 Expert Advisor itself. Across stack, features, architecture, and pitfalls research, one conclusion is consistent and unambiguous: **the risk engine must be built first, must be fully independent of any ML inference, and must be duplicated (not merely mirrored) between Python and MQL5** so the EA can enforce limits even if the Python process is dead. This is not a stylistic preference — it is the single most repeated pattern across institutional risk-architecture guidance, MQL5 community failure post-mortems, and the project's own existing specs, all of which independently converge on the same answer.

The recommended approach: extend the existing `backtest_engine.py`/`strategy_registry.py`/`dashboard.py` lab with a real walk-forward, cost-aware revalidation pass (closing a known gap), then build `risk_engine.py` + `RiskGuard.mqh` together against synthetic adversarial orders before any live logic exists, then layer the production hedge engine (with periodic cointegration re-testing and regime-based strategy selection) and finally the MQL5 EA with a heartbeat/manage-only-mode contract. The ML model (LightGBM) and the automated validation gate can be developed in parallel with the risk/bridge work since neither is on the critical path to a safe demo loop — but neither should be wired into the live order path until the risk engine and EA are proven with synthetic orders.

The dominant risks are not technological but methodological: strategy-generator overfitting via repeated hypothesis testing (multiple comparisons), backtests that ignore transaction costs (especially dangerous for scalping, where costs can consume the entire edge), walk-forward validation implemented in name only, cointegration treated as a permanent property instead of continuously re-tested, and — the most architecturally dangerous failure mode specific to this system — Python process death leaving an orphaned hedge leg with no risk management. Every one of these has a concrete, already-partially-specified mitigation; the job of the roadmap is to sequence work so these mitigations are built-in from the start rather than retrofitted under time pressure before a demo-to-real transition.

## Key Findings

### Recommended Stack

The existing Python stack (pandas 2.3.x, numpy, statsmodels, hmmlearn, scikit-learn, pyarrow, streamlit) stays as-is; pandas 3.0 (just released) should be explicitly avoided this milestone due to known time-series index/dtype regression risk. New additions are narrowly scoped to what the remaining layers need.

**Core technologies:**
- LightGBM 4.6.0 — gradient-boosting baseline for camada 1 (directional signal + confidence) — already chosen in project specs; interpretable (SHAP/feature importance) which matters for auditing a system that risks real capital, unlike a neural net
- MetaTrader5 (pip) 5.0.5735 — Python↔MT5 bridge, already in use for data; pin must match installed terminal build or it fails silently
- Optuna 4.9.0 — defer until random+mutation search in the strategy lab is demonstrably the bottleneck; not a must-have for this milestone
- `sklearn.model_selection.TimeSeriesSplit` (already a dependency) — the single mechanism to drive walk-forward for both the ML model and the new `backtest_engine.walk_forward_validate()` entry point, avoiding two divergent validation implementations
- File-based IPC (shared JSON, already specced) first; pyzmq + a vetted MQL5-ZeroMQ fork only if file-polling latency is *measured* (not assumed) to be insufficient — do not pre-optimize

**Explicitly avoid this milestone:** deep learning (LSTM/Transformer) as the ML baseline, full/un-fractional Kelly sizing, pandas 3.0, `dingmaotu/mql-zmq` used unforked (known compile errors), Backtrader framework adoption (the project's own custom backtest engine is purpose-built and should be extended, not replaced).

### Expected Features

**Must have (table stakes — system is unsafe without these):**
- Deterministic stop-loss on every order, enforced in both `risk_engine.py` and the EA
- Fractional Kelly (0.25x–0.5x) or equivalent capped position sizing
- Correlation-adjusted exposure caps (per-pair and portfolio-wide) — already more sophisticated than typical retail EAs, appropriately so for a pairs-hedge system
- Daily/weekly max-drawdown circuit breaker, max simultaneous positions cap, manual kill-switch file
- EA-side redundant risk checks that never trust Python blindly (defense in depth)
- Heartbeat/connection-loss fail-safe in the EA (degrade to "manage-only," never panic-close, never freeze)
- Lot/stop normalization against broker symbol specs; hedging-mode account confirmation at EA startup
- Objective backtest gate before any strategy reaches production, extended with **mandatory walk-forward out-of-sample revalidation** — currently a known, explicitly-flagged gap
- Transaction costs (spread/slippage/commission) in every backtest, not bolted on later
- Real-time regime detection actually wired to live strategy selection (not just an offline label)
- Pre-real-money checklist gate (4–6 week demo soak, explicit human confirmation) and basic alerting (Telegram/email) — buildable early, no dependency on ML/hedge layers

**Should have (differentiators):**
- Automated fast-track validation gate for background-generated candidates — must be *stricter* than the manual gate (extra OOS pass, trade-concentration check, shadow period), never a looser shadow of it
- Regime-conditioned model confidence / per-regime accuracy tracking
- Live performance-vs-backtest divergence monitor (rolling-window threshold first; CUSUM-style detection later)
- PnL in "R" units decoupling strategy logic from sizing (already implemented — preserve this separation when building the hedge/risk engines)

**Defer (v2+):**
- CUSUM-style sequential decay detection (after simple threshold monitor is proven)
- Socket/ZeroMQ IPC (after file-based is proven insufficient, measured not assumed)
- Deep learning models (after a validated gradient-boosting baseline shows a specific capacity gap)

### Architecture Approach

The existing 5-layer design (data/regime → strategy lab/registry → ML model → hedge engine → risk engine → MQL5 execution) is correct and does not need restructuring — this milestone adds files and a few new responsibilities, not new top-level structure. The critical architectural decision is the **risk engine boundary**: `risk_engine.py` is the pre-trade authority (sizing, exposure/drawdown checks, kill-switch read) when Python is alive; `RiskGuard.mqh` is the last-line-of-defense authority inside the EA with zero dependency on Python liveness. Both must encode identical numeric limits from a shared config, not approximations of each other.

**Major components:**
1. Live regime detector (extends existing HMM, runs continuously not as a batch script) → feeds strategy selector
2. Strategy registry extended with regime tagging + `auto_validator.py` (new) wrapping the *same* `validate_strategy()` function the dashboard uses — never a forked, looser gate
3. `hedge_engine.py` (new) — strategy selector keyed by (pair, regime), proposes orders only, never executes directly; includes a divergence monitor that triggers mechanical strategy-swap or exposure-to-zero, never online retraining
4. `risk_engine.py` + `RiskGuard.mqh` (new, paired) — deterministic, duplicated, no ML import path
5. `bridge/` module (new) — IPC behind an interface (`send_order`, `read_fills`, `heartbeat`), file-based first, swappable to ZeroMQ later without touching hedge/risk code
6. `ScalpingEA.mq5` (new) — order placement, heartbeat watchdog, manage-only degraded mode

### Critical Pitfalls

1. **Strategy generator overfitting via repeated hypothesis testing** — the current generate→test→mutate loop will find spuriously good parameters by chance; fix with a strict three-way data split, candidate-count logging, and a second held-out confirmation window before any production promotion.
2. **Transaction costs ignored in `backtest_engine.py`** — currently computes PnL in raw "R" with no spread/slippage/commission; must be fixed in the backtest engine itself before real-data revalidation, not deferred to the hedge engine layer — scalping economics make this potentially fatal to the entire edge.
3. **Walk-forward implemented in name only** — window type/length/train-test gap must be fixed and documented *before* touching real data, applied identically to every candidate, with a minimum trades-per-fold requirement; rolling (not anchored) windows are recommended given the project's own regime-dependent thesis.
4. **Risk engine silently coupling to ML inference** — the realistic failure mode is gradual (e.g., "skip an exposure check if ML confidence is high"), not a deliberate violation; prevent structurally by ensuring `risk_engine.py` never imports `ml_model.py`, by freezing Kelly-fraction inputs at approval time, and by code-review-gating any PR that adds an `if confidence_ml` branch near risk logic.
5. **Python-process-down leaves orphaned hedge legs unmanaged** — the file-based IPC bridge has no inherent liveness signal; every signal file needs a timestamp, the EA must independently age out to manage-only mode on timeout, and "kill Python mid-position" must be an explicit, performed test before any demo trading begins.

## Implications for Roadmap

### Phase 1: Walk-Forward & Cost-Aware Revalidation of the Strategy Lab
**Rationale:** Closes the most consequential already-known gap (no separated test period, no transaction costs) before any production code consumes lab output.
**Delivers:** `backtest_engine.walk_forward_validate()` using `TimeSeriesSplit`, fixed/documented window methodology, realistic per-symbol cost modeling, dashboard showing net-of-cost metrics.
**Addresses:** Objective backtest gate, walk-forward validation (table stakes).
**Avoids:** Pitfall 1 (overfitting), Pitfall 2 (cost blindness), Pitfall 3 (fake walk-forward).

### Phase 2: Deterministic Risk Engine (Python + MQL5, Paired)
**Rationale:** True root dependency for everything downstream.
**Delivers:** `risk_engine.py` + `RiskGuard.mqh`, tested against synthetic adversarial orders, structurally verified to have zero dependency on `ml_model.py`.
**Implements:** The Risk Engine Boundary pattern (duplicated, not single-sourced authority).

### Phase 3: File-Based IPC Bridge + Heartbeat Contract
**Rationale:** Must be validated end-to-end with synthetic orders before real strategy logic depends on it.
**Delivers:** `bridge/file_bridge.py`, `mql5/Bridge.mqh`, timestamped signal files, EA-side timeout/manage-only logic, a performed "kill Python mid-position" test.
**Avoids:** Pitfall 7 (orphaned hedge legs).

### Phase 4: Production Hedge Engine with Periodic Cointegration Re-Testing
**Rationale:** First component whose output (proposed orders) reaches the risk engine and bridge.
**Delivers:** `hedge_engine.py`, rolling Engle-Granger re-tests, correlation-breakdown exit independent of z-score.
**Avoids:** Pitfall 6 (cointegration treated as permanent).

### Phase 5: Regime Tagging + Live Strategy Selector
**Rationale:** Depends on Phase 1 and Phase 4 (needs registry + working hedge engine).
**Delivers:** Registry schema with regime column, strategy selector keyed by (pair, regime), "reduce to zero" fallback, continuous live regime detector.
**Avoids:** Anti-Pattern 2 (ML confidence picking strategy instead of regime registry).

### Phase 6: MQL5 Expert Advisor (Full Execution)
**Rationale:** RiskGuard.mqh (Phase 2) must already be stable before order-placement logic is finalized.
**Delivers:** `ScalpingEA.mq5` with CTrade order placement, mandatory stop-loss, hedging-mode pre-flight check, lot/stop normalization.

### Phase 7: ML Baseline Model (LightGBM) — Parallelizable
**Rationale:** Off the critical path for demo safety; only needs Layer 0 features.
**Delivers:** `ml_model.py` with regime-conditioned signal/confidence, weekly retrain, explicit "model unavailable" fallback.
**Avoids:** Pitfall 4 (silent ML coupling into risk logic).

### Phase 8: Automated Fast-Track Validation Gate + Divergence Monitor
**Rationale:** Deferred until walk-forward (Phase 1) and risk engine (Phase 2) are proven; explicit "Pending" decision in PROJECT.md.
**Delivers:** `auto_validator.py` (stricter than dashboard gate), rolling-window live-vs-backtest divergence monitor.
**Avoids:** Pitfall 5, Anti-Pattern 1 (looser shadow gate), Anti-Pattern 4 (divergence as retrain trigger).

### Phase Ordering Rationale

- Risk engine before everything execution-related is non-negotiable per all four research files independently.
- Walk-forward/cost revalidation precedes the hedge engine so production logic isn't built on illusory numbers.
- IPC bridge with heartbeat is validated in isolation before real strategy logic depends on it (costliest failure mode in pitfalls research).
- ML model and automated gate are explicitly parallelizable/deferrable, off the critical path.
- Regime-driven switching needs both gate/registry work and the hedge engine — a content dependency, not just code.

### Research Flags

Needs research: Phase 3 (MQL5-ZeroMQ ecosystem, LOW confidence sourcing), Phase 5 (no canonical regime→strategy registry reference architecture), Phase 8 (concentration/independence algorithm still an open Pending decision).

Standard patterns (skip research-phase): Phase 2 (risk engine, already well-specced), Phase 4 (cointegration re-testing, already well-specced), Phase 7 (LightGBM + walk-forward, standard pattern).

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM | Version facts cross-checked across multiple sources; MQL5/IPC library choices explicitly LOW within file |
| Features | MEDIUM | Project specs HIGH (already decided); external industry-norm claims MEDIUM, cross-checked 2+ sources |
| Architecture | MEDIUM | No canonical reference architecture for retail MQL5+Python; cross-validated against project's own specs |
| Pitfalls | MEDIUM | Individually LOW-confidence web sources, but corroborate each other and project's own CONCERNS.md |

**Overall confidence:** MEDIUM

### Gaps to Address

- MQL5-ZeroMQ library choice (LOW confidence) — re-verify by compiling fork's example EA before integration, only if file-based proves insufficient
- Automated gate's concrete trade-independence/concentration algorithm — decide exact thresholds during Phase 8 planning
- Triple-barrier labeling — implement directly in `ml_model.py` rather than adopt `mlfinlab` (licensing concern)
- Real broker spread/commission data — only available once demo account exists; design Phase 1 cost modeling to accept broker-specific parameters
- M1 vs M5 signal timeframe — confirm via walk-forward accuracy comparison during Phase 7, not decided upfront

## Sources

### Primary (HIGH confidence)
- Project's own `docs/risk_engine_mql5_spec.md`, `docs/hedge_engine_spec.md`, `docs/ml_model_spec.md`, `docs/strategy_lab_spec.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md`, `.planning/codebase/STACK.md`, `.planning/codebase/ARCHITECTURE.md`
- Bank of England PRA Supervisory Statement SS5/18 (Algorithmic Trading)

### Secondary (MEDIUM confidence)
- PyPI/GitHub/readthedocs for LightGBM, MetaTrader5, Optuna, scikit-learn, hmmlearn, pandas, APScheduler
- MQL5.com official articles and forum posts on Python↔MT5 socket integration
- Nurp, AlgoBulls, QuantStrategy.io, QuantInsti, Bookmap, AlgoTrading101
- QuantConnect docs; Northfield (Thomas K. Philips) CUSUM monitoring paper

### Tertiary (LOW confidence)
- `coke5151/mql5-zmq`, `ding9736/MQL5-ZeroMQ`, `dingmaotu/mql-zmq`
- `mlfinlab`/`triple-barrier` PyPI licensing landscape
- MQL5 Freelance job posting on Watchdog/Heartbeat EA pattern

---
*Research completed: 2026-06-30*
*Ready for roadmap: yes*
