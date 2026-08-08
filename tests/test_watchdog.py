"""
test_watchdog.py
===================
Prova que `scripts/watchdog.py` reinicia sozinho processos "desejados"
(arrancados e nunca explicitamente parados) que morrem, nunca mexe em
processos já parados pelo utilizador, e nunca levanta mesmo perante
falhas ao tentar reiniciar.

Usa comandos triviais (sys.executable -c "...") — sem MT5, sem scripts
reais do projeto. Cada teste limpa explicitamente qualquer processo que
tenha ficado vivo (finally), para nunca deixar "zombies" depois da
suite correr.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

from scripts.watchdog import run_forever
from src.process_control import get_pid, is_running, start_process, stop_process

_SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(10)"]
_EXIT_IMMEDIATELY_CMD = [sys.executable, "-c", "pass"]

_log = logging.getLogger("test_watchdog")


def test_watchdog_restarts_a_desired_process_that_died(tmp_path):
    pid_dir = str(tmp_path / "pids")
    original_pid = start_process("w1", _EXIT_IMMEDIATELY_CMD, pid_dir=pid_dir)
    # Dá tempo ao processo (que só faz "pass") para terminar sozinho,
    # antes do vigilante correr — sem isto, o teste seria instável
    # (dependeria de quão rápido o SO agenda o processo).
    time.sleep(1.0)
    assert not is_running("w1", pid_dir=pid_dir), "pré-condição: o processo tem de já ter morrido"

    try:
        run_forever(check_seconds=0.01, pid_dir=pid_dir, log=_log, max_checks=1)
        assert is_running("w1", pid_dir=pid_dir), "o vigilante devia ter reiniciado 'w1'"
        assert get_pid("w1", pid_dir=pid_dir) != original_pid
    finally:
        stop_process("w1", pid_dir=pid_dir)


def test_watchdog_leaves_alive_processes_untouched(tmp_path):
    pid_dir = str(tmp_path / "pids")
    original_pid = start_process("w2", _SLEEP_CMD, pid_dir=pid_dir)
    try:
        run_forever(check_seconds=0.01, pid_dir=pid_dir, log=_log, max_checks=2)
        assert is_running("w2", pid_dir=pid_dir)
        assert get_pid("w2", pid_dir=pid_dir) == original_pid, (
            "um processo já vivo nunca deve ser reiniciado — só mortos"
        )
    finally:
        stop_process("w2", pid_dir=pid_dir)


def test_watchdog_never_restarts_a_process_the_user_explicitly_stopped(tmp_path):
    pid_dir = str(tmp_path / "pids")
    start_process("w3", _EXIT_IMMEDIATELY_CMD, pid_dir=pid_dir)
    stop_process("w3", pid_dir=pid_dir)  # limpa o marcador "desejado"

    run_forever(check_seconds=0.01, pid_dir=pid_dir, log=_log, max_checks=1)

    assert not is_running("w3", pid_dir=pid_dir), (
        "clicar em Parar tem de ser definitivo — o vigilante nunca deve reviver isto sozinho"
    )


def test_watchdog_never_raises_when_restart_command_is_broken(tmp_path):
    pid_dir = str(tmp_path / "pids")
    # Arranque INICIAL com um comando válido (para start_process não
    # rebentar na preparação do teste) — depois corrompe o .cmd.json
    # registado para simular o REINÍCIO em si a falhar, que é o caminho
    # que este teste quer exercitar.
    start_process("w4", _EXIT_IMMEDIATELY_CMD, pid_dir=pid_dir)
    time.sleep(1.0)
    assert not is_running("w4", pid_dir=pid_dir), "pré-condição: o processo tem de já ter morrido"

    cmd_file = os.path.join(pid_dir, "w4.cmd.json")
    with open(cmd_file, "w", encoding="utf-8") as f:
        json.dump({"cmd": ["este-executavel-nao-existe-de-todo.exe"], "cwd": None}, f)

    # Nunca deve levantar, mesmo que a tentativa de reinício em si falhe
    # (executável inexistente).
    run_forever(check_seconds=0.01, pid_dir=pid_dir, log=_log, max_checks=2)


def test_watchdog_does_nothing_when_no_processes_are_desired(tmp_path):
    pid_dir = str(tmp_path / "pids")
    # Nunca deve levantar mesmo sem nenhum processo alguma vez arrancado.
    run_forever(check_seconds=0.01, pid_dir=pid_dir, log=_log, max_checks=1)
