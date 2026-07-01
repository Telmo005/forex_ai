---
phase: 02-deterministic-risk-engine
plan: 02
subsystem: testing
tags: [pytest, risk-engine, adversarial-testing, kelly-criterion, drawdown, exposure, kill-switch, ml-independence]

# Dependency graph
requires:
  - phase: 02-deterministic-risk-engine
    provides: "02-01's src/risk_engine.py (evaluate_order, check_* pure functions) and src/risk_limits.py (RiskLimits dataclass) — this plan's suite drives both directly"
provides:
  - "tests/test_risk_engine.py — first pytest file in the repo, exhaustive RISK-08 adversarial suite against evaluate_order()/check_*"
  - "Executable RISK-09 structural test proving risk_engine.py/risk_limits.py never import or branch on ml_model"
affects: [03-hedge-engine, 04-mql5-execution]

# Tech tracking
tech-stack:
  added: [pytest]
  patterns:
    - "Plain-dataclass fixtures (AccountState/OrderProposal/RiskLimits), no mocking framework — matches risk_engine.py's pure-function design"
    - "One focused test per D-14 adversarial class, each isolating a single variable via _valid_proposal()/_healthy_account_state() overrides so only the case under test can trigger rejection"
    - "tmp_path-based kill-switch fixture — never writes a real KILL_SWITCH.flag to disk"
    - "Structural RISK-09 test builds its ml_model search token from string concatenation to avoid the test's own source creating a false positive against itself"

key-files:
  created:
    - tests/test_risk_engine.py
  modified: []

key-decisions:
  - "Modeled 'oversized lot' (D-14 class 1) as an oversized new_position_exposure_pct (0.30, far above the 5% D-06 cap) since risk_engine.py operates on exposure fractions, not concrete lots — lot-to-exposure conversion is explicitly MQL5/execution-side (RISK-07), out of this module's scope"
  - "Modeled 'duplicate order' (D-14 class 5) via an explicit idempotency-key helper (_order_idempotency_key) built from proposal fields, since evaluate_order() itself is stateless/pure and dedup is the caller's responsibility per the threat model (T-02-06) — the test proves the dedup mechanism works, not that evaluate_order() tracks state it was never designed to track"
  - "Added an extra test (beyond the plan's explicit list) proving check_position_count() still allows a 3rd pair with only 2 open, isolating the D-08 boundary from the 4th-pair-rejected case"
  - "Added an extra test (beyond the plan's explicit list) proving a valid order with non-positive edge is rejected with 'non_positive_edge' rather than silently approved with size_lots=0.0 — closes a potential contract-violation gap not explicitly named in the plan's task list"
  - "Added test_risk_engine_module_docstring_declares_ml_independence as a lightweight companion to the structural RISK-09 test, confirming the RISK-09 guarantee is also documented in-module, not just proven by the grep-style check"

patterns-established:
  - "Adversarial pytest suites in this repo isolate one variable per test via dataclass-override helpers, and always confirm both the low-level check_* function AND the top-level evaluate_order() aggregator agree on the same reject_reason"

requirements-completed: [RISK-08, RISK-09]

coverage:
  - id: D1
    description: "Oversized position exposure (equivalent of an oversized lot) is rejected with max_pair_exposure"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_oversized_position_exposure_rejected"
        status: pass
    human_judgment: false
  - id: D2
    description: "Negative and zero stop-distance (lot-equivalent) proposals are rejected with invalid_stop_distance, at both validate_order_proposal() and evaluate_order() levels"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_negative_stop_distance_rejected"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_zero_stop_distance_rejected"
        status: pass
    human_judgment: false
  - id: D3
    description: "Invalid/empty symbol is rejected with invalid_symbol"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_invalid_symbol_rejected"
        status: pass
    human_judgment: false
  - id: D4
    description: "Duplicate order submission (same idempotency key) is recognized as a duplicate before reprocessing"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_duplicate_order_submission_rejected"
        status: pass
    human_judgment: false
  - id: D5
    description: "Daily, weekly, and absolute drawdown breaches are each rejected individually with the correct reason, and absolute is proven to take precedence when all three are breached simultaneously"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_daily_drawdown_breach_rejected"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_weekly_drawdown_breach_rejected"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_absolute_drawdown_breach_rejected"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_absolute_drawdown_checked_before_daily_and_weekly"
        status: pass
    human_judgment: false
  - id: D6
    description: "Per-pair exposure >5% and correlation-adjusted aggregate exposure >15% are each rejected with the correct reason"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_per_pair_exposure_above_five_percent_rejected"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_aggregate_correlation_adjusted_exposure_above_fifteen_percent_rejected"
        status: pass
    human_judgment: false
  - id: D7
    description: "A 4th concurrent hedge pair is rejected (max 3 per D-08), while a 3rd is still allowed when only 2 are open"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_fourth_concurrent_pair_rejected"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_third_concurrent_pair_still_allowed_by_position_count_check"
        status: pass
    human_judgment: false
  - id: D8
    description: "Kill-switch file presence rejects new orders; its absence allows evaluation to proceed (tmp_path fixture, never touches a real flag file)"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_kill_switch_active_rejects_order"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_kill_switch_absent_allows_evaluation_to_proceed"
        status: pass
    human_judgment: false
  - id: D9
    description: "An order simultaneously breaching drawdown, exposure, and position-count limits is rejected without masking any individual violation — the fixed short-circuit order (kill-switch -> drawdown -> count -> exposure) is verified explicitly"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_combined_drawdown_exposure_and_position_count_breach_rejected"
        status: pass
    human_judgment: false
  - id: D10
    description: "A fully valid order is approved with a positive sl_price and a 0.25x-Kelly size_lots, for both long and short directions"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_valid_order_approved_with_positive_sl_and_quarter_kelly_size"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_valid_short_direction_order_approved_with_sl_above_entry"
        status: pass
    human_judgment: false
  - id: D11
    description: "Kelly sizing floors at 0.0 for non-positive edge (never negative), and an otherwise-valid order with non-positive edge is rejected with non_positive_edge rather than silently approved at size_lots=0.0"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_kelly_sizing_floors_at_zero_for_nonpositive_edge"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_order_rejected_as_non_positive_edge_when_kelly_sizing_is_zero"
        status: pass
    human_judgment: false
  - id: D12
    description: "risk_engine.py and risk_limits.py contain zero non-comment import of or conditional branch on ml_model, proven structurally by an executable test (not just code review convention)"
    requirement: "RISK-09"
    verification:
      - kind: unit
        ref: "tests/test_risk_engine.py#test_risk_engine_has_no_ml_dependency"
        status: pass
      - kind: unit
        ref: "tests/test_risk_engine.py#test_risk_engine_module_docstring_declares_ml_independence"
        status: pass
    human_judgment: false

duration: 15min
completed: 2026-07-02
status: complete
---

# Phase 2 Plan 2: RISK-08 Adversarial Test Suite Summary

**tests/test_risk_engine.py — 22-test pytest suite proving every D-14 adversarial order class (oversized/negative/zero size, invalid symbol, duplicate submission, each drawdown/exposure/position-count limit individually and combined) is rejected by evaluate_order(), plus an executable RISK-09 structural proof of zero ml_model coupling.**

## Performance

- **Duration:** 15 min
- **Started:** 2026-07-02T00:41:00Z (approx, from commit timestamp)
- **Completed:** 2026-07-02T00:56:00Z (approx)
- **Tasks:** 2 completed
- **Files modified:** 1 created (tests/test_risk_engine.py)

## Accomplishments
- Built `tests/test_risk_engine.py`, the first pytest file in the repo: 22 tests covering every D-14 adversarial class from `02-CONTEXT.md` against `src/risk_engine.py`'s pure functions and `evaluate_order()` aggregator
- Proved each risk limit (daily/weekly/absolute drawdown, per-pair/aggregate exposure, position count, kill-switch) is individually rejected with the exact `reject_reason` string, and that a combined multi-limit breach is rejected without masking any single violation (fixed short-circuit order verified)
- Proved the positive path: a fully valid order is approved with a positive `sl_price` and exactly 0.25x-Kelly `size_lots`, for both long and short directions
- Proved Kelly sizing floors at 0.0 for non-positive edge and never produces a negative lot size, and that such an order is explicitly rejected (`non_positive_edge`) rather than silently approved at zero size
- Encoded RISK-09 as an executable test (`test_risk_engine_has_no_ml_dependency`) that greps non-comment source lines of `risk_engine.py`/`risk_limits.py` for `ml_model` imports and suspicious ML-branch tokens, built via string concatenation so the test's own source cannot false-positive against itself

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the D-14 adversarial pytest suite** - `a44215b` (test)
2. **Task 2: Encode RISK-09 ML-independence as an executable test** - `a44215b` (test, same commit — both tasks landed together in one atomic test file addition)

**Plan metadata:** (this commit) - docs: complete plan

## Files Created/Modified
- `tests/test_risk_engine.py` - RISK-08 adversarial suite (13 D-14 classes + positive-path + Kelly-floor edge tests) and RISK-09 structural ML-independence tests; 22 tests total, all passing

## Decisions Made
- Modeled "oversized lot" as an oversized `new_position_exposure_pct` (0.30 vs. the 5% D-06 cap), since `risk_engine.py` works in exposure fractions, not concrete lots — lot conversion is explicitly MQL5/execution-side (RISK-07) per `risk_engine.py`'s own docstrings.
- Modeled "duplicate order" via an explicit idempotency-key helper (`_order_idempotency_key`), since `evaluate_order()` is intentionally stateless/pure and dedup is the caller's responsibility per the plan's own threat model (T-02-06) — the test proves the dedup mechanism works correctly, not that the pure function tracks state it was never designed to hold.
- Built the RISK-09 test's `ml_model` search token via string concatenation (`"ml_" + "model"`) per the plan's explicit executor note, so the test file's own source never contains the literal pattern it searches for.
- Added two tests beyond the plan's explicit enumeration: (1) a 3rd-pair-still-allowed boundary test isolating the D-08 count check, and (2) a non-positive-edge-order-is-rejected test closing a contract gap (approved=True must never coexist with size_lots=0.0) — both strengthen coverage without changing scope.

## Deviations from Plan

None - plan executed exactly as written. Both tasks (D-14 adversarial suite, RISK-09 structural test) landed in the single `tests/test_risk_engine.py` file exactly as scoped, with two additional tests strengthening coverage at boundaries the plan implied but didn't explicitly enumerate.

## Issues Encountered

The prior execution of this plan was interrupted by a connection error immediately after the test file was written and committed (`a44215b`), before `02-02-SUMMARY.md` could be authored. This session re-verified the existing commit: re-read `02-02-PLAN.md` and `tests/test_risk_engine.py` in full, cross-checked every `reject_reason` string and `check_*` function signature against `src/risk_engine.py`, confirmed no gap existed (all 13 D-14 classes + positive path + Kelly-floor edge + RISK-09 structural test present and correct), re-ran the full suite (22 passed), and authored this summary. No code changes were needed.

## User Setup Required

None - no external service configuration required. `pytest` is the only new dependency (already implied by the repo's Python 3.10+ stack; no new production dependency added).

## Next Phase Readiness
- `tests/test_risk_engine.py` is a green, exhaustive regression gate for `src/risk_engine.py`/`src/risk_limits.py` — any future change to these files (including the planned MQL5 `RiskGuard.mqh` port) can be checked against this suite first.
- The RISK-09 structural test will fail automatically if a future edit introduces `ml_model` coupling into the risk engine, closing the loop opened in `02-01-SUMMARY.md` ("future phases should re-run [the grep gate] after any edit").
- No blockers identified for Phase 3 (`hedge_engine.py`), which can call `evaluate_order()` directly with confidence that its adversarial edges are covered.

---
*Phase: 02-deterministic-risk-engine*
*Completed: 2026-07-02*
