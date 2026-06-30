# Codebase Concerns

**Analysis Date:** 2026-06-30

## Critical Missing Implementations

**Camada 1 — ML Model (Core Strategy Signal):**
- Issue: `src/ml_model.py` does not exist; entire machine learning layer is unimplemented
- Files: Missing `src/ml_model.py`
- Impact: No directional signal generation for trade decisions; system currently runs on statistical hedge logic alone, without adaptive market regime learning
- Fix approach: Implement gradient boosting baseline (LightGBM/XGBoost) with triple-barrier labeling and walk-forward validation per `docs/ml_model_spec.md`
- Blocker: Cannot advance to production (camada 2+) without this

**Camada 2 — Production Hedge Engine:**
- Issue: `src/hedge_engine.py` does not exist; live execution logic for validated strategies is missing
- Files: Missing `src/hedge_engine.py`
- Impact: Cannot execute real trades; must manually promote strategy parameters from dashboard to live system (no automation)
- Fix approach: Implement stateful hedge engine consuming `hedge_candidates.csv` and `features_*.parquet` with real-time z-score monitoring
- Blocker: No production deployment possible

**Camada 3 — Python-Side Risk Engine:**
- Issue: `src/risk_engine.py` does not exist; deterministic risk validation layer not implemented
- Files: Missing `src/risk_engine.py`
- Impact: No Kelly criterion sizing, no drawdown limits, no multi-position exposure tracking — orders cannot be safely dimensioned before reaching MQL5
- Fix approach: Implement Kelly-fractioned sizing, exposure tracking, drawdown circuit breaker per `docs/risk_engine_mql5_spec.md`
- Blocker: Cannot move from demo to live trading without risk guardrails

**Camada 4 — MQL5 Expert Advisor:**
- Issue: `mql5/ScalpingEA.mq5` and `mql5/RiskGuard.mqh` do not exist
- Files: Missing `mql5/ScalpingEA.mq5`, Missing `mql5/RiskGuard.mqh`
- Impact: No on-exchange execution; system has no interface to MetaTrader5 terminal or corretora
- Fix approach: Implement EA with redundant risk checks, hedging-mode validation, heartbeat/timeout logic per `docs/risk_engine_mql5_spec.md`
- Blocker: Cannot execute any live trades

## Data Quality & Validation Gaps

**No Walk-Forward Out-of-Sample Backtest:**
- Issue: Strategy validation in `src/strategy_generator.py` uses the same historical data for generation and selection; no separate test period
- Files: `src/strategy_generator.py` (lines 81-123), `docs/hedge_engine_spec.md` (line 88-92 recommendations)
- Impact: All strategies may suffer from selection bias — a strategy performing well in generated data may not generalize to truly unseen future data
- Trigger: ROADMAP.md line 18 lists "Backtest walk-forward out-of-sample" as not yet done
- Fix approach: Split historical data into [train/generate, validate, test]. Run `strategy_generator.py` on train/generate only, then re-backtest top strategies on held-out test period
- Priority: High — critical before any live deployment

**Real Data Linkage Untested:**
- Issue: `data_pipeline.py --mode mt5` has never been validated against a real MT5 terminal account
- Files: `src/data_pipeline.py` (lines 89-110)
- Current state: ROADMAP.md line 8 marks this as "⏳ Por testar na corretora real"
- Impact: Symbol names, timeframes, data alignment may differ on live account; system could fail silently (e.g., pulling wrong data or no data at all)
- Fix approach: Test `data_pipeline.py --mode mt5` on demo account first, confirm symbol list and data quality before using in strategy generation
- Priority: High — must be done before step 2 of ROADMAP

**MT5 Initialization Brittle:**
- Issue: `data_pipeline.py` assumes MetaTrader5 library is installed and terminal is open; no graceful degradation
- Files: `src/data_pipeline.py` (lines 51-55, 89-102)
- Impact: `fetch_mt5()` will crash if MT5 not available, even in `--mode synth` — coupling between optional dependency and main pipeline logic
- Workaround: Must install MetaTrader5 even if running `--mode synth`
- Fix approach: Move MT5 import into `fetch_mt5()` only, add try-except with clear error message
- Priority: Medium

## Test Coverage Gaps

**Backtest Engine Lacks Edge Case Testing:**
- Issue: `src/backtest_engine.py` has no unit tests; complex numerical logic (z-score, rolling beta, equity curves) never validated in isolation
- Files: `src/backtest_engine.py`
- Current state: No test files found in repository; strategy_generator.py tests end-to-end but not individual functions
- Impact: Bugs in `compute_rolling_beta()`, `compute_zscore()`, or `compute_stats()` could silently produce incorrect backtests
- Fix approach: Create `tests/test_backtest_engine.py` with fixtures: known price series with predictable spreads, verify beta calculations, z-score outputs, and edge cases (all-winning trades, all-losing trades, no trades)
- Priority: High

**Strategy Registry Has No Validation:**
- Issue: `src/strategy_registry.py` stores arbitrary dicts without schema validation; malformed records could break dashboard
- Files: `src/strategy_registry.py` (lines 54-75)
- Impact: If `strategy_generator.py` writes a record with missing/wrong field types, dashboard will crash at read time
- Fix approach: Add Pydantic model or dict schema validation before inserting into DB; fail fast with clear error message
- Priority: Medium

**Dashboard UI Not Tested:**
- Issue: `dashboard.py` is Streamlit-based; no automated tests for filtering, metric calculations, or plotting logic
- Files: `dashboard.py`
- Impact: UI bugs (wrong metric displayed, broken filters) could lead to incorrect strategy validation decisions
- Current state: Manual testing only — relies on visual inspection by user
- Fix approach: Consider Streamlit testing framework (e.g., `streamlit.testing.v1`) for core filters and metric displays
- Priority: Low (visual inspection acceptable for prototype)

## Architecture & Design Concerns

**Missing Specification: Python ↔ MQL5 Communication Protocol:**
- Issue: `docs/risk_engine_mql5_spec.md` section "Comunicação Python <-> MQL5" (lines 64-80) leaves this critical decision open
- Files: `docs/risk_engine_mql5_spec.md`
- Impact: Cannot implement camada 2 or camada 4 without knowing exact protocol (file-based vs socket)
- Recommendation from ROADMAP: Start with shared file (simpler) for validation, migrate to socket/ZeroMQ only if latency insufficient
- Fix approach: Prototype with shared JSON file in `MQL5/Files/` folder, use polling with 100-200ms cadence for M5 scalping
- Priority: High (blocks camada 3/4 implementation)

**HMM Regime Detection Unreliable:**
- Issue: `data_pipeline.py` (lines 268-284) uses fallback heuristic (percentile-based volatility bucketing) when GaussianHMM fails to converge
- Files: `src/data_pipeline.py` (lines 268-284), `ROADMAP.md` (lines 25-27)
- Impact: Regime labels inconsistent — in some runs HMM, in others simple percentile bucketing; strategies trained on one regime type may not work under the other
- Current state: Documented as intentional (line 26-27 ROADMAP: "HMM com fallback... evita que o pipeline pare")
- Fragility: If HMM convergence becomes unreliable, entire regime-conditioned strategy validation becomes suspect
- Fix approach: Log which regime detector was used per run, monitor regime switching frequency (if too high/low, suspect configuration); consider alternative: always use stable heuristic, use HMM only as additive feature
- Priority: Medium

**Strategy Generation Mutation Logic Not Validated:**
- Issue: `src/strategy_generator.py` (lines 51-58) mutates parameters with random perturbations; no validation that mutations stay within valid ranges
- Files: `src/strategy_generator.py` (lines 51-58)
- Impact: Mutation can push parameters to edge cases (e.g., `entry_threshold` = 1.3, almost at minimum)
- Current state: `max(lo, min(hi, ...))` clamps, so range respected, but distribution of mutants may be biased toward edges
- Fix approach: Use Gaussian mutation centered on original, then clamp; verify mutation produces diverse parameter space by analyzing generated candidates
- Priority: Low (currently working, optimization only)

## Security & Safety Concerns

**No Kill-Switch Implemented:**
- Issue: `docs/risk_engine_mql5_spec.md` (line 30-32) specifies kill-switch requirement; not implemented
- Files: Missing file-based kill-switch mechanism
- Impact: Cannot instantly halt all trading if critical bug detected; only option is to restart Python process and/or close EA manually in MT5
- Fix approach: Create `KILL_SWITCH.flag` file; `risk_engine.py` and `ScalpingEA.mq5` both check for its existence each cycle
- Priority: Critical (non-negotiable before live trading per `docs/risk_engine_mql5_spec.md` line 82)

**No Alerting System:**
- Issue: `docs/risk_engine_mql5_spec.md` (lines 92-94) requires alerts (Telegram/email) for drawdown/connectivity/EA failures
- Files: No alerting code found
- Impact: Cannot detect abnormal system behavior in real-time; operator may miss critical failures for hours
- Fix approach: Add Telegram/email integration to Python risk engine; log to file at minimum
- Priority: High (required before demo account testing per line 89-94)

**Hardcoded Credentials in Docs:**
- Issue: `data_pipeline.py` (line 33) example shows credentials in command-line args; user might copy this as-is
- Files: `src/data_pipeline.py` (line 33)
- Impact: Credentials could end up in bash history or git history by mistake
- Fix approach: Document use of environment variables (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) instead of args; update example
- Priority: Medium (educational concern, but important for safety)

## Performance & Scaling Concerns

**Hedge Candidate Scan is O(n²):**
- Issue: `src/data_pipeline.py` (lines 204-251) scans all pairs, `combinations()` produces O(n²) comparisons
- Files: `src/data_pipeline.py` (lines 216-228)
- Current scale: 7 symbols → 21 pairs tested — acceptable
- Scaling: 50 symbols → 1225 pairs; 100 symbols → 4950 pairs — cointegration test becomes bottleneck
- Fix approach: Already documented as acceptable for typical forex setups (few hundred pairs max); no action needed if staying <50 symbols
- Priority: Low (informational; fix only if symbol count grows 10x)

**No Caching of Cointegration Tests:**
- Issue: Each run of `strategy_generator.py` calls `strategy_generator.py` which calls `run_hedge_backtest()` which recomputes rolling beta and z-scores from scratch
- Files: `src/backtest_engine.py` (lines 93-95), `src/strategy_generator.py` (lines 80-123)
- Impact: For N strategy candidates × M bars, recomputing rolling beta is wasteful; 10 generations × 10 candidates/generation × 5 pairs × 20K bars = millions of redundant OLS fits
- Fix approach: Cache `features_*.parquet` in memory during `run_strategy_lab()`, only compute rolling beta once per pair, reuse across parameter variations
- Priority: Medium (optimization; current runtime acceptable for small candidate pools)

## Data Integrity Concerns

**Parquet Files Not Versioned:**
- Issue: `src/data_pipeline.py` (line 302) outputs `features_{SYM}.parquet` and `hedge_candidates.csv` without version or timestamp
- Files: `src/data_pipeline.py` (line 302)
- Impact: If pipeline runs twice with different settings, output overwrites silently; cannot trace which strategy was tested against which data
- Fix approach: Add timestamp to output filenames or create versioned output directories (e.g., `output/2026-06-30_14h30/`)
- Priority: Medium

**No Checksum/Validation of Loaded Data:**
- Issue: `src/strategy_generator.py` (line 143) reads `hedge_candidates.csv` without verifying it matches data used in pipeline
- Files: `src/strategy_generator.py` (lines 143-154)
- Impact: If user runs pipeline once, then modifies hedge_candidates.csv manually, strategy_generator will silently use wrong data
- Fix approach: Store hash of hedge_candidates.csv in strategy_lab.db; warn if hash changes between runs
- Priority: Low (user error, not codebase bug; mitigated by clear documentation)

## Dependencies at Risk

**HMMlearn Optional Dependency:**
- Issue: `hmmlearn` imported optionally (line 52-55); if not installed, falls back to percentile bucketing
- Files: `src/data_pipeline.py` (lines 52-55)
- Status: Flagged in ROADMAP as intentional fallback mechanism
- Risk: Low, already handled with clear warning logs
- Action: None needed; design is correct

**LightGBM Not Used Yet:**
- Issue: `requirements.txt` includes `lightgbm` but no code imports it
- Files: `requirements.txt` (line 7)
- Impact: Installed but not used; may become stale if version not pinned
- Fix approach: When `src/ml_model.py` is implemented, will be consumed; for now, acceptable to leave for future use
- Priority: Low

## Incomplete Specifications

**ML Model Output Format Undefined:**
- Issue: `docs/ml_model_spec.md` (lines 57-67) shows example output dict but no specification for how/when it is called from `hedge_engine.py`
- Files: `docs/ml_model_spec.md`, `docs/hedge_engine_spec.md`
- Impact: When implementing camada 2, will need to guess at interface contract
- Fix approach: Add section to `hedge_engine_spec.md` detailing how to call ML model (synchronous? async? cache predictions?) and what to do if model unavailable
- Priority: Medium

**Regime-Specific Accuracy Not Defined:**
- Issue: `docs/ml_model_spec.md` (line 66) specifies `regime_specific_accuracy` field but does not define how it is computed or maintained
- Files: `docs/ml_model_spec.md`
- Impact: Implementer must infer: is this accumulated over training? over entire test set? per-regime within test set?
- Fix approach: Add specific definition: "accuracy computed per regime label on held-out test set; updated weekly during retraining"
- Priority: Low (clarification only)

## Known Non-Issues (Intentional Design Decisions)

**Synthetic Data Diverges from Real:**
- Current state: `data_pipeline.py --mode synth` generates perfectly structured data with shared market factor and known betas
- Design intent: Validated in ROADMAP line 24-40 as intentional for prototype development
- Mitigation: Process explicitly documents this as "prova de conceito" (line 49 ROADMAP); all strategies must be revalidated on real data
- Action: None; continue with synth for development, move to real MT5 data for validation per ROADMAP step 1-2

**No Transaction Costs in Strategy Lab:**
- Current state: `src/backtest_engine.py` computes PnL in R units (spread deviations) without incorporating spread, slippage, or commission
- Design intent: CLAUDE.md line 4 specifies "Custos de transação entram sempre no backtest"
- Mitigation: Documented as oversight; should be added when camada 2 is implemented (apply costs in hedge_engine.py or risk_engine.py)
- Fix approach: When implementing `hedge_engine.py`, wrap PnL calculations with realistic cost assumptions from corretora
- Priority: High (blocks real data validation per ROADMAP step 2)

**Strategy Parameters Not Live-Reloadable:**
- Current state: Once parameters are approved in dashboard and camada 2 exists, only way to change is restart EA
- Design intent: Acceptable for production MVP (see CLAUDE.md rule 7 "Nenhum parâmetro vai para hedge_engine.py de produção sem primeiro aparecer como ✅ Aprovada")
- Mitigation: Parameters are logged to database; can implement reload mechanism later if needed
- Action: Document in `docs/hedge_engine_spec.md` that parameter reload requires EA restart; flag as future enhancement

---

*Concerns audit: 2026-06-30*
