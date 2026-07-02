# Phase 3: Production Hedge Engine - Pattern Map

**Mapped:** 2026-07-02
**Files analyzed:** 3 (1 new source module, 1 new constants section within it, 1 new test file)
**Analogs found:** 3 / 3

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `src/hedge_engine.py` (constants: `HEDGE_PARAMS`/`COINT_RECHECK_EVERY_BARS`) | config | transform | `src/risk_limits.py` (`RiskLimits` dataclass + module docstring) | exact |
| `src/hedge_engine.py` (`evaluate_hedge_signal()`, `recheck_cointegration()`, `load_eligible_strategies()`) | service | event-driven / transform | `src/risk_engine.py` (`evaluate_order()`, `check_*` pure functions) | exact |
| `src/hedge_engine.py` (`replay_feed()`, `run_hedge_loop()`) | service | streaming | `src/backtest_engine.py::run_hedge_backtest()` (bar-index loop) + `src/data_pipeline.py::scan_hedge_candidates()` (coint reuse) | role-match |
| `src/hedge_engine.py` (strategy-loading query) | model/query | CRUD (read-only) | `src/strategy_registry.py::list_strategies()`/`get_strategy()` | exact |
| `tests/test_hedge_engine.py` | test | request-response (pure-function assertions) | `tests/test_risk_engine.py` | exact |

## Pattern Assignments

### `src/hedge_engine.py` — module docstring + constants table (config)

**Analog:** `src/risk_limits.py` (whole file, lines 1-89)

**Docstring/framing pattern** (lines 1-29):
```python
"""
risk_limits.py
===============
Tabela de constantes nomeadas para o motor de risco determinístico
(`risk_engine.py`). Segue a convenção já estabelecida em
`backtest_engine.py` (DEFAULT_COST_PARAMS/WALK_FORWARD_CONFIG): todo
limite numérico de risco é um campo nomeado, documentado, com o ID da
decisão que o originou (`D-01` a `D-14`, ver 02-CONTEXT.md) — nunca um
"magic number" solto no meio de uma expressão.
...
RISK-09 (estrutural, não apenas convenção): este módulo não importa nem
ramifica com base em `ml_model.py` ou qualquer saída de inferência de
ML. Os únicos imports permitidos são da biblioteca padrão.
"""
```
Copy this exact framing for `hedge_engine.py`'s own module docstring: state which requirement IDs it satisfies (HEDGE-01/02/03), which decision IDs (D-01..D-08 from `03-CONTEXT.md`) each constant traces to, and the structural non-negotiable ("this module never sizes/rejects for risk reasons — see HEDGE-02").

**Named-constant-table pattern** (lines 72-89):
```python
@dataclass
class RiskLimits:
    """Limites de risco determinísticos, todos com origem em decisões
    explícitas registadas em 02-CONTEXT.md. ..."""

    kelly_fraction: float = 0.25              # D-01 — fração de Kelly conservadora (extremo do intervalo 0.25x-0.5x)
    max_pair_exposure_pct: float = 0.05       # D-06 — exposição máxima por par individual, 5% do equity
    ...
    max_concurrent_pairs: int = 3             # D-08 — máximo de pares de hedge simultâneos (3 pares / 6 pernas)
```
Mirror this exactly for `HEDGE_PARAMS` (plain dict, matching `backtest_engine.py`'s `WALK_FORWARD_CONFIG` dict style since `hedge_engine.py`'s params flow into `run_hedge_backtest`-style function calls, not just risk gating):
```python
HEDGE_PARAMS = {
    "ENTRY_THRESHOLD": 2.0,          # D-01 — |z| mínimo para abrir
    "MIN_CORRELATION_ENTRY": 0.5,    # D-02 — correlação mínima para abrir
    "EXIT_THRESHOLD": 0.3,           # D-03 — |z| máximo para fechar por reversão
    "MIN_CORRELATION_EXIT": 0.4,     # D-04 — correlação mínima para MANTER; abaixo disto, fecha imediatamente
    "MAX_HOLD_BARS": 75,             # D-05 — stop de tempo (M5, ~6h15)
}
COINT_RECHECK_EVERY_BARS = 50        # D-06 — cadência de reteste de cointegração
```
Also mirror the "duplicated by hand elsewhere" warning style (risk_limits.py lines 17-24) if `hedge_engine.py` constants ever need cross-referencing with `backtest_engine.run_hedge_backtest`'s `params` dict shape — call out explicitly that `HEDGE_PARAMS` keys must stay consistent with the keys `run_hedge_backtest()` expects (`entry_threshold`, `exit_threshold`, `min_correlation`, `max_hold_bars`) if that function is reused for any replay/backtest-parity path.

---

### `src/hedge_engine.py::evaluate_hedge_signal()` (service, event-driven)

**Analog:** `src/risk_engine.py::evaluate_order()` and its `check_*` helpers (lines 629-729, 223-263)

**Pure-function signature + docstring pattern** (risk_engine.py lines 223-246):
```python
def check_drawdown_breaker(state: AccountState, limits: RiskLimits) -> tuple[bool, str | None]:
    """Disjuntor de drawdown (RISK-04). Bloqueia novas ordens quando
    qualquer um dos três limites D-locked é atingido/ultrapassado.
    O drawdown ABSOLUTO é verificado primeiro porque é uma condição de
    kill-switch PERMANENTE ...

    params:
        state  (AccountState) - ...
        limits (RiskLimits)   - ...

    devolve:
        (True, None)                              - nenhum disjuntor ativo
        (False, "absolute_drawdown_kill_switch")   - D-05: ...
    """
```
Every branch documents its exact return-tuple/dict shape and the decision ID it enforces. `evaluate_hedge_signal()` must follow the same doc convention: every returned dict variant (`open_hedge`/`close_hedge` with `trigger` field) documented with which D-id it corresponds to.

**Fixed-order, short-circuit aggregator pattern** (risk_engine.py lines 629-729, esp. 660-693):
```python
def evaluate_order(proposal: OrderProposal, state: AccountState, limits: RiskLimits,
                    correlation_matrix: dict[tuple[str, str], float],
                    kill_switch_path: str,
                    new_position_exposure_pct: float = 0.0) -> RiskDecision:
    """Agrega TODAS as verificações ..., na ordem fixa:
    validação de input -> kill-switch -> drawdown -> contagem de
    posições -> exposição -> dimensionamento + stop-loss. Uma rejeição
    em qualquer etapa interrompe a avaliação (short-circuit) ...
    """
    ...
    def _reject(reason: str) -> RiskDecision:
        return RiskDecision(approved=False, size_lots=0.0, sl_price=0.0,
                             reject_reason=reason, **base_kwargs)

    valid, reason = validate_order_proposal(proposal)
    if not valid:
        return _reject(reason)

    ok, reason = check_kill_switch(kill_switch_path)
    if not ok:
        return _reject(reason)
    ...
```
Copy this exact "fixed order, explicit reason string, short-circuit via early return" shape for `evaluate_hedge_signal()`'s exit-check ordering (D-04 correlation-breakdown MUST be checked before D-03 reversion and D-05 time-stop, per `03-CONTEXT.md` D-05's explicit override note and RESEARCH.md Pitfall 2). Use a local `_reject`-style helper (`_close(trigger, value)`) to avoid repeating dict-construction boilerplate across branches, exactly as `_reject()` is used here.

**Deliberate-comparison-operator documentation pattern** (risk_engine.py lines 40-51, module docstring):
```python
NOTA DELIBERADA sobre operadores de comparação (02-REVIEW.md, info):
os limites de EXPOSIÇÃO (D-06/D-07, `check_exposure_limits`) usam `>`
estrito ... enquanto os disjuntores de DRAWDOWN ... usam `>=` ...
Isto é intencional, não uma inconsistência a "corrigir" ...
```
`hedge_engine.py` must add the equivalent note for its own `>=` vs `<=` choices (`abs(zscore) >= ENTRY_THRESHOLD`, `abs(zscore) <= EXIT_THRESHOLD`, `correlation < MIN_CORRELATION_EXIT` — note research's Pattern 1 code example uses strict `<`, decide and document consistently, matching this project's convention of never leaving a boundary-inclusivity choice implicit).

---

### `src/hedge_engine.py::recheck_cointegration()` (service, transform)

**Analog:** `src/data_pipeline.py::scan_hedge_candidates()` (lines 204-251), reused verbatim per `03-CONTEXT.md`/RESEARCH.md

**Cointegration call pattern** (lines 224-248):
```python
try:
    score, pvalue, _ = coint(a, b)
except Exception as e:
    log.warning(f"Cointegração falhou para {sym_a}/{sym_b}: {e}")
    continue
...
rows.append({
    "pair_a": sym_a, "pair_b": sym_b,
    "correlation": round(corr, 3),
    "coint_pvalue": round(pvalue, 4),
    "is_cointegrated": pvalue < cfg.coint_pvalue_threshold,
    "hedge_ratio_beta": round(beta, 4),
    "spread_zscore": round(spread_z, 3),
})
```
`recheck_cointegration()` must reuse this exact `coint()` call signature and rounding/field-naming convention (`is_cointegrated`, `coint_pvalue`), and — per RESEARCH.md's Security Domain "fail closed" requirement — the `except Exception` branch must return `is_cointegrated=False` (not skip/keep-stale), which is a stricter behavior than `scan_hedge_candidates()`'s `continue` (that function just omits the row; `hedge_engine.py` cannot silently omit a re-test result for an open position — it must produce a negative, actionable result).

**Rolling beta/z-score reuse pattern** — `src/backtest_engine.py::compute_rolling_beta()`/`compute_zscore()` (lines 166-191):
```python
def compute_rolling_beta(price_a: pd.Series, price_b: pd.Series,
                          window: int, recalc_every: int = 50) -> pd.Series:
    """Recalcula o hedge ratio (beta) periodicamente via OLS, usando
    apenas a janela de dados ANTERIOR ao ponto atual. Entre recálculos,
    mantém o último beta conhecido (forward-fill)."""
    ...

def compute_zscore(spread: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    mean = spread.rolling(window).mean()
    std = spread.rolling(window).std()
    zscore = (spread - mean) / std
    return zscore, std
```
Import and call these directly (`from src.backtest_engine import compute_rolling_beta, compute_zscore`) — do not reimplement. This is explicitly required by `03-CONTEXT.md` code_context and flagged as an anti-pattern otherwise (backtest/production drift).

---

### `src/hedge_engine.py::run_hedge_loop()` / `replay_feed()` (service, streaming)

**Analog:** `src/backtest_engine.py::run_hedge_backtest()` bar-index loop (lines 230 onward) — structural precedent for "iterate bar-by-bar, maintain position state across iterations, evaluate entry/exit each step."

**Loop/state-per-bar shape convention** (inferred from `run_hedge_backtest`'s docstring, lines 233-256):
```python
def run_hedge_backtest(price_a: pd.Series, price_b: pd.Series, params: dict,
                        cost_params: dict | None = None,
                        score_start: int | None = None) -> dict:
    """Simula a lógica de entrada/saída de docs/hedge_engine_spec.md.

    params esperados:
        entry_threshold   (float) - |z| mínimo para abrir
        exit_threshold    (float) - |z| máximo para fechar por reversão
        min_correlation   (float) - correlação mínima para abrir/manter
        max_hold_bars     (int)   - stop de tempo
        beta_window       (int)   - janela do hedge ratio
        corr_window       (int)   - janela de correlação/z-score
        recalc_every       (int, opcional) - cadência de recálculo do beta
    """
```
`run_hedge_loop(feed, eligible_strategies, risk_evaluate_fn, coint_recheck_every=COINT_RECHECK_EVERY_BARS)` should keep this exact `params`-dict-driven signature style (not scattered keyword args) and document expected keys the same way. Position/bars-held/bars-since-coint-check state must live in local variables inside the loop function's scope (never module-level globals) — matching `risk_engine.py`'s explicit "no global state" rule (module docstring lines 19-26) which extends naturally to this new module per `03-CONTEXT.md`'s "explicit inputs, no hidden state" convention.

**No direct analog exists in this codebase for a live/replay-swappable generator feed** (`replay_feed()` as a `pd.read_parquet`-backed generator) — `data_pipeline.py` reads parquet only for batch writing, never as a streaming generator. Use RESEARCH.md's Pattern 2 code example as the primary reference for this specific piece (see "No Analog Found" below); anchor the parquet-reading mechanics (`pd.read_parquet`) to `data_pipeline.py`'s existing I/O conventions (explicit column access, no positional reliance).

---

### `src/hedge_engine.py::load_eligible_strategies()` (model/query, CRUD read)

**Analog:** `src/strategy_registry.py::list_strategies()` (lines 195-206), consumed the same way `risk_engine.py::resolve_kelly_inputs()` consumes `get_strategy()` records (lines 451-531)

**Query pattern** (lines 195-206):
```python
def list_strategies(db_path: str, status: str | None = None) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    query = "SELECT * FROM strategies"
    params = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    df = pd.read_sql(query, conn, params=params)
    conn.close()
    return df
```
`hedge_engine.py` must call `list_strategies(db_path, status="passed")` (NOT `"✅ Aprovada"` — verified emoji string is display-only, per RESEARCH.md's Strategy Loading Contract correction) then filter in pandas for both `wf_passed == 1` AND `revalidated_on_real_data == 1` — never open a raw `sqlite3.connect()` inside `hedge_engine.py` (mirrors the Don't-Hand-Roll rule already established for `risk_engine.py`'s relationship to `strategy_registry.py`).

**Two-flag-never-inferred pattern** — `risk_engine.py::resolve_kelly_inputs()` (lines 476-478):
```python
wf_passed = bool(strategy_record.get("wf_passed"))
revalidated = bool(strategy_record.get("revalidated_on_real_data"))

if wf_passed and revalidated and strategy_record.get("wf_fold_results"):
    ...
else:
    log.warning(
        "resolve_kelly_inputs: usando estatísticas de backtest in-sample "
        "(wf_passed=%s, revalidated_on_real_data=%s) — não out-of-sample "
        "walk-forward. Ver Pitfall 4 de 02-RESEARCH.md.",
        wf_passed, revalidated,
    )
```
Copy this exact `bool(record.get(...))` + explicit `AND` + explicit warning-on-fallback style for `load_eligible_strategies()`'s own filter — this is the precise discipline RESEARCH.md's Pitfall 4 calls out as already implemented correctly elsewhere in the codebase.

---

### `tests/test_hedge_engine.py` (test, request-response / pure-function assertions)

**Analog:** `tests/test_risk_engine.py` (lines 1-90 read; full-file structure)

**Suite framing docstring pattern** (lines 1-28):
```python
"""
test_risk_engine.py
=====================
Suite adversarial (RISK-08): prova que `src/risk_engine.py` rejeita
corretamente toda a classe de ordem adversarial sintética definida em
`02-CONTEXT.md` D-14 ...

Também codifica RISK-09 (independência de ML) como teste executável:
...

Estilo: fixtures de dataclass simples (`AccountState`, `RiskLimits`,
`OrderProposal`), sem framework de mocking — as funções sob teste em
`risk_engine.py` já são puras por desenho ...

Uso:
    python -m pytest tests/test_risk_engine.py -q
"""
```
`test_hedge_engine.py` should open with the equivalent framing: which HEDGE-* requirement IDs and which Pitfalls (1, 2, 3, 4, 6 from `03-RESEARCH.md`) it proves as executable tests — especially Pitfall 2 ("correlation-breakdown exit ordering") as a named test case exactly as RESEARCH.md prescribes: *"given a position with correlation below MIN_CORRELATION_EXIT AND z-score also within EXIT_THRESHOLD simultaneously, the exit reason returned must be `correlation_breakdown`, not `reversion`."*

**Fixture-helper pattern** (lines 64-90):
```python
def _healthy_account_state(open_positions: list[dict] | None = None) -> AccountState:
    """Conta saudável: sem drawdown, HWM igual ao equity atual, sem
    posições abertas por default — usada como baseline em quase todos
    os testes de rejeição, para garantir que APENAS o caso adversarial
    sob teste está a disparar a rejeição (isolamento de variável)."""
    return AccountState(...)


def _valid_proposal(**overrides) -> OrderProposal:
    """Proposta de ordem válida por default ... os testes adversariais
    fazem override de UM campo de cada vez para isolar a classe de
    rejeição sob teste."""
    base = dict(...)
```
Mirror this exact `_healthy_*()`/`_valid_*(**overrides)` baseline-fixture-with-override style for `hedge_engine.py` test fixtures (e.g., `_flat_position_state()`, `_open_position(**overrides)`), isolating one D-id per test the same way.

---

## Shared Patterns

### Named-constant-table with decision-ID citations
**Source:** `src/risk_limits.py` (whole file), `src/backtest_engine.py::WALK_FORWARD_CONFIG` (lines 535-540)
**Apply to:** `hedge_engine.py`'s `HEDGE_PARAMS` / `COINT_RECHECK_EVERY_BARS` — every numeric value must cite its `D-xx` id inline as a trailing comment, never a bare literal inside a function body.

### Pure functions, explicit inputs, no hidden I/O/global state
**Source:** `src/risk_engine.py` module docstring (lines 19-26)
**Apply to:** All of `hedge_engine.py`'s decision functions (`evaluate_hedge_signal`, `recheck_cointegration`, `load_eligible_strategies`) — only `run_hedge_loop`'s outer driver may hold explicit (non-global, function-scoped) mutable state across iterations, exactly as `check_kill_switch` is the one documented I/O exception in `risk_engine.py`.

### Fixed evaluation order, short-circuit, explicit reject/trigger reason strings
**Source:** `src/risk_engine.py::evaluate_order()` (lines 629-729)
**Apply to:** `evaluate_hedge_signal()`'s exit-check ordering (correlation-breakdown before reversion before time-stop, per D-05's override) and `load_eligible_strategies()`'s tie-break logic (D-08) — every rejection/trigger must return a named string reason, logged via `log.warning`, never silent.

### Fail-closed on statistical-test exception
**Source:** `src/data_pipeline.py::scan_hedge_candidates()` try/except around `coint()` (lines 224-228), extended per RESEARCH.md Security Domain
**Apply to:** `recheck_cointegration()` — must return `is_cointegrated=False` on any exception (stricter than `scan_hedge_candidates()`'s "skip the row"), since an open position's cointegration check has no "just omit it" option.

### Two-flag-never-inferred strategy gating
**Source:** `src/risk_engine.py::resolve_kelly_inputs()` (lines 476-478)
**Apply to:** `load_eligible_strategies()` — must check `wf_passed == 1 AND revalidated_on_real_data == 1` explicitly, both flags, with a warning log if only one is true and the record is excluded.

### Module/function docstrings citing requirement IDs and decision IDs together
**Source:** `src/risk_engine.py` and `src/risk_limits.py` docstrings throughout
**Apply to:** Every new function in `hedge_engine.py` — cite the `HEDGE-0x` requirement and the `D-0x` decision(s) it implements, plus which `03-RESEARCH.md` Pitfall (if any) it guards against, matching the density of cross-referencing already established in Phase 2's files.

## No Analog Found

| File/Piece | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `replay_feed()` (parquet-backed streaming generator yielding `dict[symbol, Bar]` per step) | utility | streaming | No existing code in this repo reads parquet as a live-like bar-by-bar generator — `data_pipeline.py` only writes parquet in batch; `backtest_engine.py` operates on whole in-memory `pd.Series`, not a generator/iterator abstraction. Use RESEARCH.md's Pattern 2 code example (already reviewed and repo-appropriate) as the primary reference for this piece; anchor only the low-level `pd.read_parquet`/index-intersection mechanics to `data_pipeline.py::load_all_symbols`'s existing shared-index convention. |
| `propose_to_risk_engine()` field-mapping shim (hedge proposal dict -> `OrderProposal`) | transform | request-response | No existing code translates between two internal dict/dataclass shapes across modules yet (Phase 2 is the first consumer of Phase 1 outputs via `resolve_kelly_inputs`, which is a read, not a shape-translation). Use RESEARCH.md's verified "Risk Engine Integration Contract" code example directly — it was built and verified in this session against the actual `OrderProposal`/`evaluate_order()` signature, so it is already the correct analog-equivalent reference. |

## Metadata

**Analog search scope:** `src/` (all modules), `tests/` (single existing test file), `docs/hedge_engine_spec.md` referenced but not re-extracted (already fully summarized in `03-CONTEXT.md`/`03-RESEARCH.md`)
**Files scanned:** `src/risk_engine.py`, `src/risk_limits.py`, `src/backtest_engine.py`, `src/data_pipeline.py`, `src/strategy_registry.py`, `tests/test_risk_engine.py`
**Pattern extraction date:** 2026-07-02
