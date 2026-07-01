---
phase: 01-walk-forward-cost-aware-validation
plan: 03
subsystem: backtest-engine
tags: [walk-forward, time-series-split, backtest-engine, sqlite-migration, validation]

# Dependency graph
requires:
  - phase: 01-walk-forward-cost-aware-validation (01-01)
    provides: "resolve_cost_params() + DEFAULT_COST_PARAMS scaffolding"
  - phase: 01-walk-forward-cost-aware-validation (01-02)
    provides: "cost-aware run_hedge_backtest(cost_params=...) so every fold is net-of-cost by construction"
provides:
  - "walk_forward_validate() in backtest_engine.py — rolling TimeSeriesSplit folds, relaxed per-fold gate, full aggregate gate"
  - "WALK_FORWARD_CONFIG (n_splits=5, max_train_size=5000, gap=0, window_type=rolling) + FOLD_THRESHOLDS (min_trades_per_fold=6) named constants"
  - "5 new tests in backtest_engine_test.py proving rolling window, relaxed gate, aggregate gate, and per-fold cost-awareness"
  - "strategy_registry.migrate_add_walk_forward_columns() (idempotent, called from init_db()) adding wf_passed / wf_fold_results / revalidated_on_real_data"
  - "strategy_registry.save_walk_forward_result() — UPDATEs an existing strategy row with the walk-forward verdict"
  - "docs/strategy_lab_spec.md 'Metodologia walk-forward' subsection documenting the exact fixed methodology"
affects: [01-04-real-data-revalidation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "sklearn.model_selection.TimeSeriesSplit with max_train_size set (not left default/None) to force rolling instead of anchored/expanding windows"
    - "Relaxed gate expressed through the same validate_strategy() function with a distinct relaxed thresholds dict, rather than a second hand-rolled validation path"
    - "Walk-forward registry write is a separate UPDATE (save_walk_forward_result) after the lab's original INSERT (save_strategy) — never a duplicate row"

key-files:
  created: []
  modified:
    - src/backtest_engine.py
    - src/backtest_engine_test.py
    - src/strategy_registry.py
    - docs/strategy_lab_spec.md

key-decisions:
  - "Rolling window chosen per research Assumption A2 — user may override to anchored/expanding if this assumption is incorrect. WALK_FORWARD_CONFIG['max_train_size'] = None restores sklearn's default anchored/expanding TimeSeriesSplit behavior; this was NOT reconfirmed in a discuss-phase step and is surfaced here explicitly for review."
  - "min_trades_per_fold=6 chosen as the conservative midpoint of RESEARCH.md's recommended 5-8 range — distinct from DEFAULT_THRESHOLDS['min_trades']=20 (aggregate), and deliberately NOT the aggregate divided by n_splits, so a fold can't sneak through on 2-3 noisy trades just because the aggregate total is large enough."
  - "gap=0 in WALK_FORWARD_CONFIG — params are already fixed (no refit happens per fold), so there is no train/test boundary information leak that a gap would need to guard against, unlike in a re-optimization setting."
  - "revalidated_on_real_data is a column and flag fully distinct from wf_passed, never derived from it — a strategy can have wf_passed=True from a synthetic-data run while revalidated_on_real_data stays False. Only a confirmed --mode mt5 run (plan 01-04) may set the latter true. This directly implements CLAUDE.md regra 7's approval-gate discipline for the walk-forward mechanism."
  - "Resumed an interrupted prior session: Task 1 (walk_forward_validate + 5 tests) was already committed (72ad7ba) and verified untouched. Task 2's registry code (migrate_add_walk_forward_columns, save_walk_forward_result) was already written but uncommitted; verified it against the plan's acceptance criteria (idempotency, UPDATE-not-INSERT, distinct flags) with a live smoke test before committing, then added the missing docs/strategy_lab_spec.md 'Metodologia walk-forward' subsection that Task 2 still required."

patterns-established:
  - "Any future validation mechanism that needs a 'relaxed local gate + full aggregate gate' shape can reuse validate_strategy() with a second thresholds dict, rather than writing a parallel gate function."
  - "Registry verdict columns for a revalidation mechanism are added via a dedicated UPDATE helper (save_walk_forward_result), keeping save_strategy's original INSERT contract untouched — a template for any future post-hoc verdict (e.g. a future live-performance flag)."

requirements-completed: [VALID-01]

coverage:
  - id: D1
    description: "walk_forward_validate() re-runs fixed strategy params across 5 sequential rolling (non-anchored) out-of-sample folds via TimeSeriesSplit(max_train_size=5000), returning fold_results + aggregate_stats + aggregate_passed + overall_passed + window_type"
    verification:
      - kind: unit
        ref: "src/backtest_engine_test.py::test_walk_forward_folds_are_rolling_not_expanding (Test 1) -> pass"
      - kind: automated
        ref: "C:\\Users\\Erick SG\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m pytest src/backtest_engine_test.py -x -q -> 10 passed"
        status: pass
    human_judgment: false
  - id: D2
    description: "Per-fold relaxed gate (positive net-of-cost return + min_trades_per_fold=6) enforced independently of the full DEFAULT_THRESHOLDS, which applies only to the concatenated aggregate out-of-sample trades"
    verification:
      - kind: unit
        ref: "src/backtest_engine_test.py Test 2 (min_trades_per_fold gate) + Test 3 (positive-return gate) + Test 4 (aggregate-fails-despite-folds-passing case) -> pass"
        status: pass
    human_judgment: false
  - id: D3
    description: "Every per-fold backtest is cost-aware (VALID-02 applies uniformly across all folds, not just one) — proven via paired zero-cost vs non-zero-cost runs asserting all(...) trade-producing folds show strictly lower net-of-cost return"
    verification:
      - kind: unit
        ref: "src/backtest_engine_test.py Test 5 (fold-by-fold cost pairing with all(...)) -> pass"
        status: pass
    human_judgment: false
  - id: D4
    description: "Registry additively and idempotently persists wf_passed / wf_fold_results / revalidated_on_real_data via migrate_add_walk_forward_columns() called from init_db(); save_walk_forward_result() UPDATEs an existing strategy row (never INSERTs a duplicate); revalidated_on_real_data is distinct from wf_passed"
    verification:
      - kind: automated
        ref: "grep -v '^#' src/strategy_registry.py | grep -c 'migrate_add_walk_forward_columns|def save_walk_forward_result|revalidated_on_real_data' -> 16"
        status: pass
      - kind: other
        ref: "Manual smoke test: init_db() called twice on same fresh DB (idempotent, no error), save_strategy() + save_walk_forward_result() round-trip confirms wf_passed=1, revalidated_on_real_data=0, and status field from save_strategy left untouched by the UPDATE"
        status: pass
    human_judgment: false
  - id: D5
    description: "docs/strategy_lab_spec.md documents the fixed walk-forward methodology exactly: window type=rolling, WALK_FORWARD_CONFIG values (n_splits/max_train_size/gap), FOLD_THRESHOLDS rationale, relaxed-vs-aggregate gate split, revalidation-not-reoptimization stance, and the revalidated_on_real_data distinction"
    verification:
      - kind: other
        ref: "docs/strategy_lab_spec.md 'Metodologia walk-forward' subsection (added between 'Modelo de custos de transação' and 'Como correr')"
        status: pass
    human_judgment: false

# Metrics
duration: ~35min (resumed session; Task 1 pre-existing from interrupted prior run)
completed: 2026-07-01
status: complete
---

# Phase 1 Plan 3: Walk-Forward Validation Mechanism Summary

**Added `walk_forward_validate()` — rolling `TimeSeriesSplit` out-of-sample folds with a relaxed per-fold gate plus the full `DEFAULT_THRESHOLDS` on the aggregate — and the additive registry columns (`wf_passed`, `wf_fold_results`, `revalidated_on_real_data`) plus exact spec documentation needed to persist and audit its verdict.**

## Performance

- **Duration:** ~35 min for this resumed session (Task 2 completion + summary); Task 1 was already complete from an earlier, interrupted session
- **Started:** 2026-07-01 (resumed after connection interruption)
- **Completed:** 2026-07-01T19:12:23+02:00
- **Tasks:** 2 of 2 completed
- **Files modified:** 4 (`src/backtest_engine.py`, `src/backtest_engine_test.py`, `src/strategy_registry.py`, `docs/strategy_lab_spec.md`)

## Accomplishments

- **`walk_forward_validate()` added to `backtest_engine.py`** (Task 1, already committed at session start). Uses `sklearn.model_selection.TimeSeriesSplit(n_splits=5, max_train_size=5000, gap=0)` to generate 5 sequential ROLLING (not anchored/expanding) out-of-sample folds. Each fold reuses the already cost-aware `run_hedge_backtest()` unchanged — no refit, no mutation of `params` per fold (this is revalidation of frozen parameters, not re-optimization). Per-fold gate is relaxed (positive net-of-cost return + `FOLD_THRESHOLDS["min_trades_per_fold"]`=6), expressed by reusing `validate_strategy()` with a distinct relaxed thresholds dict rather than a second hand-rolled gate. The aggregate gate concatenates all out-of-sample trades across folds and applies the FULL `DEFAULT_THRESHOLDS`. `overall_passed` requires both.
- **Registry migration and persistence helper** (Task 2, completed this session). `migrate_add_walk_forward_columns()` mirrors the 01-02 `migrate_add_cost_columns` pattern exactly (`PRAGMA table_info()` + conditional `ALTER TABLE`), called from `init_db()` immediately after the cost-column migration. `save_walk_forward_result(db_path, strategy_id, wf_passed, wf_fold_results, revalidated_on_real_data)` UPDATEs the existing strategy row (never a duplicate INSERT) with `wf_passed`/`revalidated_on_real_data` as INTEGER 0/1 and `wf_fold_results` as JSON, matching the existing `trades` column's serialization convention.
- **`revalidated_on_real_data` kept strictly distinct from `wf_passed`** (CLAUDE.md regra 7). A strategy can have `wf_passed=True` from a synthetic-data run while `revalidated_on_real_data` stays `False` — only a confirmed `--mode mt5` run (plan 01-04) may set the latter. Verified live: `save_walk_forward_result(..., True, [...], False)` round-trips to `wf_passed=1, revalidated_on_real_data=0` with `save_strategy`'s original fields (e.g. `status`) untouched by the UPDATE.
- **`docs/strategy_lab_spec.md` gained a new "Metodologia walk-forward" subsection** (Task 2, completed this session) documenting exactly: rolling (not anchored) window type and why, the exact `WALK_FORWARD_CONFIG` values (`n_splits=5`, `max_train_size=5000`, `gap=0`) with rationale for each, `FOLD_THRESHOLDS["min_trades_per_fold"]=6` and why it's distinct from the aggregate `min_trades=20`, the relaxed-per-fold-vs-full-aggregate gate split, the cost-always-applied-per-fold guarantee, the `revalidated_on_real_data` vs `wf_passed` distinction, and an explicit surfaced note that the rolling-window choice was adopted from a research assumption (A2) not reconfirmed in discuss-phase. Also updated the "Limitações conhecidas" section to mark the walk-forward mechanism as resolved (with the real-data execution gap explicitly deferred to plan 01-04).

## Task Commits

Each task was committed atomically:

1. **Task 1: Add walk_forward_validate() with rolling folds and dual gate** - `72ad7ba` (feat) — completed and committed in the prior, interrupted session; verified untouched and all 10 tests still passing at the start of this session.
2. **Task 2: Add walk-forward registry columns + save_walk_forward_result(), document methodology** - `5ed1482` (feat) — completed this session: verified the already-written `strategy_registry.py` code against the plan's acceptance criteria with a live smoke test (idempotent migration, UPDATE-not-INSERT semantics, distinct flags), then added the missing `docs/strategy_lab_spec.md` "Metodologia walk-forward" subsection that Task 2 still required.

## Files Created/Modified

- `src/backtest_engine.py` - `WALK_FORWARD_CONFIG`, `FOLD_THRESHOLDS`, `walk_forward_validate()` (Task 1, prior session)
- `src/backtest_engine_test.py` - 5 new tests for rolling-window behavior, relaxed per-fold gate, aggregate full gate, and per-fold cost-awareness (Task 1, prior session)
- `src/strategy_registry.py` - `migrate_add_walk_forward_columns()` (called from `init_db()`), `save_walk_forward_result()` (this session's commit)
- `docs/strategy_lab_spec.md` - New "Metodologia walk-forward" subsection; "Limitações conhecidas" bullet updated to reflect the mechanism now existing (this session's commit)

## Decisions Made

- **Rolling window chosen per research Assumption A2 — user may override to anchored/expanding if this assumption is incorrect.** `WALK_FORWARD_CONFIG["max_train_size"] = None` restores sklearn's default anchored/expanding `TimeSeriesSplit` behavior if this project's regime-dependent-scalping thesis turns out not to require rolling windows. This was NOT reconfirmed in a discuss-phase step — surfacing it here explicitly per the plan's instruction, so it's visible for review rather than silently baked in.
- **`min_trades_per_fold=6`** — the conservative midpoint of RESEARCH.md's recommended 5-8 range, distinct from and NOT derived from `DEFAULT_THRESHOLDS["min_trades"]=20` divided by `n_splits`. Prevents a fold with 2-3 noisy trades from passing just because the aggregate total across all folds clears 20.
- **`gap=0`** in `WALK_FORWARD_CONFIG` — since `params` are already fixed and never refit per fold, there's no train/test boundary leak risk that a nonzero gap would need to guard against (unlike a re-optimization workflow where the model is refit at each fold boundary).
- **Resumed-session verification approach:** rather than re-deriving Task 2's already-written registry code from scratch, verified it against the plan's exact acceptance criteria with a live smoke test (`init_db()` called twice for idempotency, `save_strategy()` + `save_walk_forward_result()` round-trip for UPDATE semantics and flag distinctness) before committing, to confirm the pre-existing uncommitted code was correct rather than assuming it.

## Deviations from Plan

None - Task 2 executed exactly as specified once resumed; the only "deviation" was procedural (resuming after a connection interruption), not a change to scope or approach. The already-written registry code matched the plan's acceptance criteria without needing correction.

## Issues Encountered

None specific to this session. Continued using the exact Python invocation recorded in `01-01-SUMMARY.md` (`C:\Users\Erick SG\AppData\Local\Programs\Python\Python312\python.exe`) since bare `python`/`py` are not reliably on PATH in this environment.

## User Setup Required

None. All work was self-contained within the repository.

## Next Phase Readiness

**Both tasks complete.** The walk-forward mechanism (`walk_forward_validate()`) is a single, documented, cost-aware code path proven on synthetic data, and the registry can persist its verdict additively with a distinct real-data flag (`revalidated_on_real_data`, separate from `wf_passed`). Plan 01-04 can now run `walk_forward_validate()` against an already-approved strategy's real MT5 data (confirmed available on disk per `01-01-SUMMARY.md`'s Task 3), call `save_walk_forward_result()` with `revalidated_on_real_data=True` only after that confirmed real-data run, and satisfy VALID-01 end-to-end.

---
*Phase: 01-walk-forward-cost-aware-validation*
*Completed: 2026-07-01*
