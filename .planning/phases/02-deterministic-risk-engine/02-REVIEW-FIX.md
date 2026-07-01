---
status: all_fixed
phase_dir: .planning/phases/02-deterministic-risk-engine
review_path: .planning/phases/02-deterministic-risk-engine/02-REVIEW.md
iteration: 1
findings_in_scope:
  critical: 0
  warning: 3
  info: 5
  total: 8
fixed:
  - warning_1_correlation_pair_b_and_reverse_lookup
  - warning_2_exposure_input_validation
  - warning_3_mql5_exposure_pre_aggregation_documented
  - info_1_comparison_operator_asymmetry_documented
  - info_2_d14_lot_dedup_coverage_by_analogy_confirmed_no_action
  - info_3_avg_loss_r_epsilon_floor
  - info_4_kelly_size_lots_clamped_to_d06
  - info_5_unused_any_import_removed
skipped: []
commits:
  - 80bf16e fix(02): consider both hedge legs and reverse key order in correlation-adjusted exposure
  - 99bf982 fix(02): reject NaN/inf/negative new_position_exposure_pct before exposure gates
  - 0c30762 fix(02): document CheckExposureLimits pre-aggregated-input as an accepted, tracked exception
  - 3ca5500 fix(02): address remaining info-level findings from 02-REVIEW.md
test_suite: "python -m pytest tests/test_risk_engine.py src/backtest_engine_test.py -q -> 50 passed"
---

# Phase 2 Code Review Fix Report (iteration 1)

All 8 findings from `02-REVIEW.md` (0 critical, 3 warning, 5 info) were
addressed. Test suite grew from 22 to 32 tests in `tests/test_risk_engine.py`
(all passing), and the combined required suite
(`tests/test_risk_engine.py` + `src/backtest_engine_test.py`) passes at
**50/50**.

## Warning 1 — `aggregate_exposure_pct` only compared `pair_a` legs, ignored `pair_b` and reverse key order

**File:** `src/risk_engine.py` (`aggregate_exposure_pct`, `_lookup_correlation`)

Fixed by:
- Adding `_lookup_correlation(correlation_matrix, x, y)`, a bidirectional
  lookup that tries both `(x, y)` and `(y, x)` key orders before falling
  back to `0.0`. Confirmed via `data_pipeline.py::scan_hedge_candidates`
  that the correlation matrix produced by Layer 0 populates only one row
  per unordered symbol pair, so a caller building `correlation_matrix`
  from that output cannot guarantee both key orders — the one-directional
  lookup was a real, not theoretical, gap.
- Extending the per-position-pair correlation used in
  `aggregate_exposure_pct` to `max()` across all four leg combinations
  (`a.pair_a`/`b.pair_a`, `a.pair_a`/`b.pair_b`, `a.pair_b`/`b.pair_a`,
  `a.pair_b`/`b.pair_b`) instead of only `a.pair_a`/`b.pair_a`. Using the
  max (not average) across legs is the conservative reading correct for a
  risk limit — if any single leg-pair is strongly correlated, the
  co-movement risk already exists regardless of the other three
  combinations.

**Tests added** (`tests/test_risk_engine.py`):
- `test_aggregate_exposure_detects_correlation_hidden_in_pair_b_leg` — same
  numeric scenario as the existing `test_aggregate_correlation_adjusted_
  exposure_above_fifteen_percent_rejected`, but the correlated symbol sits
  in the `pair_b` slot of the second position. This is exactly the case
  the review confirmed the old code silently missed (would have approved
  the order instead of rejecting it).
- `test_aggregate_exposure_detects_correlation_with_reversed_matrix_key_
  order` — same scenario, but the correlation matrix is keyed
  `(AUDUSD, EURUSD)` instead of `(EURUSD, AUDUSD)`, proving the reverse
  key-order lookup works.

Both new tests fail against the pre-fix code (verified logically: the old
one-directional, `pair_a`-only lookup returns `0.0` for both scenarios,
so `aggregate` would stay at the raw sum, `0.08`, well under the 15%
D-07 limit, and the order would be wrongly approved).

## Warning 2 — `new_position_exposure_pct` never validated for negative/NaN/inf

**File:** `src/risk_engine.py` (`check_exposure_limits`)

Added an explicit guard at the top of `check_exposure_limits`:
```python
if not math.isfinite(new_position_exposure_pct) or new_position_exposure_pct < 0:
    return False, "invalid_exposure_input"
```
This runs before both the D-06 per-pair comparison and the D-07
aggregate-with-correlation step, closing the exact adversarial-input
class the review described (a `NaN` silently passing every `>`/`>=`
comparison; a large negative value masking real exposure when summed
into `raw_sum`).

**Tests added:**
`test_negative_new_position_exposure_rejected`,
`test_nan_new_position_exposure_rejected`,
`test_infinite_new_position_exposure_rejected`, plus a control test
`test_zero_new_position_exposure_still_allowed` confirming the guard is
`< 0`, not `<= 0` (0.0 is the legitimate default meaning "no proposed
position exposure to add").

## Warning 3 — MQL5 `CheckExposureLimits` trusts a pre-aggregated scalar

**Files:** `mql5/RiskGuard.mqh`, `docs/risk_engine_mql5_spec.md`

Decision: **kept as a documented, accepted exception** rather than
restructured. Per the review's own recommended resolution path, and
confirmed against `docs/risk_engine_mql5_spec.md`'s existing rationale
("O EA NÃO deve confiar cegamente... deve ter as suas próprias
verificações redundantes"): recomputing the D-07 correlation adjustment
inside `RiskGuard.mqh` would require giving the EA its own
correlation/cointegration matrix, which is a Layer 0 (`data_pipeline.py`)
artifact that doesn't exist on the MQL5 side yet and is out of this
phase's scope. Adding it as a function parameter now would either (a)
require inventing a new bridge/data structure ahead of the Phase 3/4 file
bridge design, or (b) simulate "independence" with data that isn't
actually independently sourced — neither is a real improvement, and (a)
risks violating this phase's explicit "pure function, no hidden I/O"
design principle by forcing premature I/O-shaped parameters.

Instead:
- Expanded the header comment directly above `CheckExposureLimits` in
  `mql5/RiskGuard.mqh` with an explicit "DESVIO DELIBERADO E ACEITE"
  section explaining exactly why this is weaker than
  `CheckDrawdownBreaker`/`CheckPositionCount` for RISK-07 specifically,
  and naming the concrete forward-looking fix (Phase 3/4 file bridge
  should also publish a periodic correlation-matrix snapshot so the EA
  can recompute independently).
- Added the same rationale, with a named Phase 3/4 follow-up task, to
  `docs/risk_engine_mql5_spec.md` under "Repetição das regras de risco
  localmente" so it's tracked as a concrete backlog item, not just a code
  comment that could be lost.

No code/signature change — `CheckExposureLimits`'s interface and
behavior are unchanged, so no MQL5 test updates were needed.

## Info 1 — Comparison-operator asymmetry (`>` vs `>=`) undocumented

**Files:** `src/risk_engine.py` (module docstring),
`mql5/RiskGuard.mqh` (header comment)

Added an explicit "NOTA DELIBERADA" paragraph to both files' top-level
comments stating that exposure limits (D-06/D-07) intentionally use
strict `>` while drawdown breakers (D-03/D-04/D-05) and position count
(D-08) intentionally use `>=`, with the rationale (continuous sizing
input vs. discrete count / safety breaker where hitting the exact limit
should already block). No behavior change — both languages already
agreed with each other per-check; this closes only the "undocumented"
part of the finding.

## Info 2 — D-14 "oversized lots"/"duplicate orders" covered by analogy only

**Decision: no code change, confirmed as correctly deferred.**

The review's own conclusion was explicit: *"This is honest and
intentional, not a hidden gap... No action needed this phase; flagging
so it isn't lost track of before Phase 4."* Re-read
`tests/test_risk_engine.py`'s existing
`test_oversized_position_exposure_rejected` and
`test_duplicate_order_submission_rejected` docstrings, which already
candidly document that they test the closest Python-side equivalent
(risk_engine.py has no concept of concrete lot sizes or order-submission
dedup state — that's MQL5/execution-layer responsibility, RISK-07).
Confirmed no dedup mechanism exists anywhere yet in the reviewed files
(neither Python nor `RiskGuardTests.mq5`). This finding requires a
Phase 3/4 design decision (idempotency-key mechanism in the file
bridge), not a Phase 2 code fix — deferring is the correct action here,
matching the reviewer's own recommendation. Flagged again here so it
survives into Phase 3/4 planning.

## Info 3 — `resolve_kelly_inputs` payoff_ratio fallback divides near-zero `avg_loss_r`

**File:** `src/risk_engine.py` (`resolve_kelly_inputs`, new module constant
`_MIN_ABS_AVG_LOSS_R = 1e-6`)

The `avg_loss_r not in (None, 0)` guard only excluded an exact `0`. Added
an additional condition `abs(float(avg_loss_r)) >= _MIN_ABS_AVG_LOSS_R`
so an infinitesimal but non-zero `avg_loss_r` (e.g. `1e-12`, a plausible
rounding artifact) falls back to the `profit_factor` proxy instead of
producing a wildly inflated `payoff_ratio` that would otherwise flow
directly into Kelly sizing.

**Tests added:**
`test_resolve_kelly_inputs_uses_avg_win_loss_when_avg_loss_r_is_healthy`
(control case), `test_resolve_kelly_inputs_falls_back_to_profit_factor_
when_avg_loss_r_is_near_zero` (the bug case: `avg_loss_r=1e-12`),
`test_resolve_kelly_inputs_falls_back_to_profit_factor_when_avg_loss_r_
is_exactly_zero` (existing behavior, unchanged).

## Info 4 — Kelly-derived `size_lots` never capped against `max_pair_exposure_pct` (D-06)

**File:** `src/risk_engine.py` (`evaluate_order`)

Re-read the review's own framing: it flagged this as "may be intentional"
but noted the connection between Kelly sizing and D-06/D-07 was "only
enforced for the caller-supplied proxy value, not the actual returned
size." Checked `02-CONTEXT.md`/`02-RESEARCH.md` for any explicit
decision to defer this cap to Phase 3/4 — found none; D-06 is defined as
a hard per-pair exposure limit with no stated carve-out for Kelly-derived
sizing specifically. Given that, fixed it: `evaluate_order` now clamps
`size_fraction` to `limits.max_pair_exposure_pct` before returning it as
`size_lots`, closing the gap where a caller could pass a small
`new_position_exposure_pct` proxy to pass `check_exposure_limits` while
the actual approved `size_lots` (driven by `win_rate`/`payoff_ratio`)
exceeded D-06.

Updated `evaluate_order`'s docstring to state the new invariant:
`size_lots <= limits.max_pair_exposure_pct` always holds for an approved
decision.

**Tests added/updated:**
- `test_kelly_derived_size_lots_clamped_to_max_pair_exposure_pct` — uses
  `win_rate=0.9, payoff_ratio=5.0` (unclamped 0.25x-Kelly = 0.22, well
  above the 5% limit) with a small `new_position_exposure_pct=0.01` proxy,
  proving `size_lots` is clamped to exactly `0.05` independent of the
  proxy value used to pass the gate.
- Updated the pre-existing
  `test_valid_order_approved_with_positive_sl_and_quarter_kelly_size` to
  use `win_rate=0.52, payoff_ratio=1.3` instead of `win_rate=0.6,
  payoff_ratio=1.5` — the original fixture's unclamped Kelly fraction
  (0.0833) now exceeds the D-06 limit under the new clamp, which would
  have made the test assert on the wrong (pre-clamp) value. The new
  fixture's unclamped fraction (0.0377) stays below 5%, so this test
  continues to isolate and prove the 0.25x-Kelly math path specifically,
  while the new test above proves the clamp path. Added an explicit
  precondition assertion (`expected_size < DEFAULT_LIMITS.max_pair_
  exposure_pct`) so a future edit can't silently reintroduce ambiguity
  between "testing Kelly math" and "testing the clamp" in the same test.

No MQL5-side change needed — Kelly sizing is Python-only logic; MQL5 has
no equivalent function to keep in parity here (confirmed by grep: only
the `RISKGUARD_KELLY_FRACTION` constant exists in `RiskGuard.mqh`, no
sizing computation).

## Info 5 — Unused `Any` import

**File:** `src/risk_engine.py`

Removed `from typing import Any` (confirmed via grep that `Any` did not
appear anywhere else in the file after removal).

## Constant/interface parity check (RISK-07)

No numeric constant or function signature in `src/risk_limits.py` or
`src/risk_engine.py` changed in this fix pass — all fixes were either
internal logic corrections (Warning 1, Info 3, Info 4), input validation
additions that introduce a new reject-reason string but no changed
constant/signature (Warning 2), or documentation-only changes (Warning 3,
Info 1). `mql5/RiskGuard.mqh`'s D-01..D-12 `#define` constants and
function signatures are therefore still exactly in sync with
`risk_limits.py` / `risk_engine.py` — no MQL5 constant updates were
required as part of this fix pass. The two `RiskGuard.mqh` edits made
(Warning 3, Info 1) are comment-only, verified via `git diff --stat`
showing zero lines changed outside comment blocks.

## Final verification

```
python -m pytest tests/test_risk_engine.py src/backtest_engine_test.py -q
50 passed
```

(32 in `tests/test_risk_engine.py`, up from the review's confirmed 22;
18 in `src/backtest_engine_test.py`, unaffected by this phase's changes
and included per the task's required command.)
