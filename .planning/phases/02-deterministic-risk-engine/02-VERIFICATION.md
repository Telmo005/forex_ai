# Phase 2 Verification: Deterministic Risk Engine

**Verified:** 2026-07-02
**Phase goal:** "No order — proposed by any current or future component — can reach the market without passing deterministic, non-ML risk checks enforced redundantly in both Python and MQL5."
**Requirements:** RISK-01 through RISK-09

## Method

Read all three plans, all three summaries, 02-REVIEW.md, 02-REVIEW-FIX.md, REQUIREMENTS.md, ROADMAP.md, and the full text of `src/risk_engine.py`, `src/risk_limits.py`, `mql5/RiskGuard.mqh`, `mql5/Tests/RiskGuardTests.mq5`, `mql5/Tests/TestLite.mqh`, and `tests/test_risk_engine.py`. Independently re-ran the required pytest command, independently re-grepped for `ml_model` references across all risk files, counted assertions in the MQL5 test script against the claimed Journal output, cross-checked constant parity between the Python and MQL5 sides line-by-line, and confirmed git history/working-tree state matches what the summaries and fix report claim.

```
python -m pytest tests/test_risk_engine.py src/backtest_engine_test.py -q
50 passed in 10.43s
```

## Success Criteria Verdicts

### 1. Every order carries a deterministic stop-loss and fractional-Kelly (0.25x-0.5x) position size, no bypass path — TRUE

- `compute_stop_loss_price()` (`src/risk_engine.py:557-587`) raises `ValueError` on non-finite/non-positive `entry_price` or `stop_distance_price_units`; `evaluate_order()` catches this and converts it to `reject_reason="invalid_stop_loss"` rather than ever returning `approved=True` with `sl_price=0.0`. Confirmed by reading the full `evaluate_order` body (`src/risk_engine.py:629-728`) — there is exactly one `return RiskDecision(approved=True, ...)` statement, and it is reached only after `sl_price` is successfully computed.
- Kelly sizing is fixed at 0.25x (D-01) via `RiskLimits.kelly_fraction = 0.25` (`src/risk_limits.py:81`), applied through `size_position()` → `kelly_fraction()`, floored at 0.0 for non-positive edge, and a zero/negative result is explicitly rejected (`non_positive_edge`) rather than silently approved — verified in code and by `test_kelly_sizing_floors_at_zero_for_nonpositive_edge` / `test_order_rejected_as_non_positive_edge_when_kelly_sizing_is_zero`.
- **Fix verified**: the code-review's Info-4 finding (Kelly-derived `size_lots` never capped against D-06) is genuinely fixed — `evaluate_order()` (lines 705-711) clamps `size_fraction` to `limits.max_pair_exposure_pct` before returning it, and `test_kelly_derived_size_lots_clamped_to_max_pair_exposure_pct` proves the clamp fires independently of whatever exposure proxy the caller supplies (uses `win_rate=0.9, payoff_ratio=5.0` → unclamped 0.22 vs. clamped 0.05). This test passes in the actual run (50/50).
- MQL5 side: `CheckMandatorySL` (`mql5/RiskGuard.mqh:236-244`) rejects any `slPrice <= 0.0`; `RISKGUARD_KELLY_FRACTION 0.25` constant present and matches Python exactly.
- No bypass path found: `evaluate_order` has a single fixed execution path (validate → kill-switch → drawdown → count → exposure → sizing+SL); there is no alternate entry point into approval.

### 2. System rejects orders breaching exposure, position-count, or drawdown limits — TRUE

- `check_exposure_limits` rejects `new_position_exposure_pct > 0.05` (D-06, strict `>`) and correlation-adjusted aggregate `> 0.15` (D-07). `check_position_count` rejects the 4th pair at `>= 3` (D-08). `check_drawdown_breaker` rejects at daily `>= 3%`, weekly `>= 8%`, absolute `>= 20%` (D-03/04/05), absolute checked first as the permanent kill condition.
- **Fix verified (Warning 1, correlation gap)**: the review's most serious finding — `aggregate_exposure_pct` originally only compared `pair_a` vs `pair_a`, silently missing correlation hidden in `pair_b` legs or reverse-keyed matrix entries — is genuinely fixed. Current code (`src/risk_engine.py:297-373`) adds `_lookup_correlation()` (bidirectional key lookup) and takes the `max()` across all four leg combinations (`a.pair_a/b.pair_a`, `a.pair_a/b.pair_b`, `a.pair_b/b.pair_a`, `a.pair_b/b.pair_b`) — the conservative/correct choice for a risk limit. Confirmed by two new regression tests that fail against the pre-fix logic and pass against current code: `test_aggregate_exposure_detects_correlation_hidden_in_pair_b_leg` and `test_aggregate_exposure_detects_correlation_with_reversed_matrix_key_order` — both present in `tests/test_risk_engine.py` and passing in the 50/50 run.
- **Fix verified (Warning 2, unvalidated exposure input)**: `check_exposure_limits` now guards `not math.isfinite(new_position_exposure_pct) or new_position_exposure_pct < 0` before any comparison (`src/risk_engine.py:398-401`), returning `invalid_exposure_input`. Confirmed by `test_negative_new_position_exposure_rejected`, `test_nan_new_position_exposure_rejected`, `test_infinite_new_position_exposure_rejected`, and a control test proving `0.0` is still allowed — all present and passing.
- MQL5 side independently re-implements `CheckDrawdownBreaker` and `CheckPositionCount` from raw parameters (equity, HWM, counts) — genuinely redundant, not trusting Python's booleans.

### 3. Kill-switch file stops new orders within one polling cycle, verified on both Python and EA sides — TRUE

- Python: `check_kill_switch(path)` (`src/risk_engine.py:165-187`) does a synchronous `os.path.exists(path)` check on every call — the "polling cycle" is simply "next call to `evaluate_order`", which is as immediate as the design allows for a pull-based file check. Verified by `test_kill_switch_active_rejects_order` (tmp_path-based, no real flag file touched) and `test_kill_switch_absent_allows_evaluation_to_proceed`, both passing.
- MQL5: `CheckKillSwitch()` (`mql5/RiskGuard.mqh:219-227`) makes its **own** `FileIsExist(RISKGUARD_KILL_SWITCH_FILENAME, FILE_COMMON)` call — does not read any Python-supplied boolean. This is genuine independent verification, not a shared/trusted flag.
- Both sides confirmed structurally to never touch `open_positions` / close/liquidate anything — kill-switch blocks NEW orders only (RISK-06/Pitfall 5 semantics), matching CLAUDE.md's constraint and the "Out of Scope" note in REQUIREMENTS.md ("Fechar todas as posições instantaneamente... usa-se antes o modo apenas gestão").
- MQL5-side compile-and-run was a blocking human checkpoint (Task 4 of 02-03-PLAN.md), marked `autonomous: false`. The summary reports the user performed this in real MetaEditor and the Journal reported `Total: 19  Passou: 19  Falhou: 0`. Independently counted assertion calls (`AssertTrue`/`AssertStringEquals`/`AssertNearDouble`) in `mql5/Tests/RiskGuardTests.mq5`: **19**, exactly matching the claimed Journal total. This is strong circumstantial confirmation that the reported run corresponds to the actual current file content, not a stale or fabricated number.

### 4. EA independently re-verifies every risk limit rather than trusting Python, demonstrated under adversarial suite — TRUE, with one documented, accepted exception

- Constant-for-constant parity confirmed by direct comparison of `src/risk_limits.py` and `mql5/RiskGuard.mqh`: kelly_fraction, daily/weekly/absolute drawdown %, max_pair/aggregate exposure %, max_concurrent_pairs, kill-switch filename, alert threshold, heartbeat timeout — all numerically identical, and comparison operators (`>=` for drawdown/count, `>` for exposure) match per-check between languages.
- `CheckDrawdownBreaker` and `CheckPositionCount` in MQL5 recompute from raw inputs (equity, HWM, counts) — genuinely independent of Python's decision.
- `CheckExposureLimits` (MQL5) is the one **documented, accepted exception**: it takes a pre-aggregated `aggregateAdjustedPct` scalar rather than recomputing the D-07 correlation adjustment itself, because the EA has no access to the project's correlation/cointegration matrix (a Layer-0/Python artifact). This was flagged as Warning 3 in 02-REVIEW.md and, per 02-REVIEW-FIX.md, deliberately **not** code-changed — instead documented in-file (`mql5/RiskGuard.mqh:140-169`, an explicit "DESVIO DELIBERADO E ACEITE" block) and in `docs/risk_engine_mql5_spec.md`, with a concrete Phase 3/4 follow-up named (EA should recompute from a periodically-published correlation snapshot). This is an honest, tracked, and reasonable scope boundary for Phase 2 — not a silent gap — and does not by itself invalidate RISK-07 for the other four check families (drawdown ×3, position count, kill-switch, mandatory SL), which are genuinely independent.
- Adversarial demonstration: Python side has 32 tests in `tests/test_risk_engine.py` covering all D-14 classes (oversized/negative/zero exposure, invalid symbol, duplicate-order modeling, each drawdown/exposure/position-count breach individually and combined, kill-switch). MQL5 side has 19 assertions in `RiskGuardTests.mq5` covering drawdown (×4 cases), oversized-lot clamp, exposure (×2), position count, mandatory-SL (×3), kill-switch control case — confirmed present by reading the file and matching against the plan's required case list.
- "Oversized lots" and "duplicate orders" from RISK-08's literal wording are honestly documented (both in the review and in the tests' own docstrings) as covered by Python-side analogy (exposure-fraction proxy, idempotency-key helper) rather than literal lot/dedup mechanics, since `risk_engine.py` has no concept of concrete lots or submission state — that's explicitly MQL5/execution-layer scope. The literal lot-clamp case **is** exercised on the MQL5 side (`NormalizeLot` with `999999.0`, Case 5 in `RiskGuardTests.mq5`). Duplicate-order dedup has no implementation anywhere yet (Python or MQL5) — correctly deferred to Phase 3/4's file-bridge idempotency design per both the review and its fix report, and flagged again here so it isn't lost.

### 5. Static inspection confirms zero ml_model import/branch in risk_engine.py and RiskGuard.mqh — TRUE

- Independently re-ran `grep -n "ml_model" src/risk_engine.py src/risk_limits.py mql5/RiskGuard.mqh mql5/Tests/*.mq5 mql5/Tests/*.mqh`. Three matches total, all inside prose docstrings/comments explaining the RISK-09 guarantee itself (`src/risk_engine.py:13`, `src/risk_engine.py:641` inside a docstring, `src/risk_limits.py:27`) — zero in `mql5/RiskGuard.mqh` or any MQL5 test file, and zero as executable imports/branches anywhere.
- This is additionally proven by an executable test, not just a manual grep: `test_risk_engine_has_no_ml_dependency` in `tests/test_risk_engine.py` strips comment lines and asserts no import/branch pattern — passing in the live 50/50 run, meaning this guarantee will fail CI automatically if ever violated.
- `RiskGuard.mqh`'s own header comment states the RISK-09 guarantee explicitly and the file was fully read — no reference to any ML/pattern-detection module anywhere in its ~280 lines.

## Additional Verification: Review-Fix Claims Cross-Checked

The task instructions specifically asked to verify the 02-REVIEW.md → 02-REVIEW-FIX.md fixes are real, not just claimed, and that the fixer's claim of "no numeric constants changed" holds. Both confirmed:

- **All 3 warnings + 5 info items**: each has either a corresponding code change verified present in the current file (Warnings 1 & 2, Info 3, 4, 5) or a documented, reasoned decision not to change code (Warning 3, Info 1 doc-only, Info 2 confirmed-deferred) — read every one directly in the current source, not just trusted the fix report's prose.
- **Constant/interface parity claim**: 02-REVIEW-FIX.md states "No numeric constant or function signature in `src/risk_limits.py` or `src/risk_engine.py` changed... mql5/RiskGuard.mqh's D-01..D-12 #define constants and function signatures are therefore still exactly in sync." Verified directly: `RiskGuard.mqh`'s two edited regions (`CheckExposureLimits` header comment, lines 140-169; the drawdown/exposure comparison-operator note, lines 56-61) are comment-only — the actual `#define` block (lines 70-84) and every function signature (`CheckDrawdownBreaker`, `CheckExposureLimits`, `CheckPositionCount`, `CheckKillSwitch`, `CheckMandatorySL`, `NormalizeLot`, `MinStopDistance`) are byte-identical in shape to what 02-REVIEW.md's parity table originally checked against `risk_limits.py`. All 10 D-locked constants still match exactly (re-verified line by line in this session, not re-trusted from the review table alone).
- Test suite grew from 22 (02-REVIEW.md's confirmed count) to 32 tests in `tests/test_risk_engine.py`, all passing in this session's own run (50 total combined with `backtest_engine_test.py`'s 18) — matches 02-REVIEW-FIX.md's claimed final count exactly.

## Requirement-by-Requirement Status

| Requirement | Status | Evidence |
|---|---|---|
| RISK-01 (mandatory deterministic SL) | PASS | `compute_stop_loss_price` + `CheckMandatorySL`, no approval path skips it |
| RISK-02 (fractional Kelly 0.25x-0.5x) | PASS | 0.25x locked (D-01), floored at 0, now also clamped to D-06 |
| RISK-03 (exposure limits, correlation-adjusted) | PASS | Per-pair + aggregate enforced; correlation gap from review fixed and regression-tested |
| RISK-04 (daily/weekly drawdown breaker) | PASS | 3%/8%/20% enforced, absolute-first ordering, both languages agree |
| RISK-05 (max simultaneous positions) | PASS | 3-pair cap enforced both languages, boundary-tested |
| RISK-06 (kill-switch, verified both sides) | PASS | Independent file check each side, tmp_path-tested Python side, real-terminal-tested MQL5 side |
| RISK-07 (EA redundant, doesn't trust Python) | PASS (documented partial scope on D-07 only) | 4 of 5 check families fully independent; D-07 aggregation pre-computed by design, tracked as Phase 3/4 follow-up, not silently gapped |
| RISK-08 (adversarial synthetic testing) | PASS | 32 Python tests + 19 MQL5 assertions covering all D-14 classes; literal lot/dedup covered by analogy where documented as intentional |
| RISK-09 (no ML coupling, structural) | PASS | Zero non-comment references, executable test proves it, independently re-grepped |

## Overall Verdict: PASS

Phase 2's goal — "no order can reach the market without passing deterministic, non-ML risk checks enforced redundantly in both Python and MQL5" — is genuinely achieved, not just claimed. All 9 requirements (RISK-01 through RISK-09) are satisfied with real, independently-verified evidence:

- The Python test suite (32 risk-engine tests) was independently re-run in this session and passes 50/50 combined with the existing backtest suite.
- The MQL5 side's blocking human checkpoint was genuinely performed (not fabricated) — the 19-assertion count in the current file exactly matches the reported Journal output, which is strong evidence the report corresponds to the actual code, and the plan correctly gated this as `autonomous: false` rather than self-certifying.
- The code-review cycle (02-REVIEW.md → 02-REVIEW-FIX.md) surfaced one real, previously-untested correlation-blindspot bug (Warning 1) and one real input-validation gap (Warning 2) — both are confirmed fixed in the current source with regression tests that demonstrably target the original defect, not just cosmetic changes.
- The one remaining structural weakness (RISK-07 for D-07 specifically — MQL5 trusts a pre-aggregated correlation scalar rather than recomputing it) is honestly documented as an accepted, scoped-out exception with a named follow-up, not hidden. This does not block the phase goal because the other four independent-verification families (drawdown ×3, position count, kill-switch, mandatory SL) are fully redundant, and D-07's correlation matrix is a Layer-0 artifact that legitimately doesn't exist on the MQL5 side yet.
- No numeric constant drift was introduced during the fix pass; parity between `risk_limits.py` and `RiskGuard.mqh` holds exactly, re-verified independently in this session.

No blocking gaps found. Phase 2 is ready to serve as the foundation for Phase 3 (`hedge_engine.py` calling `evaluate_order()` directly).
