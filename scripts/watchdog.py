"""
watchdog.py
=============
Vigilante que reinicia sozinho qualquer processo gerido por
`src/process_control.py` (busca contínua, loop ao vivo, atualização de
dados do MT5) que morra inesperadamente — pedido explícito do
utilizador (2026-08): "nada pode parar... só pode parar quando eu
parar". NUNCA decide QUANDO um processo deve correr (isso continua a
ser o utilizador, via os botões do dashboard) — só garante que, uma vez
pedido, continua vivo até um "Parar" explícito.

Usa a distinção DESEJADO vs. VIVO de
`process_control.get_desired_processes()`: um processo arrancado
(`start_process`) e nunca parado (`stop_process`) fica "desejado"; se
deixar de estar vivo sem ter sido pedido a parar, este script
reinicia-o com o MESMO comando exato que o arrancou da primeira vez
(gravado por `start_process` em `<name>.cmd.json`).

O próprio vigilante não é supervisionado por mais ninguém (é o topo da
cadeia, mesmo padrão de qualquer supervisor: systemd/supervisord/NSSM
também não se auto-reiniciam) — por isso corre num loop com try/except
por ciclo que nunca desiste (mesma disciplina de
`run_continuous_strategy_search.run_forever`), e confirma periodicamente
"ainda vivo" no log mesmo quando não há nada para reiniciar — silêncio
total pareceria "morreu" a quem estivesse a observar (lição já aprendida
com o EA: ver `mql5/ScalpingEA.mq5` "OK: a vigiar normalmente").

Uso:
    python scripts/watchdog.py
    python scripts/watchdog.py --check-seconds 60
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _ensure_utf8_console() -> None:
    """Mesma correção de run_continuous_strategy_search.py — o console
    do Windows (cp1252 por default) não codifica acentos portugueses;
    sem isto, logging pode lançar UnicodeEncodeError."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        enc = getattr(stream, "encoding", None)
        if (enc is None or enc.lower() != "utf-8") and hasattr(stream, "buffer"):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"))


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("watchdog")

from process_control import PID_DIR, get_desired_processes, is_running, start_process  # noqa: E402

CHECK_SECONDS_DEFAULT = 30.0
HEARTBEAT_EVERY_N_CHECKS_DEFAULT = 10  # ~5 min de silêncio com o default de 30s antes de confirmar "ainda vivo"


def run_forever(
    check_seconds: float,
    pid_dir: str,
    log,
    max_checks: int | None = None,
    heartbeat_every_n_checks: int = HEARTBEAT_EVERY_N_CHECKS_DEFAULT,
) -> None:
    """Loop principal — extraído de main() para ser testável/chamável
    diretamente (ex.: com max_checks definido, para um teste curto em
    vez de correr para sempre)."""
    check = 0
    while max_checks is None or check < max_checks:
        check += 1
        try:
            desired = get_desired_processes(pid_dir=pid_dir)
            restarted_any = False
            for name, spec in desired.items():
                if is_running(name, pid_dir=pid_dir):
                    continue
                log.warning(
                    "%s devia estar a correr mas morreu — a reiniciar automaticamente "
                    "(sem limite de tentativas, nunca desiste).", name,
                )
                try:
                    pid = start_process(name, spec["cmd"], cwd=spec.get("cwd"), pid_dir=pid_dir)
                    log.info("%s reiniciado com sucesso (PID %d).", name, pid)
                except Exception:
                    log.exception(
                        "%s: falha ao tentar reiniciar — tenta de novo no próximo ciclo (%.0fs).",
                        name, check_seconds,
                    )
                restarted_any = True

            if not restarted_any and check % heartbeat_every_n_checks == 0:
                log.info(
                    "OK: a vigiar %d processo(s) (%s) — todos vivos.",
                    len(desired), ", ".join(sorted(desired)) or "nenhum",
                )
        except KeyboardInterrupt:
            raise
        except Exception:
            # Mesma disciplina de run_continuous_strategy_search.run_forever:
            # uma falha neste ciclo (ex.: erro transitório a ler um .cmd.json)
            # nunca pode matar o vigilante inteiro — é o topo da cadeia de
            # supervisão, ninguém o reinicia se ele próprio morrer.
            log.exception("Vigilante: falha inesperada neste ciclo — a continuar no próximo (nunca desiste).")

        if max_checks is None or check < max_checks:
            time.sleep(check_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-seconds", type=float, default=CHECK_SECONDS_DEFAULT,
                         help="Cadência de verificação — cada processo desejado que não estiver "
                              "vivo é reiniciado nesse mesmo ciclo.")
    parser.add_argument("--pid-dir", default=PID_DIR)
    args = parser.parse_args()

    log.info(
        "A iniciar vigilante — Ctrl+C para parar. Verifica a cada %.0fs; reinicia sozinho "
        "qualquer processo que já foi pedido e ainda não foi explicitamente parado.",
        args.check_seconds,
    )
    try:
        run_forever(args.check_seconds, args.pid_dir, log)
    except KeyboardInterrupt:
        log.info("Interrompido pelo utilizador.")


if __name__ == "__main__":
    _ensure_utf8_console()
    main()
