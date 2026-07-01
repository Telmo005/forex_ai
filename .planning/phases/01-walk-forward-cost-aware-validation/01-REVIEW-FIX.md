---
phase: 01-walk-forward-cost-aware-validation
review_path: .planning/phases/01-walk-forward-cost-aware-validation/01-REVIEW.md
status: all_fixed
findings_in_scope: 8
fixed: 8
skipped: 0
iteration: 1
---

# Phase 01: Code Review Fix Report

**Iteration:** 1
**Status:** all_fixed (8/8 findings addressed — 1 critical, 4 warning, 3 info)
**Test suite:** `src/backtest_engine_test.py` — 18 passed (10 pre-existing + 8 new), 0 failed

## CR-01 (Critical) — `walk_forward_validate()` discarded training-window warm-up context

**Fixed.** `src/backtest_engine.py`

- `run_hedge_backtest()` gained an optional `score_start: int | None` parameter. Rolling
  statistics (`compute_rolling_beta`, `compute_zscore`, `rolling_corr`) are still computed
  over the *entire* series passed in (unchanged), but only trades whose `entry_bar >=
  score_start` are counted in the returned `trades`/`stats`. `score_start=None` (default)
  preserves the exact prior behavior for all existing callers (`strategy_generator.py`,
  single-window validation).
- `walk_forward_validate()` now concatenates each fold's `train_idx` + `test_idx` (real
  preceding history, not throwaway) into one combined series, and passes
  `score_start=test_start_local` (the test window's position within the combined series)
  to `run_hedge_backtest()`. This means beta/z-score/correlation are warmed up causally
  using real market history before the scored test window begins, exactly matching the
  code's own claim of "mirrors the real workflow" — which was false before this fix.
- Trade dicts inside a fold keep locally-relative `entry_bar`/`exit_bar` indices (relative
  to that fold's combined series); this is safe because `compute_stats()` only reads
  `pnl_r`/`bars_held` from trades, never absolute bar positions, so no cross-fold index
  collision is possible in `aggregate_stats`.

**Tests added** (`src/backtest_engine_test.py`):
- `test_run_hedge_backtest_score_start_excludes_trades_entered_before_cutoff` — proves
  trades with `entry_bar < score_start` are excluded, and `bars_tested` reflects only the
  scored window.
- `test_run_hedge_backtest_score_start_none_is_equivalent_to_full_scoring` — proves
  `score_start=None` is fully behavior-preserving (byte-identical `trades`/`stats`).
- `test_walk_forward_validate_warms_up_beta_zscore_from_train_idx_not_cold_start` — uses
  `beta_window=800`/`corr_window=300` against ~666-bar test folds (smaller than
  `beta_window`); under the old cold-start bug this configuration could NEVER produce a
  single trade in any fold (since `start = max(beta_window, corr_window)` would always
  exceed the fold size). With the fix, trades occur because real warm-up history is now
  available.
- `test_walk_forward_validate_does_not_double_count_warmup_bars_across_folds` — proves
  `aggregate_stats["total_trades"]` equals the exact sum of per-fold `total_trades` (no
  warm-up bar or trade is double-counted across fold boundaries, even though consecutive
  folds' warm-up windows can overlap with a prior fold's test window).

All 6 pre-existing walk-forward tests still pass unmodified, confirming the fix is
backward-compatible with the already-proven rolling-window/gate/aggregation behavior.

## Warnings (4/4 fixed)

### WR-01 — Batch revalidation aborted entirely on first missing/corrupt input

**Fixed.** `src/revalidate_walk_forward.py` — wrapped the per-strategy loop body in
`revalidate_approved_strategies()` in `try/except Exception`, logging via `log.exception`
and `continue`-ing to the next strategy on failure. A single strategy referencing a
deleted `features_{sym}.parquet`, malformed `params` JSON, or (now, post IN-01/WR-04 fixes)
a `reference_lot_size` mismatch or an orphaned `strategy_id` no longer kills the batch —
all other strategies still get revalidated and persisted.

### WR-02 — Module-level stdout/stderr reassignment broke under non-console execution

**Fixed.** `src/revalidate_walk_forward.py` — extracted the UTF-8 rewrap into
`_ensure_utf8_console()`, guarded with `hasattr(stream, "buffer")` before wrapping, and
moved the call site from unconditional module-import-time execution to
`if __name__ == "__main__":` only. Verified: `import revalidate_walk_forward` now succeeds
cleanly, and `python src/revalidate_walk_forward.py --help` still exits 0 with correctly
encoded accented/emoji output.

### WR-03 — `profit_factor = 999.0` sentinel leaked into dashboard "best profit factor" KPI

**Fixed.** `src/backtest_engine.py` + `dashboard.py` — named the sentinel
`PROFIT_FACTOR_NO_LOSSES_SENTINEL` (module-level constant, still `999.0`; kept as a finite
value rather than `float("inf")` because `wf_fold_results` is JSON-serialized via
`json.dumps`, and `Infinity` is not valid JSON per RFC 8259 even though Python's parser
accepts it — avoids a latent interop landmine). `dashboard.py`'s "Melhor profit factor" KPI
now computes `best_pf` over rows with `profit_factor < PROFIT_FACTOR_NO_LOSSES_SENTINEL`
only, and shows a caption naming how many strategies hit the sentinel when any do, so it
reads as "no losses in sample" rather than "999x edge".

**Test added:** `test_compute_stats_uses_named_sentinel_when_no_losing_trades` — asserts
`compute_stats()` returns exactly `PROFIT_FACTOR_NO_LOSSES_SENTINEL` for an all-wins trade
list, and a normal ratio otherwise.

### WR-04 — `resolve_cost_params()` silently dropped one leg's `reference_lot_size`

**Fixed.** `src/backtest_engine.py` — `resolve_cost_params()` now raises `ValueError`
immediately if `pair_a` and `pair_b` have different `reference_lot_size` values, instead of
silently using leg A's value while `commission_per_lot` (the sum of both legs) gets divided
by only that one value downstream in `run_hedge_backtest()`. Masked today because every
`DEFAULT_COST_PARAMS` entry uses `1.0`; this now fails loudly the moment a real MT5
calibration introduces differing per-symbol values, instead of silently miscalculating
`commission_r`.

**Tests added:** `test_resolve_cost_params_raises_on_reference_lot_size_mismatch` (patches
`DEFAULT_COST_PARAMS` to diverge and confirms `ValueError`) and
`test_resolve_cost_params_same_reference_lot_size_still_works` (confirms the normal/current
path is unaffected).

## Info (3/3 fixed)

### IN-01 — `save_walk_forward_result()` silently no-op'd on unmatched `strategy_id`

**Fixed.** `src/strategy_registry.py` — captured `cur = conn.execute(...)`, checked
`cur.rowcount` after commit, and raise `ValueError` if it's `0`. Combined with the WR-01
fix, this now surfaces as a clean per-strategy skip+log in the batch CLI rather than either
a silent no-op or (pre-WR-01) a full batch crash. Verified manually: calling
`save_walk_forward_result()` against a fresh `init_db()` database with a nonexistent
`strategy_id` raises `ValueError` as expected (no dedicated test file exists yet for
`strategy_registry.py`; this was outside the `backtest_engine_test.py` scope named in the
task, so verification was manual rather than a committed automated test).

### IN-02 — `int()` truncation biased integer strategy params low

**Fixed.** `src/strategy_generator.py` — changed `int(val)` to `int(round(val))` in both
`random_params()` and `mutate_params()` for `INT_PARAMS` (`max_hold_bars`, `beta_window`,
`corr_window`). Verified with a 2000-iteration bounds sweep that all generated/mutated
integer params stay within their configured `[lo, hi]` ranges after the change (no boundary
violation introduced by rounding instead of truncating).

### IN-03 — `apply_transaction_costs` dict-merge could silently overwrite a caller's `commission_r`

**Fixed.** `src/backtest_engine.py` — added an `assert "commission_r" not in cost_params`
immediately before the `{**cost_params, "commission_r": commission_r}` merge inside
`run_hedge_backtest()`, so a future caller pre-populating `commission_r` in `cost_params`
fails loudly instead of having its value silently discarded.

**Test added:** `test_run_hedge_backtest_asserts_on_preexisting_commission_r_in_cost_params`
— confirms `AssertionError` is raised when `cost_params` already contains `commission_r`.

## Verification

```
C:\Users\Erick SG\AppData\Local\Programs\Python\Python312\python.exe -m pytest src/backtest_engine_test.py -q
..................
18 passed in 4.53s
```

All modules touched (`src/backtest_engine.py`, `src/backtest_engine_test.py`,
`src/revalidate_walk_forward.py`, `src/strategy_registry.py`, `src/strategy_generator.py`,
`dashboard.py`) compile cleanly (`python -m py_compile`), and
`python src/revalidate_walk_forward.py --help` exits 0 with correct UTF-8 output.

## Commits

| Finding | Commit |
|---|---|
| CR-01 | `0cc0b09` fix(01): warm up walk-forward folds with real train_idx history (CR-01) |
| WR-04 | `535ad5d` fix(01): fail loudly on reference_lot_size mismatch in resolve_cost_params (WR-04) |
| WR-01, WR-02 | `5e90fbc` fix(01): isolate per-strategy failures and guard UTF-8 console rewrap (WR-01, WR-02) |
| WR-03 | `326bdbc` fix(01): name the profit_factor no-losses sentinel and exclude it from dashboard KPI (WR-03) |
| IN-01 | `b5b07be` fix(01): raise on save_walk_forward_result() no-op update (IN-01) |
| IN-02 | `cf5d963` fix(01): round instead of truncate integer strategy params (IN-02) |
| IN-03 | `f9b2afa` fix(01): assert commission_r isn't silently overwritten in cost_params (IN-03) |

## Nothing skipped

All 8 findings (1 critical, 4 warning, 3 info) were fixed in this iteration. No deferrals.
