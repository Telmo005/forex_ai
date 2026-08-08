"""
process_control.py
=====================
Gestão mínima de processos em segundo plano, geridos por ficheiro PID —
usado por `dashboard.py` para arrancar/parar scripts de longa duração
(busca contínua, loop ao vivo, atualização de dados do MT5) a partir de
um clique, sem nenhuma dependência nova (usa `tasklist`/`taskkill`, já
presentes em qualquer Windows — ver CLAUDE.md "Platform Requirements").

Cada processo arrancado ganha a SUA PRÓPRIA janela de consola
(`subprocess.CREATE_NEW_CONSOLE`) — visível, com os logs a aparecer ao
vivo, porque o pedido era "quero ver o processamento a correr", não um
processo escondido sem nenhum sinal visual.

DESEJADO vs. VIVO (ver `get_desired_processes`, usado por
`scripts/watchdog.py`): `start_process` grava também `<name>.cmd.json`
com o comando exato usado — o marcador de "isto DEVE estar a correr".
`stop_process` apaga esse marcador (paragem pedida pelo utilizador =
deixa de ser desejado). Se um processo morrer sozinho (crash, VPS a
reiniciar, etc.) sem passar por `stop_process`, o `.cmd.json` continua
lá — é assim que o vigilante distingue "morreu sem ser pedido, reinicia
sozinho" de "o utilizador pediu para parar, não mexer".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PID_DIR = os.path.join("output", ".pids")


def _pid_file(name: str, pid_dir: str) -> str:
    return os.path.join(pid_dir, f"{name}.pid")


def _cmd_file(name: str, pid_dir: str) -> str:
    return os.path.join(pid_dir, f"{name}.cmd.json")


def _pid_alive(pid: int) -> bool:
    """Confirma que `pid` ainda está vivo via `tasklist` — nunca confia
    só na existência do ficheiro PID, que pode sobreviver a um processo
    já morto (crash, Ctrl+C fora do controlo deste módulo)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return str(pid) in result.stdout


def get_pid(name: str, pid_dir: str = PID_DIR) -> int | None:
    """Lê o PID gravado para `name`, ou None se nunca foi arrancado (ou
    o ficheiro estiver corrompido/ilegível)."""
    path = _pid_file(name, pid_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def is_running(name: str, pid_dir: str = PID_DIR) -> bool:
    """True se há um PID gravado para `name` E esse processo ainda está
    vivo. Um ficheiro PID "órfão" (processo já morreu sem passar por
    stop_process()) é limpo automaticamente aqui, para não confundir a
    próxima chamada a start_process()."""
    pid = get_pid(name, pid_dir)
    if pid is None:
        return False
    alive = _pid_alive(pid)
    if not alive:
        try:
            os.remove(_pid_file(name, pid_dir))
        except OSError:
            pass
    return alive


def start_process(name: str, cmd: list[str], cwd: str | None = None, pid_dir: str = PID_DIR) -> int:
    """Lança `cmd` numa janela de consola nova e independente (sobrevive
    ao fecho do processo do dashboard) e grava o PID gerado em
    `<pid_dir>/<name>.pid`.

    levanta:
        RuntimeError - se já houver um processo vivo registado para
            `name` — o chamador deve verificar is_running(name) antes,
            para decidir mostrar "Parar" em vez de tentar arrancar de
            novo (nunca duplica processos silenciosamente).
    """
    if is_running(name, pid_dir):
        raise RuntimeError(
            f"start_process: já há um processo '{name}' a correr (PID {get_pid(name, pid_dir)})."
        )

    os.makedirs(pid_dir, exist_ok=True)
    creationflags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, cwd=cwd, creationflags=creationflags)
    with open(_pid_file(name, pid_dir), "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    # Marcador de "isto DEVE estar a correr" para o vigilante (ver
    # get_desired_processes) — grava o comando exato, para um restart
    # automático poder relançar exatamente o mesmo processo.
    with open(_cmd_file(name, pid_dir), "w", encoding="utf-8") as f:
        json.dump({"cmd": cmd, "cwd": cwd}, f)
    return proc.pid


def stop_process(name: str, pid_dir: str = PID_DIR) -> bool:
    """Termina o processo registado para `name` (`taskkill /F /T` — o
    `/T` mata também a árvore de processos filhos) e apaga o ficheiro
    PID. Devolve False se não havia nada vivo a correr — parar algo que
    já não existe não é um erro, nunca levanta exceção nesse caso.

    SEMPRE apaga também `<name>.cmd.json` (o marcador de "desejado"),
    mesmo quando não havia nada vivo — clicar em Parar é sempre uma
    decisão explícita do utilizador de "já não quero isto a correr",
    por isso o vigilante (`get_desired_processes`) nunca deve voltar a
    reiniciar isto sozinho depois de um Parar, mesmo que o processo já
    tivesse morrido silenciosamente antes do clique."""
    pid = get_pid(name, pid_dir)
    if pid is None or not _pid_alive(pid):
        try:
            os.remove(_pid_file(name, pid_dir))
        except OSError:
            pass
        _clear_desired(name, pid_dir)
        return False

    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
    try:
        os.remove(_pid_file(name, pid_dir))
    except OSError:
        pass
    _clear_desired(name, pid_dir)
    return True


def _clear_desired(name: str, pid_dir: str) -> None:
    try:
        os.remove(_cmd_file(name, pid_dir))
    except OSError:
        pass


def get_desired_processes(pid_dir: str = PID_DIR) -> dict[str, dict]:
    """Devolve `{name: {"cmd": [...], "cwd": ...}}` para cada processo
    que já foi arrancado por `start_process` e nunca foi explicitamente
    parado por `stop_process` — usado por `scripts/watchdog.py` para
    decidir o que reiniciar sozinho se morrer inesperadamente. Um
    `.cmd.json` corrompido/ilegível é ignorado (nunca levanta), para um
    ficheiro estragado não impedir a supervisão dos restantes."""
    if not os.path.isdir(pid_dir):
        return {}
    desired = {}
    for filename in os.listdir(pid_dir):
        if not filename.endswith(".cmd.json"):
            continue
        name = filename[: -len(".cmd.json")]
        path = os.path.join(pid_dir, filename)
        try:
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
            desired[name] = {"cmd": spec["cmd"], "cwd": spec.get("cwd")}
        except (OSError, ValueError, KeyError):
            continue
    return desired
