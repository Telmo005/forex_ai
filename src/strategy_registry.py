"""
strategy_registry.py
=====================
Camada de persistência para o laboratório de estratégias. Cada estratégia
gerada e testada (ver strategy_generator.py) fica guardada aqui, com
todos os parâmetros, estatísticas e o histórico de trades — é isto que
o dashboard.py lê para mostrar a UI de validação.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    pair_a TEXT,
    pair_b TEXT,
    params TEXT,
    status TEXT,
    fail_reasons TEXT,
    generation INTEGER,
    parent_id TEXT,
    total_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    sharpe_per_trade REAL,
    total_return_r REAL,
    max_drawdown_r REAL,
    avg_hold_bars REAL,
    avg_win_r REAL,
    avg_loss_r REAL,
    bars_tested INTEGER,
    trades TEXT
);
"""


def init_db(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()


def save_strategy(db_path: str, record: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO strategies (
            id, created_at, pair_a, pair_b, params, status, fail_reasons,
            generation, parent_id, total_trades, win_rate, profit_factor,
            sharpe_per_trade, total_return_r, max_drawdown_r, avg_hold_bars,
            avg_win_r, avg_loss_r, bars_tested, trades
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record["id"], record["created_at"], record["pair_a"], record["pair_b"],
            json.dumps(record["params"]), record["status"], json.dumps(record["fail_reasons"]),
            record["generation"], record["parent_id"], record["total_trades"], record["win_rate"],
            record["profit_factor"], record["sharpe_per_trade"], record["total_return_r"],
            record["max_drawdown_r"], record["avg_hold_bars"], record["avg_win_r"],
            record["avg_loss_r"], record["bars_tested"], json.dumps(record["trades"]),
        ),
    )
    conn.commit()
    conn.close()


def list_strategies(db_path: str, status: str | None = None) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    query = "SELECT * FROM strategies"
    params = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    df = pd.read_sql(query, conn, params=params)
    conn.close()
    return df


def get_strategy(db_path: str, strategy_id: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    conn.close()
    if row is None:
        return None
    return dict(zip(cols, row))
