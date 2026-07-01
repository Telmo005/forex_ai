---
phase: 02-deterministic-risk-engine
plan: 03
subsystem: risk-engine
tags: [mql5, risk-guard, kill-switch, drawdown, exposure, testing, standalone-script]

# Dependency graph
requires:
  - phase: 02-deterministic-risk-engine
    provides: "src/risk_limits.py's finalized D-01..D-14 numeric constants (Plan 01, wave 1), hand-duplicated here into MQL5 #defines"
provides:
  - "mql5/RiskGuard.mqh — pure MQL5 risk-check functions (CheckDrawdownBreaker, CheckExposureLimits, CheckPositionCount, CheckKillSwitch, CheckMandatorySL) plus broker-spec normalizers (NormalizeLot, MinStopDistance), independently re-verifying every Python-side risk decision (RISK-07)"
  - "mql5/Tests/TestLite.mqh — minimal hand-rolled assertion helper (CTestLite) for in-process MQL5 testing without Strategy Tester"
  - "mql5/Tests/RiskGuardTests.mq5 — standalone Script driving all D-14 adversarial cases against RiskGuard.mqh's pure functions"
  - "PENDING: human confirmation that RiskGuardTests.mq5 compiles clean in MetaEditor (F7) and PrintSummary reports 0 failures (Task 4 checkpoint, not yet performed)"
affects: [04-mql5-execution]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-function MQL5 risk checks: every Check* function takes explicit double/string/bool parameters and returns bool + string &rejectReason out-param, with zero internal AccountInfoDouble/PositionsTotal/SymbolInfoDouble calls in decision logic — only NormalizeLot/MinStopDistance (broker-spec normalizers, not decision logic) legitimately touch SymbolInfoDouble/SymbolInfoInteger"
    - "Hand-rolled minimal test harness (CTestLite in TestLite.mqh) instead of adopting MQLUnit/MTUnit, per 02-RESEARCH.md 'Don't Hand-Roll' guidance — a dozen risk functions don't justify a Strategy-Tester-coupled framework"
    - "Standalone .mq5 Script (not an EA) as the MQL5 test runner — compiles and runs in MetaEditor/any chart with no live terminal, no Strategy Tester, no historical data (RESEARCH.md Pattern 2)"
    - "Hand-duplicated D-01..D-14 numeric constants between src/risk_limits.py (Python) and mql5/RiskGuard.mqh (MQL5 #defines), synchronized manually via 02-CONTEXT.md as single source of truth — no automated cross-language import is possible"

key-files:
  created:
    - mql5/RiskGuard.mqh
    - mql5/Tests/TestLite.mqh
    - mql5/Tests/RiskGuardTests.mq5
  modified: []

key-decisions:
  - "All drawdown/kill-switch checks in RiskGuard.mqh block NEW orders only, never close existing positions — enforced by design: no function in this file touches open positions (RISK-06/Pitfall 5)."
  - "CheckKillSwitch() is the only decision function permitted to touch the filesystem (FileIsExist on KILL_SWITCH.flag via FILE_COMMON) — this is the literal nature of the D-09 mechanism, kept deliberately minimal and never trusting a Python-supplied boolean (RISK-07)."
  - "NormalizeLot/MinStopDistance are explicitly carved out as broker-spec normalizers, not risk decision logic, and are the only functions permitted to call SymbolInfoDouble/SymbolInfoInteger — copied verbatim from .claude/skills/mql5-trading-ea/SKILL.md."

patterns-established:
  - "RiskGuard.mqh: pure decision functions + explicit exception list (NormalizeLot/MinStopDistance as broker normalizers, CheckKillSwitch as the one file-touching decision) documented inline at the top of the file."
  - "RiskGuardTests.mq5: one assertion block per D-14 adversarial class, with defensive skip-not-fail handling when a broker symbol (EURUSD) isn't available in the terminal running the Script."

requirements-completed: [RISK-07, RISK-06, RISK-08, RISK-09, RISK-01]

coverage:
  - id: D1
    description: "RiskGuard.mqh implements CheckDrawdownBreaker/CheckExposureLimits/CheckPositionCount/CheckKillSwitch/CheckMandatorySL as pure functions taking explicit parameters (no internal AccountInfoDouble/PositionsTotal/SymbolInfoDouble calls in decision logic), plus NormalizeLot/MinStopDistance broker normalizers, hand-duplicating D-01..D-14 from risk_limits.py with source-of-truth comments, zero ml_model references"
    requirement: "RISK-07"
    verification:
      - kind: unit
        ref: "shell: test $(grep -c 'ml_model' mql5/RiskGuard.mqh) -eq 0 && grep -q 'CheckDrawdownBreaker' mql5/RiskGuard.mqh && grep -q 'CheckKillSwitch' mql5/RiskGuard.mqh && grep -q 'CheckMandatorySL' mql5/RiskGuard.mqh"
        status: pass
    human_judgment: false
  - id: D2
    description: "TestLite.mqh defines a minimal CTestLite assertion class (AssertTrue/AssertStringEquals/AssertNearDouble/PrintSummary) with pass/fail counters, requiring no Strategy Tester or external watcher"
    verification:
      - kind: unit
        ref: "shell: grep -q 'class CTestLite' mql5/Tests/TestLite.mqh && grep -q 'AssertTrue' mql5/Tests/TestLite.mqh && grep -q 'PrintSummary' mql5/Tests/TestLite.mqh"
        status: pass
    human_judgment: false
  - id: D3
    description: "RiskGuardTests.mq5 is a standalone Script (OnStart entry point) that includes RiskGuard.mqh + TestLite.mqh and drives every D-14 adversarial class (absolute/daily/weekly drawdown breach, oversized-lot clamp, per-pair and aggregate exposure breach, 4th-pair rejection, missing-SL rejection) ending in PrintSummary()"
    requirement: "RISK-08"
    verification:
      - kind: unit
        ref: "shell: grep -q 'void OnStart' mql5/Tests/RiskGuardTests.mq5 && grep -q 'CTestLite' mql5/Tests/RiskGuardTests.mq5 && grep -q 'RiskGuard.mqh' mql5/Tests/RiskGuardTests.mq5 && grep -q 'PrintSummary' mql5/Tests/RiskGuardTests.mq5"
        status: pass
    human_judgment: false
  - id: D4
    description: "RiskGuardTests.mq5 compiles with zero errors/warnings in MetaEditor (F7) and, when run as a Script, PrintSummary reports all D-14 adversarial assertions passing (0 failed) in the Experts/Journal log"
    requirement: "RISK-08"
    verification: []
    human_judgment: true
    rationale: "MetaEditor/MT5 is a Windows GUI application that cannot be launched, compiled, or observed from this shell session. Compilation and Script execution require a human to open MetaEditor, run F7, drag the Script onto a chart, and read the Journal output. This is Task 4's blocking checkpoint and has NOT been performed yet."

# Metrics
duration: unknown (interrupted mid-plan; Tasks 1-3 completed before a connection error, resumed in a follow-up session for verification + documentation only)
completed: 2026-07-02
status: partial
---

# Phase 2 Plan 3: MQL5 RiskGuard + Standalone Test Harness Summary

**mql5/RiskGuard.mqh pure risk-check functions (drawdown/exposure/position-count/kill-switch/mandatory-SL) plus a standalone RiskGuardTests.mq5 Script harness — code complete and structurally verified, but the MetaEditor compile-and-run checkpoint (Task 4) is still pending human action.**

## Performance

- **Duration:** unknown — Tasks 1-3 were completed by a prior executor session that was interrupted by a connection error immediately after Task 3's commit. This follow-up session performed no new code changes: it re-verified Tasks 1-3 against the plan's acceptance criteria and wrote this SUMMARY.
- **Started:** unknown (prior session)
- **Completed:** Tasks 1-3 complete as of commit `c949b0e`; Task 4 (human checkpoint) not yet performed
- **Tasks:** 3 of 4 completed (Task 4 is a blocking `checkpoint:human-verify` gate)
- **Files modified:** 3 created (mql5/RiskGuard.mqh, mql5/Tests/TestLite.mqh, mql5/Tests/RiskGuardTests.mq5)

## Accomplishments
- Built `mql5/RiskGuard.mqh`: pure-function risk checks (`CheckDrawdownBreaker`, `CheckExposureLimits`, `CheckPositionCount`, `CheckKillSwitch`, `CheckMandatorySL`) that take explicit parameters and never call `AccountInfoDouble`/`PositionsTotal`/`SymbolInfoDouble` inside decision logic — independently re-verifying every risk limit without trusting Python's numbers (RISK-07). Also includes `NormalizeLot`/`MinStopDistance` as explicitly-carved-out broker-spec normalizers (legitimately reading `SymbolInfoDouble`/`SymbolInfoInteger`), copied per `.claude/skills/mql5-trading-ea/SKILL.md`.
- Hand-duplicated all D-01..D-14 numeric limits from `src/risk_limits.py` (Plan 01, already finalized) as named `#define` constants in RiskGuard.mqh, each citing its decision ID and a comment stating explicitly these are hand-synced (never imported) with `02-CONTEXT.md` as the single source of truth.
- `CheckKillSwitch()` makes its own `FileIsExist("KILL_SWITCH.flag", FILE_COMMON)` call (D-09/RISK-06 EA-side), never trusting a Python-supplied boolean.
- All drawdown/kill-switch checks block new orders only — no function in RiskGuard.mqh touches existing positions (RISK-06/Pitfall 5).
- Confirmed zero `ml_model` references anywhere in RiskGuard.mqh (RISK-09 MQL5-side), verified via grep.
- Built `mql5/Tests/TestLite.mqh`: minimal hand-rolled `CTestLite` class (`AssertTrue`, `AssertStringEquals`, `AssertNearDouble`, `PrintSummary`) — deliberately not adopting MQLUnit/MTUnit per 02-RESEARCH.md's "Don't Hand-Roll" guidance, since a dozen risk functions don't justify a Strategy-Tester-coupled framework.
- Built `mql5/Tests/RiskGuardTests.mq5`: a standalone Script (`OnStart()` entry point, not an EA) that `#include`s both RiskGuard.mqh and TestLite.mqh and exercises all D-14 adversarial classes (absolute/daily/weekly drawdown breach, oversized-lot clamp with a defensive skip if the test symbol is unavailable, per-pair exposure >5%, aggregate exposure >15%, 4th concurrent pair, missing stop-loss), ending with `test.PrintSummary()`.
- Re-verified all three of the plan's task-level automated `<verify>` grep commands in this follow-up session (not just trusted the prior commits) — all three print their expected PASS strings.

## Task Commits

Each task was committed atomically by the prior (interrupted) session:

1. **Task 1: Create mql5/RiskGuard.mqh pure risk-check functions** - `e44f2aa` (feat)
2. **Task 2: Create mql5/Tests/TestLite.mqh minimal assertion helper** - `962b45f` (feat)
3. **Task 3: Create mql5/Tests/RiskGuardTests.mq5 standalone Script runner** - `c949b0e` (feat)
4. **Task 4: MetaEditor compile-and-run checkpoint** - NOT YET PERFORMED (blocking `checkpoint:human-verify` gate; requires human action in a Windows GUI app not reachable from this shell)

**Plan metadata:** (this commit) - docs: partial plan summary, checkpoint pending

## Files Created/Modified
- `mql5/RiskGuard.mqh` - Pure risk-check functions (CheckDrawdownBreaker, CheckExposureLimits, CheckPositionCount, CheckKillSwitch, CheckMandatorySL) + broker normalizers (NormalizeLot, MinStopDistance) + hand-duplicated D-01..D-14 constants
- `mql5/Tests/TestLite.mqh` - CTestLite minimal assertion helper (AssertTrue, AssertStringEquals, AssertNearDouble, PrintSummary)
- `mql5/Tests/RiskGuardTests.mq5` - Standalone Script driving all D-14 adversarial cases against RiskGuard.mqh

## Decisions Made
None new in this follow-up session — Tasks 1-3's design decisions were made and documented (in-file comments) by the prior executor session, matching the plan's action descriptions exactly:
- Pure-function design with explicit `&rejectReason` out-params, no internal terminal-state reads in decision logic.
- `NormalizeLot`/`MinStopDistance` explicitly carved out as the only functions permitted to read `SymbolInfoDouble`/`SymbolInfoInteger`.
- `CheckKillSwitch` as the only decision function permitted to touch the filesystem.
- Hand-rolled `CTestLite` instead of adopting an MQL5 test framework.

## Deviations from Plan

None in the code itself - Tasks 1-3 were executed exactly as specified in `02-03-PLAN.md`. The only deviation is procedural: the prior executor session was interrupted by a connection error immediately after Task 3's commit, before Task 4 (the blocking human checkpoint) could be presented and before this SUMMARY.md was written. This follow-up session performed no code changes — only re-verification of Tasks 1-3 and creation of this SUMMARY.

## Issues Encountered

Prior session interrupted by a connection error after Task 3's commit, leaving Task 4 (blocking checkpoint) and this SUMMARY.md undone. This follow-up session re-ran all three task-level automated `<verify>` commands independently (not relying on the prior commits' say-so) and confirmed:
- `RiskGuard.mqh` structure PASS (zero `ml_model` references; `CheckDrawdownBreaker`/`CheckKillSwitch`/`CheckMandatorySL` all present)
- `TestLite.mqh` PASS (`class CTestLite`, `AssertTrue`, `PrintSummary` all present)
- `RiskGuardTests.mq5` PASS (`void OnStart`, `CTestLite`, `RiskGuard.mqh` include, `PrintSummary` all present)

No code-level issues found. The working tree was clean (no uncommitted changes) before this SUMMARY was added.

## User Setup Required

**Task 4 is a blocking `checkpoint:human-verify` gate that requires manual action in MetaEditor — this CANNOT be performed from an automated shell session (no Windows GUI access).**

Steps the user must perform:
1. Open MetaEditor (from the MT5 terminal: Tools -> MetaQuotes Language Editor).
2. Copy `mql5/RiskGuard.mqh` into the terminal's `MQL5/Include` folder (or open the project folder directly in MetaEditor) so the `#include` resolves.
3. Copy `mql5/Tests/TestLite.mqh` next to `RiskGuardTests.mq5` and open `mql5/Tests/RiskGuardTests.mq5`.
4. Compile with F7 — expect zero errors and zero warnings.
5. Run the Script: drag `RiskGuardTests` onto any open chart, or use MetaEditor's Run action. No account login, no Strategy Tester, no historical data required.
6. Read the Experts/Journal log: `PrintSummary()` must report all assertions passed (0 failed). Every adversarial case (drawdown breaches, oversized-lot clamp, exposure breaches, 4th-pair rejection, missing-SL rejection) must show as passing.

**Resume signal:** Type "approved" if it compiles clean and all assertions pass, or paste the compiler errors / failed-assertion log so the discrepancy can be fixed.

## Next Phase Readiness

- Code for Tasks 1-3 is complete, committed, and structurally verified via grep against every acceptance criterion in `02-03-PLAN.md`.
- **This plan is NOT fully done.** Task 4 (blocking checkpoint) must be completed by the user in MetaEditor before Phase 2 as a whole can be considered closed, since `02-03-PLAN.md`'s own `<verification>` block requires the manual compile-and-run step as proof of RISK-07/RISK-08 (MQL5-side).
- Once Task 4 is confirmed (or fixes are applied in response to a failed-assertion log), this SUMMARY's frontmatter `status` should be updated from `partial` to `complete` and coverage item D4's `verification`/`status` populated with the actual Journal outcome.
- `mql5/RiskGuard.mqh` is ready to be consumed by the future `ScalpingEA.mq5` (Phase 4) once Task 4 confirms it compiles and behaves correctly in a real MetaEditor environment.

---
*Phase: 02-deterministic-risk-engine*
*Completed: 2026-07-02 (Tasks 1-3 only; Task 4 checkpoint pending)*
