"""
revalidate_walk_forward.py
============================
CLI que revalida estratégias já ✅ Aprovadas no dashboard (status="passed"
em strategy_registry) contra o mecanismo de walk-forward out-of-sample
(backtest_engine.walk_forward_validate(), plan 01-03 desta fase), e
persiste um veredito honesto via strategy_registry.save_walk_forward_result().

Isto é a EXECUÇÃO do mecanismo de walk-forward (já provado em dados
sintéticos no plan 01-03) contra os dados de mercado que já estão em disco
(output/features_{sym}.parquet) — não regenera dados, não reprova a
ligação MT5. A proveniência desses dados (reais via `--mode mt5`, ou o
fallback sintético/CSV-import) foi estabelecida no plan 01-01 e fica
registada em .planning/phases/01-walk-forward-cost-aware-validation/
01-01-SUMMARY.md — este script NÃO lê esse ficheiro nem tenta inferir a
proveniência sozinho.

IMPORTANTE (CLAUDE.md regra 7 / RESEARCH.md Pitfall 4 desta fase):
`revalidated_on_real_data` NUNCA pode ser True para dados sintéticos ou de
fallback. Este script expõe uma flag explícita `--real-data` (default
FALSE) que o executor/checkpoint humano só deve ativar depois de confirmar
que os dados em output/ vieram de facto de `data_pipeline.py --mode mt5`
(nunca `--mode synth`). O script em si é "burro" quanto a isto — não
adivinha, não faz parsing do SUMMARY, só obedece ao valor explícito do
argparse. A decisão de qual invocação correr é do humano/checkpoint, não
deste código.

Uso:
    python src/revalidate_walk_forward.py                 # fallback — revalidated_on_real_data fica False
    python src/revalidate_walk_forward.py --real-data     # só depois de confirmar dados reais em disco
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys

import pandas as pd

def _ensure_utf8_console() -> None:
    """Força stdout/stderr para UTF-8: o console do Windows (cp1252 por
    default) não consegue codificar acentos portugueses nem emojis usados
    nas mensagens deste script (ex.: "✅", "não"), e
    argparse.print_help()/logging escrevem diretamente para esses streams.
    Sem isto, `--help` e o logging normal podem lançar UnicodeEncodeError em
    vez de simplesmente imprimir texto — já observado como problema
    cosmético noutros scripts desta fase (ver 01-02-SUMMARY.md "Issues
    Encountered"), mas aqui precisa de correção real porque o --help tem de
    sair com código 0 (critério de aceitação).

    WR-02 (01-REVIEW.md): isto corria incondicionalmente à importação do
    módulo e assumia que sys.stdout/sys.stderr sempre expõem `.buffer` —
    falso para streams já substituídos por outra ferramenta (ex. captura de
    stdout do pytest nalgumas configurações), o que levantava AttributeError
    só por importar este módulo, mesmo sem correr main(). Agora só se chama
    explicitamente a partir de `if __name__ == "__main__"` (nunca à
    importação) e verifica `hasattr(stream, "buffer")` antes de envolver.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        enc = getattr(stream, "encoding", None)
        if (enc is None or enc.lower() != "utf-8") and hasattr(stream, "buffer"):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"))

from backtest_engine import resolve_cost_params, walk_forward_validate
from strategy_registry import list_strategies, save_walk_forward_result

log = logging.getLogger("revalidate_walk_forward")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def revalidate_approved_strategies(
    db_path: str,
    output_dir: str,
    real_data: bool,
    log=log,
) -> list[dict]:
    """Carrega estratégias status="passed", revalida cada uma via
    walk_forward_validate() (sempre cost-aware, via resolve_cost_params) e
    persiste o veredito com save_walk_forward_result(). Devolve a lista de
    resultados (um dict por estratégia revalidada) para uso em testes ou
    logging externo.

    Não regenera dados nem reprova a ligação MT5 — consome apenas o que já
    está em output/features_{sym}.parquet.
    """
    approved = list_strategies(db_path, status="passed")
    if approved.empty:
        log.info(
            "Nenhuma estratégia ✅ Aprovada encontrada em %s — nada a revalidar. "
            "Aprova estratégias no dashboard (streamlit run dashboard.py) primeiro.",
            db_path,
        )
        return []

    log.info(
        "%d estratégia(s) aprovada(s) encontrada(s) — a revalidar via walk-forward "
        "(modo: %s).",
        len(approved),
        "REAL DATA (--real-data confirmado)" if real_data else
        "FALLBACK/SINTÉTICO — revalidated_on_real_data ficará False (VALID-01 NÃO satisfeito por esta corrida)",
    )

    price_cache: dict[str, pd.Series] = {}

    def load_price(sym: str) -> pd.Series:
        if sym not in price_cache:
            path = os.path.join(output_dir, f"features_{sym}.parquet")
            price_cache[sym] = pd.read_parquet(path)["close"]
        return price_cache[sym]

    results = []
    for _, row in approved.iterrows():
        strategy_id = row["id"]
        pair_a = row["pair_a"]
        pair_b = row["pair_b"]
        try:
            # WR-01 (01-REVIEW.md): qualquer falha aqui (parquet em falta,
            # params JSON corrompido, reference_lot_size divergente em
            # resolve_cost_params, etc.) não pode abortar o batch inteiro —
            # regista-se o erro, salta-se esta estratégia, e continua-se
            # com as restantes, para que um único registo mal-formado não
            # apague o progresso de revalidação já feito nesta corrida.
            params = json.loads(row["params"])
            cost_params = resolve_cost_params(pair_a, pair_b)

            price_a = load_price(pair_a)
            price_b = load_price(pair_b)

            wf_result = walk_forward_validate(price_a, price_b, params, cost_params=cost_params)

            save_walk_forward_result(
                db_path,
                strategy_id,
                wf_passed=wf_result["overall_passed"],
                wf_fold_results=wf_result["fold_results"],
                revalidated_on_real_data=real_data,
            )

            log.info(
                "Estratégia %s (%s/%s): wf_passed=%s, revalidated_on_real_data=%s",
                strategy_id, pair_a, pair_b, wf_result["overall_passed"], real_data,
            )

            results.append({
                "strategy_id": strategy_id,
                "pair_a": pair_a,
                "pair_b": pair_b,
                "wf_passed": wf_result["overall_passed"],
                "revalidated_on_real_data": real_data,
                "fold_results": wf_result["fold_results"],
            })
        except Exception:
            log.exception(
                "Falha a revalidar estratégia %s (%s/%s) — a saltar, restantes continuam.",
                strategy_id, pair_a, pair_b,
            )
            continue

    return results


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Revalida estratégias ✅ Aprovadas via walk-forward out-of-sample "
            "(backtest_engine.walk_forward_validate) contra os dados já em disco."
        )
    )
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--db", default=None, help="Caminho para strategy_lab.db (default: <output-dir>/strategy_lab.db)")
    parser.add_argument(
        "--real-data",
        action="store_true",
        default=False,
        help=(
            "Marca revalidated_on_real_data=True nesta corrida. SÓ deve ser passado "
            "depois de confirmar (fora deste script) que os dados em output/ vieram "
            "de `data_pipeline.py --mode mt5` (nunca --mode synth). Default: False."
        ),
    )
    args = parser.parse_args()

    db_path = args.db or os.path.join(args.output_dir, "strategy_lab.db")

    if args.real_data:
        log.info(
            "Modo REAL-DATA ativo (--real-data passado explicitamente). "
            "revalidated_on_real_data será marcado True — confirma que output/ "
            "contém dados de `--mode mt5`, nunca sintéticos (RESEARCH.md Pitfall 4)."
        )
    else:
        log.info(
            "Modo FALLBACK (default, sem --real-data). revalidated_on_real_data ficará "
            "False nesta corrida — dados sintéticos/fallback NUNCA podem satisfazer "
            "VALID-01, independentemente do resultado de wf_passed (RESEARCH.md Pitfall 4)."
        )

    revalidate_approved_strategies(db_path, args.output_dir, real_data=args.real_data, log=log)


if __name__ == "__main__":
    _ensure_utf8_console()
    main()
