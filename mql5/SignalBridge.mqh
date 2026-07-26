//+------------------------------------------------------------------+
//|                                                SignalBridge.mqh   |
//+------------------------------------------------------------------+
//
// SignalBridge.mqh
// ==================
// Lado MQL5 da ponte de ficheiros Python->MQL5 (Fase 3/4, decisão de
// ARCHITECTURE.md resolvida em `src/signal_bridge.py`: ficheiro
// partilhado, JSON Lines, sem biblioteca de JSON genérica em nenhum
// dos dois lados). Este ficheiro só contém funções PURAS (sem
// FileOpen/FileRead) — a leitura do ficheiro em si é responsabilidade
// de `ScalpingEA.mq5`, exatamente a mesma separação já estabelecida em
// `RiskGuard.mqh` (funções de decisão puras vs. I/O do chamador).
//
// FORMATO DAS LINHAS (produzido por src/signal_bridge.py, esquema
// fixo e plano, sem aninhamento — ver docstring desse módulo):
//   open_hedge:  {"action":"open_hedge","pair_a":"EURUSD","pair_b":"GBPUSD",
//                 "direction":1,"hedge_ratio":1.057,"risk_fraction":0.0125,
//                 "sl_distance_price_units":0.0050,"timestamp":...}
//   close_hedge: {"action":"close_hedge","pair_a":"EURUSD","pair_b":"GBPUSD",
//                 "trigger":"reversion","timestamp":...}
//
// ParseSignalLine() usa um extrator de campo manual (JsonExtractRaw),
// não um parser JSON genérico — o esquema é fixo e controlado pelo
// próprio projeto (nunca input de terceiros), por isso não precisa de
// suportar aninhamento, arrays, nem escaping complexo.
//
// ComputeLotFromRiskFraction() é a peça RISK-07 explicitamente adiada
// por `risk_engine.py::size_position()` — a conversão da fração de
// equity (Kelly fracionário) para lotes concretos exige o
// tick_value/tick_size do símbolo, que só existe em runtime no
// terminal MT5, nunca do lado Python.
//+------------------------------------------------------------------+
#property strict

struct SSignalEvent
{
   string action;                    // "open_hedge" | "close_hedge"
   string pair_a;
   string pair_b;
   int    direction;                 // +1 | -1 (só relevante para open_hedge)
   double hedge_ratio;                // beta — só relevante para open_hedge
   double risk_fraction;              // fração de equity (0.25x-Kelly) — NUNCA lotes, ver docstring do módulo
   double sl_distance_price_units;    // distância do stop, em unidades de preço da perna A
   double new_position_exposure_pct;     // D-06 — exposição da própria posição nova (== risk_fraction)
   double aggregate_exposure_pct_after;  // D-07 — exposição agregada ajustada por correlação, JÁ pré-calculada
                                          // do lado Python (o EA não tem matriz de correlação própria —
                                          // ver CheckExposureLimits em RiskGuard.mqh e risk_engine_mql5_spec.md)
   string trigger;                   // motivo do close_hedge (auditoria/log)
};

//+------------------------------------------------------------------+
//| JsonExtractRaw — extrai o valor bruto (ainda com aspas, se string)|
//| associado a "key" numa linha JSON plana de um só nível. Devolve   |
//| false se a chave não existir na linha. Função PURA: só            |
//| manipulação de string, nenhum I/O.                                |
//+------------------------------------------------------------------+
bool JsonExtractRaw(string json, string key, string &rawValue)
{
   string needle = "\"" + key + "\":";
   int keyPos = StringFind(json, needle);
   if(keyPos < 0)
      return false;

   int valueStart = keyPos + StringLen(needle);
   int len = StringLen(json);

   // Salta espaços em branco depois de ':'
   while(valueStart < len && StringGetCharacter(json, valueStart) == ' ')
      valueStart++;

   bool isString = (valueStart < len && StringGetCharacter(json, valueStart) == '"');
   int scanStart = isString ? valueStart + 1 : valueStart;
   int end = scanStart;

   if(isString)
   {
      // Avança até à próxima aspa dupla (o esquema fixo nunca escapa
      // aspas dentro de valores — símbolos/ações são identificadores
      // simples).
      while(end < len && StringGetCharacter(json, end) != '"')
         end++;
      rawValue = StringSubstr(json, scanStart, end - scanStart);
   }
   else
   {
      // Valor numérico/booleano: avança até ',' ou '}'.
      while(end < len && StringGetCharacter(json, end) != ',' && StringGetCharacter(json, end) != '}')
         end++;
      rawValue = StringSubstr(json, scanStart, end - scanStart);
   }
   return true;
}

//+------------------------------------------------------------------+
//| JsonGetString / JsonGetDouble / JsonGetInt — wrappers tipados     |
//| sobre JsonExtractRaw, com default explícito quando a chave está   |
//| ausente (nunca um crash silencioso por campo em falta).           |
//+------------------------------------------------------------------+
string JsonGetString(string json, string key, string defaultValue)
{
   string raw;
   if(!JsonExtractRaw(json, key, raw))
      return defaultValue;
   return raw;
}

double JsonGetDouble(string json, string key, double defaultValue)
{
   string raw;
   if(!JsonExtractRaw(json, key, raw))
      return defaultValue;
   return StringToDouble(raw);
}

int JsonGetInt(string json, string key, int defaultValue)
{
   string raw;
   if(!JsonExtractRaw(json, key, raw))
      return defaultValue;
   return (int)StringToInteger(raw);
}

//+------------------------------------------------------------------+
//| ParseSignalLine — parser de topo, devolve false para uma linha    |
//| vazia ou sem a chave "action" (linha corrompida/truncada —        |
//| fail-closed: o chamador nunca deve agir sobre um evento que não   |
//| parseou corretamente).                                            |
//+------------------------------------------------------------------+
bool ParseSignalLine(string line, SSignalEvent &event)
{
   if(StringLen(line) == 0)
      return false;

   event.action = JsonGetString(line, "action", "");
   if(event.action != "open_hedge" && event.action != "close_hedge")
      return false;

   event.pair_a = JsonGetString(line, "pair_a", "");
   event.pair_b = JsonGetString(line, "pair_b", "");
   if(event.pair_a == "" || event.pair_b == "")
      return false;

   event.direction              = JsonGetInt(line, "direction", 0);
   event.hedge_ratio             = JsonGetDouble(line, "hedge_ratio", 0.0);
   event.risk_fraction           = JsonGetDouble(line, "risk_fraction", 0.0);
   event.sl_distance_price_units = JsonGetDouble(line, "sl_distance_price_units", 0.0);
   event.new_position_exposure_pct    = JsonGetDouble(line, "new_position_exposure_pct", 0.0);
   event.aggregate_exposure_pct_after = JsonGetDouble(line, "aggregate_exposure_pct_after", 0.0);
   event.trigger                 = JsonGetString(line, "trigger", "");

   return true;
}

//+------------------------------------------------------------------+
//| ComputeLotFromRiskFraction (RISK-07) — converte uma fração de     |
//| equity (já 0.25x-Kelly, decidida por risk_engine.py) num tamanho  |
//| de lote concreto, a partir da especificação real do símbolo na    |
//| corretora (tick_value/tick_size, só disponíveis em runtime).      |
//|                                                                     |
//| Fórmula: valor_em_risco = equity * riskFraction; perda_por_lote =  |
//| (slDistancePriceUnits / tickSize) * tickValue; lots = valor_em_     |
//| risco / perda_por_lote. Função PURA — o chamador (ScalpingEA.mq5)  |
//| recolhe tickValue/tickSize via SymbolInfoDouble e passa-os         |
//| explicitamente (mesma disciplina de RiskGuard.mqh: nenhuma função  |
//| de decisão chama SymbolInfo*/AccountInfo* internamente).           |
//|                                                                     |
//| Devolve 0.0 (nunca um valor negativo/infinito) se qualquer input   |
//| for não-positivo — o chamador deve tratar 0.0 como "não enviar     |
//| ordem", nunca tentar normalizar um lote inválido.                  |
//+------------------------------------------------------------------+
double ComputeLotFromRiskFraction(double equity, double riskFraction,
                                   double slDistancePriceUnits,
                                   double tickValue, double tickSize)
{
   if(equity <= 0.0 || riskFraction <= 0.0 || slDistancePriceUnits <= 0.0
      || tickValue <= 0.0 || tickSize <= 0.0)
      return 0.0;

   double riskAmount   = equity * riskFraction;
   double lossPerLot   = (slDistancePriceUnits / tickSize) * tickValue;
   if(lossPerLot <= 0.0)
      return 0.0;

   return riskAmount / lossPerLot;
}
//+------------------------------------------------------------------+
