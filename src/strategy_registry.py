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


def migrate_add_cost_columns(db_path: str) -> None:
    """Migração aditiva e idempotente: adiciona `cost_model_version` a uma
    base de dados já existente (criada antes do plan 01-02). Usa
    PRAGMA table_info() para verificar se a coluna já existe antes de
    correr ALTER TABLE, porque sqlite3 não suporta `ADD COLUMN IF NOT
    EXISTS` — mesma técnica documentada em 01-RESEARCH.md "Registry
    Schema Migration". Chamada a partir de init_db() para que qualquer
    chamador existente (ex. strategy_generator.py) receba a migração
    automaticamente, sem precisar de um script de migração separado.

    NOTA: as colunas de walk-forward (wf_passed, wf_fold_results,
    revalidated_on_real_data) NÃO são adicionadas aqui — pertencem ao
    plan 01-03.
    """
    conn = sqlite3.connect(db_path)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
    if "cost_model_version" not in existing_cols:
        conn.execute("ALTER TABLE strategies ADD COLUMN cost_model_version TEXT")
    conn.commit()
    conn.close()


def migrate_add_walk_forward_columns(db_path: str) -> None:
    """Migração aditiva e idempotente: adiciona as colunas de walk-forward
    (`wf_passed`, `wf_fold_results`, `revalidated_on_real_data`) a uma base
    de dados já existente (criada antes do plan 01-03). Mesma técnica de
    `migrate_add_cost_columns` (PRAGMA table_info() + ALTER TABLE condicional,
    porque sqlite3 não suporta `ADD COLUMN IF NOT EXISTS`) — ver
    01-RESEARCH.md "Registry Schema Migration". Chamada a partir de
    init_db() logo a seguir à migração de custos, para que qualquer
    chamador existente (ex. strategy_generator.py) receba as novas colunas
    automaticamente.

    `wf_passed` e `revalidated_on_real_data` são armazenados como
    INTEGER 0/1 (sqlite não tem tipo booleano nativo). `wf_fold_results` é
    TEXT (JSON), mesma convenção de serialização da coluna `trades`
    existente.

    CRÍTICO (regra 7 / VALID-01): `revalidated_on_real_data` é uma flag
    DISTINTA de `wf_passed` — uma estratégia pode passar walk-forward em
    dados sintéticos (wf_passed=1, revalidated_on_real_data=0); só uma
    corrida confirmada em dados reais (--mode mt5, plan 01-04) marca
    revalidated_on_real_data=1. Ver save_walk_forward_result() abaixo.
    """
    conn = sqlite3.connect(db_path)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
    new_cols = {
        "wf_passed": "INTEGER",
        "wf_fold_results": "TEXT",
        "revalidated_on_real_data": "INTEGER",
    }
    for col, coltype in new_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE strategies ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()


def init_db(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()
    migrate_add_cost_columns(db_path)
    migrate_add_walk_forward_columns(db_path)


def save_strategy(db_path: str, record: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO strategies (
            id, created_at, pair_a, pair_b, params, status, fail_reasons,
            generation, parent_id, total_trades, win_rate, profit_factor,
            sharpe_per_trade, total_return_r, max_drawdown_r, avg_hold_bars,
            avg_win_r, avg_loss_r, bars_tested, trades, cost_model_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record["id"], record["created_at"], record["pair_a"], record["pair_b"],
            json.dumps(record["params"]), record["status"], json.dumps(record["fail_reasons"]),
            record["generation"], record["parent_id"], record["total_trades"], record["win_rate"],
            record["profit_factor"], record["sharpe_per_trade"], record["total_return_r"],
            record["max_drawdown_r"], record["avg_hold_bars"], record["avg_win_r"],
            record["avg_loss_r"], record["bars_tested"], json.dumps(record["trades"]),
            record["cost_model_version"],
        ),
    )
    conn.commit()
    conn.close()


def save_walk_forward_result(db_path: str, strategy_id: str, wf_passed: bool,
                              wf_fold_results: list[dict],
                              revalidated_on_real_data: bool) -> None:
    """Persiste o veredito de walk_forward_validate() [backtest_engine.py,
    plan 01-03] para uma estratégia JÁ existente na tabela — faz UPDATE, não
    INSERT, porque a estratégia já foi guardada por save_strategy() quando o
    laboratório a testou pela primeira vez; esta função só anexa o
    resultado da revalidação walk-forward a esse registo.

    `wf_passed` e `revalidated_on_real_data` são gravados como INTEGER 0/1
    (sqlite não tem tipo booleano nativo). `wf_fold_results` é serializado
    em JSON, mesma convenção da coluna `trades` existente.

    IMPORTANTE (regra 7 / VALID-01): `revalidated_on_real_data` é uma flag
    DISTINTA de `wf_passed`, nunca derivada dela. Uma estratégia pode passar
    walk-forward em dados SINTÉTICOS (wf_passed=True) sem que isso
    signifique revalidação em dados reais — só chamar este helper com
    `revalidated_on_real_data=True` depois de uma corrida confirmada contra
    dados reais (`data_pipeline.py --mode mt5`, não `--mode synth`), nunca
    inferir isto a partir do valor de wf_passed. O gate de produção futuro
    (Phase 3, hedge_engine.py) deve exigir AMBAS as flags verdadeiras, não
    só wf_passed.

    levanta:
        ValueError - se `strategy_id` não corresponder a nenhuma linha
            existente (IN-01, 01-REVIEW.md). Sem este check, um `UPDATE ...
            WHERE id = ?` que não afeta nenhuma linha (ex.: registry
            resetado, ou a linha foi apagada entre list_strategies() e esta
            chamada) devolvia sucesso silencioso — o chamador (
            revalidate_walk_forward.py) registava "estratégia revalidada"
            mesmo sem nada ter sido persistido de facto.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
        UPDATE strategies
        SET wf_passed = ?, wf_fold_results = ?, revalidated_on_real_data = ?
        WHERE id = ?
        """,
        (
            int(bool(wf_passed)),
            json.dumps(wf_fold_results),
            int(bool(revalidated_on_real_data)),
            strategy_id,
        ),
    )
    conn.commit()
    rowcount = cur.rowcount
    conn.close()
    if rowcount == 0:
        raise ValueError(
            f"save_walk_forward_result: nenhuma estratégia com id={strategy_id!r} encontrada "
            "— nada foi persistido."
        )


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
