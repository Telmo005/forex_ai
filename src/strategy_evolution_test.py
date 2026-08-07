"""
strategy_evolution_test.py
=============================
Testes runnable-em-qualquer-lugar (sem MT5, sem output/) para o motor de
busca evolutiva — mesmo padrão de backtest_engine_test.py/
strategy_variants_test.py: séries sintéticas em memória, imports bare,
população/gerações pequenas para correr em segundos.

Foco: (1) os operadores genéricos (random/crossover/mutação) nunca saem
dos PARAM_RANGES declarados por tipo, (2) run_evolutionary_lab() persiste
TODOS os candidatos testados (não só os aprovados) no registry, (3) o
diário de texto é escrito quando journal_path é dado, (4) tipos
desconhecidos falham alto e cedo em vez de silenciosamente ignorados.

Uso:
    python -m pytest src/strategy_evolution_test.py -x -q
"""

from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from strategy_evolution import (
    ALL_PARAM_RANGES,
    ALL_STRATEGY_TYPES,
    _crossover,
    _mutate,
    _random_params,
    run_evolutionary_lab,
)
from strategy_registry import list_strategies


def _synthetic_pair(n: int = 600, seed: int = 3) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 0.0005, n)) + 1.1000
    noise_a = rng.normal(0, 0.0002, n)
    noise_b = rng.normal(0, 0.0002, n)
    mean_revert = np.zeros(n)
    for i in range(1, n):
        mean_revert[i] = mean_revert[i - 1] * 0.9 + rng.normal(0, 0.0008)
    price_a = pd.Series(common + noise_a + mean_revert)
    price_b = pd.Series(common + noise_b)
    return price_a, price_b


# ---------------------------------------------------------------------
# Operadores genéricos por strategy_type
# ---------------------------------------------------------------------

def test_random_params_stays_within_declared_ranges():
    rng = random.Random(1)
    for strategy_type in ALL_STRATEGY_TYPES:
        ranges = ALL_PARAM_RANGES[strategy_type]
        for _ in range(50):
            params = _random_params(strategy_type, rng)
            for key, (lo, hi) in ranges.items():
                assert lo <= params[key] <= hi, (strategy_type, key, params[key])


def test_mutate_stays_within_declared_ranges():
    rng = random.Random(2)
    for strategy_type in ALL_STRATEGY_TYPES:
        ranges = ALL_PARAM_RANGES[strategy_type]
        base = _random_params(strategy_type, rng)
        for _ in range(50):
            mutated = _mutate(strategy_type, base, rng, strength=0.5)
            for key, (lo, hi) in ranges.items():
                assert lo <= mutated[key] <= hi, (strategy_type, key, mutated[key])


def test_crossover_child_values_come_from_one_of_the_two_parents():
    rng = random.Random(3)
    for strategy_type in ALL_STRATEGY_TYPES:
        parent_a = _random_params(strategy_type, rng)
        parent_b = _random_params(strategy_type, rng)
        child = _crossover(strategy_type, parent_a, parent_b, rng)
        for key in ALL_PARAM_RANGES[strategy_type]:
            assert child[key] in (parent_a[key], parent_b[key])


# ---------------------------------------------------------------------
# run_evolutionary_lab: end-to-end em memória/tmp_path
# ---------------------------------------------------------------------

def test_run_evolutionary_lab_persists_every_candidate_tested(tmp_path):
    price_a, price_b = _synthetic_pair()
    db_path = str(tmp_path / "lab.db")

    summaries = run_evolutionary_lab(
        pairs=[("EURUSD", "AUDUSD")],
        price_data={"EURUSD": price_a, "AUDUSD": price_b},
        db_path=db_path,
        strategy_types=["zscore"],
        population_size=6,
        max_generations=3,
        patience=2,
        seed=11,
        log=lambda *a, **k: None,
    )

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["pair_a"] == "EURUSD" and summary["pair_b"] == "AUDUSD"
    assert summary["strategy_type"] == "zscore"

    df = list_strategies(db_path)
    # nº de linhas persistidas == nº de candidatos testados (nenhum
    # descartado, mesmo os reprovados) — n_tested = população * gerações
    # efetivamente correntes.
    assert len(df) == summary["n_tested"]
    assert set(df["strategy_type"].dropna().unique()) == {"zscore"}


def test_run_evolutionary_lab_writes_journal_when_path_given(tmp_path):
    price_a, price_b = _synthetic_pair()
    db_path = str(tmp_path / "lab.db")
    journal_path = str(tmp_path / "journal.md")

    run_evolutionary_lab(
        pairs=[("EURUSD", "AUDUSD")],
        price_data={"EURUSD": price_a, "AUDUSD": price_b},
        db_path=db_path,
        strategy_types=["zscore"],
        population_size=5,
        max_generations=2,
        patience=1,
        seed=5,
        journal_path=journal_path,
        log=lambda *a, **k: None,
    )

    assert pytest is not None  # sanity: pytest importado com sucesso
    with open(journal_path, encoding="utf-8") as f:
        content = f.read()
    assert "EURUSD/AUDUSD" in content
    assert "zscore" in content


def test_run_evolutionary_lab_appends_journal_across_multiple_runs(tmp_path):
    price_a, price_b = _synthetic_pair()
    db_path = str(tmp_path / "lab.db")
    journal_path = str(tmp_path / "journal.md")

    for seed in (1, 2):
        run_evolutionary_lab(
            pairs=[("EURUSD", "AUDUSD")], price_data={"EURUSD": price_a, "AUDUSD": price_b},
            db_path=db_path, strategy_types=["zscore"], population_size=4,
            max_generations=1, patience=1, seed=seed, journal_path=journal_path,
            log=lambda *a, **k: None,
        )

    with open(journal_path, encoding="utf-8") as f:
        content = f.read()
    assert content.count("## Corrida") == 2


def test_run_evolutionary_lab_unknown_strategy_type_raises(tmp_path):
    price_a, price_b = _synthetic_pair(n=50)
    db_path = str(tmp_path / "lab.db")
    with pytest.raises(ValueError):
        run_evolutionary_lab(
            pairs=[("EURUSD", "AUDUSD")], price_data={"EURUSD": price_a, "AUDUSD": price_b},
            db_path=db_path, strategy_types=["nao_existe"],
            population_size=2, max_generations=1, log=lambda *a, **k: None,
        )
