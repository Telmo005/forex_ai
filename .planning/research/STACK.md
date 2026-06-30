# Stack Research

**Domain:** Production/execution layers for an algorithmic forex scalping system (deterministic risk engine, statistical hedge engine, baseline ML regime/signal model, automated strategy validation, MQL5 Expert Advisor) — built on top of an already-working Python research stack (camadas 0 and 0.5).
**Researched:** 2026-06-30
**Confidence:** MEDIUM (version facts cross-checked across multiple sources; some niche MQL5/IPC library choices are LOW confidence due to thin, forum-grade sourcing — flagged explicitly below)

## Scope note

This research does **not** revisit camada 0 (data pipeline) or camada 0.5 (strategy lab + dashboard) — those are built, tested, and working with pandas/numpy/statsmodels/hmmlearn/scikit-learn/lightgbm/streamlit/plotly/pyarrow, per `.planning/codebase/STACK.md`. It focuses on what's needed for the remaining roadmap items: **camada 1 (ML model)**, **camada 2 (hedge engine, production)**, **camada 3 (risk engine, Python + MQL5)**, **camada 4 (Expert Advisor)**, plus the cross-cutting concerns of walk-forward backtesting and fast automated strategy validation.

## Recommended Stack

### Core Technologies (new additions for this milestone)

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| LightGBM | 4.6.0 (pin `>=4.6,<5`) | Gradient boosting model for camada 1 (regime-conditioned directional signal) | Already chosen in `docs/ml_model_spec.md` and listed in `requirements.txt`. Confirmed current, Production/Stable, MIT-licensed, actively released (Feb 2025). Handles tabular features (the project's `log_ret`, `vol_fast`, `vol_ratio`, `zscore_price`, regime, etc.) natively, trains fast on modest hardware, and — critically for a personal trading system — is far more auditable than a neural net: feature importances and SHAP values are directly interpretable, which matters when you need to explain why the model fired before risking capital. |
| MetaTrader5 (pip package) | 5.0.5735 (pin to the build matching your installed terminal — see Pitfalls) | Python↔MT5 terminal bridge for live/historical data and (later) order placement from Python during testing | Already used in camada 0 for data ingestion. Confirm the pip package version against your installed MT5 terminal build before relying on it for the risk engine's pre-trade checks — MetaQuotes ties the Python package release to terminal build compatibility, and mismatches cause silent connection failures, not loud errors. |
| Optuna | 4.9.0 | Parameter search for the strategy lab, replacing/augmenting the current random+mutation generator in `strategy_generator.py` | Project's own `docs/strategy_lab_spec.md` flags "random + simple mutation, not a more sophisticated technique" as a known limitation. Optuna's TPE sampler (default) and GPSampler (Bayesian, multi-objective since v4.4) converge to good parameter regions far faster than random search, with a built-in dashboard for visualizing which parameters matter. Define the objective as a weighted combination of the dashboard's existing gate metrics (profit factor, Sharpe, drawdown, trade count) so Optuna optimizes toward the same definition of "good" the human gate already uses — do not let it discover a different definition of success. |
| scikit-learn `TimeSeriesSplit` (already a dependency) | bundled with scikit-learn (current stable 1.9.0) | Walk-forward window generation for both the ML model (camada 1) and the out-of-sample backtest gate | No new dependency needed — `sklearn.model_selection.TimeSeriesSplit` (with `gap` parameter to avoid leakage at window boundaries) is the standard, well-tested primitive for generating sequential train/test splits. Use it to drive both the walk-forward loop in `ml_model.py` and a new `walk_forward_validate()` entry point in `backtest_engine.py` — one mechanism, two consumers, avoids drift between "how the ML model is validated" and "how the hedge strategy is validated." |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pyzmq | current 4.x (pin `>=26,<27`) | Python side of a socket-based Python↔MQL5 IPC channel, if/when file-based polling proves too slow for scalping latency | Only adopt once the shared-file approach (already recommended in `docs/risk_engine_mql5_spec.md`) is validated end-to-end and measured to be too slow. Don't pre-optimize: file polling is simpler to debug and sufficient for M5-signal/M1-execution-style scalping where the decision cadence is seconds, not milliseconds. |
| `coke5151/mql5-zmq` or `ding9736/MQL5-ZeroMQ` (MQL5-side library, not a pip package) | latest `main` (no tagged stable releases found) | MQL5-side ZeroMQ binding, paired with pyzmq, if socket IPC is adopted | LOW confidence — see Pitfalls. The original `dingmaotu/mql-zmq` (most-cited reference) has known compile errors against modern MQL5 builds; community forks exist specifically to fix this. Vet whichever fork you pick by compiling and running its example EA in MetaEditor before integrating — do not trust GitHub stars alone, this corner of the MQL5 ecosystem is thinly maintained. |
| Optuna Dashboard | bundled with `optuna[dashboard]` extras | Visualizing which hedge/strategy parameters matter most across search history | Optional, but cheap to add given the project already values transparency (the Streamlit dashboard exists for the same reason) — helps decide which parameters to keep tunable vs. hardcode after the lab has run enough generations. |
| APScheduler | 3.11.x | Scheduling periodic retraining (camada 1) and periodic cointegration re-evaluation (camada 2, per `docs/hedge_engine_spec.md`'s "reavaliar periodicamente" requirement) | Use for any in-process recurring job (weekly retrain, re-check cointegration every N bars) once the Python side runs as a long-lived process rather than one-shot CLI scripts. Actively maintained, simple cron-style triggers, avoids hand-rolling a scheduler loop. |
| `pomegranate` | current (optional, not adopted now) | Backup HMM implementation if `hmmlearn` becomes a blocker | Not recommended to adopt now — see Pitfalls/What NOT to Use. Keep as a documented fallback option only. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| MetaEditor (bundled with MT5 terminal) | Compile and debug `.mq5`/`.mqh` files | No separate install; opens from the MT5 terminal. Use the Strategy Tester in MetaEditor for EA-level dry runs against historical ticks before any demo account connection. |
| MT5 Strategy Tester (demo account, hedging mode) | EA-level integration testing before any live signal feed | Confirm `ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` on the demo account before writing a single line of `ScalpingEA.mq5` — per `docs/risk_engine_mql5_spec.md`, this is a hard prerequisite, not a nice-to-have. |
| pytest (add to dev dependencies) | Unit tests for `risk_engine.py`'s deterministic rules | Not currently in `requirements.txt`. The risk engine is explicitly the camada described as "deve continuar a funcionar mesmo se as camadas anteriores falharem" — this is exactly the kind of deterministic, rule-based code that benefits from property-based / adversarial unit tests (oversized orders, duplicate orders, invalid symbols — the adversarial cases already listed in the risk engine spec's pre-production checklist). |

## Installation

```bash
# Core additions for this milestone (Python side)
pip install lightgbm==4.6.0
pip install optuna==4.9.0
# scikit-learn TimeSeriesSplit already available via existing scikit-learn dependency

# Only if/when socket IPC replaces file-based IPC (do not install speculatively)
pip install pyzmq

# Scheduling (once Python side becomes a long-running process)
pip install apscheduler

# Dev/test dependencies
pip install pytest

# MQL5 side: no pip install — MQL5-ZeroMQ binding (if adopted) is added as
# .mqh include files copied into MQL5/Include/, compiled by MetaEditor.
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| LightGBM | XGBoost | Both are valid gradient boosting choices and the project's own spec already names both. XGBoost has marginally better documentation for monotonic constraints, which can be useful if you later want to enforce "higher z-score must not decrease confidence" type constraints on the model. Switch only if you hit a specific LightGBM limitation — don't run both in parallel, it doubles validation surface for no benefit at this stage. |
| File-based IPC (shared JSON in `MQL5/Files/`) | pyzmq + MQL5-ZeroMQ socket | Switch to sockets only after measuring that file-polling latency (typically hundreds of ms) actually breaks the strategy's edge in backtest-vs-live comparison. Given the architecture already separates signal generation (seconds-scale) from execution (the EA's own local risk checks and stop management), most scalping setups here are not HFT-latency-sensitive — confirm this empirically before adding socket complexity. |
| scikit-learn `TimeSeriesSplit` for walk-forward | Custom rolling-window loop (hand-written) | The project's existing `strategy_lab_spec.md` already flags "no explicit train/validation split within the backtest itself" as a known gap — `TimeSeriesSplit` with `gap` is the fastest way to close that gap without inventing custom logic that needs its own testing. Only hand-roll if you need purge/embargo logic beyond a simple gap (common in finance ML to avoid label leakage at window boundaries from triple-barrier labels that look into the future) — if so, the purge/embargo logic itself is the kind of thing worth a dedicated small module rather than a full new dependency. |
| Optuna for strategy parameter search | Keep pure random + mutation (status quo) | Acceptable to defer if the current random+mutation approach is finding enough approved strategies fast enough — the project spec frames this as "vale revisitar se a procura demorar demasiado", i.e., a should-fix, not a must-fix. Don't add Optuna until the lab's current approach is demonstrably the bottleneck. |
| Implement triple-barrier labeling directly in `src/ml_model.py` (~50 lines) | `mlfinlab` (Hudson & Thames) | `mlfinlab`'s actively-maintained reference implementation has moved toward a commercial license for newer releases; for a single well-understood algorithm already fully specified in `docs/ml_model_spec.md`, adding a heavyweight (and now partially closed) dependency is not justified. Use a small open PyPI package (`triple-barrier`) only if you want a pre-tested implementation and accept its smaller community/review surface. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|--------------|
| Deep learning (LSTM/Transformer) as the camada 1 baseline | Explicitly out of scope per `PROJECT.md` and `docs/ml_model_spec.md` — needs more data than a single-trader system typically has, harder to validate honestly with walk-forward, and the project's own stated thesis is that validation quality matters more than model architecture on noisy forex data. Confirmed still the right call: nothing in current 2026 ecosystem changes this trade-off for tabular features at this data scale. | LightGBM gradient boosting (already chosen) |
| Treating `hmmlearn` as a long-term dependency without a contingency plan | Confirmed LOW-activity: latest release 0.3.3 is from October 2024, with no releases in the 12+ months since, and third-party health trackers (Snyk) classify it as inactive/limited-maintenance. It still works and has a stable scikit-learn-style API, so there's no urgency to migrate — but do not add new hard dependencies on hmmlearn-specific internals, and keep the project's existing volatility-percentile fallback (already implemented per `ROADMAP.md`) as the permanent safety net, not a temporary shim. | Continue using `hmmlearn` + existing fallback; `pomegranate` is the documented escape hatch if a blocking bug ever appears, not a proactive migration target |
| Pure/full Kelly criterion for position sizing | Mathematically optimal for long-run growth but produces drawdowns most traders (including disciplined ones) cannot tolerate psychologically, and the inputs (win-rate, payoff ratio) are estimates with real error bars, not exact values — using the full formula compounds estimation error into oversized bets. Already correctly identified as a risk in `docs/risk_engine_mql5_spec.md`. | Fractional Kelly (0.25x–0.5x), implemented as a plain function in `risk_engine.py` — no library needed, this is a one-line formula; do not add a dependency for it. |
| `dingmaotu/mql-zmq` (the original, most-cited MQL5 ZeroMQ binding) used as-is | Confirmed to have compile errors against modern MQL5 builds based on multiple community forks created specifically to fix this — using the unforked original risks hours lost to build errors unrelated to your own code. | If pursuing socket IPC, use one of the actively-fixed forks (`coke5151/mql5-zmq` or `ding9736/MQL5-ZeroMQ`), and compile-test the fork's own example EA first before integrating it into `ScalpingEA.mq5`. Re-verify this pick at implementation time — this corner of the ecosystem has low source quality (forum posts, small repos), confidence here is LOW. |
| pandas 3.0 (just released, Jan 2026) | A major version bump from the 2.x series the project's existing pipeline (`data_pipeline.py`, `backtest_engine.py`) was built and tested against. Major pandas versions have historically broken implicit dtype/index behaviors that time-series code relies on (exactly the kind of code in this project — recall the index-alignment bug already hit and fixed in synthetic data generation per `ROADMAP.md`). | Stay on pandas 2.3.x (latest 2.3.3, Sep 2025) for this milestone. Revisit pandas 3.0 migration as its own dedicated, tested phase later — not bundled into risk/hedge/ML engine work where a silent index/dtype regression would be especially dangerous. |
| Backtrader (full framework adoption) for the new walk-forward/automated-validation work | Development has stalled since ~2021 (open issues/PRs unmerged), and the project already has a working, custom, no-lookahead `backtest_engine.py` purpose-built for the hedge logic's specific PnL-in-R semantics. Adopting a general framework now means rewriting working, tested code for marginal benefit. | Extend the existing `backtest_engine.py` with a `walk_forward_validate()` function using `TimeSeriesSplit`, rather than migrating to a third-party backtesting framework. |

## Stack Patterns by Variant

**If file-based IPC latency proves insufficient for the target scalping timeframe (measured, not assumed):**
- Use pyzmq (Python) + a maintained MQL5-ZeroMQ fork (MQL5 side)
- Because in-process ZeroMQ round-trips are measured in microseconds vs. hundreds of milliseconds for file polling — but only pay this complexity cost once you've proven you need it

**If the strategy lab's random+mutation search is taking too many generations to find approved strategies:**
- Add Optuna with a custom objective combining the dashboard's existing gate metrics
- Because TPE/Bayesian search converges faster than random search as the parameter space grows (more pairs, more hedge parameters)

**If the M1 execution timeframe proves too noisy for the ML signal (per the open question already in `ARCHITECTURE.md`):**
- Generate the camada 1 signal on M5 features, execute on M1
- Because the project's own architecture doc already flags this as the likely resolution — confirm via walk-forward accuracy comparison before committing

**Once the Python side becomes a long-running process (rather than one-shot CLI invocations):**
- Add APScheduler for periodic retraining and periodic cointegration re-checks
- Because both `docs/ml_model_spec.md` (weekly retrain cadence) and `docs/hedge_engine_spec.md` (periodic cointegration re-evaluation) call for recurring jobs, not one-off runs

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| LightGBM 4.6.0 | numpy 2.x, pandas 2.3.x | No known conflicts; LightGBM 4.x wheels support numpy 2 (so does hmmlearn 0.3.3+, confirmed above) — keep numpy on the 2.x line, not pandas 3.0, for this milestone. |
| Optuna 4.9.0 | Python 3.9+ | Compatible with project's Python 3.10+ requirement. |
| pandas 2.3.x | statsmodels 0.14.6, pyarrow (current) | statsmodels 0.14.6 (Dec 2025) requires pandas >= 1.4 — comfortably satisfied by 2.3.x; do not jump to pandas 3.0 without re-validating statsmodels and the cointegration code path. |
| MetaTrader5 pip package 5.0.5735 | MT5 terminal build | The pip package version must correspond to a compatible installed terminal build — verify against the terminal's own "Help → About" build number before relying on it in the risk engine's live checks; mismatches fail silently rather than raising a clear version error. |

## Sources

- https://pypi.org/project/lightgbm/ — version and license confirmation (MEDIUM confidence, cross-checked with GitHub releases and readthedocs)
- https://pypi.org/project/metatrader5/ , https://www.mql5.com/en/docs/python_metatrader5 — package version and hedging/netting support (MEDIUM)
- https://github.com/dingmaotu/mql-zmq , https://github.com/coke5151/mql5-zmq , https://github.com/ding9736/MQL5-ZeroMQ — MQL5 ZeroMQ binding landscape and maintenance status (LOW — thin sourcing, forum/GitHub README level, re-verify at implementation time)
- https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html — current API surface (MEDIUM)
- https://github.com/hmmlearn/hmmlearn , https://snyk.io/advisor/python/hmmlearn , https://pypi.org/project/hmmlearn/ — maintenance status confirmation across 3 independent sources (MEDIUM, cross-checked)
- https://optuna.org/ , https://optuna.readthedocs.io/ , https://github.com/optuna/optuna — version and Bayesian/TPE sampler capabilities (MEDIUM)
- https://github.com/hudson-and-thames/mlfinlab , https://pypi.org/project/triple-barrier/ — triple-barrier labeling library landscape and licensing concern (LOW — single-pass search, recommend re-verifying license terms directly before any adoption decision)
- https://pandas.pydata.org/docs/whatsnew/v3.0.0.html , https://pypi.org/project/pandas/2.3.2/ — pandas 2.x vs 3.0 version timeline (MEDIUM)
- https://pypi.org/project/APScheduler/ , https://apscheduler.readthedocs.io/en/3.x/userguide.html — scheduler version and trigger types (MEDIUM)
- Project's own `docs/ml_model_spec.md`, `docs/hedge_engine_spec.md`, `docs/risk_engine_mql5_spec.md`, `docs/strategy_lab_spec.md`, `ARCHITECTURE.md`, `ROADMAP.md` — existing decisions and constraints this research must respect, not re-litigate (HIGH confidence — primary source, already-validated project decisions)

---
*Stack research for: production/execution layers of an algorithmic forex scalping system (risk engine, hedge engine, ML regime model, automated validation, MQL5 EA)*
*Researched: 2026-06-30*
