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
import functools
import json
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("run_live_hedge_loop")

from src.divergence_monitor import enforce_kill_switch_if_diverging
from src.hedge_engine import (
    live_feed,
    load_eligible_strategies,
    propose_to_risk_engine,
    run_hedge_loop,
)
from src.risk_engine import AccountState
from src.signal_bridge import write_heartbeat, write_signal_file
from src.trade_ledger import record_trade_close, record_trade_open

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


def _baseline_stats_for_strategy_id(db_path: str, strategy_id: str | None) -> dict:
    """Vai buscar as estatísticas out-of-sample da estratégia EXATA
    (por id) que fixou/fechou este trade — via strategy_registry.get_strategy(),
    nunca resolvendo de novo por par via select_strategy_for_pair().

    Com --reload-every-bars > 0, uma posição pode ter sido aberta com uma
    estratégia que já não está entre as elegíveis atuais (foi substituída
    por uma melhor entretanto) — resolver por PAR nesse caso compararia o
    trade real contra a baseline da estratégia ERRADA (a atual, não a que
    realmente o negociou). Ler diretamente da base de dados pelo id
    (guardado no evento de fecho por hedge_engine.run_hedge_loop, ver
    "strategy_id") elimina esta divergência por construção — sempre a
    MESMA estratégia que abriu o trade, nunca a que está em vigor agora."""
    from src.strategy_registry import get_strategy

    if not strategy_id:
        return {}
    record = get_strategy(db_path, strategy_id)
    if record is None:
        log.warning("_baseline_stats_for_strategy_id: id=%s não encontrado em %s", strategy_id, db_path)
        return {}

    raw = record.get("wf_fold_results")
    if raw:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict) and parsed.get("aggregate_stats"):
            return parsed["aggregate_stats"]
    return {"win_rate": record.get("win_rate", 0.0), "profit_factor": record.get("profit_factor", 0.0)}


def _heartbeat_loop(heartbeat_path: str, interval_seconds: float, stop_event: threading.Event) -> None:
    """Corre numa thread daemon separada, escrevendo o heartbeat a cada
    `interval_seconds` — o EA usa isto para decidir entrar em modo
    "só gestão" (RiskGuard.mqh). CRÍTICO: uma falha de escrita aqui
    (ex.: Common\\Files temporariamente bloqueado durante um reinício
    do terminal MT5) NÃO PODE matar esta thread para sempre — sem o
    try/except, uma exceção não apanhada termina só esta thread
    silenciosamente (o processo principal continua vivo, tasklist nunca
    mostra nada de errado), deixando o heartbeat parado indefinidamente
    sem nenhum sinal visível de que parou — bug real encontrado em
    produção 2026-08-04: heartbeat parou ~4 min depois de um reinício do
    terminal, o processo Python continuou "vivo" mas o EA ficaria preso
    em modo só-gestão sem ninguém saber porquê."""
    while not stop_event.is_set():
        try:
            write_heartbeat(heartbeat_path)
        except OSError:
            log.warning(
                "heartbeat: falha a escrever %s (Common\\Files temporariamente inacessível?) — "
                "a tentar de novo no próximo ciclo, nunca desiste.",
                heartbeat_path, exc_info=True,
            )
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
    parser.add_argument("--ledger-db", default="output/live_trades.db",
                         help="Base de dados SQLite onde as operações reais são registadas "
                              "(src/trade_ledger.py) para o monitor de divergência.")
    parser.add_argument(
        "--reload-every-bars", type=int, default=50,
        help="A cada quantas barras o loop relê output/strategy_lab.db à procura de "
             "estratégias elegíveis novas/melhores (ver hedge_engine.run_hedge_loop, "
             "reload_eligible_fn) — permite ao scripts/run_continuous_strategy_search.py "
             "promover uma estratégia recém-validada sem reiniciar este processo. Uma "
             "posição já aberta NUNCA troca de estratégia a meio do trade (fixada na "
             "entrada); só a próxima entrada usa a lista recarregada. 0 desliga a recarga "
             "(comportamento antigo, lista fixa desde o arranque).",
    )
    parser.add_argument(
        "--selection-policy", default="best_oos_profit_factor",
        choices=["most_recent", "best_oos_profit_factor"],
        help="Critério de desempate quando mais de uma estratégia elegível serve o mesmo "
             "par (ver hedge_engine.select_strategy_for_pair). Default: a estratégia com "
             "melhor profit_factor out-of-sample (walk-forward) — nunca a mais recente só "
             "por ser mais recente.",
    )
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
    # Mesmo nome de ficheiro que RiskGuard.mqh::RISKGUARD_KILL_SWITCH_FILENAME
    # e src.risk_limits.KILL_SWITCH_PATH — só a PASTA muda (Common\Files real,
    # não o cwd do processo Python). Ver nota em hedge_engine.propose_to_risk_engine.
    kill_switch_path = os.path.join(args.common_files, "KILL_SWITCH.flag")

    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(heartbeat_path, args.heartbeat_interval, stop_event), daemon=True,
    )
    heartbeat_thread.start()
    log.info("Heartbeat a atualizar a cada %.0fs em %s", args.heartbeat_interval, heartbeat_path)

    # D-11 (ver src/risk_limits.py: "valor exposto SÓ como dado; Fase 4
    # decide/envia o alerta") — esta é a Fase 4 que finalmente usa o dado.
    # Um dict simples (não persistido) para nunca repetir o mesmo alerta de
    # drawdown a cada evento enquanto a conta continuar acima do limiar —
    # só alerta na TRANSIÇÃO para acima do limiar.
    drawdown_already_alerted = {"daily": False, "weekly": False, "absolute": False}

    def _check_drawdown_alert(decision) -> None:
        from src.alerts import send_alert
        from src.risk_limits import RiskLimits

        threshold = RiskLimits().alert_threshold_pct_of_limit
        for kind, attr in (("daily", "daily_drawdown_pct_of_limit"),
                           ("weekly", "weekly_drawdown_pct_of_limit"),
                           ("absolute", "absolute_drawdown_pct_of_limit")):
            ratio = getattr(decision, attr, 0.0)
            if ratio >= threshold and not drawdown_already_alerted[kind]:
                drawdown_already_alerted[kind] = True
                send_alert(
                    f"⚠️ Drawdown {kind} a {ratio * 100:.0f}% do limite (D-11: aviso a partir de "
                    f"{threshold * 100:.0f}%). Ainda não bloqueia — só informa.",
                    subject=f"Forex AI — Drawdown {kind} a aproximar-se do limite",
                )
            elif ratio < threshold * 0.9:
                # histerese simples: só volta a permitir novo alerta depois
                # de recuar claramente abaixo do limiar (evita alarme repetido
                # por oscilar exatamente à volta do threshold)
                drawdown_already_alerted[kind] = False

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

        pair_a, pair_b = event["pair_a"], event["pair_b"]
        action = event["proposal"]["action"]

        # Monitor de divergência (Camada 3.5): regista a operação real no
        # ledger e, ao fechar, compara o desempenho ao vivo com o validado —
        # nunca aumenta risco, só pode acionar o kill-switch (D-09) se a
        # realidade divergir mal do backtest.
        if action == "open_hedge" and event.get("risk_decision") is not None:
            _check_drawdown_alert(event["risk_decision"])

        if action == "open_hedge" and getattr(event.get("risk_decision"), "approved", False):
            record_trade_open(
                args.ledger_db, pair_a, pair_b, direction=event["proposal"]["direction"],
                entry_price_a=event["entry_price_a"], stop_distance_price_units=event["stop_distance_price_units"],
            )
        elif action == "close_hedge":
            record_trade_close(args.ledger_db, pair_a, pair_b, exit_price_a=event["exit_price_a"],
                                exit_reason=event["proposal"]["trigger"])
            baseline = _baseline_stats_for_strategy_id(args.db, event.get("strategy_id"))
            enforce_kill_switch_if_diverging(args.ledger_db, pair_a, pair_b, baseline, kill_switch_path)

        n = write_signal_file([event], signal_path)
        if n:
            log.info("Evento -> ponte: %s %s/%s (%s)", action, pair_a, pair_b,
                      event["proposal"].get("trigger"))

    feed = live_feed(symbols, args.timeframe, warmup_bars=args.warmup_bars, poll_seconds=args.poll_seconds)
    risk_evaluate_fn = functools.partial(propose_to_risk_engine, kill_switch_path=kill_switch_path)

    reload_eligible_fn = (
        functools.partial(load_eligible_strategies, args.db) if args.reload_every_bars > 0 else None
    )
    if reload_eligible_fn is not None:
        log.info(
            "Recarga dinâmica de estratégias ativa: a cada %d barras, política de seleção '%s'.",
            args.reload_every_bars, args.selection_policy,
        )

    log.info("A iniciar run_hedge_loop() ao vivo — Ctrl+C para parar.")
    try:
        run_hedge_loop(
            feed, eligible, risk_evaluate_fn,
            account_state_provider=_mt5_account_state_provider,
            correlation_matrix=corr_matrix,
            on_event=on_event,
            selection_policy=args.selection_policy,
            reload_eligible_fn=reload_eligible_fn,
            reload_every_bars=args.reload_every_bars,
        )
    except KeyboardInterrupt:
        log.info("Interrompido pelo utilizador.")
    finally:
        stop_event.set()
        log.info("Parado. O heartbeat vai expirar em breve — o EA entra em modo só-gestão "
                 "(nunca fecha posições existentes).")


if __name__ == "__main__":
    main()
