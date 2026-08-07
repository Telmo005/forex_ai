"""
divergence_monitor.py
=======================
Camada 3.5 — compara o desempenho AO VIVO (via `trade_ledger.py`) com
o desempenho validado no laboratório/walk-forward (a estratégia
elegível já usada por `hedge_engine.py`) e aciona o kill-switch (D-09,
`risk_engine.check_kill_switch`) se a realidade divergir mal do que
foi validado.

PRINCÍPIO (o mesmo discutido e acordado nesta sessão): este módulo
NUNCA aumenta risco para tentar "recuperar" — só REDUZ (bloqueando
novas ordens) quando o desempenho ao vivo é significativamente pior do
que o validado. Nunca fecha posições existentes (mesma semântica do
kill-switch em risk_engine.py — RISK-06/Pitfall 5: "parar de abrir",
não "liquidar"). Nunca reativa o kill-switch sozinho — um kill-switch
acionado por divergência só é removido manualmente, depois de revisão
humana (mesma disciplina de D-09).

Regras de decisão DETERMINÍSTICAS (nunca um modelo de ML a decidir
risco — RISK-09 herdado): ver `DIVERGENCE_THRESHOLDS`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.trade_ledger import list_closed_trades

log = logging.getLogger("divergence_monitor")

DIVERGENCE_THRESHOLDS = {
    # Nunca julgar com amostra pequena — mesma disciplina de min_trades
    # do resto do projeto (regra 4/CLAUDE.md, backtest_engine.FOLD_THRESHOLDS)
    "min_trades_before_check": 10,
    # live win_rate abaixo de metade do validado -> gatilho
    "win_rate_ratio_floor": 0.5,
    # profit_factor ao vivo abaixo disto (a perder dinheiro de forma
    # consistente) -> gatilho, independentemente do que foi validado
    "min_profit_factor": 0.8,
    # janela rolante de trades fechados a considerar
    "window": 30,
}


def compute_live_stats(db_path: str, pair_a: str, pair_b: str,
                        window: int = DIVERGENCE_THRESHOLDS["window"]) -> dict | None:
    """Estatísticas ao vivo (janela rolante dos últimos `window` trades
    fechados) para (pair_a, pair_b). Devolve None se não houver
    nenhuma operação fechada ainda — "sem dados" é distinto de "0
    trades", nunca confundir os dois (None nunca deve ser interpretado
    como "está tudo bem")."""
    trades = list_closed_trades(db_path, pair_a, pair_b, limit=window)
    if not trades:
        return None

    pnl_values = [t["pnl_r"] for t in trades if t["pnl_r"] is not None]
    if not pnl_values:
        return None

    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "total_trades": len(pnl_values),
        "win_rate": len(wins) / len(pnl_values),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else 999.0,
        "total_return_r": sum(pnl_values),
    }


def check_divergence(live_stats: dict | None, baseline_stats: dict,
                      thresholds: dict = DIVERGENCE_THRESHOLDS) -> tuple[bool, str | None]:
    """Compara `live_stats` (ver `compute_live_stats`) contra
    `baseline_stats` (as estatísticas agregadas out-of-sample da
    estratégia elegível, mesma forma de
    `risk_engine.resolve_kelly_inputs`/`backtest_engine.compute_stats`
    — espera pelo menos `win_rate`/`profit_factor`).

    Ordem de avaliação fixa, short-circuit (mesmo padrão de
    risk_engine.evaluate_order): amostra insuficiente -> nunca aciona;
    profit_factor ao vivo abaixo do mínimo absoluto -> aciona
    independentemente da baseline; win_rate ao vivo muito abaixo do
    validado -> aciona.

    devolve:
        (True, motivo)  - divergência detetada, motivo explícito
        (False, None)   - sem divergência (ou amostra insuficiente)
    """
    if live_stats is None or live_stats["total_trades"] < thresholds["min_trades_before_check"]:
        return False, None

    if live_stats["profit_factor"] < thresholds["min_profit_factor"]:
        return True, (
            f"profit_factor ao vivo ({live_stats['profit_factor']:.2f}) abaixo do mínimo "
            f"aceitável ({thresholds['min_profit_factor']:.2f}) em {live_stats['total_trades']} trades"
        )

    baseline_win_rate = baseline_stats.get("win_rate", 0.0)
    if baseline_win_rate > 0 and live_stats["win_rate"] < baseline_win_rate * thresholds["win_rate_ratio_floor"]:
        return True, (
            f"win_rate ao vivo ({live_stats['win_rate']:.1%}) muito abaixo do validado "
            f"({baseline_win_rate:.1%}) em {live_stats['total_trades']} trades"
        )

    return False, None


def enforce_kill_switch_if_diverging(ledger_db_path: str, pair_a: str, pair_b: str,
                                      baseline_stats: dict, kill_switch_path: str,
                                      thresholds: dict = DIVERGENCE_THRESHOLDS) -> bool:
    """Ponto de entrada único chamado periodicamente pelo loop ao vivo
    (`scripts/run_live_hedge_loop.py`). Se a divergência for detetada,
    CRIA o ficheiro de kill-switch (a mera existência já bloqueia toda
    nova ordem em todo o sistema, Python e MQL5, D-09) e regista o
    motivo exato — nunca silencioso. Nunca reativa/apaga o
    kill-switch (reset é sempre uma ação humana deliberada).

    devolve:
        (bool) - True se o kill-switch foi (agora ou já antes) acionado
            por esta chamada, False se não havia divergência
    """
    live_stats = compute_live_stats(ledger_db_path, pair_a, pair_b, thresholds["window"])
    triggered, reason = check_divergence(live_stats, baseline_stats, thresholds)

    if not triggered:
        return False

    path = Path(kill_switch_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"divergence_monitor: {pair_a}/{pair_b} — {reason}\n"
            "Kill-switch acionado automaticamente pelo monitor de divergência. "
            "Reset manual só depois de revisão humana.",
            encoding="utf-8",
        )
        # Alerta enviado só na transição (ficheiro acabado de criar) — nunca
        # repetido a cada chamada seguinte enquanto o kill-switch continuar
        # ativo (send_alert() em si já não bloqueia o chamador em caso de
        # falha, ALERT-01).
        from src.alerts import send_alert

        send_alert(
            f"🛑 Kill-switch acionado por divergência em {pair_a}/{pair_b}: {reason}. "
            "Nenhuma posição existente foi fechada — só bloqueia novas aberturas. "
            "Reset manual necessário depois de revisão.",
            subject="Forex AI — Kill-switch acionado (divergência)",
        )
    log.warning(
        "DIVERGÊNCIA DETETADA (%s/%s): %s — kill-switch acionado (%s). "
        "Nenhuma posição existente foi fechada; só bloqueia ABERTURAS novas.",
        pair_a, pair_b, reason, kill_switch_path,
    )
    return True
