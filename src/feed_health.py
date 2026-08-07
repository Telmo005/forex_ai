"""
feed_health.py
================
Regista e lê o estado de saúde do feed de dados ao vivo
(`hedge_engine.live_feed`) num ficheiro JSON simples, para que
`dashboard.py` mostre um alerta visual quando o feed está preso a
tentar reconectar ao MT5.

Deliberadamente SEM Telegram/email — pedido explícito do utilizador foi
"um simples log ou alerta na tela já é suficiente", ao contrário de
`src/alerts.py` (drawdown/kill-switch), que continua a ser o único
canal externo. Este módulo nunca decide nada (nunca para o loop, nunca
força um kill-switch) — só regista o estado mais recente para leitura
externa.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DEFAULT_HEALTH_PATH = os.path.join("output", "feed_health.json")


def write_feed_health(status: str, consecutive_failures: int, last_error: str | None = None,
                       path: str = DEFAULT_HEALTH_PATH) -> None:
    """status esperado: "ok" | "degraded" (a tentar reconectar, sem
    limite de tentativas — ver hedge_engine.live_feed)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "status": status,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def read_feed_health(path: str = DEFAULT_HEALTH_PATH) -> dict | None:
    """Devolve None se o ficheiro não existir ainda (loop ao vivo nunca
    correu) ou estiver corrompido/ilegível — nunca levanta exceção,
    dashboard.py trata None como "sem informação", nunca como erro."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
