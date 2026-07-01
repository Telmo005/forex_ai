# docs/risk_engine_mql5_spec.md

Especificação para a camada 3 (`src/risk_engine.py`) e camada 4
(`mql5/ScalpingEA.mq5` + `mql5/RiskGuard.mqh`) — ver `ARCHITECTURE.md`.
Esta é a camada mais importante do ponto de vista de segurança: tudo
aqui é determinístico, sem ML, e deve continuar a funcionar mesmo se as
camadas anteriores falharem ou enviarem algo inesperado.

## Responsabilidades do motor de risco (lado Python, `risk_engine.py`)

Recebe ordens propostas da camada 2 (hedge) e/ou camada 1 (ML direto,
se houver estratégias não-hedge também) e decide: aprovar com que
tamanho, rejeitar, ou pedir confirmação humana.

- **Dimensionamento via Kelly fracionado**: calcular o Kelly ótimo a
  partir do win-rate e payoff ratio histórico (vindos do backtest da
  camada 1/2), mas usar só uma fração dele (sugestão inicial: 0.25x a
  0.5x Kelly) — Kelly puro é matematicamente ótimo para crescimento de
  longo prazo mas implica drawdowns muito maiores do que a maioria das
  pessoas tolera psicologicamente, e os parâmetros de win-rate/payoff
  são estimativas com erro, não valores exatos.
- **Exposição máxima**: limite de exposição total (soma de todas as
  posições abertas, ajustada por correlação — duas posições em pares
  correlacionados contam mais para o limite do que duas descorrelacionadas)
  e limite por par individual.
- **Drawdown diário/semanal máximo**: se atingido, bloquear novas
  ordens até ao próximo período (ex.: dia seguinte) — circuit breaker
  automático.
- **Número máximo de posições simultâneas.**
- **Kill-switch manual**: um mecanismo simples (ex.: ficheiro
  `KILL_SWITCH.flag` cuja presença bloqueia tudo) que tu consegues
  acionar imediatamente sem precisar de mexer em código.

## Responsabilidades do EA em MQL5 (lado execução)

O EA NÃO deve confiar cegamente em qualquer sinal recebido — deve ter as
suas próprias verificações redundantes, porque é a última linha de
defesa antes de uma ordem real ser enviada à corretora.

- **Conta em modo hedging**: confirmar com `AccountInfoInteger
  (ACCOUNT_MARGIN_MODE)` que a conta está em `ACCOUNT_MARGIN_MODE_RETAIL_HEDGING`,
  não netting — sem isto, posições opostas no mesmo par fecham-se
  automaticamente em vez de coexistirem, o que quebra a lógica de hedge.
- **Normalização de lote e stops**: usar sempre
  `SYMBOL_VOLUME_MIN`/`MAX`/`STEP` e `SYMBOL_TRADE_STOPS_LEVEL` antes de
  enviar qualquer ordem — corretoras rejeitam ordens com lote ou stop
  fora destes limites, e isto tem de ser tratado programaticamente, não
  assumido.
- **Repetição das regras de risco localmente**: mesmo que o motor de
  risco em Python já tenha aprovado a ordem, o EA deve verificar
  novamente os limites de exposição/drawdown antes de executar — isto é
  redundância intencional. Se o processo Python enviar algo
  inconsistente com o estado real da conta (ex.: por causa de uma falha
  de sincronização), o EA deve recusar, não confiar cegamente.
- **Stop loss obrigatório em toda ordem**: nunca enviar uma ordem sem
  stop loss definido, mesmo que a estratégia seja "hedge-se sozinho" —
  falhas de comunicação ou bugs podem deixar uma perna sem a outra por
  tempo suficiente para causar dano.
- **Heartbeat / timeout**: se o EA não receber sinal do Python há mais
  de X segundos (ex.: 30s), entrar em modo "só gestão" — continuar a
  gerir posições já abertas (aplicar stops, fechar por tempo) mas não
  abrir novas, até a ligação ser restabelecida.

## Comunicação Python <-> MQL5 (decisão em aberto, ver ARCHITECTURE.md)

Duas opções viáveis:

1. **Ficheiro partilhado** (mais simples): Python escreve um JSON/CSV
   com a ordem aprovada numa pasta partilhada (ex.: `MQL5/Files/`), EA
   faz polling a cada N ms com `FileIsExist`/`FileOpen`. Latência ~ alguns
   centenas de ms a 1s — aceitável para a maioria dos casos, pode ser
   insuficiente para scalping muito agressivo (M1 com alvo de poucos
   pips).
2. **Socket TCP local ou ZeroMQ**: latência muito menor (ms), mas exige
   uma DLL/biblioteca de socket no MQL5 (ex.: `mql-zmq`) — mais setup,
   mais robusto a longo prazo.

Recomendação: começar com ficheiro partilhado para validar toda a lógica
end-to-end em demo, migrar para socket só se a latência se mostrar
insuficiente na prática.

## Checklist antes de ligar a uma conta REAL (não negociável)

- [ ] Backtest walk-forward das camadas 1+2 com custos de transação
      realistas, em pelo menos 6-12 meses de dados fora da amostra.
- [ ] Motor de risco testado isoladamente com ordens sintéticas
      adversariais (tamanhos absurdos, ordens duplicadas, símbolos
      inválidos) — confirmar que rejeita corretamente.
- [ ] EA a correr em conta DEMO durante um período mínimo (sugestão:
      4-6 semanas) com monitorização diária.
- [ ] Kill-switch testado e confirmado a funcionar instantaneamente.
- [ ] Alertas configurados (ex.: notificação por Telegram/email) para:
      drawdown a aproximar-se do limite, EA desligado inesperadamente,
      perda de ligação Python<->MQL5.
