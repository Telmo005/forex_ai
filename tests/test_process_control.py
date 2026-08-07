"""
test_process_control.py
==========================
Prova que `src/process_control.py` gere processos em segundo plano por
ficheiro PID corretamente: arranca, deteta que está vivo, para, deteta
que já não está vivo, nunca deixa arrancar dois processos com o mesmo
nome ao mesmo tempo, e limpa ficheiros PID órfãos (processo já morto).

Usa comandos triviais de curta duração (sys.executable -c "...") — sem
MT5, sem scripts do projeto, só para provar a gestão do processo em si.
Cada teste para explicitamente o processo que arrancou (finally), para
nunca deixar processos "zombie" depois da suite correr.
"""

from __future__ import annotations

import os
import sys

import pytest

from src.process_control import get_pid, is_running, start_process, stop_process

_SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(10)"]


def test_start_process_then_is_running_true(tmp_path):
    pid_dir = str(tmp_path / "pids")
    pid = start_process("t1", _SLEEP_CMD, pid_dir=pid_dir)
    try:
        assert pid > 0
        assert is_running("t1", pid_dir=pid_dir)
        assert get_pid("t1", pid_dir=pid_dir) == pid
    finally:
        stop_process("t1", pid_dir=pid_dir)


def test_stop_process_makes_is_running_false(tmp_path):
    pid_dir = str(tmp_path / "pids")
    start_process("t2", _SLEEP_CMD, pid_dir=pid_dir)
    assert is_running("t2", pid_dir=pid_dir)
    assert stop_process("t2", pid_dir=pid_dir) is True
    assert is_running("t2", pid_dir=pid_dir) is False


def test_stop_process_on_nothing_running_returns_false(tmp_path):
    pid_dir = str(tmp_path / "pids")
    assert stop_process("nao-existe", pid_dir=pid_dir) is False


def test_start_process_raises_if_already_running(tmp_path):
    pid_dir = str(tmp_path / "pids")
    start_process("t3", _SLEEP_CMD, pid_dir=pid_dir)
    try:
        with pytest.raises(RuntimeError):
            start_process("t3", _SLEEP_CMD, pid_dir=pid_dir)
    finally:
        stop_process("t3", pid_dir=pid_dir)


def test_is_running_false_for_unknown_name(tmp_path):
    pid_dir = str(tmp_path / "pids")
    assert is_running("ghost", pid_dir=pid_dir) is False


def test_is_running_cleans_up_orphaned_pid_file_for_dead_process(tmp_path):
    pid_dir = str(tmp_path / "pids")
    os.makedirs(pid_dir, exist_ok=True)
    # PID praticamente garantido morto/inexistente — simula um processo
    # que já terminou sem nunca passar por stop_process().
    with open(os.path.join(pid_dir, "orphan.pid"), "w", encoding="utf-8") as f:
        f.write("999999")

    assert is_running("orphan", pid_dir=pid_dir) is False
    assert not os.path.exists(os.path.join(pid_dir, "orphan.pid"))
