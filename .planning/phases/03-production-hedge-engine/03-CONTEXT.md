# Phase 3: Production Hedge Engine - Context

**Gathered:** 2026-07-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Build `src/hedge_engine.py`: the production hedge-decision layer that consumes dashboard-approved (and Phase 1 walk-forward-validated, real-data-revalidated) strategy parameters and Layer 0 outputs (features/cointegration), and produces structured order proposals (open_hedge/close_hedge records). It does NOT size positions, does NOT decide risk limits, and does NOT execute orders — every proposal passes through Phase 2's `risk_engine.py` before it can become a real order. Cointegration is treated as a continuously re-verified property, never a permanent one from the original `hedge_candidates.csv` snapshot.

</domain>

<decisions>
## Implementation Decisions

User discussed the numeric entry/exit thresholds directly (2 rounds, 4 questions); everything else was left to Claude's discretion, grounded in `docs/hedge_engine_spec.md` (already-written project spec).

### Entry logic
- **D-01:** `ENTRY_THRESHOLD = 2.0` — minimum `abs(spread_zscore)` to open a hedge position (spec's own suggestion, confirmed by user).
- **D-02:** `MIN_CORRELATION_ENTRY = 0.5` — minimum correlation to allow entry (user chose more permissive than the spec's suggested 0.6, to allow more candidate pairs through).
- Cointegration check on entry: `is_cointegrated == True` on the most recently recomputed test (never a stale snapshot) — per spec, non-negotiable, no numeric parameter needed.

### Exit logic
- **D-03:** `EXIT_THRESHOLD = 0.3` — close on reversion when `abs(spread_zscore) <= 0.3`.
- **D-04:** `MIN_CORRELATION_EXIT = 0.4` — close immediately (regardless of z-score) when recalculated correlation drops below this — the hedge premise broke down.
- **D-05:** `MAX_HOLD_BARS = 75` — time-stop: close if a position has been open this many bars (M5 timeframe) without reverting (~6h15 of market time). Note `MIN_CORRELATION_EXIT` (D-04) triggers independently and takes priority the moment correlation breaks, regardless of how long the position has been open.
- Risk-driven stop (RISK-04/05/06 circuit breakers from Phase 2) is NOT this module's authority — `hedge_engine.py` proposes, `risk_engine.py` can veto/close; this module never implements its own risk-driven exit logic (per spec, and per HEDGE-02's "propose, never execute" boundary).

### Cointegration re-verification schedule
- **D-06 (Claude's discretion):** Re-test cointegration (Engle-Granger) every **50 bars** during an open position's lifetime, and once before every new entry decision (never reuse a snapshot older than the current re-test interval). Rationale: 50 bars is roughly two checks within a typical `MAX_HOLD_BARS=75` hold period — frequent enough to catch a breaking relationship before the time-stop would otherwise mask it as "just hasn't reverted yet," while avoiding re-running Engle-Granger on every single bar (computationally wasteful for a scalping cadence). This is a named, documented, retunable constant — not a magic number — matching the project's established convention (`WALK_FORWARD_CONFIG`, `RiskLimits` style).

### Strategy source and multi-strategy handling
- **D-07 (Claude's discretion, corrected by 03-RESEARCH.md):** `hedge_engine.py` loads strategy parameters ONLY from `strategy_registry.py` rows where `status == "passed"` (the literal DB string — verified against `dashboard.py`; "✅ Aprovada" is only a UI display label, never persisted) AND `wf_passed == 1` AND `revalidated_on_real_data == 1` (Phase 1's real-data walk-forward gate) — never live-generated, never in-sample-only approved. This is HEDGE-01's exact requirement.
- **D-08 (Claude's discretion):** v1 does NOT implement regime-based strategy selection (REGIME-01/REGIME-02 are explicitly v2, per REQUIREMENTS.md and PROJECT.md Key Decisions — "trocar entre estratégias pré-aprovadas... não inventar novas ao vivo" is a *v2* live-divergence behavior). For this phase: every strategy meeting D-07's criteria is independently eligible to propose orders for its own pair — there is no "pick the single best strategy" logic in v1. `risk_engine.py`'s `max_concurrent_pairs` cap (Phase 2, D-08 = 3) is the natural backstop against over-proposing. If two approved strategies target the exact identical pair (same `pair_a`/`pair_b`), the planner should implement a simple, explicit tie-break (e.g., most-recently-approved wins, or reject the pair as ambiguous and log a warning) — not a silent first-match — but this is expected to be a rare edge case, not a core design axis.

### Claude's Discretion
- Exact tie-break rule when two approved strategies target the same pair (D-08) — planner's call, must be explicit and logged, not silent.
- Whether cointegration re-testing (D-06) runs against `output/features_*.parquet` directly or against an in-memory rolling buffer — implementation detail, not a business decision (spec already establishes: recompute from the parquet/stream, never trust the original snapshot).
- Exact structure/module boundary for how `hedge_engine.py` invokes `risk_engine.py` (direct Python function call vs. some future serialization) — Phase 2's `RiskDecision`/`evaluate_order()` interface is already JSON-serializable per Phase 2's D-13 decision; this phase should call it directly as a Python function (both modules live in the same process for now — no IPC needed until Phase 4's EA exists).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Hedge Engine Specification
- `docs/hedge_engine_spec.md` — Full spec: input format, entry/exit logic, output record structure, cointegration re-verification principle, common pitfalls to avoid (stale z-score snapshots, treating cointegration as permanent, confusing correlation with cointegration).

### Project-Level Constraints
- `.planning/PROJECT.md` §Constraints — "Produção" constraint: no strategy parameter enters `hedge_engine.py` without first being ✅ Approved via the dashboard gate.
- `.planning/REQUIREMENTS.md` §Hedge Engine de Produção — HEDGE-01, HEDGE-02, HEDGE-03, the exact v1 requirements this phase must satisfy.
- `CLAUDE.md` rule 3 — every hedge strategy must cite its exact mathematical trigger (z-score, correlation threshold, time stop) — never "close when it looks good."

### Prior Phase Artifacts (direct dependencies)
- `src/risk_engine.py` / `src/risk_limits.py` (Phase 2) — `evaluate_order()` is the function every hedge-engine proposal must be routed through before becoming a real order (HEDGE-02). `RiskDecision` is the JSON-serializable return contract.
- `src/strategy_registry.py` (Phase 1) — `get_strategy()`/`list_strategies()`, and the `status`, `wf_passed`, `revalidated_on_real_data`, `cost_model_version` columns this phase's strategy-selection logic (D-07) depends on.
- `src/backtest_engine.py` (Phase 1) — `compute_rolling_beta()`, `compute_zscore()` and the Engle-Granger cointegration pattern already used in `data_pipeline.py::scan_hedge_candidates` — reuse these exact causal, no-lookahead patterns for live re-testing (D-06), do not reimplement from scratch.
- `.planning/research/PITFALLS.md` (project-level research) — Pitfall on cointegration staleness and the "rolling re-test, not static snapshot" requirement, directly relevant to D-06.

### Skills
- `.claude/skills/quant-finance-math/SKILL.md` — Engle-Granger cointegration test and Kalman-filter (dynamic hedge ratio) patterns; consult for the re-test implementation in D-06.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/data_pipeline.py::scan_hedge_candidates()` — the existing Engle-Granger cointegration + correlation scan logic; this phase's live re-test (D-06) should reuse the same statistical test, not a different implementation, to keep entry-time and re-test-time cointegration checks consistent.
- `src/backtest_engine.py::compute_rolling_beta()` / `compute_zscore()` — causal, no-lookahead rolling calculations already used for backtesting; the production z-score recalculation this phase needs (per spec: "recalcular a cada novo candle") should reuse these exact functions, not a parallel implementation, to avoid backtest/production behavior drift.
- `src/risk_limits.py` / `src/risk_engine.py` (Phase 2) — named-constant-table and pure-function conventions to mirror for `hedge_engine.py`'s own `HEDGE_PARAMS`-style constants (D-01 through D-06) and its own pure decision functions.
- `src/strategy_registry.py` — already has the exact columns (`status`, `wf_passed`, `revalidated_on_real_data`) this phase's strategy-loading logic (D-07) needs; no schema changes required.

### Established Patterns
- Named, documented module-level constants (never magic numbers), each citing its decision ID — established in Phase 1 (`WALK_FORWARD_CONFIG`) and Phase 2 (`RiskLimits`), continue here for `ENTRY_THRESHOLD`/`EXIT_THRESHOLD`/etc.
- Pure functions with explicit inputs, structured dict/dataclass returns — established in `risk_engine.py`; this phase's `evaluate_hedge_signal()`-style function(s) should follow the same shape.
- "R" unit PnL / structured proposal records (not direct execution) — this phase's output record (per spec's suggested dict shape) continues the project's "propose, never execute" layering.

### Integration Points
- `src/hedge_engine.py` is new (currently a placeholder per `ARCHITECTURE.md`) — clean build, no existing code to extend.
- Direct integration point: every proposed order record from `hedge_engine.py` must be passed to `risk_engine.py::evaluate_order()` before it's considered anything more than a proposal (HEDGE-02) — same-process Python function call, no IPC yet.
- Future integration point (Phase 4): `hedge_engine.py`'s approved+sized proposals will eventually reach the EA via the file-based bridge — out of scope for this phase, but the output record shape (already JSON-serializable per the spec) should not need redesigning when that bridge is built.

</code_context>

<specifics>
## Specific Ideas

No additional UI/UX or example references beyond the existing `docs/hedge_engine_spec.md` and the numeric thresholds discussed above (D-01 through D-05).

</specifics>

<deferred>
## Deferred Ideas

- Regime-based strategy selection (REGIME-01/REGIME-02) — explicitly v2, deferred until Phases 1-4 run stably on demo per the roadmap's own milestone boundary.
- Live divergence circuit breaker (DIVERGE-01) — v2, needs a running hedge engine on live/demo data first before it can be meaningfully built.
- Dynamic hedge ratio via Kalman filter (mentioned in quant-finance-math skill) — the current spec and this phase use the static `hedge_ratio_beta` from Layer 0's OLS regression; a dynamic Kalman-filter-based hedge ratio is a possible future refinement, not required for v1.

### Reviewed Todos (not folded)
None — no matching todos were found for this phase (`todo.match-phase` returned 0 matches).

</deferred>

---

*Phase: 3-production-hedge-engine*
*Context gathered: 2026-07-02*
