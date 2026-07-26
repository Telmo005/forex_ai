//+------------------------------------------------------------------+
//|                                                   ScalpingEA.mq5  |
//+------------------------------------------------------------------+
//
// ScalpingEA.mq5
// ================
// Camada 4 (execução) — ver ARCHITECTURE.md e
// docs/risk_engine_mql5_spec.md. Consome os sinais já aprovados por
// `src/hedge_engine.py`/`src/risk_engine.py` (camadas 2/3) através da
// ponte de ficheiros de `src/signal_bridge.py`, mas NUNCA confia
// cegamente neles — reverifica localmente cada verificação de risco
// essencial via `RiskGuard.mqh` antes de enviar qualquer ordem
// (RISK-07: redundância intencional, última linha de defesa).
//
// Este EA NÃO decide risco nem escolhe estratégias — só executa o que
// já foi aprovado, com a MESMA disciplina de RiskGuard.mqh: qualquer
// rejeição local é tão válida quanto uma rejeição do lado Python, e
// nunca é silenciosa (sempre Print()).
//
// Modo "só gestão" (heartbeat perdido) e kill-switch BLOQUEIAM APENAS
// abertura de novas posições — nunca fecham posições existentes
// (RISK-06/Pitfall 5, mesma semântica de risk_engine.py). O stop-loss
// já enviado com cada ordem continua a proteger as posições abertas
// mesmo sem qualquer intervenção adicional deste EA.
//
// PRÉ-REQUISITO NÃO NEGOCIÁVEL: a conta tem de estar em modo hedging
// (ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) — sem isto, posições opostas no
// mesmo símbolo fecham-se automaticamente em vez de coexistirem,
// quebrando toda a lógica de hedge multi-perna.
//
// ANTES DE LIGAR A UMA CONTA REAL: ver o checklist não-negociável em
// docs/risk_engine_mql5_spec.md — este ficheiro nunca foi corrido nem
// compilado num terminal MT5 real (o ambiente onde foi escrito não tem
// MetaEditor); tem de passar por compilação + conta DEMO com
// monitorização diária antes de qualquer uso real (CLAUDE.md regra 6).
//+------------------------------------------------------------------+
#property strict
#property copyright "forex_ai_project"

#include <Trade/Trade.mqh>
#include "RiskGuard.mqh"
#include "SignalBridge.mqh"

//--- Inputs -----------------------------------------------------------

input string SignalFilePath        = "signal_queue.jsonl";   // ficheiro de sinais (pasta Common\Files)
input string HeartbeatFilePath     = "heartbeat.flag";        // ficheiro de heartbeat (pasta Common\Files)
input int    PollingMillis         = 500;                     // cadência de polling (ms)
input int    HeartbeatTimeoutSecs  = RISKGUARD_HEARTBEAT_TIMEOUT_SECONDS;  // D-12
input long   MagicNumber           = 20260720;                // identifica as posições abertas por este EA
input string HedgeTagPrefix        = "HEDGE_";                 // prefixo do comentário que identifica as duas pernas de um hedge

//--- Estado global (persistido entre chamadas do EA; equity tracking
// via GlobalVariable* para sobreviver a um restart do EA/terminal —
// ver UpdateEquityTracking abaixo) ---------------------------------

CTrade   trade;
bool     g_managementOnlyMode = false;

//+------------------------------------------------------------------+
//| OnInit                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("ERRO FATAL: conta não está em modo hedging (ACCOUNT_MARGIN_MODE_RETAIL_HEDGING). "
            "EA não pode operar com segurança — lógica de hedge multi-perna exige hedging mode.");
      return(INIT_FAILED);
   }

   trade.SetExpertMagicNumber(MagicNumber);

   InitEquityTracking();

   if(!EventSetMillisecondTimer(PollingMillis))
   {
      Print("ERRO FATAL: EventSetMillisecondTimer falhou.");
      return(INIT_FAILED);
   }

   Print("ScalpingEA inicializado. Magic=", MagicNumber, " SignalFile=", SignalFilePath,
         " HeartbeatTimeout=", HeartbeatTimeoutSecs, "s");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| OnDeinit                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("ScalpingEA parado. Posições existentes continuam com o seu stop-loss no lado da corretora.");
}

//+------------------------------------------------------------------+
//| OnTimer — ciclo principal de polling (ver skill mql5-trading-ea) |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateEquityTracking();

   string killReason;
   bool killSwitchActive = !CheckKillSwitch(killReason);
   if(killSwitchActive)
      Print("KILL SWITCH ATIVO — nenhuma ordem nova será processada nesta iteração.");

   g_managementOnlyMode = IsHeartbeatStale();
   if(g_managementOnlyMode)
      Print("AVISO: heartbeat do Python expirado (> ", HeartbeatTimeoutSecs,
            "s) — modo SÓ GESTÃO ativo (sem novas aberturas; posições existentes continuam protegidas pelo seu stop-loss).");

   if(!killSwitchActive && !g_managementOnlyMode)
      ProcessSignalFile();
}

//+------------------------------------------------------------------+
//| IsHeartbeatStale — compara o instante de modificação do ficheiro  |
//| de heartbeat contra HeartbeatTimeoutSecs. Um ficheiro ausente     |
//| conta como heartbeat perdido (fail-closed: nunca assume "tudo     |
//| bem" por omissão de dados).                                       |
//|                                                                     |
//| USA TimeLocal(), NUNCA TimeCurrent() (bug corrigido 2026-07-20):   |
//| TimeCurrent() devolve a hora do SERVIDOR da corretora (muitas      |
//| vezes horas à frente/atrás da hora local, por fuso horário), mas   |
//| FILE_MODIFY_DATE reflete o relógio LOCAL do sistema operativo — a  |
//| mesma base de tempo usada por `signal_bridge.write_heartbeat()`    |
//| (datetime.now() em Python, também hora local). Comparar as duas    |
//| horas de fontes diferentes fazia este check reportar "expirado"    |
//| permanentemente sempre que a corretora estivesse num fuso horário  |
//| diferente do PC — confirmado em teste manual em conta demo.        |
//+------------------------------------------------------------------+
bool IsHeartbeatStale()
{
   if(!FileIsExist(HeartbeatFilePath, FILE_COMMON))
      return true;

   int handle = FileOpen(HeartbeatFilePath, FILE_READ | FILE_BIN | FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return true;

   datetime modifyTime = (datetime)FileGetInteger(handle, FILE_MODIFY_DATE);
   FileClose(handle);

   return (TimeLocal() - modifyTime) > HeartbeatTimeoutSecs;
}

//+------------------------------------------------------------------+
//| ProcessSignalFile — lê e apaga o ficheiro de sinais (padrão       |
//| "consumir uma vez" da skill mql5-trading-ea), processando uma     |
//| linha JSON por evento. Uma linha que falhe o parse é ignorada e   |
//| registada — nunca faz abortar o processamento das restantes.      |
//+------------------------------------------------------------------+
void ProcessSignalFile()
{
   if(!FileIsExist(SignalFilePath, FILE_COMMON))
      return;

   int handle = FileOpen(SignalFilePath, FILE_READ | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("ERRO: não foi possível abrir ", SignalFilePath, " para leitura.");
      return;
   }

   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      if(StringLen(line) == 0)
         continue;

      SSignalEvent event;
      if(!ParseSignalLine(line, event))
      {
         Print("AVISO: linha de sinal não parseou (ignorada): ", line);
         continue;
      }

      if(event.action == "open_hedge")
         ProcessOpenSignal(event);
      else if(event.action == "close_hedge")
         ProcessCloseSignal(event);
   }
   FileClose(handle);

   // Consumir o sinal (skill mql5-trading-ea): apaga o ficheiro depois de
   // processado. Janela de corrida teórica com uma nova escrita do lado
   // Python é aceite para o volume de sinais deste projeto (scalping, não
   // HFT) — ver skill para o racional completo.
   FileDelete(SignalFilePath, FILE_COMMON);
}

//+------------------------------------------------------------------+
//| CountOpenHedgePairs (D-08) — conta pares de hedge distintos       |
//| atualmente abertos por ESTE EA (magic number), a partir das       |
//| posições reais do terminal — nunca de um contador em memória, que |
//| não sobreviveria a um restart do EA.                              |
//+------------------------------------------------------------------+
int CountOpenHedgePairs()
{
   string seenTags[];
   int seenCount = 0;

   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      string comment = PositionGetString(POSITION_COMMENT);
      if(StringFind(comment, HedgeTagPrefix) != 0)
         continue;

      bool alreadySeen = false;
      for(int j = 0; j < seenCount; j++)
      {
         if(seenTags[j] == comment)
         {
            alreadySeen = true;
            break;
         }
      }
      if(!alreadySeen)
      {
         ArrayResize(seenTags, seenCount + 1);
         seenTags[seenCount] = comment;
         seenCount++;
      }
   }
   return seenCount;
}

//+------------------------------------------------------------------+
//| IsHedgeTagOpen — verifica se já existe alguma posição (deste EA)  |
//| com o comentário exato `tag`. Guard adicional (não substitui       |
//| CheckPositionCount, que só limita a CONTAGEM de pares distintos): |
//| sem isto, um sinal open_hedge repetido para o MESMO par (ex.: um  |
//| bug de duplicação a montante, ou — como observado em teste manual |
//| 2026-07-20 — várias execuções do script de teste com o mesmo par) |
//| empilharia posições adicionais em vez de ser rejeitado, porque     |
//| CountOpenHedgePairs só conta tags DISTINTAS, não deteta repetição  |
//| do mesmo tag.                                                      |
//+------------------------------------------------------------------+
bool IsHedgeTagOpen(string tag)
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(PositionGetString(POSITION_COMMENT) == tag)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| ProcessOpenSignal (HEDGE-02/RISK-07) — reverifica localmente TODOS |
//| os limites de risco essenciais antes de abrir as duas pernas.     |
//| Uma rejeição em qualquer verificação impede a ordem inteira — não  |
//| abre só uma perna.                                                 |
//+------------------------------------------------------------------+
void ProcessOpenSignal(const SSignalEvent &event)
{
   string reason;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   string tagCheck = HedgeTagPrefix + event.pair_a + "_" + event.pair_b;
   if(IsHedgeTagOpen(tagCheck))
   {
      Print("REJEITADO localmente (already_open): ", event.pair_a, "/", event.pair_b,
            " já tem uma posição de hedge aberta — sinal open_hedge ignorado "
            "(só uma posição por par de cada vez).");
      return;
   }

   int openPairs = CountOpenHedgePairs();
   if(!CheckPositionCount(openPairs, RISKGUARD_MAX_CONCURRENT_PAIRS, reason))
   {
      Print("REJEITADO localmente (", reason, "): ", event.pair_a, "/", event.pair_b);
      return;
   }

   double dailyStart, weeklyStart, absHWM;
   GetEquityTrackingValues(dailyStart, weeklyStart, absHWM);
   if(!CheckDrawdownBreaker(equity, dailyStart, weeklyStart, absHWM,
                             RISKGUARD_DAILY_DRAWDOWN_PCT, RISKGUARD_WEEKLY_DRAWDOWN_PCT,
                             RISKGUARD_ABSOLUTE_DRAWDOWN_PCT, reason))
   {
      Print("REJEITADO localmente (", reason, "): ", event.pair_a, "/", event.pair_b);
      return;
   }

   double perPairExposure[1];
   perPairExposure[0] = event.new_position_exposure_pct;
   if(!CheckExposureLimits(perPairExposure, event.aggregate_exposure_pct_after,
                            RISKGUARD_MAX_PAIR_EXPOSURE_PCT, RISKGUARD_MAX_AGGREGATE_EXPOSURE_PCT, reason))
   {
      Print("REJEITADO localmente (", reason, "): ", event.pair_a, "/", event.pair_b);
      return;
   }

   if(!CheckKillSwitch(reason))
   {
      Print("REJEITADO localmente (", reason, "): ", event.pair_a, "/", event.pair_b);
      return;
   }

   // RISK-07: conversão da fração de equity (0.25x-Kelly, decidida do
   // lado Python) para lotes concretos — exige tick_value/tick_size reais
   // do símbolo, só disponíveis aqui.
   double tickValueA = SymbolInfoDouble(event.pair_a, SYMBOL_TRADE_TICK_VALUE);
   double tickSizeA  = SymbolInfoDouble(event.pair_a, SYMBOL_TRADE_TICK_SIZE);
   double lotA = ComputeLotFromRiskFraction(equity, event.risk_fraction,
                                             event.sl_distance_price_units, tickValueA, tickSizeA);
   if(lotA <= 0.0)
   {
      Print("REJEITADO localmente: lote calculado inválido/zero para ", event.pair_a,
            " (tick_value/tick_size indisponíveis ou input degenerado)");
      return;
   }
   lotA = NormalizeLot(event.pair_a, lotA);
   double lotB = NormalizeLot(event.pair_b, lotA * event.hedge_ratio);

   // direction=+1 (long spread) -> compra pair_a, vende pair_b.
   // direction=-1 (short spread) -> vende pair_a, compra pair_b.
   // (mesma convenção de docs/hedge_engine_spec.md e evaluate_hedge_signal)
   ENUM_ORDER_TYPE typeA = (event.direction >= 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   ENUM_ORDER_TYPE typeB = (event.direction >= 0) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;

   double priceA = (typeA == ORDER_TYPE_BUY) ? SymbolInfoDouble(event.pair_a, SYMBOL_ASK)
                                              : SymbolInfoDouble(event.pair_a, SYMBOL_BID);
   double slA = (typeA == ORDER_TYPE_BUY) ? priceA - event.sl_distance_price_units
                                           : priceA + event.sl_distance_price_units;

   // Distância de stop da perna B: escalada por hedge_ratio (beta) a
   // partir da distância da perna A. Simplificação documentada (não uma
   // otimalidade matemática comprovada): dado spread = a - beta*b, esta
   // escolha garante que QUALQUER uma das duas pernas, sozinha, atingir a
   // sua distância de stop já corresponderia a um movimento adverso do
   // spread de stop_distance_price_units — ver nota em
   // src/hedge_engine.py sobre STOP_DISTANCE_STD_MULTIPLIER. Revisitar se
   // a validação em conta demo mostrar stops assimétricos a disparar de
   // forma não intencional numa perna specific.
   double slDistanceB = event.sl_distance_price_units * MathAbs(event.hedge_ratio);
   double priceB = (typeB == ORDER_TYPE_BUY) ? SymbolInfoDouble(event.pair_b, SYMBOL_ASK)
                                              : SymbolInfoDouble(event.pair_b, SYMBOL_BID);
   double slB = (typeB == ORDER_TYPE_BUY) ? priceB - slDistanceB : priceB + slDistanceB;

   if(!CheckMandatorySL(slA, reason) || !CheckMandatorySL(slB, reason))
   {
      Print("REJEITADO localmente: stop-loss calculado inválido para ", event.pair_a, "/", event.pair_b);
      return;
   }

   string tag = HedgeTagPrefix + event.pair_a + "_" + event.pair_b;

   bool okA = OpenHedgeLeg(event.pair_a, typeA, lotA, slA, tag);
   if(!okA)
   {
      Print("Falha ao abrir perna A de ", tag, " — hedge não aberto.");
      return;
   }

   bool okB = OpenHedgeLeg(event.pair_b, typeB, lotB, slB, tag);
   if(!okB)
   {
      Print("ERRO CRÍTICO: falha ao abrir perna B de ", tag,
            " — a fechar perna A imediatamente para não ficar exposto sem cobertura.");
      CloseHedgeTag(tag);
      return;
   }

   Print("HEDGE ABERTO: ", tag, " | perna A: ", event.pair_a, " ", EnumToString(typeA),
         " ", DoubleToString(lotA, 2), " lotes, SL=",
         DoubleToString(slA, (int)SymbolInfoInteger(event.pair_a, SYMBOL_DIGITS)),
         " | perna B: ", event.pair_b, " ", EnumToString(typeB), " ", DoubleToString(lotB, 2),
         " lotes, SL=", DoubleToString(slB, (int)SymbolInfoInteger(event.pair_b, SYMBOL_DIGITS)));
}

//+------------------------------------------------------------------+
//| ProcessCloseSignal (D-03/D-04/D-05) — fecha as duas pernas do par |
//| identificado pelo tag de comentário. Autoridade própria do motor  |
//| de hedge (não passa pelo motor de risco — só uma ABERTURA precisa |
//| de aprovação de risco, um fecho nunca é bloqueado).               |
//+------------------------------------------------------------------+
void ProcessCloseSignal(const SSignalEvent &event)
{
   string tag = HedgeTagPrefix + event.pair_a + "_" + event.pair_b;
   int closed = CloseHedgeTag(tag);
   if(closed > 0)
      Print("Fechado hedge ", tag, " (", closed, " perna(s)) — motivo: ", event.trigger);
   else
      Print("AVISO: sinal de fecho recebido para ", tag, " mas nenhuma posição aberta encontrada.");
}

//+------------------------------------------------------------------+
//| CloseHedgeTag — fecha todas as posições (deste EA) com o          |
//| comentário exato `tag`. Devolve o número de pernas fechadas.      |
//+------------------------------------------------------------------+
int CloseHedgeTag(string tag)
{
   int closedCount = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(PositionGetString(POSITION_COMMENT) != tag)
         continue;

      if(trade.PositionClose(ticket))
         closedCount++;
      else
         Print("ERRO ao fechar posição ", ticket, " (", tag, "): ", trade.ResultRetcodeDescription());
   }
   return closedCount;
}

//+------------------------------------------------------------------+
//| OpenHedgeLeg — envia uma perna com stop-loss obrigatório (ver      |
//| skill mql5-trading-ea). Rejeita antes de enviar se o stop estiver  |
//| ausente ou demasiado perto do preço (SYMBOL_TRADE_STOPS_LEVEL).    |
//+------------------------------------------------------------------+
bool OpenHedgeLeg(string symbol, ENUM_ORDER_TYPE orderType, double lot, double sl, string comment)
{
   lot = NormalizeLot(symbol, lot);

   if(sl <= 0.0)
   {
      Print("REJEITADO: tentativa de ordem sem stop loss em ", symbol);
      return false;
   }

   double price = (orderType == ORDER_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                                                 : SymbolInfoDouble(symbol, SYMBOL_BID);
   double minDist = MinStopDistance(symbol);
   if(MathAbs(price - sl) < minDist)
   {
      Print("REJEITADO: stop loss demasiado perto do preço em ", symbol, " (mínimo: ", minDist, ")");
      return false;
   }

   bool result = (orderType == ORDER_TYPE_BUY)
                 ? trade.Buy(lot, symbol, price, sl, 0.0, comment)
                 : trade.Sell(lot, symbol, price, sl, 0.0, comment);

   if(!result)
      Print("Falha ao enviar ordem ", symbol, ": ", trade.ResultRetcodeDescription());

   return result;
}

//+------------------------------------------------------------------+
//| Equity tracking (D-03/D-04/D-05) — persistido via GlobalVariable* |
//| (sobrevive a um restart do EA/terminal, ver docs no topo do       |
//| ficheiro) para que o disjuntor de drawdown não perca o marco de   |
//| início do dia/semana só porque o EA foi reiniciado.               |
//+------------------------------------------------------------------+

string GVDailyStart()  { return "ScalpingEA_DailyStartEquity_" + (string)MagicNumber; }
string GVWeeklyStart() { return "ScalpingEA_WeeklyStartEquity_" + (string)MagicNumber; }
string GVAbsoluteHWM() { return "ScalpingEA_AbsoluteHWM_" + (string)MagicNumber; }
string GVLastDay()     { return "ScalpingEA_LastDayOfYear_" + (string)MagicNumber; }
string GVLastWeek()    { return "ScalpingEA_LastWeekNumber_" + (string)MagicNumber; }

void InitEquityTracking()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(!GlobalVariableCheck(GVDailyStart()))
      GlobalVariableSet(GVDailyStart(), equity);
   if(!GlobalVariableCheck(GVWeeklyStart()))
      GlobalVariableSet(GVWeeklyStart(), equity);
   if(!GlobalVariableCheck(GVAbsoluteHWM()))
      GlobalVariableSet(GVAbsoluteHWM(), equity);

   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   if(!GlobalVariableCheck(GVLastDay()))
      GlobalVariableSet(GVLastDay(), now.day_of_year);
   if(!GlobalVariableCheck(GVLastWeek()))
      GlobalVariableSet(GVLastWeek(), now.day_of_year / 7);
}

//+------------------------------------------------------------------+
//| UpdateEquityTracking — deteta virada de dia/semana (heurística    |
//| simples: day_of_year/7 para "semana"; suficiente para o disjuntor |
//| D-04, não pretende ser um calendário ISO 8601 exato) e atualiza o  |
//| high-water-mark absoluto a cada chamada.                          |
//+------------------------------------------------------------------+
void UpdateEquityTracking()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   int lastDay  = (int)GlobalVariableGet(GVLastDay());
   int lastWeek = (int)GlobalVariableGet(GVLastWeek());
   int thisWeek = now.day_of_year / 7;

   if(now.day_of_year != lastDay)
   {
      GlobalVariableSet(GVDailyStart(), equity);
      GlobalVariableSet(GVLastDay(), now.day_of_year);
   }
   if(thisWeek != lastWeek)
   {
      GlobalVariableSet(GVWeeklyStart(), equity);
      GlobalVariableSet(GVLastWeek(), thisWeek);
   }

   double hwm = GlobalVariableGet(GVAbsoluteHWM());
   if(equity > hwm)
      GlobalVariableSet(GVAbsoluteHWM(), equity);
}

void GetEquityTrackingValues(double &dailyStart, double &weeklyStart, double &absHWM)
{
   dailyStart  = GlobalVariableGet(GVDailyStart());
   weeklyStart = GlobalVariableGet(GVWeeklyStart());
   absHWM      = GlobalVariableGet(GVAbsoluteHWM());
}
//+------------------------------------------------------------------+
