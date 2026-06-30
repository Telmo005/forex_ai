<!-- GSD:project-start source:PROJECT.md -->

## Project

**Forex AI Scalping System**

Sistema de trading algorítmico autónomo para forex, focado em scalping via hedge estatístico entre pares correlacionados/cointegrados. Deteta o regime de mercado e adapta o comportamento em tempo real, gera e valida continuamente novas estratégias num laboratório offline, e troca automaticamente entre estratégias já aprovadas (ou reduz exposição) quando a performance ao vivo diverge do esperado. Execução real via Expert Advisor MQL5 ligado a MT5, com um motor de risco determinístico como última linha de defesa. Construído e usado por um único trader (o utilizador).

**Core Value:** O sistema nunca deve negociar capital real com uma estratégia que não passou por validação objetiva (manual ou automática) — a sobrevivência do capital vem antes de qualquer otimização de retorno.

### Constraints

- **Risco**: Toda a lógica de stop-loss, drawdown máximo e limite de exposição tem de existir como regra determinística (não-ML) no motor de risco/EA — nunca depender só de inferência de modelo
- **Validação**: Sempre walk-forward (janelas deslizantes no tempo), nunca split aleatório em série temporal
- **Custos**: Spread, slippage e comissão entram sempre no backtest — crítico em scalping
- **Conta MT5**: Tem de estar em modo hedging (não netting) para a lógica de hedge multi-perna funcionar — confirmar com a corretora antes de execução real
- **Produção**: Nenhum parâmetro de estratégia entra em `hedge_engine.py` sem primeiro passar por um gate de aprovação (manual no dashboard, ou automático com critérios objetivos equivalentes)
- **Conta real**: Nunca passar de demo para real sem confirmação explícita do utilizador, mesmo depois de validação exaustiva em demo

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.10+ - Core pipeline, data processing, strategy generation, backtesting, and risk engine
- MQL5 - Expert Advisor execution layer (in terminal MetaTrader 5); currently not implemented
- Shell/PowerShell - Build and development automation

## Runtime

- Python 3.10+ (via pip on Windows)
- pip
- Lockfile: `requirements.txt` (present, not pinned to specific versions)

## Frameworks

- pandas - Data manipulation, time series analysis, feature engineering (`src/data_pipeline.py`, `src/backtest_engine.py`)
- numpy - Numerical computations, array operations for rolling statistics
- statsmodels - Cointegration testing (Engle-Granger), OLS regression for hedge ratio calculation (`src/data_pipeline.py:48-49`)
- hmmlearn - Hidden Markov Model for market regime detection (`src/data_pipeline.py:52`, optional import with fallback)
- scikit-learn - Machine learning utilities (baseline for model layer, not yet implemented)
- lightgbm - Gradient boosting (specified in `docs/ml_model_spec.md` as preferred baseline)
- Streamlit - Dashboard for strategy validation and results viewing (`dashboard.py:20`)
- Plotly - Interactive charting for equity curves and trade history (`dashboard.py:19`)
- PyArrow - Parquet file format for feature storage and time-series data (`requirements.txt:5`)
- PyTorch - Deep learning (conditional, for future LSTM/Transformer models, commented in `requirements.txt:15`)
- PyZMQ - Socket communication (conditional, for Python↔MQL5 messaging via ZeroMQ, commented in `requirements.txt:18`)

## Key Dependencies

- MetaTrader5 (Windows-only, conditional) - Live/historical forex data from MT5 terminal (`src/data_pipeline.py:95`, lines 90-110)
- pandas - Data frames for all time-series and statistical operations
- numpy - Linear algebra (OLS via `np.linalg.lstsq` in `src/backtest_engine.py:44`)
- statsmodels - Cointegration matrix and hedge ratio calculation core

## Configuration

- Database location: `STRATEGY_DB` environment variable (default: `output/strategy_lab.db`), read in `dashboard.py:27`
- MT5 connection: Login, password, server passed as CLI arguments to `data_pipeline.py --mode mt5`
- No build configuration (pure Python, no compilation step)
- Entry points:
- `output/features_*.parquet` - Time-series features per symbol (Parquet format for efficiency)
- `output/hedge_candidates.csv` - Cointegrated pair rankings with z-scores
- `output/strategy_lab.db` - SQLite database of all tested strategies (`src/strategy_registry.py:18-41`)

## Platform Requirements

- Windows (required for MetaTrader5 terminal integration via `MetaTrader5` SDK)
- Python 3.10+ installed
- MetaTrader 5 terminal installed (for `--mode mt5` testing; synthetic mode works anywhere)
- Windows (MT5 terminal must be open and logged in for live data and execution)
- MetaTrader 5 terminal with active broker connection
- MT5 account configured in hedging mode (not netting) per `docs/risk_engine_mql5_spec.md:40-43`

## Data Storage & Persistence

- `output/` directory - Generated parquet, CSV, and SQLite outputs (not versioned per `.gitignore:2-4`)
- `.planning/` directory (future) - Codebase analysis and architecture documents
- `.env` file excluded (per `.gitignore:14`) - Not used currently; MT5 credentials passed via CLI args
- Broker server, login, password passed via command-line arguments (not stored)

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Overview

- **Type hints** for all function signatures
- **Dataclasses** for configuration objects
- **Descriptive docstrings** explaining domain-specific logic (financial/statistical concepts)
- **Numeric precision** in financial calculations (rounded values stored in dictionaries)
- **Logging over silent failures** with structured logging throughout

## Naming Patterns

- `snake_case` (e.g., `data_pipeline.py`, `backtest_engine.py`, `strategy_registry.py`)
- Descriptive purpose in name: `data_pipeline`, `backtest_engine`, `hedge_candidates`
- `snake_case` verbs for action (e.g., `fetch_mt5()`, `engineer_features()`, `scan_hedge_candidates()`)
- Prefix with descriptive scope: `compute_rolling_beta()`, `detect_regime()`, `validate_strategy()`
- Single responsibility per function name
- `snake_case` for all variable names
- Descriptive, full words (e.g., `correlation`, `spread_zscore`, `entry_threshold`, not abbrev.)
- Dictionary keys are `snake_case` strings (e.g., `"pair_a"`, `"coint_pvalue"`, `"profit_factor"`)
- UPPERCASE_CONSTANTS for module-level immutable mappings (e.g., `TF_MAP_MINUTES`, `PARAM_RANGES`, `INT_PARAMS`)
- Use Python 3.10+ union syntax: `dict | None` (not `Optional[dict]`)
- Use `pd.Series`, `pd.DataFrame` for pandas objects (imported as aliases)
- Type hints on parameters: `def func(x: str, y: int) -> dict:`
- Return types explicit for all functions, including `-> None` for side-effect-only functions
- PascalCase: `PipelineConfig` (in `data_pipeline.py`)
- Contains configuration parameters with sensible defaults via `field(default_factory=...)`

## Code Style

- No external formatter enforced (no `.prettierrc`, `.flake8`, or `pyproject.toml` linter config)
- Follows implicit PEP 8 style
- Line length: not strictly enforced (docstrings may exceed 100 chars for explanatory text)
- Indentation: 4 spaces
- No explicit linting configuration present
- Code assumes manual review for style consistency
- Type hints serve as implicit correctness check
- Used to avoid Python reserved keywords: `open_` (in `fetch_synthetic()` at `src/data_pipeline.py:143`)

## Import Organization

- No path aliases (no `jsconfig.json` equivalent)
- Relative imports used: `from backtest_engine import ...` (assumes script run from parent directory)
- `sys.path` manipulation in `dashboard.py` (`src/`) for streamlit entry point:
- `np` for numpy: `import numpy as np`
- `pd` for pandas: `import pandas as pd`

## Error Handling

- **Try-except for optional dependencies:** Graceful degradation when imports fail
- **Specific exception handling:** Catch narrow exception classes, log with context
- **Fallback strategies:** If operation fails (HMM convergence, MT5 connection), fall back to deterministic alternative
- **RuntimeError for critical failures:** Unrecoverable errors that stop execution

## Logging

- Module-level logger: `log = logging.getLogger("data_pipeline")`
- Configured at module entry via `logging.basicConfig()` (in `data_pipeline.py`)
- Format: `"%(asctime)s | %(levelname)s | %(message)s"`
- **Info level:** Processing progress, pipeline milestones
- **Warning level:** Non-fatal errors, fallbacks, degradation
- **Print in lab:** Strategy generator uses `log` parameter (default `print`) for test results

## Comments

- **Mathematical or domain-specific logic:** Explain the "why" of formulas or statistical decisions
- **Non-obvious data transformations:** Explain index alignment, lookahead prevention
- **Boundary conditions:** Explain thresholds or iteration limits
- **Section dividers:** Separate logical blocks with full-width comment sections
- Commenting obvious code: `x = 1  # set x to 1` (not used)
- TODOs without context (none present)
- **Module docstrings:** Comprehensive (20+ lines), explain purpose, modes of operation, usage examples
- **Function docstrings:** Explain parameters, behavior, assumptions (no formal parameters list)
- **Docstrings in Portuguese:** Full codebase uses Portuguese for variable names, comments, and docstrings (domain language matches finance/trading literature in Portuguese)

## Function Design

- Functions are concise (10–50 lines typically), with clear single responsibility
- Longer functions (50–100 lines) include subsection comments to break logic
- Functions accept config objects (`PipelineConfig`) rather than many individual params
- Params typed explicitly: `def func(x: str, y: int, z: float | None = None):`
- Default arguments used for optional/fallback values: `params.get("recalc_every", 50)`
- Explicit return types: `-> dict`, `-> pd.DataFrame`, `-> tuple[bool, list[str]]`
- Return structured data (dicts, dataclasses, pandas objects) for composability
- Multiple return values as tuple only when semantically related: `(zscore, std) = compute_zscore(...)`
- None returned for side-effect-only functions (file I/O, plotting)
- Small helper functions composed into larger workflows
- Example: `run_pipeline()` calls `load_all_symbols()`, `engineer_features()`, `detect_regime()`, `scan_hedge_candidates()` in sequence

## Module Design

- Modules define public functions (no `__all__` used)
- Import directly: `from backtest_engine import run_hedge_backtest, validate_strategy`
- Private/internal functions not prefixed with `_` (convention not enforced)
- Configuration objects bundled with defaults: `PipelineConfig` in `data_pipeline.py`
- Used for clarity, not all modules use them (e.g., `backtest_engine.py` uses plain dicts for params)
- No barrel/index files (`__init__.py` not present in `src/`)
- Direct imports from module names: `from strategy_registry import list_strategies`
- Avoided by design: linear dependency flow (pipeline → features → hedge → backtest → registry)
- `strategy_generator.py` imports from both `backtest_engine.py` and `strategy_registry.py` (no cycle)

## Numeric Precision

- Financial metrics rounded to 3–4 decimal places before storage
- Example: `"correlation": round(corr, 3)`, `"coint_pvalue": round(pvalue, 4)`
- Stored in dictionaries to maintain precision for statistical analysis
- NumPy operations on raw arrays: `np.linalg.lstsq()`, `np.maximum.accumulate()`
- Results converted to Python floats for serialization: `float(pnl_r)`, `round(float(pnl_r), 4)`

## Pandas Usage

- Explicit positional reset when mixing series from different symbols
- Rolling windows with `rolling()` method (causal by definition)
- Forward-fill and back-fill for sparse data: `.ffill().bfill()`
- Explicit column specification: `pd.DataFrame({"col1": vals1, "col2": vals2}, index=idx)`
- Not relying on positional alignment (index provided explicitly)

## Naming Across Layers

- `entry_threshold`, `exit_threshold`, `min_correlation` (backtest params)
- `pair_a`, `pair_b` (symbol pairs)
- `pnl_r`, `profit_factor`, `win_rate` (metrics)
- Underscore-separated compound names, no camelCase

## Environment-Specific Behavior

- `mode: str` parameter defaults to "synth" (no network dependencies)
- "mt5" mode only available on Windows with terminal running
- `MetaTrader5` imported locally in function (not module-level) — allows import on non-Windows systems
- `hmmlearn` imported with try-except; system degrades gracefully if not installed

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## System Overview

```text

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

- **No ML-only decisions** — risk rules (Layer 3) are always deterministic; ML (Layer 1) informs but does not override.
- **Causal processing** — no lookahead bias; rolling windows look backward only; beta/z-score/correlation recalculated using past data only.
- **Walk-forward validation** — strategy lab trains on historical window, then tests on forward window; parameters never chosen by eye.
- **Unit economics in "R"** — PnL measured in spread std-dev multiples, not money; decouples signal quality from position sizing (Layer 3 owns sizing).
- **Mutation-driven search** — if a generation of strategies mostly fails, best candidates are mutated and tested again; automates hyperparameter exploration.

## Layers

- Purpose: Ingest price data, engineer adaptive features, detect pairs suitable for hedging, classify market regime
- Location: `src/data_pipeline.py`
- Contains: MT5/synthetic data fetch, feature engineering (rolling vol, z-score), Engle-Granger cointegration test, HMM regime detection, CSV/parquet output
- Depends on: MT5 terminal (if --mode mt5) or numpy/pandas (if --mode synth)
- Used by: Layer 0.5 (reads hedge candidates & features), Layer 1 (reads features & regime labels)
- Purpose: Automated parameter search and validation; ensures only strategies passing objective criteria advance to production
- Location: `src/backtest_engine.py`, `src/strategy_registry.py`, `src/strategy_generator.py`, `dashboard.py`
- Contains: Backtest engine (simulates hedge logic without lookahead), stats calculator, approval gate (min trades/profit factor/Sharpe/drawdown thresholds), SQLite registry, mutation loop, Streamlit UI
- Depends on: Layer 0 outputs (hedge candidates, features), pandas, numpy, statsmodels (for stats)
- Used by: Humans (via dashboard); Layer 2 will consume approved parameters from DB
- Purpose: Predict directional probability + confidence per symbol, conditioned on regime
- Location: `src/ml_model.py` (placeholder)
- Contains: Feature pipeline, classifier, inference interface
- Depends on: Layer 0 features; possibly scikit-learn/LightGBM or PyTorch (if deep learning)
- Used by: Layer 2 (optional signal for entry direction)
- Purpose: Decide when to open/close multi-leg hedge positions
- Location: `src/hedge_engine.py` (placeholder)
- Contains: Entry logic (high z-score + correlation + optional ML signal), exit logic (reversion, correlation breakdown, time stop, risk stop from Layer 3)
- Depends on: Layer 0 outputs (z-score, correlation, cointegration test, regime), Layer 1 (optional direction signal)
- Used by: Layer 3 (receives proposed orders)
- Purpose: Validate and size orders; enforce deterministic risk limits
- Location: `src/risk_engine.py` (placeholder) + `mql5/RiskGuard.mqh` (placeholder)
- Contains: Position sizing (Kelly fraction), max exposure logic, drawdown enforcement, kill-switch
- Depends on: Layer 2 (proposed orders), account state
- Used by: Layer 4 (sends approved orders)
- Purpose: Execute trades in MT5 terminal; manage open positions; apply stops
- Location: `mql5/ScalpingEA.mq5` + related includes (placeholder)
- Contains: Order placement, position management, stop application, standalone risk enforcement (if Python link drops)
- Depends on: Layer 3 (approved orders or direct Python feed), MT5 API
- Used by: MT5 terminal / real trading account

## Data Flow

### Primary Request Path (Full Pipeline)

### Strategy Lab Flow (Layer 0.5)

### Trade Execution Cycle (Future Layers 2–4)

- Per-symbol: features (rolling vol, z-score, regime, ML signal) — ephemeral, recalculated each bar
- Per-pair: spread z-score, correlation, beta (rolling, causal)
- Per-position: entry price, entry bar, entry spread std dev, direction
- Per-account: equity, drawdown high-water mark, open position count, daily risk used
- Persistent: strategy registry (SQLite) stores all tested parameters + stats + trade history

## Key Abstractions

- Purpose: Represents two cointegrated symbols (e.g., EURUSD + GBPUSD) with a known hedge ratio (beta) to construct a stationary spread
- Examples: `src/data_pipeline.py` (scan_hedge_candidates output rows), `src/backtest_engine.py` (run_hedge_backtest parameters)
- Pattern: Two price series + beta → spread = price_a - beta * price_b; z-score of spread is the trading signal
- Purpose: A set of parameters for the hedge logic (entry_threshold, exit_threshold, max_hold_bars, etc.) + its test statistics
- Examples: Database rows in `strategy_registry.py`, rows in dashboard table
- Pattern: params dict (entry_threshold=2.0, exit_threshold=0.3, ...) → backtest result → {trades, stats, status}
- Purpose: A single completed hedge transaction (open bar, close bar, PnL in R, exit reason)
- Examples: Entries in the "trades" JSON field in strategy registry
- Pattern: {entry_bar, exit_bar, bars_held, direction, pnl_r, exit_reason}
- Purpose: Market state classification (trending, sideways, etc.) from HMM on returns & volatility
- Examples: Regime labels in `output/features_*.parquet`, used to condition ML model & entry decisions
- Pattern: Integer state (0, 1, 2) per bar; recalculated rolling

## Entry Points

- Location: `main()` function
- Triggers: User runs command-line with --mode synth (or --mode mt5)
- Responsibilities: Load/generate data → engineer features → test cointegration → detect regime → save CSV/parquets
- Location: `main()` function
- Triggers: User runs command-line after data_pipeline completes
- Responsibilities: Read hedge candidates & features → orchestrate parameter generation/backtest/mutation loop → save results to SQLite
- Location: Streamlit app entry point
- Triggers: User runs after strategy_generator completes
- Responsibilities: Query strategy registry → render tables & charts → allow filtering & drill-down
- Location: Placeholder; to be implemented
- Triggers: User runs after Layer 0 data is ready
- Responsibilities: Train classifier on features & regime labels → produce inference interface
- Location: Placeholder; to be implemented
- Triggers: Runs live or in backtest loop
- Responsibilities: Read Layer 0 outputs + Layer 1 signal → emit open/close decisions
- Location: Placeholder; to be implemented
- Triggers: Receives proposed orders from hedge_engine
- Responsibilities: Size & validate orders → emit approved orders to EA
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

- `compute_rolling_beta()` slices data[i-window:i] for bar i (data strictly in the past)
- `zscore = (spread - spread.rolling(window).mean()) / spread.rolling(window).std()` — pandas `.rolling()` looks backward by definition

### Hard-Coded Indicator Thresholds

### ML Model Deciding Position Size or Risk Limits

### Monolithic Strategy Class

### No Trade Count Minimum in Validation

## Error Handling

- **Data fetch failure** (Layer 0): Raise RuntimeError with MT5 error code or file not found message; log.error
- **Insufficient data for calculation** (Layer 0): If rolling window size exceeds data length, or if all correlation values are NaN, log.warning and skip pair/symbol
- **HMM convergence failure** (Layer 0): Catch convergence warning; fall back to percentile-based regime classification (also in data_pipeline.py detect_regime)
- **Backtest edge cases** (Layer 0.5): If trades list is empty, compute_stats still returns baseline stats (all zeros); validate_strategy handles this (fails approval)
- **Database I/O** (strategy_registry): If SQLite connection fails, sqlite3.Error bubbles up; calling code should catch and retry or abort
- **Dashboard empty result** (dashboard.py): Checks if df.empty and shows warning message ("Run data_pipeline and strategy_generator first")

## Cross-Cutting Concerns

- Layer 0 (data_pipeline.py): `logging.basicConfig` + log.info on symbol fetch, feature completion, cointegration results, regime detection
- Layer 0.5: Inline print() to stdout (strategy_generator passes log=print); logs strategy test count, generation count, mutation actions
- Dashboard: Streamlit st.warning/st.info for user feedback
- No log level configuration in current code; consider adding --verbose flag in future
- Data alignment: All symbols must share same DatetimeIndex (enforced in load_all_symbols by shared_idx)
- Cointegration p-value: Only pairs with p < coint_pvalue_threshold (default 0.05) are considered candidates (scan_hedge_candidates)
- Strategy stats: Each tested strategy validated against DEFAULT_THRESHOLDS (min_trades, min_profit_factor, min_sharpe, max_drawdown); status set to "passed" or "failed" with reasons (validate_strategy)
- Feature availability: engineer_features drops NaN rows; rolling windows start producing non-NaN only after window_size bars
- HMM output: 3-state regime labels (Layer 0) can be fed to Layer 1 (ML model) as conditional feature; Layer 2 can adjust entry/exit thresholds per regime (not yet implemented in Layer 0.5 backtest, but framework is ready)
- UI: Dashboard could be extended to show regime distribution per tested strategy (future enhancement)

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

| Skill | Description | Path |
|-------|-------------|------|
| mql5-trading-ea | Padrões de código MQL5 para Expert Advisors de trading - gestão de posições em conta modo hedging (não netting), normalização de lote e stops, uso da classe CTrade, verificação de regras de risco no lado da execução, e comunicação com um processo Python externo via ficheiro partilhado. Usa sempre esta skill ao escrever, alterar ou depurar qualquer ficheiro .mq5 ou .mqh neste projeto, ou ao discutir execução de ordens, gestão de posições, ou o Expert Advisor. | `.claude/skills/mql5-trading-ea/SKILL.md` |
| quant-finance-math | Fórmulas, testes estatísticos e padrões de código Python para matemática quantitativa aplicada a forex - cointegração (Engle-Granger), filtro de Kalman para hedge ratio dinâmico, Hidden Markov Models para deteção de regime, Kelly criterion para dimensionamento de posição, e triple barrier labeling. Usa sempre esta skill ao escrever ou alterar qualquer lógica de hedge estatístico, deteção de regime, dimensionamento de posição, ou validação de modelo neste projeto - mesmo que o pedido não mencione explicitamente "matemática" ou "estatística". | `.claude/skills/quant-finance-math/SKILL.md` |
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
