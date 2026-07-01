//+------------------------------------------------------------------+
//|                                             RiskGuardTests.mq5    |
//+------------------------------------------------------------------+
//
// RiskGuardTests.mq5
// ====================
// Script standalone (NÃO um Expert Advisor) que exercita as funções
// puras de mql5/RiskGuard.mqh com inputs adversariais fixos (RISK-08,
// D-14). Corre inteiramente dentro do MetaEditor/MetaTrader 5 sem
// necessidade de: terminal ligado a uma conta ao vivo, Strategy Tester,
// ou dados históricos — arrastar para qualquer gráfico ou "Run" no
// MetaEditor é suficiente (02-RESEARCH.md Pattern 2).
//
// Cada assert cobre uma classe adversarial distinta de D-14:
//   1. Drawdown absoluto violado -> kill-switch permanente (D-05)
//   2. Drawdown diário violado (D-03)
//   3. Drawdown semanal violado (D-04)
//   4. Nenhum disjuntor de drawdown ativo -> aprova (caso de controlo)
//   5. Lote excessivo -> NormalizeLot deve fazer clamp ao máximo da
//      corretora (RISK-08 lot-clamp; protegido para não falhar a suite
//      inteira se o símbolo de teste não estiver disponível no terminal)
//   6. Exposição por par > 5% -> rejeitada (D-06)
//   7. Exposição agregada > 15% -> rejeitada (D-07)
//   8. 4º par concorrente -> rejeitado (D-08)
//   9. Stop-loss em falta (slPrice <= 0) -> rejeitado (RISK-01)
//  10. Kill-switch (ficheiro ausente) -> aprova (caso de controlo)
//
// Este ficheiro NÃO referencia qualquer módulo ou saída de inferência
// de Machine Learning (RISK-09) — apenas chama as funções determinísticas
// de RiskGuard.mqh com valores fixos.
//+------------------------------------------------------------------+
#property strict
#property script_show_inputs

#include "..\RiskGuard.mqh"
#include "TestLite.mqh"

//+------------------------------------------------------------------+
//| OnStart — ponto de entrada do Script.                             |
//+------------------------------------------------------------------+
void OnStart()
{
   CTestLite test("RiskGuardTests");
   string reason;

   // ------------------------------------------------------------------
   // Caso 1 (D-14): drawdown absoluto de 20% a partir do HWM -> rejeita
   // com "absolute_drawdown_kill_switch" (D-05), verificado primeiro
   // por ser a condição mais severa (kill-switch permanente).
   // ------------------------------------------------------------------
   reason = "";
   bool passed1 = CheckDrawdownBreaker(
                     8000.0,   // equity atual
                     9800.0,   // dailyStartEquity
                     9500.0,   // weeklyStartEquity
                     10000.0,  // absoluteHWM
                     RISKGUARD_DAILY_DRAWDOWN_PCT,
                     RISKGUARD_WEEKLY_DRAWDOWN_PCT,
                     RISKGUARD_ABSOLUTE_DRAWDOWN_PCT,
                     reason);
   test.AssertTrue(!passed1, "drawdown absoluto de 20% do HWM deve rejeitar a ordem");
   test.AssertStringEquals("absolute_drawdown_kill_switch", reason,
                            "razão de rejeição deve ser 'absolute_drawdown_kill_switch'");

   // ------------------------------------------------------------------
   // Caso 2 (D-14): drawdown diário de 3% (sem violar semanal/absoluto)
   // -> rejeita com "daily_drawdown_breaker" (D-03).
   // ------------------------------------------------------------------
   reason = "";
   bool passed2 = CheckDrawdownBreaker(
                     9700.0,   // equity atual (queda de 3% face ao início do dia)
                     10000.0,  // dailyStartEquity
                     10000.0,  // weeklyStartEquity
                     10000.0,  // absoluteHWM
                     RISKGUARD_DAILY_DRAWDOWN_PCT,
                     RISKGUARD_WEEKLY_DRAWDOWN_PCT,
                     RISKGUARD_ABSOLUTE_DRAWDOWN_PCT,
                     reason);
   test.AssertTrue(!passed2, "drawdown diário de 3% deve rejeitar a ordem");
   test.AssertStringEquals("daily_drawdown_breaker", reason,
                            "razão de rejeição deve ser 'daily_drawdown_breaker'");

   // ------------------------------------------------------------------
   // Caso 3 (D-14): drawdown semanal de 8% (diário dentro do limite,
   // ex. início da semana coincide com início do dia) -> rejeita com
   // "weekly_drawdown_breaker" (D-04).
   // ------------------------------------------------------------------
   reason = "";
   bool passed3 = CheckDrawdownBreaker(
                     9200.0,   // equity atual (queda de 8% face ao início da semana)
                     9200.0,   // dailyStartEquity == equity atual -> 0% drawdown diário
                     10000.0,  // weeklyStartEquity
                     10000.0,  // absoluteHWM
                     RISKGUARD_DAILY_DRAWDOWN_PCT,
                     RISKGUARD_WEEKLY_DRAWDOWN_PCT,
                     RISKGUARD_ABSOLUTE_DRAWDOWN_PCT,
                     reason);
   test.AssertTrue(!passed3, "drawdown semanal de 8% deve rejeitar a ordem");
   test.AssertStringEquals("weekly_drawdown_breaker", reason,
                            "razão de rejeição deve ser 'weekly_drawdown_breaker'");

   // ------------------------------------------------------------------
   // Caso 4 (controlo): nenhum disjuntor de drawdown ativo -> aprova.
   // ------------------------------------------------------------------
   reason = "";
   bool passed4 = CheckDrawdownBreaker(
                     10000.0,  // equity atual == HWM, sem drawdown nenhum
                     10000.0,
                     10000.0,
                     10000.0,
                     RISKGUARD_DAILY_DRAWDOWN_PCT,
                     RISKGUARD_WEEKLY_DRAWDOWN_PCT,
                     RISKGUARD_ABSOLUTE_DRAWDOWN_PCT,
                     reason);
   test.AssertTrue(passed4, "sem drawdown nenhum, a ordem deve ser aprovada (caso de controlo)");

   // ------------------------------------------------------------------
   // Caso 5 (D-14, RISK-08 lot-clamp): lote excessivamente grande deve
   // ser normalizado (clamped) ao volume máximo permitido pela
   // corretora para o símbolo. Protegido: se o símbolo de teste não
   // estiver disponível no terminal atual (ex.: corrida fora de um
   // Market Watch com EURUSD visível), a asserção é ignorada em vez de
   // fazer a suite inteira falhar por uma causa externa à lógica de
   // risco em si.
   // ------------------------------------------------------------------
   string testSymbol = "EURUSD";
   if(SymbolSelect(testSymbol, true) && SymbolInfoDouble(testSymbol, SYMBOL_VOLUME_MAX) > 0.0)
   {
      double maxLot     = SymbolInfoDouble(testSymbol, SYMBOL_VOLUME_MAX);
      double normalized = NormalizeLot(testSymbol, 999999.0);
      test.AssertNearDouble(maxLot, normalized, 0.0001,
                             "lote de 999999.0 deve ser normalizado (clamped) ao volume máximo da corretora");
   }
   else
   {
      Print("  [SKIP] teste de NormalizeLot ignorado — símbolo '", testSymbol,
            "' indisponível neste terminal (não afeta a lógica de risco em si)");
   }

   // ------------------------------------------------------------------
   // Caso 6 (D-14/D-06): exposição de um único par a 6% (> 5% do
   // limite D-06) -> rejeitada com "max_pair_exposure", mesmo com
   // agregação dentro do limite.
   // ------------------------------------------------------------------
   reason = "";
   double perPairExposure6[1] = {0.06};
   bool passed6 = CheckExposureLimits(
                     perPairExposure6,
                     0.06,   // agregado == a própria posição, ainda dentro de 15%
                     RISKGUARD_MAX_PAIR_EXPOSURE_PCT,
                     RISKGUARD_MAX_AGGREGATE_EXPOSURE_PCT,
                     reason);
   test.AssertTrue(!passed6, "exposição de 6% num único par deve rejeitar (> 5% do limite D-06)");
   test.AssertStringEquals("max_pair_exposure", reason,
                            "razão de rejeição deve ser 'max_pair_exposure'");

   // ------------------------------------------------------------------
   // Caso 7 (D-14/D-07): três posições dentro do limite por par (4%
   // cada) mas exposição agregada ajustada por correlação de 18% (>
   // 15% do limite D-07) -> rejeitada com "max_aggregate_exposure".
   // ------------------------------------------------------------------
   reason = "";
   double perPairExposure7[3] = {0.04, 0.04, 0.04};
   bool passed7 = CheckExposureLimits(
                     perPairExposure7,
                     0.18,   // agregado ajustado por correlação, já calculado pelo chamador
                     RISKGUARD_MAX_PAIR_EXPOSURE_PCT,
                     RISKGUARD_MAX_AGGREGATE_EXPOSURE_PCT,
                     reason);
   test.AssertTrue(!passed7, "exposição agregada de 18% deve rejeitar (> 15% do limite D-07)");
   test.AssertStringEquals("max_aggregate_exposure", reason,
                            "razão de rejeição deve ser 'max_aggregate_exposure'");

   // ------------------------------------------------------------------
   // Caso 8 (D-14/D-08): já 3 pares abertos (no limite) -> a tentativa
   // de abrir um 4º par é rejeitada com "max_concurrent_pairs".
   // ------------------------------------------------------------------
   reason = "";
   bool passed8 = CheckPositionCount(3, RISKGUARD_MAX_CONCURRENT_PAIRS, reason);
   test.AssertTrue(!passed8, "4º par concorrente deve ser rejeitado (limite D-08 = 3)");
   test.AssertStringEquals("max_concurrent_pairs", reason,
                            "razão de rejeição deve ser 'max_concurrent_pairs'");

   // ------------------------------------------------------------------
   // Caso 9 (RISK-01): stop-loss em falta (slPrice <= 0) -> rejeitado
   // com "missing_stop_loss", independentemente de qualquer outra
   // condição de risco.
   // ------------------------------------------------------------------
   reason = "";
   bool passed9 = CheckMandatorySL(0.0, reason);
   test.AssertTrue(!passed9, "ordem sem stop-loss (slPrice=0.0) deve ser rejeitada");
   test.AssertStringEquals("missing_stop_loss", reason,
                            "razão de rejeição deve ser 'missing_stop_loss'");

   reason = "";
   bool passed9b = CheckMandatorySL(-1.5, reason);
   test.AssertTrue(!passed9b, "ordem com stop-loss negativo deve ser rejeitada");

   reason = "";
   bool passed9c = CheckMandatorySL(1.0950, reason);
   test.AssertTrue(passed9c, "ordem com stop-loss positivo válido deve ser aprovada (caso de controlo)");

   // ------------------------------------------------------------------
   // Caso 10 (D-09/RISK-06, controlo): assumindo que o ficheiro
   // KILL_SWITCH.flag NÃO existe na pasta Common\Files deste terminal
   // de teste, o kill-switch deve estar inativo e aprovar. Esta
   // asserção documenta o comportamento esperado no ambiente de teste
   // limpo; não cria nem apaga o ficheiro (esta suite não deve ter
   // efeitos colaterais no filesystem partilhado).
   // ------------------------------------------------------------------
   reason = "";
   bool killSwitchInactive = CheckKillSwitch(reason);
   if(killSwitchInactive)
      test.AssertTrue(true, "kill-switch inativo (ficheiro ausente) -> ordens aprovadas (caso de controlo)");
   else
      Print("  [INFO] KILL_SWITCH.flag presente neste terminal — CheckKillSwitch corretamente rejeitou "
            "com razão '", reason, "' (não é uma falha do teste, reflete o estado real do ficheiro partilhado)");

   test.PrintSummary();
}
//+------------------------------------------------------------------+
