//+------------------------------------------------------------------+
//|                                          SignalBridgeTests.mq5    |
//+------------------------------------------------------------------+
//
// SignalBridgeTests.mq5
// =======================
// Script standalone (mesma convenção de RiskGuardTests.mq5): exercita
// as funções puras de mql5/SignalBridge.mqh com linhas JSON fixas e
// inputs sintéticos — sem terminal ligado a conta ao vivo, sem
// Strategy Tester. Arrastar para qualquer gráfico ou "Run" no
// MetaEditor é suficiente.
//
// Cobre:
//   1. ParseSignalLine — open_hedge válido (todos os campos)
//   2. ParseSignalLine — close_hedge válido (campos mínimos)
//   3. ParseSignalLine — linha vazia -> false (fail-closed)
//   4. ParseSignalLine — action desconhecida/em falta -> false
//   5. ParseSignalLine — pair_a/pair_b em falta -> false
//   6. ComputeLotFromRiskFraction — caso normal, matemática correta
//   7. ComputeLotFromRiskFraction — qualquer input não-positivo -> 0.0
//+------------------------------------------------------------------+
#property strict
#property script_show_inputs

#include "..\SignalBridge.mqh"
#include "TestLite.mqh"

void OnStart()
{
   CTestLite test("SignalBridgeTests");

   // ------------------------------------------------------------------
   // Caso 1: open_hedge válido, todos os campos.
   // ------------------------------------------------------------------
   string line1 = "{\"action\":\"open_hedge\",\"pair_a\":\"EURUSD\",\"pair_b\":\"GBPUSD\","
                   "\"direction\":1,\"hedge_ratio\":1.057,\"risk_fraction\":0.0125,"
                   "\"sl_distance_price_units\":0.0050,\"new_position_exposure_pct\":0.0125,"
                   "\"aggregate_exposure_pct_after\":0.041,\"timestamp\":1234.5}";
   SSignalEvent event1;
   bool parsed1 = ParseSignalLine(line1, event1);
   test.AssertTrue(parsed1, "open_hedge válido deve parsear com sucesso");
   test.AssertStringEquals("open_hedge", event1.action, "action deve ser 'open_hedge'");
   test.AssertStringEquals("EURUSD", event1.pair_a, "pair_a deve ser 'EURUSD'");
   test.AssertStringEquals("GBPUSD", event1.pair_b, "pair_b deve ser 'GBPUSD'");
   test.AssertTrue(event1.direction == 1, "direction deve ser 1");
   test.AssertNearDouble(1.057, event1.hedge_ratio, 0.0001, "hedge_ratio deve ser 1.057");
   test.AssertNearDouble(0.0125, event1.risk_fraction, 0.00001, "risk_fraction deve ser 0.0125");
   test.AssertNearDouble(0.0050, event1.sl_distance_price_units, 0.00001,
                          "sl_distance_price_units deve ser 0.0050");
   test.AssertNearDouble(0.0125, event1.new_position_exposure_pct, 0.00001,
                          "new_position_exposure_pct deve ser 0.0125");
   test.AssertNearDouble(0.041, event1.aggregate_exposure_pct_after, 0.0001,
                          "aggregate_exposure_pct_after deve ser 0.041");

   // ------------------------------------------------------------------
   // Caso 2: close_hedge válido, campos mínimos (sem direction/hedge_ratio).
   // ------------------------------------------------------------------
   string line2 = "{\"action\":\"close_hedge\",\"pair_a\":\"EURUSD\",\"pair_b\":\"GBPUSD\","
                   "\"trigger\":\"reversion\",\"timestamp\":1234.6}";
   SSignalEvent event2;
   bool parsed2 = ParseSignalLine(line2, event2);
   test.AssertTrue(parsed2, "close_hedge válido deve parsear com sucesso");
   test.AssertStringEquals("close_hedge", event2.action, "action deve ser 'close_hedge'");
   test.AssertStringEquals("reversion", event2.trigger, "trigger deve ser 'reversion'");

   // ------------------------------------------------------------------
   // Caso 3 (fail-closed): linha vazia -> false.
   // ------------------------------------------------------------------
   SSignalEvent event3;
   bool parsed3 = ParseSignalLine("", event3);
   test.AssertTrue(!parsed3, "linha vazia deve falhar o parse (fail-closed)");

   // ------------------------------------------------------------------
   // Caso 4 (fail-closed): action desconhecida -> false.
   // ------------------------------------------------------------------
   SSignalEvent event4;
   bool parsed4 = ParseSignalLine("{\"action\":\"delete_everything\",\"pair_a\":\"EURUSD\",\"pair_b\":\"GBPUSD\"}", event4);
   test.AssertTrue(!parsed4, "action desconhecida deve falhar o parse (fail-closed)");

   // ------------------------------------------------------------------
   // Caso 5 (fail-closed): pair_b em falta -> false.
   // ------------------------------------------------------------------
   SSignalEvent event5;
   bool parsed5 = ParseSignalLine("{\"action\":\"open_hedge\",\"pair_a\":\"EURUSD\"}", event5);
   test.AssertTrue(!parsed5, "pair_b em falta deve falhar o parse (fail-closed)");

   // ------------------------------------------------------------------
   // Caso 6: ComputeLotFromRiskFraction, matemática normal.
   // equity=10000, riskFraction=0.01 -> valor_em_risco=100
   // slDistance=0.0050, tickSize=0.00001, tickValue=1.0 (EURUSD-like)
   // -> perda_por_lote = (0.0050/0.00001)*1.0 = 500
   // -> lots = 100/500 = 0.2
   // ------------------------------------------------------------------
   double lots6 = ComputeLotFromRiskFraction(10000.0, 0.01, 0.0050, 1.0, 0.00001);
   test.AssertNearDouble(0.2, lots6, 0.0001, "ComputeLotFromRiskFraction deve devolver 0.2 lotes no caso normal");

   // ------------------------------------------------------------------
   // Caso 7 (fail-closed): qualquer input não-positivo -> 0.0.
   // ------------------------------------------------------------------
   test.AssertTrue(ComputeLotFromRiskFraction(0.0, 0.01, 0.0050, 1.0, 0.00001) == 0.0,
                    "equity<=0 deve devolver 0.0 lotes");
   test.AssertTrue(ComputeLotFromRiskFraction(10000.0, 0.0, 0.0050, 1.0, 0.00001) == 0.0,
                    "riskFraction<=0 deve devolver 0.0 lotes");
   test.AssertTrue(ComputeLotFromRiskFraction(10000.0, 0.01, 0.0, 1.0, 0.00001) == 0.0,
                    "slDistancePriceUnits<=0 deve devolver 0.0 lotes");
   test.AssertTrue(ComputeLotFromRiskFraction(10000.0, 0.01, 0.0050, 0.0, 0.00001) == 0.0,
                    "tickValue<=0 deve devolver 0.0 lotes");
   test.AssertTrue(ComputeLotFromRiskFraction(10000.0, 0.01, 0.0050, 1.0, 0.0) == 0.0,
                    "tickSize<=0 deve devolver 0.0 lotes");

   test.PrintSummary();
}
//+------------------------------------------------------------------+
