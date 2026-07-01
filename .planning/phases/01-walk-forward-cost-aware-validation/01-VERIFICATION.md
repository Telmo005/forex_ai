---
phase: 01-walk-forward-cost-aware-validation
verified: 2026-07-01T21:30:00Z
status: passed
score: 3/3 success criteria verified (VALID-01, VALID-02 both satisfied)
behavior_unverified: 0
overrides_applied: 0
---

# Phase 1: Walk-Forward & Cost-Aware Validation Verification Report

**Phase Goal:** Strategies cannot reach production approval based on synthetic-only or cost-blind backtests — every validated candidate has survived genuine walk-forward, cost-aware testing against real market history.
**Verified:** 2026-07-01T21:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths / Success Criteria

| # | Truth (ROADMAP Success Criterion) | Status | Evidence |
|---|---|---|---|
| 1 | A dashboard-approved strategy has been revalidated walk-forward, out-of-sample, on real (non-synthetic) market data before being eligible for production use. | VERIFIED | `output/hedge_candidates.csv` on disk shows the exact 5/21 cointegrated pairs (GBPUSD/USDCHF, GBPUSD/USDCAD, USDCAD/USDCHF, USDJPY/NZDUSD, EURUSD/USDCHF) that 01-04-SUMMARY.md claims were pulled from Tickmill-Demo (login 25340933) via `--mode mt5` — this is independent corroborating evidence, not just a SUMMARY claim. `src/revalidate_walk_forward.py` is a real, runnable CLI (`--help` verified, code read in full) that calls `walk_forward_validate()` then `save_walk_forward_result(..., revalidated_on_real_data=<explicit flag>)`. The `--real-data` flag is a "dumb" boolean, never inferred from data — the honesty guarantee (synthetic can never set the flag) is structurally enforced, not just documented. `strategy_lab.db` itself does not exist in this worktree (git-worktree isolation, consistent with 01-04-SUMMARY's own explanation and 01-01/01-04's stated convention that `output/` is gitignored per-worktree) — the DB-row claim (6 strategies with `revalidated_on_real_data=1`) could not be independently re-queried, but every other piece of evidence (real MT5 data on disk matching exactly, honest-by-construction CLI, dashboard wiring, commit history) is consistent and corroborating. |
| 2 | Every backtest run — manual or automated — reports performance net of modeled spread, slippage, and commission; no metric in the dashboard is cost-blind. | VERIFIED | Code-read confirms: `run_hedge_backtest()` (`src/backtest_engine.py:230-402`) subtracts `apply_transaction_costs()` inside the trade-close block before rounding/storage. The only approval-relevant call site, `strategy_generator.py:97`, always passes `cost_params=resolve_cost_params(pair_a, pair_b)` — grepped every call site of `run_hedge_backtest` in the repo; no production/approval path calls it with `cost_params=None`. `dashboard.py` shows a net-of-cost caption + `cost_model_version` badge with no toggle to view cost-blind numbers (read in full, lines 48-80). `walk_forward_validate()` passes `cost_params` through unchanged to every fold. Test suite (run live, see below) proves `apply_transaction_costs` math and that `run_hedge_backtest(cost_params=X)` produces strictly lower `total_return_r` than `cost_params=None`. |
| 3 | The walk-forward methodology (window type, length, train/test gap, minimum trades per fold) is fixed and documented, and is applied identically to every candidate. | VERIFIED | `WALK_FORWARD_CONFIG` (`n_splits=5, max_train_size=5000, gap=0, window_type="rolling"`) and `FOLD_THRESHOLDS["min_trades_per_fold"]=6` are named module-level constants in `src/backtest_engine.py:535-552`, consumed identically by `walk_forward_validate()` for any strategy passed to it — no per-candidate override path exists in `revalidate_walk_forward.py`. `docs/strategy_lab_spec.md` "Metodologia walk-forward" section (lines 116-188) documents the exact values, the relaxed-per-fold-vs-full-aggregate gate split, and explicitly flags the rolling-vs-anchored choice as an unconfirmed research assumption (A2) for human review — this transparency is itself evidence of methodological rigor, not a gap. |

**Score:** 3/3 success criteria verified.

### CR-01 Critical Bug Fix — Verified in Current Code

**Claim under test:** 01-REVIEW.md found that `walk_forward_validate()` discarded `train_idx` and cold-started every fold's beta/z-score/correlation rolling windows at bar 0 of the isolated test slice, burning 20-80% of each fold as unusable warm-up and contradicting the code's own "mirrors the real workflow" comment. 01-REVIEW-FIX.md claims this was fixed.

**Verification performed:** Read the current `src/backtest_engine.py` directly (not the REVIEW-FIX narrative) and traced the logic by hand plus ran a live sklearn probe.

**Current code (`src/backtest_engine.py:230-402`, `walk_forward_validate` at `555-676`):**

- `run_hedge_backtest()` gained an optional `score_start: int | None` parameter (`backtest_engine.py:232`). Rolling stats (`compute_rolling_beta`, `compute_zscore`, `rolling_corr`) are computed over the *entire* series passed in, unchanged (`backtest_engine.py:293-296`). Only trades whose `entry_bar >= score_cutoff` are appended to the returned `trades` list (`backtest_engine.py:313, 385-397`). `score_start=None` (default) preserves the exact prior behavior for all existing callers (`strategy_generator.py`), verified by a passing regression test (`test_run_hedge_backtest_score_start_none_is_equivalent_to_full_scoring`).
- `walk_forward_validate()` (`backtest_engine.py:631-651`) now computes, per fold: `combined_start = train_idx[0]`, `combined_end = test_idx[-1] + 1`, slices `price_a`/`price_b` over `[combined_start:combined_end]` (i.e. real training-window history immediately preceding the test window, concatenated with the test window itself), and passes `score_start=test_start_local = test_idx[0] - combined_start` into `run_hedge_backtest()`. This means beta/z-score/correlation are warmed up causally on real preceding market data before any trade can be scored — not a cold start at bar 0 of the test fold.
- **Independent confirmation of the fold-slicing mechanics:** I ran `TimeSeriesSplit(n_splits=5, max_train_size=500, gap=0)` live against a 6000-bar range and confirmed `train_idx[0]` is non-zero and capped at exactly `max_train_size` bars immediately preceding each test window (e.g. fold 0: `train[500:999]`, test `[1000:1999]`) — i.e. `combined_start = train_idx[0]` genuinely supplies real, contiguous, causal prior history, not an arbitrary or empty slice.
- The fix is **backward-compatible**: `score_start=None` is untouched, and 6 pre-existing walk-forward tests (rolling-window shape, relaxed per-fold gate, aggregate full gate, per-fold cost-awareness) all still pass unmodified.

**Test suite specifically targeting CR-01 (read + executed, not merely claimed):**
- `test_run_hedge_backtest_score_start_excludes_trades_entered_before_cutoff` — proves trades with `entry_bar < score_start` are excluded.
- `test_run_hedge_backtest_score_start_none_is_equivalent_to_full_scoring` — proves the default path is unchanged.
- `test_walk_forward_validate_warms_up_beta_zscore_from_train_idx_not_cold_start` — the decisive test: uses `beta_window=800`/`corr_window=300` against test folds smaller than 800 bars. Under the **old, buggy** behavior this configuration could never produce a single trade in any fold (since `start = max(beta_window, corr_window)` would always exceed the fold size). Under the fixed code, trades occur because real warm-up history is now available — this is a genuine behavioral proof the fix works, not just a presence check.
- `test_walk_forward_validate_does_not_double_count_warmup_bars_across_folds` — proves `aggregate_stats["total_trades"]` equals the exact sum of per-fold `total_trades`, i.e. no warm-up bar or trade is double-counted across fold boundaries.

**Verdict on CR-01: FIX CONFIRMED CORRECT AND COMPLETE in the current code.** This is not a documentation-only claim — the logic was read line-by-line, the fold-slicing behavior was independently reproduced via a live `TimeSeriesSplit` probe, and the specific regression tests that would fail under the old cold-start bug were executed and pass.

**Remaining edge case (noted, not a blocker):** For fold 0 specifically, `TimeSeriesSplit` cannot produce a training window longer than the data available before the first split point, so fold 0's warm-up window can be *shorter* than `max_train_size` (verified live: with `max_train_size=5000` against a 19,800-bar real series, fold 0's `train_idx` is only 3,300 bars, not the full 5,000). This is only a problem if `max(beta_window, corr_window)` for a given strategy's params exceeds fold 0's actual (possibly-truncated) warm-up length. Cross-checked against `strategy_generator.py`'s `PARAM_RANGES`: `beta_window` max is 800, `corr_window` max is 300, so `max(beta_window, corr_window) <= 800` for every strategy the lab can produce — comfortably below the observed minimum fold-0 warm-up (3,300 bars) at real data volumes. **This is not a defect in the current phase's scope** (no candidate strategy can trigger it today), but it is not defensively guarded in code (no explicit assertion that fold 0's warm-up meets `max(beta_window, corr_window)`) — worth a one-line guard or comment if `PARAM_RANGES` is ever widened in a future phase, or if walk-forward is ever run against a much shorter real-data history.

### Test Suite — Executed Live By This Verification

```
C:\Users\Erick SG\AppData\Local\Programs\Python\Python312\python.exe -m pytest src/backtest_engine_test.py -q
```

**Result:**
```
..................                                                       [100%]
18 passed in 5.20s
```

All 18 tests pass, including all CR-01-specific regression tests, all cost-model tests, all walk-forward gate tests, and all code-review-fix regression tests (WR-01 through WR-04, IN-01 through IN-03 areas covered by dedicated tests where applicable). This matches — and independently confirms — 01-REVIEW-FIX.md's claimed "18 passed in 4.53s" result exactly in test count.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `src/backtest_engine.py` | `DEFAULT_COST_PARAMS`, `resolve_cost_params()`, `apply_transaction_costs()`, `run_hedge_backtest(cost_params=...)`, `walk_forward_validate()`, `WALK_FORWARD_CONFIG`, `FOLD_THRESHOLDS` | VERIFIED | All present, read in full, substantive (not stubs), wired into each other and into consumers. |
| `src/backtest_engine_test.py` | Runnable test file, no MT5/output dependency | VERIFIED | 18 tests, all synthetic in-memory data, executed live, all pass. |
| `src/strategy_registry.py` | `migrate_add_cost_columns()`, `migrate_add_walk_forward_columns()`, `save_walk_forward_result()`, idempotent migrations | VERIFIED | Read in full; migrations use `PRAGMA table_info` + conditional `ALTER TABLE`; called from `init_db()`; `save_walk_forward_result` raises `ValueError` on zero-rowcount UPDATE (IN-01 fix present). |
| `src/strategy_generator.py` | `cost_params` threaded into every `run_hedge_backtest` call | VERIFIED | Line 96-97: `cost_params = resolve_cost_params(...)` then passed unconditionally. |
| `src/revalidate_walk_forward.py` | CLI entry point for VALID-01, honest `--real-data` flag | VERIFIED | Read in full; `--real-data` defaults False; per-strategy try/except (WR-01 fix present); UTF-8 console guard fixed to `__main__`-only (WR-02 fix present). |
| `dashboard.py` | Net-of-cost display, walk-forward panel, real-data badge, no cost-blind toggle | VERIFIED | Read in full; `init_db()` called at startup before any `list_strategies()` read; graceful `st.warning` degradation for older DBs; badge logic correctly distinguishes `revalidated_on_real_data` from `wf_passed`. |
| `docs/strategy_lab_spec.md` | Cost model + walk-forward methodology documented exactly | VERIFIED | Both subsections present, exact parameter values stated, "Limitações conhecidas" section closed for both gaps with accurate reframing (production-eligibility vs. mechanism completeness). |
| `output/hedge_candidates.csv`, `output/features_*.parquet` | Real MT5 data on disk | VERIFIED | Present on disk; cointegration results match 01-01/01-04 SUMMARY claims exactly (5/21 pairs, same pair names). |
| `output/strategy_lab.db` | 6 approved strategies, `revalidated_on_real_data=1` | NOT PRESENT IN THIS WORKTREE | Gitignored per-worktree by project convention (confirmed consistent with 01-01-SUMMARY's own statement that `output/` artifacts are never committed). Cannot be independently re-queried by this verification, but all other evidence corroborates the claim was genuine (real data on disk matches exactly; the CLI's honesty mechanism is structurally sound; commit history is consistent). |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `resolve_cost_params()` [01-01] | `apply_transaction_costs()` [01-02] | data-contract test | WIRED | `test_resolve_cost_params_output_is_consumable_by_apply_transaction_costs` passes live. |
| `strategy_generator.run_strategy_lab` | `run_hedge_backtest(cost_params=...)` | direct call | WIRED | Grepped; only call site in the file, always passes resolved cost_params. |
| `save_strategy()` | `cost_model_version` column | registry write | WIRED | `strategy_registry.py:114-134` INSERT includes `record["cost_model_version"]`. |
| `walk_forward_validate()` [01-03] | `revalidate_walk_forward.py` [01-04] | CLI orchestration | WIRED | `revalidate_walk_forward.py:134` calls `walk_forward_validate(...)` then `save_walk_forward_result(...)`. |
| 01-01 recorded data provenance | `revalidated_on_real_data` flag | human-gated CLI flag | WIRED (honest-by-construction) | CLI never parses the SUMMARY itself; flag is explicit boolean, default False; only a human/checkpoint decision sets it True. |
| `wf_passed`/`wf_fold_results`/`revalidated_on_real_data` columns | dashboard walk-forward panel + badge | `list_strategies()` -> render | WIRED | `dashboard.py:217-282`, guarded against missing columns, correctly distinguishes the two flags. |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|---|---|---|---|---|
| VALID-01 | 01-01, 01-03, 01-04 | Estratégias aprovadas passam por revalidação walk-forward out-of-sample em dados reais antes de produção | SATISFIED | Mechanism (`walk_forward_validate`) proven correct (CR-01 fixed, tests pass); real-data pull independently corroborated via `hedge_candidates.csv`; honest CLI flag design prevents synthetic-as-real. |
| VALID-02 | 01-01, 01-02, 01-03 | `backtest_engine.py` modela custos de transação em toda validação, incluindo qualquer gate automático futuro | SATISFIED | Cost application lives inside `run_hedge_backtest()`'s simulation loop itself (not a downstream wrapper), so any future automated gate inherits it by construction; no reachable cost-blind approval path found. |

**Orphaned requirements check:** REQUIREMENTS.md traceability table maps only VALID-01 and VALID-02 to Phase 1 (line 68-69), and both appear in this phase's plan frontmatter (`01-01-PLAN.md`, `01-02-PLAN.md`, `01-03-PLAN.md`, `01-04-PLAN.md` `requirements:` fields). No orphans — 20/20 v1 requirements are accounted for across the roadmap, with VALID-01/VALID-02 fully claimed and covered here.

### Anti-Patterns Found

Grepped all phase-touched files (`src/backtest_engine.py`, `src/revalidate_walk_forward.py`, `dashboard.py`, `src/strategy_registry.py`) for `TBD|FIXME|XXX|TODO|HACK|placeholder-as-stub|not yet implemented`. All matches were false positives (Portuguese words like "todos" substring-matching "TODO", and "APROVADA" substring-matching "HACK"-adjacent patterns was not actually a match — re-checked manually). The genuine uses of the word "placeholder" in `backtest_engine.py` refer to the **intentionally documented** cost-value calibration gap (COST_MODEL_VERSION = "placeholder-v1"), which is explicitly named, versioned, and tracked — not an undocumented debt marker. No blocker-level anti-patterns found.

### Human Verification Required

None required to close this phase — all previously-flagged human-verify checkpoints (MT5 connection probe in 01-01, VALID-01 real-vs-fallback confirmation in 01-04) were already resolved during execution with recorded outcomes (`mt5-ok`, `valid01-real`), and this verification independently corroborated the real-data claim via the on-disk `hedge_candidates.csv` matching exactly.

One item worth optional human attention (not blocking): the 6 real-data-revalidated strategies all have `wf_passed=False` per 01-04-SUMMARY.md (the strict `min_trades_per_fold=6` gate is hard to clear with only 21-38 aggregate trades split across 5 folds). This is a legitimate, expected outcome of a conservative gate — not a defect — but it means **no strategy in this milestone is yet fully production-eligible** (VALID-01's mechanism is proven and real-data-executed, but no candidate has cleared the walk-forward bar yet). This is a data/strategy-quality observation for Phase 2+ planning, not a phase-1 gap.

### Gaps Summary

No gaps found. Both ROADMAP success criteria for VALID-01/VALID-02 are met, the CR-01 critical bug identified in code review is genuinely fixed in the current code (verified by direct code reading, live sklearn probe, and live test execution — not by trusting the REVIEW-FIX narrative), and the full test suite passes (18/18, executed live by this verification, not copied from a prior claim). Requirements traceability is clean with no orphans.

---

_Verified: 2026-07-01T21:30:00Z_
_Verifier: Claude (gsd-verifier)_
