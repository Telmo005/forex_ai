# forex_ai

Sistema de trading algorítmico para forex (scalping), com deteção de
regime via ML, hedge estatístico entre pares cointegrados, e motor de
risco determinístico como camada de segurança. Ver `ARCHITECTURE.md`
para o desenho completo do sistema e `ROADMAP.md` para o estado atual.

## Começar

```bash
pip install -r requirements.txt
python src/data_pipeline.py --mode synth     # testar sem corretora

python src/strategy_generator.py --max-pairs 4 --n-generations 2
streamlit run dashboard.py                    # abre a UI de validação no browser
```

Ver `src/data_pipeline.py` docstring e `ROADMAP.md` para os próximos
passos. Para usar com a tua corretora real (MT5, Windows):

```bash
pip install MetaTrader5
python src/data_pipeline.py --mode mt5 --login SEU_LOGIN --password "SUA_SENHA" --server "NomeDoServidor"
```

## A UI de validação (`dashboard.py`)

Depois de correr `src/strategy_generator.py`, abre `streamlit run
dashboard.py` no browser. Vais ver: quantas estratégias foram testadas,
quantas passaram/falharam, e uma tabela com todas elas (par, profit
factor, win rate, Sharpe, drawdown, nº de trades, duração testada).
Clica numa estratégia para veres o motivo exato de reprovação (se
falhou) ou a curva de equity completa e o histórico de trades (se
passou). Nenhum parâmetro deve ir para produção sem aparecer aqui como
✅ Aprovada primeiro.

## Estrutura do projeto

```
CLAUDE.md                       contexto lido automaticamente pelo Claude Code
ARCHITECTURE.md                 desenho completo do sistema (5 camadas + lab)
ROADMAP.md                      estado atual e próximos passos
requirements.txt
.gitignore
dashboard.py                     UI (Streamlit) de validação de estratégias

src/
  data_pipeline.py               camada 0 - feito
  backtest_engine.py             camada 0.5 - feito (motor de backtest sem lookahead)
  strategy_registry.py           camada 0.5 - feito (persistência SQLite)
  strategy_generator.py          camada 0.5 - feito (gera → testa → muta)
  ml_model.py                    camada 1 - por fazer
  hedge_engine.py                camada 2 - por fazer
  risk_engine.py                 camada 3 - por fazer

mql5/                            camada 4 (EA) - por fazer

docs/
  glossary.md                    termos matemáticos/financeiros
  strategy_lab_spec.md           spec da camada 0.5 (lab + UI)
  hedge_engine_spec.md           spec da camada 2
  ml_model_spec.md               spec da camada 1
  risk_engine_mql5_spec.md       spec das camadas 3 e 4

.claude/skills/
  quant-finance-math/SKILL.md    fórmulas e código de referência (matemática)
  mql5-trading-ea/SKILL.md       padrões de código MQL5

output/                          dados gerados (gitignored), inclui strategy_lab.db
```

## Para abrir no VS Code com Claude Code

1. Abrir esta pasta como workspace no VS Code.
2. Com a extensão Claude Code instalada, o `CLAUDE.md` é lido
   automaticamente no início de cada sessão — não precisas de colar
   contexto manualmente.
3. As skills em `.claude/skills/` carregam automaticamente quando
   relevantes (ex.: ao trabalhar em `hedge_engine.py`, a skill
   `quant-finance-math` ativa-se sozinha).
4. Para continuar o desenvolvimento, pede para implementar o próximo
   item de `ROADMAP.md` — o Claude Code vai ler a spec correspondente em
   `docs/` antes de começar.
