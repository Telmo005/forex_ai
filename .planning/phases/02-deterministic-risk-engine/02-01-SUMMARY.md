---
phase: 02-deterministic-risk-engine
plan: 01
subsystem: risk-engine
tags: [risk, kelly-criterion, drawdown, exposure, kill-switch, deterministic, python]

# Dependency graph
requires:
  - phase: 01-walk-forward-cost-aware-validation
    provides: strategy_registry.py's wf_passed/wf_fold_results/revalidated_on_real_data columns, consumed by resolve_kelly_inputs() for out-of-sample Kelly inputs
provides:
  - src/risk_limits.py — RiskLimits dataclass with all D-01..D-14 numeric limits as named, decision-ID-cited fields
  - src/risk_engine.py — pure-function pre-trade risk decision core (evaluate_order, check_kill_switch, check_drawdown_breaker, check_position_count, check_exposure_limits, size_position)
  - Structural (grep-verified) proof that risk_engine.py/risk_limits.py never import or branch on ml_model.py (RISK-09)
affects: [03-hedge-engine, 04-mql5-execution]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-function risk decision core: every check_* function takes explicit AccountState/RiskLimits/proposal inputs, returns (bool, reason|None), no hidden I/O except the deliberate kill-switch file check"
    - "Named-constant-table convention (mirrors backtest_engine.py's DEFAULT_COST_PARAMS/WALK_FORWARD_CONFIG): every D-01..D-14 numeric value is a dataclass field with an inline decision-ID comment, never a bare magic number"
    - "Variance-scaling correlation-adjusted exposure aggregation: raw_sum * (1 + avg_pairwise_correlation), degrading to raw_sum for <2 positions"
    - "JSON-serializable RiskDecision value object, shaped for the future file-based Python<->MQL5 bridge (D-13) without requiring interface changes"

key-files:
  created:
    - src/risk_limits.py
    - src/risk_engine.py
  modified: []

key-decisions:
  - "Adopted variance-scaling ((1 + avg_pairwise_correlation)) as the D-07 correlation-adjustment formula, per 02-RESEARCH.md's resolved recommendation — named as CORRELATION_ADJUSTMENT constant in risk_limits.py"
  - "Kelly inputs resolved via resolve_kelly_inputs(), which prefers wf_fold_results aggregate stats when wf_passed=1 AND revalidated_on_real_data=1, falling back to raw win_rate/profit_factor with an explicit log.warning (Pitfall 4 gate)"
  - "No alerts.py, no Telegram/requests dependency added — Phase 2 exposes drawdown_pct_of_limit() as clean queryable data only; Phase 4 (ALERT-01) owns the alerting decision/send logic, per the plan's explicit deferral note"
  - "Kill-switch and drawdown breakers block NEW orders only (never close-all/liquidate) — enforced structurally by evaluate_order()'s design: no code path in this module touches existing positions"

patterns-established:
  - "RiskLimits/AccountState/OrderProposal/RiskDecision as plain dataclasses, mirroring PipelineConfig style from data_pipeline.py"
  - "evaluate_order() fixed check ordering: validate input -> kill-switch -> drawdown -> position-count -> exposure -> sizing+SL, short-circuiting on first rejection"

requirements-completed: [RISK-01, RISK-02, RISK-03, RISK-04, RISK-05, RISK-06, RISK-09]

coverage:
  - id: D1
    description: "RiskLimits dataclass in risk_limits.py exposes all D-01..D-14 numeric risk limits as named fields citing decision IDs, matching the locked defaults exactly"
    requirement: "RISK-01"
    verification:
      - kind: unit
        ref: "python -c import-and-assert RiskLimits() field values match D-locked tuple (0.25,0.05,0.15,0.03,0.08,0.20,3)"
        status: pass
    human_judgment: false
  - id: D2
    description: "evaluate_order() returns a RiskDecision with a positive sl_price and 0.25x-Kelly-fractional size_lots for a valid order, or an explicit reject_reason otherwise"
    requirement: "RISK-01"
    verification:
      - kind: unit
        ref: "inline sanity script: evaluate_order() on valid OrderProposal returns approved=True, size_lots>0.0, sl_price>0.0, reject_reason=None"
        status: pass
    human_judgment: false
  - id: D3
    description: "Kelly-fractional position sizing at 0.25x (D-01), floored at 0.0 for non-positive edge, reading win_rate/payoff_ratio from strategy_registry with an out-of-sample preference gate"
    requirement: "RISK-02"
    verification:
      - kind: unit
        ref: "inline sanity script: kelly_fraction(0.3,1.0,0.25)==0.0 (bad edge floors to 0); kelly_fraction(0.6,1.5,0.25)>0.0 (good edge positive)"
        status: pass
    human_judgment: false
  - id: D4
    description: "Per-pair (5%) and correlation-adjusted aggregate (15%) exposure limits reject a breaching order (RISK-03/D-06/D-07), using the variance-scaling formula"
    requirement: "RISK-03"
    verification:
      - kind: unit
        ref: "inline sanity script: check_exposure_limits() with new_position_exposure_pct=0.06 returns (False, 'max_pair_exposure')"
        status: pass
    human_judgment: false
  - id: D5
    description: "Daily (3%), weekly (8%), and absolute (20%) drawdown breakers reject new orders when breached, absolute checked first as a standing kill condition"
    requirement: "RISK-04"
    verification:
      - kind: unit
        ref: "python -c plan-specified verify command: check_drawdown_breaker() at equity=8000/hwm=10000 returns (False, 'absolute_drawdown_kill_switch')"
        status: pass
    human_judgment: false
  - id: D6
    description: "Max 3 concurrent hedge pairs (D-08) enforced — a 4th position attempt is rejected"
    requirement: "RISK-05"
    verification:
      - kind: unit
        ref: "inline sanity script: check_position_count() with 3 open_positions returns (False, 'max_concurrent_pairs')"
        status: pass
    human_judgment: false
  - id: D7
    description: "File-based kill-switch (KILL_SWITCH.flag presence) instantly blocks the next evaluate_order() call with reason 'kill_switch'"
    requirement: "RISK-06"
    verification:
      - kind: unit
        ref: "inline sanity script: check_kill_switch() and evaluate_order() both return (False,'kill_switch')/reject_reason='kill_switch' once the flag file is created"
        status: pass
    human_judgment: false
  - id: D8
    description: "risk_engine.py and risk_limits.py contain zero import of or branch on ml_model.py — structurally verified by static grep, not just convention"
    requirement: "RISK-09"
    verification:
      - kind: unit
        ref: "shell: grep -v '^\\s*#' src/risk_engine.py src/risk_limits.py | grep -cE 'import ml_model|from ml_model' == 0"
        status: pass
    human_judgment: false

duration: 22min
completed: 2026-07-02
status: complete
---

# Phase 2 Plan 1: Deterministic Risk Engine Core Summary

**Python pre-trade risk authority (src/risk_engine.py + src/risk_limits.py) delivering Kelly-fractional (0.25x) sizing, per-pair/correlation-adjusted-aggregate exposure caps, daily/weekly/absolute drawdown breakers, position-count cap, and a file-based kill-switch — structurally proven free of any ml_model.py coupling.**

## Performance

- **Duration:** 22 min
- **Started:** 2026-07-02T00:26:00Z (approx, worktree file timestamps)
- **Completed:** 2026-07-02T00:48:00Z (approx)
- **Tasks:** 3 completed
- **Files modified:** 2 created (src/risk_limits.py, src/risk_engine.py)

## Accomplishments
- Built `src/risk_limits.py`: a `RiskLimits` dataclass with every D-01..D-14 numeric constant as a named field citing its decision ID, plus `KILL_SWITCH_PATH` (D-09, overridable default) and `CORRELATION_ADJUSTMENT` (names the D-07 variance-scaling formula)
- Built `src/risk_engine.py`: pure-function decision core with `check_kill_switch`, `check_drawdown_breaker`, `check_position_count`, `check_exposure_limits`, `aggregate_exposure_pct` (variance-scaling), `kelly_fraction`/`size_position` (0.25x per D-01), `resolve_kelly_inputs` (out-of-sample gate per Pitfall 4), `compute_stop_loss_price` (RISK-01), `validate_order_proposal` (adversarial input validation, D-14/T-02-01), and the top-level `evaluate_order()` aggregator returning a JSON-serializable `RiskDecision`
- Verified structurally (static grep, not just docstring/convention) that neither file imports or branches on `ml_model.py` (RISK-09)
- Exposed `drawdown_pct_of_limit()` as clean queryable data (daily/weekly/absolute fraction-of-limit) for Phase 4's ALERT-01 to consume later, without building any alerting logic in this phase

## Task Commits

Each task was committed atomically:

1. **Task 1: Create src/risk_limits.py constant tables** - `f6aa5b3` (feat)
2. **Task 2: Create src/risk_engine.py pure-function decision core** - `991fb6a` (feat)
3. **Task 3: Add ML-independence static gate assertion to Python side** - verification-only task (no code changes required; grep gate confirmed 0 matches against both already-clean files, folded into this SUMMARY's verification record — see plan's own note: "This task adds no new ml_model references; the verify gate below is the structural proof required by RISK-09")

**Plan metadata:** (this commit) - docs: complete plan

## Files Created/Modified
- `src/risk_limits.py` - RiskLimits dataclass (all D-01..D-14 limits), KILL_SWITCH_PATH, CORRELATION_ADJUSTMENT constant
- `src/risk_engine.py` - AccountState/OrderProposal/RiskDecision dataclasses; check_kill_switch, check_drawdown_breaker, check_position_count, check_exposure_limits, aggregate_exposure_pct, kelly_fraction, resolve_kelly_inputs, size_position, compute_stop_loss_price, validate_order_proposal, drawdown_pct_of_limit, evaluate_order

## Decisions Made
- Adopted variance-scaling `(1 + avg_pairwise_correlation)` for D-07 exposure aggregation, exactly as resolved in 02-RESEARCH.md Open Question 1 — named as `CORRELATION_ADJUSTMENT = "variance_scaling"` in risk_limits.py so the choice is documented, not implicit (Pitfall 3 avoidance).
- Implemented `resolve_kelly_inputs()` to read Kelly inputs from `strategy_registry.get_strategy()`-shaped dicts, preferring `wf_fold_results` aggregate stats when both `wf_passed=1` and `revalidated_on_real_data=1`, else falling back to raw `win_rate`/`profit_factor` with an explicit `log.warning` — directly implements Pitfall 4's out-of-sample gate.
- Did not create `alerts.py` or add any Telegram/`requests` dependency. Per the plan's explicit deferral note (ALERT-01 is Phase 4 scope), this phase only exposes `drawdown_pct_of_limit()` as queryable data on demand — no alert is computed or sent here.
- `evaluate_order()`'s fixed check order (validate input -> kill-switch -> drawdown -> position-count -> exposure -> sizing+SL) matches the plan's required ordering exactly, with short-circuit rejection at the first failing check.
- Added `validate_order_proposal()` as an explicit first-line input-validation step (not required by name in Task 2's behavior bullets, but directly serves T-02-01 of the plan's own threat model and RISK-08's D-14 adversarial-input scope) — rejects non-finite/non-positive prices or stop distances, invalid direction, and invalid/empty symbols before any risk-decision logic runs.

## Deviations from Plan

None - plan executed exactly as written. Task 3 required no code changes (the plan itself states "This task adds no new ml_model references; the verify gate below is the structural proof required by RISK-09") — the grep gate was run and confirmed 0 matches, satisfying the task's `<done>` criterion without any file modification.

## Issues Encountered

None. Both task-level `<verify>` commands and the plan-level `<verification>` block were run and passed exactly as specified in 02-01-PLAN.md, plus an additional broad sanity script exercising every pure function (kill-switch, drawdown, position-count, exposure, Kelly sizing, stop-loss computation, full evaluate_order approve/reject paths, adversarial input rejection) — all passed.

## User Setup Required

None - no external service configuration required. This plan added no new dependencies (only stdlib: `dataclasses`, `logging`, `math`, `os`, `json`).

## Next Phase Readiness

- `src/risk_engine.py` and `src/risk_limits.py` are ready to be exercised by Plan 02's adversarial synthetic-order test suite (RISK-08) and by the MQL5 `RiskGuard.mqh` plan.
- `hedge_engine.py` (Phase 3) can call `evaluate_order()` directly once it exists — the interface (`OrderProposal` in, `RiskDecision` out) already anticipates that caller shape and is JSON-serializable for the future file-based bridge (D-13).
- No blockers identified. The RISK-09 structural gate (grep-based) is in place and passing; future phases should re-run it after any edit to these two files, not just trust convention.

---
*Phase: 02-deterministic-risk-engine*
*Completed: 2026-07-02*
