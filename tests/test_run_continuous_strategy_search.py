"""
test_run_continuous_strategy_search.py
=========================================
Prova a lógica de `scripts.run_continuous_strategy_search._maybe_refresh_mt5_data`
(quando é que decide chamar o MT5 sozinho, opt-in via --auto-refresh-mt5) —
usa um `run_pipeline_fn` falso injetado (nunca chama o MT5 real/MetaTrader5,
que pode nem estar instalado neste ambiente de testes).
"""

from __future__ import annotations

import logging
import os
import time

import numpy as np
import pandas as pd

from scripts.run_continuous_strategy_search import _maybe_refresh_mt5_data, _refine_all_champions
from src.strategy_registry import init_db, save_strategy, save_walk_forward_result


def test_refreshes_when_hedge_csv_missing(tmp_path):
    calls = []
    _maybe_refresh_mt5_data(
        str(tmp_path), refresh_every_hours=1.0,
        run_pipeline_fn=lambda cfg, mode, creds: calls.append((mode, creds)),
    )
    assert len(calls) == 1
    assert calls[0][0] == "mt5"


def test_skips_refresh_when_hedge_csv_is_fresh(tmp_path):
    hedge_csv = tmp_path / "hedge_candidates.csv"
    hedge_csv.write_text("pair_a,pair_b\n")

    calls = []
    _maybe_refresh_mt5_data(
        str(tmp_path), refresh_every_hours=1.0,
        run_pipeline_fn=lambda cfg, mode, creds: calls.append((mode, creds)),
    )
    assert calls == []


def test_refreshes_when_hedge_csv_is_stale(tmp_path):
    hedge_csv = tmp_path / "hedge_candidates.csv"
    hedge_csv.write_text("pair_a,pair_b\n")
    stale_time = time.time() - (2 * 3600)  # 2h atrás
    os.utime(hedge_csv, (stale_time, stale_time))

    calls = []
    _maybe_refresh_mt5_data(
        str(tmp_path), refresh_every_hours=1.0,
        run_pipeline_fn=lambda cfg, mode, creds: calls.append((mode, creds)),
    )
    assert len(calls) == 1


def test_refresh_failure_is_swallowed_never_raises(tmp_path):
    def _boom(cfg, mode, creds):
        raise RuntimeError("MT5 fechado")

    # Nunca deve levantar, mesmo que o pipeline falhe (terminal fechado, etc.)
    _maybe_refresh_mt5_data(str(tmp_path), refresh_every_hours=1.0, run_pipeline_fn=_boom)


# ---------------------------------------------------------------------
# _refine_all_champions: refina cada estratégia já campeã, semeada a
# partir dos seus próprios parâmetros (pedido do utilizador 2026-08)
# ---------------------------------------------------------------------

CHAMPION_PARAMS = {
    "entry_threshold": 2.0, "exit_threshold": 0.3, "min_correlation": 0.5,
    "max_hold_bars": 80, "beta_window": 300, "corr_window": 150, "recalc_every": 50,
}


def _write_synthetic_features(output_dir: str, symbol: str, n: int = 600, seed: int = 1) -> None:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 0.0005, n)) + 1.1000
    pd.DataFrame({"close": prices}).to_parquet(os.path.join(output_dir, f"features_{symbol}.parquet"))


def _seed_eligible_strategy(db_path: str, pair_a: str, pair_b: str, strategy_id: str = "champ0001") -> None:
    """Insere uma estratégia já elegível diretamente na base de dados —
    para testar `_refine_all_champions` sem depender de uma busca real ter
    organicamente encontrado e validado algo primeiro (esse caminho já é
    coberto pelos testes de `run_evolutionary_lab`/`refine_champion`)."""
    init_db(db_path)
    record = {
        "id": strategy_id, "created_at": "2026-08-08T00:00:00+00:00",
        "pair_a": pair_a, "pair_b": pair_b, "params": CHAMPION_PARAMS,
        "status": "passed", "fail_reasons": [], "generation": 0, "parent_id": None,
        "trades": [], "cost_model_version": "v1", "strategy_type": "zscore",
        "total_trades": 25, "win_rate": 0.6, "profit_factor": 1.8, "sharpe_per_trade": 0.3,
        "total_return_r": 12.0, "max_drawdown_r": 4.0, "avg_hold_bars": 40.0,
        "avg_win_r": 1.0, "avg_loss_r": -0.6, "bars_tested": 600,
    }
    save_strategy(db_path, record)
    save_walk_forward_result(
        db_path, strategy_id, wf_passed=True, wf_fold_results=[], revalidated_on_real_data=True,
    )


def test_refine_all_champions_refines_pair_with_eligible_strategy(tmp_path):
    output_dir = str(tmp_path)
    db_path = os.path.join(output_dir, "lab.db")
    journal_path = os.path.join(output_dir, "journal.md")

    _seed_eligible_strategy(db_path, "EURUSD", "AUDUSD")
    _write_synthetic_features(output_dir, "EURUSD", seed=1)
    _write_synthetic_features(output_dir, "AUDUSD", seed=2)

    _refine_all_champions(
        output_dir, db_path, journal_path,
        population_size=4, max_generations=1, patience=1, seed=1, log=logging.getLogger("test"),
    )

    from src.strategy_registry import list_strategies
    df = list_strategies(db_path)
    # 1 estratégia semeada + 4 candidatos testados pelo refinamento (o
    # próprio campeão + 3 vizinhos, população=4, 1 geração).
    assert len(df) == 1 + 4


def test_refine_all_champions_skips_gracefully_when_no_eligible_strategies(tmp_path):
    output_dir = str(tmp_path)
    db_path = os.path.join(output_dir, "lab.db")
    init_db(db_path)

    # Nunca deve levantar, mesmo sem nenhuma estratégia elegível.
    _refine_all_champions(
        output_dir, db_path, os.path.join(output_dir, "journal.md"),
        population_size=4, max_generations=1, patience=1, seed=1, log=logging.getLogger("test"),
    )

    from src.strategy_registry import list_strategies
    assert list_strategies(db_path).empty


def test_refine_all_champions_skips_pair_with_missing_price_data(tmp_path):
    output_dir = str(tmp_path)
    db_path = os.path.join(output_dir, "lab.db")

    _seed_eligible_strategy(db_path, "EURUSD", "AUDUSD")
    # Deliberadamente NÃO escreve os parquets — simula dados em falta.

    # Nunca deve levantar — regista e salta este par, sem afetar outros.
    _refine_all_champions(
        output_dir, db_path, os.path.join(output_dir, "journal.md"),
        population_size=4, max_generations=1, patience=1, seed=1, log=logging.getLogger("test"),
    )

    from src.strategy_registry import list_strategies
    df = list_strategies(db_path)
    # Só a estratégia semeada original — nenhum candidato novo foi testado.
    assert len(df) == 1
