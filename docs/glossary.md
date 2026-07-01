# docs/glossary.md

Referência rápida dos termos matemáticos/financeiros usados neste
projeto. Consulta também a skill `quant-finance-math` para fórmulas e
código.

**Cointegração** — relação entre duas (ou mais) séries não-estacionárias
em que uma combinação linear delas é estacionária (reverte à média),
mesmo que cada série individualmente "passeie" sem limite. Base
matemática do hedge estatístico: se EURUSD e GBPUSD são cointegrados,
existe um beta tal que `EURUSD - beta*GBPUSD` oscila à volta de uma
média estável, e desvios dessa média são oportunidades de trade.

**Correlação vs. cointegração** — correlação mede se duas séries se
movem juntas no curto prazo; cointegração mede se existe uma relação de
equilíbrio de longo prazo entre elas. Pares podem ter correlação alta
sem serem cointegrados (sem reversão à média garantida), o que é mais
arriscado para hedge.

**Teste de Engle-Granger** — teste estatístico de cointegração entre
duas séries: regride uma na outra (OLS), depois testa se o resíduo dessa
regressão é estacionário (via teste ADF). P-value baixo (ex. < 0.05)
sugere cointegração.

**Hedge ratio (beta)** — coeficiente da regressão OLS entre os preços
dos dois ativos; diz quantas unidades de B comprar/vender por cada
unidade de A para que a combinação seja estacionária.

**Z-score do spread** — `(spread_atual - média_histórica) / desvio_padrão_histórico`.
Mede quantos desvios-padrão o spread está afastado do seu valor "normal"
— é o gatilho de entrada/saída do hedge estatístico.

**Filtro de Kalman** — método para estimar um valor que muda ao longo do
tempo (ex.: o hedge ratio) de forma recursiva, atualizando a estimativa
a cada novo dado em vez de recalcular do zero com uma janela fixa. Mais
adaptativo a mudanças na relação entre os pares do que um beta de OLS
fixo.

**Hidden Markov Model (HMM)** — modelo estatístico para detetar estados
ocultos (regimes de mercado) a partir de observações visíveis (retorno,
volatilidade). Assume que o mercado transita entre um número finito de
regimes, cada um com a sua própria distribuição estatística de
retornos/volatilidade.

**Processo de Ornstein-Uhlenbeck (OU)** — modelo matemático de reversão
à média em tempo contínuo. Usado para estimar a "velocidade" com que um
spread cointegrado tende a voltar à média — útil para definir o stop de
tempo de uma posição de hedge.

**Kelly criterion** — fórmula que calcula a fração ótima do capital a
apostar dado um win-rate e payoff ratio, para maximizar crescimento
geométrico de longo prazo. `f* = (bp - q) / b`, onde `b` = payoff ratio,
`p` = probabilidade de ganhar, `q = 1-p`. Na prática usa-se sempre uma
fração do Kelly completo (ex.: 0.25x-0.5x) por causa da incerteza nos
parâmetros estimados.

**Triple barrier method** — técnica para rotular dados de treino em
finanças: definir 3 barreiras (take-profit, stop-loss, tempo máximo)
para cada ponto no tempo, e usar qual delas é atingida primeiro como
label, em vez de usar o retorno bruto do próximo período.

**Walk-forward validation** — método de validação para séries temporais:
treinar numa janela de tempo, testar na janela seguinte (nunca
sobreposta nem invertida), avançar a janela, repetir. Evita a fuga de
informação (look-ahead bias) que um split aleatório train/test
introduziria.

**Profit factor** — lucro bruto total / prejuízo bruto total. Acima de 1
significa que a estratégia é lucrativa antes de custos; útil junto com
Sharpe ratio porque captura assimetria entre ganhos e perdas que o
Sharpe sozinho não mostra bem.

**Slippage** — diferença entre o preço esperado de uma ordem e o preço
realmente executado, normalmente por causa de latência ou liquidez
insuficiente no momento da execução. Especialmente relevante em
scalping, onde a margem por trade é pequena.
