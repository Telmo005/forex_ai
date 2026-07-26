"""
signal_bridge.py
==================
Ponte de ficheiros Python -> MQL5 (Fase 3/4, decisão em aberto de
`ARCHITECTURE.md` resolvida aqui como "ficheiro partilhado, formato
JSON Lines" — ver `docs/risk_engine_mql5_spec.md` "Comunicação Python
<-> MQL5"). Traduz os eventos já aprovados de
`hedge_engine.run_hedge_loop()` para linhas JSON simples e planas (sem
aninhamento) que `mql5/ScalpingEA.mq5` consome via
`mql5/SignalBridge.mqh::ParseSignalLine()` — deliberadamente NÃO usa
uma biblioteca de JSON genérica em nenhum dos dois lados: o esquema é
fixo e plano, e um parser manual de poucas linhas em MQL5 evita
depender de uma lib externa não vendida neste repositório.

Este módulo não decide risco nem sizing — só serializa o que
`propose_to_risk_engine`/`run_hedge_loop` já decidiram (HEDGE-02
mantém-se: a autoridade de aprovação continua inteiramente do lado
Python/`risk_engine.py`; o EA reverifica localmente antes de executar,
RISK-07).

`RiskDecision.size_lots` é uma FRAÇÃO DE EQUITY (0.25x-Kelly), não um
lote concreto da corretora — a conversão para lotes reais é
responsabilidade do lado MQL5 (RISK-07,
`SignalBridge.mqh::ComputeLotFromRiskFraction`), porque exige o
tick_value/tick_size do símbolo, só disponível em runtime no terminal.
Esse campo é escrito na linha JSON como `"risk_fraction"`, nunca como
`"lots"`, para que o nome não sugira erradamente que já é um valor
pronto a enviar a `CTrade`.

Só eventos ACIONÁVEIS são escritos: `close_hedge` (sempre — é
autoridade própria do módulo de hedge, D-03/D-04/D-05) e `open_hedge`
com `risk_decision.approved is True` (uma proposta rejeitada nunca
chega ao ficheiro partilhado — não há necessidade de o EA sequer saber
que existiu).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("signal_bridge")


def _event_to_json_line(event: dict) -> str | None:
    """Converte um único evento de `run_hedge_loop()` numa linha JSON
    plana, ou devolve None se o evento não for acionável (proposta de
    abertura rejeitada pelo motor de risco).

    devolve:
        str | None - linha JSON (sem newline final) pronta a escrever,
            ou None se este evento não deve ser enviado ao EA
    """
    proposal = event["proposal"]
    action = proposal["action"]

    if action == "close_hedge":
        return json.dumps({
            "action": "close_hedge",
            "pair_a": proposal["pair_a"],
            "pair_b": proposal["pair_b"],
            "trigger": proposal["trigger"],
            "timestamp": time.time(),
        })

    # action == "open_hedge"
    decision = event.get("risk_decision")
    if decision is None or not getattr(decision, "approved", False):
        return None

    return json.dumps({
        "action": "open_hedge",
        "pair_a": proposal["pair_a"],
        "pair_b": proposal["pair_b"],
        "direction": proposal["direction"],
        "hedge_ratio": event["hedge_ratio"],
        "risk_fraction": decision.size_lots,
        "sl_distance_price_units": event["stop_distance_price_units"],
        # D-07 (ver docstring do módulo + risk_engine_mql5_spec.md): valores
        # de exposição já pré-calculados do lado Python, porque o EA não tem
        # matriz de correlação própria — RiskGuard.mqh::CheckExposureLimits
        # só reconfirma estes dois valores contra os limiares locais.
        "new_position_exposure_pct": event["new_position_exposure_pct"],
        "aggregate_exposure_pct_after": event["aggregate_exposure_pct_after"],
        "timestamp": time.time(),
    })


def write_signal_file(events: list[dict], path: str) -> int:
    """Escreve os eventos ACIONÁVEIS de `run_hedge_loop()` em `path`,
    um objeto JSON plano por linha (JSON Lines) — substitui sempre o
    conteúdo anterior do ficheiro (o EA consome-o inteiro e apaga-o a
    cada ciclo de polling, ver `SignalBridge.mqh`; não há acumulação
    entre chamadas deste lado).

    params:
        events (list[dict]) - saída de hedge_engine.run_hedge_loop()
        path   (str)        - caminho do ficheiro partilhado (pasta
            Common\\Files do terminal MT5 em produção; qualquer caminho
            em testes)

    devolve:
        (int) - nº de linhas efetivamente escritas (eventos acionáveis)
    """
    lines = [line for line in (_event_to_json_line(e) for e in events) if line is not None]

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    if lines:
        log.info("write_signal_file: %d evento(s) acionável(is) escrito(s) em %s", len(lines), path)
    return len(lines)


def write_heartbeat(path: str) -> None:
    """Atualiza o ficheiro de heartbeat com o timestamp UTC atual — o
    EA compara o instante de modificação deste ficheiro contra
    `RISKGUARD_HEARTBEAT_TIMEOUT_SECONDS` (D-12) para decidir entrar em
    modo "só gestão" (nunca fecha posições existentes, só bloqueia
    ABERTURA de novas — mesma semântica do kill-switch/disjuntores de
    drawdown, RISK-06/Pitfall 5).

    O CONTEÚDO em si não é lido pelo EA (que usa o mtime do próprio
    ficheiro) — é escrito de forma legível (ISO 8601) só para depuração
    humana caso o ficheiro seja aberto manualmente.

    params:
        path (str) - caminho do ficheiro de heartbeat partilhado
    """
    import datetime

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(datetime.datetime.now(datetime.timezone.utc).isoformat(), encoding="utf-8")
