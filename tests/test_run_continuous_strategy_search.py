"""
test_run_continuous_strategy_search.py
=========================================
Prova a lógica de `scripts.run_continuous_strategy_search._maybe_refresh_mt5_data`
(quando é que decide chamar o MT5 sozinho, opt-in via --auto-refresh-mt5) —
usa um `run_pipeline_fn` falso injetado (nunca chama o MT5 real/MetaTrader5,
que pode nem estar instalado neste ambiente de testes).
"""

from __future__ import annotations

import os
import time

from scripts.run_continuous_strategy_search import _maybe_refresh_mt5_data


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
