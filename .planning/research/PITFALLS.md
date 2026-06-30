# Pitfalls Research

**Domain:** Algorithmic/quant forex scalping — statistical hedge engine, ML regime detection, deterministic risk engine, MQL5 execution
**Researched:** 2026-06-30
**Confidence:** MEDIUM (web findings are LOW-confidence websearch sources, but they corroborate each other, established quant-finance literature, and the project's own `docs/*_spec.md` files, which independently reached the same conclusions — cross-checking raises practical confidence to MEDIUM)

## Critical Pitfalls

### Pitfall 1: Strategy generator overfits to its own backtest (selection bias masquerading as edge)

**What goes wrong:**
`strategy_generator.py` already runs a generate→test→reprove→mutate→retest loop. That loop is, by construction, a search over parameter space that rewards whatever combination happens to fit the historical sample best — including pure noise. With enough generations and candidates, some parameter set will look excellent purely by chance, even if the underlying "edge" doesn't exist. This is distinct from classic overfitting in ML — it's overfitting via repeated hypothesis testing (multiple comparisons), and a single profit factor / Sharpe / drawdown gate (as currently in `dashboard.py`) does not detect it, because the gate is evaluated on the same data the generator searched over.

**Why it happens:**
The lab's mutation loop and the approval gate currently both draw from the same historical window (per `CONCERNS.md`: "no separate test period"). Every iteration that "fails" and gets mutated is implicitly informing later iterations, which is a soft form of look-ahead even without explicit future data leaking in.

**How to avoid:**
- Strict three-way split: generation/mutation pool, validation (used only for the gate), and a held-out final test period the generator never sees, touched only once per candidate before production promotion.
- Track and cap the number of candidates evaluated per pair/regime combination; apply a multiple-comparisons correction conceptually (e.g., require a stronger threshold — higher Sharpe/profit-factor minimum — as the number of candidates tested grows), or at minimum log how many candidates were tried so a 1-in-20 lucky result can be recognized as such.
- Treat walk-forward (Pitfall 3) as mandatory before promotion, not optional — it is the actual overfitting detector, not the dashboard gate.
- Re-test top "approved" strategies on a second, later out-of-sample window before they ever reach `hedge_engine.py`; if performance degrades sharply between the original test and this confirmation window, reject regardless of the original metrics.

**Warning signs:**
- A strategy with unusually high Sharpe/profit-factor relative to the rest of the population (statistical outlier — likely noise, not skill).
- Parameters at first glance "too precise" (e.g., entry_threshold = 2.137 instead of a round, economically-motivated number) — a sign of optimization grinding to a local noise peak.
- Performance that holds in-sample but collapses in any later period — even by a few weeks.
- Many generations needed before a candidate passes the gate (the more search, the more selection bias).

**Phase to address:** Walk-forward revalidation phase (already on roadmap) + a small addition to the strategy lab phase: log candidate count per pair so overfitting risk is visible, not invisible.

---

### Pitfall 2: Backtest costs ignored or applied too late (scalping-specific cost blindness)

**What goes wrong:**
Per `CONCERNS.md`, `backtest_engine.py` currently computes PnL in "R" units (spread standard deviations) with **no spread, slippage, or commission**. In scalping, where the average winning trade may be only a few R or a few pips, transaction costs are not a minor drag — they can consume the entire edge. A strategy that looks profitable in R-space can be a guaranteed loser once realistic costs are applied, and the team will not find out until real or demo money is on the line if costs are bolted on only at the `hedge_engine.py` layer instead of in the backtest/lab itself.

**Why it happens:**
It's tempting to defer cost modeling to "later, in production," because costs require broker-specific data (spread schedule, commission per lot, typical slippage during news/volatility) that isn't available when the lab/backtest engine was first built. CLAUDE.md already names this as a hard rule, but `CONCERNS.md` confirms the rule isn't yet implemented.

**How to avoid:**
- Add transaction cost modeling to `backtest_engine.py` itself (not just `hedge_engine.py`), so every strategy ever proposed for approval is evaluated cost-aware, before it reaches the dashboard gate.
- Use realistic per-symbol values from the actual broker (spread observed on demo account, commission per round-turn lot, conservative slippage estimate) rather than a single global flat cost — EURUSD and exotic crosses have very different cost profiles.
- Model spread as variable, not fixed — spreads widen during low liquidity and news; a strategy that only survives at the tightest observed spread is fragile.
- Re-derive "R" units (currently spread standard deviation) to be net of costs, or report both gross and net R, so the dashboard gate can compare like-for-like with the eventual production numbers.
- Sanity-check: number of trades × round-turn cost should be subtracted explicitly and shown in the dashboard, not buried in aggregate stats — for scalping this number is often shockingly large relative to gross PnL.

**Warning signs:**
- Strategies with very high trade counts and small average win size — the classic profile most vulnerable to cost erosion.
- A large gap between profit factor calculated on raw R and a quick mental estimate of (avg win in pips) vs (typical spread in pips) for that pair.
- Dashboard "approved" strategies whose edge per trade is smaller than 2x the expected spread+commission+slippage.

**Phase to address:** Should be folded into the walk-forward / real-data revalidation phase (ROADMAP step 2) — costs must be added to `backtest_engine.py` before strategies are revalidated on real MT5 data, not after. This blocks "ROADMAP step 2: Correr o laboratório nos dados reais."

---

### Pitfall 3: Walk-forward done in name only (in-sample re-optimization with a fresh-looking label)

**What goes wrong:**
It's common for teams to implement something they call "walk-forward" that actually re-optimizes on a window, "tests" on the next window, but then feeds learnings from that test back into the next optimization round — silently reintroducing look-ahead bias. Equally common: defining the walk-forward window sizes, anchored-vs-rolling choice, and train/test gap *after* seeing how a particular configuration performs, which is itself a form of overfitting one level up (overfitting the validation methodology, not just the strategy).

**Why it happens:**
Walk-forward is conceptually simple but the implementation details — window type (anchored vs rolling), window length, gap between train and test, and what happens to candidates that fail a given fold — are where mistakes hide. Since `src/strategy_generator.py` and `backtest_engine.py` were both built and tuned against synthetic data only so far, there's a real risk the walk-forward implementation gets shaped by what makes synthetic-data results look good, rather than being decided independently first.

**How to avoid:**
- Fix the walk-forward methodology (window type, length, train/test gap, number of folds, rolling vs anchored) **before** running it on real data even once, and document the choice in `docs/` the same way Engle-Granger vs Johansen was documented as a decision with rationale.
- Apply the exact same walk-forward configuration to every candidate strategy — never tune the splits per-strategy to make a favorite candidate pass.
- Use rolling (non-anchored) windows for this project specifically: forex regime/correlation structure changes over months (rate cycles, risk-on/risk-off shifts), so an ever-growing anchored window will dilute recent regime information with stale relationships — a rolling window of fixed length is more appropriate for a system that already explicitly models regime-dependent behavior (HMM).
- Require a minimum number of trades per fold for a result to count — too few trades per fold makes per-fold metrics statistically meaningless, but is an easy way to "pass" walk-forward by accident if folds are too short for scalping frequency.
- Re-test cointegration and correlation independently *within* each walk-forward fold, not just at the start — a pair that was cointegrated in the training window may have decohered by the test window (see Pitfall 6).

**Warning signs:**
- Walk-forward "passes" on every fold with suspiciously similar metrics (real markets are not that stable; uniform performance across folds suggests leakage or synthetic-data artifacts).
- Window/gap parameters that were adjusted more than once during implementation without a documented reason.
- No record of how many folds/candidates were tried before settling on the final walk-forward configuration.

**Phase to address:** Dedicated "Backtest walk-forward out-of-sample" phase (already on ROADMAP). Should be planned and the methodology locked down *before* real MT5 data is even pulled, so the temptation to fit the methodology to early real-data results doesn't arise.

---

### Pitfall 4: Risk engine silently leans on ML inference instead of staying purely deterministic

**What goes wrong:**
The project's own constraint (CLAUDE.md rule 1, `docs/risk_engine_mql5_spec.md`) is explicit: risk must never depend solely on ML inference. The realistic failure mode isn't a deliberate violation — it's gradual coupling. Examples specific to this architecture: Kelly-fraction sizing uses "win-rate and payoff ratio histórico (vindos do backtest da camada 1/2)" — if those win-rate/payoff inputs come from a live-updating ML-influenced backtest rather than a frozen, approved baseline, the position-sizing math becomes indirectly ML-dependent even though the sizing formula itself is deterministic. Similarly, if `risk_engine.py` ever short-circuits a check when `confidence_ml` is high (e.g., "skip extra exposure check if ML confidence > 0.8"), that's exactly the silent dependency the architecture was designed to prevent — and it tends to get added later as a "smart" optimization once the ML layer exists and looks trustworthy.

**Why it happens:**
Once a working ML model exists, its outputs are tempting to use everywhere because they're available and look like better information. The 5-layer separation needs to be enforced by interface contract, not just intention — `docs/ml_model_spec.md` is flagged in `CONCERNS.md` as not yet defining how/when `hedge_engine.py` calls the ML model or what happens if it's unavailable, which is exactly the gap where silent coupling creeps in.

**How to avoid:**
- `risk_engine.py` should not import or call anything from `src/ml_model.py`, directly or transitively — enforce this with a structural check (e.g., a test that asserts no import of the ML module appears in the risk engine's dependency graph).
- Kelly-fraction inputs (win-rate, payoff ratio) must come from the frozen, ✅-Approved backtest stats stored at promotion time, not recomputed live from a model that may itself be retrained or drift.
- Define explicitly in `docs/ml_model_spec.md` / `docs/hedge_engine_spec.md` what happens when the ML model is unavailable, stale, or returns low confidence — the correct answer should be "hedge/risk layers behave exactly as if no ML signal existed" (degrade to pure statistical/deterministic logic), never "skip a risk check."
- The MQL5 EA's redundant local risk checks (already specced) are the real safety net here — keep them genuinely independent of anything Python sends, including `confidence_ml`.
- Code review checklist item for any future PR touching `risk_engine.py` or `RiskGuard.mqh`: "does this PR introduce any branch conditioned on an ML output?" If yes, it needs explicit justification and ideally rejection.

**Warning signs:**
- Any `if confidence_ml > X` conditional inside risk-approval logic.
- Kelly sizing inputs that change between runs without a corresponding new ✅-Approved entry in the strategy registry.
- Risk engine code that imports `ml_model.py` or reads ML-produced files directly rather than via the hedge layer's already-filtered proposal.

**Phase to address:** Risk engine phase (ROADMAP priority 3, before ML model). Building the risk engine *before* the ML model, as already planned, is the single best structural defense — write the interface contract assuming ML output may never arrive, then layer ML on top later without modifying risk engine internals.

---

### Pitfall 5: Fast automated approval gate is weaker than the manual dashboard gate it's meant to parallel

**What goes wrong:**
The project explicitly wants ("Gate de validação automática rápida") a faster path to promote background-generated candidates without the manual dashboard review cycle, while claiming to keep "a mesma disciplina do gate manual." In practice, automated gates that are built to be fast tend to quietly drop checks that are hard to automate well — particularly the things a human catches by eyeballing an equity curve: suspicious trade clustering, an equity curve driven by one or two outlier trades, parameter values at the edge of allowed ranges (`CONCERNS.md` already flags the mutation logic can push parameters toward range edges), or a strategy that's "diversified" only on paper because all its trades fire on the same correlated days.

**Why it happens:**
Profit factor, Sharpe, drawdown, and trade count are necessary but not sufficient — they're summary statistics that can hide concentration risk and good-by-luck outcomes. A fast gate, almost by definition, can only check what's cheap to compute, and the cheap checks are exactly the ones a human dashboard reviewer supplements with visual/contextual judgment.

**How to avoid:**
- Don't make the automatic gate "equivalent" to the manual one by using the same thresholds on the same summary stats — make it *stricter* on dimensions that are hard to eyeball automatically: require a minimum number of statistically independent trades (not just trade count — cluster trades by overlapping time windows and require diversity), cap the contribution of the single best trade to total profit (e.g., reject if removing the best trade flips profit factor below 1), and require the walk-forward pass (Pitfall 3) as a non-negotiable precondition, not an optional nice-to-have.
- Keep a "probation" period for auto-approved strategies: live in production at reduced size, monitored more tightly, before being treated as equal to a manually-approved strategy with full sizing — the automated gate's job is to be a fast *first* filter, not the final word.
- Log every auto-approved strategy with full justification (which checks passed, by how much margin) so the user can spot-audit a sample after the fact even though they didn't review each one before promotion — this preserves an audit trail equivalent in spirit to manual review even though it isn't manual review.
- Periodically (e.g., monthly) sample a handful of auto-approved strategies and run them through full manual dashboard review retroactively, to catch any drift between the automated gate's standards and what a human would actually approve.

**Warning signs:**
- Auto-approval rate much higher than the historical manual-approval rate for similar parameter ranges — suggests the automated gate is more permissive than it should be.
- Auto-approved strategies clustering around parameter values close to range limits.
- A single trade or single day responsible for a large share of an auto-approved strategy's reported profit.

**Phase to address:** This is explicitly a "Pending" decision in PROJECT.md Key Decisions. Recommend a dedicated phase (after walk-forward, after risk engine) — do not bundle into the same phase as the strategy lab itself, since the gate's job is fundamentally about trust and audit, which deserves isolated design and testing.

---

### Pitfall 6: Treating cointegration/correlation as a fixed property instead of re-testing it continuously

**What goes wrong:**
`docs/hedge_engine_spec.md` already names this risk correctly ("não tratar cointegração como uma propriedade permanente do par"), and the research confirms it's one of the most common real-world stat-arb failures: cointegration and correlation are statistical properties of a specific historical window, not physical constants. Currency pair relationships shift with monetary policy regime changes, risk-on/risk-off shifts, and structural breaks. A pair that was cointegrated when `hedge_candidates.csv` was generated can decohere weeks later while the production hedge engine keeps trading it as if the relationship still holds.

There's a second, related trap specific to scanning many pairs: with dozens of candidate pairs tested for cointegration, some will test positive purely by chance (multiple comparisons problem) — research estimates roughly 5% of tested pairs will appear cointegrated by chance alone at a standard significance threshold, regardless of any real economic relationship.

**Why it happens:**
Re-testing cointegration on every cycle is more expensive than checking a cached flag, and it's easy to treat `is_cointegrated` from `hedge_candidates.csv` as a stable label rather than a point-in-time snapshot — `docs/hedge_engine_spec.md` already flags the spread_zscore snapshot-staleness risk but the cointegration flag itself has the same staleness problem, just on a slower timescale.

**How to avoid:**
- Re-run the Engle-Granger test on a rolling window at a defined cadence (e.g., daily, or every N bars) for every pair currently held or being considered, not just at pipeline-generation time — the project's own spec already recommends this; make sure it's actually implemented in `hedge_engine.py`, not just specified.
- Require an economic rationale (shared base/quote currency, shared commodity exposure, shared regional risk sentiment) for any pair before it's even eligible for testing — this reduces the multiple-comparisons problem by shrinking the candidate universe to economically plausible pairs rather than scanning all combinations blindly.
- When scanning many pairs, apply a stricter significance threshold than the textbook 0.05, or report how many pairs were tested alongside the p-value so a reviewer can sanity-check the false-discovery rate.
- Engle-Granger is sensitive to which series is the dependent variable in the first-stage regression (already a known limitation — test both orderings and require consistency, or use the ordering with lower residual variance, and document the choice).
- The hedge engine's exit condition for "correlation breakdown" must trigger even mid-position, independent of the z-score / profit target — the spec already requires this; ensure it's tested explicitly with a synthetic scenario where correlation decays mid-trade.

**Warning signs:**
- A pair stays in `hedge_candidates.csv` across many pipeline runs with little change in p-value — could mean stable relationship, or could mean cointegration isn't being recomputed.
- Sudden divergence in a held position with z-score not reverting, where correlation has dropped sharply — should already trigger the spec'd exit, verify it actually fires.
- Number of cointegrated pairs found scales suspiciously close to (number of pairs tested × 0.05) — sign that many "hits" are false discoveries.

**Phase to address:** Production hedge engine phase (`src/hedge_engine.py`, ROADMAP priority 4) — this is core logic for that module, not an afterthought; the spec already gets this right conceptually, the phase plan should make periodic re-testing a first-class, tested requirement.

---

### Pitfall 7: Python-process-down does not mean MT5-stops-trading (orphaned legs, stale signals, unmanaged risk)

**What goes wrong:**
This architecture deliberately splits logic across two processes that can fail independently: Python (data, hedge logic, risk sizing) and the MQL5 EA (execution). If the Python process crashes, hangs, or loses connectivity while a hedge position is open, the EA — by design — keeps running inside the MT5 terminal. Without an explicit heartbeat/timeout contract, this creates several specific failure modes for *this* architecture: (a) one leg of a multi-pair hedge gets closed by the EA's own logic (e.g., hitting its individual stop-loss) while the other leg has no Python process left to tell it to also close, leaving a naked directional position; (b) a stale shared-file signal (the chosen Python↔MQL5 communication mechanism per the spec) gets re-read and re-acted-on by the EA if the EA's polling logic doesn't distinguish "no new signal" from "old signal, act again"; (c) regime-switch or kill-switch decisions made in Python never reach the EA because the file-write step never happened, while the EA has no way to know the difference between "no new instructions" and "Python is dead."

**Why it happens:**
The shared-file polling mechanism recommended in `docs/risk_engine_mql5_spec.md` (simpler to build, chosen deliberately over sockets for the first iteration) has no inherent liveness signal — `FileIsExist`/`FileOpen` tells the EA a file exists, not that the process writing it is alive and recent. Without an explicit timestamp-based heartbeat, "no new file" and "Python crashed 10 minutes ago" look identical to the EA.

**How to avoid:**
- The spec already requires this — make sure it actually gets implemented, not just documented: every Python-written signal file must include a timestamp; the EA must check `current_time - signal_timestamp < TIMEOUT` before treating any instruction as current, and must independently age out to "manage-only mode" (manage open positions, apply stops, allow time-stops, but open no new trades) if the timeout is exceeded.
- "Manage-only mode" must include the ability to close/flatten existing hedge legs together using EA-local logic (time stop, correlation-breakdown proxy it can compute from price data alone) — it cannot wait for Python to come back to close a position, because Python may not come back before damage accumulates.
- Every order the EA places, hedge or not, must carry its own stop-loss at the broker level (already specced) — this is the single most effective mitigation against an orphaned leg turning into unbounded loss while Python is down.
- Test this failure mode explicitly and early: kill the Python process mid-position during demo testing and confirm the EA degrades gracefully (closes/manages positions, stops opening new ones) rather than freezing or ignoring stale files.
- Treat "Python process restarted" as a resynchronization event requiring the EA and Python to agree on current account/position state before Python resumes issuing new-trade instructions — don't let Python assume its last in-memory state still matches the account.

**Warning signs:**
- No timestamp field in the shared signal file format (check `docs/risk_engine_mql5_spec.md`'s eventual implementation against this).
- EA code that reads the signal file without comparing its age to a timeout constant.
- No documented/tested behavior for "what does the EA do when it hasn't heard from Python in N seconds."
- Manual test of killing the Python process never performed before demo trading begins.

**Phase to address:** MQL5 EA phase (ROADMAP priority 7, last) — but the heartbeat/timeout *contract* (file format, timeout value, manage-only-mode behavior) should be finalized and documented during the risk engine phase (priority 3), since both Python and MQL5 sides need to agree on it before either is built, not bolted on at the end.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Backtest PnL in "R" units without transaction costs | Isolates hedge-logic validation from sizing/cost assumptions early | Strategies look profitable that aren't once costs applied; must be re-validated before any real-money use | Only during early lab/prototype phase on synthetic data — must be closed before real-data revalidation (already flagged as High priority in CONCERNS.md) |
| Shared-file Python↔MQL5 communication instead of socket/ZeroMQ | Much simpler to build and debug; no DLL/library setup | Latency (hundreds of ms to 1s) may be too slow for aggressive M1 scalping; requires careful heartbeat/timestamp design to avoid staleness | Acceptable for M5+ timeframes and initial demo validation; revisit if backtests show edge requires faster reaction than file-polling allows |
| HMM regime detector with percentile-volatility fallback | Pipeline never blocks on HMM convergence failure | Inconsistent regime labeling across runs makes regime-conditioned strategy validation harder to trust; CONCERNS.md already flags this | Acceptable if which detector fired is logged and monitored; not acceptable to leave unlogged once regime-switching production logic depends on it |
| Synthetic-data-only development and testing | Fast iteration without broker/data dependency | Strategies and even bugs (e.g., already-fixed timestamp misalignment bug) may not surface until real data is used; synthetic data has none of real markets' regime shifts, gaps, or fat tails | Acceptable through camada 0/0.5 prototyping only — never acceptable as the sole validation basis before production, already correctly scoped this way in ROADMAP |
| Single dashboard gate metrics (trade count, profit factor, Sharpe, drawdown) as sole approval bar | Fast, objective, easy to implement and explain | Misses concentration risk (one lucky trade), multiple-comparisons overfitting, and regime fragility — see Pitfalls 1 and 5 | Acceptable as a first-pass filter; never acceptable as the only gate before walk-forward + real-data confirmation |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|-------------------|
| MT5 (MetaTrader5 Python package) | Assuming symbol names match a generic convention (e.g., "EURUSD") when broker uses suffixes/prefixes (e.g., "EURUSD.a", "EURUSDm") | Query broker's actual symbol list programmatically (`mt5.symbols_get()`) on first connect and store the mapping; never hardcode symbol strings across brokers |
| MT5 account mode | Assuming the demo account is in hedging mode without checking | Check `AccountInfoInteger(ACCOUNT_MARGIN_MODE)` at EA startup and refuse to run (or run in safe/no-trade mode) if it isn't `ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` — already specced, must be enforced in code, not just docs |
| MT5 order sending | Sending lot size or stop-loss distance that doesn't respect `SYMBOL_VOLUME_MIN/MAX/STEP` or `SYMBOL_TRADE_STOPS_LEVEL` | Always normalize via broker-reported symbol properties before sending any order; already specced, verify it's actually implemented and unit-tested with edge-case symbols |
| Python `MetaTrader5` package import | Importing unconditionally at module top-level even for `--mode synth` runs (current bug per CONCERNS.md) | Defer the import to inside `fetch_mt5()` only, with a clear error if missing, so synthetic-mode development never requires the broker terminal |
| Broker spread/commission data | Using a single illustrative number researched online instead of the actual broker's live values | Pull real spread behavior from the demo account over a representative period (including news events) before finalizing cost assumptions in the backtest |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| O(n²) cointegration scan across all symbol pairs | Pipeline run time grows quadratically as symbol universe grows | Already documented as acceptable in CONCERNS.md for <50 symbols; cap candidate universe via economic-rationale pre-filter (also helps Pitfall 6) | Becomes a bottleneck above roughly 50 symbols (1225+ pairs) |
| Recomputing rolling beta/z-score from scratch per candidate in the strategy lab | Strategy generation loop runtime balloons as generations × candidates × pairs grows | Cache `features_*.parquet` derived series in memory once per pair per `run_strategy_lab()` call, reuse across parameter mutations | Noticeable once candidate pools or generation counts scale up significantly beyond current small-pool prototyping |
| File-based Python↔MQL5 polling at tight intervals | High disk I/O and EA polling overhead if interval set too aggressively for the chosen timeframe | Match polling cadence to actual strategy timeframe (100-200ms is overkill for M5 holding periods of 50-100 bars); only tighten if latency is proven insufficient | Becomes relevant if the team later targets M1 or tick-level scalping where the existing file-based approach's latency ceiling matters |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Credentials passed as CLI arguments (current example in `data_pipeline.py` docs, per CONCERNS.md) | Credentials land in shell history / git history if copy-pasted as-is | Use environment variables (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`); update all examples in docs and README |
| No kill-switch implemented yet (per CONCERNS.md, flagged Critical) | Cannot halt live trading instantly if a bug is discovered; only recourse is manual EA removal/process kill, which may be slow under stress | Implement `KILL_SWITCH.flag`-style file checked every cycle by both `risk_engine.py` and the EA, exactly as already specced; test it under simulated live conditions before any demo-to-real transition |
| No alerting on drawdown/connectivity/EA failure (per CONCERNS.md, flagged High) | Operator may not notice a critical failure for hours, especially overnight given forex's 24/5 nature | Implement at minimum a Telegram/email alert for drawdown approaching limit, EA unexpectedly stopped, and Python↔MQL5 heartbeat loss, as already specced; required before demo testing begins per the project's own checklist |
| Demo-to-real transition without explicit user confirmation | Real capital exposed to an insufficiently validated system | Already a hard project rule (CLAUDE.md rule 6) — enforce it as a literal manual gate in any automation/tooling, never auto-promote |

## "Looks Done But Isn't" Checklist

- [ ] **Backtest engine**: Often missing realistic transaction costs even when "tests pass" — verify `backtest_engine.py` subtracts spread+commission+slippage per trade, not just in a separate later layer.
- [ ] **Walk-forward validation**: Often re-implemented as "split the data once and call it walk-forward" — verify there are multiple rolling/anchored folds with documented window/gap parameters fixed before any real-data run.
- [ ] **Cointegration check**: Often computed once at data-pipeline time and treated as permanent — verify `hedge_engine.py` re-tests cointegration/correlation on a live rolling basis, not just consuming a static `hedge_candidates.csv` snapshot.
- [ ] **Risk engine "deterministic" claim**: Often quietly gains an ML-conditioned branch once the ML layer exists — verify no import or conditional logic in `risk_engine.py`/`RiskGuard.mqh` depends on `ml_model.py` output or `confidence_ml`.
- [ ] **MT5 hedging-mode check**: Often assumed true because "I set up a hedging account" — verify the EA checks `ACCOUNT_MARGIN_MODE` programmatically at runtime, every session, not just once during manual setup.
- [ ] **Heartbeat/timeout between Python and EA**: Often documented in the spec but not actually wired into the EA's polling loop — verify by killing the Python process mid-test and observing the EA enters manage-only mode within the specified timeout.
- [ ] **Kill-switch**: Often "planned" but not implemented until right before going live, under time pressure — verify it exists and is tested well before the demo-testing checklist requires it.
- [ ] **Automated fast-approval gate**: Often "equivalent" to the manual gate only on paper — verify it requires walk-forward pass and trade-independence/concentration checks, not just the same four summary stats as the manual dashboard.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|-----------------|
| Strategy generator overfitting discovered after promotion | MEDIUM | Pull the strategy from production immediately (set to inactive in registry), re-run walk-forward with stricter folds, require confirmation on a fresh out-of-sample window before re-promotion |
| Transaction costs found to erode an "approved" strategy's edge | LOW | Re-run backtest with realistic costs added, demote any strategy whose net-of-cost profit factor falls below threshold; mostly a data/recompute fix, not a code rewrite, if costs are added as a backtest parameter rather than hardcoded |
| Cointegration breakdown not caught mid-position | MEDIUM-HIGH | Add the missing periodic re-test to `hedge_engine.py`, backtest the fix against the historical period where breakdown occurred to confirm it would have triggered an exit; review all currently-open or recently-closed positions for unflagged breakdown exposure |
| Risk engine found to have an ML-conditioned branch | HIGH | Treat as a safety incident, not a normal bug — halt live/demo trading via kill-switch immediately, audit all risk decisions made while the branch was live, remove the conditional, re-run the adversarial risk-engine test suite before resuming |
| Python crash leaves orphaned hedge leg | HIGH | Manual intervention required immediately (close the naked leg in the MT5 terminal); afterward, implement/verify the heartbeat-timeout manage-only mode so the EA would have closed it automatically; add this exact scenario as a required pre-demo test |
| Automated fast-approval gate found to have approved a bad strategy | MEDIUM | Demote the strategy, retroactively run it through full manual dashboard review plus walk-forward, tighten the automated gate's thresholds based on what it missed, and re-audit other auto-approved strategies from the same period |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|---------------|
| 1. Strategy generator overfitting | Walk-forward revalidation phase (ROADMAP item, before production hedge engine) | Confirm three-way data split exists, candidate count is logged, and a second held-out confirmation window is required before promotion |
| 2. Missing transaction costs in backtest | Real-data revalidation phase (ROADMAP step 2), before strategy lab is re-run on real data | Confirm `backtest_engine.py` subtracts realistic spread/commission/slippage per trade and dashboard shows net-of-cost metrics |
| 3. Walk-forward done in name only | Dedicated walk-forward phase (ROADMAP item) | Confirm window type/length/gap documented and fixed before real-data run; confirm same config applied to all candidates; confirm minimum trades-per-fold enforced |
| 4. Risk engine silently ML-dependent | Risk engine phase (ROADMAP priority 3, before ML model phase) | Confirm no import of `ml_model.py` in `risk_engine.py`/`RiskGuard.mqh`; confirm explicit "ML unavailable" behavior defined and tested |
| 5. Fast auto-approval gate weaker than manual gate | New phase, after walk-forward + risk engine are done | Confirm auto-gate requires walk-forward pass, trade-concentration check, and produces an audit log reviewable after the fact |
| 6. Cointegration treated as permanent | Production hedge engine phase (`hedge_engine.py`, ROADMAP priority 4) | Confirm periodic re-test of cointegration/correlation is implemented and covered by a synthetic-breakdown test case |
| 7. Python crash / orphaned EA positions | Heartbeat contract finalized in risk engine phase (priority 3); implemented in MQL5 EA phase (priority 7) | Confirm shared-signal file has a timestamp, EA enforces timeout, and a manual "kill Python mid-position" test is performed before any demo trading |

## Sources

- [What Is Overfitting in Algorithmic Trading? — Bookmap](https://bookmap.com/blog/what-is-overfitting-in-algorithmic-trading)
- [What is Overfitting in Trading? — AlgoTrading101](https://algotrading101.com/learn/what-is-overfitting-in-trading/)
- [Backtesting effectively: Avoiding curve fitting — 24markets](https://24markets.com/education/backtesting-effectively-avoiding-curve-fitting)
- [Overfitting in Trading Models: Examples, Risks, and Prevention — Aron Groups](https://arongroups.co/forex-articles/overfitting-in-trading/)
- [The impact of transaction costs and slippage on algorithmic trading performance — ResearchGate](https://www.researchgate.net/publication/384458498_The_impact_of_transactions_costs_and_slippage_on_algorithmic_trading_performance)
- [Why a Perfect Backtest Often Means a Flawed Strategy — FX Replay](https://fxreplay.com/learn/why-a-perfect-backtest-often-means-a-flawed-strategy)
- [Realistic Backtesting: Transaction Costs, Slippage, and Walk-Forward Optimization — Hyper Trading Automation](https://www.hyper-quant.tech/research/realistic-backtesting-methodology)
- [Backtesting Series Episode 5: Transaction Cost Modelling — BSIC Bocconi](https://bsic.it/backtesting-series-episode-5-transaction-cost-modelling/)
- [7 Common Backtesting Mistakes That Lead to False Confidence — QuantStrategy.io](https://quantstrategy.io/blog/7-common-backtesting-mistakes-that-lead-to-false-confidence/)
- [Walk-Forward Optimization: Anchored vs. Rolling Windows — Susan Potter](https://www.susanpotter.net/quant/walk-forward-optimization/)
- [Walk-Forward Optimization: How It Works, Its Limitations — QuantInsti](https://blog.quantinsti.com/walk-forward-optimization-introduction/)
- [Walk-Forward Optimization vs. Traditional Backtesting — QuantStrategy.io](https://quantstrategy.io/blog/walk-forward-optimization-vs-traditional-backtesting-which/)
- [MT5 Strategy Tester - Hedging/Netting Issue — MQL5 forum](https://www.mql5.com/en/forum/340771)
- [9 Key Differences Between Netting And Hedging On MT5 — ThisDayLive](https://www.thisdaylive.com/2026/01/10/9-key-differences-between-netting-and-hedging-on-mt5/)
- [Hedging vs Netting in MT5: Key Differences and Strategies — JustMarkets](https://justmarkets.com/trading-articles/learning/hedging-vs-netting-in-metatrader-5)
- [Account type: netting or hedging — MQL5 Book](https://www.mql5.com/en/book/automation/account/account_netting_hedge)
- [Hedging vs Netting on MT5: Key Differences Explained — B2Broker](https://b2broker.com/news/the-difference-between-hedging-and-netting-on-mt5/)
- [Systemic failures and organizational risk management in algorithmic trading — Min & Borch, 2022 (Sage/PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8978471/)
- [Machine learning in financial markets: A critical review of algorithmic trading and risk management — ResearchGate](https://www.researchgate.net/publication/378287610_Machine_learning_in_financial_markets_A_critical_review_of_algorithmic_trading_and_risk_management)
- [Time-Consistent Mean-Variance Pairs-Trading Under Regime-Switching Cointegration — SIAM Journal on Financial Mathematics](https://epubs.siam.org/doi/10.1137/18M1209611)
- [Statistical Arbitrage Models 2025: Pairs Trading, Cointegration, PCA Factors & Execution Risk — CoinCryptoRank](https://coincryptorank.com/blog/stat-arb-models-deep-dive)
- [Statistical Arbitrage Through Cointegrated Stocks (Part 1): Engle-Granger and Johansen Tests — MQL5 Articles](https://www.mql5.com/en/articles/18702)
- [Statistical Arbitrage Through Cointegrated Stocks (Part 10): Detecting Structural Breaks — MQL5 Articles](https://www.mql5.com/en/articles/20946)
- Project-internal: `docs/risk_engine_mql5_spec.md`, `docs/hedge_engine_spec.md`, `.planning/codebase/CONCERNS.md`, `ROADMAP.md`, `CLAUDE.md` (used to ground and cross-validate web findings against the project's own already-identified risks)

---
*Pitfalls research for: algorithmic forex scalping system (statistical hedge engine, ML regime detection, deterministic risk engine, MQL5 execution)*
*Researched: 2026-06-30*
