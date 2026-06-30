# Coding Conventions

**Analysis Date:** 2026-06-30

## Overview

This codebase follows **Python 3.10+ style** with a strong emphasis on:
- **Type hints** for all function signatures
- **Dataclasses** for configuration objects
- **Descriptive docstrings** explaining domain-specific logic (financial/statistical concepts)
- **Numeric precision** in financial calculations (rounded values stored in dictionaries)
- **Logging over silent failures** with structured logging throughout

## Naming Patterns

**Files:**
- `snake_case` (e.g., `data_pipeline.py`, `backtest_engine.py`, `strategy_registry.py`)
- Descriptive purpose in name: `data_pipeline`, `backtest_engine`, `hedge_candidates`

**Functions:**
- `snake_case` verbs for action (e.g., `fetch_mt5()`, `engineer_features()`, `scan_hedge_candidates()`)
- Prefix with descriptive scope: `compute_rolling_beta()`, `detect_regime()`, `validate_strategy()`
- Single responsibility per function name

**Variables:**
- `snake_case` for all variable names
- Descriptive, full words (e.g., `correlation`, `spread_zscore`, `entry_threshold`, not abbrev.)
- Dictionary keys are `snake_case` strings (e.g., `"pair_a"`, `"coint_pvalue"`, `"profit_factor"`)
- UPPERCASE_CONSTANTS for module-level immutable mappings (e.g., `TF_MAP_MINUTES`, `PARAM_RANGES`, `INT_PARAMS`)

**Types:**
- Use Python 3.10+ union syntax: `dict | None` (not `Optional[dict]`)
- Use `pd.Series`, `pd.DataFrame` for pandas objects (imported as aliases)
- Type hints on parameters: `def func(x: str, y: int) -> dict:`
- Return types explicit for all functions, including `-> None` for side-effect-only functions

**Dataclass Naming:**
- PascalCase: `PipelineConfig` (in `data_pipeline.py`)
- Contains configuration parameters with sensible defaults via `field(default_factory=...)`

## Code Style

**Formatting:**
- No external formatter enforced (no `.prettierrc`, `.flake8`, or `pyproject.toml` linter config)
- Follows implicit PEP 8 style
- Line length: not strictly enforced (docstrings may exceed 100 chars for explanatory text)
- Indentation: 4 spaces

**Linting:**
- No explicit linting configuration present
- Code assumes manual review for style consistency
- Type hints serve as implicit correctness check

**Trailing Underscores:**
- Used to avoid Python reserved keywords: `open_` (in `fetch_synthetic()` at `src/data_pipeline.py:143`)

## Import Organization

**Order:**
1. `from __future__ import annotations` (at top, Python 3.10+)
2. Standard library: `import argparse`, `import logging`, `import os`, `from dataclasses import ...`
3. Third-party: `import numpy as np`, `import pandas as pd`, `import streamlit as st`
4. Local: `from backtest_engine import run_hedge_backtest`

**Path Aliases:**
- No path aliases (no `jsconfig.json` equivalent)
- Relative imports used: `from backtest_engine import ...` (assumes script run from parent directory)
- `sys.path` manipulation in `dashboard.py` (`src/`) for streamlit entry point:
  ```python
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
  ```

**Module Aliases:**
- `np` for numpy: `import numpy as np`
- `pd` for pandas: `import pandas as pd`

## Error Handling

**Patterns:**
- **Try-except for optional dependencies:** Graceful degradation when imports fail
  ```python
  try:
      from hmmlearn.hmm import GaussianHMM
      _HAS_HMM = True
  except ImportError:
      _HAS_HMM = False
  ```
  (See `src/data_pipeline.py:51–55`)

- **Specific exception handling:** Catch narrow exception classes, log with context
  ```python
  try:
      score, pvalue, _ = coint(a, b)
  except Exception as e:
      log.warning(f"Cointegração falhou para {sym_a}/{sym_b}: {e}")
      continue
  ```
  (See `src/data_pipeline.py:224–228`)

- **Fallback strategies:** If operation fails (HMM convergence, MT5 connection), fall back to deterministic alternative
  ```python
  if _HAS_HMM:
      try:
          # attempt HMM fit
      except Exception as e:
          log.warning(f"HMM falhou ({e}), a usar fallback de volatilidade.")
  # fallback: percentile-based regime labeling
  ```
  (See `src/data_pipeline.py:268–284`)

- **RuntimeError for critical failures:** Unrecoverable errors that stop execution
  ```python
  if not mt5.initialize(...):
      raise RuntimeError(f"Falha ao inicializar MT5: {mt5.last_error()}")
  ```
  (See `src/data_pipeline.py:97–98`)

## Logging

**Framework:** Python `logging` module (standard library)

**Setup:**
- Module-level logger: `log = logging.getLogger("data_pipeline")`
- Configured at module entry via `logging.basicConfig()` (in `data_pipeline.py`)
- Format: `"%(asctime)s | %(levelname)s | %(message)s"`

**Patterns:**
- **Info level:** Processing progress, pipeline milestones
  ```python
  log.info(f"Carregando {sym} ({mode})...")
  log.info(f"{sym}: {len(feat)} barras processadas, regime atual = ...")
  log.info(f"Pipeline concluído. {n_coint} par(es) cointegrado(s) encontrados")
  ```
  (See `src/data_pipeline.py:171, 303, 314`)

- **Warning level:** Non-fatal errors, fallbacks, degradation
  ```python
  log.warning(f"Cointegração falhou para {sym_a}/{sym_b}: {e}")
  log.warning("HMM convergiu para estado degenerado, a usar fallback...")
  ```
  (See `src/data_pipeline.py:227, 276`)

- **Print in lab:** Strategy generator uses `log` parameter (default `print`) for test results
  ```python
  log(f"Geração {gen}: {passed_count}/{len(gen_results)} estratégia(s) aprovada(s).")
  ```
  (See `src/strategy_generator.py:106`)

## Comments

**When to Comment:**
- **Mathematical or domain-specific logic:** Explain the "why" of formulas or statistical decisions
  ```python
  # ruído idiossincrático ESTACIONÁRIO (não acumulado) -> preserva cointegração
  # drift de tendência ligado ao regime PARTILHADO, escalado por beta para
  # não introduzir uma componente I(1) extra e independente por símbolo
  ```
  (See `src/data_pipeline.py:130–135`)

- **Non-obvious data transformations:** Explain index alignment, lookahead prevention
  ```python
  # Alinhamento POSICIONAL (não por label) entre as duas séries — evita
  # que diferenças de timestamp entre símbolos causem NaNs silenciosos por
  # desalinhamento de índice do pandas.
  ```
  (See `src/backtest_engine.py:74–79`)

- **Boundary conditions:** Explain thresholds or iteration limits
  ```python
  # fallback sem hmmlearn ou se o HMM não convergiu de forma estável
  ```
  (See `src/data_pipeline.py:280`)

- **Section dividers:** Separate logical blocks with full-width comment sections
  ```python
  # --------------------------------------------------------------------------
  # Feature engineering (substitui indicadores fixos por estatísticas adaptativas)
  # --------------------------------------------------------------------------
  ```
  (See `src/data_pipeline.py:183–185`)

**Avoid:**
- Commenting obvious code: `x = 1  # set x to 1` (not used)
- TODOs without context (none present)

**Docstrings (Module & Function):**
- **Module docstrings:** Comprehensive (20+ lines), explain purpose, modes of operation, usage examples
  ```python
  """
  data_pipeline.py
  =================
  Camada 0 do sistema: ingestão de dados + deteção automática de relações...
  
  Funciona em dois modos:
    - MT5  : liga-se ao terminal MetaTrader5...
    - SYNTH: gera dados sintéticos correlacionados...
  
  Uso:
      python data_pipeline.py --mode synth
  """
  ```
  (See `src/data_pipeline.py:1–34`)

- **Function docstrings:** Explain parameters, behavior, assumptions (no formal parameters list)
  ```python
  def fetch_synthetic(...) -> pd.DataFrame:
      """Gera uma série sintética a partir de um fator de mercado PARTILHADO
      (mesmo array para todos os símbolos) mais ruído idiossincrático
      estacionário. Isto garante, por construção, que pares com beta != 0
      ficam cointegrados entre si...
      O índice de tempo (`idx`) também é PARTILHADO entre símbolos -- gerar
      um `pd.date_range(...)` separadamente por símbolo introduz desalinhamento...
      """
  ```
  (See `src/data_pipeline.py:113–127`)

- **Docstrings in Portuguese:** Full codebase uses Portuguese for variable names, comments, and docstrings (domain language matches finance/trading literature in Portuguese)

## Function Design

**Size:** 
- Functions are concise (10–50 lines typically), with clear single responsibility
- Longer functions (50–100 lines) include subsection comments to break logic
  - Example: `run_hedge_backtest()` (62 lines) breaks into beta/zscore setup, trade simulation loop, stats computation

**Parameters:**
- Functions accept config objects (`PipelineConfig`) rather than many individual params
- Params typed explicitly: `def func(x: str, y: int, z: float | None = None):`
- Default arguments used for optional/fallback values: `params.get("recalc_every", 50)`

**Return Values:**
- Explicit return types: `-> dict`, `-> pd.DataFrame`, `-> tuple[bool, list[str]]`
- Return structured data (dicts, dataclasses, pandas objects) for composability
- Multiple return values as tuple only when semantically related: `(zscore, std) = compute_zscore(...)`
- None returned for side-effect-only functions (file I/O, plotting)

**Composition Over Nesting:**
- Small helper functions composed into larger workflows
- Example: `run_pipeline()` calls `load_all_symbols()`, `engineer_features()`, `detect_regime()`, `scan_hedge_candidates()` in sequence

## Module Design

**Exports:**
- Modules define public functions (no `__all__` used)
- Import directly: `from backtest_engine import run_hedge_backtest, validate_strategy`
- Private/internal functions not prefixed with `_` (convention not enforced)

**Dataclasses:**
- Configuration objects bundled with defaults: `PipelineConfig` in `data_pipeline.py`
- Used for clarity, not all modules use them (e.g., `backtest_engine.py` uses plain dicts for params)

**Barrel Files:**
- No barrel/index files (`__init__.py` not present in `src/`)
- Direct imports from module names: `from strategy_registry import list_strategies`

**Circular Imports:**
- Avoided by design: linear dependency flow (pipeline → features → hedge → backtest → registry)
- `strategy_generator.py` imports from both `backtest_engine.py` and `strategy_registry.py` (no cycle)

## Numeric Precision

**Rounding:**
- Financial metrics rounded to 3–4 decimal places before storage
- Example: `"correlation": round(corr, 3)`, `"coint_pvalue": round(pvalue, 4)`
- Stored in dictionaries to maintain precision for statistical analysis

**Array Operations:**
- NumPy operations on raw arrays: `np.linalg.lstsq()`, `np.maximum.accumulate()`
- Results converted to Python floats for serialization: `float(pnl_r)`, `round(float(pnl_r), 4)`

## Pandas Usage

**Data Alignment:**
- Explicit positional reset when mixing series from different symbols
  ```python
  n_common = min(len(price_a), len(price_b))
  price_a = price_a.iloc[-n_common:].reset_index(drop=True)
  price_b = price_b.iloc[-n_common:].reset_index(drop=True)
  ```
  (See `src/backtest_engine.py:80–82`) — prevents silent NaN introduction from timestamp misalignment

- Rolling windows with `rolling()` method (causal by definition)
- Forward-fill and back-fill for sparse data: `.ffill().bfill()`

**DataFrame Construction:**
- Explicit column specification: `pd.DataFrame({"col1": vals1, "col2": vals2}, index=idx)`
- Not relying on positional alignment (index provided explicitly)

## Naming Across Layers

All modules follow consistent dictionary key naming for parameters and results:
- `entry_threshold`, `exit_threshold`, `min_correlation` (backtest params)
- `pair_a`, `pair_b` (symbol pairs)
- `pnl_r`, `profit_factor`, `win_rate` (metrics)
- Underscore-separated compound names, no camelCase

## Environment-Specific Behavior

**Mode flags:** 
- `mode: str` parameter defaults to "synth" (no network dependencies)
- "mt5" mode only available on Windows with terminal running

**Optional dependencies:**
- `MetaTrader5` imported locally in function (not module-level) — allows import on non-Windows systems
- `hmmlearn` imported with try-except; system degrades gracefully if not installed

---

*Convention analysis: 2026-06-30*
