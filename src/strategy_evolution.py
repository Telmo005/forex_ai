"""
strategy_evolution.py
=======================
Motor de busca evolutiva para o laboratório de estratégias (camada 0.5) —
substitui o loop simples de "mutar os 3 melhores" de
`strategy_generator.run_strategy_lab()` por um algoritmo genético a
sério: população grande, seleção por torneio, crossover + mutação, e
paragem antecipada por falta de melhoria. Cobre também as famílias de
estratégia extra de `strategy_variants.py` (não só o molde zscore
original), tratando cada par (`pair_a`, `pair_b`) x `strategy_type` como
um nicho evolutivo independente.

Reusa (nunca reimplementa) `backtest_engine.resolve_cost_params`/
`validate_strategy`, `strategy_registry.init_db`/`save_strategy`, e
`strategy_variants.run_backtest_for_type` — este módulo só decide QUAIS
parâmetros testar a seguir, nunca como simular um trade ou o que conta
como "aprovado".

`run_strategy_lab()` (o loop antigo) fica completamente intocado —
`--evolutionary` em strategy_generator.py é que escolhe qual dos dois
motores corre.
"""

from __future__ import annotations

import os
import random
import uuid
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backtest_engine import COST_MODEL_VERSION, resolve_cost_params, validate_strategy
from strategy_generator import INT_PARAMS as INT_PARAMS_ZSCORE
from strategy_generator import PARAM_RANGES as PARAM_RANGES_ZSCORE
from strategy_registry import init_db, save_strategy
from strategy_variants import INT_PARAMS_BY_TYPE, PARAM_RANGES_BY_TYPE, STRATEGY_TYPES, run_backtest_for_type

ALL_STRATEGY_TYPES = ("zscore",) + STRATEGY_TYPES
ALL_PARAM_RANGES = {"zscore": PARAM_RANGES_ZSCORE, **PARAM_RANGES_BY_TYPE}
ALL_INT_PARAMS = {"zscore": INT_PARAMS_ZSCORE, **INT_PARAMS_BY_TYPE}


# --------------------------------------------------------------------------
# Operadores genéricos por strategy_type (cada tipo tem o seu próprio
# espaço de parâmetros — crossover/mutação só fazem sentido DENTRO do
# mesmo tipo, nunca entre tipos diferentes, porque as chaves diferem)
# --------------------------------------------------------------------------

def _random_params(strategy_type: str, rng: random.Random) -> dict:
    ranges = ALL_PARAM_RANGES[strategy_type]
    int_keys = ALL_INT_PARAMS[strategy_type]
    params = {}
    for key, (lo, hi) in ranges.items():
        val = rng.uniform(lo, hi)
        params[key] = int(round(val)) if key in int_keys else round(val, 4)
    return params


def _crossover(strategy_type: str, parent_a: dict, parent_b: dict, rng: random.Random) -> dict:
    """Crossover uniforme: cada parâmetro é herdado independentemente de
    um dos dois pais (não de uma média/interpolação) — preserva
    combinações de parâmetros que já funcionaram num dos pais, em vez de
    as diluir."""
    ranges = ALL_PARAM_RANGES[strategy_type]
    return {key: (parent_a[key] if rng.random() < 0.5 else parent_b[key]) for key in ranges}


def _mutate(strategy_type: str, base: dict, rng: random.Random, strength: float = 0.2) -> dict:
    ranges = ALL_PARAM_RANGES[strategy_type]
    int_keys = ALL_INT_PARAMS[strategy_type]
    new = {}
    for key, (lo, hi) in ranges.items():
        span = hi - lo
        delta = rng.uniform(-strength, strength) * span
        val = max(lo, min(hi, base[key] + delta))
        new[key] = int(round(val)) if key in int_keys else round(val, 4)
    return new



# Teto aplicado ao profit_factor só para efeitos de FITNESS (nunca ao valor
# guardado/mostrado no registry/dashboard, que continua exatamente o que
# compute_stats() produziu). Sem este teto, o PROFIT_FACTOR_NO_LOSSES_SENTINEL
# (999.0, backtest_engine.py — "sem perdas na amostra", não um profit factor
# real) ou qualquer valor próximo dele, mesmo numa amostra de 1-3 trades por
# puro acaso, esmagava o sinal de qualquer candidato genuíno: só multiplicar
# por (total_trades / min_trades_for_fitness) não chega, porque 999 * 0.1
# ainda é ~100x maior do que o profit_factor de 1.2-1.8 de uma amostra
# estatisticamente séria — a busca convergia sistematicamente para "sorte de
# amostra pequena" em vez de "edge real com muitos trades" (confirmado na
# primeira corrida real: todas as 8 combinações par+tipo ficaram presas em
# candidatos de 1-9 trades). Mesma disciplina que dashboard.py já aplica ao
# excluir o sentinel de agregados de "melhor profit factor" (WR-03,
# backtest_engine.PROFIT_FACTOR_NO_LOSSES_SENTINEL).
FITNESS_PROFIT_FACTOR_CAP = 5.0


def _fitness(stats: dict, min_trades_for_fitness: int) -> float:
    """profit_factor (com teto, ver FITNESS_PROFIT_FACTOR_CAP) é o
    critério primário, ponderado pela confiança da amostra: amostras
    abaixo de `min_trades_for_fitness` são penalizadas proporcionalmente
    ao nº de trades, para que a busca prefira sempre um edge modesto mas
    testado sobre muitos trades a um profit factor aparentemente enorme
    baseado em 1-3 trades com sorte."""
    capped_profit_factor = min(stats["profit_factor"], FITNESS_PROFIT_FACTOR_CAP)
    sample_weight = min(1.0, stats["total_trades"] / min_trades_for_fitness)
    return capped_profit_factor * sample_weight


def _normalize_reason(reason: str) -> str:
    """Agrupa mensagens de fail_reasons pelo MOTIVO (ex.: "profit factor
    insuficiente"), não pelo valor numérico exato embutido na mensagem —
    sem isto, o histograma do diário teria uma entrada distinta por cada
    valor de profit_factor observado, em vez de agregar por categoria."""
    return reason.split(" (")[0].strip()


# --------------------------------------------------------------------------
# Busca evolutiva de um único nicho (par + strategy_type)
# --------------------------------------------------------------------------

def _run_niche(
    pair_a: str, pair_b: str, price_a: pd.Series, price_b: pd.Series, strategy_type: str,
    db_path: str, population_size: int, max_generations: int, patience: int,
    tournament_size: int, elite_fraction: float, min_trades_for_fitness: int,
    rng: random.Random, log,
) -> dict:
    cost_params = resolve_cost_params(pair_a, pair_b)

    population = [_random_params(strategy_type, rng) for _ in range(population_size)]
    parent_ids: list[str | None] = [None] * population_size

    best_ever: dict | None = None
    best_ever_fitness = -np.inf
    # Rastreado à parte de best_ever: best_ever é o argmax de _fitness()
    # (um proxy de busca — pode preferir um candidato de 10 trades com
    # profit_factor=86 a um de 24 trades com profit_factor=2.7, mesmo que
    # só o segundo passe validate_strategy()). best_passed garante que o
    # diário sempre mostra claramente SE algo passou o gate real, não só
    # "o que pareceu melhor" pelo proxy interno de seleção.
    best_passed: dict | None = None
    best_passed_fitness = -np.inf
    generations_without_improvement = 0
    n_tested = 0
    fail_reason_counter: Counter = Counter()
    last_gen = 0

    for gen in range(max_generations):
        gen_results: list[tuple[dict, float]] = []
        for params, parent_id in zip(population, parent_ids):
            sid = uuid.uuid4().hex[:8]
            result = run_backtest_for_type(strategy_type, price_a, price_b, params, cost_params=cost_params)
            stats = result["stats"]
            passed, reasons = validate_strategy(stats)
            fitness = _fitness(stats, min_trades_for_fitness)

            record = {
                "id": sid,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "pair_a": pair_a, "pair_b": pair_b,
                "params": params,
                "status": "passed" if passed else "failed",
                "fail_reasons": reasons,
                "generation": gen,
                "parent_id": parent_id,
                "trades": result["trades"],
                "cost_model_version": COST_MODEL_VERSION,
                "strategy_type": strategy_type,
                **stats,
            }
            save_strategy(db_path, record)
            gen_results.append((record, fitness))
            n_tested += 1
            if passed and fitness > best_passed_fitness:
                best_passed_fitness = fitness
                best_passed = record
            for reason in reasons:
                fail_reason_counter[_normalize_reason(reason)] += 1

        gen_results.sort(key=lambda rf: rf[1], reverse=True)
        best_this_gen_record, best_this_gen_fitness = gen_results[0]
        if best_this_gen_fitness > best_ever_fitness:
            best_ever_fitness = best_this_gen_fitness
            best_ever = best_this_gen_record
            generations_without_improvement = 0
        else:
            generations_without_improvement += 1

        passed_count = sum(1 for r, _ in gen_results if r["status"] == "passed")
        log(
            f"{pair_a}/{pair_b} [{strategy_type}] geração {gen}: "
            f"{passed_count}/{len(gen_results)} aprovada(s), "
            f"melhor profit_factor={best_this_gen_record['profit_factor']} "
            f"(trades={best_this_gen_record['total_trades']})"
        )
        last_gen = gen

        if generations_without_improvement >= patience:
            log(f"{pair_a}/{pair_b} [{strategy_type}]: sem melhoria em {patience} gerações — paragem antecipada.")
            break
        if gen == max_generations - 1:
            break

        # Próxima geração: elitismo (o melhor de sempre passa intacto) +
        # torneio + crossover + mutação para preencher o resto da população.
        n_elite = max(1, int(round(population_size * elite_fraction)))
        elites = [r for r, _ in gen_results[:n_elite]]

        def _tournament() -> dict:
            contenders = rng.sample(gen_results, min(tournament_size, len(gen_results)))
            return max(contenders, key=lambda rf: rf[1])[0]

        next_population = [e["params"] for e in elites]
        next_parent_ids: list[str | None] = [e["id"] for e in elites]
        while len(next_population) < population_size:
            parent_a_record = _tournament()
            parent_b_record = _tournament()
            child = _crossover(strategy_type, parent_a_record["params"], parent_b_record["params"], rng)
            child = _mutate(strategy_type, child, rng)
            next_population.append(child)
            next_parent_ids.append(parent_a_record["id"])

        population = next_population
        parent_ids = next_parent_ids

    return {
        "pair_a": pair_a, "pair_b": pair_b, "strategy_type": strategy_type,
        "n_tested": n_tested, "n_generations": last_gen + 1,
        "best": best_ever, "best_fitness": best_ever_fitness,
        "best_passed": best_passed,
        "fail_reason_counter": fail_reason_counter,
    }


# --------------------------------------------------------------------------
# Orquestrador — todos os pares x todos os strategy_types pedidos
# --------------------------------------------------------------------------

def run_evolutionary_lab(
    pairs: list[tuple[str, str]],
    price_data: dict[str, pd.Series],
    db_path: str,
    strategy_types: list[str] | None = None,
    population_size: int = 200,
    max_generations: int = 40,
    patience: int = 15,
    tournament_size: int = 4,
    elite_fraction: float = 0.05,
    min_trades_for_fitness: int = 10,
    seed: int = 42,
    journal_path: str | None = None,
    log=print,
) -> list[dict]:
    """Corre uma busca evolutiva independente por (par, strategy_type),
    persiste TODOS os candidatos testados via strategy_registry.save_strategy
    (mesmo os reprovados — nada é descartado, ver docstring do módulo), e
    devolve um resumo por nicho. Se `journal_path` for dado, anexa um
    resumo legível (ver `_write_journal`).

    strategy_types (opcional): lista de nomes de strategy_evolution.ALL_STRATEGY_TYPES
        a testar; None usa todos.
    """
    init_db(db_path)
    rng = random.Random(seed)
    types = strategy_types or list(ALL_STRATEGY_TYPES)

    unknown = [t for t in types if t not in ALL_STRATEGY_TYPES]
    if unknown:
        raise ValueError(f"strategy_types desconhecido(s): {unknown} — válidos: {ALL_STRATEGY_TYPES}")

    niche_summaries = []
    for pair_a, pair_b in pairs:
        for strategy_type in types:
            summary = _run_niche(
                pair_a, pair_b, price_data[pair_a], price_data[pair_b], strategy_type,
                db_path, population_size, max_generations, patience,
                tournament_size, elite_fraction, min_trades_for_fitness, rng, log,
            )
            niche_summaries.append(summary)

    if journal_path:
        _write_journal(journal_path, niche_summaries)

    return niche_summaries


def _write_journal(journal_path: str, summaries: list[dict]) -> None:
    """Anexa (NUNCA sobrescreve) uma secção legível por humano/IA a
    `journal_path` — o "ficheiro de texto com feedback" pedido: o que foi
    tentado, o melhor encontrado (mesmo reprovado), e o motivo de falha
    dominante por nicho, para que a próxima corrida (ou a próxima sessão)
    não repita a mesma busca às cegas."""
    lines = [f"\n## Corrida {datetime.now(timezone.utc).isoformat()}\n"]
    for s in summaries:
        best = s["best"]
        top_reasons = s["fail_reason_counter"].most_common(3)
        reasons_str = "; ".join(f"{msg} ({n}x)" for msg, n in top_reasons) or "n/a (nenhuma falha registada)"
        if best:
            best_line = (
                f"profit_factor={best['profit_factor']}, sharpe={best['sharpe_per_trade']}, "
                f"drawdown={best['max_drawdown_r']}R, trades={best['total_trades']}, "
                f"status={best['status']}"
            )
        else:
            best_line = "nenhum candidato válido"

        best_passed = s.get("best_passed")
        if best_passed:
            passed_line = (
                f"✅ id={best_passed['id']} profit_factor={best_passed['profit_factor']}, "
                f"sharpe={best_passed['sharpe_per_trade']}, drawdown={best_passed['max_drawdown_r']}R, "
                f"trades={best_passed['total_trades']} — precisa AINDA de revalidate_walk_forward.py "
                f"--real-data para virar elegível (in-sample não é suficiente, CLAUDE.md regra 2)."
            )
        else:
            passed_line = "nenhum (0 candidatos passaram validate_strategy nesta corrida)"

        lines.append(
            f"- **{s['pair_a']}/{s['pair_b']} [{s['strategy_type']}]** — "
            f"{s['n_tested']} testada(s) em {s['n_generations']} geração/gerações. "
            f"Melhor por ranking interno: {best_line}. "
            f"Melhor que passou o gate real: {passed_line} "
            f"Motivos de falha mais comuns: {reasons_str}.\n"
        )
    parent = os.path.dirname(journal_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.writelines(lines)
