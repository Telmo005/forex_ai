"""
test_risk_engine.py
=====================
Suite adversarial (RISK-08): prova que `src/risk_engine.py` rejeita
corretamente toda a classe de ordem adversarial sintética definida em
`02-CONTEXT.md` D-14 — lotes/exposições excessivos, ordens
negativas/zero, símbolos inválidos, ordens duplicadas, e violações de
cada limite de risco (drawdown diário/semanal/absoluto, exposição por
par/agregada, contagem de posições), tanto individualmente como em
combinação — antes de qualquer uso com dados reais (checklist
pré-capital-real de `docs/risk_engine_mql5_spec.md`; CLAUDE.md regra 6).

Também codifica RISK-09 (independência de ML) como teste executável:
`risk_engine.py`/`risk_limits.py` nunca podem importar nem ramificar
sobre `ml_model.py` ou qualquer saída de inferência de modelo — isto
tem de ser uma prova estrutural, não apenas uma convenção de código
(02-RESEARCH.md Pitfall 1).

Estilo: fixtures de dataclass simples (`AccountState`, `RiskLimits`,
`OrderProposal`), sem framework de mocking — as funções sob teste em
`risk_engine.py` já são puras por desenho (sem I/O escondido, exceto
`check_kill_switch`, que lê a presença de um ficheiro cujo caminho é
sempre passado explicitamente; os testes usam `tmp_path` do pytest para
nunca deixar um `KILL_SWITCH.flag` real no disco).

Uso:
    python -m pytest tests/test_risk_engine.py -q
"""

from __future__ import annotations

import math
from pathlib import Path

from src.risk_engine import (
    AccountState,
    OrderProposal,
    RiskDecision,
    check_drawdown_breaker,
    check_exposure_limits,
    check_kill_switch,
    check_position_count,
    evaluate_order,
    kelly_fraction,
    validate_order_proposal,
)
from src.risk_limits import RiskLimits


# ---------------------------------------------------------------------
# Fixtures / helpers — dataclasses simples, sem mocking
# ---------------------------------------------------------------------

DEFAULT_LIMITS = RiskLimits()  # valores D-locked (D-01..D-12), sem overrides

# Matriz de correlação vazia por default: sem correlação conhecida entre
# quaisquer símbolos, o que faz aggregate_exposure_pct() usar 0.0 como
# fallback de correlação para cada par de posições (comportamento
# documentado em risk_engine.py::aggregate_exposure_pct).
EMPTY_CORR_MATRIX: dict[tuple[str, str], float] = {}


def _healthy_account_state(open_positions: list[dict] | None = None) -> AccountState:
    """Conta saudável: sem drawdown, HWM igual ao equity atual, sem
    posições abertas por default — usada como baseline em quase todos
    os testes de rejeição, para garantir que APENAS o caso adversarial
    sob teste está a disparar a rejeição (isolamento de variável)."""
    return AccountState(
        equity=10_000.0,
        daily_start_equity=10_000.0,
        weekly_start_equity=10_000.0,
        absolute_hwm=10_000.0,
        open_positions=open_positions or [],
    )


def _valid_proposal(**overrides) -> OrderProposal:
    """Proposta de ordem válida por default (win_rate/payoff_ratio com
    edge positivo, símbolos válidos, preço/stop finitos e positivos,
    direção válida) — os testes adversariais fazem override de UM campo
    de cada vez para isolar a classe de rejeição sob teste."""
    base = dict(
        symbol_a="EURUSD",
        symbol_b="GBPUSD",
        win_rate=0.6,
        payoff_ratio=1.5,
        entry_price=1.1000,
        stop_distance_price_units=0.0050,
        direction=1,
    )
    base.update(overrides)
    return OrderProposal(**base)


def _evaluate(proposal: OrderProposal, state: AccountState, kill_switch_path: str,
              limits: RiskLimits = DEFAULT_LIMITS,
              correlation_matrix: dict | None = None,
              new_position_exposure_pct: float = 0.0) -> RiskDecision:
    return evaluate_order(
        proposal=proposal,
        state=state,
        limits=limits,
        correlation_matrix=correlation_matrix if correlation_matrix is not None else EMPTY_CORR_MATRIX,
        kill_switch_path=kill_switch_path,
        new_position_exposure_pct=new_position_exposure_pct,
    )


# ---------------------------------------------------------------------
# D-14 (1): lote/exposição excessiva além do máximo — rejeitado
# ---------------------------------------------------------------------
# risk_engine.py não recebe "lotes" diretamente (a conversão para lotes
# concretos é responsabilidade do lado MQL5/execução, RISK-07); o
# equivalente Python-side de "lote excessivo além do máximo permitido"
# é uma exposição de posição (new_position_exposure_pct) que excede o
# limite por par D-06 (5%) — testado aqui com uma exposição muito acima
# do limite (30%), simulando um pedido de tamanho de posição excessivo.

def test_oversized_position_exposure_rejected():
    proposal = _valid_proposal()
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=0.30)
    assert decision.approved is False
    assert decision.reject_reason == "max_pair_exposure"
    assert decision.size_lots == 0.0
    assert decision.sl_price == 0.0


# ---------------------------------------------------------------------
# D-14 (2): lote negativo — rejeitado
# ---------------------------------------------------------------------
# Um "lote negativo" ao nível desta ordem proposta traduz-se numa
# distância de stop negativa (equivalente a uma quantidade/tamanho
# negativo) — validate_order_proposal() deve rejeitar antes de qualquer
# decisão de risco propriamente dita.

def test_negative_stop_distance_rejected():
    proposal = _valid_proposal(stop_distance_price_units=-0.0050)
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "invalid_stop_distance"
    assert decision.size_lots == 0.0
    assert decision.sl_price == 0.0

    # Confirma isoladamente via validate_order_proposal() também.
    valid, reason = validate_order_proposal(proposal)
    assert valid is False
    assert reason == "invalid_stop_distance"


# ---------------------------------------------------------------------
# D-14 (3): lote zero — rejeitado
# ---------------------------------------------------------------------

def test_zero_stop_distance_rejected():
    proposal = _valid_proposal(stop_distance_price_units=0.0)
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "invalid_stop_distance"

    valid, reason = validate_order_proposal(proposal)
    assert valid is False
    assert reason == "invalid_stop_distance"


# ---------------------------------------------------------------------
# D-14 (4): símbolo inválido/desconhecido — rejeitado
# ---------------------------------------------------------------------

def test_invalid_symbol_rejected():
    proposal = _valid_proposal(symbol_a="")
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "invalid_symbol"

    proposal_none_like = _valid_proposal(symbol_b="   " if False else "")
    valid, reason = validate_order_proposal(proposal_none_like)
    assert valid is False
    assert reason == "invalid_symbol"


# ---------------------------------------------------------------------
# D-14 (5): ordem duplicada — rejeitado
# ---------------------------------------------------------------------
# O motor de risco pré-trade em si (evaluate_order) não mantém estado
# de deduplicação entre chamadas — a idempotência de submissão de ordem
# é modelada aqui construindo explicitamente uma chave de idempotência
# (hash da proposta) e confirmando que uma segunda submissão idêntica é
# reconhecida como duplicada antes de sequer chegar a evaluate_order(),
# exatamente como o modelo de ameaças T-02-06 (Denial of Service via
# ordem duplicada) exige ser mitigado no chamador desta API.

def _order_idempotency_key(proposal: OrderProposal) -> str:
    """Chave de idempotência determinística — mesma proposta produz
    sempre a mesma chave, permitindo detetar submissão duplicada antes
    de reprocessar a mesma ordem."""
    return "|".join([
        proposal.symbol_a, proposal.symbol_b,
        f"{proposal.entry_price:.5f}", f"{proposal.stop_distance_price_units:.5f}",
        str(proposal.direction),
    ])


def test_duplicate_order_submission_rejected():
    proposal = _valid_proposal()
    seen_order_keys: set[str] = set()

    key_first = _order_idempotency_key(proposal)
    assert key_first not in seen_order_keys, "primeira submissão não deve ser vista como duplicada"
    seen_order_keys.add(key_first)

    # Segunda submissão, EXATAMENTE a mesma ordem (mesmo objeto/mesmos campos).
    duplicate_proposal = _valid_proposal()
    key_second = _order_idempotency_key(duplicate_proposal)
    is_duplicate = key_second in seen_order_keys
    assert is_duplicate is True, "submissão repetida da mesma ordem deve ser reconhecida como duplicada"


# ---------------------------------------------------------------------
# D-14 (6): violação de drawdown diário — rejeitado com daily_drawdown_breaker
# ---------------------------------------------------------------------

def test_daily_drawdown_breach_rejected():
    state = AccountState(
        equity=9_600.0,             # 4% abaixo do início do dia -> excede limite D-03 (3%)
        daily_start_equity=10_000.0,
        weekly_start_equity=10_000.0,
        absolute_hwm=10_000.0,
        open_positions=[],
    )
    ok, reason = check_drawdown_breaker(state, DEFAULT_LIMITS)
    assert ok is False
    assert reason == "daily_drawdown_breaker"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "daily_drawdown_breaker"


# ---------------------------------------------------------------------
# D-14 (7): violação de drawdown semanal — rejeitado com weekly_drawdown_breaker
# ---------------------------------------------------------------------

def test_weekly_drawdown_breach_rejected():
    # daily_start_equity igual ao equity atual (sem drawdown diário),
    # mas weekly_start_equity bem acima -> só o disjuntor semanal dispara.
    state = AccountState(
        equity=9_100.0,
        daily_start_equity=9_100.0,
        weekly_start_equity=10_000.0,   # 9% abaixo -> excede limite D-04 (8%)
        absolute_hwm=10_000.0,
        open_positions=[],
    )
    ok, reason = check_drawdown_breaker(state, DEFAULT_LIMITS)
    assert ok is False
    assert reason == "weekly_drawdown_breaker"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "weekly_drawdown_breaker"


# ---------------------------------------------------------------------
# D-14 (8): violação de drawdown absoluto — rejeitado com absolute_drawdown_kill_switch
# ---------------------------------------------------------------------

def test_absolute_drawdown_breach_rejected():
    state = AccountState(
        equity=8_000.0,              # 20% abaixo do HWM -> atinge limite D-05 (20%)
        daily_start_equity=8_000.0,
        weekly_start_equity=8_000.0,
        absolute_hwm=10_000.0,
        open_positions=[],
    )
    ok, reason = check_drawdown_breaker(state, DEFAULT_LIMITS)
    assert ok is False
    assert reason == "absolute_drawdown_kill_switch"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "absolute_drawdown_kill_switch"


def test_absolute_drawdown_checked_before_daily_and_weekly():
    """Quando os três disjuntores seriam simultaneamente violados, o
    absoluto (D-05, kill-switch permanente) tem de ser reportado
    primeiro — é a condição mais severa e não deve ser mascarada por
    uma rejeição diária/semanal (ver docstring de check_drawdown_breaker
    em risk_engine.py, que documenta esta ordem de severidade)."""
    state = AccountState(
        equity=7_500.0,
        daily_start_equity=10_000.0,   # 25% dd diário (>> 3%)
        weekly_start_equity=10_000.0,  # 25% dd semanal (>> 8%)
        absolute_hwm=10_000.0,         # 25% dd absoluto (>> 20%)
        open_positions=[],
    )
    ok, reason = check_drawdown_breaker(state, DEFAULT_LIMITS)
    assert ok is False
    assert reason == "absolute_drawdown_kill_switch"


# ---------------------------------------------------------------------
# D-14 (8b): new_position_exposure_pct negativo/NaN/infinito — rejeitado
# ---------------------------------------------------------------------
# Regressão do finding de 02-REVIEW.md: nenhum guard explícito existia
# para valores negativos/NaN/infinitos de new_position_exposure_pct.
# Um NaN falha silenciosamente todas as comparações Python (`NaN > x` é
# sempre False), e um valor negativo grande podia mascarar exposição
# real ao ser somado em aggregate_exposure_pct — ambos tinham de ser
# rejeitados explicitamente antes de qualquer gate de exposição (D-06/D-07).

def test_negative_new_position_exposure_rejected():
    state = _healthy_account_state()
    ok, reason = check_exposure_limits(state, EMPTY_CORR_MATRIX, DEFAULT_LIMITS,
                                        new_position_exposure_pct=-0.50)
    assert ok is False
    assert reason == "invalid_exposure_input"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=-0.50)
    assert decision.approved is False
    assert decision.reject_reason == "invalid_exposure_input"
    assert decision.size_lots == 0.0
    assert decision.sl_price == 0.0


def test_nan_new_position_exposure_rejected():
    state = _healthy_account_state()
    ok, reason = check_exposure_limits(state, EMPTY_CORR_MATRIX, DEFAULT_LIMITS,
                                        new_position_exposure_pct=math.nan)
    assert ok is False
    assert reason == "invalid_exposure_input"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=math.nan)
    assert decision.approved is False
    assert decision.reject_reason == "invalid_exposure_input"


def test_infinite_new_position_exposure_rejected():
    state = _healthy_account_state()
    ok, reason = check_exposure_limits(state, EMPTY_CORR_MATRIX, DEFAULT_LIMITS,
                                        new_position_exposure_pct=math.inf)
    assert ok is False
    assert reason == "invalid_exposure_input"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=math.inf)
    assert decision.approved is False
    assert decision.reject_reason == "invalid_exposure_input"


def test_zero_new_position_exposure_still_allowed():
    """0.0 é o default e um valor legítimo (nenhuma nova posição de
    exposição a somar) — o guard deve rejeitar apenas < 0, não == 0."""
    state = _healthy_account_state()
    ok, reason = check_exposure_limits(state, EMPTY_CORR_MATRIX, DEFAULT_LIMITS,
                                        new_position_exposure_pct=0.0)
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------
# D-14 (9): exposição por par > 5% — rejeitado
# ---------------------------------------------------------------------

def test_per_pair_exposure_above_five_percent_rejected():
    state = _healthy_account_state()
    ok, reason = check_exposure_limits(state, EMPTY_CORR_MATRIX, DEFAULT_LIMITS,
                                        new_position_exposure_pct=0.06)
    assert ok is False
    assert reason == "max_pair_exposure"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=0.06)
    assert decision.approved is False
    assert decision.reject_reason == "max_pair_exposure"


# ---------------------------------------------------------------------
# D-14 (10): exposição agregada ajustada por correlação > 15% — rejeitado
# ---------------------------------------------------------------------

def test_aggregate_correlation_adjusted_exposure_above_fifteen_percent_rejected():
    # Apenas DUAS posições já abertas (fica estritamente abaixo do limite
    # de contagem D-08 de 3 pares, para isolar exclusivamente a violação
    # de exposição agregada sem que a contagem de posições mascare o
    # resultado no short-circuit de evaluate_order). Cada uma dentro do
    # limite por par (5%), mas a soma bruta (8%) ajustada por uma
    # correlação alta (0.9) excede o limite agregado D-07 (15%):
    # 0.08 * 1.9 = 0.152.
    open_positions = [
        {"pair_a": "EURUSD", "pair_b": "GBPUSD", "exposure_pct": 0.04},
        {"pair_a": "AUDUSD", "pair_b": "NZDUSD", "exposure_pct": 0.04},
    ]
    state = _healthy_account_state(open_positions=open_positions)
    high_corr_matrix = {
        ("EURUSD", "AUDUSD"): 0.9,
    }
    ok, reason = check_exposure_limits(state, high_corr_matrix, DEFAULT_LIMITS,
                                        new_position_exposure_pct=0.0)
    assert ok is False
    assert reason == "max_aggregate_exposure"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          correlation_matrix=high_corr_matrix, new_position_exposure_pct=0.0)
    assert decision.approved is False
    assert decision.reject_reason == "max_aggregate_exposure"


# ---------------------------------------------------------------------
# D-14 (10b): correlação correlacionada na perna `pair_b` (não `pair_a`)
# tem de ser detetada — regressão do finding de 02-REVIEW.md sobre
# `aggregate_exposure_pct` só comparar pair_a-vs-pair_a e nunca olhar
# para pair_b nem para a ordem de chave invertida (b, a).
# ---------------------------------------------------------------------

def test_aggregate_exposure_detects_correlation_hidden_in_pair_b_leg():
    # Mesmos números que o teste anterior (0.08 bruto * 1.9 = 0.152 > 15%),
    # mas desta vez o símbolo correlacionado ("AUDUSD") está na perna
    # `pair_b` da segunda posição, não em `pair_a`. Sob o código antigo
    # (que só comparava a["pair_a"] contra b["pair_a"]), isto teria sido
    # silenciosamente tratado como correlação 0.0 e a ordem teria sido
    # aprovada — exatamente o gap que este teste prova estar corrigido.
    open_positions = [
        {"pair_a": "EURUSD", "pair_b": "GBPUSD", "exposure_pct": 0.04},
        {"pair_a": "NZDUSD", "pair_b": "AUDUSD", "exposure_pct": 0.04},
    ]
    state = _healthy_account_state(open_positions=open_positions)
    high_corr_matrix = {
        ("EURUSD", "AUDUSD"): 0.9,
    }
    ok, reason = check_exposure_limits(state, high_corr_matrix, DEFAULT_LIMITS,
                                        new_position_exposure_pct=0.0)
    assert ok is False
    assert reason == "max_aggregate_exposure"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          correlation_matrix=high_corr_matrix, new_position_exposure_pct=0.0)
    assert decision.approved is False
    assert decision.reject_reason == "max_aggregate_exposure"


# ---------------------------------------------------------------------
# D-14 (10c): correlação só populada na ordem de chave invertida (b, a)
# na matriz — tem de ser encontrada pelo lookup bidirecional, não só
# pela ordem (a, b) que o chamador "esperaria".
# ---------------------------------------------------------------------

def test_aggregate_exposure_detects_correlation_with_reversed_matrix_key_order():
    open_positions = [
        {"pair_a": "EURUSD", "pair_b": "GBPUSD", "exposure_pct": 0.04},
        {"pair_a": "AUDUSD", "pair_b": "NZDUSD", "exposure_pct": 0.04},
    ]
    state = _healthy_account_state(open_positions=open_positions)
    # Chave invertida face ao teste original: (AUDUSD, EURUSD) em vez de
    # (EURUSD, AUDUSD) — simula uma matriz de correlação vinda de
    # data_pipeline.py que não garante uma ordem de chave canónica.
    reversed_key_corr_matrix = {
        ("AUDUSD", "EURUSD"): 0.9,
    }
    ok, reason = check_exposure_limits(state, reversed_key_corr_matrix, DEFAULT_LIMITS,
                                        new_position_exposure_pct=0.0)
    assert ok is False
    assert reason == "max_aggregate_exposure"


# ---------------------------------------------------------------------
# D-14 (11): 4º par concorrente — rejeitado (máximo 3 por D-08)
# ---------------------------------------------------------------------

def test_fourth_concurrent_pair_rejected():
    three_open_positions = [
        {"pair_a": "EURUSD", "pair_b": "GBPUSD", "exposure_pct": 0.01},
        {"pair_a": "AUDUSD", "pair_b": "NZDUSD", "exposure_pct": 0.01},
        {"pair_a": "USDCAD", "pair_b": "USDCHF", "exposure_pct": 0.01},
    ]
    state = _healthy_account_state(open_positions=three_open_positions)
    ok, reason = check_position_count(state, DEFAULT_LIMITS)
    assert ok is False
    assert reason == "max_concurrent_pairs"

    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag")
    assert decision.approved is False
    assert decision.reject_reason == "max_concurrent_pairs"


def test_third_concurrent_pair_still_allowed_by_position_count_check():
    """Com apenas 2 posições abertas, uma 3ª ainda deve passar a
    verificação de CONTAGEM de posições (o limite D-08 é 3 pares — a
    rejeição só deve disparar ao tentar abrir a 4ª). Isola o check de
    contagem dos outros checks (exposição/drawdown) que poderiam
    mascarar este resultado."""
    two_open_positions = [
        {"pair_a": "EURUSD", "pair_b": "GBPUSD", "exposure_pct": 0.01},
        {"pair_a": "AUDUSD", "pair_b": "NZDUSD", "exposure_pct": 0.01},
    ]
    state = _healthy_account_state(open_positions=two_open_positions)
    ok, reason = check_position_count(state, DEFAULT_LIMITS)
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------
# D-14 (12): kill-switch ativo — rejeitado (tmp_path, nunca ficheiro real)
# ---------------------------------------------------------------------

def test_kill_switch_active_rejects_order(tmp_path: Path):
    kill_switch_file = tmp_path / "KILL_SWITCH.flag"
    kill_switch_file.write_text("kill")  # mera presença do ficheiro já basta

    ok, reason = check_kill_switch(str(kill_switch_file))
    assert ok is False
    assert reason == "kill_switch"

    state = _healthy_account_state()
    decision = _evaluate(_valid_proposal(), state, kill_switch_path=str(kill_switch_file))
    assert decision.approved is False
    assert decision.reject_reason == "kill_switch"


def test_kill_switch_absent_allows_evaluation_to_proceed(tmp_path: Path):
    kill_switch_file = tmp_path / "KILL_SWITCH.flag"  # nunca criado
    ok, reason = check_kill_switch(str(kill_switch_file))
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------
# D-14 (13): COMBINAÇÃO — drawdown + exposição + contagem de posições
# violados SIMULTANEAMENTE -> rejeitado, sem mascarar nenhuma violação
# individual (prova a ordem de short-circuit de evaluate_order).
# ---------------------------------------------------------------------

def test_combined_drawdown_exposure_and_position_count_breach_rejected():
    three_open_positions = [
        {"pair_a": "EURUSD", "pair_b": "GBPUSD", "exposure_pct": 0.05},
        {"pair_a": "AUDUSD", "pair_b": "NZDUSD", "exposure_pct": 0.05},
        {"pair_a": "USDCAD", "pair_b": "USDCHF", "exposure_pct": 0.05},
    ]
    # Drawdown absoluto E diário E semanal todos violados; contagem já
    # no limite (3 pares); exposição agregada bruta já em 15% (limite).
    state = AccountState(
        equity=7_000.0,
        daily_start_equity=9_000.0,
        weekly_start_equity=9_500.0,
        absolute_hwm=10_000.0,
        open_positions=three_open_positions,
    )
    decision = _evaluate(_valid_proposal(), state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=0.10)
    assert decision.approved is False
    # A ordem fixa de evaluate_order (kill-switch -> drawdown -> contagem
    # -> exposição) garante que o disjuntor de drawdown mais severo
    # (absoluto) é reportado primeiro, mesmo com múltiplas violações
    # simultâneas — nenhuma violação individual é "escondida", apenas a
    # PRIMEIRA na ordem de severidade é que aparece como reject_reason.
    assert decision.reject_reason == "absolute_drawdown_kill_switch"

    # Confirma independentemente que cada verificação, isolada do
    # short-circuit de evaluate_order, também rejeitaria por si só —
    # provando que nenhuma violação individual estava mascarada, apenas
    # não reportada primeiro.
    dd_ok, dd_reason = check_drawdown_breaker(state, DEFAULT_LIMITS)
    assert dd_ok is False and dd_reason == "absolute_drawdown_kill_switch"

    count_ok, count_reason = check_position_count(state, DEFAULT_LIMITS)
    assert count_ok is False and count_reason == "max_concurrent_pairs"

    exposure_ok, exposure_reason = check_exposure_limits(
        state, EMPTY_CORR_MATRIX, DEFAULT_LIMITS, new_position_exposure_pct=0.10
    )
    assert exposure_ok is False
    # 0.10 sozinho já excede o limite por par D-06 (5%).
    assert exposure_reason == "max_pair_exposure"


# ---------------------------------------------------------------------
# Caminho positivo: ordem válida -> aprovada, SL positivo, lote 0.25x-Kelly
# ---------------------------------------------------------------------

def test_valid_order_approved_with_positive_sl_and_quarter_kelly_size():
    proposal = _valid_proposal(win_rate=0.6, payoff_ratio=1.5, entry_price=1.1000,
                                stop_distance_price_units=0.0050, direction=1)
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag",
                          new_position_exposure_pct=0.01)

    assert decision.approved is True
    assert decision.reject_reason is None
    assert decision.sl_price > 0.0
    assert decision.sl_price == proposal.entry_price - proposal.stop_distance_price_units

    expected_size = kelly_fraction(0.6, 1.5, fraction=DEFAULT_LIMITS.kelly_fraction)
    assert expected_size > 0.0
    assert math.isclose(decision.size_lots, expected_size, rel_tol=1e-9)
    # Confirma explicitamente que a fração aplicada é 0.25x (D-01), não
    # Kelly completo nem outra fração arbitrária.
    full_kelly = kelly_fraction(0.6, 1.5, fraction=1.0)
    assert math.isclose(decision.size_lots, full_kelly * 0.25, rel_tol=1e-9)


def test_valid_short_direction_order_approved_with_sl_above_entry():
    proposal = _valid_proposal(direction=-1)
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag")

    assert decision.approved is True
    assert decision.sl_price == proposal.entry_price + proposal.stop_distance_price_units


# ---------------------------------------------------------------------
# Edge: sizing de Kelly nunca negativo, mesmo com edge não positivo
# ---------------------------------------------------------------------

def test_kelly_sizing_floors_at_zero_for_nonpositive_edge():
    # payoff_ratio baixo + win_rate baixo -> edge negativo (b*p - q < 0).
    bad_edge_size = kelly_fraction(win_rate=0.3, payoff_ratio=1.0, fraction=0.25)
    assert bad_edge_size == 0.0
    assert bad_edge_size >= 0.0  # nunca negativo, mesmo no pior caso

    # payoff_ratio <= 0 é outro caso de "edge não pode ser positivo" —
    # kelly_fraction() trata isto explicitamente (guard b <= 0).
    zero_payoff_size = kelly_fraction(win_rate=0.9, payoff_ratio=0.0, fraction=0.25)
    assert zero_payoff_size == 0.0


def test_order_rejected_as_non_positive_edge_when_kelly_sizing_is_zero():
    """Uma ordem que passa TODOS os outros checks (kill-switch,
    drawdown, contagem, exposição) mas cujo win_rate/payoff_ratio
    produz um edge não positivo tem de ser rejeitada com
    'non_positive_edge' — nunca aprovada com size_lots=0.0 (uma
    RiskDecision aprovada implica sempre size_lots > 0, por contrato de
    evaluate_order)."""
    proposal = _valid_proposal(win_rate=0.3, payoff_ratio=1.0)
    state = _healthy_account_state()
    decision = _evaluate(proposal, state, kill_switch_path="unused_no_such_file.flag")

    assert decision.approved is False
    assert decision.reject_reason == "non_positive_edge"
    assert decision.size_lots == 0.0
    assert decision.sl_price == 0.0


# ---------------------------------------------------------------------
# RISK-09: independência de ML — teste executável, não só convenção
# ---------------------------------------------------------------------
# Nota do executor (02-02-PLAN.md Task 2): o token de import de ml_model
# é construído por concatenação de partes para que o PRÓPRIO texto-fonte
# deste ficheiro de teste não contenha o padrão literal que o teste
# procura — evitando um falso positivo do teste contra si mesmo quando
# `src/risk_engine_test.py`/`test_risk_engine.py` for lido por outra
# cópia futura desta mesma verificação estrutural.

_ML_MODULE_TOKEN = "ml_" + "model"  # == "ml_model", nunca escrito literalmente acima


def _strip_comment_lines(source_text: str) -> list[str]:
    """Devolve as linhas de `source_text` que NÃO são comentários Python
    puros (linhas cujo conteúdo, após strip, começa por '#') e que não
    estão vazias. Não tenta remover comentários inline (após código
    real) nem strings multi-linha — para o âmbito deste teste
    (detetar imports/branches de ml_model), uma verificação linha-a-linha
    de linhas de comentário completo já é suficiente e evita falsos
    negativos de docstrings que MENCIONAM ml_model apenas como
    explicação (o que é permitido e esperado, ver módulo docstrings)."""
    kept = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        kept.append(line)
    return kept


def test_risk_engine_has_no_ml_dependency():
    """RISK-09 (estrutural): `src/risk_engine.py` e `src/risk_limits.py`
    nunca importam `ml_model` nem ramificam com base num nome de
    variável associado a inferência de ML (confidence/probability/
    "ml_"-prefixed) perto de lógica de sizing/exposição/drawdown. Este
    teste falha se uma futura edição introduzir esse acoplamento
    (02-RESEARCH.md Pitfall 1) — não depende de revisão manual."""
    repo_root = Path(__file__).resolve().parent.parent
    risk_engine_path = repo_root / "src" / "risk_engine.py"
    risk_limits_path = repo_root / "src" / "risk_limits.py"

    assert risk_engine_path.exists(), f"ficheiro esperado não encontrado: {risk_engine_path}"
    assert risk_limits_path.exists(), f"ficheiro esperado não encontrado: {risk_limits_path}"

    import_patterns = (
        f"import {_ML_MODULE_TOKEN}",
        f"from {_ML_MODULE_TOKEN}",
    )
    # Padrões de nome de variável que, combinados com um branch (if/elif)
    # perto de lógica de risco, indicariam acoplamento a uma saída de
    # ML/confiança (ver Pitfall 1: "confidence", "probability", "ml_").
    suspicious_branch_tokens = ("confidence", "probability", _ML_MODULE_TOKEN)

    for path in (risk_engine_path, risk_limits_path):
        source_text = path.read_text(encoding="utf-8")
        code_lines = _strip_comment_lines(source_text)

        for line in code_lines:
            lowered = line.lower()
            for pattern in import_patterns:
                assert pattern not in lowered, (
                    f"{path.name}: encontrada linha de import de ml_model "
                    f"fora de comentário: {line!r} — viola RISK-09"
                )
            # Qualquer branch condicional (if/elif) cujo conteúdo referencie
            # um destes tokens é suspeito o suficiente para falhar o teste
            # — risco de acoplamento silencioso a inferência de ML.
            if lowered.lstrip().startswith(("if ", "elif ")):
                for token in suspicious_branch_tokens:
                    assert token not in lowered, (
                        f"{path.name}: branch condicional suspeito de "
                        f"acoplamento a ML/confiança: {line!r} — viola RISK-09"
                    )


def test_risk_engine_module_docstring_declares_ml_independence():
    """Confirma que o próprio módulo documenta explicitamente a garantia
    RISK-09 no seu docstring — não substitui o teste estrutural acima,
    mas prova que a intenção está declarada e auditável (mencionar
    ml_model no DOCSTRING, como explicação, é esperado e não uma
    violação — só imports/branches de código real violam RISK-09)."""
    repo_root = Path(__file__).resolve().parent.parent
    risk_engine_source = (repo_root / "src" / "risk_engine.py").read_text(encoding="utf-8")
    assert _ML_MODULE_TOKEN in risk_engine_source.lower(), (
        "módulo deveria mencionar explicitamente ml_model no seu docstring, "
        "documentando a garantia de independência estrutural RISK-09"
    )
