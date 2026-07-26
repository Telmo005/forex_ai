"""
run_live_hedge_loop.py
========================
Liga o motor de hedge de PRODUÇÃO (`hedge_engine.run_hedge_loop()`) ao
terminal MT5 ao vivo e à ponte de sinais (`src/signal_bridge.py`) que
`mql5/ScalpingEA.mq5` consome. Este é o primeiro processo que faz
decisões REAIS e contínuas (ainda que hoje só existe 1 estratégia
elegível, `output/strategy_lab.db`) — cada proposta ainda passa
inteiramente por `risk_engine.evaluate_order()` (HEDGE-02) e o EA
reverifica tudo localmente (RISK-07) antes de qualquer ordem real.

IMPORTANTE — a correr numa conta DEMO:
    - Corre indefinidamente até Ctrl+C. Mantém o heartbeat fresco numa
      thread separada; se este processo morrer, o EA entra em modo
      "só gestão" (nunca fecha posições existentes, só para de abrir
      novas) passados HeartbeatTimeoutSecs (default 30s).
    - `--n-bars` do warmup e `--poll-seconds` são ajustáveis; valores
      default pensados para M5.
    - NUNCA correr contra uma conta REAL sem primeiro validar
      exaustivamente em demo (CLAUDE.md regra 6, checklist completo em
      docs/risk_engine_mql5_spec.md).

Uso:
    python scripts/run_live_hedge_loop.py
    python scripts/run_live_hedge_loop.py --db output/strategy_lab.db --common-files "C:\\...\\Common\\Files"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("run_live_hedge_loop")

from src.hedge_engine import live_feed, load_eligible_strategies, propose_to_risk_engine, run_hedge_loop
from src.risk_engine import AccountState
from src.signal_bridge import write_heartbeat, write_signal_file

DEFAULT_COMMON_FILES = os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files")


def _load_correlation_matrix(hedge_candidates_csv: str) -> dict:
    """Lê `output/hedge_candidates.csv` (Camada 0) para um dict
    {(pair_a, pair_b): correlation} — usado por
    `risk_engine.aggregate_exposure_pct` (D-07). Se o ficheiro não
    existir, devolve {} (aggregate_exposure_pct já trata isso como
    "sem correlação conhecida", fallback seguro documentado)."""
    import pandas as pd

    if not os.path.exists(hedge_candidates_csv):
        log.warning("_load_correlation_matrix: %s não encontrado — matriz de correlação vazia", hedge_candidates_csv)
        return {}
    df = pd.read_csv(hedge_candidates_csv)
    return {(row["pair_a"], row["pair_b"]): row["correlation"] for _, row in df.iterrows()}


def _mt5_account_state_provider():
    """Constrói um AccountState real a partir do terminal MT5 ligado.
    Import local (mesmo padrão de data_pipeline.py) — só exigido
    quando este driver corre.

    NOTA (v1, limitação conhecida): `daily_start_equity`/
    `weekly_start_equity`/`absolute_hwm` usam o equity ATUAL como
    aproximação (sem persistência entre reinícios deste processo,
    ao contrário do lado MQL5 que já persiste via GlobalVariable*) —
    suficiente para EXERCITAR o mecanismo end-to-end, mas não é o
    tracking real de drawdown diário/semanal que uma operação contínua
    precisaria. `open_positions` fica sempre vazio (Python não lê
    posições MT5 aqui; o EA já reverifica exposição/contagem
    localmente a partir das suas próprias posições reais, RISK-07)."""
    import MetaTrader5 as mt5

    info = mt5.account_info()
    if info is None:
        log.warning("_mt5_account_state_provider: account_info() indisponível — a usar equity neutro")
        return AccountState(equity=10_000.0, daily_start_equity=10_000.0,
                             weekly_start_equity=10_000.0, absolute_hwm=10_000.0, open_positions=[])
    equity = float(info.equity)
    return AccountState(equity=equity, daily_start_equity=equity, weekly_start_equity=equity,
                         absolute_hwm=equity, open_positions=[])


def _heartbeat_loop(heartbeat_path: str, interval_seconds: float, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        write_heartbeat(heartbeat_path)
        stop_event.wait(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/strategy_lab.db")
    parser.add_argument("--hedge-candidates-csv", default="output/hedge_candidates.csv")
    parser.add_argument("--common-files", default=DEFAULT_COMMON_FILES)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--warmup-bars", type=int, default=300)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--heartbeat-interval", type=float, default=5.0)
    parser.add_argument("--log-file", default="output/live_hedge_loop.log",
                         help="Ficheiro onde o log é também gravado (além da consola) — "
                              "importante para uma corrida de vários dias/semanas, "
                              "sobrevive a fechar a janela do terminal.")
    args = parser.parse_args()

    if args.log_file:
        log_dir = os.path.dirname(args.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(args.log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        logging.getLogger().addHandler(file_handler)
        log.info("Log também a ser gravado em %s", args.log_file)

    if not os.path.isdir(args.common_files):
        log.error("Pasta Common\\Files não encontrada: %s — usa --common-files", args.common_files)
        return

    eligible = load_eligible_strategies(args.db)
    if not eligible:
        log.error(
            "Nenhuma estratégia elegível em %s (status=passed AND wf_passed=1 AND "
            "revalidated_on_real_data=1) — nada a fazer. Corre strategy_generator.py + "
            "revalidate_walk_forward.py --real-data primeiro.",
            args.db,
        )
        return

    symbols = sorted({s["pair_a"] for s in eligible} | {s["pair_b"] for s in eligible})
    log.info("%d estratégia(s) elegível(is), símbolos: %s", len(eligible), symbols)

    corr_matrix = _load_correlation_matrix(args.hedge_candidates_csv)
    signal_path = os.path.join(args.common_files, "signal_queue.jsonl")
    heartbeat_path = os.path.join(args.common_files, "heartbeat.flag")

    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(heartbeat_path, args.heartbeat_interval, stop_event), daemon=True,
    )
    heartbeat_thread.start()
    log.info("Heartbeat a atualizar a cada %.0fs em %s", args.heartbeat_interval, heartbeat_path)

    def on_event(event: dict) -> None:
        # Bug corrigido 2026-07-26 (confirmado em teste manual): as
        # primeiras `warmup_bars` iterações do feed são histórico de
        # AQUECIMENTO (só para as janelas rolantes beta/z-score/correlação
        # deixarem de estar "frias") — nunca decisões ao vivo. Sem este
        # filtro, evaluate_hedge_signal disparava normalmente sobre esses
        # bars históricos e o evento resultante era escrito na ponte como
        # se fosse uma decisão em tempo real, arriscando o EA executar uma
        # ordem real a partir de dados já passados. `event["bar_index"]`
        # é o índice posicional no feed (0-based) — < warmup_bars identifica
        # inequivocamente um evento gerado durante o aquecimento.
        if event["bar_index"] < args.warmup_bars:
            log.info(
                "Evento durante aquecimento (bar %d < %d) — IGNORADO, não enviado à ponte: %s %s/%s (%s)",
                event["bar_index"], args.warmup_bars, event["proposal"]["action"],
                event["pair_a"], event["pair_b"], event["proposal"].get("trigger"),
            )
            return
        n = write_signal_file([event], signal_path)
        if n:
            log.info("Evento -> ponte: %s %s/%s (%s)", event["proposal"]["action"],
                      event["pair_a"], event["pair_b"], event["proposal"].get("trigger"))

    feed = live_feed(symbols, args.timeframe, warmup_bars=args.warmup_bars, poll_seconds=args.poll_seconds)

    log.info("A iniciar run_hedge_loop() ao vivo — Ctrl+C para parar.")
    try:
        run_hedge_loop(
            feed, eligible, propose_to_risk_engine,
            account_state_provider=_mt5_account_state_provider,
            correlation_matrix=corr_matrix,
            on_event=on_event,
        )
    except KeyboardInterrupt:
        log.info("Interrompido pelo utilizador.")
    finally:
        stop_event.set()
        log.info("Parado. O heartbeat vai expirar em breve — o EA entra em modo só-gestão "
                 "(nunca fecha posições existentes).")


if __name__ == "__main__":
    main()
