# Testing Patterns

**Analysis Date:** 2026-06-30

## Current State

**No automated test suite exists.** There are no pytest/unittest configurations, no test files (*.test.py, *.spec.py), and no CI/CD pipeline.

Testing is performed manually via:
1. **Synthetic data mode** (`python src/data_pipeline.py --mode synth`): Validates pipeline logic without MT5 dependency
2. **Strategy laboratory backtest** (`python src/strategy_generator.py`): Generates and backtests strategies on historical data
3. **Dashboard UI** (`streamlit run dashboard.py`): Visual validation of results

## Missing Test Infrastructure

**Test Runner:**
- No pytest or unittest configuration found
- No `conftest.py` or `setup.cfg` for test discovery
- No `requirements-dev.txt` or dev dependencies specified

**Test Coverage:**
- 0% (no test files)
- Critical functions untested: `fetch_mt5()`, `engineer_features()`, `scan_hedge_candidates()`, `detect_regime()`, `run_hedge_backtest()`

**Recommendations for Future Implementation:**

### Unit Testing Framework

**Choice:** pytest (recommended for scientific/quantitative code)
- Simpler assertion syntax than unittest
- Better fixture support for numerical data
- Plugin ecosystem (pytest-cov for coverage, pytest-xdist for parallel)

**Setup:**
```bash
pip install pytest pytest-cov pytest-xdist
```

**Config file** `pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

### Test File Organization

**Location:** Separate from source
```
tests/
├── conftest.py                    # Shared fixtures
├── test_data_pipeline.py          # Tests for src/data_pipeline.py
├── test_backtest_engine.py        # Tests for src/backtest_engine.py
├── test_strategy_generator.py     # Tests for src/strategy_generator.py
├── test_strategy_registry.py      # Tests for src/strategy_registry.py
├── fixtures/                      # Test data (parquet, csv samples)
│   ├── sample_features.parquet
│   └── sample_hedge_candidates.csv
└── unit/                          # Additional organization (optional)
    └── test_validators.py
```

**Naming:**
- Test modules: `test_<module_name>.py`
- Test functions: `test_<function_name>_<scenario>`
  - Example: `test_compute_rolling_beta_linear_series()`, `test_run_hedge_backtest_no_trades()`
- Test classes: `Test<Module>` for organization (optional)

**Inverse Location (Co-located):** Not recommended for this codebase, as source and tests would mix in `src/`

## Test Structure (Proposed)

### Example: `tests/test_data_pipeline.py`

```python
import pytest
import pandas as pd
import numpy as np
from src.data_pipeline import (
    engineer_features, scan_hedge_candidates, detect_regime, PipelineConfig
)

class TestEngineerFeatures:
    """Feature engineering tests."""
    
    @pytest.fixture
    def sample_df(self):
        """Create a synthetic price dataframe."""
        dates = pd.date_range("2023-01-01", periods=200, freq="5min")
        data = {
            "open": np.random.uniform(1.0, 1.1, 200),
            "high": np.random.uniform(1.01, 1.11, 200),
            "low": np.random.uniform(0.99, 1.09, 200),
            "close": np.random.uniform(1.0, 1.1, 200),
            "volume": np.random.randint(50, 500, 200),
        }
        return pd.DataFrame(data, index=dates)
    
    def test_engineer_features_returns_dataframe(self, sample_df):
        """Check that function returns a DataFrame."""
        result = engineer_features(sample_df, corr_window=50)
        assert isinstance(result, pd.DataFrame)
    
    def test_engineer_features_adds_log_ret(self, sample_df):
        """Verify log_ret column is computed."""
        result = engineer_features(sample_df, corr_window=50)
        assert "log_ret" in result.columns
    
    def test_engineer_features_drops_na(self, sample_df):
        """Verify NaN rows are dropped."""
        result = engineer_features(sample_df, corr_window=50)
        assert result.isna().sum().sum() == 0


class TestDetectRegime:
    """Regime detection tests."""
    
    @pytest.fixture
    def features_with_vol(self):
        """Create synthetic features with volatility."""
        idx = pd.date_range("2023-01-01", periods=300, freq="5min")
        df = pd.DataFrame(
            {
                "log_ret": np.random.normal(0, 0.001, 300),
                "vol_fast": np.random.uniform(0.001, 0.005, 300),
            },
            index=idx,
        )
        return df
    
    def test_detect_regime_returns_series(self, features_with_vol):
        """Check return type."""
        result = detect_regime(features_with_vol, n_states=3)
        assert isinstance(result, pd.Series)
    
    def test_detect_regime_values_in_range(self, features_with_vol):
        """Verify regime labels are in valid range."""
        result = detect_regime(features_with_vol, n_states=3)
        assert result.min() >= 0
        assert result.max() < 3  # n_states = 3
```

### Example: `tests/test_backtest_engine.py`

```python
import pytest
import pandas as pd
import numpy as np
from src.backtest_engine import (
    compute_rolling_beta, compute_zscore, run_hedge_backtest, validate_strategy
)

class TestComputeRollingBeta:
    """Beta calculation tests."""
    
    @pytest.fixture
    def cointegrated_pair(self):
        """Create two cointegrated series."""
        n = 500
        common_factor = np.cumsum(np.random.normal(0, 0.0005, n))
        price_a = pd.Series(1.1 * np.exp(common_factor + np.random.normal(0, 0.0001, n)))
        price_b = pd.Series(1.2 * np.exp(0.8 * common_factor + np.random.normal(0, 0.0001, n)))
        return price_a, price_b
    
    def test_compute_rolling_beta_shape(self, cointegrated_pair):
        """Output series has same length as input."""
        a, b = cointegrated_pair
        result = compute_rolling_beta(a, b, window=100)
        assert len(result) == len(a)
    
    def test_compute_rolling_beta_no_nans(self, cointegrated_pair):
        """Result has no NaN values."""
        a, b = cointegrated_pair
        result = compute_rolling_beta(a, b, window=100)
        assert not result.isna().any()


class TestRunHedgeBacktest:
    """Full backtest simulation tests."""
    
    @pytest.fixture
    def sample_params(self):
        """Standard backtest parameters."""
        return {
            "entry_threshold": 2.0,
            "exit_threshold": 0.5,
            "min_correlation": 0.5,
            "max_hold_bars": 100,
            "beta_window": 200,
            "corr_window": 100,
            "recalc_every": 50,
        }
    
    @pytest.fixture
    def mean_reverting_pair(self):
        """Create synthetic mean-reverting spread."""
        n = 1000
        spread = np.sin(np.linspace(0, 10 * np.pi, n)) + np.random.normal(0, 0.1, n)
        price_a = pd.Series(1.1 * np.exp(np.cumsum(np.random.normal(0, 0.0001, n))))
        price_b = pd.Series(1.2 * np.exp(np.cumsum(np.random.normal(0, 0.0001, n))))
        return price_a, price_b
    
    def test_run_hedge_backtest_returns_dict(self, sample_params, mean_reverting_pair):
        """Output is a dictionary with trades and stats."""
        a, b = mean_reverting_pair
        result = run_hedge_backtest(a, b, sample_params)
        assert isinstance(result, dict)
        assert "trades" in result
        assert "stats" in result


class TestValidateStrategy:
    """Strategy validation gate tests."""
    
    def test_validate_strategy_rejects_few_trades(self):
        """Reject if total_trades < min_trades."""
        stats = {
            "total_trades": 5,
            "profit_factor": 2.0,
            "sharpe_per_trade": 0.5,
            "max_drawdown_r": 3.0,
            "total_return_r": 5.0,
        }
        passed, reasons = validate_strategy(stats)
        assert not passed
        assert any("poucos trades" in r for r in reasons)
    
    def test_validate_strategy_rejects_negative_return(self):
        """Reject if total_return_r <= 0."""
        stats = {
            "total_trades": 50,
            "profit_factor": 1.5,
            "sharpe_per_trade": 0.3,
            "max_drawdown_r": 5.0,
            "total_return_r": -2.0,
        }
        passed, reasons = validate_strategy(stats)
        assert not passed
        assert any("não positivo" in r for r in reasons)
    
    def test_validate_strategy_accepts_good_stats(self):
        """Accept strategy with good statistics."""
        stats = {
            "total_trades": 50,
            "profit_factor": 1.5,
            "sharpe_per_trade": 0.2,
            "max_drawdown_r": 5.0,
            "total_return_r": 10.0,
        }
        passed, reasons = validate_strategy(stats)
        assert passed
        assert len(reasons) == 0
```

### Example: `tests/conftest.py`

```python
import pytest
import pandas as pd
import numpy as np

@pytest.fixture(scope="session")
def synthetic_market_data():
    """Shared synthetic market data for all tests."""
    n = 2000
    dates = pd.date_range("2023-01-01", periods=n, freq="5min")
    
    market_factor = np.cumsum(np.random.normal(0, 0.0006, n))
    
    symbols = {}
    for sym, beta in [("EURUSD", 1.0), ("GBPUSD", 0.85), ("USDJPY", -0.6)]:
        close = np.exp(np.log(1.1) + beta * market_factor + np.random.normal(0, 0.001, n))
        symbols[sym] = pd.Series(close, index=dates, name="close")
    
    return symbols

@pytest.fixture
def temp_db(tmp_path):
    """Temporary SQLite database for strategy registry tests."""
    db_file = tmp_path / "test_strategies.db"
    return str(db_file)
```

## Mocking

**Framework:** `unittest.mock` (standard library)

**Patterns (Proposed):**

**1. Mock MT5 Connection**
```python
from unittest.mock import patch, MagicMock
import src.data_pipeline as pipeline

@patch("src.data_pipeline.mt5")
def test_fetch_mt5_connection_error(mock_mt5):
    """Handle MT5 connection failure gracefully."""
    mock_mt5.initialize.return_value = False
    mock_mt5.last_error.return_value = "Connection refused"
    
    with pytest.raises(RuntimeError):
        pipeline.fetch_mt5("EURUSD", "M5", 100)
```

**2. Mock File I/O**
```python
from unittest.mock import patch, mock_open

@patch("builtins.open", new_callable=mock_open)
def test_strategy_registry_save_writes_json(mock_file):
    """Verify database write calls correct INSERT."""
    # mock_file() returns MagicMock
    assert mock_file.called
```

**3. Avoid Mocking:**
- **Don't mock:** pandas operations, numpy arrays, simple data transformations
- **Reason:** These are libraries, not implementation details — testing against real behavior ensures compatibility
- **DO test:** Real data alignment (`reset_index(drop=True)` behavior), rolling window causality

**What NOT to Mock:**
- `pandas.Series.rolling()` — test with real rolling windows
- `numpy.linalg.lstsq()` — test with real matrix solutions
- DataFrame operations (`apply()`, `groupby()`) — always use real data

## Test Types

**Unit Tests:**
- **Scope:** Single function with synthetic data (e.g., `compute_rolling_beta()`, `engineer_features()`)
- **Approach:** 
  - Test edge cases (empty series, constant values, NaN handling)
  - Verify output shape and type
  - Validate numerical correctness (e.g., beta near expected value for known-cointegrated pair)
- **Location:** `tests/test_<module>.py`

**Integration Tests:**
- **Scope:** Multi-function workflows (e.g., data pipeline from features → hedge detection)
- **Approach:**
  - Use realistic synthetic data (correlated/cointegrated pairs)
  - Verify end-to-end output (e.g., `scan_hedge_candidates()` finds expected pairs)
  - Test error propagation (e.g., cointegration test failure doesn't crash pipeline)
- **Location:** `tests/test_<workflow>.py` or separate `tests/integration/`

**Backtesting Tests:**
- **Scope:** Backtest logic doesn't have lookahead bias, statistics are correct
- **Approach:**
  - Create known-outcome scenarios (mean-reverting spread → should generate trades)
  - Verify no lookahead (beta computed only from past data)
  - Check that Sharpe and profit factor match manual calculation
- **Location:** `tests/test_backtest_engine.py`

**E2E Tests:**
- **Not currently implemented**
- **Future:** Full pipeline from synthetic data generation → feature engineering → strategy generation → validation

## Run Commands (Proposed)

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific file
pytest tests/test_data_pipeline.py

# Run specific test class
pytest tests/test_data_pipeline.py::TestEngineerFeatures

# Run with coverage
pytest --cov=src --cov-report=html

# Run tests matching pattern
pytest -k "test_validate_strategy"

# Run in parallel (requires pytest-xdist)
pytest -n auto
```

## Fixtures and Factories

**Test Data Location:**
- Store reusable data in `tests/fixtures/` (parquet files, CSV samples)
- Use pytest fixtures for in-memory construction

**Example Fixtures (conftest.py):**
```python
@pytest.fixture
def hedge_candidates_df():
    """Sample cointegrated pairs from hedge detection."""
    return pd.DataFrame({
        "pair_a": ["EURUSD", "EURUSD"],
        "pair_b": ["GBPUSD", "USDJPY"],
        "correlation": [0.75, -0.45],
        "coint_pvalue": [0.02, 0.04],
        "is_cointegrated": [True, True],
        "hedge_ratio_beta": [0.85, -0.6],
    })

@pytest.fixture
def backtest_params():
    """Standard params for backtest engine."""
    return {
        "entry_threshold": 2.0,
        "exit_threshold": 0.5,
        "min_correlation": 0.5,
        "max_hold_bars": 100,
        "beta_window": 200,
        "corr_window": 100,
    }
```

## Coverage

**Requirements (Proposed):**
- Minimum 70% line coverage for `src/` directory
- 85% coverage for critical modules: `backtest_engine.py`, `data_pipeline.py`, `strategy_registry.py`
- Coverage gates on CI/CD (fail if coverage drops)

**View Coverage:**
```bash
pytest --cov=src --cov-report=html --cov-report=term
open htmlcov/index.html  # macOS/Linux
start htmlcov\index.html # Windows
```

**Coverage Report Interpretation:**
- Line coverage: % of lines executed
- Branch coverage: % of conditional branches tested (if/else, try/except)
- Focus on untested error paths and validation logic

## Known Gaps

**Not Tested:**
1. **MT5 connection:** Real terminal integration (environment-specific)
2. **Dashboard UI:** Streamlit interactive components (manual testing only)
3. **Strategy lab end-to-end:** Full mutation + backtest loop (integration test)
4. **Concurrent execution:** Multiple strategy tests in parallel (not applicable — single-threaded)
5. **Real market data:** Cannot test without live MT5 terminal

**Future Test Priorities:**
1. Unit tests for `backtest_engine.py` (most critical path)
2. Integration tests for data pipeline validation
3. Strategy registry persistence (database operations)
4. Validation gate logic (approve/reject strategy)

---

*Testing analysis: 2026-06-30*
