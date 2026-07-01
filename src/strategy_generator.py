"""
strategy_generator.py
======================
Gera variações de parâmetros para a estratégia de hedge (camada 2),
testa cada uma via backtest_engine, e regista o resultado no
strategy_registry. Se uma geração inteira falhar, muta os parâmetros das
melhores tentativas (mesmo falhadas) e tenta de novo — um loop
automático de geração -> teste -> falha -> nova geração, em vez de uma
única tentativa manual.

Uso:
    python src/strategy_generator.py --output-dir ./output --max-pairs 4

Os resultados ficam em output/strategy_lab.db, lidos pelo dashboard.py.
"""

from __future__ import annotations

import argparse
import os
import random
import uuid
from datetime import datetime, timezone

import pandas as pd

from backtest_engine import run_hedge_backtest, validate_strategy
from strategy_registry import init_db, save_strategy

# Intervalos válidos para cada parâmetro — usados tanto para geração
# aleatória inicial como para limitar mutações.
PARAM_RANGES = {
    "entry_threshold": (1.3, 3.0),
    "exit_threshold": (0.1, 0.8),
    "min_correlation": (0.4, 0.75),
    "max_hold_bars": (30, 200),
    "beta_window": (200, 800),
    "corr_window": (100, 300),
}
INT_PARAMS = {"max_hold_bars", "beta_window", "corr_window"}


def random_params(rng: random.Random) -> dict:
    params = {}
    for key, (lo, hi) in PARAM_RANGES.items():
        val = rng.uniform(lo, hi)
        params[key] = int(val) if key in INT_PARAMS else round(val, 2)
    return params


def mutate_params(base: dict, rng: random.Random, strength: float = 0.25) -> dict:
    new = {}
    for key, (lo, hi) in PARAM_RANGES.items():
        span = hi - lo
        delta = rng.uniform(-strength, strength) * span
        val = max(lo, min(hi, base[key] + delta))
        new[key] = int(val) if key in INT_PARAMS else round(val, 2)
    return new


def run_strategy_lab(
    pairs: list[tuple[str, str]],
    price_data: dict[str, pd.Series],
    db_path: str,
    n_initial: int = 10,
    n_generations: int = 2,
    n_per_generation: int = 9,
    seed: int = 42,
    log=print,
) -> list[dict]:
    rng = random.Random(seed)
    init_db(db_path)
    all_results: list[dict] = []

    candidates = [
        (pair_a, pair_b, random_params(rng), None)
        for pair_a, pair_b in pairs
        for _ in range(n_initial)
    ]

    for gen in range(n_generations + 1):
        gen_results = []
        for pair_a, pair_b, params, parent_id in candidates:
            sid = uuid.uuid4().hex[:8]
            result = run_hedge_backtest(price_data[pair_a], price_data[pair_b], params)
            stats = result["stats"]
            passed, reasons = validate_strategy(stats)

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
                **stats,
            }
            save_strategy(db_path, record)
            gen_results.append(record)
            all_results.append(record)

        passed_count = sum(1 for r in gen_results if r["status"] == "passed")
        log(f"Geração {gen}: {passed_count}/{len(gen_results)} estratégia(s) aprovada(s).")

        if gen == n_generations:
            break

        # Próxima geração: mutar as melhores tentativas (por profit factor)
        # de cada par, mesmo as que falharam — é assim que se refina em
        # vez de tentar parâmetros completamente aleatórios outra vez.
        candidates = []
        for pair_a, pair_b in pairs:
            pair_results = [r for r in gen_results if r["pair_a"] == pair_a and r["pair_b"] == pair_b]
            pair_results.sort(key=lambda r: r["profit_factor"], reverse=True)
            seeds = pair_results[:3] or pair_results
            per_seed = max(1, n_per_generation // max(1, len(seeds)))
            for base in seeds:
                for _ in range(per_seed):
                    candidates.append((pair_a, pair_b, mutate_params(base["params"], rng), base["id"]))

    return all_results


def main():
    parser = argparse.ArgumentParser(description="Laboratório de geração e teste de estratégias")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--max-pairs", type=int, default=4)
    parser.add_argument("--n-initial", type=int, default=10)
    parser.add_argument("--n-generations", type=int, default=2)
    parser.add_argument("--n-per-generation", type=int, default=9)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    hedge_csv = os.path.join(args.output_dir, "hedge_candidates.csv")
    if not os.path.exists(hedge_csv):
        raise FileNotFoundError(
            f"{hedge_csv} não encontrado. Corre primeiro: python src/data_pipeline.py --mode synth"
        )

    hedge_table = pd.read_csv(hedge_csv)
    coint = hedge_table[hedge_table["is_cointegrated"]].head(args.max_pairs)
    pairs = list(zip(coint["pair_a"], coint["pair_b"]))
    if not pairs:
        raise RuntimeError("Nenhum par cointegrado encontrado em hedge_candidates.csv.")

    symbols = sorted({s for pair in pairs for s in pair})
    price_data = {}
    for sym in symbols:
        path = os.path.join(args.output_dir, f"features_{sym}.parquet")
        price_data[sym] = pd.read_parquet(path)["close"]

    db_path = os.path.join(args.output_dir, "strategy_lab.db")
    print(f"A testar {len(pairs)} par(es): {pairs}")
    run_strategy_lab(
        pairs, price_data, db_path,
        n_initial=args.n_initial, n_generations=args.n_generations,
        n_per_generation=args.n_per_generation, seed=args.seed,
    )
    print(f"Concluído. Resultados em {db_path} — corre `streamlit run dashboard.py` para validar.")


if __name__ == "__main__":
    main()
