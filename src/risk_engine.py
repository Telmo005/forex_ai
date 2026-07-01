"""
risk_engine.py
================
Núcleo de decisão de risco pré-trade, em Python — a autoridade que
qualquer ordem proposta (hoje: sintética/de teste; no futuro,
`hedge_engine.py`, Fase 3) tem de atravessar antes de chegar à execução
(MQL5, Fase 4).

Este é o módulo mais crítico em termos de segurança de capital de todo
o projeto (CLAUDE.md regra 1 / RISK-09): toda a lógica de stop-loss,
dimensionamento de posição, exposição e drawdown está implementada aqui
como REGRAS DETERMINÍSTICAS — nunca como inferência de um modelo de ML.
Este ficheiro não importa `ml_model` nem qualquer módulo de inferência,
e nenhuma função aqui ramifica com base numa variável de
confiança/sinal/probabilidade de modelo. Isto é verificado
estruturalmente (grep estático, não apenas revisão de código) em
`02-01-PLAN.md` Task 3.

Todas as funções de decisão (`check_*`, `size_position`,
`evaluate_order`) são FUNÇÕES PURAS: recebem os inputs explicitamente
(estado da conta, limites, proposta de ordem, matriz de correlação) e
devolvem uma decisão — sem I/O escondido, sem estado global, sem
aleatoriedade. A única exceção é `check_kill_switch`, que lê a
presença de um ficheiro (`os.path.exists`), porque essa é literalmente
a natureza do mecanismo de kill-switch (D-09) — mesmo assim, o caminho
do ficheiro é um parâmetro explícito, nunca fixo dentro da função.

IMPORTANTE (RISK-06, Pitfall 5 de 02-RESEARCH.md): o kill-switch e os
disjuntores de drawdown bloqueiam APENAS novas ordens — nunca fecham
posições existentes. "Fechar tudo instantaneamente" pode realizar uma
perda assimétrica numa perna de um hedge; a semântica correta é
"parar de abrir", não "liquidar". Nenhuma função neste módulo tem
qualquer efeito sobre posições já abertas.

Ordem de avaliação em `evaluate_order` (fixa, não configurável):
kill-switch -> drawdown -> contagem de posições -> exposição ->
dimensionamento + stop-loss. Um "reject" em qualquer etapa interrompe a
avaliação (short-circuit) e devolve a razão exata da rejeição.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from src.risk_limits import RiskLimits

log = logging.getLogger("risk_engine")


# ============================================================================
# Value objects
# ============================================================================


@dataclass
class AccountState:
    """Estado da conta, tal como visto pelo lado Python no momento da
    decisão. Nunca lido diretamente de uma API externa dentro deste
    módulo — é responsabilidade do chamador (futuramente
    `hedge_engine.py` / a ponte de ficheiros da Fase 3/4) construir este
    objeto a partir do estado real da conta e passá-lo explicitamente.

    Campos:
        equity                (float) - equity atual da conta
        daily_start_equity    (float) - equity no início do dia de trading corrente
        weekly_start_equity   (float) - equity no início da semana de trading corrente
        absolute_hwm          (float) - high-water-mark histórico do equity (máximo já atingido)
        open_positions        (list[dict]) - posições de hedge abertas, cada uma com pelo menos:
            {"pair_a": str, "pair_b": str, "exposure_pct": float}
            exposure_pct é a exposição de risco (não nocional) dessa posição,
            já expressa como fração do equity (ex.: 0.03 = 3%).
    """

    equity: float
    daily_start_equity: float
    weekly_start_equity: float
    absolute_hwm: float
    open_positions: list[dict] = field(default_factory=list)


@dataclass
class OrderProposal:
    """Ordem proposta a avaliar por `evaluate_order`. Representa o
    pedido de abertura de UM par de hedge (duas pernas), com os inputs
    de Kelly já resolvidos a partir de `strategy_registry.py` (D-02) —
    ver `resolve_kelly_inputs()` abaixo para como obter estes valores
    corretamente (com o gate out-of-sample da Pitfall 4).

    Campos:
        symbol_a, symbol_b  (str)   - símbolos das duas pernas do par
        win_rate            (float) - taxa de vitória usada para o dimensionamento de Kelly
        payoff_ratio         (float) - rácio ganho médio / perda média (Kelly)
        entry_price          (float) - preço de entrada de referência (perna A), usado para o stop-loss
        stop_distance_price_units (float) - distância do stop-loss em unidades de preço
            (ex.: desvio-padrão do spread * multiplicador), decidido pela camada de hedge
            (Fase 3); este módulo não inventa a distância do stop, só garante que
            um sl_price positivo é sempre calculado a partir dela.
        direction            (int)   - +1 (long spread) ou -1 (short spread)
    """

    symbol_a: str
    symbol_b: str
    win_rate: float
    payoff_ratio: float
    entry_price: float
    stop_distance_price_units: float
    direction: int


@dataclass
class RiskDecision:
    """Saída de `evaluate_order`. Forma escolhida deliberadamente para
    ser JSON-serializável (bool/float/str/None) — D-13: mesmo que a
    ponte de ficheiros Python<->MQL5 só seja construída na Fase 3/4,
    esta forma já é compatível com essa serialização sem precisar de
    qualquer alteração de interface nessa altura.

    Campos:
        approved                    (bool)       - True só se todas as verificações passarem
        size_lots                   (float)      - tamanho da posição em lotes (0.0 se rejeitada ou edge não positivo)
        sl_price                    (float)      - preço de stop-loss determinístico (0.0 se rejeitada)
        reject_reason               (str | None) - razão exata da rejeição, ou None se aprovada
        daily_drawdown_pct_of_limit    (float)   - drawdown diário atual como fração do limite D-03 (dado exposto para Fase 4/ALERT-01)
        weekly_drawdown_pct_of_limit   (float)   - idem para o limite semanal D-04
        absolute_drawdown_pct_of_limit (float)   - idem para o limite absoluto D-05
    """

    approved: bool
    size_lots: float
    sl_price: float
    reject_reason: str | None
    daily_drawdown_pct_of_limit: float = 0.0
    weekly_drawdown_pct_of_limit: float = 0.0
    absolute_drawdown_pct_of_limit: float = 0.0


# ============================================================================
# Kill-switch (D-09, RISK-06)
# ============================================================================


def check_kill_switch(path: str) -> tuple[bool, str | None]:
    """Verifica a presença do ficheiro de kill-switch (D-09). A mera
    EXISTÊNCIA do ficheiro em `path` bloqueia toda nova ordem — não
    fecha posições existentes (RISK-06, Pitfall 5 de 02-RESEARCH.md).

    Esta é a única função de decisão neste módulo com I/O (leitura de
    filesystem), porque é a natureza literal do mecanismo D-09; o
    caminho é sempre um parâmetro explícito, nunca hard-coded aqui —
    ver `src.risk_limits.KILL_SWITCH_PATH` para o default do projeto.

    params:
        path (str) - caminho do ficheiro de kill-switch a verificar

    devolve:
        (True, None)              - kill-switch inativo, ordens podem prosseguir
        (False, "kill_switch")    - kill-switch ativo, nenhuma nova ordem deve ser aprovada
    """
    import os

    if os.path.exists(path):
        log.warning("kill-switch ativo (%s) — nenhuma nova ordem será aprovada", path)
        return False, "kill_switch"
    return True, None


# ============================================================================
# Disjuntor de drawdown (D-03/D-04/D-05, RISK-04)
# ============================================================================


def drawdown_pct_of_limit(state: AccountState, limits: RiskLimits) -> dict[str, float]:
    """Devolve o drawdown atual (diário/semanal/absoluto) como fração
    do respetivo limite D-03/D-04/D-05 — não como fração do equity.
    Estes números são expostos como VALORES LIMPOS e CONSULTÁVEIS para
    que a Fase 4 (ALERT-01, D-11) possa calcular o seu próprio gatilho
    de "80% do limite mais próximo" sem que esta fase implemente
    qualquer lógica de alerta (ver nota de deferimento em
    02-01-PLAN.md <objective>). Esta fase só expõe os números; a Fase 4
    decide e envia o alerta.

    Função pura: nenhum I/O, nenhum efeito secundário.

    devolve:
        {"daily": float, "weekly": float, "absolute": float}
        cada valor é (drawdown_atual / limite_D-locked); ex.: 0.5 significa
        "a meio caminho do limite correspondente".
    """
    daily_dd = (state.daily_start_equity - state.equity) / state.daily_start_equity if state.daily_start_equity else 0.0
    weekly_dd = (state.weekly_start_equity - state.equity) / state.weekly_start_equity if state.weekly_start_equity else 0.0
    absolute_dd = (state.absolute_hwm - state.equity) / state.absolute_hwm if state.absolute_hwm else 0.0

    return {
        "daily": daily_dd / limits.daily_drawdown_pct if limits.daily_drawdown_pct else 0.0,
        "weekly": weekly_dd / limits.weekly_drawdown_pct if limits.weekly_drawdown_pct else 0.0,
        "absolute": absolute_dd / limits.absolute_drawdown_pct if limits.absolute_drawdown_pct else 0.0,
    }


def check_drawdown_breaker(state: AccountState, limits: RiskLimits) -> tuple[bool, str | None]:
    """Disjuntor de drawdown (RISK-04). Bloqueia novas ordens quando
    qualquer um dos três limites D-locked é atingido/ultrapassado.
    O drawdown ABSOLUTO é verificado primeiro porque é uma condição de
    kill-switch PERMANENTE (D-05, requer reset manual/confirmação do
    utilizador) — não um bloqueio temporário como diário/semanal, que
    resetam automaticamente no próximo dia/semana. Esta função não
    implementa o reset em si (isso é estado externo, fora do âmbito de
    uma função pura); apenas classifica corretamente qual condição foi
    violada, na ordem certa de severidade.

    Esta função NUNCA fecha posições existentes — só impede a
    aprovação de NOVAS ordens (RISK-06/Pitfall 5).

    params:
        state  (AccountState) - equity atual + marcos de início de dia/semana + HWM absoluto
        limits (RiskLimits)   - limites D-03/D-04/D-05

    devolve:
        (True, None)                              - nenhum disjuntor ativo
        (False, "absolute_drawdown_kill_switch")   - D-05: drawdown absoluto >= 20% do HWM
        (False, "daily_drawdown_breaker")          - D-03: drawdown diário >= 3%
        (False, "weekly_drawdown_breaker")         - D-04: drawdown semanal >= 8%
    """
    daily_dd = (state.daily_start_equity - state.equity) / state.daily_start_equity if state.daily_start_equity else 0.0
    weekly_dd = (state.weekly_start_equity - state.equity) / state.weekly_start_equity if state.weekly_start_equity else 0.0
    absolute_dd = (state.absolute_hwm - state.equity) / state.absolute_hwm if state.absolute_hwm else 0.0

    if absolute_dd >= limits.absolute_drawdown_pct:
        log.warning("drawdown absoluto de %.2f%% >= limite D-05 (%.2f%%) — kill-switch permanente",
                    absolute_dd * 100, limits.absolute_drawdown_pct * 100)
        return False, "absolute_drawdown_kill_switch"
    if daily_dd >= limits.daily_drawdown_pct:
        log.warning("drawdown diário de %.2f%% >= limite D-03 (%.2f%%) — bloqueio até ao próximo dia",
                    daily_dd * 100, limits.daily_drawdown_pct * 100)
        return False, "daily_drawdown_breaker"
    if weekly_dd >= limits.weekly_drawdown_pct:
        log.warning("drawdown semanal de %.2f%% >= limite D-04 (%.2f%%) — bloqueio até à próxima semana",
                    weekly_dd * 100, limits.weekly_drawdown_pct * 100)
        return False, "weekly_drawdown_breaker"
    return True, None


# ============================================================================
# Contagem de posições (D-08, RISK-05)
# ============================================================================


def check_position_count(state: AccountState, limits: RiskLimits) -> tuple[bool, str | None]:
    """Impõe o número máximo de pares de hedge simultâneos (D-08: 3
    pares / 6 pernas). Rejeita quando abrir mais uma posição excederia
    o limite — ou seja, quando já existem `max_concurrent_pairs`
    posições abertas e se tenta abrir uma 4ª.

    params:
        state  (AccountState) - open_positions atual
        limits (RiskLimits)   - max_concurrent_pairs (D-08)

    devolve:
        (True, None)                       - contagem atual permite mais uma posição
        (False, "max_concurrent_pairs")     - já no limite, a nova ordem seria a (limite+1)-ésima
    """
    if len(state.open_positions) >= limits.max_concurrent_pairs:
        log.warning("contagem de posições (%d) já no limite D-08 (%d) — nova ordem rejeitada",
                    len(state.open_positions), limits.max_concurrent_pairs)
        return False, "max_concurrent_pairs"
    return True, None


# ============================================================================
# Exposição (D-06/D-07, RISK-03)
# ============================================================================


def _lookup_correlation(correlation_matrix: dict, symbol_x: str, symbol_y: str) -> float:
    """Consulta `correlation_matrix` para o par (symbol_x, symbol_y),
    tentando AMBAS as ordens de chave — (x, y) e (y, x) — porque a
    matriz de correlação vinda da Camada 0 (`data_pipeline.py::
    scan_hedge_candidates`) produz apenas UMA linha por par não-ordenado
    de símbolos (não garante as duas direções (a,b) e (b,a) como chaves
    distintas). Sem este fallback bidirecional, uma correlação real
    ficaria silenciosamente tratada como 0.0 sempre que o chamador
    consultasse a ordem de chave que a matriz não populou (ver 02-REVIEW.md,
    finding sobre `aggregate_exposure_pct` ignorar `pair_b`/lookup reverso).
    Usa abs() porque tanto correlação positiva como negativa forte
    aumentam o risco de movimento conjunto adverso.

    devolve:
        (float) - abs(correlação) encontrada, ou 0.0 se nenhuma das duas
            ordens de chave existir na matriz (sem correlação conhecida).
    """
    if (symbol_x, symbol_y) in correlation_matrix:
        return abs(correlation_matrix[(symbol_x, symbol_y)])
    if (symbol_y, symbol_x) in correlation_matrix:
        return abs(correlation_matrix[(symbol_y, symbol_x)])
    return 0.0


def aggregate_exposure_pct(open_positions: list[dict], correlation_matrix: dict) -> float:
    """Calcula a exposição agregada, ajustada por correlação, das
    posições abertas — abordagem "variance-scaling" (D-07, ver
    `src.risk_limits.CORRELATION_ADJUSTMENT` e 02-RESEARCH.md "Code
    Examples"/"Open Question 1", já resolvida): a soma bruta das
    exposições individuais é escalada por (1 + correlação_média_par_a_par)
    entre TODOS os pares de posições abertas (não só dentro de cada
    hedge). Com 0 ou 1 posições abertas não há correlação a ajustar —
    degrada graciosamente para a soma bruta.

    Cada posição de hedge tem DUAS pernas (`pair_a`, `pair_b`) — a
    correlação relevante entre duas posições abertas pode residir em
    qualquer combinação das quatro pernas (a.pair_a/b.pair_a,
    a.pair_a/b.pair_b, a.pair_b/b.pair_a, a.pair_b/b.pair_b), não só na
    combinação pair_a-vs-pair_a. Usa-se o MÁXIMO abs(correlação) entre
    as quatro combinações como a correlação efetiva desse par de
    posições — a leitura conservadora correta para um limite de risco
    (RISK-03/D-07): se qualquer uma das quatro pernas estiver
    fortemente correlacionada, o risco de movimento conjunto adverso já
    existe, mesmo que as outras três combinações sejam descorrelacionadas.

    params:
        open_positions      (list[dict]) - cada dict com pelo menos
            {"pair_a": str, "pair_b": str, "exposure_pct": float}
        correlation_matrix   (dict)      - lookup {(symbol_a, symbol_b): float}
            de correlação entre símbolos (Camada 0, data_pipeline.py); pode
            estar populada numa só ordem de chave por par de símbolos —
            `_lookup_correlation()` tenta ambas as ordens.

    devolve:
        (float) - exposição agregada ajustada por correlação, comparável
            diretamente contra RiskLimits.max_aggregate_exposure_pct (D-07)
    """
    raw_sum = sum(p["exposure_pct"] for p in open_positions)
    if len(open_positions) < 2:
        return raw_sum

    pairs = [
        (a, b)
        for i, a in enumerate(open_positions)
        for b in open_positions[i + 1:]
    ]
    corrs = [
        max(
            _lookup_correlation(correlation_matrix, a["pair_a"], b["pair_a"]),
            _lookup_correlation(correlation_matrix, a["pair_a"], b["pair_b"]),
            _lookup_correlation(correlation_matrix, a["pair_b"], b["pair_a"]),
            _lookup_correlation(correlation_matrix, a["pair_b"], b["pair_b"]),
        )
        for a, b in pairs
    ]
    avg_corr = sum(corrs) / len(corrs) if corrs else 0.0
    return raw_sum * (1 + avg_corr)


def check_exposure_limits(state: AccountState, correlation_matrix: dict,
                           limits: RiskLimits,
                           new_position_exposure_pct: float = 0.0) -> tuple[bool, str | None]:
    """Impõe os limites de exposição por par (D-06, 5%) e agregada
    ajustada por correlação (D-07, 15%). Considera a exposição da NOVA
    posição proposta somada às já abertas, para que a verificação seja
    prospetiva (rejeita ANTES de a posição ser aberta, não depois).

    params:
        state                       (AccountState) - open_positions atual
        correlation_matrix          (dict)         - ver aggregate_exposure_pct()
        limits                      (RiskLimits)   - max_pair_exposure_pct (D-06), max_aggregate_exposure_pct (D-07)
        new_position_exposure_pct   (float)        - exposição da posição proposta (fração do equity)

    devolve:
        (True, None)                    - dentro de ambos os limites
        (False, "invalid_exposure_input") - new_position_exposure_pct não finito
            (NaN/inf) ou negativo (D-14: input adversarial/malformado nunca
            deve conseguir contornar um gate de risco por aritmética degenerada)
        (False, "max_pair_exposure")    - a própria nova posição já excede 5% sozinha (D-06)
        (False, "max_aggregate_exposure") - exposição agregada ajustada por correlação excede 15% (D-07)
    """
    if not math.isfinite(new_position_exposure_pct) or new_position_exposure_pct < 0:
        log.warning("new_position_exposure_pct inválido (NaN/inf/negativo): %r — rejeitado antes de qualquer gate de exposição",
                    new_position_exposure_pct)
        return False, "invalid_exposure_input"

    if new_position_exposure_pct > limits.max_pair_exposure_pct:
        log.warning("exposição da nova posição (%.2f%%) > limite por par D-06 (%.2f%%)",
                    new_position_exposure_pct * 100, limits.max_pair_exposure_pct * 100)
        return False, "max_pair_exposure"

    projected_positions = list(state.open_positions) + (
        [{"pair_a": "_proposed", "pair_b": "_proposed", "exposure_pct": new_position_exposure_pct}]
        if new_position_exposure_pct > 0
        else []
    )
    aggregate = aggregate_exposure_pct(projected_positions, correlation_matrix)
    if aggregate > limits.max_aggregate_exposure_pct:
        log.warning("exposição agregada ajustada por correlação (%.2f%%) > limite D-07 (%.2f%%)",
                    aggregate * 100, limits.max_aggregate_exposure_pct * 100)
        return False, "max_aggregate_exposure"
    return True, None


# ============================================================================
# Dimensionamento de posição via Kelly fracionário (D-01/D-02, RISK-02)
# ============================================================================


def kelly_fraction(win_rate: float, payoff_ratio: float, fraction: float = 0.25) -> float:
    """Kelly criterion fracionário — implementado VERBATIM a partir de
    `.claude/skills/quant-finance-math/SKILL.md` (não re-derivado). O
    `fraction` default aqui é 0.25 (D-01), sobrepondo o default de 0.3
    usado como exemplo genérico na skill.

    params:
        win_rate      (float) - taxa de vitória histórica/out-of-sample (0.0-1.0)
        payoff_ratio  (float) - ganho médio / perda média
        fraction      (float) - fração do Kelly completo a aplicar (D-01: 0.25)

    devolve:
        (float) - fração do capital a arriscar, nunca negativa (floor em 0.0
            quando o edge calculado não é positivo — nunca apostar contra o
            próprio edge).
    """
    b = payoff_ratio
    p = win_rate
    q = 1 - p
    if b <= 0:
        return 0.0
    f_star = (b * p - q) / b
    return max(0.0, f_star * fraction)


def resolve_kelly_inputs(strategy_record: dict) -> tuple[float, float]:
    """Extrai (win_rate, payoff_ratio) de um registo de
    `strategy_registry.get_strategy()`, aplicando o gate out-of-sample
    da Pitfall 4 (02-RESEARCH.md): prefere as estatísticas agregadas de
    walk-forward (`wf_fold_results`) quando `wf_passed=1` E
    `revalidated_on_real_data=1` — nunca inferindo uma flag a partir da
    outra (mesma disciplina de `strategy_registry.save_walk_forward_result`).
    Cai para as estatísticas brutas de backtest (`win_rate`,
    `profit_factor` como proxy do payoff_ratio) só quando os dados de
    walk-forward ainda não existem, emitindo um aviso explícito — nunca
    silenciosamente.

    D-02: os inputs de Kelly vêm sempre de estatísticas já persistidas
    em strategy_registry.py, nunca recalculados a partir de inferência
    de modelo ao vivo (RISK-09).

    params:
        strategy_record (dict) - registo devolvido por
            strategy_registry.get_strategy(), esperado conter pelo menos
            win_rate, profit_factor, wf_passed, wf_fold_results,
            revalidated_on_real_data.

    devolve:
        (win_rate, payoff_ratio) (tuple[float, float])
    """
    wf_passed = bool(strategy_record.get("wf_passed"))
    revalidated = bool(strategy_record.get("revalidated_on_real_data"))

    if wf_passed and revalidated and strategy_record.get("wf_fold_results"):
        import json

        fold_results = strategy_record["wf_fold_results"]
        if isinstance(fold_results, str):
            fold_results = json.loads(fold_results)
        # wf_fold_results tem a forma de walk_forward_validate() em
        # backtest_engine.py: usa-se aggregate_stats se presente no dict
        # persistido, senão agrega manualmente os folds.
        if isinstance(fold_results, dict) and "aggregate_stats" in fold_results:
            agg = fold_results["aggregate_stats"]
            win_rate = float(agg.get("win_rate", 0.0))
            profit_factor = float(agg.get("profit_factor", 0.0))
        else:
            win_rate = float(strategy_record.get("win_rate", 0.0))
            profit_factor = float(strategy_record.get("profit_factor", 0.0))
    else:
        log.warning(
            "resolve_kelly_inputs: usando estatísticas de backtest in-sample "
            "(wf_passed=%s, revalidated_on_real_data=%s) — não out-of-sample "
            "walk-forward. Ver Pitfall 4 de 02-RESEARCH.md.",
            wf_passed, revalidated,
        )
        win_rate = float(strategy_record.get("win_rate", 0.0))
        profit_factor = float(strategy_record.get("profit_factor", 0.0))

    # profit_factor (gross_win/gross_loss) é usado como proxy do payoff_ratio
    # (ganho médio/perda média) quando avg_win_r/avg_loss_r não estão
    # disponíveis diretamente — ambos expressam a mesma razão ganho/perda
    # sob a forma agregada vs. média; preferir avg_win_r/avg_loss_r quando
    # presentes no registo, por serem a definição mais direta.
    avg_win_r = strategy_record.get("avg_win_r")
    avg_loss_r = strategy_record.get("avg_loss_r")
    if avg_win_r is not None and avg_loss_r not in (None, 0):
        payoff_ratio = abs(float(avg_win_r) / float(avg_loss_r))
    else:
        payoff_ratio = profit_factor

    return win_rate, payoff_ratio


def size_position(win_rate: float, payoff_ratio: float, limits: RiskLimits) -> float:
    """Devolve o tamanho de posição (fração do capital a arriscar,
    ainda não convertida em lotes concretos — a conversão para lotes
    exige o preço/valor de tick do símbolo, responsabilidade do lado
    MQL5/execução per RISK-07) via Kelly fracionário a `limits.kelly_fraction`
    (D-01: 0.25x). Nunca devolve um valor negativo.

    params:
        win_rate      (float)      - ver kelly_fraction()
        payoff_ratio  (float)      - ver kelly_fraction()
        limits        (RiskLimits) - fornece kelly_fraction (D-01)

    devolve:
        (float) - fração do capital a arriscar, 0.0 se o edge não for positivo
    """
    return kelly_fraction(win_rate, payoff_ratio, fraction=limits.kelly_fraction)


# ============================================================================
# Stop-loss determinístico (RISK-01)
# ============================================================================


def compute_stop_loss_price(entry_price: float, stop_distance_price_units: float,
                             direction: int) -> float:
    """Calcula um preço de stop-loss determinístico a partir do preço
    de entrada e de uma distância de stop já decidida pela camada de
    hedge (Fase 3) — este módulo não decide a distância do stop, só
    garante que a ordem nunca é aprovada sem um sl_price positivo
    (RISK-01: stop-loss determinístico em toda a ordem, independente de
    qualquer inferência de ML).

    params:
        entry_price                  (float) - preço de entrada de referência
        stop_distance_price_units    (float) - distância do stop em unidades de preço (sempre >= 0)
        direction                    (int)   - +1 (long spread) ou -1 (short spread)

    devolve:
        (float) - preço de stop-loss; para direction=+1, abaixo da entrada;
            para direction=-1, acima da entrada.

    levanta:
        ValueError - se stop_distance_price_units <= 0 ou não finito, ou se
            entry_price <= 0 ou não finito — uma ordem nunca deve ser aprovada
            com um stop-loss inválido/nulo.
    """
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError(f"entry_price inválido: {entry_price!r}")
    if not math.isfinite(stop_distance_price_units) or stop_distance_price_units <= 0:
        raise ValueError(f"stop_distance_price_units inválido: {stop_distance_price_units!r}")

    if direction >= 0:
        return entry_price - stop_distance_price_units
    return entry_price + stop_distance_price_units


# ============================================================================
# Validação de input adversarial (RISK-08, D-14, T-02-01)
# ============================================================================


def validate_order_proposal(proposal: OrderProposal) -> tuple[bool, str | None]:
    """Valida a proposta de ordem contra inputs adversariais/malformados
    (D-14, T-02-01 do modelo de ameaças): lotes/preços não finitos ou
    não positivos, direção inválida. Esta é a primeira linha de defesa
    de input, antes de qualquer decisão de risco propriamente dita.

    params:
        proposal (OrderProposal) - ver dataclass acima

    devolve:
        (True, None)                     - proposta estruturalmente válida
        (False, "invalid_entry_price")   - entry_price não positivo/não finito
        (False, "invalid_stop_distance") - stop_distance_price_units não positivo/não finito
        (False, "invalid_direction")     - direction fora de {-1, 1}
        (False, "invalid_symbol")        - symbol_a/symbol_b vazios ou não-string
    """
    if not isinstance(proposal.symbol_a, str) or not proposal.symbol_a:
        return False, "invalid_symbol"
    if not isinstance(proposal.symbol_b, str) or not proposal.symbol_b:
        return False, "invalid_symbol"
    if not math.isfinite(proposal.entry_price) or proposal.entry_price <= 0:
        return False, "invalid_entry_price"
    if not math.isfinite(proposal.stop_distance_price_units) or proposal.stop_distance_price_units <= 0:
        return False, "invalid_stop_distance"
    if proposal.direction not in (-1, 1):
        return False, "invalid_direction"
    return True, None


# ============================================================================
# Agregador — evaluate_order (RISK-01..RISK-06)
# ============================================================================


def evaluate_order(proposal: OrderProposal, state: AccountState, limits: RiskLimits,
                    correlation_matrix: dict[tuple[str, str], float],
                    kill_switch_path: str,
                    new_position_exposure_pct: float = 0.0) -> RiskDecision:
    """Agrega TODAS as verificações de risco desta fase, na ordem fixa:
    validação de input -> kill-switch -> drawdown -> contagem de
    posições -> exposição -> dimensionamento + stop-loss. Uma rejeição
    em qualquer etapa interrompe a avaliação (short-circuit) e devolve
    uma RiskDecision com approved=False e reject_reason não-nulo.

    Esta função NUNCA fecha posições existentes — só decide se aprova
    uma NOVA ordem (RISK-06/Pitfall 5). Não importa nem ramifica sobre
    qualquer saída de ml_model.py (RISK-09).

    params:
        proposal                   (OrderProposal)               - ordem proposta
        state                      (AccountState)                - estado atual da conta
        limits                     (RiskLimits)                  - limites D-locked
        correlation_matrix         (dict[(str,str), float])      - lookup de correlação entre símbolos
        kill_switch_path           (str)                          - caminho do ficheiro de kill-switch (D-09)
        new_position_exposure_pct  (float)                       - exposição da nova posição, fração do equity

    devolve:
        RiskDecision - ver dataclass acima; approved=True implica sempre
            size_lots > 0.0 e sl_price > 0.0; approved=False implica
            size_lots=0.0, sl_price=0.0 e reject_reason não-nulo.
    """
    dd_pct = drawdown_pct_of_limit(state, limits)
    base_kwargs = {
        "daily_drawdown_pct_of_limit": dd_pct["daily"],
        "weekly_drawdown_pct_of_limit": dd_pct["weekly"],
        "absolute_drawdown_pct_of_limit": dd_pct["absolute"],
    }

    def _reject(reason: str) -> RiskDecision:
        return RiskDecision(approved=False, size_lots=0.0, sl_price=0.0,
                             reject_reason=reason, **base_kwargs)

    valid, reason = validate_order_proposal(proposal)
    if not valid:
        return _reject(reason)

    ok, reason = check_kill_switch(kill_switch_path)
    if not ok:
        return _reject(reason)

    ok, reason = check_drawdown_breaker(state, limits)
    if not ok:
        return _reject(reason)

    ok, reason = check_position_count(state, limits)
    if not ok:
        return _reject(reason)

    ok, reason = check_exposure_limits(state, correlation_matrix, limits, new_position_exposure_pct)
    if not ok:
        return _reject(reason)

    size_fraction = size_position(proposal.win_rate, proposal.payoff_ratio, limits)
    if size_fraction <= 0.0:
        return _reject("non_positive_edge")

    try:
        sl_price = compute_stop_loss_price(
            proposal.entry_price, proposal.stop_distance_price_units, proposal.direction
        )
    except ValueError as exc:
        log.warning("stop-loss inválido para proposta %s/%s: %s",
                    proposal.symbol_a, proposal.symbol_b, exc)
        return _reject("invalid_stop_loss")

    return RiskDecision(
        approved=True,
        size_lots=size_fraction,
        sl_price=sl_price,
        reject_reason=None,
        **base_kwargs,
    )
