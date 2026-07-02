# Phase 3: Production Hedge Engine - Research

**Researched:** 2026-07-02
**Domain:** Statistical pairs-trading decision layer (Python) — cointegration re-verification, z-score entry/exit logic, strategy-registry-gated order proposal generation
**Confidence:** HIGH

## Summary

This phase builds `src/hedge_engine.py`, the pure decision layer that sits between the strategy registry (Phase 1) and the risk engine (Phase 2). The domain is already extremely well-specified inside this repository: `docs/hedge_engine_spec.md` defines the exact entry/exit triggers, `03-CONTEXT.md` locks every numeric threshold, and `src/backtest_engine.py` already implements the causal, no-lookahead statistical primitives (`compute_rolling_beta`, `compute_zscore`) this phase must reuse rather than reimplement. The project-level research summary explicitly flagged this domain as "already well-specced" (skip external research), and that assessment holds — this RESEARCH.md is therefore weighted toward internal-codebase verification (confirmed function signatures, confirmed strategy_registry columns, confirmed RiskDecision contract) plus one direct empirical benchmark (cointegration test latency) rather than external library discovery.

The critical architectural decision is not "which library" but "which existing function to call, in what order, producing what shape." `hedge_engine.py` has zero new third-party dependencies — everything it needs (`statsmodels.tsa.stattools.coint`, `pandas.rolling`, `sqlite3` via `strategy_registry.py`) is already a project dependency, already imported by `data_pipeline.py` and `backtest_engine.py`. The engineering work is (1) building a pure, testable "evaluate one pair, one bar" decision function mirroring the `risk_engine.py::evaluate_order` pattern already established in Phase 2, (2) wrapping that function in a live-like bar-by-bar/event loop that works identically against historical parquet replay today and a live feed adapter later, and (3) wiring cointegration re-verification as a scheduled, stateful side-check rather than a one-time gate.

**Primary recommendation:** Build `hedge_engine.py` as a set of pure functions (`evaluate_hedge_signal()`, `check_cointegration_freshness()`, `select_eligible_strategies()`) driven by a thin, swappable event-loop runner (`run_hedge_loop()`) that consumes a `PriceFeed`-shaped iterator — backed by parquet replay now, a live MT5/file feed later — and calls `risk_engine.evaluate_order()` directly as a same-process Python function for every proposal, exactly as `03-CONTEXT.md` D-(discretion) specifies.

**Important correction found during research:** `03-CONTEXT.md` D-07 refers to the strategy-approval gate as `status == "✅ Aprovada"`. Direct verification against `dashboard.py` in this session shows the `strategies.status` column actually stores the literal string `"passed"`/`"failed"` — the emoji text is only a display-layer mapping (`dashboard.py` line 136), never persisted. The planner must filter on `status="passed"`, not the emoji string. See "Strategy Loading Contract" below for the verified, corrected filter code. This does not change D-07's intent, only its literal implementation value.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Strategy selection (which approved params apply to which pair) | Hedge Engine (this phase) | Strategy Registry (Phase 1, read-only) | HEDGE-01: only registry rows with `status`=Approved, `wf_passed=1`, `revalidated_on_real_data=1` are eligible; hedge engine owns the filter logic, registry owns storage |
| Cointegration re-verification | Hedge Engine (this phase) | Data Pipeline (Layer 0, statistical primitive reused) | HEDGE-03: hedge engine owns the scheduling/state (when to re-test, what to do on failure); the Engle-Granger test itself is the same primitive `data_pipeline.py::scan_hedge_candidates` already uses |
| Z-score / beta recalculation | Hedge Engine (this phase) | Backtest Engine (Phase 1, function reused) | Hedge engine owns *when* to recalc and *what to do* with the result; `compute_rolling_beta`/`compute_zscore` are reused verbatim from `backtest_engine.py` to guarantee backtest/production parity |
| Entry/exit trigger evaluation | Hedge Engine (this phase) | — | Pure business logic per `docs/hedge_engine_spec.md`; no other tier owns this |
| Order proposal structuring | Hedge Engine (this phase) | Risk Engine (Phase 2, consumer) | Hedge engine produces the structured dict; risk engine is the sole consumer/authority on whether it becomes a real order |
| Position sizing, exposure limits, drawdown, kill-switch | Risk Engine (Phase 2) | — | Explicitly out of scope for this phase (HEDGE-02) — hedge engine must never compute a lot size or risk-reject an order itself |
| Order execution | Execution/EA (Phase 4, future) | — | Explicitly out of scope; this phase's proposals stop at `risk_engine.evaluate_order()` (same-process call) |
| Live/replay data source abstraction | Hedge Engine (this phase, thin adapter) | Data Pipeline (Layer 0, parquet producer) | This phase must define the feed *interface* now (bar-by-bar iterator) even though only a parquet-replay implementation exists; Phase 4 will add a live implementation behind the same interface |

## User Constraints

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Entry logic**
- **D-01:** `ENTRY_THRESHOLD = 2.0` — minimum `abs(spread_zscore)` to open a hedge position (spec's own suggestion, confirmed by user).
- **D-02:** `MIN_CORRELATION_ENTRY = 0.5` — minimum correlation to allow entry (user chose more permissive than the spec's suggested 0.6, to allow more candidate pairs through).
- Cointegration check on entry: `is_cointegrated == True` on the most recently recomputed test (never a stale snapshot) — per spec, non-negotiable, no numeric parameter needed.

**Exit logic**
- **D-03:** `EXIT_THRESHOLD = 0.3` — close on reversion when `abs(spread_zscore) <= 0.3`.
- **D-04:** `MIN_CORRELATION_EXIT = 0.4` — close immediately (regardless of z-score) when recalculated correlation drops below this — the hedge premise broke down.
- **D-05:** `MAX_HOLD_BARS = 75` — time-stop: close if a position has been open this many bars (M5 timeframe) without reverting (~6h15 of market time). `MIN_CORRELATION_EXIT` (D-04) triggers independently and takes priority the moment correlation breaks, regardless of how long the position has been open.
- Risk-driven stop (RISK-04/05/06 circuit breakers from Phase 2) is NOT this module's authority — `hedge_engine.py` proposes, `risk_engine.py` can veto/close; this module never implements its own risk-driven exit logic (per spec, and per HEDGE-02's "propose, never execute" boundary).

**Cointegration re-verification schedule**
- **D-06 (Claude's discretion):** Re-test cointegration (Engle-Granger) every **50 bars** during an open position's lifetime, and once before every new entry decision (never reuse a snapshot older than the current re-test interval). Named, documented, retunable constant — matches project convention (`WALK_FORWARD_CONFIG`, `RiskLimits` style).

**Strategy source and multi-strategy handling**
- **D-07 (Claude's discretion):** `hedge_engine.py` loads strategy parameters ONLY from `strategy_registry.py` rows where `status == "✅ Aprovada"` AND `wf_passed == 1` AND `revalidated_on_real_data == 1` (Phase 1's real-data walk-forward gate) — never live-generated, never in-sample-only approved. This is HEDGE-01's exact requirement.
- **D-08 (Claude's discretion):** v1 does NOT implement regime-based strategy selection (REGIME-01/REGIME-02 are v2). Every strategy meeting D-07's criteria is independently eligible to propose orders for its own pair — there is no "pick the single best strategy" logic in v1. `risk_engine.py`'s `max_concurrent_pairs` cap (Phase 2, D-08 = 3) is the natural backstop against over-proposing. If two approved strategies target the exact identical pair, implement a simple, explicit tie-break (e.g., most-recently-approved wins, or reject the pair as ambiguous and log a warning) — not a silent first-match.

### Claude's Discretion
- Exact tie-break rule when two approved strategies target the same pair (D-08) — must be explicit and logged, not silent.
- Whether cointegration re-testing (D-06) runs against `output/features_*.parquet` directly or against an in-memory rolling buffer — implementation detail; spec establishes: recompute from the parquet/stream, never trust the original snapshot.
- Exact structure/module boundary for how `hedge_engine.py` invokes `risk_engine.py` (direct Python function call vs. some future serialization) — call it directly as a Python function; both modules live in the same process for now, no IPC needed until Phase 4's EA exists.

### Deferred Ideas (OUT OF SCOPE)
- Regime-based strategy selection (REGIME-01/REGIME-02) — v2, deferred until Phases 1-4 run stably on demo.
- Live divergence circuit breaker (DIVERGE-01) — v2, needs a running hedge engine on live/demo data first.
- Dynamic hedge ratio via Kalman filter — current spec and this phase use the static `hedge_ratio_beta` from Layer 0's OLS regression; Kalman is a possible future refinement, not required for v1.
</user_constraints>

## Phase Requirements

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HEDGE-01 | `hedge_engine.py` de produção só consome parâmetros de estratégia ✅ Aprovados no dashboard (nunca parâmetros gerados ao vivo sem aprovação) | See "Strategy Loading Contract" below — confirmed exact SQL/filter logic against `strategy_registry.py` schema (`status`, `wf_passed`, `revalidated_on_real_data` columns verified to exist) |
| HEDGE-02 | `hedge_engine.py` propõe ordens mas nunca as executa diretamente — execução passa sempre pelo motor de risco | See "Risk Engine Integration Contract" below — confirmed exact `OrderProposal`/`RiskDecision`/`evaluate_order()` signatures from `src/risk_engine.py`, confirmed field-mapping from hedge-engine proposal shape to `OrderProposal` |
| HEDGE-03 | `hedge_engine.py` revalida cointegração periodicamente em vez de a tratar como propriedade permanente de um par | See "Cointegration Re-Verification Design" below — confirmed `coint()` signature/performance, confirmed reuse pattern from `data_pipeline.py::scan_hedge_candidates` |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| statsmodels | 0.14.6 [VERIFIED: local environment `pip show statsmodels`] | Engle-Granger cointegration test (`coint`), OLS hedge ratio (`sm.OLS`) | Already the project's cointegration library (`data_pipeline.py`, `.claude/skills/quant-finance-math/SKILL.md`); this phase reuses it, does not add it |
| pandas | project-pinned (unpinned in requirements.txt, environment has a working install) [CITED: `requirements.txt`] | Rolling z-score/correlation windows, causal `.rolling()` | Already used throughout `backtest_engine.py`/`data_pipeline.py`; this phase's live recalculation must use the exact same causal rolling pattern to avoid backtest/production drift |
| numpy | project-pinned | Array ops inside reused `compute_rolling_beta`/`compute_zscore` | Already a dependency, no new usage pattern introduced |

**No new third-party packages are required for this phase.** Everything `hedge_engine.py` needs is already an installed, imported dependency of `data_pipeline.py` or `backtest_engine.py`.

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| sqlite3 (stdlib) | — | Read approved strategies via `strategy_registry.get_strategy()`/`list_strategies()` | Already wrapped by `strategy_registry.py`; hedge_engine.py should call those functions, never open its own sqlite3 connection to `strategy_lab.db` |
| pyarrow (via `pd.read_parquet`) | project-pinned | Read `output/features_<SYMBOL>.parquet` for replay/live-like bar iteration | Same pattern `data_pipeline.py` already uses to write these files |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Reusing `backtest_engine.compute_rolling_beta`/`compute_zscore` verbatim | Reimplementing a "live" variant of beta/z-score calculation | Rejected — `03-CONTEXT.md` code_context section explicitly requires reuse "to avoid backtest/production behavior drift"; a parallel implementation is a documented pitfall in this exact codebase's own research history |
| Kalman-filter dynamic hedge ratio (`pykalman`/`filterpy`, per `.claude/skills/quant-finance-math/SKILL.md`) | Static OLS beta from `hedge_ratio_beta`/rolling OLS | Rejected for v1 — `03-CONTEXT.md` Deferred Ideas explicitly defers Kalman; static beta (already used everywhere else in the codebase) is correct scope for this phase |
| Building a `bridge/file_bridge.py`-style IPC layer now | Direct same-process Python function call to `risk_engine.evaluate_order()` | Rejected for v1 — `03-CONTEXT.md` explicitly resolves this: no IPC needed until Phase 4's EA exists; direct call is simpler and matches current architecture |

**Installation:**
```bash
# No new packages. Verify existing environment has what's needed:
pip show statsmodels pandas numpy pyarrow
```

**Version verification:** Confirmed via `pip show statsmodels` in the project's active environment: statsmodels 0.14.6, Python 3.12.10. `coint()` signature confirmed via `inspect.signature`: `coint(y0, y1, trend='c', method='aeg', maxlag=None, autolag='aic', return_results=None)` [VERIFIED: local environment]. This matches the exact call pattern already used in `data_pipeline.py::scan_hedge_candidates` (`score, pvalue, _ = coint(a, b)`), confirming no signature drift since that code was written.

## Package Legitimacy Audit

**Not applicable — this phase installs zero new external packages.** All statistical/data dependencies (`statsmodels`, `pandas`, `numpy`, `pyarrow`) are pre-existing project dependencies already present in `requirements.txt` and already imported by `src/data_pipeline.py` and `src/backtest_engine.py`. The Package Legitimacy Gate is not triggered.

## Architecture Patterns

### System Architecture Diagram

```
                    ┌─────────────────────────────┐
                    │  output/features_*.parquet   │  (Layer 0, existing)
                    │  output/hedge_candidates.csv │
                    └───────────────┬──────────────┘
                                    │ bar-by-bar iterator
                                    ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                    hedge_engine.py (this phase)                │
   │                                                                 │
   │  ┌──────────────────┐   ┌────────────────────────────────┐    │
   │  │ load_eligible_    │   │ PriceFeed (replay now,         │    │
   │  │ strategies()      │   │ live-swappable later)          │    │
   │  │ (strategy_        │   └───────────────┬────────────────┘    │
   │  │  registry.py)     │                   │ bar                 │
   │  └────────┬──────────┘                   ▼                     │
   │           │                  ┌────────────────────────────┐    │
   │           │                  │ recalc beta/zscore/corr     │    │
   │           │                  │ (reuse backtest_engine.py)  │    │
   │           │                  └───────────────┬─────────────┘    │
   │           │                                  ▼                  │
   │           │                  ┌────────────────────────────┐    │
   │           └─────────────────▶│ check_cointegration_       │    │
   │                              │ freshness() — every 50 bars │    │
   │                              │ (reuse coint() from         │    │
   │                              │  data_pipeline.py pattern)  │    │
   │                              └───────────────┬─────────────┘    │
   │                                              ▼                  │
   │                              ┌────────────────────────────┐    │
   │                              │ evaluate_hedge_signal()     │    │
   │                              │ entry/exit trigger logic    │    │
   │                              │ (pure function, per pair)   │    │
   │                              └───────────────┬─────────────┘    │
   │                                              │ open_hedge /      │
   │                                              │ close_hedge dict  │
   └──────────────────────────────────────────────┼───────────────────┘
                                                    ▼
                    ┌───────────────────────────────────────────┐
                    │  risk_engine.evaluate_order()  (Phase 2)   │
                    │  same-process Python function call         │
                    │  -> RiskDecision (approved/rejected)        │
                    └───────────────────┬─────────────────────────┘
                                        ▼
                    ┌───────────────────────────────────────────┐
                    │  (Phase 4, future) EA / execution bridge   │
                    │  — OUT OF SCOPE for this phase             │
                    └───────────────────────────────────────────┘
```

A reader can trace the primary use case: parquet bar arrives → beta/z-score/correlation recalculated causally → cointegration freshness checked (every 50 bars) → entry/exit trigger evaluated against locked thresholds → structured proposal dict built → passed to `risk_engine.evaluate_order()` → approved/rejected decision returned. Nothing after `evaluate_order()` is in scope.

### Recommended Project Structure
```
src/
├── hedge_engine.py       # this phase — pure decision functions + loop runner
├── risk_engine.py        # Phase 2, existing — consumed via evaluate_order()
├── risk_limits.py        # Phase 2, existing — RiskLimits dataclass
├── strategy_registry.py  # Phase 1, existing — get_strategy()/list_strategies()
├── backtest_engine.py    # Phase 1, existing — compute_rolling_beta/compute_zscore reused
└── data_pipeline.py      # Layer 0, existing — coint() reuse pattern, feature parquet schema
tests/
├── test_risk_engine.py   # Phase 2, existing — pattern to mirror
└── test_hedge_engine.py  # this phase — new
```

### Pattern 1: Pure Decision Function Mirroring `risk_engine.evaluate_order()`
**What:** A single pure function `evaluate_hedge_signal(pair_state, params, position, coint_state) -> dict | None` that takes explicit inputs (no hidden I/O, no global state) and returns either `None` (no action) or a structured `open_hedge`/`close_hedge` dict. Cointegration re-test scheduling is modeled as explicit state passed in, not a side-effecting timer.
**When to use:** For every entry/exit evaluation, one call per pair per bar.
**Example:**
```python
# Source: pattern mirrored from src/risk_engine.py::evaluate_order (Phase 2, verified)
# Fixed evaluation order per docs/hedge_engine_spec.md — mirrors risk_engine's
# documented "fixed order, short-circuit" convention.
def evaluate_hedge_signal(
    pair_a: str, pair_b: str,
    zscore: float, correlation: float, is_cointegrated: bool,
    position: dict | None,
    params: dict,  # ENTRY_THRESHOLD, MIN_CORRELATION_ENTRY, EXIT_THRESHOLD,
                    # MIN_CORRELATION_EXIT, MAX_HOLD_BARS — from HEDGE_PARAMS
    bars_held: int = 0,
) -> dict | None:
    if position is None:
        # Entry: cointegration + z-score + correlation, ALL required (spec, D-01/D-02)
        if is_cointegrated and abs(zscore) >= params["ENTRY_THRESHOLD"] \
                and correlation >= params["MIN_CORRELATION_ENTRY"]:
            direction = -1 if zscore > 0 else 1  # short spread if above mean
            return {"action": "open_hedge", "pair_a": pair_a, "pair_b": pair_b,
                    "direction": direction, "trigger": "spread_zscore",
                    "trigger_value": zscore}
        return None

    # Exit: correlation breakdown (D-04) checked BEFORE reversion/time-stop —
    # it must override even a fresh position (per 03-CONTEXT.md D-05 note:
    # "MIN_CORRELATION_EXIT triggers independently and takes priority").
    if correlation < params["MIN_CORRELATION_EXIT"]:
        return {"action": "close_hedge", "pair_a": pair_a, "pair_b": pair_b,
                "trigger": "correlation_breakdown", "trigger_value": correlation}
    if abs(zscore) <= params["EXIT_THRESHOLD"]:
        return {"action": "close_hedge", "pair_a": pair_a, "pair_b": pair_b,
                "trigger": "reversion", "trigger_value": zscore}
    if bars_held >= params["MAX_HOLD_BARS"]:
        return {"action": "close_hedge", "pair_a": pair_a, "pair_b": pair_b,
                "trigger": "time_stop", "trigger_value": bars_held}
    return None
```
**Note:** `docs/hedge_engine_spec.md`'s exit list numbers "reversão" before "quebra de correlação," but `03-CONTEXT.md` D-05 explicitly overrides ordering intent — correlation breakdown "takes priority... regardless." The planner should make the exit-check order an explicit, tested behavior (correlation-breakdown check first), not leave it to incidental code order.

### Pattern 2: Live-Like Bar-by-Bar Loop Behind a Swappable Feed Interface
**What:** A thin `run_hedge_loop(feed, ...)` function where `feed` is any iterable/generator yielding one aligned bar (or bar-tuple across pairs) at a time. Backed today by a parquet-replay generator (`replay_feed(feature_parquet_paths)`); backed later (Phase 4, out of scope) by a live MT5/polling generator with the *same yielded shape*.
**When to use:** This is the actual "genuine design question" flagged in the phase's additional_context — there is no live data source yet, only historical parquet files, but the loop shape must not need redesigning when a live feed exists.
**Example:**
```python
# Source: pattern derived from data_pipeline.py's existing parquet I/O
# conventions (pd.read_parquet already used to WRITE these files) and
# backtest_engine.py's existing bar-index iteration style (run_hedge_backtest's
# `for i in range(start, n)` loop) — no new library, just a generator wrapper.
from typing import Iterator, NamedTuple
import pandas as pd

class Bar(NamedTuple):
    timestamp: pd.Timestamp
    symbol: str
    close: float

def replay_feed(feature_parquet_paths: dict[str, str]) -> Iterator[dict[str, Bar]]:
    """Yields one aligned cross-symbol bar dict per step, oldest to newest.
    Replay implementation for backtest/dry-run mode. A future live_feed()
    (Phase 4) yields the identical dict shape from a polling/MT5 source —
    run_hedge_loop() below never needs to know which one it's consuming."""
    frames = {sym: pd.read_parquet(path) for sym, path in feature_parquet_paths.items()}
    common_idx = None
    for df in frames.values():
        common_idx = df.index if common_idx is None else common_idx.intersection(df.index)
    for ts in sorted(common_idx):
        yield {sym: Bar(ts, sym, df.loc[ts, "close"]) for sym, df in frames.items()}

def run_hedge_loop(feed: Iterator[dict[str, Bar]], eligible_strategies: list[dict],
                    risk_evaluate_fn, coint_recheck_every: int = 50) -> None:
    """Same loop body works for replay (today) and live (Phase 4) because
    `feed` is the only thing that changes. State (open positions, bars-since-
    last-coint-check) lives in this function's scope, explicit, not global."""
    ...  # per-pair state dicts, updated each iteration; calls evaluate_hedge_signal()
         # then risk_evaluate_fn() (== risk_engine.evaluate_order) for every proposal
```
**Key design point:** the interface boundary is the yielded bar shape (`dict[symbol, Bar]`), not the transport. This is what lets the planner build and test this phase entirely against `output/features_*.parquet` today with zero rework needed when Phase 4 wires a live feed.

### Pattern 3: Cointegration Re-Verification as Explicit Scheduled State
**What:** Track `bars_since_coint_check` per pair (both for open positions and for candidate pairs being evaluated for entry). Re-run the exact same Engle-Granger test `data_pipeline.py::scan_hedge_candidates` already runs, on a trailing window, every `COINT_RECHECK_EVERY_BARS = 50` bars (D-06) — never trust `hedge_candidates.csv`'s `is_cointegrated` value beyond its generation moment.
**When to use:** Once per pair per re-test cadence; always once immediately before a new entry decision (D-06: "never reuse a snapshot older than the current re-test interval").
**Example:**
```python
# Source: statistical primitive reused verbatim from
# src/data_pipeline.py::scan_hedge_candidates (verified signature via
# `inspect.signature(coint)` in this session: coint(y0, y1, trend='c',
# method='aeg', maxlag=None, autolag='aic', return_results=None))
from statsmodels.tsa.stattools import coint

COINT_RECHECK_EVERY_BARS = 50  # D-06 — see docstring rationale in 03-CONTEXT.md

def recheck_cointegration(price_a: pd.Series, price_b: pd.Series,
                           coint_window: int, pvalue_threshold: float) -> tuple[bool, float]:
    """Re-run Engle-Granger on the trailing `coint_window` bars — same test,
    same window convention as data_pipeline.py::scan_hedge_candidates, so
    entry-time and re-test-time cointegration checks stay consistent
    (03-CONTEXT.md canonical_refs)."""
    a = price_a.iloc[-coint_window:].reset_index(drop=True)
    b = price_b.iloc[-coint_window:].reset_index(drop=True)
    score, pvalue, _ = coint(a, b)
    return pvalue < pvalue_threshold, pvalue
```
**Verified performance:** A benchmark run in this research session (1000-bar window, `statsmodels` 0.14.6, this project's Python 3.12.10 environment) measured `coint()` at **~73ms per call** [VERIFIED: local benchmark, this session]. At a 50-bar re-check cadence with `max_concurrent_pairs=3` (Phase 2 D-08), the absolute worst case is 3 `coint()` calls every 50 bars — well under one bar's wall-clock budget even at M1, and trivial at M5. This confirms D-06's 50-bar cadence is not a computational bottleneck; the choice can remain driven purely by the statistical/trading rationale already documented in `03-CONTEXT.md`, with no performance-driven pressure to widen the interval.

### Anti-Patterns to Avoid
- **Reimplementing beta/z-score/cointegration from scratch:** `03-CONTEXT.md` code_context section is explicit — reuse `backtest_engine.compute_rolling_beta`/`compute_zscore` and the `data_pipeline.py` `coint()` pattern verbatim. A parallel implementation risks backtest/production behavior drift, the exact failure mode this project's own `ARCHITECTURE.md` anti-patterns section calls out ("Lookahead Bias in Backtest").
- **Hedge engine sizing or rejecting orders itself:** HEDGE-02 is structural, not stylistic — `hedge_engine.py` must never compute a lot size, never independently veto an order for a risk reason (drawdown, exposure). Every proposal, without exception, must flow through `risk_engine.evaluate_order()`. Mirror the `risk_engine.py` module docstring's own stated verification approach: this should be checkable by a static grep test (`hedge_engine.py` never imports/calls anything from `risk_limits.py`'s numeric fields directly, never open-codes a lot-size formula), not just code review — matching the RISK-09 precedent already set in this codebase (`tests/test_risk_engine.py` encodes ML-independence as an executable test; this phase should encode "no self-sizing" the same way).
- **Treating `hedge_candidates.csv`'s `is_cointegrated` as a permanent property:** HEDGE-03's entire point. The CSV is a Layer-0 snapshot; production must recompute, per D-06's schedule, every time.
- **Silent first-match tie-break when two approved strategies target the same pair:** D-08 requires an explicit, logged tie-break — never a silent `strategies[0]`.
- **Building the live feed adapter now:** Out of scope per the phase's own additional_context note — build the *interface* (bar-by-bar iterator shape) now, but only the parquet-replay implementation; a live MT5/file-bridge implementation is Phase 4 work.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Engle-Granger cointegration test | A custom ADF-on-residuals implementation | `statsmodels.tsa.stattools.coint` (already used by `data_pipeline.py`) | Correctness of the augmented Engle-Granger critical values is subtle (MacKinnon response surface); statsmodels already implements this correctly and the project already depends on it |
| Causal rolling beta/z-score | A hand-rolled expanding-window OLS loop | `backtest_engine.compute_rolling_beta`/`compute_zscore` | These functions are already verified no-lookahead (per `ARCHITECTURE.md` Anti-Patterns section) and already exercised by the existing backtest suite; a second implementation is a correctness/drift risk with zero benefit |
| Strategy approval filtering (SQL) | A raw `sqlite3.connect(...)` query in `hedge_engine.py` | `strategy_registry.list_strategies()`/`get_strategy()` | These already exist, already handle the `cost_model_version`/`wf_passed`/`revalidated_on_real_data` schema, and keep the DB access pattern in one place (single point of schema-change maintenance) |
| Order-approval / sizing / risk gating | Any local "if exposure too high, skip" logic inside hedge_engine.py | `risk_engine.evaluate_order()` | This is HEDGE-02's exact boundary; duplicating any risk logic here, even partially, reintroduces the "risk logic scattered across layers" anti-pattern this project's Phase 2 was built specifically to prevent |
| Bar-by-bar backtest-vs-live parity | Two separate code paths for "backtest mode" and "live mode" | Single `run_hedge_loop()` driven by a swappable `feed` iterator | Two divergent code paths is exactly the kind of drift `03-CONTEXT.md` explicitly warns against for the statistical primitives; the same principle applies to the loop shape |

**Key insight:** Every piece of statistical/data machinery this phase needs already exists somewhere in this codebase, built and (partially) tested. The engineering risk in this phase is not "getting the math right" — it's "wiring existing correct pieces together without accidentally duplicating or diverging from them." Treat every new function in `hedge_engine.py` that resembles something in `backtest_engine.py`/`data_pipeline.py`/`risk_engine.py` as a signal to import and reuse, not reimplement.

## Common Pitfalls

### Pitfall 1: Stale Z-Score / Cointegration Snapshot Used as Live Signal
**What goes wrong:** Reading `spread_zscore`/`is_cointegrated` directly from `output/hedge_candidates.csv` at decision time, as if it reflected the current bar.
**Why it happens:** The CSV is right there, already has the right columns, and looks like exactly the input the spec describes — but it's a Layer-0 pipeline snapshot, frozen at the moment `data_pipeline.py` last ran.
**How to avoid:** `hedge_candidates.csv` should only ever be used to discover *which pairs are candidates* (the initial universe); every z-score/correlation/cointegration value used in an actual entry/exit decision must be recomputed from `output/features_<SYMBOL>.parquet` (or a live feed later) at decision time, per `docs/hedge_engine_spec.md`'s own "Coisas a evitar" section.
**Warning signs:** Any code path in `hedge_engine.py` that reads `hedge_candidates.csv` inside the per-bar loop (vs. once, at startup, to build the candidate pair universe).

### Pitfall 2: Correlation-Breakdown Exit Ordering Gets Silently Deprioritized
**What goes wrong:** Implementing the exit checks in the literal order listed in `docs/hedge_engine_spec.md` (reversion, then correlation breakdown, then time-stop) without encoding `03-CONTEXT.md` D-05's explicit override ("MIN_CORRELATION_EXIT triggers independently and takes priority the moment correlation breaks, regardless of how long the position has been open").
**Why it happens:** The original spec document and the later CONTEXT.md decision are two separate documents; a planner/implementer reading only the spec would produce the spec's literal ordering, which is superseded.
**How to avoid:** Correlation-breakdown must be checked first among the exit conditions (see Pattern 1's code example above) — write this as an explicit unit test: "given a position with correlation below MIN_CORRELATION_EXIT AND z-score also within EXIT_THRESHOLD simultaneously, the exit reason returned must be `correlation_breakdown`, not `reversion`."
**Warning signs:** A trade log where `exit_reason == "reversion"` appears for a position whose correlation had already broken down before reversion occurred.

### Pitfall 3: Two Strategies Targeting the Same Pair, Silent First-Match
**What goes wrong:** `strategy_registry.list_strategies(status="✅ Aprovada")` can return multiple approved rows for the same `(pair_a, pair_b)` (e.g., re-approved after a parameter tweak, or two independently-generated strategies that happened to converge on the same pair). Naively iterating and using the first match silently discards the other, with no record of the decision.
**Why it happens:** Nothing in the registry schema prevents two approved rows for the same pair; the registry's job is to record what passed validation, not to enforce pair-uniqueness.
**How to avoid:** Per D-08, implement an explicit tie-break function (e.g., `most-recently-approved wins` via `created_at`, or explicit ambiguous-pair rejection) and log a warning identifying both `id`s whenever the tie-break fires. This must be a named, tested code path, not an implicit `strategies[0]`.
**Warning signs:** `strategy_registry` query for a pair returns `len(rows) > 1` and the code has no branch that logs or handles that case.

### Pitfall 4: Confusing `wf_passed` with `revalidated_on_real_data`
**What goes wrong:** Loading strategies where `wf_passed == 1` but `revalidated_on_real_data == 0` (walk-forward passed on synthetic data only) as if they were production-eligible.
**Why it happens:** `wf_passed` sounds like the complete gate; the two-flag design (visible in `strategy_registry.py::migrate_add_walk_forward_columns` docstring and `risk_engine.py::resolve_kelly_inputs`) is a deliberate two-step gate that's easy to collapse into one check by accident.
**How to avoid:** The strategy-loading filter in `hedge_engine.py` (HEDGE-01/D-07) must check `wf_passed == 1 AND revalidated_on_real_data == 1` — both flags, `AND`, never inferring one from the other. This exact discipline is already documented in `strategy_registry.py`'s own docstrings ("nunca inferindo uma flag a partir da outra") and already implemented correctly in `risk_engine.py::resolve_kelly_inputs` — mirror that same two-flag check.
**Warning signs:** A strategy-loading query or filter that only checks one of the two columns.

### Pitfall 5: Building a Backtest-Mode / Live-Mode Fork Instead of One Feed-Driven Loop
**What goes wrong:** Writing `run_backtest()` and `run_live()` as two separate functions with duplicated entry/exit/coint-recheck logic, because "backtest doesn't need a real-time loop."
**Why it happens:** It's tempting to treat backtest mode as "just run the whole array at once" (like `backtest_engine.run_hedge_backtest` does) rather than as a special case of the live loop.
**How to avoid:** Per the phase's own additional_context ("structure a live-like loop... that can run in backtest/replay mode now and be wired to a real feed later without redesign"), the loop body (`run_hedge_loop`) must be identical for both; only the `feed` iterator implementation changes. See Pattern 2 above.
**Warning signs:** Two functions in `hedge_engine.py` that both contain entry/exit trigger logic.

### Pitfall 6: Confusing Correlation with Cointegration in the Entry Gate
**What goes wrong:** Treating a high `correlation` value as sufficient evidence for entry without also requiring `is_cointegrated == True`.
**Why it happens:** Both are float values in the same `hedge_candidates.csv` row and both "feel like" measures of relatedness; it's easy to gate on the more familiar/simpler metric (correlation) and treat cointegration as secondary.
**How to avoid:** `docs/hedge_engine_spec.md`'s own "Coisas a evitar" section calls this out explicitly, and it's reinforced in `.claude/skills/quant-finance-math/SKILL.md`'s "Erros comuns" section: two pairs can be highly correlated short-term without being cointegrated (no statistically significant mean reversion), which makes correlation-only hedging riskier. Entry requires **both** `is_cointegrated == True` AND `correlation >= MIN_CORRELATION_ENTRY` — never either alone.
**Warning signs:** An entry code path that doesn't reference `is_cointegrated` at all, or that treats it as optional.

## Code Examples

### Strategy Loading Contract (HEDGE-01, D-07) — Verified Against Actual Schema
```python
# Source: verified against src/strategy_registry.py AND src/dashboard.py
# (both read in full this session). CORRECTION to 03-CONTEXT.md D-07: the
# literal value stored in the `status` column is "passed" (or "failed"),
# NOT the emoji string "✅ Aprovada" — that emoji form only exists as a
# UI DISPLAY mapping inside dashboard.py (line 136:
# view_display["status"].map({"passed": "✅ Aprovada", "failed": "❌ Reprovada"})),
# applied to a rendering copy of the dataframe, never written back to the
# database. `strategy_registry.save_strategy()` receives `record["status"]`
# from backtest_engine/strategy_generator's own "passed"/"failed" values
# (see backtest_engine.validate_strategy() return convention). Filtering on
# "✅ Aprovada" would silently match ZERO rows in the live database.
from src.strategy_registry import list_strategies

def load_eligible_strategies(db_path: str) -> list[dict]:
    """HEDGE-01 / D-07: only rows passing ALL THREE gates are eligible.
    Never infer revalidated_on_real_data from wf_passed (Pitfall 4).
    status filter uses "passed" (the actual stored value), not the emoji
    display string — see correction note above."""
    df = list_strategies(db_path, status="passed")
    if df.empty:
        return []
    eligible = df[(df["wf_passed"] == 1) & (df["revalidated_on_real_data"] == 1)]
    return eligible.to_dict("records")
```
**Resolved (was Open Question 1):** Confirmed via direct `grep` of `dashboard.py` in this session (lines 87-136) — the `strategies.status` column stores the literal string `"passed"`/`"failed"`, written by `backtest_engine.validate_strategy()`'s boolean-to-string convention via `strategy_generator.py`. The `"✅ Aprovada"`/`"❌ Reprovada"` emoji strings referenced in `03-CONTEXT.md` D-07 and `.planning/PROJECT.md` describe the **user-facing concept** ("dashboard-approved") but are rendered only in `dashboard.py`'s display layer (`view_display["status"].map(...)`, a copy used for `st.dataframe`), never persisted to the `strategies` table. **The planner must use `status="passed"` as the actual filter value, not the emoji string** — this is a correction to the literal text of `03-CONTEXT.md` D-07, though it does not change D-07's underlying intent (only dashboard-approved/"passed" strategies are eligible — "passed" from `validate_strategy()` IS the dashboard approval gate's outcome, confirmed by `dashboard.py`'s own status_filter radio button mapping "✅ Aprovadas" display label to the `"passed"` DB value at line 127-128).

### Risk Engine Integration Contract (HEDGE-02) — Verified Field Mapping
```python
# Source: verified against src/risk_engine.py OrderProposal/RiskDecision
# dataclasses and evaluate_order() signature (read in full this session).
from src.risk_engine import OrderProposal, evaluate_order, AccountState
from src.risk_limits import RiskLimits, KILL_SWITCH_PATH

def propose_to_risk_engine(hedge_proposal: dict, win_rate: float, payoff_ratio: float,
                            entry_price: float, stop_distance_price_units: float,
                            account_state: AccountState, correlation_matrix: dict,
                            new_position_exposure_pct: float = 0.0):
    """hedge_engine.py's proposal dict (per docs/hedge_engine_spec.md output
    format) must be translated into risk_engine's OrderProposal — the two
    shapes are NOT identical (hedge proposal has pair_a/pair_b/trigger;
    OrderProposal has symbol_a/symbol_b/win_rate/payoff_ratio/direction).
    win_rate/payoff_ratio come from strategy_registry via
    risk_engine.resolve_kelly_inputs(strategy_record) — NOT computed here
    (HEDGE-02: hedge_engine never sizes, never invents Kelly inputs)."""
    proposal = OrderProposal(
        symbol_a=hedge_proposal["pair_a"],
        symbol_b=hedge_proposal["pair_b"],
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        entry_price=entry_price,
        stop_distance_price_units=stop_distance_price_units,
        direction=hedge_proposal["direction"],
    )
    return evaluate_order(
        proposal, account_state, RiskLimits(), correlation_matrix,
        kill_switch_path=KILL_SWITCH_PATH,
        new_position_exposure_pct=new_position_exposure_pct,
    )
```
**Open item for planner:** `stop_distance_price_units` is explicitly the hedge/Phase-3 layer's responsibility per `risk_engine.py`'s own `OrderProposal` docstring ("decidido pela camada de hedge (Fase 3); este módulo não inventa a distância do stop"). This phase must decide the exact formula (e.g., `entry_std * some_multiplier`) — `03-CONTEXT.md` does not lock this value; it is Claude's discretion during planning, and must be a named, documented constant (matching the project's established convention), not inferred implicitly.

### Cointegration Freshness Check (HEDGE-03) — Verified `coint()` Call Pattern
See Pattern 3 above (`recheck_cointegration`) — signature and ~73ms/call latency verified in this session against the actual installed `statsmodels` 0.14.6.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Treating `hedge_candidates.csv` cointegration as permanent | Scheduled re-verification every N bars (D-06: 50 bars) | This phase (HEDGE-03) | Directly addresses the project's own previously-identified Pitfall ("cointegration treated as permanent property" — flagged in `.planning/research/SUMMARY.md` Critical Pitfalls #6 and `docs/hedge_engine_spec.md`'s own "Coisas a evitar" section) |
| Static Kelly inputs computed ad hoc | `risk_engine.resolve_kelly_inputs()` already implements the correct out-of-sample-preferring extraction | Phase 2 (already built) | This phase must call, not reimplement, this function when constructing risk proposals |

**Deprecated/outdated:** None specific to this phase's external dependencies — this is an internal architecture phase, not a library-currency phase.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `stop_distance_price_units` formula is undecided by `03-CONTEXT.md` and left to planning-time discretion | Risk Engine Integration Contract | If left unspecified in the plan, implementers may each choose a different ad hoc formula (e.g., using `entry_std` vs. a fixed multiple of `ENTRY_THRESHOLD`), causing sl_price to vary unpredictably run-to-run; must be a named constant, documented, and consistent with `docs/hedge_engine_spec.md`'s general framing of stop distance in spread-std-dev units |
| A2 | `coint()` benchmark of ~73ms/call (this session, 1000-bar window) generalizes to the project's actual `coint_window` (default 1000 per `PipelineConfig.coint_window`, confirmed matching) and to the target M5 timeframe's bar-arrival cadence | Pattern 3 / Common Pitfalls | If M1 (not M5) becomes the live timeframe, or if `max_concurrent_pairs` were raised well above 3, the 50-bar recheck cadence's computational headroom shrinks — should be re-verified if timeframe or pair-count assumptions change |

**If this table is empty:** N/A — see entries above.

## Open Questions

1. **Exact `stop_distance_price_units` formula for the hedge→risk handoff**
   - What we know: `risk_engine.py`'s own docstring states this is explicitly Phase 3's responsibility to decide, and that `risk_engine.py` only guarantees a positive `sl_price` is computed from whatever distance is passed in.
   - What's unclear: No numeric formula is locked in `03-CONTEXT.md`. Plausible options: a fixed multiple of `entry_std` (the spread's rolling std at entry, already computed by `compute_zscore`), or a fixed multiple of `ENTRY_THRESHOLD` itself.
   - Recommendation: Planner should propose a named constant (e.g., `STOP_DISTANCE_STD_MULTIPLIER`) expressed in spread-std-dev units (consistent with the project's "R" unit convention used throughout `backtest_engine.py`), and flag it as Claude's discretion requiring no further user confirmation (it does not change trading behavior thresholds already locked, only how the risk engine computes a stop price from the hedge engine's proposal).

2. **Multi-pair concurrent bar alignment in `replay_feed`**
   - What we know: `output/features_<SYMBOL>.parquet` files are per-symbol, and `data_pipeline.py::load_all_symbols` already guarantees a shared `DatetimeIndex` at generation time (via `shared_idx` in synth mode; MT5 mode inherits broker timestamps).
   - What's unclear: Whether MT5-mode-generated parquet files for different symbols always share identical timestamps bar-for-bar in practice (weekend gaps, broker-specific missing bars, DST) — this affects how strict `replay_feed`'s index-intersection logic needs to be.
   - Recommendation: Use `pandas` index intersection (as shown in Pattern 2) rather than assuming identical indices; log a warning if the intersected index drops more than a small fraction of bars from any symbol, since a large drop would indicate a data-alignment problem worth investigating before trusting replay results.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | This phase has no user-facing auth surface; it's a same-process Python module |
| V3 Session Management | No | No sessions involved |
| V4 Access Control | No | No multi-user/role concept in this single-trader system |
| V5 Input Validation | Yes | `hedge_engine.py` must validate/sanitize any values read from `strategy_registry.py` (params dict is stored as JSON text, parsed with `json.loads` — a malformed or tampered `params` JSON blob must not crash the loop or silently produce an invalid `OrderProposal`); mirror `risk_engine.py::validate_order_proposal`'s existing pattern (finite/positive numeric checks) for any hedge-engine-computed field (`entry_price`, `stop_distance_price_units`) before constructing `OrderProposal` |
| V6 Cryptography | No | No cryptographic operations in this phase |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/corrupted `params` JSON in `strategy_registry.strategies.params` column causing an unhandled exception mid-loop (denial of service against the hedge engine's own availability) | Denial of Service | Wrap `json.loads(record["params"])` in a try/except per-strategy; log and skip the malformed strategy rather than crashing the entire evaluation loop for all pairs — same "fail on one, continue on others" resilience pattern already used in `data_pipeline.py::scan_hedge_candidates` (`try/except` around `coint()` per pair) |
| A strategy record with `wf_passed=1`/`revalidated_on_real_data=1` but internally inconsistent/adversarial numeric fields (e.g., negative `win_rate`) reaching `risk_engine.resolve_kelly_inputs()` | Tampering | Not a new control needed in `hedge_engine.py` itself — `risk_engine.py`'s existing `validate_order_proposal()` (RISK-08/D-14) already rejects non-finite/non-positive `entry_price`/`stop_distance_price_units` before approval; hedge_engine.py must not attempt to bypass this by constructing `OrderProposal` fields that dodge validation (e.g., clamping negative values to a fake-positive default instead of letting the reject path fire) |
| Ambiguous/duplicate strategy rows for the same pair silently picked in a way an attacker (or a buggy re-approval) could exploit to smuggle an unvalidated parameter set into production | Tampering / Elevation of Privilege (of an unapproved param set) | D-08's explicit, logged tie-break (see Pitfall 3) is itself the mitigation — never silent first-match |
| Cointegration re-test failure (`coint()` raising an exception on a pathological price series) silently treated as "still cointegrated" (fail-open) | Tampering (of the trading decision itself) | Fail closed: any exception during `recheck_cointegration()` must be treated as `is_cointegrated=False` (or position exit if already open), never as "keep previous value" — this is the security-relevant framing of HEDGE-03's own business requirement; mirror `data_pipeline.py::scan_hedge_candidates`'s existing `try/except` pattern but ensure the *caller* treats a caught exception as a negative result, not a skip that leaves stale state in place |

## Sources

### Primary (HIGH confidence)
- `docs/hedge_engine_spec.md` — full spec for this phase's exact entry/exit logic, output record shape, pitfalls
- `.planning/phases/03-production-hedge-engine/03-CONTEXT.md` — locked numeric thresholds (D-01 through D-08) and discretion boundaries
- `src/risk_engine.py` — read in full this session; `OrderProposal`, `RiskDecision`, `evaluate_order()`, `AccountState` signatures and the fixed evaluation-order convention verified directly from source
- `src/risk_limits.py` — `RiskLimits` dataclass, `KILL_SWITCH_PATH` verified directly from source
- `src/strategy_registry.py` — read in full this session; `status`, `wf_passed`, `revalidated_on_real_data`, `cost_model_version` columns and the two-flag-never-inferred discipline verified directly from source
- `src/backtest_engine.py` — read in full this session; `compute_rolling_beta`, `compute_zscore`, cost model, `WALK_FORWARD_CONFIG` naming convention verified directly from source
- `src/data_pipeline.py` — read in full this session; `scan_hedge_candidates`'s exact `coint()` call pattern and `PipelineConfig.coint_window` default (1000) verified directly from source
- `dashboard.py` — grepped this session (lines 87-136); confirmed the `strategies.status` column's actual stored values are `"passed"`/`"failed"`, not the emoji display strings used only in the UI layer — corrects `03-CONTEXT.md` D-07's literal text (see Strategy Loading Contract)
- `.claude/skills/quant-finance-math/SKILL.md` — Engle-Granger/Kalman/Kelly reference patterns
- `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/research/SUMMARY.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/STRUCTURE.md` — project-level context, requirement IDs, prior-phase sequencing rationale
- `pip show statsmodels` [VERIFIED: local environment, this session] — statsmodels 0.14.6, confirms library currency
- `inspect.signature(coint)` [VERIFIED: local environment, this session] — confirms `coint(y0, y1, trend='c', method='aeg', maxlag=None, autolag='aic', return_results=None)` signature matches the existing call pattern in `data_pipeline.py`
- Local benchmark: `coint()` on a 1000-bar synthetic cointegrated pair [VERIFIED: local benchmark, this session] — ~73ms/call average over 10 runs, confirms D-06's 50-bar cadence has no computational-latency risk

### Secondary (MEDIUM confidence)
- None — this phase required no external web research; the project's own prior research (`.planning/research/SUMMARY.md`) had already flagged this domain as "already well-specced, skip research-phase," which this session's file-by-file verification confirmed.

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new dependencies; all libraries/functions verified directly against the installed environment and existing source files in this session
- Architecture: HIGH — every integration contract (strategy registry filter, risk engine call, cointegration re-test) verified against actual function signatures read in full this session, not assumed from documentation alone
- Pitfalls: HIGH — every pitfall listed is either explicitly documented in this project's own `docs/hedge_engine_spec.md`/`03-CONTEXT.md`/`ARCHITECTURE.md`, or derived directly from a concrete schema/signature mismatch risk identified while reading source in this session

**Research date:** 2026-07-02
**Valid until:** No external dependency changes are introduced by this phase, so this research does not have a meaningful "staleness" window in the usual sense (no library version to go stale). Re-verify only if `dashboard.py`'s approval-status string (Open Question 1) or `statsmodels`'s `coint()` signature change before planning begins.
