# Phase 2: Deterministic Risk Engine - Pattern Map

**Mapped:** 2026-07-01
**Files analyzed:** 8 (new)
**Analogs found:** 5 / 8 (3 have no analog — new `mql5/` directory)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `src/risk_engine.py` | service (pure decision core) | request-response (order-in, decision-out) | `src/backtest_engine.py` | role-match (constant-table + pure-function style) |
| `src/risk_limits.py` (optional split) | config | CRUD (static config table) | `src/backtest_engine.py` (`DEFAULT_COST_PARAMS`/`WALK_FORWARD_CONFIG`) | exact (named constant-table convention) |
| `src/alerts.py` (optional split) | service | event-driven (fire-and-forget outbound alert) | none in-repo; `docs/risk_engine_mql5_spec.md` + research Code Examples | no analog — new capability |
| `tests/test_risk_engine.py` | test | batch (adversarial parametrized cases) | `src/backtest_engine.py` (`validate_strategy`, `compute_stats`) as function-under-test shape; no existing `tests/` dir found | partial (no existing pytest suite to mirror; use `strategy_registry.py`'s docstring-driven documentation style instead) |
| `src/strategy_registry.py` (modified — new migration for risk-state columns, if persisted) | model / migration | CRUD | `src/strategy_registry.py` itself (`migrate_add_cost_columns`, `migrate_add_walk_forward_columns`) | exact (self-analog, same file) |
| `mql5/RiskGuard.mqh` | middleware (pure risk-check functions) | request-response | none in codebase | no analog — first `.mqh` file; use `.claude/skills/mql5-trading-ea/SKILL.md` |
| `mql5/Tests/RiskGuardTests.mq5` | test | batch (standalone Script test runner) | none in codebase | no analog — first `.mq5` file |
| `mql5/Tests/TestLite.mqh` | utility (assertion helper) | transform | none in codebase | no analog — new minimal test harness |

## Pattern Assignments

### `src/risk_engine.py` (service, request-response)

**Analog:** `src/backtest_engine.py`

**Imports pattern** (lines 1-24 of `src/backtest_engine.py`):
```python
"""
risk_engine.py
===================
[Module docstring: 15-20 lines explaining purpose, no-lookahead/determinism
guarantee, and explicitly stating the RISK-09 ML-independence constraint —
mirror backtest_engine.py's docstring convention of explaining the "why"
up front, in Portuguese, before any code.]
"""

from __future__ import annotations

from dataclasses import dataclass
```
Note: `backtest_engine.py` imports `numpy`, `pandas`, `sklearn.model_selection.TimeSeriesSplit` because it operates on time series — `risk_engine.py`'s core decision functions do not need pandas/numpy at all (per research: "risk math itself needs no third-party library"). Keep imports minimal; only import `sqlite3`/`strategy_registry` functions if reading persisted win_rate/payoff_ratio, and `os`/`json` for kill-switch file check.

**Named constant-table pattern** (lines 46-109, `DEFAULT_COST_PARAMS` + inline provenance comments):
```python
COST_MODEL_VERSION = "placeholder-v1"  # bump sempre que os valores abaixo forem recalibrados com dados reais

DEFAULT_COST_PARAMS: dict[str, dict[str, float]] = {
    "EURUSD": {
        "spread_cost": 0.00010,       # placeholder: ~1.0 pip (pendente symbol_info() real)
        ...
    },
    ...
}
```
Mirror this exactly for `RiskLimits` in `risk_engine.py`: every numeric value (D-01 through D-14) must be a named module-level constant or dataclass field with an inline comment citing the decision ID (`# D-01`, `# D-06`, etc.) and `02-CONTEXT.md` as provenance — never a bare magic number. This is the single most important pattern to copy from this analog.

**Pure-function decision core pattern** (lines 482-506, `validate_strategy`):
```python
def validate_strategy(stats: dict, thresholds: dict | None = None) -> tuple[bool, list[str]]:
    th = thresholds or DEFAULT_THRESHOLDS
    reasons = []

    if stats["total_trades"] < th["min_trades"]:
        reasons.append(
            f"poucos trades ({stats['total_trades']} < {th['min_trades']}) "
            f"- amostra pequena demais para confiar no resultado"
        )
    ...
    return (len(reasons) == 0), reasons
```
Copy this `(bool, list[str])` or `(bool, str | None)` return shape for every risk-check function (`check_kill_switch`, `check_drawdown_breaker`, `check_exposure_limits`, `check_position_count`) — no exceptions for rejection, no hidden I/O, explicit `reasons`/`reject_reason` list/string, composable into a single top-level `evaluate_order()` that aggregates all checks (same shape as `RESEARCH.md`'s `RiskDecision` output).

**Docstring convention for a function with subtle unit/ordering pitfalls** (lines 230-273, `run_hedge_backtest` docstring):
Copy the exhaustive "params esperados" / "devolve" / "levanta" (raises) docstring block style — every risk-check function should document its expected input dict/dataclass shape, return shape, and any `ValueError`/`AssertionError` conditions explicitly, the same way `resolve_cost_params()` (lines 112-159) documents its `ValueError` for `reference_lot_size` mismatch. This is directly relevant since `risk_engine.py` has several similar "silent unit-mismatch" risks (drawdown percentage base, exposure percentage base) that deserve the same defensive-assert treatment seen at lines 377-380.

---

### `src/risk_limits.py` (config, CRUD/static)

**Analog:** `src/backtest_engine.py` — `WALK_FORWARD_CONFIG` / `FOLD_THRESHOLDS` (lines 535-552)

```python
WALK_FORWARD_CONFIG = {
    "n_splits": 5,            # nº de folds sequenciais out-of-sample
    "max_train_size": 5000,   # tamanho FIXO da janela de treino (barras) -> rolling, não ancorado
    "gap": 0,                 # sem gap treino/teste: ...
    "window_type": "rolling",  # documentado explicitamente para não masquerade como ancorado
}
```
Mirror this exact shape for a `RISK_LIMITS` dict or `RiskLimits` dataclass (research's Pattern 1 already sketches the dataclass form) — each field gets an inline comment citing its decision ID:
```python
@dataclass
class RiskLimits:
    kelly_fraction: float = 0.25              # D-01
    max_pair_exposure_pct: float = 0.05       # D-06
    max_aggregate_exposure_pct: float = 0.15  # D-07
    daily_drawdown_pct: float = 0.03          # D-03
    weekly_drawdown_pct: float = 0.08         # D-04
    absolute_drawdown_pct: float = 0.20       # D-05
    max_concurrent_pairs: int = 3             # D-08
    alert_threshold_pct_of_limit: float = 0.80  # D-11
    heartbeat_timeout_seconds: int = 30       # D-12
```

---

### `src/strategy_registry.py` (model/migration — only if risk state persisted)

**Analog:** itself — `migrate_add_walk_forward_columns` (lines 66-99)

```python
def migrate_add_walk_forward_columns(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
    new_cols = {
        "wf_passed": "INTEGER",
        "wf_fold_results": "TEXT",
        "revalidated_on_real_data": "INTEGER",
    }
    for col, coltype in new_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE strategies ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()
```
If `risk_engine.py` needs to persist drawdown high-water-mark or kill-switch state across restarts (research flags this as a discretion point, JSON file vs. SQLite), reuse this exact idempotent `migrate_add_*_columns()` + `PRAGMA table_info()` pattern rather than inventing a new migration mechanism, and call the new migration from `init_db()` exactly as the two existing migrations are chained (lines 102-111). If a flat JSON file is chosen instead (simpler, matches D-13's file-based convention), this analog does not apply and no `strategy_registry.py` changes are needed.

**Read pattern for Kelly inputs** (lines 195-217, `list_strategies` / `get_strategy`):
```python
def get_strategy(db_path: str, strategy_id: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    conn.close()
    if row is None:
        return None
    return dict(zip(cols, row))
```
`risk_engine.py`'s Kelly-input reader should call `get_strategy()` as-is (no new registry function needed) and then apply Pitfall 4's out-of-sample gate in `risk_engine.py` itself: prefer `wf_fold_results`'s aggregate stats when `wf_passed=1 and revalidated_on_real_data=1`, else fall back to raw `win_rate`/`profit_factor` with an explicit logged warning — this branching belongs in the new file, not in `strategy_registry.py`.

---

### `mql5/RiskGuard.mqh` (middleware, request-response) — NO CODEBASE ANALOG

No existing `.mqh`/`.mq5` file exists in this repo (`mql5/` currently contains only `.gitkeep`). Pattern source is exclusively `.claude/skills/mql5-trading-ea/SKILL.md` (already read in full) and `02-RESEARCH.md`'s Pattern 1/2. Key excerpts to follow:

**Pure-function signature style** (skill file lines 92-124, adapted per research Pitfall 2 — must NOT call `AccountInfoDouble`/`PositionsTotal` inside decision functions):
```mql5
bool CheckDrawdownBreaker(double equity, double dailyStartEquity,
                           double weeklyStartEquity, double absoluteHWM,
                           double dailyDDPct, double weeklyDDPct, double absoluteDDPct,
                           string &rejectReason)
{
    double dailyDD = (dailyStartEquity - equity) / dailyStartEquity;
    ...
    if(absoluteDD >= absoluteDDPct) { rejectReason = "absolute_drawdown_kill_switch"; return false; }
    ...
    return true;
}
```

**Lot normalization** (skill file lines 32-48) — copy verbatim, this is already the project's documented pattern:
```mql5
double NormalizeLot(string symbol, double lot)
{
    double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
    double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
    double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
    lot = MathRound(lot / lotStep) * lotStep;
    lot = MathMax(minLot, MathMin(maxLot, lot));
    return lot;
}
```

**Kill-switch check** (skill file lines 116-120):
```mql5
if(FileIsExist("KILL_SWITCH.flag", FILE_COMMON))
{
    Print("KILL SWITCH ATIVO: nenhuma ordem nova será executada.");
    return false;
}
```

**Hedging-mode check** (skill file lines 18-24) — belongs in a thin wrapper, not the pure decision core, but must exist:
```mql5
if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
{
    Print("ERRO: conta não está em modo hedging. EA não pode operar com segurança.");
    ExpertRemove();
}
```

---

### `mql5/Tests/RiskGuardTests.mq5` + `mql5/Tests/TestLite.mqh` — NO CODEBASE ANALOG

First MQL5 Script/test-harness file in this project. Pattern source is `02-RESEARCH.md` Pattern 2 (already cites `mql5.com/en/articles/19154`):
```mql5
#include <RiskGuard.mqh>
#include "TestLite.mqh"

void OnStart()
{
    CTestLite test("RiskGuardTests");
    string reason;
    bool passed = CheckDrawdownBreaker(8000.0, 9800.0, 9500.0, 10000.0, 0.03, 0.08, 0.20, reason);
    test.AssertTrue(!passed, "20% drawdown from HWM must reject order");
    test.AssertStringEquals("absolute_drawdown_kill_switch", reason);
    test.PrintSummary();
}
```
No MQLUnit/MTUnit adoption (see RESEARCH.md "Don't Hand-Roll") — hand-roll `TestLite.mqh` as a few dozen lines: `AssertTrue`, `AssertStringEquals`, `AssertNearDouble`, pass/fail counters, `PrintSummary()`.

---

### `tests/test_risk_engine.py` (test, batch/adversarial)

**Analog:** `src/backtest_engine.py`'s `validate_strategy`/`compute_stats` as functions-under-test (no existing `tests/` directory or pytest suite found in repo — this is the first pytest file).

Since there's no existing pytest convention to mirror directly, follow the project's general docstring/comment discipline (Portuguese domain language, exhaustive docstrings) and structure test cases directly from D-14's adversarial list:
```python
"""
test_risk_engine.py
=====================
Suite adversarial (RISK-08): testa risk_engine.py contra ordens sintéticas
adversariais — lotes excessivos, ordens duplicadas, símbolos inválidos,
lotes negativos/zero, e violações de cada limite (drawdown, exposição,
contagem de posições) individualmente e em combinação (D-14).
"""
import pytest
from risk_engine import evaluate_order, RiskLimits, AccountState

def test_absolute_drawdown_triggers_kill_switch():
    ...

def test_oversized_lot_rejected():
    ...

def test_duplicate_order_rejected():
    ...
```
Use plain `dataclass` fixtures (`AccountState`, `RiskLimits`) as parameters — no mocking framework needed, consistent with the pure-function design mandated by Pitfall 2/RISK-07.

---

### `src/alerts.py` (service, event-driven) — NO CODEBASE ANALOG

No existing alerting/notification code in this repo. Pattern source is `02-RESEARCH.md`'s "Alternatives Considered" (zero-dependency `requests.post` recommended over `python-telegram-bot` for this phase) plus the project's existing logging convention (`log = logging.getLogger(...)`, lines from `data_pipeline.py` per `.claude/CLAUDE.md`'s "Logging" section) for the success/failure log messages (never log the full token).

## Shared Patterns

### Named, documented constants (never magic numbers)
**Source:** `src/backtest_engine.py` lines 46-109 (`DEFAULT_COST_PARAMS`), lines 474-479 (`DEFAULT_THRESHOLDS`), lines 535-552 (`WALK_FORWARD_CONFIG`/`FOLD_THRESHOLDS`)
**Apply to:** `src/risk_engine.py`, `src/risk_limits.py` — every D-01–D-14 numeric value must be a named constant/dataclass field with inline comment citing the decision ID and `02-CONTEXT.md`.

### Pure-function decision core, explicit inputs, no hidden I/O/state
**Source:** `src/backtest_engine.py` (`compute_rolling_beta`, `compute_zscore`, `validate_strategy`, `compute_stats` — all take explicit params, return structured data, no global reads)
**Apply to:** `src/risk_engine.py`'s risk-check functions AND `mql5/RiskGuard.mqh`'s risk-check functions (per research Pitfall 2/RISK-07 — this is a cross-language shared discipline, not just a Python convention).

### Idempotent additive SQLite migration
**Source:** `src/strategy_registry.py` lines 44-99 (`migrate_add_cost_columns`, `migrate_add_walk_forward_columns`) — `PRAGMA table_info()` check + conditional `ALTER TABLE`, chained from `init_db()`.
**Apply to:** Only if `risk_engine.py` persists state (drawdown HWM, kill-switch history) in SQLite rather than flat JSON — planner discretion per research Assumption A3.

### Structured `(bool, reasons)` / `(approved, reject_reason)` return shape
**Source:** `src/backtest_engine.py` lines 482-506 (`validate_strategy` returns `tuple[bool, list[str]]`)
**Apply to:** Every risk-check function in `risk_engine.py`, and the top-level `evaluate_order()` aggregator, matching research's proposed `RiskDecision {approved, size_lots, sl_price, reject_reason}` shape.

### ML-independence structural enforcement (RISK-09)
**Source:** No existing code pattern (new constraint) — `.planning/research/PITFALLS.md` Pitfall 4 (project-level) + this phase's own Pitfall 1.
**Apply to:** `src/risk_engine.py` and `mql5/RiskGuard.mqh` — add an explicit verification step (grep for `import ml_model`/`from ml_model` in the Python file, grep for `ml_model` string references in the `.mqh` file) as part of this phase's own test/verification, not deferred.

### Kelly criterion (verbatim, do not re-derive)
**Source:** `.claude/skills/quant-finance-math/SKILL.md` lines 102-114
```python
def kelly_fraction(win_rate: float, payoff_ratio: float, fraction: float = 0.3) -> float:
    b = payoff_ratio
    p = win_rate
    q = 1 - p
    f_star = (b * p - q) / b
    return max(0.0, f_star * fraction)
```
**Apply to:** `src/risk_engine.py`'s position-sizing function — use `fraction=0.25` (D-01) as the project-specific override of the skill's example default of `0.3`.

### Docstring discipline (Portuguese, exhaustive, "why" not "what")
**Source:** Every module in `src/` — module docstrings 15-25 lines explaining purpose/constraints; function docstrings document "params esperados", "devolve", "levanta" explicitly (see `run_hedge_backtest`, `resolve_cost_params`, `save_walk_forward_result`).
**Apply to:** All new Python files this phase.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `mql5/RiskGuard.mqh` | middleware | request-response | `mql5/` is a brand-new directory (only `.gitkeep` present) — first `.mqh` file in this project. Use `.claude/skills/mql5-trading-ea/SKILL.md` (already read in full) as the pattern source instead of a codebase analog. |
| `mql5/Tests/RiskGuardTests.mq5` | test | batch | First `.mq5` Script in this project; no MQL5 test infra exists. Use `02-RESEARCH.md` Pattern 2 (standalone Script + TestLite.mqh) as the pattern source. |
| `mql5/Tests/TestLite.mqh` | utility | transform | New minimal assertion helper; no existing MQL5 test-utility code to mirror. Hand-roll per research's "Don't Hand-Roll" guidance (reject MQLUnit/MTUnit adoption). |
| `src/alerts.py` | service | event-driven | No existing alerting/notification code anywhere in `src/`. Use research's Telegram Code Examples / Alternatives Considered (`requests.post` to Bot API) as the pattern source; follow project's existing `logging` conventions for the surrounding log statements only. |

## Metadata

**Analog search scope:** `src/` (all `.py` files), `.claude/skills/` (both skill files), `mql5/` (confirmed empty except `.gitkeep`), repo root for a `tests/` directory (none found)
**Files scanned:** `src/backtest_engine.py`, `src/strategy_registry.py`, `src/data_pipeline.py` (docstring/logging convention reference only, not separately excerpted above), `.claude/skills/quant-finance-math/SKILL.md`, `.claude/skills/mql5-trading-ea/SKILL.md`
**Pattern extraction date:** 2026-07-01
