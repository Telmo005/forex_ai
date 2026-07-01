---
status: reviewed
phase_dir: .planning/phases/02-deterministic-risk-engine
diff_base: 1bed7fa
files_reviewed:
  - src/risk_engine.py
  - src/risk_limits.py
  - tests/test_risk_engine.py
  - mql5/RiskGuard.mqh
  - mql5/Tests/TestLite.mqh
  - mql5/Tests/RiskGuardTests.mq5
findings:
  critical: 0
  warning: 3
  info: 5
  total: 8
---

# Phase 2 Code Review: Deterministic Risk Engine

## Scope

All six files are net-new in this phase (`git diff 1bed7fa --stat` shows only
additions, no prior version existed). Reviewed at **standard** depth, with
extra scrutiny on: numeric parity between `src/risk_limits.py` and
`mql5/RiskGuard.mqh`; ML-independence (RISK-09); kill-switch/drawdown
semantics never closing existing positions (RISK-06); boundary conditions on
the 3-pair cap and drawdown/exposure comparisons; adversarial test fidelity;
and MQL5 purity (no hidden `AccountInfoDouble`/`PositionsTotal`/
`SymbolInfoDouble` calls inside decision functions).

`python -m pytest tests/test_risk_engine.py -q` → **22 passed** (confirmed
by re-running, not just reading assertions).

## Summary

The module is well-disciplined and matches its own stated design goals in
the large majority of cases: constants are D-locked, documented, and
numerically identical between Python and MQL5; the kill-switch/drawdown
breakers are structurally incapable of touching `open_positions` (no code
path writes to or closes anything); RISK-09 independence is real (verified
by direct import-list inspection, not just the self-test); and the MQL5
decision functions are genuinely pure (no `AccountInfoDouble`/
`PositionsTotal`/`SymbolInfoDouble` calls anywhere except the two functions
explicitly and correctly carved out as broker-spec normalizers). No critical
issues found. Three warnings worth fixing before this becomes the basis for
Phase 3/4 integration, plus a handful of info-level notes.

## Constant parity check (Python vs MQL5)

Verified every D-locked value side by side:

| Constant | risk_limits.py | RiskGuard.mqh | Match |
|---|---|---|---|
| D-01 kelly_fraction | 0.25 | `RISKGUARD_KELLY_FRACTION` 0.25 | ✅ |
| D-03 daily_drawdown_pct | 0.03 | `RISKGUARD_DAILY_DRAWDOWN_PCT` 0.03 | ✅ |
| D-04 weekly_drawdown_pct | 0.08 | `RISKGUARD_WEEKLY_DRAWDOWN_PCT` 0.08 | ✅ |
| D-05 absolute_drawdown_pct | 0.20 | `RISKGUARD_ABSOLUTE_DRAWDOWN_PCT` 0.20 | ✅ |
| D-06 max_pair_exposure_pct | 0.05 | `RISKGUARD_MAX_PAIR_EXPOSURE_PCT` 0.05 | ✅ |
| D-07 max_aggregate_exposure_pct | 0.15 | `RISKGUARD_MAX_AGGREGATE_EXPOSURE_PCT` 0.15 | ✅ |
| D-08 max_concurrent_pairs | 3 | `RISKGUARD_MAX_CONCURRENT_PAIRS` 3 | ✅ |
| D-09 kill switch filename | `"KILL_SWITCH.flag"` | `RISKGUARD_KILL_SWITCH_FILENAME` `"KILL_SWITCH.flag"` | ✅ |
| D-11 alert_threshold_pct_of_limit | 0.80 | `RISKGUARD_ALERT_THRESHOLD_PCT` 0.80 | ✅ |
| D-12 heartbeat_timeout_seconds | 30 | `RISKGUARD_HEARTBEAT_TIMEOUT_SECONDS` 30 | ✅ |

Comparison-operator parity also holds *within* each language (not just the
constant values): drawdown breaker and position-count use `>=` on both
sides; exposure checks use strict `>` on both sides. RISK-07's "redundant
independent verification" guarantee is intact — no numeric or operator
drift found.

## Findings

### [WARNING] Correlation-adjusted exposure only compares `pair_a` legs, silently ignoring `pair_b`

**File:** `src/risk_engine.py:277-313` (`aggregate_exposure_pct`)

The pairwise correlation lookup only ever compares `a["pair_a"]` against
`b["pair_a"]` for every combination of open positions:

```python
corrs = [
    abs(correlation_matrix.get((a["pair_a"], b["pair_a"]), 0.0))
    for a, b in pairs
]
```

A hedge position is defined by *two* legs (`pair_a`, `pair_b`), but the
correlation-adjustment step never looks at `pair_b` at all — neither
`pair_b`-vs-`pair_b`, nor `pair_a`-vs-`pair_b` cross terms. Concretely: two
open hedge positions `(EURUSD, GBPUSD)` and `(USDCHF, EURGBP)` could be
highly correlated through their `pair_b` legs (`GBPUSD` vs `EURGBP`) or
cross legs, and this function would report `avg_corr = 0.0` (falling back
to the correlation-matrix default) purely because it never queries those
symbol combinations — silently under-stating aggregate risk exactly where
D-07's "correlated pairs count more" guarantee is supposed to bite hardest.

Additionally, the lookup is one-directional: `correlation_matrix.get((a, b))`
is tried, but not `(b, a)`. If the caller's correlation matrix (built from
`data_pipeline.py`'s cointegration scan) is not populated symmetrically for
both key orders, real correlation gets silently treated as 0.0.

Confirmed empirically:
```
position pairs: [(EURUSD/GBPUSD @0.04, AUDUSD/NZDUSD @0.04)]
correlation_matrix = {('EURUSD','AUDUSD'): 0.9}
corrs looked up (pair_a vs pair_a only) = [0.9]   # happens to work here
```
This specific test case in `tests/test_risk_engine.py:327-351` passes only
because the constructed example conveniently puts the correlated symbols in
the `pair_a` slot of both positions — it does not exercise the `pair_b` or
reverse-lookup gap, so the test suite would not catch a regression or a
real-world matrix where the correlated leg happens to sit in `pair_b`.

**Recommendation:** Either (a) require/enforce that `correlation_matrix` is
pre-symmetrized by the caller and extend the comparison to consider both
legs of each position (e.g., max or average correlation across all four
leg-combinations `a.pair_a`/`a.pair_b` × `b.pair_a`/`b.pair_b`), or (b) if
the current pair_a-only behavior is intentional (e.g., `pair_a` is always
the "primary"/quote-driving symbol by convention), document that invariant
explicitly in the docstring and add a test where the correlated symbol sits
in `pair_b` to prove the intentional scope, not an accidental gap.

### [WARNING] `new_position_exposure_pct` is never validated for negative/NaN/inf input

**File:** `src/risk_engine.py:316-350` (`check_exposure_limits`), `513-539`
(`validate_order_proposal`)

`validate_order_proposal` — the "first line of defense against adversarial
input" per its own docstring — validates `entry_price`,
`stop_distance_price_units`, `direction`, and the two symbols, but never
touches `new_position_exposure_pct`, which is a separate parameter threaded
through `check_exposure_limits`/`evaluate_order` rather than a field on
`OrderProposal`. Neither `check_exposure_limits` nor `evaluate_order`
guards this value with `math.isfinite()` or a `>= 0` check the way every
other numeric input in this module is guarded.

Consequences:
- A negative `new_position_exposure_pct` passes the `> limits.max_pair_exposure_pct`
  gate trivially (it's negative, so it's always "under" the limit), then
  gets added as a position dict into `projected_positions` and summed
  in `aggregate_exposure_pct`'s `raw_sum`. A large negative value could mask
  genuinely excessive existing exposure by pulling the aggregate below the
  D-07 threshold.
- `NaN` would fail every `>` and `>=` comparison silently (Python `NaN > x`
  is always `False`), meaning a `NaN` exposure would sail through both the
  per-pair and aggregate checks as if it were a compliant, tiny number —
  the exact class of adversarial-input bug D-14 says this suite must cover
  ("lotes/exposições excessivos... negative/zero"), but this specific field
  is untested for exactly this class.

**Recommendation:** Add an explicit `math.isfinite(new_position_exposure_pct)
and new_position_exposure_pct >= 0` guard in `evaluate_order`/
`check_exposure_limits` (or promote it into a validated field checked by
`validate_order_proposal`), with a corresponding adversarial test
(`test_negative_or_nan_new_position_exposure_rejected`).

### [WARNING] `check_exposure_limits` (MQL5) trusts a pre-aggregated `aggregateAdjustedPct` parameter from the caller

**File:** `mql5/RiskGuard.mqh:133-154` (`CheckExposureLimits`)

The function signature is:
```cpp
bool CheckExposureLimits(double &perPairExposurePcts[], double aggregateAdjustedPct,
                          double maxPairPct, double maxAggPct, string &rejectReason)
```
Unlike `CheckDrawdownBreaker` (which recomputes `dailyDD`/`weeklyDD`/
`absoluteDD` internally from raw equity inputs) and `CheckPositionCount`
(which takes a raw integer count), `CheckExposureLimits` takes
`aggregateAdjustedPct` as an already-fully-computed scalar — the
correlation-adjustment arithmetic itself (variance-scaling per D-07) is
never performed in MQL5 at all; it happens once, in Python
(`aggregate_exposure_pct`), and RiskGuard.mqh just compares the result
against a threshold. This is explicitly acknowledged in the file's own
comment (`"a agregação em si... já deve ter sido aplicada pelo chamador
antes de invocar esta função"`), so it is a known, documented tradeoff — but
it does mean RISK-07's "redundant independent verification" guarantee is
weaker for D-07 specifically than for D-03/D-04/D-05/D-08: if the *Python*
correlation-adjustment arithmetic has a bug (see the finding above), the
MQL5 side has no independent way to catch it, because it never recomputes
the adjustment itself — it only re-checks the two raw threshold
comparisons.

**Recommendation:** Not necessarily a blocker for this phase (documented as
intentional scope — the EA doesn't have the project's cointegration/
correlation matrix), but worth flagging explicitly in
`docs/risk_engine_mql5_spec.md` or a future Phase 4 task: the EA should
recompute `aggregateAdjustedPct` itself from its own per-pair exposures and
*its own* correlation snapshot (refreshed periodically from Python via the
file bridge), not simply accept a single pre-computed float, or the
"last-line-of-defense" guarantee for D-07 reduces to "trust Python's
arithmetic."

## Info-level notes (not blocking)

### [INFO] Comparison-operator asymmetry between exposure (`>`) and drawdown/position-count (`>=`) is internally consistent but undocumented as a deliberate choice

Exposure limits use strict `>` (exactly 5.00% or 15.00% is *allowed*),
while drawdown breakers and position count use `>=` (exactly at the limit
*rejects*). Both languages agree with each other on this per-check basis,
so RISK-07 parity holds, and the asymmetry is plausible by design (exposure
is a continuous sizing input that will rarely land on the exact boundary,
while position count is a discrete integer where "the 4th position" must be
blocked). Still, neither `risk_engine.py` nor `RiskGuard.mqh` states this
asymmetry is intentional anywhere in the docstrings/comments — worth one
line of documentation so a future reader doesn't "fix" it into false
consistency.

### [INFO] D-14 adversarial coverage: "oversized lots" and "duplicate orders" are covered by analogy, not literally

`test_oversized_position_exposure_rejected` and
`test_duplicate_order_submission_rejected` both candidly document (in their
own comments) that they're testing the closest Python-side equivalent of
"oversized lot"/"duplicate order," since `risk_engine.py` doesn't operate on
concrete lot sizes or maintain submission-dedup state — that's MQL5/
execution-layer responsibility (RISK-07). This is honest and intentional,
not a hidden gap, but it means the literal "lot in lots, duplicated order"
cases from D-14 are only actually exercised in
`mql5/Tests/RiskGuardTests.mq5` (Case 5, `NormalizeLot` clamp test) and not
at all for duplicate-order detection on the MQL5 side either (no dedup
mechanism exists yet anywhere in the reviewed files — deferred, presumably,
to the Phase 3/4 file-bridge idempotency key design). No action needed this
phase; flagging so it isn't lost track of before Phase 4.

### [INFO] `resolve_kelly_inputs` payoff_ratio fallback can silently divide near-zero, non-zero `avg_loss_r`

**File:** `src/risk_engine.py:442-447`

```python
avg_win_r = strategy_record.get("avg_win_r")
avg_loss_r = strategy_record.get("avg_loss_r")
if avg_win_r is not None and avg_loss_r not in (None, 0):
    payoff_ratio = abs(float(avg_win_r) / float(avg_loss_r))
```
The `avg_loss_r not in (None, 0)` guard only excludes an exact `0`/`None`;
a tiny non-zero `avg_loss_r` (e.g. `1e-12`, plausible from a rounding
artifact upstream in `strategy_registry.py`) would produce an enormous
`payoff_ratio`, which then flows straight into `kelly_fraction` and could
produce an oversized (though still `>= 0`-floored) position size. Low risk
in practice since `size_position`'s result is still just a *fraction*
capped only by the caller's downstream logic (not capped here at all,
notably — see next note), but worth a sanity floor (e.g., reject or clamp
if `abs(avg_loss_r) < some_epsilon`).

### [INFO] `size_position`/`kelly_fraction` output is never capped against `max_pair_exposure_pct`

**File:** `src/risk_engine.py:452-467`, `605-607`

`evaluate_order` computes `size_fraction = size_position(...)` (fractional
Kelly) and, if positive, approves the order with `size_lots=size_fraction`
— but this Kelly-derived sizing fraction is never cross-checked against
`limits.max_pair_exposure_pct` (D-06, 5%). The per-pair exposure gate
(`check_exposure_limits`) only validates the *caller-supplied*
`new_position_exposure_pct` (an input describing the proposed position's
exposure), which is logically a separate number from the Kelly-derived
`size_fraction` that ultimately gets returned as `size_lots`. Nothing in
this module enforces that the two are consistent with each other — a
caller could pass a tiny `new_position_exposure_pct` (to pass the gate)
while `win_rate`/`payoff_ratio` inputs drive Kelly sizing to a much larger
fraction, and `evaluate_order` would approve that larger `size_lots`
without re-checking it against D-06. This may be intentional given
`size_lots`'s docstring ("fração do capital a arriscar, ainda não
convertida em lotes concretos") implies further downstream conversion and
capping happens elsewhere (Phase 3/4), but it's worth an explicit assert or
comment tying `size_fraction` back to the exposure limits it's supposed to
respect, since right now the connection between "Kelly-sized risk fraction"
and "D-06/D-07 exposure limits" is only enforced for the caller-supplied
proxy value, not the actual returned size.

### [INFO] `AccountState`/`RiskDecision` `Any` import unused

**File:** `src/risk_engine.py:46`

`from typing import Any` is imported but `Any` does not appear to be used
anywhere in the file's type hints (all fields use concrete types:
`float`, `str`, `int`, `list[dict]`, etc.). Harmless (no functional impact,
no security implication) but a minor lint/cleanliness item — either use it
or drop the import.

## What was verified clean (worth stating explicitly, given the safety criticality)

- **RISK-09 (ML independence):** Confirmed via direct read of the import
  lists of `src/risk_engine.py` and `src/risk_limits.py` — only stdlib
  (`logging`, `math`, `dataclasses`, `typing`, `os` inline in
  `check_kill_switch`, `json` inline in `resolve_kelly_inputs`) plus the
  intra-module `from src.risk_limits import RiskLimits`. No `ml_model`
  import, no `confidence`/`probability`-named branch anywhere. Same
  confirmed for `mql5/RiskGuard.mqh` by full read — no reference to any ML
  or pattern-detection module.
- **RISK-06 (kill-switch/drawdown never closes positions):** Every
  drawdown/kill-switch function in both `risk_engine.py` and
  `RiskGuard.mqh` only returns a `(bool, reason)` decision; none writes to
  `state.open_positions`, calls any close/liquidate primitive, or has any
  side effect beyond an optional `log.warning`/`Print`. Verified by reading
  every function body top to bottom, not just the docstrings' claims.
- **MQL5 purity:** `CheckDrawdownBreaker`, `CheckExposureLimits`,
  `CheckPositionCount`, `CheckKillSwitch` (except its one documented
  `FileIsExist` call), and `CheckMandatorySL` take 100% explicit parameters
  and make zero calls to `AccountInfoDouble`/`PositionsTotal`/
  `SymbolInfoDouble` internally. Only `NormalizeLot`/`MinStopDistance` call
  `SymbolInfoDouble`/`SymbolInfoInteger`, and both are explicitly and
  correctly carved out in the file's own header comment as broker-spec
  normalizers, not risk decisions — consistent with the phase's pure-function
  design requirement.
- **3-pair boundary (D-08):** `check_position_count`
  (`len(open_positions) >= max_concurrent_pairs`) and MQL5's
  `CheckPositionCount` (`openPairs >= maxPairs`) both correctly allow a 3rd
  position (2 open → `2 >= 3` false → allowed) and reject a 4th (3 open →
  `3 >= 3` true → rejected). Confirmed both by static reading and by the
  passing `test_fourth_concurrent_pair_rejected` /
  `test_third_concurrent_pair_still_allowed_by_position_count_check` pair
  of tests, which deliberately isolate this boundary from other checks.
- **Drawdown severity ordering:** absolute → daily → weekly is the fixed
  check order in both languages, and `test_absolute_drawdown_checked_before_daily_and_weekly`
  / MQL5 Case 1 both prove the most severe (permanent kill-switch)
  condition is surfaced first when multiple breakers would fire
  simultaneously.
- **Stop-loss mandatory guarantee (RISK-01):** `compute_stop_loss_price`
  raises `ValueError` for non-finite/non-positive `entry_price` or
  `stop_distance_price_units`, and `evaluate_order` catches that and
  converts it into a `reject_reason="invalid_stop_loss"` rather than ever
  returning `approved=True` with `sl_price=0.0` — contract is upheld and
  directly tested (`test_order_rejected_as_non_positive_edge_when_kelly_sizing_is_zero`
  and the general approval-path tests assert `sl_price > 0.0`).
- **Test suite executes and passes:** re-ran
  `python -m pytest tests/test_risk_engine.py -q` directly — 22/22 pass,
  not just inferred from reading assertions.
