# Phase 2: Deterministic Risk Engine - Context

**Gathered:** 2026-07-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the deterministic (non-ML) risk engine: `src/risk_engine.py` (Python, pre-trade authority) and `mql5/RiskGuard.mqh` + the risk-check portions of the future EA (last-line-of-defense authority). This phase delivers position sizing, exposure limits, drawdown circuit breakers, max-position caps, and a kill-switch — tested against synthetic adversarial orders. It does NOT deliver the production hedge engine (Phase 3), the full MQL5 EA order-placement logic (Phase 4), or any ML model. Risk engine code must never import or branch on `ml_model.py` output — this is a structural constraint, not just a convention (RISK-09).

</domain>

<decisions>
## Implementation Decisions

User was presented with 3 specific discussion areas (numeric risk limits, kill-switch/alerts, heartbeat/IPC) and explicitly chose "use the values already suggested in the spec, decide the rest" rather than discussing each. The decisions below are Claude's concrete choices, grounded in `docs/risk_engine_mql5_spec.md` (already-written project spec) and the Phase 1 research SUMMARY.md's industry-norm findings — not arbitrary. All numeric constants below MUST be implemented as named, documented module-level constants (never magic numbers), so the user can retune them later without needing a new discussion.

### Position sizing
- **D-01:** Kelly fraction = **0.25x** (fractional Kelly), at the conservative end of the spec's suggested 0.25x-0.5x range. Rationale: this is the first implementation of real risk logic touching (eventually) real capital — start conservative, loosen later only with demonstrated stable performance.
- **D-02:** Kelly inputs (win-rate, payoff ratio) come from a strategy's own backtest/walk-forward stats already persisted in `strategy_registry.py` (from Phase 1) — not re-derived independently.

### Drawdown circuit breaker
- **D-03:** Daily max drawdown = **3%** of account equity → blocks all new orders until next trading day (does not close existing positions).
- **D-04:** Weekly max drawdown = **8%** of account equity → same block behavior, resets next week.
- **D-05:** Absolute/overall max drawdown = **20%** of account equity → hard kill-switch equivalent (treated as a standing kill condition, not just a new-orders block), requires manual reset/confirmation to resume.

### Exposure limits
- **D-06:** Max exposure per individual pair = **5%** of account equity (risk-weighted, not notional).
- **D-07:** Max total portfolio exposure = **15%** of account equity, correlation-adjusted per the spec (positions in correlated pairs count more toward this limit than uncorrelated ones — exact correlation-weighting formula is a planner/researcher decision, not a business decision).

### Position count
- **D-08:** Max simultaneous open positions = **3 concurrent hedge pairs (6 legs)**. Rationale: this is a personal single-trader system in its first risk-engine iteration — cap kept low and simple, can be raised later once demo-proven.

### Kill-switch
- **D-09:** File-based only for this phase (`KILL_SWITCH.flag`, per spec) — checked by both `risk_engine.py` and (in Phase 4) the EA. No dashboard button in this phase; that's a nice-to-have UI addition, not a Phase 2 requirement (RISK-06 only requires "instant, verified both sides").

### Alerts
- **D-10:** Alert channel = **Telegram** (bot token + chat ID config) as the primary channel for this phase, since `ALERT-01` requires only "Telegram/email" without specifying which — Telegram is simpler to stand up for a single-trader personal system (no SMTP setup). Email can be added later without changing the alert-trigger logic.
- **D-11:** Alert triggers (per ALERT-01, already locked in REQUIREMENTS.md): drawdown approaching limit (defined as reaching 80% of whichever drawdown limit — daily/weekly/overall — is closest to breach), EA/risk-engine process stopping unexpectedly, and Python↔MQL5 connection loss. Exact 80%-of-limit threshold is Claude's discretion (spec doesn't specify a number).

### Heartbeat / Python↔MQL5 communication
- **D-12:** Heartbeat timeout = **30 seconds**, exactly as suggested in the spec ("ex.: 30s") — confirmed as the value to implement, not just an example.
- **D-13:** Communication mechanism = **file-based** (shared JSON/CSV in `MQL5/Files/`), per the spec's explicit recommendation to start simple and only migrate to socket/ZeroMQ if latency proves insufficient in practice. This phase (Python-side risk engine) does not need to build the IPC bridge itself — that's Phase 3/4 scope — but the risk engine's interface (approve/reject/size decision) must be shaped to work with this mechanism.

### Adversarial testing scope (RISK-08)
- **D-14:** "Synthetic adversarial orders" for testing must include at minimum: oversized lot sizes (beyond broker max), duplicate order submission, invalid/unknown symbols, negative or zero lot sizes, and orders that would breach each of the limits above (drawdown, exposure, position count) individually and in combination.

### Claude's Discretion
- Exact correlation-weighting formula for portfolio exposure aggregation (D-07) — left to research/planning, since the spec states the principle ("correlated pairs count more") but not the exact math.
- Exact 80%-of-limit alert threshold implementation detail (D-11).
- Whether risk_engine.py exposes its decision as a function call, a small local API, or a file-based interface — implementation detail for the planner, constrained only by "must work with the file-based bridge eventually" (D-13).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Risk Engine Specification
- `docs/risk_engine_mql5_spec.md` — Full spec for both `risk_engine.py` (Python) and `RiskGuard.mqh`/EA (MQL5) sides: Kelly fraction range, exposure/drawdown/position rules, kill-switch mechanism, EA redundant checks, hedging-mode confirmation, heartbeat timeout, IPC mechanism choice, pre-real-money checklist.

### Project-Level Constraints
- `.planning/PROJECT.md` §Constraints — "Risco" constraint: all stop-loss/drawdown/exposure logic must be deterministic, never ML-dependent.
- `.planning/REQUIREMENTS.md` §Risk Engine — RISK-01 through RISK-09, the exact v1 requirements this phase must satisfy.
- `CLAUDE.md` rule 1 — "Risco nunca depende só do modelo de ML" (risk never depends only on the ML model).

### Prior Phase Research (architecture patterns directly applicable here)
- `.planning/research/SUMMARY.md` — Project-level research: confirms the "Risk Engine Boundary" pattern (Python risk_engine.py = pre-trade authority when alive; RiskGuard.mqh = last-line-of-defense with zero Python dependency), industry-typical Kelly/drawdown/exposure norms, and the anti-pattern of letting risk logic silently couple to ML confidence.
- `.planning/research/PITFALLS.md` (if referenced further in planning) — Pitfall 4 on risk-ML coupling, structural enforcement recommendation (no `import ml_model` in risk_engine.py, enforced by code review / lint, not just convention).

### Skills
- `.claude/skills/quant-finance-math/SKILL.md` — Kelly criterion formula and code patterns; consult when implementing position sizing.
- `.claude/skills/mql5-trading-ea/SKILL.md` — MQL5 patterns for hedging-mode checks, lot/stop normalization, CTrade usage; consult for the `RiskGuard.mqh` portion of this phase.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/strategy_registry.py` — Already has `cost_model_version`, `wf_passed`, `revalidated_on_real_data` columns (Phase 1) and an established idempotent-migration pattern (`migrate_add_*_columns()` + `PRAGMA table_info()` check) — reuse this exact migration pattern if `risk_engine.py` needs any new persistent state (e.g., current drawdown high-water mark, kill-switch state).
- `src/backtest_engine.py` — `DEFAULT_COST_PARAMS`/`resolve_cost_params()` and `WALK_FORWARD_CONFIG`/`FOLD_THRESHOLDS` (Phase 1) establish the project's convention for named, documented, per-symbol/per-concept constant tables — mirror this style for the risk-limit constants in D-01 through D-14.

### Established Patterns
- Named module-level constant dicts/tuples (not magic numbers), each with an inline comment explaining provenance/rationale — established in Phase 1, must continue here per this phase's own discussion decisions.
- Type hints + dataclasses for config objects (`PipelineConfig` in `data_pipeline.py`) — likely pattern for a `RiskLimits` or similar config dataclass in `risk_engine.py`.

### Integration Points
- `src/risk_engine.py` is new (currently only a placeholder per `ARCHITECTURE.md`) — no existing code to extend, this is a clean build.
- `mql5/` directory does not yet exist — `RiskGuard.mqh` will be the first MQL5 file in this project; consult `.claude/skills/mql5-trading-ea/SKILL.md` before writing any `.mqh`/`.mq5` code.
- Future integration point (Phase 3): `hedge_engine.py` will call into `risk_engine.py` to size/approve proposed orders — risk engine's public interface should anticipate this caller shape, but Phase 3 itself is out of scope here.

</code_context>

<specifics>
## Specific Ideas

No specific UI/UX or example references from this discussion — user deferred to spec defaults. The concrete numeric decisions above (D-01 through D-14) are the "specifics" for this phase; there was no additional "I want it like X" reference beyond the existing spec document.

</specifics>

<deferred>
## Deferred Ideas

- Dashboard kill-switch button (UI convenience alongside the file-based kill-switch) — noted as a nice-to-have, not required by RISK-06; could be added in a later phase or as a small follow-up once the dashboard already shows risk-engine state.
- Socket/ZeroMQ IPC upgrade — explicitly deferred by the spec itself until file-based latency proves insufficient; not this phase's concern.
- Email alert channel (alongside Telegram) — can be added later without changing alert-trigger logic (D-10).

### Reviewed Todos (not folded)
None — no matching todos were found for this phase (`todo.match-phase` returned 0 matches).

</deferred>

---

*Phase: 2-deterministic-risk-engine*
*Context gathered: 2026-07-01*
