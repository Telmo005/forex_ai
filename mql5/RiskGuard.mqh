//+------------------------------------------------------------------+
//|                                                  RiskGuard.mqh   |
//+------------------------------------------------------------------+
//
// RiskGuard.mqh
// ==============
// Última linha de defesa do motor de risco (camada 4, MQL5) — reverifica
// de forma INDEPENDENTE todos os limites de risco antes de qualquer
// ordem, nunca confiando cegamente nos valores que o lado Python
// (`src/risk_engine.py`, Fase 2 Plano 01) envia através da futura ponte
// de ficheiros (RISK-07). O Python já deve ter aprovado a ordem — este
// ficheiro assume que pode estar errado, corrompido, ou simplesmente
// ausente (processo Python em baixo), e reconfirma tudo a partir do seu
// próprio estado de conta/posições.
//
// DESENHO: todas as funções de DECISÃO de risco são FUNÇÕES PURAS —
// recebem os valores explicitamente como parâmetros double/string/bool
// e devolvem bool + uma razão de rejeição por parâmetro de referência
// (&rejectReason). NENHUMA delas chama AccountInfoDouble/PositionsTotal/
// SymbolInfoDouble internamente (02-RESEARCH.md Pitfall 2) — fazer isso
// tornaria estas funções impossíveis de testar fora de um terminal MT5
// ao vivo, exatamente a lacuna que esta fase fecha. A recolha do estado
// real da conta/posições é responsabilidade do futuro EA (Fase 4,
// `ScalpingEA.mq5`), não deste ficheiro.
//
// EXCEÇÕES: NormalizeLot() e MinStopDistance() legitimamente chamam
// SymbolInfoDouble/SymbolInfoInteger — são normalizadores de
// especificação da corretora (broker spec), não lógica de decisão de
// risco. Isto é o alvo do teste de "lote excessivo" do RISK-08.
// CheckKillSwitch() é a ÚNICA função de decisão que toca o filesystem
// (FileIsExist), porque essa é a natureza literal do mecanismo D-09 —
// mantida deliberadamente mínima.
//
// IMPORTANTE (RISK-06 / Pitfall 5 de 02-RESEARCH.md): todos os
// disjuntores de drawdown e o kill-switch BLOQUEIAM APENAS novas ordens
// — nunca fecham posições existentes. "Fechar tudo instantaneamente"
// pode realizar uma perda assimétrica numa perna de um hedge. Nenhuma
// função aqui tem qualquer efeito sobre posições já abertas.
//
// RISK-09 (estrutural, não apenas convenção): este ficheiro NUNCA
// referencia qualquer módulo ou saída de inferência de Machine Learning
// (o módulo de deteção de padrões/regime da Camada 1 do projeto). Toda a
// lógica aqui é determinística, baseada em limites fixos (D-01..D-14).
//
// SINCRONIZAÇÃO DE CONSTANTES (crítico): os valores D-01..D-14 abaixo
// são uma DUPLICAÇÃO À MÃO dos mesmos valores em `src/risk_limits.py`
// (Fase 2 Plano 01, já finalizado e commitado). Não existe (nem pode
// existir) um mecanismo de importação automática entre Python e MQL5 —
// são linguagens/runtimes completamente distintos. `02-CONTEXT.md` é a
// fonte de verdade única; após qualquer alteração aos valores em
// risk_limits.py, este ficheiro tem de ser atualizado manualmente e
// ambos os lados confirmados como concordantes (checklist de revisão de
// código, não um passo de geração automática — ver 02-RESEARCH.md
// "Don't Hand-Roll").
//+------------------------------------------------------------------+
#property strict

// ============================================================================
// Limites de risco D-locked (hand-duplicados de src/risk_limits.py)
// Fonte de verdade única: 02-CONTEXT.md (D-01 a D-14)
// ============================================================================

#define RISKGUARD_KELLY_FRACTION            0.25   // D-01 — fração de Kelly conservadora (0.25x-0.5x, extremo conservador)
#define RISKGUARD_MAX_PAIR_EXPOSURE_PCT      0.05   // D-06 — exposição máxima por par individual, 5% do equity
#define RISKGUARD_MAX_AGGREGATE_EXPOSURE_PCT 0.15   // D-07 — exposição agregada máxima ajustada por correlação, 15% do equity
#define RISKGUARD_DAILY_DRAWDOWN_PCT         0.03   // D-03 — drawdown diário máximo, bloqueia novas ordens até ao próximo dia
#define RISKGUARD_WEEKLY_DRAWDOWN_PCT        0.08   // D-04 — drawdown semanal máximo, bloqueia novas ordens até à próxima semana
#define RISKGUARD_ABSOLUTE_DRAWDOWN_PCT      0.20   // D-05 — drawdown absoluto/HWM máximo, kill-switch permanente (reset manual)
#define RISKGUARD_MAX_CONCURRENT_PAIRS       3      // D-08 — máximo de pares de hedge simultâneos (3 pares / 6 pernas)
#define RISKGUARD_ALERT_THRESHOLD_PCT        0.80   // D-11 — 80% do limite mais próximo (dado exposto; Fase 4/ALERT-01 decide o alerta)
#define RISKGUARD_HEARTBEAT_TIMEOUT_SECONDS  30     // D-12 — timeout do heartbeat Python<->MQL5

// Caminho do ficheiro de kill-switch (D-09). Verificado via FILE_COMMON
// (pasta "Common\Files" partilhada do terminal MT5) — cada lado
// (Python/MQL5) faz a sua PRÓPRIA chamada de existência de ficheiro,
// nunca confiando no resultado do outro lado.
#define RISKGUARD_KILL_SWITCH_FILENAME       "KILL_SWITCH.flag"   // D-09


//+------------------------------------------------------------------+
//| CheckDrawdownBreaker (D-03/D-04/D-05, RISK-04, RISK-07)          |
//| Função PURA: nenhuma chamada a AccountInfoDouble. O chamador     |
//| (futuro EA, Fase 4) tem de recolher os valores reais e passá-los |
//| explicitamente. Verifica o drawdown ABSOLUTO primeiro porque é a |
//| condição de kill-switch PERMANENTE (D-05), mais severa que os    |
//| bloqueios temporários diário/semanal.                            |
//+------------------------------------------------------------------+
bool CheckDrawdownBreaker(double equity,
                          double dailyStartEquity,
                          double weeklyStartEquity,
                          double absoluteHWM,
                          double dailyDDPct,
                          double weeklyDDPct,
                          double absoluteDDPct,
                          string &rejectReason)
{
   double dailyDD    = (dailyStartEquity > 0.0) ? (dailyStartEquity - equity) / dailyStartEquity : 0.0;
   double weeklyDD   = (weeklyStartEquity > 0.0) ? (weeklyStartEquity - equity) / weeklyStartEquity : 0.0;
   double absoluteDD = (absoluteHWM > 0.0) ? (absoluteHWM - equity) / absoluteHWM : 0.0;

   // D-05: drawdown absoluto — kill-switch permanente, verificado primeiro
   if(absoluteDD >= absoluteDDPct)
   {
      rejectReason = "absolute_drawdown_kill_switch";
      return false;
   }
   // D-03: drawdown diário — bloqueia até ao próximo dia
   if(dailyDD >= dailyDDPct)
   {
      rejectReason = "daily_drawdown_breaker";
      return false;
   }
   // D-04: drawdown semanal — bloqueia até à próxima semana
   if(weeklyDD >= weeklyDDPct)
   {
      rejectReason = "weekly_drawdown_breaker";
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckExposureLimits (D-06/D-07, RISK-03, RISK-07)                |
//| Função PURA: recebe as exposições já calculadas pelo chamador (o  |
//| EA recompute a partir de PositionsTotal()/PositionGetDouble(),   |
//| nunca a partir de um número vindo do Python). Verifica o limite  |
//| por par individual (D-06) e o limite agregado ajustado por       |
//| correlação (D-07) — a agregação em si (variance-scaling, D-07)   |
//| já deve ter sido aplicada pelo chamador antes de invocar esta     |
//| função (mesma fórmula documentada em src/risk_limits.py:         |
//| CORRELATION_ADJUSTMENT = "variance_scaling").                    |
//|                                                                    |
//| DESVIO DELIBERADO E ACEITE de RISK-07 só para D-07 (02-REVIEW.md, |
//| Fase 2, iteração 1): ao contrário de CheckDrawdownBreaker (que    |
//| recalcula dailyDD/weeklyDD/absoluteDD a partir de equity bruto) e |
//| de CheckPositionCount (que recebe uma contagem inteira crua), esta|
//| função NÃO recalcula o ajuste de correlação a partir de dados     |
//| primitivos — recebe aggregateAdjustedPct já pronto. Isto significa|
//| que, para D-07 especificamente, a redundância independente de     |
//| RISK-07 é mais fraca: se a aritmética de correlação em Python     |
//| (aggregate_exposure_pct(), src/risk_engine.py) tiver um bug, este  |
//| lado MQL5 não tem forma de o apanhar sozinho — só reconfirma os    |
//| dois limiares (por par e agregado) contra o valor já calculado.   |
//|                                                                    |
//| PORQUÊ ACEITE (não corrigido nesta fase): recalcular a agregação   |
//| de correlação exigiria que o EA tivesse a sua própria matriz de    |
//| correlação/cointegração (produto da Camada 0, `data_pipeline.py`), |
//| que não existe em MQL5 e não faz parte do âmbito desta fase — dar  |
//| ao EA essa matriz por parâmetro (ex.: um array de pares de string  |
//| + doubles) violaria o princípio "função pura, sem I/O escondido"   |
//| só para simular um passo de agregação que, para ser verdadeiramente|
//| independente, precisaria de dados que este ficheiro simplesmente    |
//| não tem ainda. Manter o pré-agregado como input é a opção          |
//| estruturalmente correta HOJE; a alternativa correta a médio prazo  |
//| (não implementada aqui) é a Fase 3/4 fazer a ponte de ficheiros    |
//| Python->MQL5 também publicar um snapshot periódico da matriz de    |
//| correlação (refrescado, não em tempo real) para que o EA possa     |
//| recalcular aggregateAdjustedPct a partir de dados primitivos, tal  |
//| como já faz para drawdown e contagem de posições — ver             |
//| docs/risk_engine_mql5_spec.md e 02-REVIEW.md (WARNING 3) para o    |
//| racional completo desta decisão.                                   |
//+------------------------------------------------------------------+
bool CheckExposureLimits(double &perPairExposurePcts[],
                         double aggregateAdjustedPct,
                         double maxPairPct,
                         double maxAggPct,
                         string &rejectReason)
{
   int n = ArraySize(perPairExposurePcts);
   for(int i = 0; i < n; i++)
   {
      if(perPairExposurePcts[i] > maxPairPct)
      {
         rejectReason = "max_pair_exposure";
         return false;
      }
   }
   if(aggregateAdjustedPct > maxAggPct)
   {
      rejectReason = "max_aggregate_exposure";
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckPositionCount (D-08, RISK-05, RISK-07)                      |
//| Função PURA: recebe a contagem já apurada pelo chamador (o EA     |
//| conta a partir do seu próprio PositionsTotal(), nunca confia num  |
//| número vindo do Python). Rejeita quando abrir mais uma posição    |
//| excederia o limite de pares concorrentes.                        |
//+------------------------------------------------------------------+
bool CheckPositionCount(int openPairs, int maxPairs, string &rejectReason)
{
   if(openPairs >= maxPairs)
   {
      rejectReason = "max_concurrent_pairs";
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckKillSwitch (D-09, RISK-06)                                  |
//| A ÚNICA função de decisão neste ficheiro que toca o filesystem —  |
//| é a natureza literal do mecanismo D-09. Faz a SUA PRÓPRIA         |
//| chamada FileIsExist (FILE_COMMON), nunca confiando num booleano   |
//| vindo do lado Python (RISK-07: redundância independente). A mera  |
//| presença do ficheiro bloqueia toda NOVA ordem — nunca fecha       |
//| posições existentes (RISK-06/Pitfall 5 de 02-RESEARCH.md).        |
//+------------------------------------------------------------------+
bool CheckKillSwitch(string &rejectReason)
{
   if(FileIsExist(RISKGUARD_KILL_SWITCH_FILENAME, FILE_COMMON))
   {
      rejectReason = "kill_switch";
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| CheckMandatorySL (RISK-01)                                       |
//| Função PURA: rejeita qualquer ordem sem um preço de stop-loss     |
//| positivo. Independente de qualquer inferência de ML (RISK-09) —  |
//| esta verificação nunca é ignorada, seja qual for a origem da      |
//| ordem proposta.                                                   |
//+------------------------------------------------------------------+
bool CheckMandatorySL(double slPrice, string &rejectReason)
{
   if(slPrice <= 0.0)
   {
      rejectReason = "missing_stop_loss";
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| NormalizeLot — normalizador de especificação da corretora        |
//| (NÃO é lógica de decisão de risco; legitimamente lê               |
//| SymbolInfoDouble porque o clamp de lote depende da especificação  |
//| do símbolo na corretora em runtime). Copiado verbatim do padrão   |
//| já documentado em .claude/skills/mql5-trading-ea/SKILL.md. Alvo   |
//| do teste de "lote excessivo" do RISK-08 (D-14).                   |
//+------------------------------------------------------------------+
double NormalizeLot(string symbol, double lot)
{
   double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   if(lotStep <= 0.0)
      lotStep = 0.01;   // salvaguarda defensiva; nunca dividir por zero

   lot = MathRound(lot / lotStep) * lotStep;
   lot = MathMax(minLot, MathMin(maxLot, lot));
   return lot;
}

//+------------------------------------------------------------------+
//| MinStopDistance — normalizador de especificação da corretora     |
//| (NÃO é lógica de decisão de risco; legitimamente lê                |
//| SymbolInfoInteger/SymbolInfoDouble porque a distância mínima de   |
//| stop é uma regra da corretora, não um limite de risco do          |
//| projeto). Copiado verbatim do padrão já documentado em            |
//| .claude/skills/mql5-trading-ea/SKILL.md.                          |
//+------------------------------------------------------------------+
double MinStopDistance(string symbol)
{
   long   stopLevel = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double point      = SymbolInfoDouble(symbol, SYMBOL_POINT);
   return stopLevel * point;
}
//+------------------------------------------------------------------+
