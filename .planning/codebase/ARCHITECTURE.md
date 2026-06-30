<!-- refreshed: 2026-06-30 -->
# Architecture

**Analysis Date:** 2026-06-30

## System Overview

Forex algorithmic trading system for scalping with statistical hedging between cointegrated pairs, regime detection via HMM, and risk management.

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 0: DATA PIPELINE                                      [DONE]   │
│ src/data_pipeline.py                                                 │
│ • MT5 real feed or synthetic market data                             │
│ • Cointegration detection (Engle-Granger) → hedge candidates        │
│ • Regime detection (HMM on returns/volatility)                      │
│ • Adaptive features (rolling vol, z-score, correlation matrix)      │
└───────────────────┬────────────────────────────────────────────────┘
                    │ Features parquet + hedge_candidates CSV
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 0.5: STRATEGY LAB                                     [DONE]   │
│ src/backtest_engine.py + strategy_registry.py + strategy_generator │
│ + dashboard.py                                                       │
│ • Generate → test → mutate parameter combinations                   │
│ • Backtest without lookahead (rolling causal windows)               │
│ • PnL in "R" (multiples of entry spread std dev)                   │
│ • SQLite registry of all tested strategies                          │
│ • Streamlit UI: status, stats, equity curve, trade history         │
│ Approval gate: min trades, profit factor, Sharpe, drawdown         │
└───────────────────┬────────────────────────────────────────────────┘
                    │ Approved parameters (pass validation)
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 1: ML MODEL                                        [NOT DONE]  │
│ src/ml_model.py                                                      │
│ • Directional signal + confidence per symbol                        │
│ • Conditioned on regime detected in Layer 0                         │
│ • Baseline: gradient boosting (LightGBM/XGBoost)                   │
└───────────────────┬────────────────────────────────────────────────┘
                    │ Direction + confidence + regime
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 2: HEDGE ENGINE (PRODUCTION)                       [NOT DONE]  │
│ src/hedge_engine.py                                                  │
│ • Opens/closes pairs using validated parameters from Layer 0.5      │
│ • Combines z-score (Layer 0) + direction (Layer 1)                  │
│ • Entry: high z-score + correlation + (optional) ML signal          │
│ • Exit: reversion (z-score threshold) OR correlation breakdown      │
│      OR time stop OR risk stop (from Layer 3)                       │
│ • Outputs: proposed orders (symbol, side, size, reason)            │
└───────────────────┬────────────────────────────────────────────────┘
                    │ Proposed orders
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 3: RISK ENGINE (DETERMINISTIC, NON-ML)            [NOT DONE]  │
│ src/risk_engine.py + mql5/RiskGuard.mqh                             │
│ • Position sizing via fractional Kelly (never ML-only)              │
│ • Max exposure per pair / total                                     │
│ • Daily max drawdown                                                │
│ • Max simultaneous positions                                        │
│ • Kill-switch logic                                                 │
│ • Validates/rejects orders from Layer 2                             │
└───────────────────┬────────────────────────────────────────────────┘
                    │ Approved orders
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 4: EXECUTION (MQL5 EA)                              [NOT DONE] │
│ mql5/ScalpingEA.mq5 + related includes                              │
│ • Receives approved orders from Layer 3 or standalone Python        │
│ • Executes in MT5 terminal                                          │
│ • Manages open positions, applies stops                             │
│ • Applies risk rules locally (safety layer if Python drops)         │
└─────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Data Loader | Fetch OHLCV from MT5 (real) or synthetic market; align by timestamp | `src/data_pipeline.py` (fetch_mt5, fetch_synthetic, load_all_symbols) |
| Feature Engineer | Rolling volatility, z-score, price extremes, regime proxy | `src/data_pipeline.py` (engineer_features) |
| Cointegration Detector | Test Engle-Granger on all symbol pairs; rank by p-value | `src/data_pipeline.py` (scan_hedge_candidates) |
| Regime Detector | HMM on returns/volatility; fallback percentile classification | `src/data_pipeline.py` (detect_regime) |
| Backtest Engine | Simulate hedge logic; compute rolling beta, z-score, correlation; generate trades without lookahead | `src/backtest_engine.py` (run_hedge_backtest, compute_rolling_beta, compute_zscore) |
| Stats Calculator | Profit factor, Sharpe, drawdown, win rate, avg hold bars | `src/backtest_engine.py` (compute_stats) |
| Approval Gate | Validate strategy against trader-defined thresholds (min trades, profit factor, Sharpe, drawdown) | `src/backtest_engine.py` (validate_strategy) |
| Strategy Registry | SQLite persistence (params, stats, trade history, generation lineage) | `src/strategy_registry.py` (init_db, save_strategy, list_strategies, get_strategy) |
| Lab Orchestrator | Generate → test → mutate loop; seed each generation from best of previous | `src/strategy_generator.py` (run_strategy_lab, random_params, mutate_params) |
| Validation UI | Streamlit dashboard: view all tested strategies, filter, inspect equity curve & trade list | `dashboard.py` |

## Pattern Overview

**Overall:** Layered, deterministic control flow with strict separation between signal generation (Layers 0–2) and risk/execution (Layers 3–4).

**Key Characteristics:**
- **No ML-only decisions** — risk rules (Layer 3) are always deterministic; ML (Layer 1) informs but does not override.
- **Causal processing** — no lookahead bias; rolling windows look backward only; beta/z-score/correlation recalculated using past data only.
- **Walk-forward validation** — strategy lab trains on historical window, then tests on forward window; parameters never chosen by eye.
- **Unit economics in "R"** — PnL measured in spread std-dev multiples, not money; decouples signal quality from position sizing (Layer 3 owns sizing).
- **Mutation-driven search** — if a generation of strategies mostly fails, best candidates are mutated and tested again; automates hyperparameter exploration.

## Layers

**Layer 0: Data Pipeline**
- Purpose: Ingest price data, engineer adaptive features, detect pairs suitable for hedging, classify market regime
- Location: `src/data_pipeline.py`
- Contains: MT5/synthetic data fetch, feature engineering (rolling vol, z-score), Engle-Granger cointegration test, HMM regime detection, CSV/parquet output
- Depends on: MT5 terminal (if --mode mt5) or numpy/pandas (if --mode synth)
- Used by: Layer 0.5 (reads hedge candidates & features), Layer 1 (reads features & regime labels)

**Layer 0.5: Strategy Lab**
- Purpose: Automated parameter search and validation; ensures only strategies passing objective criteria advance to production
- Location: `src/backtest_engine.py`, `src/strategy_registry.py`, `src/strategy_generator.py`, `dashboard.py`
- Contains: Backtest engine (simulates hedge logic without lookahead), stats calculator, approval gate (min trades/profit factor/Sharpe/drawdown thresholds), SQLite registry, mutation loop, Streamlit UI
- Depends on: Layer 0 outputs (hedge candidates, features), pandas, numpy, statsmodels (for stats)
- Used by: Humans (via dashboard); Layer 2 will consume approved parameters from DB

**Layer 1: ML Model (Future)**
- Purpose: Predict directional probability + confidence per symbol, conditioned on regime
- Location: `src/ml_model.py` (placeholder)
- Contains: Feature pipeline, classifier, inference interface
- Depends on: Layer 0 features; possibly scikit-learn/LightGBM or PyTorch (if deep learning)
- Used by: Layer 2 (optional signal for entry direction)

**Layer 2: Hedge Engine (Future)**
- Purpose: Decide when to open/close multi-leg hedge positions
- Location: `src/hedge_engine.py` (placeholder)
- Contains: Entry logic (high z-score + correlation + optional ML signal), exit logic (reversion, correlation breakdown, time stop, risk stop from Layer 3)
- Depends on: Layer 0 outputs (z-score, correlation, cointegration test, regime), Layer 1 (optional direction signal)
- Used by: Layer 3 (receives proposed orders)

**Layer 3: Risk Engine (Future)**
- Purpose: Validate and size orders; enforce deterministic risk limits
- Location: `src/risk_engine.py` (placeholder) + `mql5/RiskGuard.mqh` (placeholder)
- Contains: Position sizing (Kelly fraction), max exposure logic, drawdown enforcement, kill-switch
- Depends on: Layer 2 (proposed orders), account state
- Used by: Layer 4 (sends approved orders)

**Layer 4: MQL5 EA (Future)**
- Purpose: Execute trades in MT5 terminal; manage open positions; apply stops
- Location: `mql5/ScalpingEA.mq5` + related includes (placeholder)
- Contains: Order placement, position management, stop application, standalone risk enforcement (if Python link drops)
- Depends on: Layer 3 (approved orders or direct Python feed), MT5 API
- Used by: MT5 terminal / real trading account

## Data Flow

### Primary Request Path (Full Pipeline)

1. **Fetch & align data** (`src/data_pipeline.py`, fetch_mt5 or fetch_synthetic) — read OHLCV from MT5 or generate synthetic series
2. **Ensure index alignment** (all symbols share same DatetimeIndex) — prevents silent NaN from timestamp mismatch
3. **Engineer features** (engineer_features) — rolling volatility, z-score, log returns, range %
4. **Scan hedge candidates** (scan_hedge_candidates) — test cointegration (Engle-Granger) on all pairs; output to `output/hedge_candidates.csv`
5. **Detect regime** (detect_regime) — HMM on returns & volatility; output regime labels to `output/features_*.parquet`
6. **Save feature parquets** — one file per symbol with close, vol, z-score, regime
7. **Save regime CSV** — summary of cointegrated pairs + betas

### Strategy Lab Flow (Layer 0.5)

1. **Initialize DB** (strategy_registry.init_db) — create `output/strategy_lab.db` schema
2. **Generate initial population** (strategy_generator.run_strategy_lab) — N random param sets per pair
3. **For each generation:**
   - For each strategy candidate:
     - Run backtest (backtest_engine.run_hedge_backtest) → trades list
     - Compute stats (backtest_engine.compute_stats) → win rate, profit factor, Sharpe, drawdown
     - Validate (backtest_engine.validate_strategy) → passes or fails with reason
     - Save to DB (strategy_registry.save_strategy)
   - If passed < threshold, mutate best from this generation and create next generation
4. **Repeat** for N generations
5. **Query & visualize** (dashboard.py via strategy_registry.list_strategies) — filter by status/pair, inspect details

### Trade Execution Cycle (Future Layers 2–4)

1. Hedge Engine polls Layer 0 outputs (z-score, regime, ML signal)
2. Generates "open_hedge" or "close_hedge" decisions
3. Risk Engine validates: size via Kelly, check exposure limits, drawdown headroom
4. Approves or rejects order
5. MQL5 EA receives approved order, executes in MT5, monitors for stops

**State Management:**
- Per-symbol: features (rolling vol, z-score, regime, ML signal) — ephemeral, recalculated each bar
- Per-pair: spread z-score, correlation, beta (rolling, causal)
- Per-position: entry price, entry bar, entry spread std dev, direction
- Per-account: equity, drawdown high-water mark, open position count, daily risk used
- Persistent: strategy registry (SQLite) stores all tested parameters + stats + trade history

## Key Abstractions

**Hedge Pair:**
- Purpose: Represents two cointegrated symbols (e.g., EURUSD + GBPUSD) with a known hedge ratio (beta) to construct a stationary spread
- Examples: `src/data_pipeline.py` (scan_hedge_candidates output rows), `src/backtest_engine.py` (run_hedge_backtest parameters)
- Pattern: Two price series + beta → spread = price_a - beta * price_b; z-score of spread is the trading signal

**Strategy:**
- Purpose: A set of parameters for the hedge logic (entry_threshold, exit_threshold, max_hold_bars, etc.) + its test statistics
- Examples: Database rows in `strategy_registry.py`, rows in dashboard table
- Pattern: params dict (entry_threshold=2.0, exit_threshold=0.3, ...) → backtest result → {trades, stats, status}

**Trade Record:**
- Purpose: A single completed hedge transaction (open bar, close bar, PnL in R, exit reason)
- Examples: Entries in the "trades" JSON field in strategy registry
- Pattern: {entry_bar, exit_bar, bars_held, direction, pnl_r, exit_reason}

**Regime:**
- Purpose: Market state classification (trending, sideways, etc.) from HMM on returns & volatility
- Examples: Regime labels in `output/features_*.parquet`, used to condition ML model & entry decisions
- Pattern: Integer state (0, 1, 2) per bar; recalculated rolling

## Entry Points

**`src/data_pipeline.py --mode synth`**
- Location: `main()` function
- Triggers: User runs command-line with --mode synth (or --mode mt5)
- Responsibilities: Load/generate data → engineer features → test cointegration → detect regime → save CSV/parquets

**`src/strategy_generator.py --max-pairs 4 --n-generations 2`**
- Location: `main()` function
- Triggers: User runs command-line after data_pipeline completes
- Responsibilities: Read hedge candidates & features → orchestrate parameter generation/backtest/mutation loop → save results to SQLite

**`streamlit run dashboard.py`**
- Location: Streamlit app entry point
- Triggers: User runs after strategy_generator completes
- Responsibilities: Query strategy registry → render tables & charts → allow filtering & drill-down

**`python src/ml_model.py` (Future)**
- Location: Placeholder; to be implemented
- Triggers: User runs after Layer 0 data is ready
- Responsibilities: Train classifier on features & regime labels → produce inference interface

**`python src/hedge_engine.py` (Future)**
- Location: Placeholder; to be implemented
- Triggers: Runs live or in backtest loop
- Responsibilities: Read Layer 0 outputs + Layer 1 signal → emit open/close decisions

**`python src/risk_engine.py` (Future)**
- Location: Placeholder; to be implemented
- Triggers: Receives proposed orders from hedge_engine
- Responsibilities: Size & validate orders → emit approved orders to EA

**`mql5/ScalpingEA.mq5` (Future)**
- Location: Placeholder; to be implemented
- Triggers: Expert Advisor runs in MT5 terminal on chart
- Responsibilities: Receive orders → execute → manage positions

## Architectural Constraints

- **Threading:** Single-threaded, event-driven per layer. Data pipeline is sequential (fetch all symbols, then engineer features). Backtest engine is sequential per strategy. Strategy lab loop is sequential (generation by generation). No multi-threading or async I/O in current implementation; EA execution in MT5 is single-threaded by design.
- **Global state:** `PipelineConfig` dataclass holds configuration (symbol list, timeframe, window sizes). MT5 terminal connection (if used) is initialized per fetch call, not held globally. Strategy lab seeds RNG for reproducibility.
- **Circular imports:** None detected in Layer 0–0.5. Layer 2 will depend on 0 & 1; Layer 3 on 2; Layer 4 on 3. No backward dependencies.
- **Index alignment:** All symbols must share same DatetimeIndex in pandas operations (e.g., spread = price_a - beta * price_b). Synthetic data generation uses shared index from the start to prevent silent NaN from timestamp mismatch (bug fixed in ROADMAP).
- **Causal windows:** All rolling statistics (beta, z-score, correlation, volatility) use `.rolling()` which looks backward only. Recalculation of beta happens every N bars, using data strictly in the past window.
- **PnL units:** Strategy lab measures PnL in "R" (spread std-dev multiples) to decouple signal quality from position sizing; Layer 3 (risk engine) will convert to money units.

## Anti-Patterns

### Lookahead Bias in Backtest

**What happens:** When computing the hedge ratio (beta), the spread z-score, or correlation, including future bars in the window would allow the backtest to "see ahead," giving false performance.

**Why it's wrong:** Leads to overly optimistic strategy statistics; validated strategies fail in live trading.

**Do this instead:** Use causal rolling windows. In `src/backtest_engine.py`:
- `compute_rolling_beta()` slices data[i-window:i] for bar i (data strictly in the past)
- `zscore = (spread - spread.rolling(window).mean()) / spread.rolling(window).std()` — pandas `.rolling()` looks backward by definition

### Hard-Coded Indicator Thresholds

**What happens:** Using fixed RSI > 70 or MACD > 0 as entry signals without validating if they work in current market regime.

**Why it's wrong:** Indicators are often curve-fit to historical data and fail in new regimes (trending vs. sideways).

**Do this instead:** Use adaptive features from Layer 0 (rolling volatility, z-score of price within regime context) and validate in Layer 0.5 strategy lab on walk-forward data. Thresholds (entry_threshold, exit_threshold, max_hold_bars) are discovered via parameter search, not guessed.

### ML Model Deciding Position Size or Risk Limits

**What happens:** Passing an ML confidence score directly to position sizing (e.g., "high confidence → 10 units, low confidence → 1 unit") without a deterministic risk check.

**Why it's wrong:** ML can be confidently wrong; a model error can compound with large position into a catastrophic loss.

**Do this instead:** Layer 3 (Risk Engine) always applies deterministic rules (Kelly fraction, max exposure, drawdown limit) regardless of Layer 1 confidence. Layer 1 signal informs entry direction only; Layer 3 owns sizing.

### Monolithic Strategy Class

**What happens:** Combining data fetching, feature engineering, backtesting, risk checks, and execution into one module.

**Why it's wrong:** Impossible to test parts independently; fixing a bug in risk logic might break backtest calculation.

**Do this instead:** Separate into layers (current design). Each layer can be tested in isolation: Layer 0 produces valid features; Layer 0.5 backtests correctly without lookahead; Layer 2 proposes reasonable orders; Layer 3 sizes them properly; Layer 4 executes.

### No Trade Count Minimum in Validation

**What happens:** Approving a strategy that made only 3 trades with +200% return.

**Why it's wrong:** 3 trades is noise; any positive return from 3 random trades is likely luck.

**Do this instead:** Approval gate (backtest_engine.validate_strategy, DEFAULT_THRESHOLDS) enforces minimum trade count (default: 20). Below that threshold, any statistic is statistically insignificant.

## Error Handling

**Strategy:** Fail fast with logging; do not silently return NaN or empty results.

**Patterns:**
- **Data fetch failure** (Layer 0): Raise RuntimeError with MT5 error code or file not found message; log.error
- **Insufficient data for calculation** (Layer 0): If rolling window size exceeds data length, or if all correlation values are NaN, log.warning and skip pair/symbol
- **HMM convergence failure** (Layer 0): Catch convergence warning; fall back to percentile-based regime classification (also in data_pipeline.py detect_regime)
- **Backtest edge cases** (Layer 0.5): If trades list is empty, compute_stats still returns baseline stats (all zeros); validate_strategy handles this (fails approval)
- **Database I/O** (strategy_registry): If SQLite connection fails, sqlite3.Error bubbles up; calling code should catch and retry or abort
- **Dashboard empty result** (dashboard.py): Checks if df.empty and shows warning message ("Run data_pipeline and strategy_generator first")

## Cross-Cutting Concerns

**Logging:** 
- Layer 0 (data_pipeline.py): `logging.basicConfig` + log.info on symbol fetch, feature completion, cointegration results, regime detection
- Layer 0.5: Inline print() to stdout (strategy_generator passes log=print); logs strategy test count, generation count, mutation actions
- Dashboard: Streamlit st.warning/st.info for user feedback
- No log level configuration in current code; consider adding --verbose flag in future

**Validation:**
- Data alignment: All symbols must share same DatetimeIndex (enforced in load_all_symbols by shared_idx)
- Cointegration p-value: Only pairs with p < coint_pvalue_threshold (default 0.05) are considered candidates (scan_hedge_candidates)
- Strategy stats: Each tested strategy validated against DEFAULT_THRESHOLDS (min_trades, min_profit_factor, min_sharpe, max_drawdown); status set to "passed" or "failed" with reasons (validate_strategy)
- Feature availability: engineer_features drops NaN rows; rolling windows start producing non-NaN only after window_size bars

**Regime Awareness:**
- HMM output: 3-state regime labels (Layer 0) can be fed to Layer 1 (ML model) as conditional feature; Layer 2 can adjust entry/exit thresholds per regime (not yet implemented in Layer 0.5 backtest, but framework is ready)
- UI: Dashboard could be extended to show regime distribution per tested strategy (future enhancement)

---

*Architecture analysis: 2026-06-30*
