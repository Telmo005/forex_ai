# REQUIREMENTS.md

## v1 Requirements

Mínimo para considerar o sistema seguro para negociar em conta demo, por ordem de prioridade de risco decrescente.

### Risk Engine (motor de risco determinístico)

- [ ] **RISK-01**: Sistema aplica stop-loss determinístico em toda ordem, independente de qualquer inferência de ML
- [ ] **RISK-02**: Sistema dimensiona posições via Kelly fracionário (0.25x–0.5x, nunca Kelly completo)
- [ ] **RISK-03**: Sistema impõe limites de exposição máxima por par e agregada, ajustados por correlação entre pares
- [ ] **RISK-04**: Sistema impõe disjuntor de drawdown máximo diário/semanal que bloqueia novas ordens até reset
- [ ] **RISK-05**: Sistema limita o número máximo de posições simultâneas abertas
- [ ] **RISK-06**: Utilizador consegue parar instantaneamente todas as novas ordens via kill-switch (ficheiro), verificado tanto pelo lado Python como pelo EA
- [ ] **RISK-07**: EA verifica de forma redundante e independente os limites de risco antes de cada ordem, sem confiar cegamente no que o Python envia
- [ ] **RISK-08**: Motor de risco (Python + MQL5) é testado contra ordens sintéticas adversariais (lotes excessivos, ordens duplicadas, símbolos inválidos) antes de qualquer uso com dados reais
- [ ] **RISK-09**: `risk_engine.py` e `RiskGuard.mqh` nunca importam nem ramificam com base em saída de `ml_model.py` — garantido estruturalmente, não só por convenção

### Hedge Engine de Produção

- [ ] **HEDGE-01**: `hedge_engine.py` de produção só consome parâmetros de estratégia ✅ Aprovados no dashboard (nunca parâmetros gerados ao vivo sem aprovação)
- [ ] **HEDGE-02**: `hedge_engine.py` propõe ordens mas nunca as executa diretamente — execução passa sempre pelo motor de risco
- [ ] **HEDGE-03**: `hedge_engine.py` revalida cointegração periodicamente em vez de a tratar como propriedade permanente de um par

### Validação Walk-Forward

- [ ] **VALID-01**: Estratégias aprovadas no laboratório passam por revalidação walk-forward out-of-sample em dados de mercado reais (não sintéticos) antes de chegarem a produção
- [ ] **VALID-02**: `backtest_engine.py` modela custos de transação (spread, slippage, comissão) em toda validação — incluindo qualquer gate automático futuro, não só o gate manual

### Expert Advisor (MQL5)

- [ ] **EA-01**: EA confirma que a conta MT5 está em modo hedging (não netting) antes de executar qualquer lógica multi-perna
- [ ] **EA-02**: EA normaliza lote e stops conforme as especificações do símbolo da corretora em toda ordem, sem exceções
- [ ] **EA-03**: EA aplica stop-loss obrigatório em toda ordem colocada
- [ ] **EA-04**: EA entra em modo "apenas gestão" quando a ligação ao Python expira (heartbeat) — continua a aplicar stops/saídas por tempo às posições existentes, não abre novas, não fecha à força
- [ ] **EA-05**: Comunicação Python↔MQL5 usa ficheiros partilhados com timestamp; EA verifica a idade do sinal de forma independente antes de agir sobre ele

### Alertas

- [ ] **ALERT-01**: Sistema envia alerta (Telegram/email) quando o drawdown se aproxima do limite, o EA para inesperadamente, ou a ligação Python↔MQL5 cai

## v2 Requirements (deferred)

Adicionar depois do v1 estar a correr de forma estável em conta demo.

- [ ] **ML-01**: Modelo de ML baseline (gradient boosting, ex. LightGBM) fornece sinal direcional e confiança ao motor de hedge — trigger: motor de hedge + motor de risco estáveis em demo sem ML
- [ ] **REGIME-01**: Deteção de regime em tempo real (HMM) está ligada à seleção ativa de estratégia, não é só uma etiqueta offline — trigger: 2+ estratégias aprovadas e revalidadas out-of-sample
- [ ] **REGIME-02**: Quando nenhuma estratégia aprovada serve o regime atual, sistema reduz exposição a zero — nunca inventa nem implanta uma estratégia nova ao vivo
- [ ] **AUTOGATE-01**: Gate de validação automática (sem aprovação manual) promove candidatos gerados em background usando os mesmos critérios objetivos do dashboard, mais uma passagem out-of-sample extra e um período de sombra — nunca mais permissivo que o gate manual — trigger: gate manual + revalidação OOS provados fiáveis
- [ ] **DIVERGE-01**: Sistema monitoriza divergência entre a performance ao vivo e o esperado pelo backtest (limiar em janela deslizante) e reage trocando de estratégia já aprovada ou reduzindo exposição — trigger: motor de hedge a correr ao vivo (demo) tempo suficiente para ter baseline de comparação

## Out of Scope

- **Garantia de lucro contínuo ou "zero perdas"** — estatisticamente impossível para qualquer sistema de trading real; promover isso geraria excesso de confiança e menos monitorização
- **Aprendizagem online contínua (atualização de pesos a cada tick)** — torna a validação walk-forward sem sentido e tende a ajustar-se a ruído em scalping; substituída por retreino periódico com revalidação completa
- **Deep learning como modelo de ML inicial** — precisa de mais dados do que está realisticamente disponível, mais difícil de validar/depurar; gradient boosting primeiro, deep learning só se houver lacuna de capacidade comprovada
- **Deteção de decaimento estilo CUSUM** — mais rigorosa estatisticamente mas adiada até o monitor de limiar simples (DIVERGE-01) estar provado em operação real
- **Upgrade para comunicação via socket/ZeroMQ** — adiado até a latência da comunicação por ficheiro se provar insuficiente, medida e não assumida
- **Substituir completamente a revisão manual pelo gate automático** — remove a última verificação qualitativa humana; o gate automático complementa, nunca substitui, o dashboard manual
- **Implantar estratégias novas e não testadas ao vivo durante uma sequência de perdas** — viola a Core Value do projeto (nenhum capital real em estratégia não validada)
- **Fechar todas as posições instantaneamente em qualquer desconexão Python↔EA** — pode realizar perda assimétrica numa perna do hedge; usa-se antes o modo "apenas gestão"
- **Trading multi-conta ou multi-utilizador** — sistema pessoal de um único trader

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| VALID-01 | Phase 1 | Pending |
| VALID-02 | Phase 1 | Pending |
| RISK-01 | Phase 2 | Pending |
| RISK-02 | Phase 2 | Pending |
| RISK-03 | Phase 2 | Pending |
| RISK-04 | Phase 2 | Pending |
| RISK-05 | Phase 2 | Pending |
| RISK-06 | Phase 2 | Pending |
| RISK-07 | Phase 2 | Pending |
| RISK-08 | Phase 2 | Pending |
| RISK-09 | Phase 2 | Pending |
| HEDGE-01 | Phase 3 | Pending |
| HEDGE-02 | Phase 3 | Pending |
| HEDGE-03 | Phase 3 | Pending |
| EA-01 | Phase 4 | Pending |
| EA-02 | Phase 4 | Pending |
| EA-03 | Phase 4 | Pending |
| EA-04 | Phase 4 | Pending |
| EA-05 | Phase 4 | Pending |
| ALERT-01 | Phase 4 | Pending |

**Coverage:** 20/20 v1 requirements mapped. No orphans.

v2 requirements (ML-01, REGIME-01, REGIME-02, AUTOGATE-01, DIVERGE-01) are intentionally unmapped — deferred to a future milestone per their stated triggers, after Phases 1-4 run stably on a demo account.

---
*Last updated: 2026-06-30*
