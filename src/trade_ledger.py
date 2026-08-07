"""
trade_ledger.py
=================
Regista, em SQLite, cada operação de hedge REAL (ou em conta demo)
aberta/fechada pelo loop ao vivo (`scripts/run_live_hedge_loop.py`),
para que `divergence_monitor.py` (Camada 3.5, monitor de divergência)
tenha um histórico ao vivo contra o qual comparar o desempenho
validado no laboratório/walk-forward.

Distinto de `strategy_registry.py` (que guarda os TESTES do
laboratório, offline) — este módulo guarda o que REALMENTE aconteceu
na conta, uma linha por operação real.

PnL em "R" — mesma unidade do resto do projeto (múltiplos da distância
de stop na entrada). SIMPLIFICAÇÃO DOCUMENTADA: `pnl_r` aqui é um proxy
de UMA SÓ perna (perna A), não a contabilidade exata das duas pernas
do hedge — suficiente para o monitor de divergência detetar uma
tendência de "isto está a correr mal", não para reconciliação
financeira exata da conta (essa vem sempre do próprio extrato da
corretora). Documentado aqui para nunca ser confundido com contabilidade
oficial.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_trades (
    id TEXT PRIMARY KEY,
    pair_a TEXT,
    pair_b TEXT,
    direction INTEGER,
    entry_timestamp TEXT,
    entry_price_a REAL,
    stop_distance_price_units REAL,
    exit_timestamp TEXT,
    exit_price_a REAL,
    exit_reason TEXT,
    pnl_r REAL,
    status TEXT
);
"""


def init_ledger(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()


def record_trade_open(db_path: str, pair_a: str, pair_b: str, direction: int,
                       entry_price_a: float, stop_distance_price_units: float) -> str:
    """Regista a abertura de uma operação de hedge ao vivo. Devolve o
    `id` gerado (UUID), útil para correlacionar logs, mas
    `record_trade_close` encontra a operação por (pair_a, pair_b,
    status="open") — nunca precisa do id de volta do chamador."""
    init_ledger(db_path)
    trade_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO live_trades (id, pair_a, pair_b, direction, entry_timestamp,
                                  entry_price_a, stop_distance_price_units, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (trade_id, pair_a, pair_b, direction, datetime.now(timezone.utc).isoformat(),
         entry_price_a, stop_distance_price_units),
    )
    conn.commit()
    conn.close()
    return trade_id


def record_trade_close(db_path: str, pair_a: str, pair_b: str, exit_price_a: float,
                        exit_reason: str) -> float | None:
    """Fecha a operação ABERTA mais recente para (pair_a, pair_b) — mesma
    invariante de "só uma posição aberta por par de cada vez" já
    imposta por `hedge_engine.py`/`ScalpingEA.mq5` (nunca deveria haver
    ambiguidade sobre qual linha fechar). Calcula `pnl_r` como
    `((exit_price_a - entry_price_a) / stop_distance_price_units) *
    direction` — proxy de perna única, ver docstring do módulo.

    devolve:
        float | None - pnl_r calculado, ou None se não havia nenhuma
            operação aberta para este par (aviso do chamador, não erro
            fatal — pode acontecer se o ledger foi limpo/reiniciado a
            meio de uma posição já aberta)
    """
    init_ledger(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
        SELECT id, direction, entry_price_a, stop_distance_price_units FROM live_trades
        WHERE pair_a = ? AND pair_b = ? AND status = 'open'
        ORDER BY entry_timestamp DESC LIMIT 1
        """,
        (pair_a, pair_b),
    )
    row = cur.fetchone()
    if row is None:
        conn.close()
        return None

    trade_id, direction, entry_price_a, stop_distance = row
    pnl_r = ((exit_price_a - entry_price_a) / stop_distance) * direction if stop_distance else 0.0

    conn.execute(
        """
        UPDATE live_trades
        SET exit_timestamp = ?, exit_price_a = ?, exit_reason = ?, pnl_r = ?, status = 'closed'
        WHERE id = ?
        """,
        (datetime.now(timezone.utc).isoformat(), exit_price_a, exit_reason, pnl_r, trade_id),
    )
    conn.commit()
    conn.close()
    return pnl_r


def list_all_trades(db_path: str, limit: int | None = None) -> list[dict]:
    """Devolve TODAS as operações (abertas E fechadas, todos os pares),
    mais recente primeiro por `entry_timestamp` — usado pela UI
    (`dashboard.py`, painel "Operações") para mostrar o que está mesmo a
    acontecer na conta ao vivo. Distinto de `list_closed_trades()`
    (filtra por par exato e só operações já fechadas — usado pelo
    monitor de divergência, não pela UI)."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    query = "SELECT * FROM live_trades ORDER BY entry_timestamp DESC"
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    cur = conn.execute(query)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def list_closed_trades(db_path: str, pair_a: str, pair_b: str, limit: int | None = None) -> list[dict]:
    """Devolve as operações FECHADAS para (pair_a, pair_b), mais
    recente primeiro. `limit` (se fornecido) restringe a uma janela
    rolante recente — ver `divergence_monitor.compute_live_stats`."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    query = """
        SELECT * FROM live_trades WHERE pair_a = ? AND pair_b = ? AND status = 'closed'
        ORDER BY exit_timestamp DESC
    """
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    cur = conn.execute(query, (pair_a, pair_b))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows
