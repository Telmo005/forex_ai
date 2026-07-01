---
phase: 01-walk-forward-cost-aware-validation
plan: 02
subsystem: backtest-engine
tags: [transaction-costs, backtest-engine, strategy-lab, sqlite-migration, dashboard]

# Dependency graph
requires: [01-01]
provides:
  - "apply_transaction_costs() helper in backtest_engine.py"
  - "run_hedge_backtest(price_a, price_b, params, cost_params=None) — cost-aware trade-close block"
  - "STANDARD_LOT_CONTRACT_SIZE constant for correct $/lot -> price-unit -> R conversion"
  - "src/backtest_engine_test.py — 5 passing tests, runnable anywhere (no MT5/output/ dependency)"
  - "strategy_registry.migrate_add_cost_columns() + cost_model_version column, invoked from init_db()"
  - "strategy_generator.run_strategy_lab threads resolve_cost_params() into every run_hedge_backtest call"
  - "dashboard.py net-of-cost caption + cost_model_version badge, graceful fallback if column missing"
  - "docs/strategy_lab_spec.md cost-blindness limitation closed"
affects: [01-03-walk-forward-mechanism, 01-04-real-data-revalidation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cost applied inside the simulation loop at trade-close time, not bolted on downstream (RESEARCH.md Pattern 1)"
    - "Commission ($/lot) converted to price-units via STANDARD_LOT_CONTRACT_SIZE (100,000 base-currency units) before dividing by entry_std to get R — dividing $/lot directly by entry_std conflates incompatible units"
    - "Additive, idempotent SQLite migration via PRAGMA table_info() + conditional ALTER TABLE, invoked from init_db()"

key-files:
  created:
    - src/backtest_engine_test.py
    - .planning/phases/01-walk-forward-cost-aware-validation/01-02-SUMMARY.md
  modified:
    - src/backtest_engine.py
    - src/strategy_generator.py
    - src/strategy_registry.py
    - dashboard.py
    - docs/strategy_lab_spec.md

key-decisions:
  - "Fixed a real unit-conversion bug found during Task 2's end-to-end verification: the plan's literal instruction (commission_per_lot / reference_lot_size relative to entry_std) divides a dollar figure directly by a price-unit spread std-dev, which are incompatible units. Verified against real EURUSD/GBPUSD MT5 data on disk: this produced commission_r ~75 (dwarfing typical multi-R trades) and total_return_r in the tens of thousands instead of a small realistic cost drag. Fixed by introducing STANDARD_LOT_CONTRACT_SIZE (100,000 base-currency units, the universal forex contract-size convention, not a broker-specific placeholder) to convert commission_per_lot to price-units first, mirroring what MT5's trade_tick_value would do in practice. Committed as a separate, clearly-labeled fix commit rather than silently folding it into Task 1's commit, since Task 1 was already committed when the bug was found."
  - "pytest was not in requirements.txt or installed anywhere on this machine — installed it (network was slow/flaky but succeeded) since the plan explicitly requires src/backtest_engine_test.py to be run via pytest. Not added to requirements.txt (out of this plan's stated files_modified scope); flagging as a note for a future plan/phase to formalize as a dev-dependency."
  - "No walk-forward columns (wf_passed, wf_fold_results, revalidated_on_real_data) were added to strategy_registry in this plan, per explicit plan instruction — those belong to plan 01-03."

patterns-established:
  - "Any future automated validation gate (v2 AUTOGATE-01) automatically inherits cost-aware numbers, because cost application lives inside run_hedge_backtest() itself, not in a downstream consumer."
  - "cost_model_version is now a first-class provenance column on every strategy row, extensible the same way for a future broker-calibrated cost model version."

requirements-completed: [VALID-02]

coverage:
  - id: D1
    description: "apply_transaction_costs() exists with correct R-unit conversion; 5 behavior tests pass including the resolve_cost_params() [01-01] -> apply_transaction_costs() [01-02] data-contract compatibility test"
    verification:
      - kind: automated
        ref: "python -m pytest src/backtest_engine_test.py -x -q -> 5 passed"
        status: pass
    human_judgment: false
  - id: D2
    description: "run_hedge_backtest(..., cost_params=<nonzero>) produces strictly lower total_return_r than cost_params=None on identical input, with realistic magnitude (not a unit-conversion blowup)"
    verification:
      - kind: automated
        ref: "test_run_hedge_backtest_with_costs_yields_lower_total_return_than_without (in backtest_engine_test.py) -> pass"
      - kind: other
        ref: "Manual smoke test against real EURUSD/GBPUSD MT5 data on disk: total_return_r 47.656 (no cost) vs 45.313 (with cost) across 141 trades, post-fix"
        status: pass
    human_judgment: false
  - id: D3
    description: "strategy_generator.run_strategy_lab resolves and passes cost_params into every run_hedge_backtest call; every strategy record carries cost_model_version"
    verification:
      - kind: automated
        ref: "grep -v '^#' src/strategy_generator.py | grep -c 'resolve_cost_params|cost_params=cost_params|COST_MODEL_VERSION' -> 4"
        status: pass
      - kind: other
        ref: "Ran strategy_generator.py --max-pairs 2 --n-initial 3 --n-generations 1 --n-per-generation 2 against real MT5 data; queried registry, confirmed cost_model_version='placeholder-v1' on every row"
        status: pass
    human_judgment: false
  - id: D4
    description: "migrate_add_cost_columns is idempotent; save_strategy's INSERT/placeholders/tuple match the extended column count; no walk-forward columns added"
    verification:
      - kind: other
        ref: "Called init_db() twice on same fresh DB path — no error; PRAGMA table_info confirmed cost_model_version present after both calls"
        status: pass
    human_judgment: false
  - id: D5
    description: "dashboard.py shows a net-of-cost caption + cost_model_version badge, no cost-blind toggle exists, graceful warning if column missing; docs/strategy_lab_spec.md no longer lists cost-blindness as an open gap"
    verification:
      - kind: automated
        ref: "grep -c cost_model_version dashboard.py -> 3"
        status: pass
      - kind: other
        ref: "python -m ast.parse on dashboard.py (syntax OK); headless `streamlit run dashboard.py` smoke test against real strategy_lab.db on disk -> HTTP 200, no exceptions in server log"
        status: pass
    human_judgment: false

# Metrics
duration: ~50min
completed: 2026-07-01
status: complete
---

# Phase 1 Plan 2: Cost-Aware Backtest Wiring Summary

**Wired transaction-cost modeling into the actual simulation loop end-to-end (engine -> strategy lab -> registry -> dashboard), fixed a real dollar/price-unit conversion bug found while verifying it against real MT5 data, and closed the cost-blindness gap named in `docs/strategy_lab_spec.md`.**

## Performance

- **Duration:** ~50 min
- **Started:** 2026-07-01 (immediately following plan 01-01)
- **Completed:** 2026-07-01
- **Tasks:** 3 of 3 completed (+ 1 correctness fix committed separately)
- **Files modified:** 5 (`src/backtest_engine.py`, `src/strategy_generator.py`, `src/strategy_registry.py`, `dashboard.py`, `docs/strategy_lab_spec.md`)
- **Files created:** 1 (`src/backtest_engine_test.py`)

## Accomplishments

- **`apply_transaction_costs()` added to `backtest_engine.py`.** Module-level, typed, small pure function mirroring `compute_zscore`'s shape: sums `spread_cost` + `slippage_cost` (price-units), converts to R by dividing by `entry_std`, subtracts that plus `commission_r`, and returns `pnl_r` unchanged when `entry_std <= 0` (reusing the existing truthy-and-positive guard idiom). `run_hedge_backtest()` now accepts `cost_params: dict | None = None` and applies costs inside the trade-close block, immediately after `pnl_r` is computed and before rounding/storage — inside the simulation loop, not bolted on downstream, so any future automated gate inherits cost-aware numbers by construction.
- **Found and fixed a real unit-conversion bug during Task 2's end-to-end verification.** The plan's literal instruction for converting `commission_per_lot` ($/lot) to R by dividing by `entry_std` (a price-unit spread std-dev) conflates two incompatible unit systems. Verified against real EURUSD/GBPUSD MT5 data on disk: this produced `commission_r ≈ 75` (dwarfing typical multi-R trades) and `total_return_r` in the tens of thousands instead of a realistic small cost drag. Fixed by introducing `STANDARD_LOT_CONTRACT_SIZE = 100_000` (the universal forex contract-size convention — 1 lot = 100,000 base-currency units — not a broker-specific placeholder) to convert commission to price-units first, mirroring what MT5's `trade_tick_value` would do in practice. Post-fix, EURUSD/GBPUSD `total_return_r` went from 47.656 (no cost) to 45.313 (with cost) across 141 trades — a small, realistic drag, as expected.
- **`src/backtest_engine_test.py` created** with 5 passing tests (all synthetic in-memory `pd.Series`, no MT5/`output/` dependency, runnable anywhere): cost-math correctness, the `entry_std <= 0` guard, empty/absent `cost_params` no-op, `run_hedge_backtest` producing a strictly lower `total_return_r` with nonzero costs, and a data-contract compatibility test proving `resolve_cost_params()` [01-01]'s actual output dict is consumable by `apply_transaction_costs()` [01-02] with no `KeyError`/type mismatch.
- **`strategy_generator.run_strategy_lab` threads `cost_params` through every backtest call.** Each candidate resolves its own per-pair `cost_params` via `resolve_cost_params(pair_a, pair_b)` [01-01] before calling `run_hedge_backtest`, and every saved record now carries `"cost_model_version": COST_MODEL_VERSION`. No CLI flag was added (costs are per-symbol engine defaults, not a lab-run parameter, per 01-RESEARCH.md Pitfall 1).
- **`strategy_registry` gained an idempotent `migrate_add_cost_columns()` migration** (`PRAGMA table_info` + conditional `ALTER TABLE ADD COLUMN`), invoked from `init_db()` so existing on-disk DBs pick up `cost_model_version` automatically. `save_strategy`'s INSERT column list, placeholders, and value tuple were extended to persist it. No walk-forward columns (`wf_passed`, `wf_fold_results`, `revalidated_on_real_data`) were added — those belong to plan 01-03 by explicit plan instruction.
- **`dashboard.py` is now unambiguously net-of-cost.** The top caption states all displayed metrics include modeled transaction costs; an `st.info` badge shows the active `cost_model_version`(s) read from the loaded registry data; a graceful `st.warning` (reusing the existing empty-state idiom) fires if an older on-disk DB lacks the column, instead of crashing. No toggle exists anywhere to view cost-blind numbers.
- **`docs/strategy_lab_spec.md` updated** — the "Modelo de custos de transação" section moved from future tense ("wiring happens in plan 01-02") to present tense, documents `STANDARD_LOT_CONTRACT_SIZE` and the `cost_model_version` provenance flow; the "Limitações conhecidas" section's cost-blindness bullet is struck through and replaced with a note that wiring is complete and the remaining gap is calibration (placeholder values), not wiring.

## Task Commits

Each task was committed atomically (plus one correctness-fix commit discovered mid-execution):

1. **Task 1: Add `apply_transaction_costs()` and wire it into `run_hedge_backtest()`** - `9e3e4be` (feat)
2. **Fix: correct commission unit conversion** (found during Task 2 verification, not part of the original task list, but a direct correction to Task 1's logic) - `9592c3a` (fix)
3. **Task 2: Thread cost_params through strategy_generator and add cost_model_version to the registry** - `8344e84` (feat)
4. **Task 3: Make the dashboard net-of-cost and update the spec** - `c3bcdb6` (feat)

## Files Created/Modified

- `src/backtest_engine.py` - Added `apply_transaction_costs()`, `STANDARD_LOT_CONTRACT_SIZE`, extended `run_hedge_backtest()` signature and trade-close block for cost application
- `src/backtest_engine_test.py` (new) - 5 tests covering cost math, edge cases, PnL-reduction property, and the 01-01/01-02 data-contract compatibility
- `src/strategy_registry.py` - Added `migrate_add_cost_columns()`, called from `init_db()`; extended `save_strategy` INSERT/tuple for `cost_model_version`
- `src/strategy_generator.py` - `run_strategy_lab` resolves and passes `cost_params` per pair, records `cost_model_version` on every strategy
- `dashboard.py` - Net-of-cost caption, `cost_model_version` badge, graceful fallback warning
- `docs/strategy_lab_spec.md` - Cost model section updated to present tense + `STANDARD_LOT_CONTRACT_SIZE` documented; cost-blindness limitation closed

## Decisions Made

- **Fixed the commission/R unit-conversion bug rather than shipping the plan's literal (but incorrect) instruction.** The plan said to derive `commission_r` from `commission_per_lot / reference_lot_size` "relative to entry_std" — implemented literally this divides dollars by a price-unit std-dev, which is dimensionally wrong and produces nonsensical results (verified: `commission_r ≈ 75` against real data, blowing up `total_return_r` into the tens of thousands). Introduced `STANDARD_LOT_CONTRACT_SIZE` (a universal, non-placeholder market convention, not a new broker-specific assumption) to fix the unit chain: $/lot → price-units (via contract size) → R (via entry_std). This stays fully within the plan's stated scope (no risk-engine sizing logic invented) while making the cost model actually correct rather than superficially "wired."
- **Committed the fix separately from Task 1**, since Task 1 was already committed when the bug was found during Task 2's end-to-end verification against real MT5 data — per the project's git convention (new commits over amends), and to keep an honest audit trail of what was found and when.
- **Installed `pytest`** (absent from this machine and from `requirements.txt`) since the plan explicitly requires running `src/backtest_engine_test.py` via `pytest`. Did not add it to `requirements.txt` since that file wasn't in this plan's `files_modified` scope — flagging as a loose end for a future plan to formalize.
- **Did not add walk-forward registry columns** in this plan, per explicit plan instruction reserving those for 01-03.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Bug found during own implementation] Commission-to-R conversion divided incompatible units**
- **Found during:** Task 2 (end-to-end smoke test of `strategy_generator` against real MT5 data on disk)
- **Issue:** Task 1's implementation (following the plan's literal wording) computed `commission_r = commission_per_lot / reference_lot_size / entry_std`, dividing a dollar figure by a price-unit spread std-dev. Against real EURUSD/GBPUSD data, `entry_std ≈ 0.1` (spread scale, not raw pip scale, because `beta` multiplies `price_b`), producing `commission_r ≈ 75` — a cost term ~15-20x larger than typical multi-R trade outcomes, and `total_return_r` values in the tens of thousands (clearly wrong; a realistic scalping strategy should show small cost drag, not this).
- **Fix:** Added `STANDARD_LOT_CONTRACT_SIZE = 100_000` (universal forex contract-size convention: 1 standard lot = 100,000 units of base currency) and converted `commission_per_lot` to price-units via this constant before dividing by `entry_std`. This mirrors what MT5's `trade_tick_value` would do with real broker data, staying consistent with the plan's own "placeholder pending real broker calibration" framing.
- **Files modified:** `src/backtest_engine.py`, `src/backtest_engine_test.py` (Test 5 updated to use the corrected conversion)
- **Verification:** Post-fix, EURUSD/GBPUSD `total_return_r` = 45.313 (with cost) vs. 47.656 (no cost) across 141 trades — realistic magnitude. All 5 tests in `backtest_engine_test.py` still pass. `strategy_generator` re-run against real data shows `total_return_r` values in normal ranges (roughly -150 to +90 across a small smoke-test batch), not exploding.
- **Committed in:** `9592c3a`

---

**Total deviations:** 1 auto-fixed (correctness bug in the plan's own literal instruction, caught by testing against real data rather than only synthetic/toy values)
**Impact on plan:** No scope creep — the fix stays entirely within `backtest_engine.py`'s cost-conversion logic; no new files, no risk-engine logic invented, no change to `run_hedge_backtest`'s public contract beyond what Task 1 already specified.

## Issues Encountered

- `pytest` was not installed anywhere on this machine (only discovered when running the plan's verify command) — installed via `pip install pytest` through a visibly slow/flaky network connection (one retry on a timeout, but it succeeded). Not a blocker, but worth noting for future plans in this environment.
- Windows console encoding (`cp1252`/similar) mangles Portuguese accented characters in some `print()`/`logging` output when piped through the Bash tool (e.g., "Concluído" renders as "Conclu�do") — cosmetic only, not a functional issue; the underlying data and files are correct UTF-8.

## User Setup Required

None. All work was self-contained within the repository and used the Python interpreter path already recorded in 01-01-SUMMARY.md.

## Next Phase Readiness

**All 3 tasks complete, plus one correctness fix.** VALID-02 is now satisfied end-to-end: `run_hedge_backtest()` is cost-aware inside its own simulation loop, `strategy_generator.py` never calls it without `cost_params`, `strategy_registry.py` persists `cost_model_version` per strategy via an idempotent migration, and `dashboard.py` shows only net-of-cost metrics with no cost-blind display path anywhere.

Plan 01-03 (walk-forward mechanism) can now build `walk_forward_validate()` on top of a `run_hedge_backtest()` that is already cost-aware — per-fold stats computed by 01-03 will automatically be net-of-cost, with no additional wiring needed. Plan 01-03 will add the `wf_passed`, `wf_fold_results`, and `revalidated_on_real_data` registry columns (explicitly deferred here).

---
*Phase: 01-walk-forward-cost-aware-validation*
*Completed: 2026-07-01*
