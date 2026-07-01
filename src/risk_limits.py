"""
risk_limits.py
===============
Tabela de constantes nomeadas para o motor de risco determinístico
(`risk_engine.py`). Segue a convenção já estabelecida em
`backtest_engine.py` (DEFAULT_COST_PARAMS/WALK_FORWARD_CONFIG): todo
limite numérico de risco é um campo nomeado, documentado, com o ID da
decisão que o originou (`D-01` a `D-14`, ver 02-CONTEXT.md) — nunca um
"magic number" solto no meio de uma expressão.

Estes valores NÃO são reotimizáveis pelo laboratório de estratégias
(`strategy_generator.py`) nem por qualquer inferência de ML — são
limites de risco fixados por decisão humana (CLAUDE.md regra 1) e só
mudam através de uma nova decisão explícita, documentada aqui e em
02-CONTEXT.md.

IMPORTANTE: estes mesmos valores estão DUPLICADOS à mão em
`mql5/RiskGuard.mqh` (Fase 2, camada MQL5 — last-line-of-defense). Não
existe (nem pode existir) um mecanismo de partilha automática entre
Python e MQL5 — são linguagens/runtimes completamente distintos. Após
qualquer alteração aos valores abaixo, atualizar manualmente o lado
MQL5 e confirmar que ambos concordam (checklist de revisão de código,
não um passo de geração automática — ver 02-RESEARCH.md "Don't
Hand-Roll").

RISK-09 (estrutural, não apenas convenção): este módulo não importa nem
ramifica com base em `ml_model.py` ou qualquer saída de inferência de
ML. Os únicos imports permitidos são da biblioteca padrão.
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------
# Nome da abordagem de ajuste de exposição por correlação (D-07)
# --------------------------------------------------------------------------
#
# Duas abordagens equivalentes são standard na literatura de gestão de
# risco de portefólio (ver 02-RESEARCH.md "Don't Hand-Roll" + "Open
# Question 1", já resolvida): (a) escalar a exposição agregada por um
# fator (1 + correlação_média_par_a_par) — "variance-scaling"; ou (b)
# impor um teto por cluster de pares com correlação acima de um limiar
# — "cluster-cap". Este projeto adota (a), porque degrada
# graciosamente para raw_sum quando há 0-1 posições abertas (nada a
# ajustar) e é mais simples de testar unitariamente do que (b), que
# exigiria um passo extra de agrupamento por limiar. A implementação
# exata vive em risk_engine.py::aggregate_exposure_pct(); esta
# constante só nomeia a escolha para que o código e os testes possam
# citá-la sem repetir a justificação.
CORRELATION_ADJUSTMENT = "variance_scaling"  # (1 + avg_pairwise_correlation) — ver 02-RESEARCH.md


# --------------------------------------------------------------------------
# Caminho do ficheiro de kill-switch (D-09)
# --------------------------------------------------------------------------
#
# Ficheiro cuja mera PRESENÇA bloqueia toda nova ordem (verificado tanto
# por risk_engine.py como, futuramente, pelo EA em RiskGuard.mqh — cada
# lado com a sua própria chamada de existência de ficheiro, nunca
# confiando no outro). O caminho exato partilhado entre o cwd do
# processo Python e a pasta "Common\Files" do MQL5 (FILE_COMMON) só
# fica definitivo quando a ponte de ficheiros da Fase 3/4 for
# construída (02-RESEARCH.md Open Question 2, já resolvida como "manter
# configurável"). Por isso este valor é um DEFAULT de parâmetro, nunca
# hard-coded dentro da lógica de decisão — qualquer chamador pode
# passar outro caminho (ex.: um ficheiro temporário em testes).
KILL_SWITCH_PATH = "KILL_SWITCH.flag"  # D-09 — default sobreponível, não fixo na lógica


@dataclass
class RiskLimits:
    """Limites de risco determinísticos, todos com origem em decisões
    explícitas registadas em 02-CONTEXT.md. Instanciar sem argumentos
    devolve exatamente os valores D-locked; qualquer override deve ser
    feito explicitamente pelo chamador (ex.: em testes adversariais) e
    nunca a partir de uma inferência de modelo (RISK-09).
    """

    kelly_fraction: float = 0.25              # D-01 — fração de Kelly conservadora (extremo do intervalo 0.25x-0.5x)
    max_pair_exposure_pct: float = 0.05       # D-06 — exposição máxima por par individual, 5% do equity
    max_aggregate_exposure_pct: float = 0.15  # D-07 — exposição agregada máxima, ajustada por correlação, 15% do equity
    daily_drawdown_pct: float = 0.03          # D-03 — drawdown diário máximo, bloqueia novas ordens até ao próximo dia
    weekly_drawdown_pct: float = 0.08         # D-04 — drawdown semanal máximo, bloqueia novas ordens até à próxima semana
    absolute_drawdown_pct: float = 0.20       # D-05 — drawdown absoluto/HWM máximo, kill-switch permanente (reset manual)
    max_concurrent_pairs: int = 3             # D-08 — máximo de pares de hedge simultâneos (3 pares / 6 pernas)
    alert_threshold_pct_of_limit: float = 0.80  # D-11 — valor exposto SÓ como dado; Fase 4 (ALERT-01) decide/envia o alerta, esta fase não implementa lógica de alerta nenhuma
    heartbeat_timeout_seconds: int = 30       # D-12 — timeout do heartbeat Python<->MQL5 (forma só, ponte é Fase 3/4)
