"""
test_hedge_engine.py
======================
Prova, como testes executáveis, as propriedades de segurança de
`src/hedge_engine.py`:

- HEDGE-01 (D-07): só estratégias `status=="passed"` E `wf_passed==1`
  E `revalidated_on_real_data==1` são elegíveis — uma estratégia
  aprovada só em dados sintéticos NUNCA passa (Pitfall 4).
- D-08: duas estratégias elegíveis para o mesmo par resolvem por
  desempate explícito e registado, nunca `strategies[0]` silencioso.
- D-01..D-05 / Pitfall 2: `evaluate_hedge_signal` exige cointegração+
  z-score+correlação em conjunto na entrada (Pitfall 6: correlação
  sozinha nunca abre) e avalia saídas na ordem quebra-de-correlação ->
  reversão -> stop-de-tempo, com a quebra de correlação a ter sempre
  prioridade mesmo quando os outros gatilhos disparam no mesmo bar.
- HEDGE-03: `recheck_cointegration` reusa `statsmodels.coint()` e falha
  fechado — `(False, nan)`, nunca uma exceção propagada nem `(True, ...)`.
- HEDGE-02: toda proposta é encaminhada para `risk_engine.evaluate_order`
  via `propose_to_risk_engine`; o módulo nunca calcula o seu próprio
  lote nem ramifica sobre limites de risco (prova estrutural, não só
  revisão de código).

Estilo: fixtures `_valid_*(**overrides)`/`_healthy_*()`, sem framework
de mocking, mesma disciplina de `tests/test_risk_engine.py`.

Uso:
    python -m pytest tests/test_hedge_engine.py -q
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.hedge_engine import (
    HEDGE_PARAMS,
    Bar,
    evaluate_hedge_signal,
    load_eligible_strategies,
    propose_to_risk_engine,
    recheck_cointegration,
    replay_feed,
    run_hedge_loop,
    select_strategy_for_pair,
)
from src.risk_engine import AccountState, RiskDecision
from src.strategy_registry import init_db, save_strategy, save_walk_forward_result


# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------

def _strategy_record(**overrides) -> dict:
    base = dict(
        id="strat-1", created_at="2026-07-01T00:00:00", pair_a="EURUSD", pair_b="GBPUSD",
        # Mesmos valores numéricos do antigo HEDGE_PARAMS genérico (D-01..D-05),
        # agora como os parâmetros ESPECÍFICOS desta estratégia fixture —
        # preserva o comportamento esperado pelos testes existentes enquanto
        # exercita o caminho real de _hedge_params_from_strategy_record()
        # (2026-07-23, CLAUDE.md regra 7).
        params={"entry_threshold": 2.0, "exit_threshold": 0.3, "min_correlation": 0.5,
                "max_hold_bars": 75, "beta_window": 100, "corr_window": 100},
        status="passed", fail_reasons=[], generation=0,
        parent_id=None, total_trades=30, win_rate=0.55, profit_factor=1.3,
        sharpe_per_trade=0.2, total_return_r=5.0, max_drawdown_r=2.0, avg_hold_bars=20.0,
        avg_win_r=1.5, avg_loss_r=-1.0, bars_tested=1000, trades=[], cost_model_version="placeholder-v1",
    )
    base.update(overrides)
    return base


def _make_registry(tmp_path: Path, records: list[tuple[dict, dict | None]]) -> str:
    """Cria uma strategy_lab.db temporária real (não mock) com os
    registos dados. Cada item é (record, wf_kwargs | None); wf_kwargs,
    se presente, é passado a save_walk_forward_result()."""
    db_path = str(tmp_path / "strategy_lab.db")
    init_db(db_path)
    for record, wf_kwargs in records:
        save_strategy(db_path, record)
        if wf_kwargs is not None:
            save_walk_forward_result(db_path, record["id"], **wf_kwargs)
    return db_path


def _healthy_account_state() -> AccountState:
    return AccountState(equity=10_000.0, daily_start_equity=10_000.0,
                         weekly_start_equity=10_000.0, absolute_hwm=10_000.0,
                         open_positions=[])


# ---------------------------------------------------------------------
# HEDGE-01 / D-07: gate de elegibilidade de estratégias
# ---------------------------------------------------------------------

def test_load_eligible_strategies_requires_both_wf_flags(tmp_path, caplog):
    records = [
        (_strategy_record(id="A", status="passed"),
         dict(wf_passed=True, wf_fold_results=[], revalidated_on_real_data=True)),
        (_strategy_record(id="B", status="passed"),
         dict(wf_passed=True, wf_fold_results=[], revalidated_on_real_data=False)),
        (_strategy_record(id="C", status="failed"),
         dict(wf_passed=True, wf_fold_results=[], revalidated_on_real_data=True)),
    ]
    db_path = _make_registry(tmp_path, records)

    with caplog.at_level("WARNING"):
        eligible = load_eligible_strategies(db_path)

    ids = {r["id"] for r in eligible}
    assert ids == {"A"}
    assert any("B" in rec.message for rec in caplog.records)


def test_load_eligible_strategies_empty_db_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "does_not_exist.db")
    assert load_eligible_strategies(db_path) == []


def test_load_eligible_strategies_no_raw_sqlite_in_module():
    src = (Path(__file__).resolve().parent.parent / "src" / "hedge_engine.py").read_text(encoding="utf-8")
    assert "import sqlite3" not in src
    assert "sqlite3.connect" not in src


# ---------------------------------------------------------------------
# D-08: desempate explícito para o mesmo par
# ---------------------------------------------------------------------

def test_select_strategy_for_pair_tie_break_most_recent(caplog):
    eligible = [
        _strategy_record(id="older", pair_a="EURUSD", pair_b="GBPUSD", created_at="2026-01-01T00:00:00"),
        _strategy_record(id="newer", pair_a="EURUSD", pair_b="GBPUSD", created_at="2026-06-01T00:00:00"),
    ]
    with caplog.at_level("WARNING"):
        chosen = select_strategy_for_pair(eligible, "EURUSD", "GBPUSD")
    assert chosen["id"] == "newer"
    assert any("older" in rec.message and "newer" in rec.message for rec in caplog.records)


def test_select_strategy_for_pair_single_match_no_warning(caplog):
    eligible = [_strategy_record(id="only", pair_a="EURUSD", pair_b="GBPUSD")]
    with caplog.at_level("WARNING"):
        chosen = select_strategy_for_pair(eligible, "EURUSD", "GBPUSD")
    assert chosen["id"] == "only"
    assert not caplog.records


def test_select_strategy_for_pair_no_match_returns_none():
    eligible = [_strategy_record(id="only", pair_a="EURUSD", pair_b="GBPUSD")]
    assert select_strategy_for_pair(eligible, "USDJPY", "AUDUSD") is None


# ---------------------------------------------------------------------
# Entrada (D-01/D-02, Pitfall 6)
# ---------------------------------------------------------------------

def test_entry_opens_when_all_conditions_met():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=2.5, correlation=0.6,
                                    is_cointegrated=True, position=None, params=HEDGE_PARAMS)
    assert signal["action"] == "open_hedge"
    assert signal["direction"] == -1  # zscore positivo -> short spread
    assert signal["trigger"] == "spread_zscore"


def test_entry_direction_flips_for_negative_zscore():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=-2.5, correlation=0.6,
                                    is_cointegrated=True, position=None, params=HEDGE_PARAMS)
    assert signal["direction"] == 1


def test_entry_blocked_when_correlation_high_but_not_cointegrated():
    """Pitfall 6: correlação sozinha nunca é suficiente para abrir."""
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=2.5, correlation=0.9,
                                    is_cointegrated=False, position=None, params=HEDGE_PARAMS)
    assert signal is None


def test_entry_blocked_when_zscore_below_threshold():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=1.0, correlation=0.9,
                                    is_cointegrated=True, position=None, params=HEDGE_PARAMS)
    assert signal is None


# ---------------------------------------------------------------------
# Saída (D-03/D-04/D-05, Pitfall 2) — ordem correlation_breakdown-first
# ---------------------------------------------------------------------

_OPEN_POSITION = {"direction": -1, "entry_bar": 0}


def test_exit_correlation_breakdown_overrides_reversion_same_bar():
    """Correlação abaixo do limiar E z-score dentro do limiar de
    reversão simultaneamente -> o gatilho tem de ser
    'correlation_breakdown', nunca 'reversion' (Pitfall 2)."""
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=0.1, correlation=0.2,
                                    is_cointegrated=True, position=_OPEN_POSITION, params=HEDGE_PARAMS)
    assert signal["action"] == "close_hedge"
    assert signal["trigger"] == "correlation_breakdown"


def test_exit_correlation_breakdown_overrides_time_stop():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=3.0, correlation=0.2,
                                    is_cointegrated=True, position=_OPEN_POSITION, params=HEDGE_PARAMS,
                                    bars_held=100)
    assert signal["trigger"] == "correlation_breakdown"


def test_exit_reversion_when_correlation_healthy():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=0.1, correlation=0.8,
                                    is_cointegrated=True, position=_OPEN_POSITION, params=HEDGE_PARAMS)
    assert signal["trigger"] == "reversion"


def test_exit_time_stop_when_not_reverted_and_correlation_healthy():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=3.0, correlation=0.8,
                                    is_cointegrated=True, position=_OPEN_POSITION, params=HEDGE_PARAMS,
                                    bars_held=75)
    assert signal["trigger"] == "time_stop"


def test_no_exit_when_position_open_and_no_condition_met():
    signal = evaluate_hedge_signal("EURUSD", "GBPUSD", zscore=3.0, correlation=0.8,
                                    is_cointegrated=True, position=_OPEN_POSITION, params=HEDGE_PARAMS,
                                    bars_held=10)
    assert signal is None


# ---------------------------------------------------------------------
# HEDGE-03: recheck_cointegration — fail-closed
# ---------------------------------------------------------------------

def test_recheck_cointegration_true_for_stationary_spread():
    rng = np.random.default_rng(0)
    n = 1200
    b = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 100)
    a = 1.5 * b + pd.Series(rng.normal(0, 0.5, n))  # spread estacionário por construção
    is_coint, pvalue = recheck_cointegration(a, b, coint_window=1000, pvalue_threshold=0.05)
    assert is_coint is True
    assert pvalue < 0.05


def test_recheck_cointegration_false_for_independent_random_walks():
    rng = np.random.default_rng(0)  # verificado empiricamente: não-cointegrado na janela trailing de 1000
    n = 1200
    a = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 100)
    b = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 100)
    is_coint, pvalue = recheck_cointegration(a, b, coint_window=1000, pvalue_threshold=0.05)
    assert is_coint is False


def test_recheck_cointegration_fails_closed_on_exception():
    degenerate = pd.Series([1.0] * 50)  # série constante -> coint() deve levantar/produzir erro numérico
    is_coint, pvalue = recheck_cointegration(degenerate, degenerate, coint_window=50, pvalue_threshold=0.05)
    assert is_coint is False
    assert math.isnan(pvalue)


# ---------------------------------------------------------------------
# HEDGE-02: handoff para o motor de risco
# ---------------------------------------------------------------------

def test_propose_to_risk_engine_maps_fields_and_uses_resolve_kelly_inputs():
    hedge_proposal = {"pair_a": "EURUSD", "pair_b": "GBPUSD", "direction": 1}
    strategy_record = _strategy_record(win_rate=0.55, profit_factor=1.3, avg_win_r=1.5, avg_loss_r=-1.0,
                                        wf_passed=0, revalidated_on_real_data=0)
    decision = propose_to_risk_engine(
        hedge_proposal, strategy_record, entry_price=1.1000, stop_distance_price_units=0.0050,
        account_state=_healthy_account_state(), correlation_matrix={}, new_position_exposure_pct=0.01,
    )
    assert isinstance(decision, RiskDecision)
    assert decision.approved is True
    assert decision.sl_price == pytest.approx(1.1000 - 0.0050)


def test_propose_to_risk_engine_rejects_when_edge_non_positive():
    hedge_proposal = {"pair_a": "EURUSD", "pair_b": "GBPUSD", "direction": 1}
    strategy_record = _strategy_record(win_rate=0.3, profit_factor=1.0, avg_win_r=1.0, avg_loss_r=-1.0,
                                        wf_passed=0, revalidated_on_real_data=0)
    decision = propose_to_risk_engine(
        hedge_proposal, strategy_record, entry_price=1.1000, stop_distance_price_units=0.0050,
        account_state=_healthy_account_state(), correlation_matrix={},
    )
    assert decision.approved is False
    assert decision.reject_reason == "non_positive_edge"


def test_propose_to_risk_engine_handles_malformed_wf_fold_results_gracefully():
    hedge_proposal = {"pair_a": "EURUSD", "pair_b": "GBPUSD", "direction": 1}
    strategy_record = _strategy_record(wf_passed=1, revalidated_on_real_data=1,
                                        wf_fold_results="{not valid json")
    decision = propose_to_risk_engine(
        hedge_proposal, strategy_record, entry_price=1.1000, stop_distance_price_units=0.0050,
        account_state=_healthy_account_state(), correlation_matrix={},
    )
    assert decision.approved is False
    assert decision.reject_reason == "malformed_strategy_record"


def test_hedge_engine_has_no_self_sizing_or_risk_limits_gating():
    """Prova estrutural (não só revisão): hedge_engine.py nunca calcula
    o seu próprio tamanho de posição nem ramifica sobre campos numéricos
    de risk_limits — a única forma como este módulo toca em risco é
    instanciar RiskLimits() e entregá-la a evaluate_order (HEDGE-02)."""
    src = (Path(__file__).resolve().parent.parent / "src" / "hedge_engine.py").read_text(encoding="utf-8")
    forbidden_lot_patterns = ("size_lots =", "lots = equity", "* equity /")
    for pattern in forbidden_lot_patterns:
        assert pattern not in src, f"padrão de dimensionamento próprio encontrado: {pattern!r}"

    risk_limits_numeric_fields = (
        "max_pair_exposure_pct", "max_aggregate_exposure_pct", "daily_drawdown_pct",
        "weekly_drawdown_pct", "absolute_drawdown_pct", "kelly_fraction",
    )
    for field in risk_limits_numeric_fields:
        assert field not in src, f"hedge_engine.py não deve referenciar RiskLimits.{field} diretamente"


# ---------------------------------------------------------------------
# replay_feed / run_hedge_loop — integração
# ---------------------------------------------------------------------

def _write_feature_parquet(tmp_path: Path, symbol: str, closes: list[float]) -> str:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="5min")
    df = pd.DataFrame({"close": closes}, index=idx)
    path = tmp_path / f"features_{symbol}.parquet"
    df.to_parquet(path)
    return str(path)


def test_replay_feed_yields_aligned_bars(tmp_path):
    path_a = _write_feature_parquet(tmp_path, "EURUSD", [1.1, 1.2, 1.3])
    path_b = _write_feature_parquet(tmp_path, "GBPUSD", [1.3, 1.4, 1.5])
    steps = list(replay_feed({"EURUSD": path_a, "GBPUSD": path_b}))
    assert len(steps) == 3
    assert steps[0]["EURUSD"].close == 1.1
    assert steps[0]["GBPUSD"].close == 1.3
    assert isinstance(steps[0]["EURUSD"], Bar)


def test_run_hedge_loop_is_deterministic_across_identical_replays(tmp_path):
    rng = np.random.default_rng(3)
    n = 250
    b_vals = np.cumsum(rng.normal(0, 0.01, n)) + 1.30
    a_vals = 1.2 * b_vals + rng.normal(0, 0.002, n)
    path_a = _write_feature_parquet(tmp_path, "EURUSD", a_vals.tolist())
    path_b = _write_feature_parquet(tmp_path, "GBPUSD", b_vals.tolist())

    eligible = [_strategy_record(id="s1", pair_a="EURUSD", pair_b="GBPUSD",
                                  wf_passed=1, revalidated_on_real_data=1)]

    def stub_risk_evaluate_fn(hedge_proposal, strategy_record, entry_price, stop_distance, account_state, corr_matrix):
        return RiskDecision(approved=True, size_lots=0.01, sl_price=entry_price - 0.01, reject_reason=None)

    def run():
        feed = replay_feed({"EURUSD": path_a, "GBPUSD": path_b})
        return run_hedge_loop(feed, eligible, stub_risk_evaluate_fn,
                               beta_window=30, corr_window=30, recalc_every=10)

    events_1 = run()
    events_2 = run()
    assert [e["proposal"] for e in events_1] == [e["proposal"] for e in events_2]


def test_run_hedge_loop_rejected_proposal_does_not_open_position(tmp_path):
    rng = np.random.default_rng(5)
    n = 200
    b_vals = np.cumsum(rng.normal(0, 0.01, n)) + 1.30
    a_vals = 1.2 * b_vals + rng.normal(0, 0.002, n)
    path_a = _write_feature_parquet(tmp_path, "EURUSD", a_vals.tolist())
    path_b = _write_feature_parquet(tmp_path, "GBPUSD", b_vals.tolist())

    eligible = [_strategy_record(id="s1", pair_a="EURUSD", pair_b="GBPUSD",
                                  wf_passed=1, revalidated_on_real_data=1)]

    def always_reject(hedge_proposal, strategy_record, entry_price, stop_distance, account_state, corr_matrix):
        return RiskDecision(approved=False, size_lots=0.0, sl_price=0.0, reject_reason="kill_switch")

    feed = replay_feed({"EURUSD": path_a, "GBPUSD": path_b})
    events = run_hedge_loop(feed, eligible, always_reject, beta_window=30, corr_window=30, recalc_every=10)

    open_events = [e for e in events if e["proposal"]["action"] == "open_hedge"]
    assert open_events, "pré-condição do teste: pelo menos uma proposta de abertura devia ter sido gerada"
    assert all(e["risk_decision"].approved is False for e in open_events)


def test_run_hedge_loop_on_event_callback_fires_for_every_event(tmp_path):
    """on_event deve ser chamado exatamente uma vez por evento produzido,
    com o mesmo dict que acaba em `events`, sem alterar o valor devolvido."""
    rng = np.random.default_rng(3)
    n = 250
    b_vals = np.cumsum(rng.normal(0, 0.01, n)) + 1.30
    a_vals = 1.2 * b_vals + rng.normal(0, 0.002, n)
    path_a = _write_feature_parquet(tmp_path, "EURUSD", a_vals.tolist())
    path_b = _write_feature_parquet(tmp_path, "GBPUSD", b_vals.tolist())

    eligible = [_strategy_record(id="s1", pair_a="EURUSD", pair_b="GBPUSD",
                                  wf_passed=1, revalidated_on_real_data=1)]

    def stub_risk_evaluate_fn(hedge_proposal, strategy_record, entry_price, stop_distance, account_state, corr_matrix):
        return RiskDecision(approved=True, size_lots=0.01, sl_price=entry_price - 0.01, reject_reason=None)

    seen_via_callback = []
    feed = replay_feed({"EURUSD": path_a, "GBPUSD": path_b})
    events = run_hedge_loop(feed, eligible, stub_risk_evaluate_fn,
                             beta_window=30, corr_window=30, recalc_every=10,
                             on_event=seen_via_callback.append)

    assert events, "pré-condição do teste: o loop devia produzir pelo menos um evento"
    assert seen_via_callback == events


# ---------------------------------------------------------------------
# select_strategy_for_pair: policy="best_oos_profit_factor"
# ---------------------------------------------------------------------

def test_select_strategy_for_pair_best_oos_profit_factor_policy(caplog):
    def _with_wf(profit_factor: float) -> str:
        import json
        return json.dumps({"aggregate_stats": {"profit_factor": profit_factor}})

    eligible = [
        _strategy_record(id="low_pf", pair_a="EURUSD", pair_b="GBPUSD",
                          created_at="2026-06-01T00:00:00", wf_fold_results=_with_wf(1.3)),
        _strategy_record(id="high_pf", pair_a="EURUSD", pair_b="GBPUSD",
                          created_at="2026-01-01T00:00:00", wf_fold_results=_with_wf(4.5)),
    ]
    with caplog.at_level("WARNING"):
        chosen = select_strategy_for_pair(eligible, "EURUSD", "GBPUSD", policy="best_oos_profit_factor")
    # "high_pf" é mais antiga (created_at) mas tem profit_factor out-of-sample
    # maior — policy="best_oos_profit_factor" tem de vencer por isso, não
    # por recência (ao contrário do desempate default "most_recent").
    assert chosen["id"] == "high_pf"


def test_select_strategy_for_pair_best_oos_policy_prefers_data_over_missing():
    import json
    eligible = [
        _strategy_record(id="no_data", pair_a="EURUSD", pair_b="GBPUSD",
                          created_at="2026-06-01T00:00:00", wf_fold_results=None),
        _strategy_record(id="has_data", pair_a="EURUSD", pair_b="GBPUSD",
                          created_at="2026-01-01T00:00:00",
                          wf_fold_results=json.dumps({"aggregate_stats": {"profit_factor": 0.5}})),
    ]
    chosen = select_strategy_for_pair(eligible, "EURUSD", "GBPUSD", policy="best_oos_profit_factor")
    # "no_data" é mais recente mas não tem profit_factor out-of-sample
    # nenhum utilizável — nunca deve vencer um registo com dados reais,
    # mesmo que o profit_factor desse registo seja baixo (0.5).
    assert chosen["id"] == "has_data"


def test_select_strategy_for_pair_default_policy_unchanged():
    """policy default continua "most_recent" — não muda o comportamento
    já fixado por test_select_strategy_for_pair_tie_break_most_recent."""
    eligible = [
        _strategy_record(id="older", pair_a="EURUSD", pair_b="GBPUSD", created_at="2026-01-01T00:00:00"),
        _strategy_record(id="newer", pair_a="EURUSD", pair_b="GBPUSD", created_at="2026-06-01T00:00:00"),
    ]
    assert select_strategy_for_pair(eligible, "EURUSD", "GBPUSD")["id"] == "newer"


# ---------------------------------------------------------------------
# run_hedge_loop: fixação de estratégia por posição aberta + recarga
# ---------------------------------------------------------------------

def test_run_hedge_loop_pins_strategy_across_reload_and_only_new_entries_use_new_strategy(tmp_path):
    """Uma posição aberta ANTES de uma recarga tem de FECHAR com a
    estratégia antiga (nunca troca a meio do trade, CLAUDE.md regra 7);
    só a entrada seguinte (posição já fechada) é que passa a usar a
    estratégia nova."""
    rng = np.random.default_rng(3)
    n = 250
    b_vals = np.cumsum(rng.normal(0, 0.01, n)) + 1.30
    a_vals = 1.2 * b_vals + rng.normal(0, 0.002, n)
    path_a = _write_feature_parquet(tmp_path, "EURUSD", a_vals.tolist())
    path_b = _write_feature_parquet(tmp_path, "GBPUSD", b_vals.tolist())

    # Com esta seed/params (confirmado empiricamente): abre no bar 40,
    # fecha no bar 51 — uma recarga a cada 45 barras dispara no bar 45,
    # a MEIO desse trade.
    old_record = _strategy_record(id="old", pair_a="EURUSD", pair_b="GBPUSD",
                                   wf_passed=1, revalidated_on_real_data=1)
    new_record = _strategy_record(id="new", pair_a="EURUSD", pair_b="GBPUSD",
                                   wf_passed=1, revalidated_on_real_data=1,
                                   params={**old_record["params"], "exit_threshold": 0.5})

    strategy_ids_seen_at_open = []

    def stub_risk_evaluate_fn(hedge_proposal, strategy_record, entry_price, stop_distance, account_state, corr_matrix):
        strategy_ids_seen_at_open.append(strategy_record["id"])
        return RiskDecision(approved=True, size_lots=0.01, sl_price=entry_price - 0.01, reject_reason=None)

    feed = replay_feed({"EURUSD": path_a, "GBPUSD": path_b})
    events = run_hedge_loop(
        feed, [old_record], stub_risk_evaluate_fn,
        beta_window=30, corr_window=30, recalc_every=10,
        reload_eligible_fn=lambda: [new_record], reload_every_bars=45,
    )

    opens = [e for e in events if e["proposal"]["action"] == "open_hedge"]
    closes = [e for e in events if e["proposal"]["action"] == "close_hedge"]
    # Confirmado empiricamente com esta seed/params: abre no bar 32 (fecha
    # 33, trade curto, antes de qualquer recarga), abre de novo no bar 40
    # (fecha 51 — a recarga no bar 45 acontece A MEIO deste segundo trade),
    # e abre de novo no bar 60 (já depois do fecho do segundo trade).
    assert len(opens) >= 3, "pré-condição: precisa de pelo menos 3 aberturas para provar a troca"
    assert opens[1]["bar_index"] == 40 and closes[1]["bar_index"] == 51
    assert opens[2]["bar_index"] == 60

    # O SEGUNDO trade abriu só com "old" disponível, e a recarga (bar 45)
    # aconteceu a meio dele — tem de fechar com "old", nunca "new".
    assert closes[1]["strategy_id"] == "old"
    assert strategy_ids_seen_at_open[1] == "old"

    # Depois do fecho do segundo trade, eligible_strategies já é [new] —
    # a terceira entrada tem de resolver "new".
    assert strategy_ids_seen_at_open[2] == "new"


def test_run_hedge_loop_reload_extends_pairs_without_resetting_existing_state(tmp_path):
    """Uma recarga que introduz um par NOVO nunca deve tocar no estado
    (posições, contadores) de um par já a ser seguido."""
    rng = np.random.default_rng(5)
    n = 120
    b_vals = np.cumsum(rng.normal(0, 0.01, n)) + 1.30
    a_vals = 1.2 * b_vals + rng.normal(0, 0.002, n)
    c_vals = np.cumsum(rng.normal(0, 0.01, n)) + 0.90
    path_a = _write_feature_parquet(tmp_path, "EURUSD", a_vals.tolist())
    path_b = _write_feature_parquet(tmp_path, "GBPUSD", b_vals.tolist())
    path_c = _write_feature_parquet(tmp_path, "AUDUSD", c_vals.tolist())

    original = _strategy_record(id="orig", pair_a="EURUSD", pair_b="GBPUSD",
                                 wf_passed=1, revalidated_on_real_data=1)
    new_pair_record = _strategy_record(id="new-pair", pair_a="EURUSD", pair_b="AUDUSD",
                                        wf_passed=1, revalidated_on_real_data=1)

    def stub_risk_evaluate_fn(hedge_proposal, strategy_record, entry_price, stop_distance, account_state, corr_matrix):
        return RiskDecision(approved=True, size_lots=0.01, sl_price=entry_price - 0.01, reject_reason=None)

    feed = replay_feed({"EURUSD": path_a, "GBPUSD": path_b, "AUDUSD": path_c})
    events = run_hedge_loop(
        feed, [original], stub_risk_evaluate_fn,
        beta_window=30, corr_window=30, recalc_every=10,
        reload_eligible_fn=lambda: [original, new_pair_record], reload_every_bars=20,
    )
    # Não deve rebentar, e o par original continua a produzir eventos
    # normalmente depois da recarga introduzir o par novo.
    eurusd_gbpusd_events = [e for e in events if e["pair_a"] == "EURUSD" and e["pair_b"] == "GBPUSD"]
    assert eurusd_gbpusd_events
