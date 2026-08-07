"""
hedge_engine.py
================
Camada 2 (produção) — decide QUANDO propor a abertura/fecho de uma
posição de hedge entre dois símbolos cointegrados, a partir de
estratégias já aprovadas no dashboard (HEDGE-01). Este módulo nunca
dimensiona posições, nunca decide limites de risco, e nunca executa
uma ordem — toda a proposta gerada aqui é encaminhada para
`risk_engine.evaluate_order()` (Fase 2) antes de poder tornar-se uma
ordem real (HEDGE-02). A cointegração de um par é tratada como uma
propriedade continuamente reverificada, nunca permanente a partir do
snapshot original de `hedge_candidates.csv` (HEDGE-03).

Este módulo NUNCA importa `ml_model.py` nem ramifica sobre qualquer
saída de inferência de modelo — a seleção de estratégia é sempre a
partir de parâmetros já aprovados e persistidos em
`strategy_registry.py` (RISK-09, herdado de `risk_engine.py`).

Ordem de avaliação em `evaluate_hedge_signal` (fixa, não configurável)
para o ramo de saída: quebra de correlação (D-04) -> reversão (D-03)
-> stop de tempo (D-05). A quebra de correlação tem SEMPRE prioridade,
mesmo que o stop de tempo ou a reversão também estejam a disparar no
mesmo bar — ver docstring da função para a justificação (D-05).

NOTA DELIBERADA sobre operadores de comparação (mesma disciplina de
`risk_engine.py`): a entrada usa `>=` em `abs(zscore) >= ENTRY_THRESHOLD`
e `correlation >= MIN_CORRELATION_ENTRY` (estar exatamente no limiar já
qualifica); a saída por reversão usa `<=` em
`abs(zscore) <= EXIT_THRESHOLD` (estar exatamente no limiar já fecha);
a saída por quebra de correlação usa `<` estrito em
`correlation < MIN_CORRELATION_EXIT` (estar exatamente no limiar ainda
é saudável, só abaixo dele quebra) — esta escolha é intencional: o
limiar de entrada e o de reversão são gatilhos "atingir já é suficiente",
enquanto o limiar de saída por correlação representa a fronteira da
premissa do hedge continuar válida, e um valor exatamente nela ainda
não a invalidou.
"""

from __future__ import annotations

import json
import logging
from typing import Iterator, NamedTuple

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

from src.backtest_engine import compute_rolling_beta, compute_zscore
from src.risk_engine import OrderProposal, RiskDecision, resolve_kelly_inputs
from src.risk_limits import KILL_SWITCH_PATH, RiskLimits
from src.strategy_registry import list_strategies

log = logging.getLogger("hedge_engine")


# ============================================================================
# Constantes nomeadas (D-01..D-08, ver 03-CONTEXT.md)
# ============================================================================

HEDGE_PARAMS = {
    "ENTRY_THRESHOLD": 2.0,          # D-01 — |z| mínimo para abrir
    "MIN_CORRELATION_ENTRY": 0.5,    # D-02 — correlação mínima para abrir
    "EXIT_THRESHOLD": 0.3,           # D-03 — |z| máximo para fechar por reversão
    "MIN_CORRELATION_EXIT": 0.4,     # D-04 — abaixo disto, fecha imediatamente (prioridade sobre D-03/D-05)
    "MAX_HOLD_BARS": 75,             # D-05 — stop de tempo (M5, ~6h15 de mercado)
}

COINT_RECHECK_EVERY_BARS = 50  # D-06 — cadência de reteste de cointegração para posições abertas


def _hedge_params_from_strategy_record(strategy_record: dict) -> dict | None:
    """Deriva os limiares de entrada/saída de `evaluate_hedge_signal` a
    partir dos parâmetros ESPECÍFICOS já validados (laboratório +
    walk-forward) da estratégia aprovada para este par — nunca do
    `HEDGE_PARAMS` genérico do módulo (CLAUDE.md regra 7: nenhum
    parâmetro entra em produção sem ter sido validado; usar um limiar
    diferente do que foi realmente testado violaria essa garantia).

    Mapeamento das chaves em minúsculas de `strategy_registry` (mesmo
    esquema de `backtest_engine.run_hedge_backtest`) para as chaves
    MAIÚSCULAS de `HEDGE_PARAMS`:
        entry_threshold -> ENTRY_THRESHOLD
        exit_threshold  -> EXIT_THRESHOLD
        min_correlation -> MIN_CORRELATION_ENTRY (usado tal-e-qual)
        min_correlation * 0.7 -> MIN_CORRELATION_EXIT (o laboratório só
            tem UM conceito de correlação mínima; a quebra de
            correlação em `run_hedge_backtest` dispara em
            `corr < min_correlation * 0.7`, ver backtest_engine.py — a
            mesma proporção é usada aqui para que a saída em produção
            reproduza fielmente o que foi validado, não um limiar
            MIN_CORRELATION_EXIT independente e nunca testado)
        max_hold_bars   -> MAX_HOLD_BARS

    devolve:
        dict | None - dict no formato HEDGE_PARAMS, ou None se
            `strategy_record["params"]` estiver malformado (JSON
            inválido ou sem alguma chave esperada) — o chamador deve
            tratar None como "não negociar este par" (fail-closed),
            nunca cair para o HEDGE_PARAMS genérico.
    """
    raw_params = strategy_record.get("params")
    try:
        params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
        min_correlation = float(params["min_correlation"])
        return {
            "ENTRY_THRESHOLD": float(params["entry_threshold"]),
            "EXIT_THRESHOLD": float(params["exit_threshold"]),
            "MIN_CORRELATION_ENTRY": min_correlation,
            "MIN_CORRELATION_EXIT": min_correlation * 0.7,
            "MAX_HOLD_BARS": int(params["max_hold_bars"]),
        }
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        log.warning(
            "_hedge_params_from_strategy_record: params malformados/incompletos na "
            "estratégia %s (%s) — par não será negociado (fail-closed)",
            strategy_record.get("id"), exc,
        )
        return None

# Multiplicador do desvio-padrão do spread na entrada, usado para derivar
# stop_distance_price_units no handoff para o motor de risco (Claude's
# discretion, RESEARCH.md Open Question 1 / Assumption A1). Em unidades
# "R" consistentes com backtest_engine.py: um stop a 2x o desvio-padrão do
# spread na entrada é conservador o suficiente para não ser atingido por
# ruído normal de scalping, sem ser tão largo que domine o dimensionamento
# via Kelly de risk_engine.py. Named, documented — nunca um literal solto
# na chamada a risk_engine.
STOP_DISTANCE_STD_MULTIPLIER = 2.0

# Fração mínima do índice intersectado abaixo da qual replay_feed() avisa
# que está a descartar uma fração relevante de barras de algum símbolo
# (ex.: feriados/DST específicos de uma corretora) — só um aviso, não
# bloqueia a leitura (Open Question 2 de 03-RESEARCH.md, validação estrita
# fica para o feed ao vivo da Fase 4).
_ALIGNMENT_DROP_WARN_FRACTION = 0.05


# ============================================================================
# Elegibilidade de estratégias (HEDGE-01, D-07, D-08)
# ============================================================================


def load_eligible_strategies(db_path: str) -> list[dict]:
    """Carrega estratégias elegíveis a propor ordens em produção (HEDGE-01,
    D-07). Elegível = `status == "passed"` (o literal da base de dados,
    NÃO a etiqueta de UI "✅ Aprovada", que é só um mapeamento de
    apresentação em `dashboard.py`) E `wf_passed == 1` E
    `revalidated_on_real_data == 1`. As duas flags de walk-forward NUNCA
    são inferidas uma da outra (mesma disciplina de
    `risk_engine.resolve_kelly_inputs`) — uma estratégia que só passou
    walk-forward em dados sintéticos (`wf_passed=1`,
    `revalidated_on_real_data=0`) nunca é devolvida como elegível.

    Nunca abre uma conexão sqlite3 direta — usa sempre
    `strategy_registry.list_strategies()` (Don't-Hand-Roll).

    params:
        db_path (str) - caminho da base de dados SQLite do registo de estratégias

    devolve:
        list[dict] - registos elegíveis (forma de strategy_registry.get_strategy()),
            lista vazia se a base de dados não existir ou não tiver linhas elegíveis
    """
    df = list_strategies(db_path, status="passed")
    if df.empty:
        return []

    eligible_rows = []
    for record in df.to_dict("records"):
        wf_passed = bool(record.get("wf_passed"))
        revalidated = bool(record.get("revalidated_on_real_data"))
        if wf_passed and revalidated:
            eligible_rows.append(record)
        elif wf_passed or revalidated:
            # Uma das duas flags está ligada mas não ambas — caso notável
            # (ex.: passou walk-forward só em dados sintéticos) que merece
            # aviso explícito, nunca exclusão silenciosa (Pitfall 4).
            log.warning(
                "load_eligible_strategies: estratégia %s excluída — "
                "wf_passed=%s, revalidated_on_real_data=%s (ambas têm de ser verdadeiras)",
                record.get("id"), wf_passed, revalidated,
            )
    return eligible_rows


def _oos_profit_factor(record: dict) -> float | None:
    """Extrai `aggregate_stats.profit_factor` (out-of-sample, walk-forward)
    de `record["wf_fold_results"]` — mesmo campo já consumido por
    `risk_engine.resolve_kelly_inputs()` e por `dashboard.py`. Devolve
    `None` (nunca levanta exceção) se o campo estiver ausente, malformado,
    ou sem `aggregate_stats` — o chamador trata `None` como "sem dados
    para comparar", nunca como zero (que faria um registo sem dados
    parecer pior do que um com profit_factor genuinamente baixo)."""
    raw = record.get("wf_fold_results")
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict) and parsed.get("aggregate_stats"):
            return float(parsed["aggregate_stats"]["profit_factor"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return None


def select_strategy_for_pair(eligible: list[dict], pair_a: str, pair_b: str,
                              policy: str = "most_recent") -> dict | None:
    """Resolve qual estratégia elegível propõe ordens para o par exato
    (`pair_a`, `pair_b`) — D-08. v1 não implementa seleção por regime
    (REGIME-01/02 são v2); se mais do que uma estratégia elegível
    partilhar o mesmo par, o desempate é explícito e registado (nunca um
    `strategies[0]` silencioso).

    policy:
        "most_recent" (default, compatibilidade com o comportamento
            original) - vence a estratégia com maior `created_at`.
        "best_oos_profit_factor" - vence a estratégia com maior
            `aggregate_stats.profit_factor` out-of-sample (ver
            `_oos_profit_factor`) — usado pela troca dinâmica em
            produção (scripts/run_live_hedge_loop.py), porque "mais
            recentemente aprovada" não é o mesmo que "melhor". Um
            registo sem dados out-of-sample utilizáveis (`None`) nunca
            vence um registo com dados reais, e cai para o desempate por
            `created_at` só entre registos igualmente sem dados.

    params:
        eligible (list[dict]) - saída de load_eligible_strategies()
        pair_a, pair_b (str)  - símbolos do par exato a resolver
        policy (str)          - ver acima

    devolve:
        dict | None - a estratégia escolhida, ou None se nenhuma estratégia
            elegível cobrir este par
    """
    matches = [r for r in eligible if r.get("pair_a") == pair_a and r.get("pair_b") == pair_b]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    if policy == "best_oos_profit_factor":
        def _key(r: dict) -> tuple[float, str]:
            pf = _oos_profit_factor(r)
            # (tem_dados, profit_factor, created_at): registos com dados
            # out-of-sample ordenam sempre acima dos que não têm, nunca
            # comparando None com float diretamente.
            return (pf is not None, pf if pf is not None else float("-inf"), r.get("created_at") or "")
        chosen = max(matches, key=_key)
    else:
        chosen = max(matches, key=lambda r: r.get("created_at") or "")

    log.warning(
        "select_strategy_for_pair: %d estratégias elegíveis para %s/%s (ids=%s) — "
        "desempate por policy=%s, escolhida id=%s",
        len(matches), pair_a, pair_b, [m.get("id") for m in matches], policy, chosen.get("id"),
    )
    return chosen


# ============================================================================
# Avaliador de sinal de entrada/saída (D-01..D-05)
# ============================================================================


def evaluate_hedge_signal(pair_a: str, pair_b: str, zscore: float, correlation: float,
                           is_cointegrated: bool, position: dict | None, params: dict,
                           bars_held: int = 0) -> dict | None:
    """Avalia se deve abrir ou fechar uma posição de hedge entre `pair_a`
    e `pair_b`, dado o estado atual do sinal. Função pura — não tem
    autoridade de dimensionamento nem de risco (HEDGE-02): apenas emite
    a proposta abstrata `open_hedge`/`close_hedge`, nunca um lote ou
    `sl_price`.

    Ramo de ENTRADA (`position is None`): abre só quando TODAS as
    condições se verificam — `is_cointegrated` (cointegração nunca é
    opcional nem substituída por correlação, Pitfall 6), `abs(zscore) >=
    ENTRY_THRESHOLD` (D-01) e `correlation >= MIN_CORRELATION_ENTRY`
    (D-02). Direção: `zscore > 0` (spread acima da média) -> `direction
    = -1` (short spread, expectativa de reversão para baixo);
    `zscore < 0` -> `direction = +1`.

    Ramo de SAÍDA (`position is not None`) — ORDEM FIXA, carrega
    significado de segurança (D-05, Pitfall 2): (1) quebra de
    correlação, (2) reversão, (3) stop de tempo. A quebra de correlação
    é verificada PRIMEIRO e tem prioridade sobre as outras duas mesmo
    que disparem no mesmo bar, porque representa a premissa do hedge
    deixar de ser válida — continuar a tratar isso como "ainda não
    reverteu" (reversão) ou "só mais um stop de tempo" mascararia o
    motivo real de sair. Esta ordem substitui deliberadamente a ordem
    "reversão primeiro" sugerida em docs/hedge_engine_spec.md — D-05
    supersede essa sugestão.

    devolve, por ramo:
        entrada:
            {"action": "open_hedge", "pair_a", "pair_b", "direction",
             "trigger": "spread_zscore", "trigger_value": zscore}
            ou None se alguma condição de entrada falhar.
        saída:
            {"action": "close_hedge", "pair_a", "pair_b",
             "trigger": "correlation_breakdown" | "reversion" | "time_stop",
             "trigger_value": <zscore ou correlation, conforme o gatilho>}
            ou None se nenhuma condição de saída disparar.
    """

    def _close(trigger: str, value: float) -> dict:
        return {
            "action": "close_hedge",
            "pair_a": pair_a,
            "pair_b": pair_b,
            "trigger": trigger,
            "trigger_value": value,
        }

    if position is None:
        if (
            is_cointegrated
            and abs(zscore) >= params["ENTRY_THRESHOLD"]
            and correlation >= params["MIN_CORRELATION_ENTRY"]
        ):
            return {
                "action": "open_hedge",
                "pair_a": pair_a,
                "pair_b": pair_b,
                "direction": -1 if zscore > 0 else 1,
                "trigger": "spread_zscore",
                "trigger_value": zscore,
            }
        return None

    # Ramo de saída — ordem fixa: quebra de correlação -> reversão -> tempo.
    if correlation < params["MIN_CORRELATION_EXIT"]:
        return _close("correlation_breakdown", correlation)
    if abs(zscore) <= params["EXIT_THRESHOLD"]:
        return _close("reversion", zscore)
    if bars_held >= params["MAX_HOLD_BARS"]:
        return _close("time_stop", bars_held)
    return None


# ============================================================================
# Reverificação de cointegração, fail-closed (HEDGE-03)
# ============================================================================


def recheck_cointegration(price_a: pd.Series, price_b: pd.Series,
                           coint_window: int = 1000,
                           pvalue_threshold: float = 0.05) -> tuple[bool, float]:
    """Reavalia a cointegração Engle-Granger entre `price_a` e `price_b`
    na janela final de `coint_window` barras — nunca reutiliza o
    `is_cointegrated` estático de `hedge_candidates.csv` (Pitfall 1).
    Reusa o mesmo padrão de chamada de
    `data_pipeline.scan_hedge_candidates()`, não reimplementa
    Engle-Granger.

    FAIL-CLOSED (HEDGE-03, divergência deliberada de
    `scan_hedge_candidates()`): essa função faz `continue` (omite a
    linha) quando `coint()` levanta uma exceção; esta função NUNCA pode
    "só ignorar" — uma posição aberta não tem essa opção. Qualquer
    exceção devolve `(False, nan)` e regista um aviso, nunca propaga a
    exceção e nunca devolve `(True, ...)`.

    Guard adicional (input degenerado que NÃO levanta exceção, mas
    engana o teste): uma série constante ou quase-constante (variância
    ~0) ou com valores não-finitos faz `coint()` devolver um p-value
    numericamente inválido (ex.: 0.0 por divisão 0/0 no cálculo do R²
    interno) SEM levantar exceção — isto pareceria falsamente
    "fortemente cointegrado". Este caso é detetado e tratado como
    fail-closed ANTES de chamar `coint()`, pela mesma razão de segurança
    do bloco try/except.

    params:
        price_a, price_b   (pd.Series) - séries de preço completas; só a
            janela final `coint_window` é usada (causal, sem lookahead)
        coint_window        (int)      - nº de barras finais a considerar
        pvalue_threshold     (float)    - limiar de significância (default
            consistente com PipelineConfig.coint_pvalue_threshold)

    devolve:
        (bool, float) - (is_cointegrated, pvalue); pvalue é NaN quando
            o teste falhou (fail-closed)
    """
    a = price_a.iloc[-coint_window:]
    b = price_b.iloc[-coint_window:]
    n = min(len(a), len(b))
    a, b = a.iloc[-n:].reset_index(drop=True), b.iloc[-n:].reset_index(drop=True)

    if not np.isfinite(a).all() or not np.isfinite(b).all():
        log.warning("recheck_cointegration: série com valores não-finitos — fail-closed")
        return False, float("nan")
    if a.std() < 1e-12 or b.std() < 1e-12:
        log.warning("recheck_cointegration: série (quase-)constante (variância ~0) — fail-closed")
        return False, float("nan")

    try:
        _, pvalue, _ = coint(a, b)
    except Exception as exc:
        log.warning("recheck_cointegration: coint() falhou — tratado como NÃO cointegrado (fail-closed): %s", exc)
        return False, float("nan")

    # bool() nativo (não numpy.bool_) — mesma disciplina de JSON-serializabilidade
    # de risk_engine.RiskDecision (D-13).
    return bool(pvalue < pvalue_threshold), float(pvalue)


# ============================================================================
# Handoff para o motor de risco (HEDGE-02)
# ============================================================================


def propose_to_risk_engine(hedge_proposal: dict, strategy_record: dict, entry_price: float,
                            stop_distance_price_units: float, account_state, correlation_matrix: dict,
                            new_position_exposure_pct: float = 0.0,
                            kill_switch_path: str | None = None) -> RiskDecision:
    """Traduz uma proposta de hedge (`open_hedge`/`close_hedge` de
    `evaluate_hedge_signal`) para `risk_engine.OrderProposal` e devolve
    a `RiskDecision` de `risk_engine.evaluate_order()` — a autoridade de
    dimensionamento/aprovação é SEMPRE do motor de risco, nunca deste
    módulo (HEDGE-02). `hedge_engine.py` não inventa `win_rate`/
    `payoff_ratio` — vêm sempre de `risk_engine.resolve_kelly_inputs()`.

    `stop_distance_price_units` é decidido pelo CHAMADOR (o loop, via
    `entry_spread_std * STOP_DISTANCE_STD_MULTIPLIER`) — esta função não
    inventa a distância do stop, só a repassa (mesmo contrato do
    docstring de `OrderProposal`).

    Inputs numéricos não-finitos/não-positivos de `entry_price`/
    `stop_distance_price_units` NÃO são corrigidos aqui — são passados
    tal-e-qual para que o próprio `validate_order_proposal` de
    `risk_engine.py` os rejeite (nunca contornar essa validação).

    Um `strategy_record` com `wf_fold_results` malformado (JSON
    inválido) é apanhado e a proposta é descartada com
    `reject_reason="malformed_strategy_record"` — nunca propaga a
    exceção para o chamador (proteção contra DoS por um registo
    corrompido no meio de um loop).

    devolve:
        RiskDecision - ver risk_engine.py; approved=True implica sempre
            size_lots > 0 e sl_price > 0.
    """
    from src.risk_engine import evaluate_order

    try:
        win_rate, payoff_ratio = resolve_kelly_inputs(strategy_record)
    except (ValueError, TypeError) as exc:
        log.warning(
            "propose_to_risk_engine: strategy_record %s tem dados malformados (%s) — proposta descartada",
            strategy_record.get("id"), exc,
        )
        return RiskDecision(approved=False, size_lots=0.0, sl_price=0.0,
                             reject_reason="malformed_strategy_record")

    proposal = OrderProposal(
        symbol_a=hedge_proposal["pair_a"],
        symbol_b=hedge_proposal["pair_b"],
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        entry_price=entry_price,
        stop_distance_price_units=stop_distance_price_units,
        direction=hedge_proposal["direction"],
    )
    return evaluate_order(
        proposal=proposal,
        state=account_state,
        limits=RiskLimits(),
        correlation_matrix=correlation_matrix,
        # kill_switch_path (bug encontrado 2026-07-30): o default
        # KILL_SWITCH_PATH ("KILL_SWITCH.flag") é relativo ao cwd do
        # processo Python, NÃO à pasta Common\Files onde o EA
        # (RiskGuard.mqh, FILE_COMMON) verifica o mesmo ficheiro. Sem
        # este parâmetro explícito, um kill-switch criado em
        # Common\Files (ex.: por divergence_monitor.py) nunca seria
        # visto pelo lado Python — quebrando a garantia de verificação
        # independente dos dois lados (RISK-06/D-09). O chamador ao
        # vivo (scripts/run_live_hedge_loop.py) DEVE passar o caminho
        # real; o default aqui só existe para não quebrar chamadas
        # antigas/testes que nunca tocam num kill-switch real.
        kill_switch_path=kill_switch_path or KILL_SWITCH_PATH,
        new_position_exposure_pct=new_position_exposure_pct,
    )


# ============================================================================
# Feed de substituição (replay) — mesma forma que um futuro feed ao vivo
# ============================================================================


class Bar(NamedTuple):
    timestamp: pd.Timestamp
    symbol: str
    close: float


def replay_feed(feature_parquet_paths: dict[str, str]) -> Iterator[dict[str, "Bar"]]:
    """Gera, um passo de cada vez, um dicionário `{simbolo: Bar}`
    alinhado por timestamp, a partir dos parquets de
    `output/features_<SYMBOL>.parquet` — a intersecção dos índices de
    todos os símbolos, da barra mais antiga para a mais recente.

    Esta é a mesma forma que `live_feed()` (abaixo) também produz —
    `run_hedge_loop()` nunca sabe de qual dos dois está a consumir
    (Pitfall 5).

    Regista um aviso se a intersecção descartar mais do que uma fração
    pequena das barras de algum símbolo (gaps de fim de semana,
    feriados específicos de uma corretora) — não bloqueia a leitura,
    só torna o problema visível para investigação.

    params:
        feature_parquet_paths (dict[str, str]) - {simbolo: caminho_do_parquet}

    produz:
        dict[str, Bar] - um passo por timestamp comum, close lido por
            acesso explícito à coluna "close" (nunca posicional)
    """
    frames = {sym: pd.read_parquet(path) for sym, path in feature_parquet_paths.items()}

    common_idx = None
    for df in frames.values():
        common_idx = df.index if common_idx is None else common_idx.intersection(df.index)
    common_idx = common_idx.sort_values() if common_idx is not None else pd.DatetimeIndex([])

    for sym, df in frames.items():
        dropped_fraction = 1 - (len(common_idx) / len(df)) if len(df) else 0.0
        if dropped_fraction > _ALIGNMENT_DROP_WARN_FRACTION:
            log.warning(
                "replay_feed: %.1f%% das barras de %s foram descartadas pela intersecção "
                "de índices (%d de %d barras usadas) — possível desalinhamento de timestamps",
                dropped_fraction * 100, sym, len(common_idx), len(df),
            )

    for ts in common_idx:
        yield {sym: Bar(timestamp=ts, symbol=sym, close=float(df.loc[ts, "close"]))
               for sym, df in frames.items()}


def live_feed(symbols: list[str], timeframe: str, warmup_bars: int = 300,
              poll_seconds: float = 15.0,
              feed_health_path: str | None = None) -> Iterator[dict[str, "Bar"]]:
    """Gera a mesma forma que `replay_feed()` (`dict[str, Bar]` por
    passo) mas a partir do terminal MT5 ao vivo — `run_hedge_loop()`
    nunca sabe qual dos dois está a consumir (Pitfall 5).

    Primeiro produz `warmup_bars` passos históricos (barras já
    fechadas, `copy_rates_from_pos(..., 1, warmup_bars)` — posição 1
    salta a barra ainda em formação) para que as janelas rolantes
    (beta/z-score/correlação) não arranquem "frias" sem contexto. A
    partir daí, entra em modo de polling: dorme `poll_seconds` e só
    produz um novo passo quando TODOS os símbolos tiverem avançado para
    o MESMO novo timestamp de fecho (M5 fecha à mesma hora de relógio
    em todos os pares da mesma corretora) — se só alguns avançaram,
    espera-se pelo próximo ciclo em vez de publicar um passo
    parcialmente desalinhado.

    Corre indefinidamente (gerador infinito) — o chamador
    (`run_hedge_loop()`) processa um passo de cada vez; para parar, o
    processo que consome este gerador tem de ser terminado (Ctrl+C ou
    equivalente), não há um sinal de paragem embutido aqui.

    RECONEXÃO (D-14, pedido explícito do utilizador — sem Telegram/email,
    só log + ficheiro de estado para o dashboard): uma falha genuína de
    fetch (`rates is None`, distinta de "barra ainda não fechou", que é
    normal e não conta como falha) incrementa um contador de falhas
    consecutivas e grava o estado em `feed_health_path` via
    `feed_health.write_feed_health()` — NUNCA desiste, tenta para sempre
    a cada `poll_seconds`. A cada `RECONNECT_ATTEMPT_EVERY_N_FAILURES`
    falhas consecutivas, chama `mt5.initialize()` de novo (idempotente,
    seguro chamar mesmo já inicializado) — sem isto, uma quebra que
    invalidasse a sessão IPC local (não só a ligação da corretora, que o
    terminal já trata sozinho) nunca recuperaria sozinha.

    params:
        symbols       (list[str]) - símbolos a subscrever (nomes exatos da corretora)
        timeframe     (str)       - "M1"/"M5"/etc., mapeado para MT5 via TIMEFRAME_<tf>
        warmup_bars   (int)       - nº de barras históricas fechadas para aquecimento
        poll_seconds  (float)     - intervalo de sondagem por novo fecho de barra
        feed_health_path (str | None) - caminho do ficheiro de estado (ver
            feed_health.py); default None usa feed_health.DEFAULT_HEALTH_PATH

    produz:
        dict[str, Bar] - um passo por timestamp de fecho comum a todos os símbolos
    """
    import time as _time

    from src.feed_health import DEFAULT_HEALTH_PATH, write_feed_health

    health_path = feed_health_path or DEFAULT_HEALTH_PATH
    RECONNECT_ATTEMPT_EVERY_N_FAILURES = 3

    import MetaTrader5 as mt5  # import local: só exigido neste modo, mesmo padrão de data_pipeline.py

    # mt5.initialize() liga-se à sessão já aberta e autenticada no terminal
    # MT5 em execução (sem argumentos — mesmo bug já corrigido em
    # data_pipeline.fetch_mt5: passar login=None explicitamente é
    # rejeitado pela API, "Invalid login argument"). Sem esta chamada,
    # qualquer copy_rates_from_pos() falha com "(-10004, 'No IPC
    # connection')" — bug encontrado em teste manual 2026-07-26.
    if not mt5.initialize():
        raise RuntimeError(f"live_feed: falha ao inicializar MT5: {mt5.last_error()}")

    tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}")

    # symbol_select() força o símbolo a aparecer no Market Watch e o terminal
    # a começar a atualizá-lo — sem isto, um símbolo que não esteja já num
    # gráfico/Market Watch pode devolver histórico desatualizado (cache
    # antiga, não sincronizada com o instante atual), o que faz a
    # intersecção de timestamps com um símbolo ATIVO (ex.: o que está no
    # gráfico do EA) ficar vazia — bug confirmado em teste manual 2026-07-29
    # (aquecimento com "0 barras" mesmo sem nenhuma exceção levantada).
    for sym in symbols:
        if not mt5.symbol_select(sym, True):
            log.warning("live_feed: symbol_select(%s) falhou — %s", sym, mt5.last_error())

    warmup_series: dict[str, pd.Series] = {}
    for sym in symbols:
        rates = mt5.copy_rates_from_pos(sym, tf_const, 1, warmup_bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"live_feed: sem barras de aquecimento para {sym} — {mt5.last_error()}")
        idx = pd.to_datetime(pd.DataFrame(rates)["time"], unit="s")
        warmup_series[sym] = pd.Series(pd.DataFrame(rates)["close"].values, index=idx)
        log.info("live_feed: %s aquecimento %d barras, %s -> %s",
                 sym, len(idx), idx.iloc[0], idx.iloc[-1])

    common_idx = None
    for s in warmup_series.values():
        common_idx = s.index if common_idx is None else common_idx.intersection(s.index)
    common_idx = common_idx.sort_values() if common_idx is not None else pd.DatetimeIndex([])

    if len(common_idx) == 0:
        raise RuntimeError(
            "live_feed: intersecção de timestamps entre símbolos ficou vazia (aquecimento "
            "inútil) — ver as linhas de log acima com o intervalo de datas de cada símbolo "
            "para perceber qual está dessincronizado."
        )

    last_ts: dict[str, pd.Timestamp] = {}
    for ts in common_idx:
        for sym in symbols:
            last_ts[sym] = ts
        yield {sym: Bar(timestamp=ts, symbol=sym, close=float(warmup_series[sym].loc[ts]))
               for sym in symbols}

    log.info("live_feed: aquecimento concluído (%d barras) — a entrar em polling ao vivo (%.0fs)",
             len(common_idx), poll_seconds)

    consecutive_failures = 0

    while True:
        _time.sleep(poll_seconds)

        candidate_bars: dict[str, Bar] = {}
        all_ready = True
        fetch_failed = False
        last_error_msg: str | None = None
        for sym in symbols:
            rates = mt5.copy_rates_from_pos(sym, tf_const, 1, 1)
            if rates is None or len(rates) == 0:
                # Reativa a subscrição do símbolo (bug encontrado em teste
                # manual 2026-07-29): um símbolo pode "cair" da lista ativa
                # da corretora a meio de uma corrida longa (ex.: instabilidade
                # de rede), e symbol_select() só era chamado uma vez no
                # arranque — sem repetir aqui, o símbolo nunca mais voltava a
                # dar dados, obrigando a reiniciar o processo inteiro.
                mt5.symbol_select(sym, True)
                last_error_msg = str(mt5.last_error())
                log.warning("live_feed: sem dados para %s neste ciclo de polling (falha consecutiva "
                            "nº %d) — symbol_select() repetido, a tentar de novo: %s",
                            sym, consecutive_failures + 1, last_error_msg)
                all_ready = False
                fetch_failed = True
                break
            ts = pd.to_datetime(rates[0]["time"], unit="s")
            if ts <= last_ts.get(sym, pd.Timestamp.min):
                all_ready = False  # este símbolo ainda não fechou uma barra nova (normal, não é falha)
                break
            candidate_bars[sym] = Bar(timestamp=ts, symbol=sym, close=float(rates[0]["close"]))

        if fetch_failed:
            # Falha GENUÍNA de fetch (distinta de "barra ainda não fechou"
            # acima) — nunca desiste, tenta para sempre a cada poll_seconds
            # (pedido explícito: reconexão indefinida, ilimitada). A cada
            # RECONNECT_ATTEMPT_EVERY_N_FAILURES falhas seguidas, tenta
            # reinicializar a sessão MT5 (idempotente) — sem isto, uma
            # quebra que invalidasse a ligação IPC local (não só a ligação
            # da corretora, que o terminal já trata sozinho) nunca
            # recuperaria sozinha, mesmo com symbol_select() repetido.
            consecutive_failures += 1
            write_feed_health("degraded", consecutive_failures, last_error_msg, path=health_path)
            if consecutive_failures % RECONNECT_ATTEMPT_EVERY_N_FAILURES == 0:
                log.warning(
                    "live_feed: %d falhas consecutivas — a tentar reinicializar a ligação MT5 "
                    "(mt5.initialize()); as tentativas continuam indefinidamente, sem limite.",
                    consecutive_failures,
                )
                mt5.initialize()
            continue

        if not all_ready:
            continue

        if consecutive_failures > 0:
            log.info(
                "live_feed: dados a chegar de novo após %d falha(s) consecutiva(s) — ligação recuperada.",
                consecutive_failures,
            )
            consecutive_failures = 0
        write_feed_health("ok", 0, path=health_path)

        distinct_timestamps = {bar.timestamp for bar in candidate_bars.values()}
        if len(distinct_timestamps) != 1:
            log.warning(
                "live_feed: barras dessincronizadas entre símbolos neste ciclo (%s) — a aguardar o próximo",
                {sym: bar.timestamp for sym, bar in candidate_bars.items()},
            )
            continue

        for sym, bar in candidate_bars.items():
            last_ts[sym] = bar.timestamp
        yield candidate_bars


# ============================================================================
# Loop condutor — um só corpo, feed-agnóstico (HEDGE-02 + HEDGE-03 wired)
# ============================================================================


def run_hedge_loop(feed: Iterator[dict[str, "Bar"]], eligible_strategies: list[dict], risk_evaluate_fn,
                    coint_recheck_every: int = COINT_RECHECK_EVERY_BARS,
                    beta_window: int = 100, corr_window: int = 100, recalc_every: int = 50,
                    account_state_provider=None, correlation_matrix: dict | None = None,
                    on_event=None, selection_policy: str = "most_recent",
                    reload_eligible_fn=None, reload_every_bars: int = 50) -> list[dict]:
    """Consome qualquer iterador de feed (replay hoje, ao vivo no
    futuro — corpo do loop idêntico, sem bifurcação backtest/live,
    Pitfall 5) e devolve a lista de eventos processados (propostas
    emitidas + decisão do motor de risco, para auditoria/determinismo).

    Estado (posições abertas por par, barras em posição, barras desde o
    último reteste de cointegração por par) vive inteiramente em
    variáveis locais desta função — nunca em globais do módulo.

    Beta/z-score/correlação são recalculados causalmente a cada bar, via
    `backtest_engine.compute_rolling_beta`/`compute_zscore` (reusados,
    nunca reimplementados — Pitfall de deriva backtest/produção).
    Cointegração é reverificada (`recheck_cointegration`) antes de cada
    decisão de entrada e a cada `coint_recheck_every` barras para uma
    posição aberta; um par cuja reverificação falhar é retido na entrada
    e a posição aberta correspondente é fechada via
    `evaluate_hedge_signal` (a quebra de correlação/cointegração nunca
    fica presa a um snapshot obsoleto — HEDGE-03).

    Toda proposta `open_hedge` passa por `risk_evaluate_fn` (tipicamente
    `propose_to_risk_engine`) antes de abrir posição — uma rejeição
    (`RiskDecision.approved is False`) nunca abre a posição (HEDGE-02).
    Propostas `close_hedge` são autoridade própria deste módulo
    (D-03/D-04/D-05), distintas de um fecho forçado pelo motor de risco
    (fora de âmbito desta fase).

    IMPORTANTE (CLAUDE.md regra 7, corrigido 2026-07-23): os limiares de
    entrada/saída usados em `evaluate_hedge_signal` para cada par são
    SEMPRE derivados dos parâmetros especificamente validados da
    estratégia aprovada para esse par (via
    `_hedge_params_from_strategy_record`), nunca do `HEDGE_PARAMS`
    genérico do módulo — negociar com um limiar diferente do que foi
    testado no laboratório/walk-forward violaria a garantia de que só
    parâmetros validados chegam a produção. Um par cujo
    `strategy_record["params"]` esteja malformado é simplesmente
    ignorado nesse bar (fail-closed), nunca cai para um default
    genérico.

    params:
        feed                  (Iterator[dict[str, Bar]]) - ver replay_feed()
        eligible_strategies   (list[dict])                - ver load_eligible_strategies()
        risk_evaluate_fn      (callable)                  - (hedge_proposal, strategy_record,
            entry_price, stop_distance_price_units, account_state, correlation_matrix) -> RiskDecision
        coint_recheck_every   (int)                        - D-06
        beta_window, corr_window, recalc_every (int)       - janelas causais, mesma semântica
            de run_hedge_backtest()
        account_state_provider (callable | None)           - () -> AccountState; se None,
            usa um AccountState vazio/neutro a cada chamada (uso em testes)
        correlation_matrix     (dict | None)                - ver risk_engine.aggregate_exposure_pct
        on_event                (callable | None)            - chamado com CADA evento (dict, a mesma
            forma anexada a `events`) assim que é produzido, antes de o loop avançar para o próximo
            par/bar — permite a um chamador ao vivo (ex.: escrever para src.signal_bridge) reagir
            evento-a-evento em vez de esperar o batch completo devolvido no fim. Nunca afeta o valor
            de retorno (a lista `events` continua a acumular tudo, igual a antes); None (default)
            preserva o comportamento anterior exatamente.
        selection_policy        (str)                        - repassado a select_strategy_for_pair()
            (ver hedge_engine.select_strategy_for_pair). Default "most_recent" preserva o
            comportamento pré-existente.
        reload_eligible_fn      (callable | None)            - () -> list[dict], mesma forma de
            load_eligible_strategies(). Quando None (default), `eligible_strategies` nunca muda
            depois do arranque — comportamento idêntico ao pré-existente. Quando fornecido, a cada
            `reload_every_bars` barras é chamado para obter uma lista fresca (permite a um driver ao
            vivo, ex. scripts/run_live_hedge_loop.py, promover automaticamente estratégias recém-
            validadas sem reiniciar o processo). Pares novos ganham estado vazio (nunca reinicia
            estado de um par já a ser seguido). CRÍTICO (CLAUDE.md regra 7): uma posição já aberta
            NUNCA troca de estratégia a meio do trade — o par (strategy_record, pair_params) é
            fixado no momento da abertura (guardado em `positions[key]`) e reusado para toda decisão
            de SAÍDA dessa posição, mesmo que `eligible_strategies` mude entretanto; só a próxima
            ENTRADA (posição fechada) é que resolve de novo com a lista atualizada.
        reload_every_bars       (int)                        - cadência (em barras) da recarga acima;
            ignorado se reload_eligible_fn for None.

    devolve:
        list[dict] - um registo por evento processado:
            {"bar_index", "pair_a", "pair_b", "proposal", "risk_decision" | None}
    """
    from src.risk_engine import AccountState

    if account_state_provider is None:
        def account_state_provider():
            return AccountState(equity=10_000.0, daily_start_equity=10_000.0,
                                 weekly_start_equity=10_000.0, absolute_hwm=10_000.0,
                                 open_positions=[])

    corr_matrix = correlation_matrix or {}

    pairs = sorted({(s["pair_a"], s["pair_b"]) for s in eligible_strategies})

    price_buffers: dict[str, list[float]] = {}
    timestamps: dict[str, list] = {}
    positions: dict[tuple, dict | None] = {p: None for p in pairs}
    bars_held: dict[tuple, int] = {p: 0 for p in pairs}
    bars_since_coint_check: dict[tuple, int] = {p: coint_recheck_every for p in pairs}
    last_coint_result: dict[tuple, bool] = {p: False for p in pairs}

    events: list[dict] = []
    bar_index = 0

    for step in feed:
        for sym, bar in step.items():
            price_buffers.setdefault(sym, []).append(bar.close)

        for pair_a, pair_b in pairs:
            if pair_a not in price_buffers or pair_b not in price_buffers:
                continue
            n_common = min(len(price_buffers[pair_a]), len(price_buffers[pair_b]))
            if n_common < max(beta_window, corr_window) + 1:
                continue

            series_a = pd.Series(price_buffers[pair_a][-n_common:])
            series_b = pd.Series(price_buffers[pair_b][-n_common:])

            beta = compute_rolling_beta(series_a, series_b, beta_window, recalc_every)
            spread = series_a - beta * series_b
            zscore, spread_std = compute_zscore(spread, corr_window)
            correlation = series_a.rolling(corr_window).corr(series_b)

            z = float(zscore.iloc[-1])
            corr = float(correlation.iloc[-1])
            entry_std = float(spread_std.iloc[-1])
            if pd.isna(z) or pd.isna(corr):
                continue

            key = (pair_a, pair_b)
            position = positions[key]

            # A estratégia aprovada para este par é resolvida ANTES de
            # avaliar o sinal (não só depois de decidir abrir) — os
            # limiares de entrada/saída em si têm de vir dos parâmetros
            # validados desta estratégia, nunca de um HEDGE_PARAMS
            # genérico (ver nota CLAUDE.md regra 7 na docstring desta
            # função). Um par sem parâmetros válidos é ignorado neste
            # bar, fail-closed.
            #
            # Com posição ABERTA, a estratégia usada é a que foi FIXADA no
            # momento da entrada (position["strategy_record"]/["pair_params"]),
            # nunca re-resolvida — sem isto, uma recarga de
            # eligible_strategies (reload_eligible_fn) a meio de um trade
            # trocaria os limiares de SAÍDA por outros nunca testados juntos
            # com a entrada que os originou, violando CLAUDE.md regra 7.
            if position is None:
                strategy_record = select_strategy_for_pair(
                    eligible_strategies, pair_a, pair_b, policy=selection_policy,
                )
                if strategy_record is None:
                    continue
                pair_params = _hedge_params_from_strategy_record(strategy_record)
                if pair_params is None:
                    continue
            else:
                strategy_record = position["strategy_record"]
                pair_params = position["pair_params"]

            # Reteste de cointegração: sempre antes de uma decisão de
            # entrada, e a cada coint_recheck_every barras para posição aberta.
            need_recheck = position is None or bars_since_coint_check[key] >= coint_recheck_every
            if need_recheck:
                is_cointegrated, _ = recheck_cointegration(series_a, series_b)
                last_coint_result[key] = is_cointegrated
                bars_since_coint_check[key] = 0
            else:
                is_cointegrated = last_coint_result[key]
                bars_since_coint_check[key] += 1

            signal = evaluate_hedge_signal(
                pair_a, pair_b, z, corr, is_cointegrated, position, pair_params,
                bars_held=bars_held[key],
            )

            if signal is None:
                if position is not None:
                    bars_held[key] += 1
                continue

            if signal["action"] == "open_hedge":
                stop_distance = entry_std * STOP_DISTANCE_STD_MULTIPLIER
                # Estado da conta capturado UMA VEZ para esta decisão — reusado
                # tanto na chamada a risk_evaluate_fn como no cálculo da
                # exposição agregada pós-aprovação abaixo, para que ambos
                # vejam exatamente o mesmo snapshot (account_state_provider
                # não é garantidamente idempotente entre duas chamadas).
                current_state = account_state_provider()
                decision = risk_evaluate_fn(
                    signal, strategy_record, float(series_a.iloc[-1]), stop_distance,
                    current_state, corr_matrix,
                )
                event = {
                    "bar_index": bar_index, "pair_a": pair_a, "pair_b": pair_b,
                    "proposal": signal, "risk_decision": decision,
                    # Campos extra (não usados pelos testes de determinismo/ordem
                    # acima, que só olham para "proposal") consumidos por
                    # src/signal_bridge.py para construir a linha JSON enviada
                    # ao EA — o hedge_ratio (beta) e a distância de stop já
                    # calculados aqui nunca são recalculados do lado MQL5.
                    "hedge_ratio": float(beta.iloc[-1]),
                    "entry_price_a": float(series_a.iloc[-1]),
                    "stop_distance_price_units": stop_distance,
                }
                if getattr(decision, "approved", False):
                    # D-07 (mesmo padrão já aceite em risk_engine_mql5_spec.md
                    # para RiskGuard.mqh::CheckExposureLimits): o EA não tem
                    # matriz de correlação própria, por isso a exposição
                    # agregada ajustada por correlação é pré-calculada aqui e
                    # publicada na ponte — o EA só reconfirma os dois limiares
                    # (por par e agregado) contra este valor, nunca recalcula
                    # a agregação do zero.
                    from src.risk_engine import aggregate_exposure_pct
                    projected = list(current_state.open_positions) + [
                        {"pair_a": pair_a, "pair_b": pair_b, "exposure_pct": decision.size_lots}
                    ]
                    event["new_position_exposure_pct"] = decision.size_lots
                    event["aggregate_exposure_pct_after"] = aggregate_exposure_pct(projected, corr_matrix)
                    positions[key] = {
                        "direction": signal["direction"], "entry_bar": bar_index,
                        # Fixados no momento da entrada — ver nota acima e na
                        # docstring de reload_eligible_fn (CLAUDE.md regra 7).
                        "strategy_record": strategy_record, "pair_params": pair_params,
                    }
                    bars_held[key] = 0
                events.append(event)
                if on_event is not None:
                    on_event(event)
            else:  # close_hedge — autoridade própria do módulo, não passa pelo motor de risco
                close_event = {"bar_index": bar_index, "pair_a": pair_a, "pair_b": pair_b,
                               "proposal": signal, "risk_decision": None,
                               # Consumido por src/trade_ledger.py (via on_event) para calcular
                               # pnl_r — nunca recalculado a partir de outra fonte de preço.
                               "exit_price_a": float(series_a.iloc[-1]),
                               # id da estratégia FIXADA na entrada deste trade (position,
                               # ainda não sobrescrita nesta linha) — permite a um chamador ao
                               # vivo (scripts/run_live_hedge_loop.py) ir buscar a baseline
                               # out-of-sample EXATA desta estratégia, mesmo que
                               # eligible_strategies já tenha mudado (reload_eligible_fn)
                               # entre a abertura e o fecho deste trade.
                               "strategy_id": position["strategy_record"].get("id")}
                events.append(close_event)
                if on_event is not None:
                    on_event(close_event)
                positions[key] = None
                bars_held[key] = 0

        # Recarga periódica de eligible_strategies (opt-in — ver docstring).
        # Corre DEPOIS do loop de pares deste bar (nunca muta `pairs` a meio
        # da iteração de cima) e ANTES de avançar para o próximo bar.
        if reload_eligible_fn is not None and bar_index > 0 and bar_index % reload_every_bars == 0:
            new_eligible = reload_eligible_fn()
            new_pairs = sorted({(s["pair_a"], s["pair_b"]) for s in new_eligible})
            for p in new_pairs:
                if p not in positions:
                    positions[p] = None
                    bars_held[p] = 0
                    bars_since_coint_check[p] = coint_recheck_every
                    last_coint_result[p] = False
                    pairs.append(p)
                    log.info("run_hedge_loop: novo par elegível detetado na recarga (bar %d): %s/%s",
                             bar_index, p[0], p[1])
            for p in pairs:
                if positions[p] is not None:
                    continue  # posição aberta -> fixada, nunca troca a meio do trade (ver acima)
                old_record = select_strategy_for_pair(eligible_strategies, p[0], p[1], policy=selection_policy)
                new_record = select_strategy_for_pair(new_eligible, p[0], p[1], policy=selection_policy)
                old_id = old_record.get("id") if old_record else None
                new_id = new_record.get("id") if new_record else None
                if old_id != new_id:
                    log.info(
                        "run_hedge_loop: troca de estratégia para %s/%s na recarga (bar %d): %s -> %s",
                        p[0], p[1], bar_index, old_id, new_id,
                    )
            eligible_strategies = new_eligible

        bar_index += 1

    return events
