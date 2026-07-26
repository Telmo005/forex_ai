"""
write_test_signal.py
======================
Utilitário MANUAL de teste ponta-a-ponta (não faz parte do pipeline de
produção) — escreve um sinal `open_hedge` sintético e um heartbeat
fresco diretamente na pasta `Common\\Files` do terminal MT5, para
confirmar que `mql5/ScalpingEA.mq5` consegue ler a ponte de ficheiros
(`src/signal_bridge.py`) e abrir as duas pernas corretamente numa conta
DEMO — antes de ligar ao loop real de `hedge_engine.run_hedge_loop()`.

`risk_fraction` é propositadamente pequeno (0.001 = 0.1% do equity) —
mesmo que tudo corra bem, a posição aberta é mínima; o objetivo deste
teste é confirmar o MECANISMO (leitura do ficheiro, normalização de
lote, envio das duas pernas com stop-loss), não testar dimensionamento
real.

Uso:
    python scripts/write_test_signal.py
    python scripts/write_test_signal.py --common-files "C:\\caminho\\custom\\Common\\Files"
    python scripts/write_test_signal.py --close   # escreve um sinal close_hedge em vez de open_hedge
    python scripts/write_test_signal.py --keep-alive   # mantém o heartbeat fresco até Ctrl+C

`--keep-alive`: o heartbeat escrito por uma chamada normal é um
instantâneo único — passados HeartbeatTimeoutSecs (default 30s) no
EA, volta a parecer "expirado", porque nada o está a atualizar
continuamente (só o loop real de produção faria isso). Para testar com
calma sem correr contra o relógio, `--keep-alive` volta a escrever o
heartbeat a cada 5 segundos até premires Ctrl+C — o sinal em si só é
escrito uma vez, no arranque.

Como encontrar a pasta Common\\Files se o caminho default não for o
teu: no MetaTrader, Ficheiro -> Abrir pasta de dados -> sobe um nível
até verires "Common" ao lado da pasta com o nome/número do teu
terminal -> entra em Common\\Files. Copia esse caminho completo e passa
com --common-files.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.risk_engine import RiskDecision
from src.signal_bridge import write_heartbeat, write_signal_file

DEFAULT_COMMON_FILES = os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files")


def _open_hedge_event(pair_a: str, pair_b: str) -> dict:
    return {
        "bar_index": 0, "pair_a": pair_a, "pair_b": pair_b,
        "proposal": {"action": "open_hedge", "pair_a": pair_a, "pair_b": pair_b,
                     "direction": 1, "trigger": "manual_test", "trigger_value": 0.0},
        "risk_decision": RiskDecision(approved=True, size_lots=0.001, sl_price=0.0,
                                       reject_reason=None),
        "hedge_ratio": 1.0,
        "entry_price_a": 0.0,
        "stop_distance_price_units": 0.0050,
        "new_position_exposure_pct": 0.001,
        "aggregate_exposure_pct_after": 0.001,
    }


def _close_hedge_event(pair_a: str, pair_b: str) -> dict:
    return {
        "bar_index": 0, "pair_a": pair_a, "pair_b": pair_b,
        "proposal": {"action": "close_hedge", "pair_a": pair_a, "pair_b": pair_b,
                     "trigger": "manual_test", "trigger_value": 0.0},
        "risk_decision": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-files", default=DEFAULT_COMMON_FILES,
                         help="Caminho da pasta Common\\Files do terminal MT5")
    parser.add_argument("--pair-a", default="EURUSD")
    parser.add_argument("--pair-b", default="GBPUSD")
    parser.add_argument("--close", action="store_true",
                         help="Escreve um sinal close_hedge em vez de open_hedge")
    parser.add_argument("--keep-alive", action="store_true",
                         help="Mantém o heartbeat fresco (reescreve a cada 5s) até Ctrl+C")
    args = parser.parse_args()

    if not os.path.isdir(args.common_files):
        print(f"AVISO: pasta não encontrada: {args.common_files}")
        print("Confirma o caminho correto (ver docstring deste script) e usa --common-files.")
        return

    signal_path = os.path.join(args.common_files, "signal_queue.jsonl")
    heartbeat_path = os.path.join(args.common_files, "heartbeat.flag")

    event = _close_hedge_event(args.pair_a, args.pair_b) if args.close else _open_hedge_event(args.pair_a, args.pair_b)
    n_written = write_signal_file([event], signal_path)
    write_heartbeat(heartbeat_path)

    action = "close_hedge" if args.close else "open_hedge"
    print(f"Escrito 1 sinal '{action}' ({args.pair_a}/{args.pair_b}) em: {signal_path}")
    print(f"Heartbeat atualizado em: {heartbeat_path}")
    print(f"Linhas escritas: {n_written}")
    print()
    print("Agora observa a aba 'Especialistas' do MetaTrader — o ScalpingEA deve "
          "consumir o sinal no próximo ciclo de polling (até PollingMillis, default 500ms) "
          "e apagar o ficheiro depois de processar.")

    if args.keep_alive:
        print()
        print("--keep-alive ativo: a atualizar o heartbeat a cada 5s. Prime Ctrl+C para parar.")
        try:
            while True:
                time.sleep(5)
                write_heartbeat(heartbeat_path)
        except KeyboardInterrupt:
            print("\nParado. O heartbeat vai voltar a expirar em breve (comportamento normal).")


if __name__ == "__main__":
    main()
