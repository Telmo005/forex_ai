"""
run_continuous_strategy_search.py
====================================
Corre `strategy_evolution.run_evolutionary_lab()` (camada 0.5) em ciclos
INDEFINIDOS (Ctrl+C para parar), em vez de um único batch — o "correr em
segundo plano para sempre, acumulando rumo a muitas estratégias testadas
ao longo de dias/semanas" pedido pelo utilizador. Cada ciclo:

    1. Com `--auto-refresh-mt5` (opt-in, default OFF): se
       `hedge_candidates.csv` tiver mais de `--refresh-mt5-every-hours`
       horas (ou não existir), chama `data_pipeline.run_pipeline(mode="mt5")`
       primeiro — precisa do terminal MT5 aberto para este passo. SEM esta
       flag (comportamento original), o script NUNCA chama o MT5, só lê o
       que já está em disco — é responsabilidade separada do utilizador
       correr `data_pipeline.py` à parte.
    2. Lê `output/hedge_candidates.csv` + `features_*.parquet` do disco.
    3. Corre uma busca evolutiva (população/gerações pensadas para um
       ciclo de poucos minutos, não uma corrida gigante única).
    4. Revalida via walk-forward tudo que ficou `status=="passed"`
       (`revalidate_walk_forward.revalidate_approved_strategies()`,
       reutilizado tal-e-qual) — só com `--confirm-real-data` explícito.
    5. Regista quantas estratégias elegíveis existem no fim do ciclo
       (`hedge_engine.load_eligible_strategies`) vs. no início.
    6. Dorme `--sleep-seconds` e repete, com uma seed diferente por ciclo
       (nunca repete a mesma busca — ver `_cycle_seed`).

IMPORTANTE (mesma linha vermelha de sempre, CLAUDE.md regra 2/7): este
script NUNCA negoceia nada — só descobre e persiste candidatos, e só
promove a "elegível" quem passa TODOS os gates existentes (in-sample +
walk-forward out-of-sample + `--confirm-real-data`). `scripts/
run_live_hedge_loop.py` é quem lê essas estratégias elegíveis (com
`--reload-every-bars` > 0) e troca de estratégia em runtime — nunca este
script diretamente.

Uso:
    python scripts/run_continuous_strategy_search.py --confirm-real-data
    python scripts/run_continuous_strategy_search.py --confirm-real-data --population-size 80 --sleep-seconds 60
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
    """Mesma correção de revalidate_walk_forward.py — o console do
    Windows (cp1252 por default) não codifica acentos portugueses; sem
    isto, logging pode lançar UnicodeEncodeError."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        enc = getattr(stream, "encoding", None)
        if (enc is None or enc.lower() != "utf-8") and hasattr(stream, "buffer"):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"))


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("run_continuous_strategy_search")

import pandas as pd  # noqa: E402

from data_pipeline import PipelineConfig, run_pipeline  # noqa: E402
from hedge_engine import load_eligible_strategies  # noqa: E402
from revalidate_walk_forward import revalidate_approved_strategies  # noqa: E402
from strategy_evolution import ALL_STRATEGY_TYPES, run_evolutionary_lab  # noqa: E402

STALE_WARNING_HOURS_DEFAULT = 24.0
REFRESH_MT5_EVERY_HOURS_DEFAULT = 1.0


def _warn_if_stale(path: str, stale_warning_hours: float) -> None:
    if not os.path.exists(path):
        return
    age_hours = (time.time() - os.path.getmtime(path)) / 3600.0
    if age_hours > stale_warning_hours:
        log.warning(
            "%s tem %.1fh (> %.1fh) — considera correr `python src/data_pipeline.py --mode mt5` "
            "de novo antes de confiar demasiado nesta busca.",
            path, age_hours, stale_warning_hours,
        )


def _maybe_refresh_mt5_data(
    output_dir: str, refresh_every_hours: float, run_pipeline_fn=run_pipeline,
) -> None:
    """Chama `data_pipeline.run_pipeline(mode="mt5")` se `hedge_candidates.csv`
    não existir ainda ou tiver mais de `refresh_every_hours` horas — opt-in
    via `--auto-refresh-mt5` (ver docstring do módulo). Precisa do terminal
    MT5 já aberto e autenticado (mesmo requisito de `data_pipeline.fetch_mt5`
    /`hedge_engine.live_feed` — `mt5.initialize()` sem argumentos liga-se à
    sessão já aberta). Uma falha aqui (MT5 fechado, por exemplo) é registada
    e o ciclo continua com o que já estiver em disco — nunca propaga a
    exceção, para que esquecer-se de abrir o terminal não mate a busca
    contínua inteira, só a deixe a trabalhar com dados mais antigos.

    `run_pipeline_fn` é injetável (default `data_pipeline.run_pipeline`)
    só para permitir testar a lógica de "quando é que refresca" sem
    precisar de MT5 instalado.
    """
    hedge_csv = os.path.join(output_dir, "hedge_candidates.csv")
    needs_refresh = True
    if os.path.exists(hedge_csv):
        age_hours = (time.time() - os.path.getmtime(hedge_csv)) / 3600.0
        needs_refresh = age_hours >= refresh_every_hours
        if not needs_refresh:
            log.info(
                "auto-refresh-mt5: %s tem %.1fh (< %.1fh) — ainda não precisa de atualizar.",
                hedge_csv, age_hours, refresh_every_hours,
            )
            return
        log.info("auto-refresh-mt5: %s tem %.1fh (>= %.1fh) — a atualizar via MT5...",
                  hedge_csv, age_hours, refresh_every_hours)
    else:
        log.info("auto-refresh-mt5: %s não existe ainda — a buscar dados do MT5 pela primeira vez...", hedge_csv)

    try:
        cfg = PipelineConfig(output_dir=output_dir)
        run_pipeline_fn(cfg, "mt5", {"login": None, "password": None, "server": None})
        log.info("auto-refresh-mt5: dados atualizados com sucesso.")
    except Exception:
        log.exception(
            "auto-refresh-mt5: falhou (terminal MT5 fechado/desligado?) — "
            "a continuar com os dados já em disco.",
        )


def _load_pairs_and_prices(output_dir: str, max_pairs: int) -> tuple[list[tuple[str, str]], dict[str, pd.Series]]:
    """Devolve `([], {})` (nunca levanta) quando NENHUM par está
    cointegrado neste momento — isto é um estado de MERCADO normal e
    recuperável (a cointegração pode reaparecer no próximo refresh de
    dados), não um erro de programação. Só levanta para problemas de
    SETUP genuínos (ficheiro em falta = data_pipeline.py nunca correu).
    Antes disto, "0 pares" levantava RuntimeError, que run_forever()
    registava como "Ciclo N falhou" com traceback completo a cada
    --sleep-seconds, para sempre — alarmante e sem sinal nenhum de que
    era só o mercado, não um bug (encontrado 2026-08-03/04 depois de o
    utilizador reportar ciclos "a falhar a todo o momento" após trocar
    de corretora)."""
    hedge_csv = os.path.join(output_dir, "hedge_candidates.csv")
    if not os.path.exists(hedge_csv):
        raise FileNotFoundError(
            f"{hedge_csv} não encontrado — corre primeiro `python src/data_pipeline.py --mode mt5`."
        )
    hedge_table = pd.read_csv(hedge_csv)
    coint = hedge_table[hedge_table["is_cointegrated"]].head(max_pairs)
    pairs = list(zip(coint["pair_a"], coint["pair_b"]))
    if not pairs:
        if hedge_table.empty:
            log.warning("%s está vazio — corre `python src/data_pipeline.py --mode mt5` primeiro.", hedge_csv)
        else:
            closest = hedge_table.loc[hedge_table["coint_pvalue"].idxmin()]
            log.warning(
                "Nenhum par cointegrado neste momento em %s (limiar p<0.05). Mais próximo: "
                "%s/%s (p=%.4f). Estado de mercado normal, não um erro — a busca recomeça "
                "sozinha assim que um par voltar a ficar cointegrado num refresh de dados futuro.",
                hedge_csv, closest["pair_a"], closest["pair_b"], closest["coint_pvalue"],
            )
        return [], {}

    symbols = sorted({s for pair in pairs for s in pair})
    price_data = {}
    for sym in symbols:
        path = os.path.join(output_dir, f"features_{sym}.parquet")
        price_data[sym] = pd.read_parquet(path)["close"]
    return pairs, price_data


def _cycle_seed(base_seed: int, cycle: int) -> int:
    # Cada ciclo tem de explorar região DIFERENTE do espaço de parâmetros
    # — reusar a mesma seed com os mesmos dados produziria exatamente a
    # mesma busca (run_evolutionary_lab é determinístico), desperdiçando
    # o ciclo inteiro.
    return base_seed + cycle


def run_forever(
    output_dir: str,
    db_path: str,
    max_pairs: int,
    population_size: int,
    max_generations: int,
    patience: int,
    strategy_types: list[str] | None,
    real_data: bool,
    sleep_seconds: float,
    stale_warning_hours: float,
    base_seed: int,
    max_cycles: int | None = None,
    auto_refresh_mt5: bool = False,
    refresh_mt5_every_hours: float = REFRESH_MT5_EVERY_HOURS_DEFAULT,
) -> None:
    """Loop principal — extraído de main() para ser testável/chamável
    diretamente (ex.: com max_cycles definido, para um teste de fumo
    curto em vez de correr para sempre).

    `auto_refresh_mt5` (default False, opt-in — ver docstring do módulo
    e `--auto-refresh-mt5`): se True, tenta atualizar os dados via MT5
    no início de cada ciclo quando `hedge_candidates.csv` estiver mais
    velho que `refresh_mt5_every_hours` — precisa do terminal aberto;
    uma falha aqui nunca aborta o ciclo (ver `_maybe_refresh_mt5_data`)."""
    journal_path = os.path.join(output_dir, "strategy_lab_journal.md")
    cycle = 0

    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        log.info("=== Ciclo %d ===", cycle)
        try:
            if auto_refresh_mt5:
                _maybe_refresh_mt5_data(output_dir, refresh_mt5_every_hours)

            hedge_csv = os.path.join(output_dir, "hedge_candidates.csv")
            _warn_if_stale(hedge_csv, stale_warning_hours)

            pairs, price_data = _load_pairs_and_prices(output_dir, max_pairs)

            if not pairs:
                # Estado de mercado normal (ver _load_pairs_and_prices) — já
                # registado lá com o par mais próximo; aqui só confirma que
                # o ciclo terminou (não falhou) sem nada para testar.
                log.info("Ciclo %d: nada a testar (sem pares cointegrados) — a dormir até ao próximo ciclo.", cycle)
            else:
                log.info("Ciclo %d: %d par(es) cointegrado(s): %s", cycle, len(pairs), pairs)

                n_eligible_before = len(load_eligible_strategies(db_path))

                run_evolutionary_lab(
                    pairs, price_data, db_path,
                    strategy_types=strategy_types,
                    population_size=population_size,
                    max_generations=max_generations,
                    patience=patience,
                    seed=_cycle_seed(base_seed, cycle),
                    journal_path=journal_path,
                    log=log.info,
                )

                if real_data:
                    revalidate_approved_strategies(db_path, output_dir, real_data=True, log=log)
                else:
                    log.warning(
                        "Ciclo %d: --confirm-real-data NÃO foi passado — a saltar a revalidação "
                        "walk-forward (nenhuma estratégia nova pode tornar-se elegível neste ciclo).",
                        cycle,
                    )

                n_eligible_after = len(load_eligible_strategies(db_path))
                delta = n_eligible_after - n_eligible_before
                log.info(
                    "Ciclo %d concluído: %d estratégia(s) elegível(is) (%s%d desde o início do ciclo).",
                    cycle, n_eligible_after, "+" if delta >= 0 else "", delta,
                )
        except KeyboardInterrupt:
            raise
        except Exception:
            # Um ciclo falhar (ex.: parquet temporariamente em falta a meio
            # de uma atualização concorrente de data_pipeline.py) nunca pode
            # matar o processo inteiro — é assim que "corre para sempre"
            # sobrevive a falhas transitórias. Mesma disciplina de
            # revalidate_walk_forward.py (WR-01, log.exception + continue).
            log.exception("Ciclo %d falhou — a continuar no próximo ciclo.", cycle)

        if max_cycles is None or cycle < max_cycles:
            log.info("A dormir %.0fs até ao próximo ciclo...", sleep_seconds)
            time.sleep(sleep_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--db", default=None, help="Default: <output-dir>/strategy_lab.db")
    parser.add_argument("--max-pairs", type=int, default=4)
    parser.add_argument("--population-size", type=int, default=40,
                         help="Menor do que uma corrida manual única — pensado para um ciclo "
                              "de poucos minutos, repetido indefinidamente.")
    parser.add_argument("--max-generations", type=int, default=10)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--strategy-types", type=str, default=None,
                         help="Lista separada por vírgulas — default usa todas de "
                              f"{ALL_STRATEGY_TYPES}.")
    parser.add_argument(
        "--confirm-real-data", action="store_true", default=False,
        help="Confirma que output/ contém dados de `data_pipeline.py --mode mt5` (nunca "
             "--mode synth) — sem isto, o script continua a gerar/testar estratégias mas "
             "NUNCA as revalida via walk-forward, e portanto nenhuma se torna elegível "
             "(CLAUDE.md regra 2/7, RESEARCH.md Pitfall 4).",
    )
    parser.add_argument("--sleep-seconds", type=float, default=300.0)
    parser.add_argument("--stale-warning-hours", type=float, default=STALE_WARNING_HOURS_DEFAULT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--auto-refresh-mt5", action="store_true", default=False,
        help="Atualiza os dados via MT5 sozinho, no início de cada ciclo, quando "
             "hedge_candidates.csv estiver mais velho que --refresh-mt5-every-hours "
             "(ou não existir). Precisa do terminal MT5 aberto e autenticado — sem isto "
             "(default), o script nunca toca no MT5, só lê o que já está em disco.",
    )
    parser.add_argument("--refresh-mt5-every-hours", type=float, default=REFRESH_MT5_EVERY_HOURS_DEFAULT,
                         help="Só com --auto-refresh-mt5.")
    args = parser.parse_args()

    db_path = args.db or os.path.join(args.output_dir, "strategy_lab.db")
    strategy_types = args.strategy_types.split(",") if args.strategy_types else None

    if not args.confirm_real_data:
        log.warning(
            "A correr SEM --confirm-real-data: nenhuma estratégia descoberta agora poderá "
            "tornar-se elegível para o loop ao vivo. Passa --confirm-real-data depois de "
            "confirmares que output/ vem de `--mode mt5`."
        )
    if args.auto_refresh_mt5:
        log.info(
            "auto-refresh-mt5 ATIVO — este processo vai chamar o MT5 sozinho a cada %.1fh; "
            "o terminal tem de estar aberto e autenticado.",
            args.refresh_mt5_every_hours,
        )

    log.info(
        "A iniciar busca contínua — Ctrl+C para parar. Ciclo: população=%d, gerações máx=%d, "
        "patience=%d, dorme %.0fs entre ciclos.",
        args.population_size, args.max_generations, args.patience, args.sleep_seconds,
    )
    try:
        run_forever(
            args.output_dir, db_path, args.max_pairs, args.population_size,
            args.max_generations, args.patience, strategy_types, args.confirm_real_data,
            args.sleep_seconds, args.stale_warning_hours, args.seed,
            auto_refresh_mt5=args.auto_refresh_mt5,
            refresh_mt5_every_hours=args.refresh_mt5_every_hours,
        )
    except KeyboardInterrupt:
        log.info("Interrompido pelo utilizador.")


if __name__ == "__main__":
    _ensure_utf8_console()
    main()
