# CLAUDE.md

Este ficheiro é lido automaticamente pelo Claude Code no início de cada
sessão neste projeto. Mantém-no atualizado à medida que o projeto avança.

## O que é este projeto

Sistema de trading algorítmico para forex, focado em scalping, com:
- Deteção de padrões e regime de mercado via ML (sem depender de
  indicadores fixos tipo RSI/MACD como sinal primário).
- Motor de hedge estatístico entre pares correlacionados/cointegrados.
- Motor de risco com regras determinísticas (não-ML) como camada de
  segurança final.
- Execução via MQL5 (Expert Advisor) ligado a uma corretora real via MT5.

Ver `ARCHITECTURE.md` para o desenho completo das 5 camadas e
`ROADMAP.md` para o estado atual e próximos passos.

## Stack

- **Python 3.10+** para investigação, features, treino de modelo e motor
  de hedge (`pandas`, `numpy`, `statsmodels`, `hmmlearn`, `scikit-learn`,
  `pyarrow`, `MetaTrader5` no Windows).
- **MQL5** para o Expert Advisor de execução (corre dentro do terminal
  MetaTrader 5, deve funcionar de forma autónoma mesmo se o processo
  Python cair).
- Comunicação Python <-> MQL5: ficheiros/parquet partilhados ou
  socket/ZeroMQ local (decisão em aberto, ver `docs/risk_engine_mql5_spec.md`).

## Estrutura de pastas

```
src/                 código Python (pipeline, motor de hedge, modelo ML)
docs/                especificações técnicas de cada componente
.claude/skills/      conhecimento de domínio para o Claude Code consultar
output/              dados gerados (parquet, csv) — não versionar (.gitignore)
mql5/                Expert Advisor e includes MQL5 (a criar)
```

## Como correr o que já existe

```bash
pip install -r requirements.txt
python src/data_pipeline.py --mode synth        # teste sem corretora
python src/data_pipeline.py --mode mt5 --login ... --password ... --server ...

python src/strategy_generator.py --max-pairs 4 --n-generations 2   # gera e testa estratégias
streamlit run dashboard.py                                          # UI para validar resultados
```

## Convenções e regras do projeto (importante)

1. **Risco nunca depende só do modelo de ML.** Qualquer lógica de
   stop-loss, drawdown máximo ou limite de exposição tem de existir como
   regra determinística no motor de risco/EA, independente de qualquer
   inferência de modelo. Ver `docs/risk_engine_mql5_spec.md`.
2. **Validação é sempre walk-forward.** Nunca aceitar um backtest com
   split aleatório train/test em série temporal — usar sempre janelas
   deslizantes no tempo, treinar no passado, testar no futuro.
3. **Toda estratégia de hedge tem de citar o gatilho matemático exato**
   (z-score do spread, limiar de correlação, stop de tempo) — não "fechar
   quando parecer bom". Ver `docs/hedge_engine_spec.md`.
4. **Custos de transação entram sempre no backtest** (spread, slippage,
   comissão da corretora), nunca um backtest "limpo" sem custos —
   especialmente crítico em scalping, onde os custos podem consumir todo
   o edge.
5. **Conta MT5 em modo hedging**, não netting, é pré-requisito para a
   lógica de hedge multi-perna funcionar. Confirmar isto com a corretora
   antes de implementar execução real.
6. Antes de qualquer alteração ao motor de risco ou ao EA que vá tocar
   numa conta com dinheiro real, testar exaustivamente em conta demo
   primeiro. Nunca passar de demo para real sem o utilizador confirmar
   explicitamente.
7. **Nenhum parâmetro de estratégia vai para `hedge_engine.py` de
   produção sem primeiro aparecer como ✅ Aprovada no
   `dashboard.py`.** O laboratório de estratégias (`src/strategy_generator.py`)
   existe exatamente para evitar escolher limiares "a olho".

## Skills disponíveis neste projeto

- `quant-finance-math` — fórmulas e padrões de código para cointegração,
  filtro de Kalman, HMM, Kelly criterion. Consulta sempre que escreveres
  ou alterares lógica estatística/matemática.
- `mql5-trading-ea` — padrões de MQL5 para EAs de trading: gestão de
  posições em modo hedging, normalização de lote, comunicação com
  Python, gestão de risco no lado da execução.

## Estado atual

Ver `ROADMAP.md`. Resumo: pipeline de dados (camada 0) e laboratório de
estratégias com UI de validação (camada 0.5) estão construídos e
testados com dados sintéticos — incluindo o ciclo completo gerar →
testar → reprovar → mutar → testar de novo. Próxima camada a
implementar: motor de risco (recomendado primeiro) ou modelo de ML.
