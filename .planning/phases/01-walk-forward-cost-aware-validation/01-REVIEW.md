---
phase: 01-walk-forward-cost-aware-validation
reviewed: 2026-07-01T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - dashboard.py
  - docs/strategy_lab_spec.md
  - src/backtest_engine.py
  - src/backtest_engine_test.py
  - src/revalidate_walk_forward.py
  - src/strategy_generator.py
  - src/strategy_registry.py
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-07-01T00:00:00Z
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

Reviewed the walk-forward validation and cost-aware backtest changes for Phase 1. The
cost model wiring (`resolve_cost_params` → `apply_transaction_costs` →
`run_hedge_backtest`) is numerically sound — I traced the commission
unit-conversion arithmetic by hand and it lands on the correct dollar-to-R
conversion despite looking dimensionally suspicious at first read. SQL access in
`strategy_registry.py` is fully parameterized (no injection risk), and JSON
(de)serialization paths are internally consistent (all values are cast to native
Python types before `json.dumps`, so no numpy-serialization surprises).

However, the walk-forward mechanism itself (`walk_forward_validate` in
`backtest_engine.py`) has a critical correctness bug: it discards `train_idx`
entirely and calls `run_hedge_backtest()` with **only** the test-fold slice. Since
`run_hedge_backtest()` always treats bar 0 of whatever series it receives as the
start of history (`start = max(beta_window, corr_window)`), every fold effectively
restarts beta/z-score/correlation computation from scratch with no causal warm-up
context from the preceding training window. This directly contradicts the code's
own comment ("mirrors the real workflow where beta/z-score are recalculated
causally up to the start of the test") and the spec doc's implicit claim that
walk-forward folds behave like production rolling recalculation. It silently burns
20-80% of each fold's bars as warm-up (given `beta_window`/`corr_window` ranges of
200-800 against ~1000-bar test folds), and produces artificially fold-dependent,
inconsistent effective test windows — which can mask a real strategy's OOS
performance or make a bad strategy look marginally better/worse than it would in
a live rolling deployment. This is exactly the kind of walk-forward methodology
gap the project's own rules (CLAUDE.md rule 2) are designed to prevent, so it is
classified as a blocker.

Several other robustness gaps were found in the batch-revalidation CLI
(`revalidate_walk_forward.py`): a single missing/corrupt input aborts the entire
run with no partial persistence, and a module-level stdout/stderr reassignment is
fragile under non-console execution (e.g. pytest capture). A cosmetic
`profit_factor = 999.0` sentinel for the zero-losing-trades edge case can leak into
the dashboard's headline "melhor profit factor" KPI in a way that misleads a human
reviewer. These are documented below with concrete fixes.

## Critical Issues

### CR-01: `walk_forward_validate()` discards training-window context, invalidating the causal-warm-up guarantee per fold

**File:** `src/backtest_engine.py:544-552`
**Issue:**
```python
for fold_i, (train_idx, test_idx) in enumerate(tscv.split(range(n_common))):
    # A janela de treino não é usada para refit (params já fixos) — só
    # existe porque TimeSeriesSplit exige um par (train_idx, test_idx);
    # espelha o workflow real onde beta/z-score são recalculados de
    # forma causal até ao início do teste, mas nenhum parâmetro muda.
    test_a = price_a.iloc[test_idx[0]:test_idx[-1] + 1]
    test_b = price_b.iloc[test_idx[0]:test_idx[-1] + 1]

    result = run_hedge_backtest(test_a, test_b, params, cost_params=cost_params)
```
`train_idx` is computed by `TimeSeriesSplit` but never used — only the isolated
test slice is passed to `run_hedge_backtest()`. Inside `run_hedge_backtest()`,
`compute_rolling_beta`/`compute_zscore`/`rolling_corr` all operate on whatever
series they are given, starting from index 0 of that series, with
`start = max(beta_window, corr_window)` (`src/backtest_engine.py:266`) used as the
warm-up cutoff before any trade can be considered. Because only the test fold is
passed in, bar 0 of `test_a`/`test_b` is treated as if it were the start of all
history — beta and z-score have zero real lookback into the actual preceding
market data (the training window), and the first `max(beta_window, corr_window)`
bars of every fold (up to 800 bars, against ~1000-bar folds using
`WALK_FORWARD_CONFIG` defaults) are silently unusable for trading, not because
there isn't real data there (there is — it's in `train_idx`), but because the
code never gives `run_hedge_backtest()` access to it.

This means:
1. The comment's claim that this mechanism "espelha o workflow real onde
   beta/z-score são recalculados de forma causal até ao início do teste" is false
   as implemented — the real workflow would carry forward a beta/z-score computed
   from continuous preceding history, not reset the rolling windows at every fold
   boundary.
2. Effective tradeable bars per fold shrink drastically and inconsistently
   (e.g., with `beta_window=800`, `corr_window=300`, and a 1000-bar test fold,
   only ~200 bars are usable), directly undermining the `min_trades_per_fold=6`
   gate's intent — a fold could fail purely due to lost warm-up bars, or pass
   with an artificially small/biased sample.
3. Aggregate OOS stats (`aggregate_stats`, `aggregate_passed`) are computed over
   trades from folds that each independently "cold started" their rolling
   statistics, which is not equivalent to a genuine rolling out-of-sample
   evaluation and can make a bad candidate look artificially better (fewer,
   cherry-picked trades from an unrepresentative sub-window) or a decent
   candidate look artificially worse (insufficient warm-up before regime shifts
   inside the fold).

**Fix:** Pass `train_idx` context into the per-fold backtest so that rolling
statistics are warmed up on real preceding history before entering the
test window, then only count/collect trades whose `entry_bar` falls within the
test region. For example, extend `run_hedge_backtest()` (or add a wrapper) to
accept an explicit `warmup_start` index and only append to `trades` once
`i >= test_start`:

```python
for fold_i, (train_idx, test_idx) in enumerate(tscv.split(range(n_common))):
    # Feed the backtest engine train+test context so beta/z-score are warmed
    # up on real preceding history, exactly like a live rolling deployment —
    # but only count trades whose entry occurs inside the test region.
    combined_start = train_idx[0]
    combined_end = test_idx[-1] + 1
    combined_a = price_a.iloc[combined_start:combined_end].reset_index(drop=True)
    combined_b = price_b.iloc[combined_start:combined_end].reset_index(drop=True)
    test_start_local = test_idx[0] - combined_start

    result = run_hedge_backtest(combined_a, combined_b, params, cost_params=cost_params)
    oos_trades = [t for t in result["trades"] if t["entry_bar"] >= test_start_local]
    stats = compute_stats(oos_trades, len(test_idx))
    ...
```
At minimum, update the docstring/comment to accurately describe the current
behavior (cold-start per fold) if the team decides this simplification is
acceptable, rather than claiming causal continuity that doesn't exist.

## Warnings

### WR-01: Batch revalidation aborts entirely on first missing/corrupt input, losing all progress

**File:** `src/revalidate_walk_forward.py:96-115`
**Issue:**
```python
def load_price(sym: str) -> pd.Series:
    if sym not in price_cache:
        path = os.path.join(output_dir, f"features_{sym}.parquet")
        price_cache[sym] = pd.read_parquet(path)["close"]
    return price_cache[sym]

results = []
for _, row in approved.iterrows():
    strategy_id = row["id"]
    pair_a = row["pair_a"]
    pair_b = row["pair_b"]
    params = json.loads(row["params"])
    cost_params = resolve_cost_params(pair_a, pair_b)

    price_a = load_price(pair_a)
    price_b = load_price(pair_b)
```
There is no try/except around the per-strategy loop body. If any approved
strategy references a symbol whose `output/features_{sym}.parquet` no longer
exists (deleted, moved, or never generated for that symbol), or its `params`
JSON is malformed, `pd.read_parquet`/`json.loads` raises an unhandled exception
that propagates out of `revalidate_approved_strategies()` and kills the entire
batch — including all strategies that would otherwise have revalidated
successfully. No partial results are persisted (persistence happens per-strategy
inside the loop, but nothing is saved for iterations after the crash point), and
which strategies were already processed is not obvious from the traceback alone.

**Fix:**
```python
for _, row in approved.iterrows():
    strategy_id = row["id"]
    pair_a = row["pair_a"]
    pair_b = row["pair_b"]
    try:
        params = json.loads(row["params"])
        cost_params = resolve_cost_params(pair_a, pair_b)
        price_a = load_price(pair_a)
        price_b = load_price(pair_b)
        wf_result = walk_forward_validate(price_a, price_b, params, cost_params=cost_params)
    except Exception:
        log.exception(
            "Falha a revalidar estratégia %s (%s/%s) — a saltar, restantes continuam.",
            strategy_id, pair_a, pair_b,
        )
        continue
    ...
```

### WR-02: Module-level stdout/stderr reassignment breaks under non-console execution

**File:** `src/revalidate_walk_forward.py:52-55`
**Issue:**
```python
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
```
This runs unconditionally at module import time and assumes `sys.stdout`/
`sys.stderr` always expose a `.buffer` attribute. Verified interactively: any
stream object without `.buffer` (e.g. pytest's captured stdout in some capture
modes, certain subprocess/CI redirections, or a stream already wrapped by another
tool) raises `AttributeError` the moment this module is imported — not just when
`main()` runs. Given the project already has a pytest-based test file
(`backtest_engine_test.py`) for this phase, it is likely this module will need to
be imported by a future test (e.g. to test `revalidate_approved_strategies()`
directly), and that import would fail under pytest's default capture in some
configurations.

**Fix:** Guard with `hasattr` and only rewrap when safe, or move the rewrap into
`main()` so it only executes for actual CLI invocation, not on `import`:
```python
def _ensure_utf8_console():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        enc = getattr(stream, "encoding", None)
        if (enc is None or enc.lower() != "utf-8") and hasattr(stream, "buffer"):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"))

if __name__ == "__main__":
    _ensure_utf8_console()
    main()
```

### WR-03: `profit_factor = 999.0` sentinel leaks into user-facing "best profit factor" KPI

**File:** `src/backtest_engine.py:361`, `dashboard.py:88-89`
**Issue:**
```python
profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
```
A strategy with zero losing trades (plausible for a small/lucky sample near the
`min_trades=20` threshold) gets a literal `999.0` profit factor, which passes
`validate_strategy()` trivially and is persisted to the registry unchanged. The
dashboard then computes:
```python
best_pf = df["profit_factor"].max()
c4.metric("Melhor profit factor (net-of-cost)", f"{best_pf:.2f}")
```
If any strategy hits the zero-loss edge case, the headline KPI displays
`999.00` — a magic sentinel, not a real profit factor — which a human validating
strategies in the dashboard could reasonably (and wrongly) read as "one strategy
has an extraordinary 999x profit factor" rather than "a small sample had no
losing trades yet."

**Fix:** Either surface the sentinel distinctly (e.g. `float("inf")` and format
it as "∞" in the dashboard), or exclude the sentinel from the "best" aggregate
computation:
```python
best_pf = df.loc[df["profit_factor"] < 999.0, "profit_factor"].max()
```
and/or add a dashboard caption noting that 999.0 denotes "no losing trades in
sample" rather than a literal multiple.

### WR-04: `resolve_cost_params()` combines both legs' commission but only uses leg A's `reference_lot_size`

**File:** `src/backtest_engine.py:130-138`
**Issue:**
```python
return {
    "spread_cost": cost_a["spread_cost"] + cost_b["spread_cost"],
    "slippage_cost": cost_a["slippage_cost"] + cost_b["slippage_cost"],
    "commission_per_lot": cost_a["commission_per_lot"] + cost_b["commission_per_lot"],
    "reference_lot_size": cost_a["reference_lot_size"],
}
```
`commission_per_lot` is the **sum** of both legs' commissions (each leg pays its
own commission — correct), but `reference_lot_size` is taken only from leg A and
silently drops leg B's value. Today this is masked because every entry in
`DEFAULT_COST_PARAMS` uses `reference_lot_size = 1.0`, so leg A and leg B always
agree. The moment a real (non-placeholder) calibration introduces a differing
`reference_lot_size` per symbol (e.g. after MT5 `symbol_info()` calibration per
`docs/strategy_lab_spec.md` "Quando substituir"), the combined
`commission_per_lot` (sum of two legs) would be divided by only one leg's
reference lot size in `run_hedge_backtest`'s commission conversion
(`src/backtest_engine.py:312-319`), silently producing an incorrect
`commission_r` for the mismatched leg.

**Fix:** Either assert both legs share the same `reference_lot_size` and fail
loudly if not, or track and apply each leg's reference lot size independently
when converting commission to R:
```python
if cost_a["reference_lot_size"] != cost_b["reference_lot_size"]:
    raise ValueError(
        f"reference_lot_size mismatch between {pair_a} ({cost_a['reference_lot_size']}) "
        f"and {pair_b} ({cost_b['reference_lot_size']}) — commission_r conversion "
        "assumes a single shared reference lot size."
    )
```

## Info

### IN-01: `save_walk_forward_result()` silently no-ops if `strategy_id` doesn't match any row

**File:** `src/strategy_registry.py:162-177`
**Issue:** The `UPDATE ... WHERE id = ?` statement has no rowcount check. If
`strategy_id` doesn't exist (e.g. the registry was reset or the row was deleted
between `list_strategies(status="passed")` and the `save_walk_forward_result`
call), the function returns successfully having updated zero rows, and
`revalidate_walk_forward.py` logs an "estratégia revalidada" info line even
though nothing was actually persisted.

**Fix:**
```python
cur = conn.execute("""UPDATE strategies SET ... WHERE id = ?""", (...))
conn.commit()
conn.close()
if cur.rowcount == 0:
    raise ValueError(f"save_walk_forward_result: nenhuma estratégia com id={strategy_id!r} encontrada")
```

### IN-02: `int()` truncation (not rounding) in `random_params`/`mutate_params` introduces a small systematic low-bias for integer params

**File:** `src/strategy_generator.py:47`, `src/strategy_generator.py:57`
**Issue:**
```python
params[key] = int(val) if key in INT_PARAMS else round(val, 2)
```
`int(val)` truncates toward zero rather than rounding, so integer parameters
(`max_hold_bars`, `beta_window`, `corr_window`) are very slightly biased toward
lower values than a uniform draw would imply (e.g. `int(rng.uniform(200, 800))`
never produces `800` exactly and rounds every fractional draw down rather than
to the nearest integer). Not a correctness bug (still within `[lo, hi]` bounds),
but an avoidable, unacknowledged skew in the parameter search space.

**Fix:** `params[key] = int(round(val)) if key in INT_PARAMS else round(val, 2)`

### IN-03: `apply_transaction_costs` dict-merge silently overwrites a caller-provided `commission_r`

**File:** `src/backtest_engine.py:322-325`
**Issue:**
```python
pnl_r = apply_transaction_costs(
    pnl_r, entry_std, {**cost_params, "commission_r": commission_r},
    position["direction"],
)
```
If `cost_params` (as passed into `run_hedge_backtest`) ever already contains a
`commission_r` key (e.g. a future caller pre-computing it), this silently
overwrites it with the locally computed value with no warning. Currently
harmless since `resolve_cost_params()` never emits `commission_r`, but it's an
implicit contract that isn't documented or defended.

**Fix:** Either assert `"commission_r" not in cost_params` before merging, or
rename the locally computed key to avoid any ambiguity about which value wins.

---

_Reviewed: 2026-07-01T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
