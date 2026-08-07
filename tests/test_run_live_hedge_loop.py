"""
test_run_live_hedge_loop.py
==============================
Prova que `_heartbeat_loop` nunca morre por causa de uma falha
transitória de escrita (ex.: Common\\Files temporariamente inacessível
durante um reinício do terminal MT5) — bug real encontrado em produção
2026-08-04: sem o try/except, uma exceção não apanhada matava só a
thread do heartbeat silenciosamente (o processo principal continuava
"vivo" em tasklist), deixando o EA sem sinal de vida fresco sem nenhum
aviso visível.
"""

from __future__ import annotations

import threading
import time

import scripts.run_live_hedge_loop as rlhl


def test_heartbeat_loop_survives_transient_write_failure(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _flaky_write(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Common\\Files temporariamente inacessível (simulado)")

    monkeypatch.setattr(rlhl, "write_heartbeat", _flaky_write)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=rlhl._heartbeat_loop, args=(str(tmp_path / "heartbeat.flag"), 0.05, stop_event), daemon=True,
    )
    thread.start()
    time.sleep(0.3)
    stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive(), "a thread devia ter terminado limpo ao stop_event.set()"
    assert calls["n"] >= 3, "a thread devia continuar a chamar write_heartbeat depois da primeira falha"


def test_heartbeat_loop_stops_promptly_on_stop_event(tmp_path):
    stop_event = threading.Event()
    thread = threading.Thread(
        target=rlhl._heartbeat_loop, args=(str(tmp_path / "heartbeat.flag"), 0.05, stop_event), daemon=True,
    )
    thread.start()
    time.sleep(0.1)
    stop_event.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
