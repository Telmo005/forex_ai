# Feature Research

**Domain:** Algorithmic/quant forex scalping system — production layers (risk engine, hedge engine, ML regime model, regime-driven strategy switching, automated validation gate, walk-forward revalidation, MQL5 execution)
**Researched:** 2026-06-30
**Confidence:** MEDIUM (project specs are HIGH confidence/already decided; external ecosystem claims are MEDIUM — cross-checked web search, no paid provider access in this environment)

## Feature Landscape

### Table Stakes (Must Have — System Isn't Safe With Real Capital Without These)

These are non-negotiable. A retail algo system that skips any of these is not "less polished," it is unsafe to run with real money. This list maps directly onto the "Active" requirements in `.planning/PROJECT.md` and the layers in `ARCHITECTURE.md`.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Deterministic stop-loss on every order** | Every retail/prop risk framework treats "no stop loss" as disqualifying; a single unhedged leg without a stop can blow an account in scalping timeframes | LOW | Already specified in `docs/risk_engine_mql5_spec.md` — must be enforced both in `risk_engine.py` (pre-trade) and again in the MQL5 EA (post-trade, local) |
| **Position sizing via fractional Kelly (or equivalent capped sizing)** | Pure Kelly sizing is well documented as too aggressive for real drawdown tolerance; fractional Kelly (0.25x–0.5x) or volatility-scaled sizing (constant $ risk per trade, inversely scaled to recent realized vol) is the standard professional compromise | MEDIUM | Project already commits to fractional Kelly. Volatility-scaling is a complementary/alternative technique worth keeping as a documented alternative, not a replacement |
| **Max exposure limits (per-pair and portfolio-wide, correlation-adjusted)** | Industry-standard risk layers cap exposure per instrument and in aggregate; in a hedge/pairs system, naive per-pair caps undercount risk because correlated pairs compound exposure | MEDIUM-HIGH | Spec already calls for correlation-adjusted exposure — this is more sophisticated than most retail EAs, appropriately so given the pairs-hedge core strategy |
| **Daily/weekly max drawdown circuit breaker** | Universal pattern: breach a daily/weekly loss threshold → halt new orders until next period or manual reset. This is the single most common rule in every risk-management checklist surveyed | LOW-MEDIUM | Already specified. Multi-tier (daily, weekly, overall max) is standard; project should keep daily as the primary breaker since this is a scalping system with high trade frequency |
| **Max simultaneous open positions cap** | Prevents runaway position accumulation if a signal generator misbehaves or a feedback loop occurs | LOW | Already specified |
| **Manual kill-switch (instant, no-code-change)** | A file-flag or equivalent that immediately halts all new order placement, checked by both Python and the EA, independent of any other logic path | LOW | Already specified as `KILL_SWITCH.flag`. This is the correct minimal-complexity pattern — keep it dead simple, a single file check is more reliable than a UI toggle that depends on more moving parts |
| **EA-side redundant risk checks (don't trust Python blindly)** | This is the actual core lesson from every "automation went wrong" trading post-mortem: the last hop before the broker must independently re-verify limits, not assume the upstream layer is correct | MEDIUM | Already specified in `docs/risk_engine_mql5_spec.md`. This is correctly prioritized — defense in depth across the Python/MQL5 boundary |
| **Heartbeat / connection-loss fail-safe in the EA** | If Python stops sending signals, the EA must not freeze with stale state — it should manage existing positions (apply stops, time-based closes) but stop opening new ones until reconnected | MEDIUM | Already specified (30s timeout → "management only" mode). Matches the standard MT5 EA failure-mode pattern found across multiple sources — this is the correct minimum behavior, not "close everything" (panic-closing could itself realize losses) and not "keep trading blind" |
| **Lot/stop normalization against broker symbol specs** | Brokers reject orders outside `SYMBOL_VOLUME_MIN/MAX/STEP` and `SYMBOL_TRADE_STOPS_LEVEL` — this must be handled programmatically, every single order, no exceptions | LOW | Already specified, correctly scoped as EA-side responsibility |
| **Hedging-mode account confirmation before any multi-leg logic runs** | Netting-mode accounts silently collapse opposite positions on the same symbol, which breaks pairs-hedge logic without an obvious error — this must be checked at EA startup, not assumed | LOW | Already specified. Add this as an explicit pre-flight check the EA refuses to trade past if not in `ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` |
| **Objective backtest gate before any strategy reaches production params** | This is the entire premise of the Core Value in `PROJECT.md` — never trade real capital on an unvalidated strategy. Industry minimums for promoting a backtest to live: profit factor > ~1.3–1.5, Sharpe > ~1.0 (ideally >1.5), drawdown bounded, **and** a minimum trade count for statistical significance | LOW (already built) | Already implemented in `dashboard.py`/`strategy_generator.py` with thresholds (PF 1.2, Sharpe/trade 0.15, DD 8R, ≥20 trades). These are reasonable starting thresholds for a personal system but are on the lenient end versus commonly cited industry minimums (PF 1.3–1.5, 100+ trades) — flag for tightening once real-market data is available (see Pitfalls/Gaps below) |
| **Walk-forward validation, never random split** | Single most repeated rule across all quant trading sources reviewed — random train/test split on time series leaks future information and produces backtests that look good and fail live | LOW (already decided) | Already a hard constraint in `CLAUDE.md` and `ml_model_spec.md`. Must extend to the **out-of-sample revalidation of approved lab strategies** before they reach `hedge_engine.py` — this is currently a known gap (see `strategy_lab_spec.md` "Limitações conhecidas") |
| **Transaction costs (spread, slippage, commission) in every backtest** | Especially critical for scalping, where edge per trade is small and costs can exceed gross profit; "clean" backtests without costs are a textbook cause of live-vs-backtest divergence | LOW (already decided) | Already a hard constraint. Verify this also applies to any newly added automated validation gate, not just the manual dashboard path |
| **Real-time regime detection feeding active strategy selection** | A regime-aware system that doesn't actually use regime to switch behavior is just an HMM running in the background for show. Production-grade regime-aware systems (per multiple sources) maintain a per-regime strategy or allocation table and switch live as regime probability updates | MEDIUM-HIGH | Project's HMM (layer 0) already exists for offline detection; the new piece is **wiring it to live strategy/risk-posture selection**, not just labeling. Industry pattern: maintain a table of {regime → approved strategy/parameter set or risk multiplier}, not a single fixed parameter set across all regimes |
| **Live performance vs. backtest-expectation divergence monitor** | Standard quant ops pattern (often called "strategy decay detection" or reconciliation) — track live realized stats against the backtest's expected distribution and flag/act when they diverge meaningfully | MEDIUM-HIGH | Not yet specified anywhere in the docs — this is a genuine gap. CUSUM-style sequential change detection is the most commonly cited statistically rigorous approach (faster detection of regime shift in strategy performance than fixed-window rules); a simpler rolling-window profit-factor/Sharpe threshold is an acceptable first version |
| **Automatic fallback when no approved strategy fits current regime** | Already decided as a Key Decision in `PROJECT.md`: switch to another pre-approved strategy, or reduce exposure to zero — never invent/improvise live | MEDIUM | This is the correct, conservative pattern. The "reduce to zero" branch must be unconditionally reachable (i.e., always a valid fallback state) so the switcher can never get stuck with "no good option, trade anyway" |
| **Pre-real-money checklist gate (demo soak period)** | Universal pattern in serious algo deployments: a fixed minimum period of demo/paper trading (commonly cited 30–60 days minimum, project spec already suggests 4–6 weeks) with daily monitoring before any real-money switch, plus explicit human confirmation | LOW (already decided) | Already specified in `risk_engine_mql5_spec.md` checklist and as a hard constraint in `CLAUDE.md`/`PROJECT.md` ("nunca passar de demo para real sem confirmação explícita") |
| **Alerting on critical risk events** | Drawdown approaching limit, EA unexpectedly stopped, Python↔MQL5 connection lost — these need to reach the trader outside the terminal (Telegram/email), because a single-trader system has no second human watching the screen | LOW-MEDIUM | Already listed in the pre-real-money checklist. Should be implemented early, not deferred to "later," since it has no dependency on ML/hedge layers being done — it can be built and tested against the risk engine alone |

### Differentiators (Competitive/Quality Advantage)

These go beyond bare minimum safety and are where this system can be meaningfully better than a typical retail EA or a naive ML-signal bot.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Automated fast-track validation gate for background-generated candidates** | Lets the strategy lab's continuous generation loop promote strong candidates faster than the manual dashboard review cycle, without lowering the bar — same objective criteria, different approval path | MEDIUM-HIGH | Already an open "Active" requirement and an explicit "Pending" Key Decision. Recommend: same thresholds as the dashboard gate, PLUS an additional out-of-sample walk-forward pass and a short live-shadow period (paper-trade the candidate without capital for N days) before auto-promotion — the automatic path should be *stricter* than manual, not equal, because it removes human judgment as a final check |
| **Correlation-adjusted portfolio exposure (vs. naive per-symbol caps)** | Most retail EAs only cap exposure per symbol; correlation-aware exposure aggregation is a genuine differentiator that more accurately reflects true portfolio risk in a pairs-hedge strategy | MEDIUM-HIGH | Already in spec. This is good practice that many commercial EAs skip |
| **Regime-conditioned model confidence / per-regime accuracy tracking** | Knowing the model is reliable in regime A but weak in regime B lets the hedge/risk layers discount or ignore signals contextually instead of treating all model output as equally trustworthy | MEDIUM | Already specified in `ml_model_spec.md` output schema (`regime_specific_accuracy`). Valuable and not commonly done well even in commercial systems |
| **CUSUM-style sequential strategy-decay detection** | Faster and more statistically principled than a fixed rolling-window threshold for detecting "this strategy has stopped working," which directly serves the project's divergence-reaction requirement | MEDIUM-HIGH | Not yet in any spec — recommend introducing in the risk-engine or a new monitoring module once the basic threshold-based divergence check (table stakes) is working. Treat as a v1.x upgrade, not MVP |
| **PnL measured in "R" units decoupling strategy logic from position sizing** | Already implemented in the strategy lab — this is a genuinely good architectural choice that isolates "is this entry/exit logic sound" from "how much capital should this risk," which most hobbyist backtesters conflate | LOW (done) | Keep this separation intact when building `hedge_engine.py` and `risk_engine.py` — do not let position-sizing logic leak back into the hedge engine |
| **Layered defense-in-depth risk checks (Python AND MQL5, independently)** | Most retail EAs check risk once, either client-side script or EA, not both. Redundant verification across the process boundary is a differentiator versus typical single-point-of-failure EAs | MEDIUM | Already specified — keep as-is, this is a strength of the existing design |

### Anti-Features (Commonly Tempting, Deliberately Avoid)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| **Online/continuous learning (per-tick weight updates)** | Feels more "adaptive" and cutting-edge | Removes the ability to audit what the model believed at any point in time; makes walk-forward validation meaningless since the model is always different from what was tested; in scalping the noise floor makes per-tick updates likely to chase noise, not signal | Periodic retraining (e.g., weekly) with full walk-forward revalidation before any retrained model replaces the production one — already the project's decided approach |
| **Deep learning as the first ML model** | LSTMs/Transformers are state-of-the-art in many ML domains, feels more powerful | Needs much more data than is realistically available, harder to validate/debug, easier to overfit on noisy forex data at short timeframes, slower iteration cycle | Gradient boosting (LightGBM/XGBoost) baseline first — already decided. Revisit deep learning only after a solid, validated baseline exists and a clear capacity gap is identified |
| **"Guaranteed profit" / zero-loss framing for any gate or kill-switch** | Tempting to market the risk engine internally as "this prevents losses" | Statistically impossible to promise for any real trading system; framing risk controls this way leads to overconfidence and under-monitoring once the system is "trusted" | Frame all gates as "limits and contains losses," never "prevents losses" — already explicit in `PROJECT.md` Out of Scope |
| **Letting the automatic fast-track gate fully replace human review** | Faster iteration, less manual dashboard babysitting | Removes the last qualitative check a human can apply (does this strategy make economic sense, not just pass thresholds) — single point of failure if the objective criteria themselves have a blind spot | Keep automatic gate stricter than manual (extra OOS pass, shadow period) and periodically spot-check auto-promoted strategies manually, rather than treating automatic as a full replacement |
| **Trusting a single in-sample backtest as sufficient for promotion** | Lab already does this and shows good numbers — feels like "enough proof" | In-sample selection bias: trying many parameter combinations and picking the best one *will* find spurious good results even on random data (multiple-comparisons problem) — the strategy lab's own "Limitações conhecidas" section already flags this | Mandatory out-of-sample walk-forward revalidation on a held-out period AFTER lab approval, before promotion to `hedge_engine.py` — already an open Active requirement, treat as blocking, not optional |
| **Reacting to live divergence by inventing a new strategy on the fly** | Seems "smart" — adapt immediately to what's happening | Live-generated, unvalidated logic violates the Core Value (no real capital on unvalidated strategy); under live stress is exactly when judgment is worst | Switch only between pre-approved strategies for the current regime, or reduce exposure to zero — already a decided Key Decision, correctly conservative |
| **Closing all positions instantly on any Python↔EA disconnect** | Seems like the "safe" default | Panic-closing a hedge pair asymmetrically (e.g., network drops mid-close) can realize a large loss on one leg while leaving the other open, which is worse than holding with stops in place | EA enters "management only" mode on heartbeat timeout: keep applying existing stops/time-exits, do not open new positions, do not force-close existing ones unless a risk limit is independently breached — already correctly specified |
| **Single fixed threshold set used for both lab approval and live circuit-breaker** | Reuses existing thresholds, looks consistent | Backtest distributions and live distributions are different populations (live has slippage, latency, partial fills); using identical thresholds for "should I promote this" and "should I halt this strategy live" conflates two different statistical questions | Use backtest-gate thresholds (PF, Sharpe, DD, trade count) for promotion; use a separate live-divergence detector (rolling/CUSUM comparison of live stats vs. the strategy's own backtest expectation) for the circuit breaker — these should be two distinct mechanisms even if they share some metrics |

## Feature Dependencies

```
[Deterministic Risk Engine (risk_engine.py + RiskGuard.mqh)]
    └──requires──> [Kill-switch file mechanism]
    └──requires──> [Broker symbol spec lookup (lot/stop normalization)]
    └──requires──> [Hedging-mode account check]

[Production Hedge Engine (hedge_engine.py)]
    └──requires──> [Approved strategy params from Strategy Lab dashboard]
    └──requires──> [Deterministic Risk Engine] (hedge engine proposes, never executes directly)
    └──enhanced-by──> [ML regime/signal model] (optional input, not required for first version)

[Real-time regime detection feeding live strategy switching]
    └──requires──> [HMM regime detector (layer 0, already done)]
    └──requires──> [At least 2+ approved strategies in registry, mapped to regimes]
    └──requires──> [Deterministic Risk Engine] (the "reduce exposure to zero" fallback is a risk-engine-level action)

[Live divergence circuit breaker]
    └──requires──> [Production Hedge Engine running live] (nothing to compare against before this exists)
    └──requires──> [Backtest expected-stats baseline per approved strategy] (from Strategy Lab / walk-forward OOS results)
    └──enhances──> [Regime-driven strategy switching] (divergence detection is often what TRIGGERS a switch)

[Automated fast-track validation gate]
    └──requires──> [Existing manual dashboard gate criteria] (reuses/extends, doesn't replace)
    └──requires──> [Walk-forward out-of-sample revalidation] (stricter than manual path)
    └──enhances──> [Strategy Lab generator loop] (lets background generation promote faster)

[Walk-forward out-of-sample revalidation]
    └──requires──> [Strategy Lab + approved params] (revalidates what lab already approved)
    └──requires──> [Real market data, not synthetic] (already flagged as next step in ROADMAP.md)

[MQL5 Expert Advisor execution]
    └──requires──> [Deterministic Risk Engine fully built and tested with synthetic adversarial orders]
    └──requires──> [Hedging-mode MT5 account confirmed]
    └──requires──> [Python <-> MQL5 communication channel decided] (file-based first, socket/ZeroMQ if latency insufficient)
    └──requires──> [Demo soak period before any live-capital switch]

[Alerting (Telegram/email)]
    └──enhances──> [Deterministic Risk Engine] (can be built/tested independently and early, no ML/hedge dependency)
```

### Dependency Notes

- **Risk Engine is the true root dependency.** Almost everything else (hedge engine execution, regime switching's "reduce to zero" fallback, the EA) assumes a working, tested risk engine exists first. This matches the project's own stated priority order in `ROADMAP.md` ("motor de risco antes do modelo de ML") — research confirms this is the right call, not just a stylistic preference. No serious production trading architecture puts execution before risk controls.
- **Regime-driven switching requires multiple approved strategies, not just regime detection.** The HMM (layer 0) already works, but switching is meaningless with only one approved strategy in the registry — this feature has an implicit dependency on the strategy lab having produced and approved at least two distinct strategies (ideally tuned/suited to different regimes), which is a content/data dependency as much as a code dependency.
- **Live divergence circuit breaker requires something live to monitor.** This cannot be meaningfully built or tested until the hedge engine is running against at least a demo account — it can be designed and unit-tested against synthetic divergence scenarios earlier, but full validation needs live (or live-like replay) data.
- **Automated fast-track gate enhances but does not replace the manual gate** — both should coexist; the automatic path is for background-generated candidates needing faster iteration, the manual path remains available for human spot-checks and judgment calls the objective criteria might miss.
- **Walk-forward OOS revalidation is a blocking dependency for BOTH the hedge engine and the automated gate** — it sits between "lab-approved on synthetic/historical data" and "trusted enough for production parameters," regardless of which gate (manual or automatic) approved the strategy.

## MVP Definition

### Launch With (v1 — this milestone)

Minimum to call the system "safe to run with demo capital" per the project's own Core Value and Active requirements:

- [ ] Deterministic risk engine (Python `risk_engine.py`): Kelly-fractional sizing, correlation-adjusted exposure caps, daily drawdown circuit breaker, max simultaneous positions, kill-switch file — essential, this is the safety floor
- [ ] Risk engine tested against synthetic adversarial orders (oversized lots, duplicate orders, invalid symbols) — essential per the project's own pre-real-money checklist
- [ ] `hedge_engine.py` production version consuming only ✅-Approved dashboard parameters — essential, this is the actual trading logic
- [ ] Walk-forward out-of-sample revalidation of lab-approved strategies on real (non-synthetic) market data — essential, already flagged as a known gap by the project itself
- [ ] MQL5 EA with redundant local risk checks, lot/stop normalization, hedging-mode confirmation, heartbeat/management-only fallback, mandatory stop-loss on every order — essential, last line of defense
- [ ] Basic alerting (at minimum: drawdown nearing limit, EA stopped, connection lost) — essential and low effort, no reason to defer

### Add After Validation (v1.x)

Features to add once the MVP above is running cleanly on demo:

- [ ] ML baseline model (gradient boosting) feeding directional signal + confidence into the hedge engine — trigger: once hedge engine + risk engine are stable in demo without ML input, add ML as an enhancement rather than a blocker for first live-demo cycle
- [ ] Real-time regime detection wired to live strategy/risk-posture switching — trigger: once 2+ strategies are approved and revalidated out-of-sample, build the regime→strategy mapping
- [ ] Automated fast-track validation gate for background-generated candidates — trigger: once the manual dashboard gate + OOS revalidation pipeline is proven reliable, extract its criteria into an automatic gate with an added shadow-period requirement
- [ ] Live divergence circuit breaker (simple rolling-window profit-factor/Sharpe check vs. backtest expectation) — trigger: once the hedge engine has run live (demo) long enough to have a meaningful comparison baseline

### Future Consideration (v2+)

- [ ] CUSUM-style sequential change detection for strategy decay — defer until the simpler threshold-based divergence monitor is in place and its false-positive/false-negative behavior is understood from real operation
- [ ] Socket/ZeroMQ Python↔MQL5 communication — defer until file-based communication is proven functionally correct end-to-end and latency is measured to actually be insufficient in practice (per the project's own recommendation in `risk_engine_mql5_spec.md`)
- [ ] Deep learning model architecture (LSTM/Transformer) — defer until gradient boosting baseline has a proven, validated edge and a specific capacity limitation is identified that justifies the added complexity and data requirements

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Deterministic risk engine (Python) | HIGH | MEDIUM | P1 |
| RiskGuard.mqh (EA-side risk checks) | HIGH | MEDIUM | P1 |
| Kill-switch mechanism | HIGH | LOW | P1 |
| Production hedge engine | HIGH | MEDIUM | P1 |
| Walk-forward OOS revalidation of lab strategies | HIGH | MEDIUM | P1 |
| MQL5 EA core execution + heartbeat fallback | HIGH | HIGH | P1 |
| Basic alerting (Telegram/email) | MEDIUM-HIGH | LOW | P1 |
| ML baseline model (gradient boosting) | MEDIUM | MEDIUM-HIGH | P2 |
| Regime-driven live strategy switching | MEDIUM-HIGH | MEDIUM-HIGH | P2 |
| Automated fast-track validation gate | MEDIUM | MEDIUM | P2 |
| Live divergence circuit breaker (threshold-based) | HIGH | MEDIUM | P2 |
| CUSUM-style decay detection | MEDIUM | MEDIUM-HIGH | P3 |
| Socket/ZeroMQ communication upgrade | LOW (unless latency proven insufficient) | MEDIUM-HIGH | P3 |
| Deep learning model | LOW (no proven need yet) | HIGH | P3 |

**Priority key:**
- P1: Must have before any demo-account live testing
- P2: Should have, adds real capability once P1 is proven stable
- P3: Nice to have, defer until a concrete need is demonstrated

## Competitor Feature Analysis

Direct "competitor" framing doesn't map cleanly onto a single-trader personal system, but the relevant comparison set is: (a) typical commercial/retail MT5 EAs, (b) institutional/prop-style quant pipelines, (c) prop-firm evaluation criteria (which function like an external validation gate analogous to this project's promotion gate).

| Feature | Typical Retail MT5 EA | Institutional/Prop Quant Pipeline | This Project's Approach |
|---------|------------------------|-------------------------------------|--------------------------|
| Risk checks | Often single-point (just in the EA, basic lot-size and SL only) | Multi-layer: pre-trade risk service + execution-side checks, often with real-time portfolio VaR | Two independent layers (Python risk engine + EA RiskGuard) — closer to institutional pattern, appropriately so given real-money stakes |
| Strategy promotion gate | Usually none, or a single backtest screenshot | Formal IS/OOS split, walk-forward, multiple-comparison correction, paper-trading period before capital allocation | Already has an objective IS gate (dashboard); the gap vs. institutional practice is the missing mandatory OOS walk-forward pass before production — correctly identified as next priority |
| Regime awareness | Rare; most retail EAs run one fixed logic regardless of market state | Common in systematic multi-strategy funds — regime-conditioned strategy/weight tables | Project's plan (regime → approved strategy selection, fallback to zero exposure) matches the institutional pattern, scaled down appropriately for a single-trader system |
| Live monitoring / decay detection | Rare, usually manual ("I noticed it's losing, I'll turn it off") | Standard — automated decay/drift monitoring (CUSUM or rolling-window) with predefined response (de-risk, retrain, retire) | Currently a gap (no spec exists yet) — recommended as a P2 addition, with CUSUM as a P3 refinement once basic threshold monitoring is proven |
| Account safety pre-flight checks | Inconsistent — many EAs assume netting/hedging mode without checking | Standard practice to validate account configuration before any order logic runs | Already specified (hedging-mode check) — matches best practice |

## Sources

- [7 Risk Management Strategies for Algorithmic Trading (Nurp)](https://nurp.com/algorithmic-trading-blog/7-risk-management-strategies-for-algorithmic-trading/) — MEDIUM confidence (web search, single source per claim)
- [Maximum Drawdown in Forex Algorithmic Trading (Nurp)](https://nurp.com/wisdom/mastering-maximum-drawdown-in-forex-trading-a-comprehensive-guide/) — MEDIUM confidence
- [Risk Management in Algorithmic Trading: Beyond Stop-Loss Orders (AlgoBulls)](https://algobulls.com/blog/algo-trading/risk-management) — MEDIUM confidence
- [How to Backtest Trading Strategies (QuantVPS)](https://www.quantvps.com/blog/backtesting-trading-strategies) — MEDIUM confidence
- [Understanding Drawdown, Sharpe Ratio, and Profit Factor (QuantStrategy.io)](https://quantstrategy.io/blog/essential-backtesting-metrics-understanding-drawdown-sharpe/) — MEDIUM confidence, cross-checked against multiple backtesting-metric sources (consistent PF >1.3-1.5, Sharpe >1.0-1.5, trade count 100+ across sources)
- [7 Key Metrics to Review When Backtesting Day Trading Strategies (GoatFundedTrader)](https://www.goatfundedtrader.com/blog/backtesting-day-trading-strategies) — MEDIUM confidence
- [Hidden Markov Model Market Regimes (QuantifiedStrategies)](https://www.quantifiedstrategies.com/hidden-markov-model-market-regimes-how-hmm-detects-market-regimes-in-trading-strategies/) — MEDIUM confidence
- [Regime Detection: Measuring Market Regime Shifts (PickMyTrade)](https://blog.pickmytrade.trade/regime-detection-measuring-market-regime-shifts-2026/) — MEDIUM confidence
- [Step-by-Step Python Guide for Regime-Specific Trading Using HMM and Random Forest (QuantInsti)](https://blog.quantinsti.com/regime-adaptive-trading-python/) — MEDIUM confidence, cross-checked: consistent with other sources on regime→strategy table pattern
- [Why Backtesting Environments Differ from Live Markets (AlgoBulls)](https://algobulls.com/blog/algo-trading/backtesting-technical-factor) — MEDIUM confidence
- [Reconciliation - Live Trading (QuantConnect docs)](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation) — MEDIUM-HIGH confidence (vendor documentation, but for a real institutional-grade platform's live/backtest reconciliation pattern)
- [The CUSUM Filter: A Statistical Approach to Market Trends (Quant Journey)](https://quantjourney.substack.com/p/the-cusum-filter-a-statistical-approach) — MEDIUM confidence
- [Monitoring Active Portfolios: The CUSUM Approach (Thomas K. Philips, Northfield)](https://www.northinfo.com/Documents/144.pdf) — MEDIUM-HIGH confidence (named author, cites Moustakides 1986 formal optimality result for CUSUM change detection)
- [Running LLM Trading Agents on a VPS: Compute, Cost, and Risk Infrastructure (VPSForexTrader)](https://www.vpsforextrader.com/blog/autonomous-trading-agents/) — MEDIUM confidence, relevant to heartbeat/fail-safe pattern for autonomous execution
- Project's own specs (HIGH confidence — already decided/in-repo): `docs/risk_engine_mql5_spec.md`, `docs/hedge_engine_spec.md`, `docs/ml_model_spec.md`, `docs/strategy_lab_spec.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `.planning/PROJECT.md`

**Note on confidence:** No specialized search/docs providers (Exa, Brave, Tavily, Context7, Firecrawl) were enabled in this project's GSD config (`.planning/config.json` — all `false`). All external findings used the built-in WebSearch tool and are tagged MEDIUM confidence per `gsd-tools query classify-confidence --provider websearch --verified` (cross-checked across 2+ independent sources where claims are load-bearing, e.g. profit factor/Sharpe/trade-count thresholds and the CUSUM optimality claim). Treat specific numeric thresholds (PF 1.3–1.5, Sharpe 1.0–1.5, 100+ trades) as directional industry convention, not precise targets — the project's own existing thresholds (PF 1.2, Sharpe/trade 0.15, ≥20 trades) are a reasonable conservative starting point for synthetic-data validation but should be tightened once real broker data and live demo results are available.

---
*Feature research for: algorithmic forex scalping system — production safety and adaptive-strategy layers*
*Researched: 2026-06-30*
