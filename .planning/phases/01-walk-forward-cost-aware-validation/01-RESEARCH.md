# Phase 1: Walk-Forward & Cost-Aware Validation - Research

**Researched:** 2026-06-30
**Domain:** Quant backtest methodology — walk-forward cross-validation, transaction-cost modeling for forex scalping
**Confidence:** MEDIUM

## Summary

This phase closes a gap that is already explicitly documented in the codebase itself: `docs/strategy_lab_spec.md`'s "Limitações conhecidas" section states there is no train/test split inside the backtest (everything is in-sample) and `backtest_engine.py` computes PnL purely in "R" units with zero transaction-cost modeling. Both gaps are named, by the project's own prior research (`SUMMARY.md`) and by `CLAUDE.md` rule #2 and #4, as the most consequential ones to close before any production code consumes strategy-lab output.

The fix has two independent halves that should be implemented as two new entry points rather than retrofitting the existing single-pass functions:

1. **Cost-aware backtesting** — extend `run_hedge_backtest()` (or wrap it) so every simulated trade subtracts spread, slippage, and commission, expressed in the same "R" unit system already in use, before stats are computed. This touches `backtest_engine.py` and the parameter set consumed by `strategy_generator.py`.
2. **Walk-forward revalidation** — add a new `walk_forward_validate()` entry point that runs the existing cost-aware backtest across rolling, non-overlapping train/test folds (no parameter optimization across folds is required for this phase — the parameters are already fixed/approved; this is *re*validation, not re-optimization) and aggregates an out-of-sample verdict per fold plus an overall pass/fail. This is a new function, not a rewrite of `run_hedge_backtest`.

The existing pipeline only ever validates strategies on synthetic data (`data_pipeline.py --mode synth`). VALID-01 requires this phase's walk-forward pass to run against **real market data** (`--mode mt5`), which means a real MT5 demo connection — or, if unavailable in this environment, an explicit fallback/manual step — is a hard dependency that must be resolved before Plan execution, not assumed away.

**Primary recommendation:** Add cost modeling directly inside `backtest_engine.run_hedge_backtest()` (not a post-hoc adjustment in the dashboard), add a new `walk_forward_validate()` function built on `sklearn.model_selection.TimeSeriesSplit` with a **rolling** (not anchored) window given this project's explicit regime-dependent thesis and M5 scalping timeframe, and extend `dashboard.py` + the registry schema so every displayed metric is post-cost and every approved strategy shows its walk-forward fold-by-fold breakdown, not just an aggregate.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Transaction cost modeling (spread/slippage/commission) | Backtest Engine (`src/backtest_engine.py`) | — | Cost must be inside the simulation loop that produces `pnl_r`, not bolted on by a downstream consumer — otherwise any future automated gate (v2 AUTOGATE-01) inherits cost-blind numbers by construction |
| Walk-forward fold generation & orchestration | Backtest Engine (`src/backtest_engine.py`) | Strategy Lab Orchestrator (`src/strategy_generator.py`) | The fold-splitting logic is a validation concern (belongs next to `validate_strategy`); the orchestrator only needs to call the new entry point once per approved candidate |
| Persisting walk-forward results per fold | Strategy Registry (`src/strategy_registry.py`) | Database / Storage (SQLite) | Existing registry already owns all strategy persistence; extend schema rather than create a second store |
| Displaying net-of-cost metrics + walk-forward verdict | Validation UI (`dashboard.py`) | — | Dashboard is the only human gate (HEDGE-01 requires dashboard-approved parameters); it must not be able to show a cost-blind or non-walk-forward number anywhere |
| Real (non-synthetic) market data acquisition | Data Pipeline (`src/data_pipeline.py`, `fetch_mt5`) | External: MT5 terminal | Already implemented (`--mode mt5`); this phase consumes it, does not rebuild it — but the MT5 terminal/demo account is an external dependency this phase must explicitly probe for (see Environment Availability) |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scikit-learn | already a project dependency (`requirements.txt`); pin not yet fixed — verify installed version before planning | `sklearn.model_selection.TimeSeriesSplit` drives walk-forward fold generation | Already used elsewhere in spirit (project's own prior research explicitly names this as "the single mechanism to drive walk-forward... avoiding two divergent validation implementations") [CITED: .planning/research/SUMMARY.md] |
| pandas / numpy | already project dependencies | Cost arithmetic (pip→price conversion, spread subtraction), fold slicing | Already the backbone of `backtest_engine.py` |
| MetaTrader5 (pip package) | already conditionally listed in `requirements.txt`, Windows-only | Source of real (non-synthetic) OHLCV for VALID-01, and source of live `symbol_info()` spread/point/tick-value data for realistic cost defaults | Already integrated in `data_pipeline.fetch_mt5`; this phase is the first consumer of `--mode mt5` data downstream of Layer 0 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `sklearn.model_selection.TimeSeriesSplit` (`gap` parameter) | scikit-learn ≥ 0.24 (gap param requires this; verify installed version ≥ 0.24 — virtually certain given how old that release is, but confirm) | Optional train/test gap to avoid any edge-of-window leakage between fold train end and fold test start | Use only if there's a reason to buffer (e.g., indicator lookback windows spanning the boundary); for pure re-validation with fixed parameters, gap can be 0 since no information leaks if parameters are already frozen — document the decision either way |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled fold loop (already exists informally as the `walk_forward_splits` generator in `.claude/skills/quant-finance-math/SKILL.md`) | `sklearn.model_selection.TimeSeriesSplit` | The skill file's hand-rolled generator works and is simpler to read, but using `TimeSeriesSplit` keeps the project's stated intent (per `SUMMARY.md`) to avoid "two divergent validation implementations" once Phase 7 (ML model) also needs walk-forward — recommend `TimeSeriesSplit` for `backtest_engine.py`'s new entry point, but the hand-rolled generator is an acceptable, equally correct fallback if `TimeSeriesSplit`'s expanding-default behavior fights with the rolling-window requirement (mitigated via `max_train_size`) |
| Per-broker live spread feed (querying `symbol_info()` live during backtest) | Fixed/configurable per-symbol cost parameters with optional live-feed override | A live per-bar spread feed would be more realistic but MT5 history (`copy_rates_from_pos`) doesn't carry historical spread reliably for all brokers/symbols; pragmatic default is configurable fixed (or simple volatility-scaled) cost parameters per symbol, with a note that this is a simplification vs. true historical variable spread |

**Installation:** No new packages required — `scikit-learn`, `pandas`, `numpy`, and (Windows-only, already conditional) `MetaTrader5` are already in `requirements.txt`. **No installation step needed for this phase.**

**Version verification:** `py`/`python` was not available on PATH in this research session (Windows execution alias not configured), so installed versions could not be directly queried via `pip show`/`python -c`. The planner/executor should verify with `pip show scikit-learn` (need ≥ 0.24 for `TimeSeriesSplit(gap=...)`, virtually certain to already be satisfied by any contemporary scikit-learn install) before relying on the `gap` parameter. `requirements.txt` does not pin any version — this is a pre-existing condition of the repo, not something this phase needs to fix, but flagging it as a latent risk (unpinned deps can silently drift between environments).

## Package Legitimacy Audit

**No new packages are installed in this phase.** All libraries used (`scikit-learn`, `pandas`, `numpy`, `MetaTrader5`) are pre-existing dependencies already present in `requirements.txt` and already in active use elsewhere in the codebase (`data_pipeline.py`, `backtest_engine.py`). The Package Legitimacy Gate is not applicable — skipping the audit table per the gate's own scope ("whenever this phase installs external packages").

## Architecture Patterns

### System Architecture Diagram

```
                    ┌────────────────────────────┐
                    │  output/features_*.parquet  │   (real, --mode mt5,
                    │  output/hedge_candidates.csv│    OR synthetic --mode synth)
                    └──────────────┬───────────────┘
                                   │ price series + cointegration metadata
                                   ▼
       ┌───────────────────────────────────────────────────────┐
       │  backtest_engine.py                                   │
       │                                                        │
       │  ┌──────────────────────────────────────────────┐     │
       │  │ run_hedge_backtest(price_a, price_b, params,  │     │
       │  │                    cost_params)   [MODIFIED]  │     │
       │  │  • existing entry/exit simulation loop        │     │
       │  │  • NEW: subtract spread+slippage+commission   │     │
       │  │    from pnl_r at trade close                  │     │
       │  └───────────────────┬──────────────────────────┘     │
       │                      │ trades[] (now net-of-cost)      │
       │                      ▼                                 │
       │  ┌──────────────────────────────────────────────┐     │
       │  │ compute_stats()        [unchanged interface]  │     │
       │  └───────────────────┬──────────────────────────┘     │
       │                      │ stats (now net-of-cost)         │
       │                      ▼                                 │
       │  ┌──────────────────────────────────────────────┐     │
       │  │ walk_forward_validate(price_a, price_b,       │     │
       │  │   params, cost_params, window_cfg)   [NEW]    │     │
       │  │  • TimeSeriesSplit-driven rolling folds        │     │
       │  │  • calls run_hedge_backtest() per fold         │     │
       │  │  • per-fold stats + min-trades-per-fold check  │     │
       │  │  • aggregate pass/fail verdict                 │     │
       │  └───────────────────┬──────────────────────────┘     │
       └────────────────────────┼────────────────────────────────┘
                                 │ {fold_results[], overall_passed, overall_stats}
                                 ▼
       ┌───────────────────────────────────────────────────────┐
       │  strategy_registry.py  [SCHEMA EXTENDED]               │
       │  • new columns: cost_model_version, wf_passed,         │
       │    wf_fold_results (JSON), revalidated_on_real_data    │
       └──────────────────────┬──────────────────────────────────┘
                               │
                               ▼
       ┌───────────────────────────────────────────────────────┐
       │  dashboard.py  [EXTENDED]                              │
       │  • ALL displayed metrics are net-of-cost (no toggle    │
       │    to view cost-blind numbers)                         │
       │  • new "Walk-Forward" detail panel: per-fold table +   │
       │    pass/fail per fold                                  │
       │  • "real-data revalidated" badge before a strategy     │
       │    counts as production-eligible (VALID-01 gate)       │
       └───────────────────────────────────────────────────────┘
```

A reader can trace VALID-01/VALID-02 end-to-end: real-data parquet → cost-aware backtest per fold → walk-forward aggregation → registry persistence → dashboard gate that blocks production eligibility until both checks pass.

### Recommended Project Structure

No new top-level files/folders are required — this phase extends three existing files and the registry schema:

```
src/
├── backtest_engine.py     # MODIFIED: cost params in run_hedge_backtest(); NEW walk_forward_validate()
├── strategy_registry.py   # MODIFIED: schema migration for new columns
├── strategy_generator.py  # MODIFIED (minor): pass cost_params through to run_hedge_backtest calls
dashboard.py                # MODIFIED: net-of-cost display + walk-forward detail panel
docs/
└── strategy_lab_spec.md    # MODIFIED: document the fixed walk-forward methodology + cost model (CLAUDE.md rule: "gatilho matemático exato" applies — window type/length/gap/min-trades-per-fold must be written down, not implicit in code)
```

### Pattern 1: Cost-Aware PnL Calculation Inside the Simulation Loop

**What:** Apply transaction costs at the exact point a trade closes, inside `run_hedge_backtest()`, using the same "R" unit convention already established (entry-spread-std-dev normalization).
**When to use:** Always — VALID-02 requires this for "toda validação... não só o gate manual," meaning any future automated gate inherits this for free if it lives here rather than in the dashboard layer.
**Example:**
```python
# Source: pattern derived from existing run_hedge_backtest() trade-close block
# (src/backtest_engine.py lines ~131-143) + forex cost-modeling research
# [CITED: web research on spread/slippage/commission components]

def apply_transaction_costs(pnl_r: float, entry_std: float, cost_params: dict,
                             direction: int) -> float:
    """Subtracts modeled round-trip cost (spread + slippage + commission)
    from a trade's raw pnl_r. Costs are converted into the same 'R' unit
    (entry-spread-std-dev multiples) so they compose directly with pnl_r.

    cost_params expected (per pair, in price units of the spread):
        spread_cost   - round-trip spread cost in spread-price-units
        slippage_cost - modeled slippage in spread-price-units
        commission_r  - commission already pre-converted to R units
                        (commission is typically a flat $/lot, independent
                        of entry_std, so convert it to R at the call site
                        using position size — see Pitfall 2 below)
    """
    if entry_std is None or entry_std <= 0:
        return pnl_r  # cannot normalize; defer to caller validation
    total_cost_price_units = cost_params.get("spread_cost", 0.0) + cost_params.get("slippage_cost", 0.0)
    cost_r = total_cost_price_units / entry_std
    return pnl_r - cost_r - cost_params.get("commission_r", 0.0)
```

### Pattern 2: Walk-Forward Re-Validation via Rolling `TimeSeriesSplit`

**What:** Re-run the (already-fixed, already-approved) backtest parameters across sequential, non-overlapping rolling folds and require every fold to independently pass a relaxed version of the approval gate (or at minimum a positive-expectancy + min-trades-per-fold check).
**When to use:** Once, as the out-of-sample gate before a dashboard-approved strategy becomes production-eligible (VALID-01). This is re-validation of already-chosen parameters, not a search — `n_generations`/mutation logic in `strategy_generator.py` is out of scope here.
**Example:**
```python
# Source: pattern derived from sklearn.model_selection.TimeSeriesSplit
# official docs [CITED: scikit-learn.org/stable/modules/generated/
# sklearn.model_selection.TimeSeriesSplit.html] + project's own
# walk_forward_splits() reference in .claude/skills/quant-finance-math/SKILL.md

from sklearn.model_selection import TimeSeriesSplit

def walk_forward_validate(price_a, price_b, params: dict, cost_params: dict,
                           n_splits: int = 5, train_size: int | None = None,
                           gap: int = 0, min_trades_per_fold: int = 10) -> dict:
    """Re-validates fixed, already-approved params across rolling
    out-of-sample folds. train_size caps each fold's training window so
    splits are ROLLING, not expanding/anchored (per this project's
    regime-dependent thesis — see Pitfall 3)."""
    n = min(len(price_a), len(price_b))
    tscv = TimeSeriesSplit(n_splits=n_splits, max_train_size=train_size, gap=gap)

    fold_results = []
    for fold_i, (train_idx, test_idx) in enumerate(tscv.split(range(n))):
        # Train slice is NOT used to re-fit params (params are already
        # fixed/approved) — it exists only to mirror the live workflow
        # where beta/zscore windows are computed causally up to test start.
        test_a = price_a.iloc[test_idx[0]:test_idx[-1] + 1]
        test_b = price_b.iloc[test_idx[0]:test_idx[-1] + 1]
        result = run_hedge_backtest(test_a, test_b, params, cost_params)
        stats = result["stats"]
        fold_passed = stats["total_trades"] >= min_trades_per_fold and stats["total_return_r"] > 0
        fold_results.append({"fold": fold_i, "stats": stats, "passed": fold_passed})

    overall_passed = all(f["passed"] for f in fold_results)
    return {"fold_results": fold_results, "overall_passed": overall_passed}
```

### Anti-Patterns to Avoid

- **Computing cost as a post-hoc multiplier on aggregate stats (e.g., `profit_factor * 0.9`):** Loses per-trade fidelity (a strategy with few large wins reacts very differently to fixed-per-trade costs than one with many small wins) and cannot be reused by any future automated gate that needs trade-level data. Apply cost at trade-close time, inside the loop, as in Pattern 1.
- **Re-optimizing parameters per walk-forward fold (classic WFO) when the actual requirement is re-validation:** VALID-01's wording ("revalidação... antes de chegarem a produção") plus the project's existing gate (dashboard-approved parameters are already fixed before this phase runs) means this phase does **not** need to re-run `strategy_generator`'s mutation loop per fold — that would silently turn re-validation into a second optimization pass with its own overfitting risk (the project's own SUMMARY.md flags "Walk-forward implemented in name only" as Pitfall 3 partly because of this exact confusion). Keep params frozen across folds.
- **Anchored/expanding windows by default:** `TimeSeriesSplit`'s default behavior is an expanding training set. For this project's M5 scalping, regime-dependent thesis, default (anchored) behavior is the wrong choice — must explicitly set `max_train_size` to force rolling windows (see Pitfall 3).
- **Treating "tested with `--mode synth`" as satisfying VALID-01:** Synthetic data is explicitly constructed (`fetch_synthetic`) to be cointegrated by design — it cannot fail the cointegration premise the way real markets can. VALID-01 requires real (`--mode mt5`) data specifically because synthetic data cannot expose the failure modes real markets produce (regime breaks, real spread behavior, real correlation decay).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Sequential train/test fold generation for time series | A second hand-rolled fold-index generator (beyond the one already sketched in `.claude/skills/quant-finance-math/SKILL.md`) | `sklearn.model_selection.TimeSeriesSplit` (already a project dependency) | Project's own prior research explicitly recommends this "to avoid two divergent validation implementations" once Phase 7 (ML model) also needs walk-forward splitting [CITED: .planning/research/SUMMARY.md] |
| Statistical significance / minimum sample size reasoning | An ad hoc "looks like enough trades" judgment per fold | Reuse the existing `DEFAULT_THRESHOLDS["min_trades"]` pattern, applied per-fold (not just in aggregate) — a new `min_trades_per_fold` threshold | The existing single-pass gate already encodes this discipline (`backtest_engine.validate_strategy`); a per-fold version is the same pattern at a finer grain, not a new concept to invent |

**Key insight:** Every primitive this phase needs (time-series-safe splitting, statistical-significance gating, R-unit PnL accounting) already exists somewhere in this codebase or its skill files in close-to-final form — the job here is composition and extension, not new invention. Resist any temptation to install or build a more "sophisticated" cost model or walk-forward framework before this single-pair scalping system has live broker cost data to calibrate against.

## Common Pitfalls

### Pitfall 1: Cost Parameters With No Real Calibration Source

**What goes wrong:** Hard-coding a single global spread/slippage/commission number (e.g., "2 pips for everything") produces a backtest that LOOKS cost-aware but is still effectively fictional, because EURUSD and a more exotic pair like AUDUSD or USDCHF have very different real spreads.
**Why it happens:** No broker demo account / live `symbol_info()` data is available yet at the time this phase is planned (per `PROJECT.md`: "MT5 real... ligação real ainda não foi validada").
**How to avoid:** Build the cost model as a **per-symbol configurable parameter set** (a dict/dataclass keyed by symbol or pair) with conservative published-default values as placeholders [CITED: web research — majors ~0.1-3 pips spread, $2-7/round-turn-lot commission, 1-10 pips slippage], explicitly designed to be overwritten once a real MT5 demo connection exists and `symbol_info()` can supply live `spread`/`point`/`trade_tick_value`. Document in code and in `docs/strategy_lab_spec.md` that the default values are placeholders pending real broker data — this is consistent with `SUMMARY.md`'s own "Gaps to Address" entry: "Real broker spread/commission data — only available once demo account exists; design Phase 1 cost modeling to accept broker-specific parameters."
**Warning signs:** A single `SPREAD_PIPS = 2` constant used identically across every symbol/pair in the codebase.

### Pitfall 2: Commission Unit Mismatch With "R" PnL Convention

**What goes wrong:** Commission is naturally a flat dollar amount per lot (e.g., "$5 per round turn"), but this codebase's entire PnL convention is in "R" (spread-std-dev multiples), not money. Converting commission into R units requires knowing position size in lots and the dollar value of the entry-spread-std-dev — but Layer 3 (risk engine, not yet built) is the one that owns position sizing, per `docs/strategy_lab_spec.md`'s own stated design ("Quando a camada de risco existir, consome o parâmetro `position_size_pct` próprio, não algo calculado aqui").
**Why it happens:** The R-unit decoupling (signal quality vs. position sizing) that makes this architecture clean for Phase 0.5 becomes awkward exactly at the point commission needs to be subtracted, because commission genuinely depends on position size.
**How to avoid:** For this phase, use a **fixed reference position size assumption** (e.g., "assume 1 standard lot" or a configurable `reference_lot_size` cost-model parameter) purely for the purpose of expressing commission in R-equivalent terms during validation — explicitly documented as a simplification, not the real position size the risk engine will eventually use. This keeps Phase 1 self-contained without prematurely building Phase 2's sizing logic.
**Warning signs:** Commission silently being treated as "negligible" / omitted because no obvious place to plug it in was found, or commission being expressed in raw dollars and then mixed with R-unit numbers without conversion (a hidden unit bug).

### Pitfall 3: Walk-Forward in Name Only (Wrong Window Type, No Documented Methodology)

**What goes wrong:** Calling a function `walk_forward_validate()` that, due to `TimeSeriesSplit`'s expanding-window default, silently produces an *anchored* validation instead of a *rolling* one — or applying a single train/test split (what the spec calls "in-sample relativo ao período fornecido" today) and mislabeling it walk-forward.
**Why it happens:** `TimeSeriesSplit`'s default behavior (full expanding training set per fold) does not match "rolling window" intuition unless `max_train_size` is explicitly set; it's easy to call the API and assume it does the right thing.
**How to avoid:** Explicitly decide and document — in `docs/strategy_lab_spec.md`, per CLAUDE.md rule #3's "gatilho matemático exato" requirement applied to validation methodology too — the exact window type (rolling, given M5 scalping + regime-dependent thesis), window length, train/test gap, and minimum-trades-per-fold **before** writing the implementation, then encode those exact values as named constants (not magic numbers scattered across the function call).
**Warning signs:** `walk_forward_validate()` exists but `docs/strategy_lab_spec.md` is not updated to describe its exact parameters; window length/fold count chosen ad hoc per call site instead of being a single source-of-truth config.

### Pitfall 4: Synthetic-Only Revalidation Satisfying VALID-01 in Practice (Even If Not in Intent)

**What goes wrong:** Because `--mode mt5` requires a working MT5 terminal connection that may not be available in every development/CI environment, it's tempting to "test the walk-forward code path" against `--mode synth` data and call VALID-01 satisfied once the code runs without error.
**Why it happens:** Synthetic data is always available and frictionless; MT5 requires Windows + an open, logged-in terminal + (eventually) real demo credentials.
**How to avoid:** Treat "walk-forward code works correctly" (testable on synthetic data, fine for development) and "a specific approved strategy has been revalidated on real data" (VALID-01's actual requirement) as two separate, sequential milestones within this phase. The plan should make this an explicit success-criterion checkpoint, not assume code-correctness implies requirement-completion. If a real MT5 demo connection genuinely cannot be established during this phase's execution window, that is a blocker to flag explicitly to the user — not something to silently substitute with synthetic data and mark complete (see Environment Availability below).
**Warning signs:** Plan tasks that only reference `output/features_*.parquet` without specifying which `--mode` generated them; "done" criteria that check function existence/test-passing rather than checking the registry for a `revalidated_on_real_data=True` flag tied to an actual real-data run.

### Pitfall 5: Minimum-Trades-Per-Fold Set Too Low to Be Meaningful

**What goes wrong:** Splitting an already-modest dataset (the existing default backtest only needs `min_trades=20` total) into 5+ rolling folds can leave individual folds with 2-4 trades each — reintroducing the exact "few trades, noise not edge" problem `DEFAULT_THRESHOLDS["min_trades"]` was designed to prevent, just at fold granularity.
**Why it happens:** Naively reusing `n_splits=5` (sklearn's default) without checking whether the available history and trade frequency for a given pair can actually support 5 folds with statistically meaningful trade counts each.
**How to avoid:** Make `min_trades_per_fold` a named, documented threshold (distinct from the aggregate `min_trades`) and choose `n_splits`/fold length based on the trade frequency observed in the existing single-pass backtest for that pair — not a fixed global default. If a pair's typical backtest trade count can't support the chosen fold count at the minimum, surface that as a fold-count-too-high warning, not a silent pass.
**Warning signs:** A strategy's walk-forward folds individually show 2-3 trades but the aggregate "overall_passed" still reports `True` because cumulative trades across folds happen to exceed the aggregate threshold.

## Code Examples

### Pip/Price Unit Conversion for Spread Cost (forex-specific gotcha)

```python
# Source: pattern derived from MT5 symbol_info() field semantics
# [CITED: mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py]
# point = smallest price increment for the symbol (e.g., 0.00001 for
# 5-digit EURUSD broker quoting); spread in symbol_info is in POINTS,
# not pips or price units - must multiply by `point` to get a price-unit
# cost usable directly with this codebase's price-denominated spread.

def spread_points_to_price_units(spread_points: int, point: float) -> float:
    """Converts MT5's symbol_info().spread (in points) into the same
    price units as this codebase's `spread = price_a - beta * price_b`,
    so it composes directly with apply_transaction_costs()."""
    return spread_points * point
```

### Registry Schema Migration (additive, non-breaking)

```python
# Source: pattern derived from existing src/strategy_registry.py SCHEMA
# Use ALTER TABLE with IF NOT EXISTS-style guards (sqlite3 doesn't support
# IF NOT EXISTS on ALTER TABLE ADD COLUMN before 3.x checks) - check
# PRAGMA table_info() before adding, to keep init_db() idempotent like
# the existing function already is for CREATE TABLE IF NOT EXISTS.

def migrate_add_walk_forward_columns(db_path: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db_path)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
    new_cols = {
        "cost_model_version": "TEXT",
        "wf_passed": "INTEGER",          # sqlite has no native bool; 0/1
        "wf_fold_results": "TEXT",       # JSON, same pattern as existing `trades` column
        "revalidated_on_real_data": "INTEGER",
    }
    for col, coltype in new_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE strategies ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Single in-sample backtest pass, no cost model (current `run_hedge_backtest`/`validate_strategy`) | Cost-aware, multi-fold rolling walk-forward revalidation before production eligibility | This phase (Phase 1) | Strategies that look "approved" today under `DEFAULT_THRESHOLDS` may fail once costs and out-of-sample folds are applied — expect the approved-strategy count to drop after this phase ships; this is the correct outcome, not a regression |

**Deprecated/outdated:** None — this is the first time walk-forward/cost modeling is implemented in this codebase; nothing is being replaced, only extended. `docs/strategy_lab_spec.md`'s "Limitações conhecidas" section, once this phase ships, should have its first bullet (no train/test separation) and the cost-blindness gap closed and the section updated to reflect the new methodology.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | Forex cost magnitude defaults (0.1-3 pips spread majors, $2-7/lot commission, 1-10 pips slippage) are reasonable placeholder defaults for this project's specific broker | Common Pitfalls (Pitfall 1), Standard Stack | If the user's actual broker has materially different costs (e.g., higher commission on minor pairs, or this is a market-maker broker with wider spreads than ECN), placeholder defaults could understate cost impact and let strategies through that wouldn't survive real costs — must be confirmed/overwritten once a demo account is connected |
| A2 | Rolling (not anchored) window is the correct choice for this project given M5 scalping + regime-dependent thesis | Anti-Patterns, Pitfall 3 | If anchored windows would actually serve this strategy class better (e.g., if regimes are rare and more historical data genuinely improves stability), a rolling-only implementation could underfit; this is a methodology decision worth confirming with the user during discuss-phase rather than locking silently |
| A3 | "Re-validation" (frozen params across folds) rather than "re-optimization" (re-fit params per fold) is the correct interpretation of VALID-01's "revalidação walk-forward out-of-sample" | Anti-Patterns | If the user actually intends classic walk-forward optimization (re-fit per fold, not just re-test), the scope and complexity of this phase is significantly larger and the planner needs to know before committing tasks |
| A4 | `min_trades_per_fold` should be a new, separate threshold from the existing aggregate `min_trades` (default 20), not the same number divided by fold count | Pitfall 5 | If the planner picks an arbitrary fold-level threshold without basing it on actual trade-frequency data from existing backtests, the gate could be too strict (rejecting everything) or too loose (defeating its purpose) |
| A5 | scikit-learn installed version supports `TimeSeriesSplit(gap=...)` (requires ≥0.24) | Standard Stack | Could not verify installed version in this research session (Python not on PATH); if the installed version predates 0.24, the `gap` parameter would raise a TypeError — low risk given how old 0.24 is, but must be checked before planning assumes it's available |

**If this table is empty:** N/A — assumptions present, listed above.

## Open Questions

1. **Is a real MT5 demo account/terminal connection actually available for this phase's execution, or does VALID-01's real-data requirement need a different interim resolution?**
   - What we know: `PROJECT.md` states "O utilizador tem conta demo MT5 disponível para testes, mas a ligação real ainda não foi validada" — a demo account exists but the connection itself is unvalidated.
   - What's unclear: Whether validating the MT5 connection is in scope for this phase or a precondition that needs to happen first (it touches Layer 0's `fetch_mt5`, which is implemented but per `PROJECT.md`'s Active list, "Ligação MT5 real validada... em conta demo" is its own unchecked item, separate from "Revalidação das estratégias... em histórico de mercado real").
   - Recommendation: Surface this explicitly in discuss-phase/planning — likely needs a Wave 0 task to validate the MT5 connection (run `data_pipeline.py --mode mt5` successfully against the demo account) before any walk-forward-on-real-data task can execute. If the terminal/account isn't reachable in the execution environment (e.g., this is a remote/sandboxed environment without MT5 installed), this phase may need a documented interim fallback (e.g., revalidate against a downloaded historical CSV import as a stand-in for live MT5 fetch) with an explicit note that this doesn't fully satisfy VALID-01's spirit until live MT5 fetch is later confirmed.

2. **Should the walk-forward gate thresholds (per-fold) mirror `DEFAULT_THRESHOLDS` exactly, or be a relaxed/distinct set?**
   - What we know: The existing aggregate gate requires `min_trades=20`, `min_profit_factor=1.2`, `min_sharpe=0.15`, `max_drawdown_r=8.0`, `total_return_r>0`.
   - What's unclear: Whether every fold must independently clear all five criteria (very strict — may reject almost everything given likely smaller per-fold trade counts) or only a subset (e.g., positive return + min trade count, with profit factor/Sharpe only checked in aggregate).
   - Recommendation: Default to a relaxed per-fold check (positive `total_return_r` + `min_trades_per_fold`) plus full `DEFAULT_THRESHOLDS` on the aggregated/concatenated out-of-sample trades — but this should be confirmed with the user since it's a methodology decision with real consequences for how many strategies pass.

## Project Constraints (from CLAUDE.md)

- **Regra 1 (Risco):** Not directly implemented in this phase (risk engine is Phase 2), but this phase must not introduce any logic that conflates cost/validation with risk sizing — keep `position_size_pct`/Kelly entirely out of scope here, consistent with `docs/strategy_lab_spec.md`'s existing R-unit decoupling design.
- **Regra 2 (Validação walk-forward sempre):** This phase's entire purpose. No random train/test split is acceptable anywhere in the new code — `TimeSeriesSplit` (or the equivalent hand-rolled rolling-window generator) only.
- **Regra 3 (gatilho matemático exato):** Applies by extension to validation methodology, not just hedge triggers — the walk-forward window type/length/gap/min-trades-per-fold must be written down exactly in `docs/strategy_lab_spec.md`, not left implicit in code defaults.
- **Regra 4 (Custos de transação sempre no backtest):** This phase's other core purpose (VALID-02). No "clean" backtest path may remain reachable after this phase — every call to `run_hedge_backtest()` used for validation/approval purposes must include cost params; if a cost-free signature is kept for any other internal purpose (e.g., a debugging utility), it must not be reachable from `strategy_generator.py`, `dashboard.py`, or any approval gate.
- **Regra 7 (gate do dashboard antes de produção):** Not directly modified by this phase's scope, but this phase strengthens that gate — `hedge_engine.py` (Phase 3) will eventually only consume parameters that are both ✅ Aprovada AND walk-forward-passed-on-real-data, per VALID-01. This phase should ensure the registry schema captures both flags distinctly so a future phase can enforce the AND condition.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-------------------|
| VALID-01 | Estratégias aprovadas no laboratório passam por revalidação walk-forward out-of-sample em dados de mercado reais (não sintéticos) antes de chegarem a produção | Pattern 2 (`walk_forward_validate()` using rolling `TimeSeriesSplit`), Pitfall 4 (synthetic-only revalidation does not satisfy this requirement), Open Question 1 (MT5 demo connection dependency), registry schema extension (`revalidated_on_real_data` flag) |
| VALID-02 | `backtest_engine.py` modela custos de transação (spread, slippage, comissão) em toda validação — incluindo qualquer gate automático futuro, não só o gate manual | Pattern 1 (cost applied inside `run_hedge_backtest()`'s simulation loop, not bolted on downstream), Pitfall 1 (per-symbol cost calibration), Pitfall 2 (commission/R-unit conversion), Code Examples (pip-to-price-unit conversion) |
</phase_requirements>

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Python runtime on PATH | All implementation/verification work in this phase | Could not confirm in this research session — `python`/`py` not found on PATH in the Bash tool's shell | — | Verify in a proper Windows shell (cmd/PowerShell) before planning assumes a working `python` invocation; this is likely a PATH/shell-environment quirk of the research sandbox, not a real absence of Python (project clearly has a working Python stack per `STRUCTURE.md`/`ROADMAP.md`) |
| scikit-learn (installed) | `TimeSeriesSplit` with `gap` param | Not directly verified (Python unavailable to query) | Unknown — `requirements.txt` has no version pin | Confirm via `pip show scikit-learn` ≥ 0.24 before relying on `gap`; if absent, omit `gap` (default 0) which needs no minimum version beyond `TimeSeriesSplit`'s original release |
| MT5 terminal (Windows, logged into demo account) | VALID-01's real-data requirement (`fetch_mt5`) | Unknown — `PROJECT.md` states a demo account exists but "a ligação real ainda não foi validada" | — | If unavailable during this phase's execution window, this blocks true VALID-01 completion; see Open Question 1 for the recommended interim handling |
| `output/hedge_candidates.csv`, `output/features_*.parquet` | Both new functions need existing pipeline output as input | Not present yet (`output/` is git-ignored and runtime-generated; no evidence it has been run in this fresh planning session) | — | Plan must include running `data_pipeline.py` (synth at minimum, mt5 for VALID-01) as a Wave 0 prerequisite if not already present in the execution environment |

**Missing dependencies with no fallback:**
- MT5 real demo connection for VALID-01's "real (non-synthetic) market data" requirement — there is no equivalent substitute that satisfies the requirement's literal intent; a CSV-import fallback can unblock the walk-forward code path during development but should not be presented as satisfying VALID-01 itself.

**Missing dependencies with fallback:**
- Python on PATH in the research/planning shell — almost certainly resolvable via a different shell invocation; flagged for the planner/executor to confirm via a quick smoke command rather than assumed broken.

## Sources

### Primary (HIGH confidence)
- `C:\Users\Erick SG\Desktop\Projectos\Apps\forex_ai_project\docs\strategy_lab_spec.md` — "Limitações conhecidas" section, directly states both gaps this phase closes
- `C:\Users\Erick SG\Desktop\Projectos\Apps\forex_ai_project\src\backtest_engine.py` — current implementation, exact line-level integration points for both new features
- `C:\Users\Erick SG\Desktop\Projectos\Apps\forex_ai_project\src\strategy_registry.py`, `strategy_generator.py`, `dashboard.py` — full current implementation read for this research
- `C:\Users\Erick SG\Desktop\Projectos\Apps\forex_ai_project\.planning\research\SUMMARY.md` — prior project-level research, Phase 1 rationale section
- `C:\Users\Erick SG\Desktop\Projectos\Apps\forex_ai_project\CLAUDE.md`, `.planning\PROJECT.md`, `.planning\REQUIREMENTS.md` — project rules and requirement text (VALID-01, VALID-02 verbatim)
- `.claude/skills/quant-finance-math/SKILL.md` — existing `walk_forward_splits()` reference pattern already in the project's own domain-knowledge skill

### Secondary (MEDIUM confidence)
- [TimeSeriesSplit — scikit-learn stable documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) — `gap` parameter semantics, expanding-window default behavior, cross-checked via WebSearch
- [Walk-Forward Optimization: Anchored vs. Rolling Windows](https://www.susanpotter.net/quant/walk-forward-optimization/) and [Walk-Forward Validation: Anchored vs Rolling - QuanterLab](https://quanterlab.com/articles/foundations-walk-forward) — rolling vs anchored window tradeoffs, cross-checked across 2 sources
- [Backtesting Series Episode 5: Transaction Cost Modelling – BSIC](https://bsic.it/backtesting-series-episode-5-transaction-cost-modelling/) and [Backtesting Limitations: Slippage and Liquidity Explained](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/) — spread/slippage/commission magnitude ranges for forex majors, cross-checked across 2+ sources
- [Documentation on MQL5: symbol_info / Python Integration](https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py) — confirmed field names (`spread`, `point`, `trade_tick_value`, etc.) and confirmed absence of a commission field in `symbol_info()`

### Tertiary (LOW confidence)
- None — all web findings were cross-checked against the project's own existing specs/skills or against MT5's official docs page, elevating them to MEDIUM.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages, all already in `requirements.txt` and already in active use in this exact codebase
- Architecture: HIGH — extends existing, well-understood files with clear integration points (line-level read of all touched files)
- Cost modeling specifics (exact pip/commission magnitudes for the user's actual broker): MEDIUM — based on general forex market research, not the user's specific broker, which doesn't exist yet as a validated connection (see Pitfall 1, Open Question 1)
- Walk-forward methodology (rolling vs anchored, exact window sizing): MEDIUM — general best practice is clear and cross-checked, but the exact parameters for THIS project's M5 scalping pairs should be confirmed with the user (Open Questions 2, Assumption A2)

**Research date:** 2026-06-30
**Valid until:** 30 days for the sklearn/MT5 API facts (stable, slow-moving); cost-magnitude figures should be treated as placeholder-only regardless of date, pending real broker data per Pitfall 1
