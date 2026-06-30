# Technology Stack

**Analysis Date:** 2026-06-30

## Languages

**Primary:**
- Python 3.10+ - Core pipeline, data processing, strategy generation, backtesting, and risk engine
- MQL5 - Expert Advisor execution layer (in terminal MetaTrader 5); currently not implemented

**Secondary:**
- Shell/PowerShell - Build and development automation

## Runtime

**Environment:**
- Python 3.10+ (via pip on Windows)

**Package Manager:**
- pip
- Lockfile: `requirements.txt` (present, not pinned to specific versions)

## Frameworks

**Core Data/Scientific:**
- pandas - Data manipulation, time series analysis, feature engineering (`src/data_pipeline.py`, `src/backtest_engine.py`)
- numpy - Numerical computations, array operations for rolling statistics
- statsmodels - Cointegration testing (Engle-Granger), OLS regression for hedge ratio calculation (`src/data_pipeline.py:48-49`)

**Machine Learning:**
- hmmlearn - Hidden Markov Model for market regime detection (`src/data_pipeline.py:52`, optional import with fallback)
- scikit-learn - Machine learning utilities (baseline for model layer, not yet implemented)
- lightgbm - Gradient boosting (specified in `docs/ml_model_spec.md` as preferred baseline)

**UI/Visualization:**
- Streamlit - Dashboard for strategy validation and results viewing (`dashboard.py:20`)
- Plotly - Interactive charting for equity curves and trade history (`dashboard.py:19`)

**Data Serialization:**
- PyArrow - Parquet file format for feature storage and time-series data (`requirements.txt:5`)

**Optional/Future:**
- PyTorch - Deep learning (conditional, for future LSTM/Transformer models, commented in `requirements.txt:15`)
- PyZMQ - Socket communication (conditional, for Python↔MQL5 messaging via ZeroMQ, commented in `requirements.txt:18`)

## Key Dependencies

**Critical:**
- MetaTrader5 (Windows-only, conditional) - Live/historical forex data from MT5 terminal (`src/data_pipeline.py:95`, lines 90-110)
  - Version: Not specified in requirements
  - Usage: Pulled into memory via `mt5.copy_rates_from_pos()` in MT5 mode
  - Platform: Windows only (MetaTrader 5 terminal must be open and logged in)

**Infrastructure:**
- pandas - Data frames for all time-series and statistical operations
- numpy - Linear algebra (OLS via `np.linalg.lstsq` in `src/backtest_engine.py:44`)
- statsmodels - Cointegration matrix and hedge ratio calculation core

## Configuration

**Environment:**
- Database location: `STRATEGY_DB` environment variable (default: `output/strategy_lab.db`), read in `dashboard.py:27`
- MT5 connection: Login, password, server passed as CLI arguments to `data_pipeline.py --mode mt5`
  - No env var defaults; credentials must be supplied on command line

**Build:**
- No build configuration (pure Python, no compilation step)
- Entry points:
  - `python src/data_pipeline.py` - Data ingestion and feature pipeline
  - `python src/strategy_generator.py` - Strategy mutation and backtest lab
  - `streamlit run dashboard.py` - Web UI for validation
  - (Future) MQL5 compilation in MetaEditor

**Data Output Artifacts:**
- `output/features_*.parquet` - Time-series features per symbol (Parquet format for efficiency)
- `output/hedge_candidates.csv` - Cointegrated pair rankings with z-scores
- `output/strategy_lab.db` - SQLite database of all tested strategies (`src/strategy_registry.py:18-41`)

## Platform Requirements

**Development:**
- Windows (required for MetaTrader5 terminal integration via `MetaTrader5` SDK)
- Python 3.10+ installed
- MetaTrader 5 terminal installed (for `--mode mt5` testing; synthetic mode works anywhere)

**Production:**
- Windows (MT5 terminal must be open and logged in for live data and execution)
- MetaTrader 5 terminal with active broker connection
- MT5 account configured in hedging mode (not netting) per `docs/risk_engine_mql5_spec.md:40-43`

## Data Storage & Persistence

**Local Storage:**
- `output/` directory - Generated parquet, CSV, and SQLite outputs (not versioned per `.gitignore:2-4`)
- `.planning/` directory (future) - Codebase analysis and architecture documents

**Credential Storage:**
- `.env` file excluded (per `.gitignore:14`) - Not used currently; MT5 credentials passed via CLI args
- Broker server, login, password passed via command-line arguments (not stored)

---

*Stack analysis: 2026-06-30*
