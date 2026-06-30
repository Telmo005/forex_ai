# Phase 1: Walk-Forward & Cost-Aware Validation - Pattern Map

**Mapped:** 2026-06-30
**Files analyzed:** 4 (3 modified, 1 modified UI)
**Analogs found:** 4 / 4 (all are modifications of existing files — analog is the file's own existing patterns)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|---------------|
| `src/backtest_engine.py` — `run_hedge_backtest()` cost params (MODIFIED) | service / simulation core | transform (CRUD-like: simulate trades from price series) | `src/backtest_engine.py::run_hedge_backtest` (lines 62-146, itself) | exact (in-place extension) |
| `src/backtest_engine.py` — `apply_transaction_costs()` (NEW helper) | utility | transform | `src/backtest_engine.py::compute_zscore` (lines 51-55) — small pure-function utility pattern | role-match |
| `src/backtest_engine.py` — `walk_forward_validate()` (NEW) | service / orchestrator | batch (fold-by-fold backtest orchestration) | `src/strategy_generator.py::run_strategy_lab` (lines 61-124) — loop that calls backtest + validate per candidate and aggregates | role-match (closest available orchestration-loop pattern) |
| `src/strategy_registry.py` — schema migration + extended `save_strategy`/columns (MODIFIED) | model / persistence (SQLite) | CRUD | `src/strategy_registry.py::init_db` + `SCHEMA` (lines 18-51) — itself, additive migration | exact (in-place extension) |
| `src/strategy_generator.py` — pass `cost_params` through to backtest calls (MODIFIED, minor) | service / orchestrator (CLI entry) | CRUD/batch | `src/strategy_generator.py::run_strategy_lab` (lines 81-124), `main()` (lines 127-166) | exact (in-place extension) |
| `dashboard.py` — net-of-cost metrics + walk-forward detail panel (MODIFIED) | component (Streamlit UI) | request-response (UI render from registry query) | `dashboard.py` detail section (lines 104-177), equity-curve panel (lines 133-149) | exact (in-place extension) |
| `docs/strategy_lab_spec.md` — document cost model + WF methodology (MODIFIED) | config/doc | — | N/A (documentation, not code) | n/a |

## Pattern Assignments

### `src/backtest_engine.py` — cost-aware `run_hedge_backtest()` + `apply_transaction_costs()`

**Analog:** the file's own existing trade-close block (this is an in-place extension, not a new file)

**Imports pattern** (lines 20-23, unchanged — no new imports needed, cost modeling uses only numpy/pandas already imported):
```python
from __future__ import annotations

import numpy as np
import pandas as pd
```

**Core pattern — trade-close block to extend** (lines 131-143, exact insertion point):
```python
if exit_reason:
    entry_std = position["entry_std"]
    pnl_raw = position["direction"] * (spread_vals[i] - position["entry_spread"])
    pnl_r = pnl_raw / entry_std if entry_std and entry_std > 0 else 0.0
    trades.append({
        "entry_bar": int(position["entry_bar"]),
        "exit_bar": int(i),
        "bars_held": int(bars_held),
        "direction": int(position["direction"]),
        "pnl_r": round(float(pnl_r), 4),
        "exit_reason": exit_reason,
    })
    position = None
```
Insert the cost subtraction immediately after `pnl_r` is computed and before `round(...)` is stored — i.e. `pnl_r = apply_transaction_costs(pnl_r, entry_std, cost_params, position["direction"])`. This keeps the existing dict shape and rounding convention (`round(float(x), 4)`) untouched, per the project's "Numeric Precision" convention (4 decimal places for trade-level R values, 3 for aggregate stats — see `compute_stats`, lines 178-188).

**Small pure-function utility pattern to mirror** (`compute_zscore`, lines 51-55 — module-level, typed, no class, single responsibility):
```python
def compute_zscore(spread: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    mean = spread.rolling(window).mean()
    std = spread.rolling(window).std()
    zscore = (spread - mean) / std
    return zscore, std
```
`apply_transaction_costs()` should follow this exact shape: module-level function (not nested), full type hints, short docstring explaining the financial "why" (per project convention "Mathematical or domain-specific logic: explain the why"), defensive `if ... <= 0: return` guard mirroring the `entry_std and entry_std > 0` pattern already used at line 134.

**Signature/params dict convention to follow** (docstring style at lines 62-73 — `params esperados:` block documenting every dict key with type and meaning):
```python
def run_hedge_backtest(price_a: pd.Series, price_b: pd.Series, params: dict) -> dict:
    """Simula a lógica de entrada/saída de docs/hedge_engine_spec.md.

    params esperados:
        entry_threshold   (float) - |z| mínimo para abrir
        ...
    """
```
Add `cost_params: dict | None = None` as a new parameter (default `None` → zero-cost, but per VALID-02/CLAUDE.md rule 4, any call from `strategy_generator.py`/`dashboard.py`/approval-gate code paths must always pass a populated `cost_params`). Document its expected keys (`spread_cost`, `slippage_cost`, `commission_r` or `reference_lot_size`) in the same `params esperados:` docstring style.

**Error/edge-case handling pattern already established** (line 134):
```python
pnl_r = pnl_raw / entry_std if entry_std and entry_std > 0 else 0.0
```
Reuse this exact `truthy-and-positive` guard idiom for any new division (e.g., converting commission to R units via `entry_std`).

---

### `src/backtest_engine.py` — `walk_forward_validate()` (NEW function)

**Analog:** `src/strategy_generator.py::run_strategy_lab` (lines 61-124) — closest existing "loop that runs backtest + validate per item, aggregates pass/fail, returns list/dict of results" pattern in this codebase.

**Orchestration-loop pattern to mirror** (lines 81-109):
```python
for gen in range(n_generations + 1):
    gen_results = []
    for pair_a, pair_b, params, parent_id in candidates:
        sid = uuid.uuid4().hex[:8]
        result = run_hedge_backtest(price_data[pair_a], price_data[pair_b], params)
        stats = result["stats"]
        passed, reasons = validate_strategy(stats)

        record = {
            "id": sid,
            ...
            **stats,
        }
        save_strategy(db_path, record)
        gen_results.append(record)
        all_results.append(record)

    passed_count = sum(1 for r in gen_results if r["status"] == "passed")
    log(f"Geração {gen}: {passed_count}/{len(gen_results)} estratégia(s) aprovada(s).")
```
`walk_forward_validate()` should follow the same shape: a per-fold loop that calls `run_hedge_backtest()`, collects a `fold_results` list of structured dicts (mirroring the `record = {...}` pattern), and computes an aggregate boolean (`overall_passed`) the same way `passed_count` is summed here — i.e. `all(f["passed"] for f in fold_results)` rather than inventing a new aggregation idiom.

**Validation gate to reuse (extend, don't reinvent)** — `validate_strategy` / `DEFAULT_THRESHOLDS` (lines 196-228):
```python
DEFAULT_THRESHOLDS = {
    "min_trades": 20,
    "min_profit_factor": 1.2,
    "min_sharpe": 0.15,
    "max_drawdown_r": 8.0,
}

def validate_strategy(stats: dict, thresholds: dict | None = None) -> tuple[bool, list[str]]:
    th = thresholds or DEFAULT_THRESHOLDS
    reasons = []
    if stats["total_trades"] < th["min_trades"]:
        reasons.append(f"poucos trades ({stats['total_trades']} < {th['min_trades']}) ...")
    ...
    return (len(reasons) == 0), reasons
```
Per RESEARCH.md "Don't Hand-Roll" table: add a new `min_trades_per_fold` constant (distinct from `min_trades`) following this exact `DEFAULT_THRESHOLDS`-style dict + `validate_strategy`-style `(bool, list[str])` return convention, rather than inventing a new validation shape for folds. The per-fold check in RESEARCH.md's Pattern 2 (`stats["total_trades"] >= min_trades_per_fold and stats["total_return_r"] > 0`) should ideally be expressed by calling `validate_strategy(stats, relaxed_fold_thresholds)` so there is exactly one validation code path, not two divergent ones.

**Time-series split reference (already in skill file, do not hand-roll a second generator)** — `.claude/skills/quant-finance-math/SKILL.md` lines 151-161:
```python
def walk_forward_splits(n_samples: int, train_size: int, test_size: int, step: int):
    """Gera (train_idx, test_idx) sequenciais sem sobreposição temporal.
    NUNCA usar train_test_split/KFold aleatório em dados de série
    temporal financeira - introduz fuga de informação do futuro."""
    start = 0
    while start + train_size + test_size <= n_samples:
        train_idx = range(start, start + train_size)
        test_idx = range(start + train_size, start + train_size + test_size)
        yield train_idx, test_idx
        start += step
```
RESEARCH.md recommends `sklearn.model_selection.TimeSeriesSplit(n_splits, max_train_size, gap)` over this hand-rolled generator to avoid two divergent implementations once Phase 7 (ML) needs the same primitive — but if `TimeSeriesSplit`'s rolling-window behavior (via `max_train_size`) proves awkward, this generator is the documented, already-reviewed fallback. Either way, do not write a third variant.

---

### `src/strategy_registry.py` — schema migration for walk-forward/cost columns

**Analog:** the file's own `SCHEMA` + `init_db()` (lines 18-51) and `save_strategy()` (lines 54-75) — in-place additive extension.

**Existing schema pattern** (lines 18-41):
```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    pair_a TEXT,
    pair_b TEXT,
    params TEXT,
    status TEXT,
    fail_reasons TEXT,
    generation INTEGER,
    parent_id TEXT,
    total_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    sharpe_per_trade REAL,
    total_return_r REAL,
    max_drawdown_r REAL,
    avg_hold_bars REAL,
    avg_win_r REAL,
    avg_loss_r REAL,
    bars_tested INTEGER,
    trades TEXT
);
"""


def init_db(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()
```
`CREATE TABLE IF NOT EXISTS` makes `init_db()` idempotent for new installs, but does not add columns to an existing on-disk DB — RESEARCH.md's `migrate_add_walk_forward_columns()` (Code Examples section) is the correct additive pattern: `PRAGMA table_info(strategies)` to check existing columns, then `ALTER TABLE ... ADD COLUMN` only for missing ones. Call this migration function from `init_db()` itself (after `conn.execute(SCHEMA)`) so existing callers (`strategy_generator.py: init_db(db_path)`) get the new columns automatically without a separate migration script to remember to run — this matches the project's existing "idempotent init" convention exactly.

**`save_strategy` pattern to extend** (lines 54-75 — positional tuple matching the `INSERT` column list 1:1, with `json.dumps` for any list/dict field):
```python
def save_strategy(db_path: str, record: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO strategies (
            id, created_at, pair_a, pair_b, params, status, fail_reasons,
            generation, parent_id, total_trades, win_rate, profit_factor,
            sharpe_per_trade, total_return_r, max_drawdown_r, avg_hold_bars,
            avg_win_r, avg_loss_r, bars_tested, trades
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record["id"], record["created_at"], ...
            record["avg_loss_r"], record["bars_tested"], json.dumps(record["trades"]),
        ),
    )
    conn.commit()
    conn.close()
```
Append the 4 new columns (`cost_model_version`, `wf_passed`, `wf_fold_results`, `revalidated_on_real_data`) to both the column list and the `VALUES` placeholders/tuple, using `json.dumps(record["wf_fold_results"])` for the JSON column — exactly mirroring how `trades` (a list of dicts) is already serialized at the last tuple position. `wf_passed`/`revalidated_on_real_data` should be stored as `int(bool(...))` since sqlite has no native boolean (per RESEARCH.md's migration code comment) — same convention the codebase already uses implicitly (`status` is a string `"passed"/"failed"`, not a bool, so this introduces the first true boolean-as-int column; document it).

**Read pattern, unchanged** — `list_strategies`/`get_strategy` (lines 78-100) use `SELECT *` / `pd.read_sql`, so new columns are picked up automatically with zero changes needed to these two functions.

---

### `src/strategy_generator.py` — thread `cost_params` through to backtest calls

**Analog:** the file's own `run_strategy_lab()` call site (line 85) and `main()` (lines 127-162) — in-place extension, minimal change.

**Call site to modify** (line 85):
```python
result = run_hedge_backtest(price_data[pair_a], price_data[pair_b], params)
```
becomes `result = run_hedge_backtest(price_data[pair_a], price_data[pair_b], params, cost_params=cost_params)`, where `cost_params` is threaded into `run_strategy_lab()`'s signature the same way `db_path`/`seed`/`log` are already threaded as plain function parameters (lines 61-70) — not a new global or config object, consistent with this file's existing flat-parameter style.

**CLI argument pattern to extend** (`main()`, lines 127-135 — `argparse` with typed defaults):
```python
parser = argparse.ArgumentParser(description="Laboratório de geração e teste de estratégias")
parser.add_argument("--output-dir", default="./output")
parser.add_argument("--max-pairs", type=int, default=4)
...
```
If cost params need to be CLI-overridable (vs. hard-coded per-symbol defaults inside `backtest_engine.py`), follow this same `add_argument(..., type=..., default=...)` style — but per RESEARCH.md Pitfall 1, the primary mechanism should be a per-symbol dict of placeholder defaults inside the engine, not CLI flags, since costs vary by symbol, not by lab run.

---

### `dashboard.py` — net-of-cost metrics + walk-forward detail panel

**Analog:** the file's own existing summary-metrics block (lines 54-61) and detail/equity-curve panel (lines 104-177) — in-place extension.

**Imports pattern** (lines 14-23, unchanged):
```python
import json
import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from strategy_registry import list_strategies  # noqa: E402
```
No new imports required for displaying existing-shape data; if a fold-by-fold table is rendered, reuse `pd.DataFrame` + `st.dataframe`/`st.table` exactly as done elsewhere in this file (no new charting library).

**Summary metrics (`st.columns` + `st.metric`) pattern to extend** (lines 54-61):
```python
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Estratégias testadas", len(df))
c2.metric("Aprovadas", int((df["status"] == "passed").sum()))
c3.metric("Reprovadas", int((df["status"] == "failed").sum()))
best_pf = df["profit_factor"].max()
c4.metric("Melhor profit factor", f"{best_pf:.2f}")
c5.metric("Gerações executadas", int(df["generation"].max()) + 1)
```
Add a 6th metric column for `revalidated_on_real_data`/`wf_passed` counts (e.g., "Revalidadas (real, WF)"), following the exact `st.columns(N)` + `.metric(label, value)` idiom — widen to `st.columns(6)` or `st.columns(7)`, not a separate row, to match the existing "all top-line counters in one row" layout.

**Detail-panel status block to extend** (lines 118-128 — pass/fail rendering with `st.success`/`st.error` + reasons list):
```python
if row["status"] == "passed":
    st.success("✅ Estratégia APROVADA — passou em todos os critérios de validação.")
else:
    st.error("❌ Estratégia REPROVADA")
    reasons = json.loads(row["fail_reasons"])
    st.markdown("**Motivos:**")
    for r in reasons:
        st.markdown(f"- {r}")
```
The new "Walk-Forward" panel should mirror this exact `st.success`/`st.error` + `json.loads(...)` + bulleted-reasons pattern for `wf_passed`/`wf_fold_results`, rather than inventing a different visual idiom. Add a `revalidated_on_real_data` badge using the same `st.success`/`st.warning` convention used for `df.empty` (lines 43-49) and `trades` empty-state (lines 150-151).

**Equity-curve / per-fold chart pattern to mirror** (lines 133-149 — `go.Figure()` + `add_trace(go.Scatter(...))` + `add_hline` + `update_layout`):
```python
fig = go.Figure()
fig.add_trace(go.Scatter(
    y=equity, mode="lines+markers", name="Equity (R)",
    line=dict(color="#2ca02c" if equity.iloc[-1] > 0 else "#d62728"),
))
fig.add_hline(y=0, line_dash="dot", line_color="gray")
fig.update_layout(
    title="Curva de equity (múltiplos de R, normalizado pelo desvio-padrão do spread na entrada)",
    height=350, xaxis_title="Trade nº", yaxis_title="Retorno acumulado (R)",
    margin=dict(t=40, b=20),
)
st.plotly_chart(fig, use_container_width=True)
```
For a fold-by-fold breakdown table/chart, reuse this exact `go.Figure()` + `update_layout(margin=dict(t=40, b=20))` styling (consistent height=350, same green/red pass/fail color convention `#2ca02c`/`#d62728`) so the new panel is visually consistent with the existing equity chart, not a new visual style.

**Table-rename pattern to extend** (lines 86-100, 153-162, 164-177 — every dataframe column gets a Portuguese display-name mapping via `.rename(columns={...})`):
```python
view_display[[...]].rename(columns={
    "pair_a": "Par A", "pair_b": "Par B", "status": "Estado",
    ...
})
```
New columns (`wf_passed`, `revalidated_on_real_data`, `cost_model_version`) must follow this same Portuguese-label `.rename(columns={...})` convention before display — never show raw snake_case column names in the UI, consistent with every existing table/stats block in this file.

---

## Shared Patterns

### Numeric rounding/precision convention
**Source:** `src/backtest_engine.py` lines 140 (`round(float(pnl_r), 4)`), 180-188 (`round(float(x), 3)` for aggregate stats)
**Apply to:** `apply_transaction_costs()`, any new cost/fold stat computed in `walk_forward_validate()`
```python
"pnl_r": round(float(pnl_r), 4),       # trade-level: 4 decimals
"total_return_r": round(float(equity[-1]), 3),  # aggregate: 3 decimals
```
Follow this exact split: trade-level R values get 4 decimals, aggregate/summary stats get 3 decimals.

### `params esperados:` docstring convention
**Source:** `src/backtest_engine.py` lines 62-73
**Apply to:** `run_hedge_backtest()`'s new `cost_params` argument, `walk_forward_validate()`'s full signature
```python
"""params esperados:
    entry_threshold   (float) - |z| mínimo para abrir
    ...
"""
```
Every dict-shaped parameter in this codebase is documented key-by-key with type and meaning inline in the docstring — apply identically to `cost_params` (keys: `spread_cost`, `slippage_cost`, `commission_r`/`reference_lot_size`) and any new `window_cfg`/fold-config dict.

### `(bool, list[str])` validation-gate return convention
**Source:** `src/backtest_engine.py::validate_strategy`, lines 204-228
**Apply to:** Any new per-fold validation logic inside `walk_forward_validate()` — reuse `validate_strategy()` itself with a distinct `thresholds` dict rather than writing a parallel boolean check, per RESEARCH.md's "Don't Hand-Roll" guidance.

### Status/empty-state UI convention (`st.warning`/`st.success`/`st.error`/`st.info`)
**Source:** `dashboard.py` lines 43-49, 109-111, 118-128, 150-151
**Apply to:** All new dashboard panels (walk-forward detail, real-data-revalidated badge) — use the same four-state convention (`st.warning` for missing prerequisite data, `st.info` for empty filtered result, `st.success`/`st.error` for pass/fail) rather than introducing new UI primitives.

### Additive, idempotent SQLite migration convention
**Source:** RESEARCH.md's `migrate_add_walk_forward_columns()` (derived from `strategy_registry.py`'s existing `CREATE TABLE IF NOT EXISTS` idempotency), `src/strategy_registry.py::init_db` lines 44-51
**Apply to:** `strategy_registry.py` schema changes — `PRAGMA table_info` + conditional `ALTER TABLE ADD COLUMN`, invoked from `init_db()` so every existing call site (`strategy_generator.py` line ~72) gets the migration for free.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `docs/strategy_lab_spec.md` methodology section (window type/length/gap/min-trades-per-fold) | config/doc | — | No prior walk-forward methodology has been documented in this codebase; this is genuinely new content, not a pattern extension. Write per CLAUDE.md rule 3 ("gatilho matemático exato") — name exact constants, not prose. |
| Per-symbol cost-parameter defaults (spread/slippage/commission placeholder table) | config | — | No existing per-symbol config table exists in the codebase (`TF_MAP_MINUTES` in `data_pipeline.py` line 86 is the closest *shape* — a flat dict keyed by symbol/timeframe string — reuse that dict-literal style, but the cost-defaults content itself has no in-repo precedent and must be sourced from RESEARCH.md's cited market-rate ranges, clearly marked as placeholders pending real `symbol_info()` data). |

## Metadata

**Analog search scope:** `src/` (all 4 existing Python modules), `dashboard.py`, `.claude/skills/quant-finance-math/SKILL.md`, `docs/strategy_lab_spec.md` references in RESEARCH.md
**Files scanned:** `src/backtest_engine.py`, `src/strategy_registry.py`, `src/strategy_generator.py`, `src/data_pipeline.py` (partial), `dashboard.py`, `.claude/skills/quant-finance-math/SKILL.md` (partial)
**Pattern extraction date:** 2026-06-30
