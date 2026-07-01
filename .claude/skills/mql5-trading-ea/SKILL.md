---
name: mql5-trading-ea
description: Padrões de código MQL5 para Expert Advisors de trading - gestão de posições em conta modo hedging (não netting), normalização de lote e stops, uso da classe CTrade, verificação de regras de risco no lado da execução, e comunicação com um processo Python externo via ficheiro partilhado. Usa sempre esta skill ao escrever, alterar ou depurar qualquer ficheiro .mq5 ou .mqh neste projeto, ou ao discutir execução de ordens, gestão de posições, ou o Expert Advisor.
---

# MQL5 Trading EA

Padrões de referência para `mql5/ScalpingEA.mq5` e `mql5/RiskGuard.mqh`
(camada 4 do projeto — ver `ARCHITECTURE.md` na raiz). Esta skill
assume conhecimento básico de MQL5; foca-se nos padrões específicos
deste projeto (hedge multi-perna, comunicação com Python, risco local).

## Confirmar modo hedging da conta (pré-requisito crítico)

Sem isto, posições opostas no mesmo símbolo fecham-se automaticamente
em vez de coexistirem, o que quebra qualquer lógica de hedge:

```mql5
if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
{
    Print("ERRO: conta não está em modo hedging. EA não pode operar com segurança.");
    ExpertRemove();
}
```

## Normalização de lote e stops (obrigatório antes de qualquer ordem)

Corretoras rejeitam ordens fora destes limites — nunca assumir, sempre
consultar em runtime:

```mql5
double NormalizeLot(string symbol, double lot)
{
    double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
    double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
    double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

    lot = MathRound(lot / lotStep) * lotStep;
    lot = MathMax(minLot, MathMin(maxLot, lot));
    return lot;
}

double MinStopDistance(string symbol)
{
    long stopLevel = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
    double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
    return stopLevel * point;
}
```

## Padrão de envio de ordem com CTrade

```mql5
#include <Trade/Trade.mqh>
CTrade trade;

bool OpenHedgeLeg(string symbol, ENUM_ORDER_TYPE orderType, double lot,
                   double sl, double tp, string comment)
{
    lot = NormalizeLot(symbol, lot);

    // stop loss é obrigatório - nunca enviar ordem sem ele
    if(sl <= 0)
    {
        Print("REJEITADO: tentativa de ordem sem stop loss em ", symbol);
        return false;
    }

    double minDist = MinStopDistance(symbol);
    double price = (orderType == ORDER_TYPE_BUY)
                   ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                   : SymbolInfoDouble(symbol, SYMBOL_BID);

    if(MathAbs(price - sl) < minDist)
    {
        Print("REJEITADO: stop loss demasiado perto do preço em ", symbol,
              " (mínimo: ", minDist, ")");
        return false;
    }

    bool result = (orderType == ORDER_TYPE_BUY)
                  ? trade.Buy(lot, symbol, price, sl, tp, comment)
                  : trade.Sell(lot, symbol, price, sl, tp, comment);

    if(!result)
        Print("Falha ao enviar ordem ", symbol, ": ", trade.ResultRetcodeDescription());

    return result;
}
```

## Verificação de risco LOCAL antes de executar (redundância intencional)

O motor de risco em Python (camada 3) já deve ter aprovado a ordem, mas
o EA repete as verificações essenciais — é a última linha de defesa:

```mql5
bool PassesLocalRiskCheck(double proposedLotValue)
{
    // exemplo: limite de exposição total e de drawdown diário
    double currentExposure = CalculateTotalExposure();   // implementar conforme o projeto
    double maxExposure = AccountInfoDouble(ACCOUNT_EQUITY) * MaxExposurePct;

    if(currentExposure + proposedLotValue > maxExposure)
    {
        Print("REJEITADO localmente: excede exposição máxima.");
        return false;
    }

    if(DailyDrawdownExceeded())   // implementar: compara equity atual vs equity no início do dia
    {
        Print("REJEITADO localmente: drawdown diário máximo atingido.");
        return false;
    }

    if(FileIsExist("KILL_SWITCH.flag", FILE_COMMON))
    {
        Print("KILL SWITCH ATIVO: nenhuma ordem nova será executada.");
        return false;
    }

    return true;
}
```

## Comunicação com Python via ficheiro partilhado (opção inicial recomendada)

Ver `docs/risk_engine_mql5_spec.md` para a discussão completa
ficheiro vs. socket. Padrão de polling simples para começar:

```mql5
void OnTimer()   // configurar EventSetMillisecondTimer(500) no OnInit
{
    string filename = "signal_queue.json";
    if(FileIsExist(filename, FILE_COMMON))
    {
        int handle = FileOpen(filename, FILE_READ|FILE_TXT|FILE_COMMON);
        if(handle != INVALID_HANDLE)
        {
            string content = "";
            while(!FileIsEnding(handle))
                content += FileReadString(handle);
            FileClose(handle);

            // processar `content` (parse JSON - usar uma lib MQL5 de JSON,
            // ex. a inclusa em https://www.mql5.com/en/code/13663)
            ProcessSignal(content);

            FileDelete(filename, FILE_COMMON);   // consumir o sinal
        }
    }

    CheckHeartbeatTimeout();   // ver secção seguinte
}
```

## Heartbeat / modo "só gestão" quando a ligação cai

```mql5
datetime lastSignalTime = 0;
input int HeartbeatTimeoutSeconds = 30;

void CheckHeartbeatTimeout()
{
    if(TimeCurrent() - lastSignalTime > HeartbeatTimeoutSeconds)
    {
        // não abrir novas posições, mas continuar a gerir as existentes
        // (aplicar stops, fechar por stop de tempo, etc.)
        managementOnlyMode = true;
    }
    else
    {
        managementOnlyMode = false;
    }
}
```

## Erros comuns a vigiar nesta área

Enviar ordens sem verificar `TradeIsAllowed()` e `SymbolInfoInteger
(symbol, SYMBOL_TRADE_MODE)` primeiro (alguns símbolos podem estar
temporariamente em modo "só fecho" durante notícias). Não tratar
`TRADE_RETCODE_REQUOTE` — em scalping, requotes são frequentes e devem
ter lógica de retry com novo preço, não falhar silenciosamente.
Assumir que `FileDelete` depois de processar o sinal é atómico — em
teoria há uma janela de corrida entre o Python escrever um novo sinal e
o MQL5 ainda estar a processar o anterior; para volume baixo de sinais
(scalping não é high-frequency trading de verdade) isto raramente é
problema, mas vale ter isto em mente se a frequência de sinais aumentar.
