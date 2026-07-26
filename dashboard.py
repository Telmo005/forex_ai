"""
dashboard.py
============
UI para validar as estratégias geradas pelo strategy_generator.py.
Mostra cada estratégia testada, se passou ou falhou, há quanto tempo/
quantas barras foi testada, e estatísticas completas (win rate, profit
factor, Sharpe, drawdown), com a curva de equity e o histórico de trades
de cada uma.

Uso:
    streamlit run dashboard.py
"""

import json
import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from backtest_engine import PROFIT_FACTOR_NO_LOSSES_SENTINEL  # noqa: E402
from strategy_registry import init_db, list_strategies  # noqa: E402

st.set_page_config(page_title="Forex AI — Strategy Lab", layout="wide", page_icon="🔬")

DB_PATH = os.environ.get("STRATEGY_DB", os.path.join("output", "strategy_lab.db"))

# Garante que o schema (incluindo as colunas de walk-forward de 01-03:
# wf_passed / wf_fold_results / revalidated_on_real_data, e cost_model_version
# de 01-02) está presente ANTES de qualquer list_strategies() correr. init_db()
# é seguro chamar num DB já existente — migrate_add_cost_columns() e
# migrate_add_walk_forward_columns() são aditivas e idempotentes (só fazem
# ALTER TABLE se a coluna ainda não existir), nunca tocam em dados. Sem isto,
# uma base de dados criada antes do plan 01-03 não teria as colunas de
# walk-forward, e o guard "wf_passed" in df.columns abaixo nunca seria
# suficiente por si só para garantir que a leitura é possível.
if os.path.exists(DB_PATH):
    init_db(DB_PATH)


@st.cache_data(ttl=5)
def load(db_path: str) -> pd.DataFrame:
    return list_strategies(db_path)


st.title("🔬 Forex AI — Strategy Lab")
st.caption(
    "Cada linha é uma estratégia de hedge gerada e testada automaticamente no histórico. "
    "Estado, estatísticas e curva de equity de cada uma, para validares antes de avançar para a camada de risco/execução. "
    "**Todas as métricas apresentadas incluem custos de transação modelados (spread, slippage, comissão) — "
    "não existe nenhuma vista sem custos.**"
)

df = load(DB_PATH)

if df.empty:
    st.warning(
        "Nenhuma estratégia encontrada ainda.\n\n"
        "Corre primeiro:\n"
        "```\npython src/data_pipeline.py --mode synth\npython src/strategy_generator.py\n```"
    )
    st.stop()

# ---------------------------------------------------------------------
# Badge do modelo de custos ativo (VALID-02: nenhuma métrica é cost-blind)
# ---------------------------------------------------------------------
if "cost_model_version" in df.columns and df["cost_model_version"].notna().any():
    versions = sorted(v for v in df["cost_model_version"].dropna().unique())
    st.info(
        "💰 Métricas net-of-cost — modelo de custos ativo: **" + ", ".join(versions) + "**. "
        "Ver `docs/strategy_lab_spec.md` secção \"Modelo de custos de transação\" para os componentes modelados."
    )
else:
    st.warning(
        "Esta base de dados não tem a coluna `cost_model_version` preenchida — provavelmente foi gerada "
        "antes do modelo de custos ser ligado ao backtest. As estatísticas apresentadas podem ser cost-blind. "
        "Corre novamente `python src/strategy_generator.py` para regenerar com custos incluídos."
    )

# ---------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Estratégias testadas", len(df))
c2.metric("Aprovadas", int((df["status"] == "passed").sum()))
c3.metric("Reprovadas", int((df["status"] == "failed").sum()))
# WR-03 (01-REVIEW.md): PROFIT_FACTOR_NO_LOSSES_SENTINEL (999.0) marca "sem
# trades perdedores na amostra", não um profit factor literal de 999x —
# exclui-se do "melhor" para não induzir em erro quem lê o KPI de topo.
# Se TODAS as estratégias forem sentinela (raro), cai para o próprio valor
# (nan-safe via .max() vazio) e a legenda abaixo esclarece o que significa.
finite_pf = df.loc[df["profit_factor"] < PROFIT_FACTOR_NO_LOSSES_SENTINEL, "profit_factor"]
best_pf = finite_pf.max() if not finite_pf.empty else df["profit_factor"].max()
has_sentinel = (df["profit_factor"] >= PROFIT_FACTOR_NO_LOSSES_SENTINEL).any()
c4.metric("Melhor profit factor (net-of-cost)", f"{best_pf:.2f}")
if has_sentinel:
    c4.caption(
        f"⚠️ {int((df['profit_factor'] >= PROFIT_FACTOR_NO_LOSSES_SENTINEL).sum())} "
        f"estratégia(s) sem nenhum trade perdedor na amostra (profit factor "
        f"marcado como {PROFIT_FACTOR_NO_LOSSES_SENTINEL:.0f} — não é um múltiplo "
        "real, excluído deste KPI)."
    )
c5.metric("Gerações executadas", int(df["generation"].max()) + 1)
# VALID-01: contagem de estratégias com revalidação walk-forward CONFIRMADA
# em dados reais — distinta de wf_passed (que pode ter corrido só em
# sintético/fallback, ver RESEARCH.md Pitfall 4 e save_walk_forward_result()).
if "revalidated_on_real_data" in df.columns:
    n_real = int((df["revalidated_on_real_data"] == 1).sum())
else:
    n_real = 0
c6.metric("Revalidadas (real, WF)", n_real)

st.divider()

# ---------------------------------------------------------------------
# Tabela filtrável
# ---------------------------------------------------------------------
left, right = st.columns([1, 3])
with left:
    status_filter = st.radio("Estado", ["Todas", "✅ Aprovadas", "❌ Reprovadas"])
    pair_options = sorted(set(df["pair_a"] + " / " + df["pair_b"]))
    pair_filter = st.multiselect("Filtrar por par", pair_options)

view = df.copy()
if status_filter == "✅ Aprovadas":
    view = view[view["status"] == "passed"]
elif status_filter == "❌ Reprovadas":
    view = view[view["status"] == "failed"]
if pair_filter:
    view = view[(view["pair_a"] + " / " + view["pair_b"]).isin(pair_filter)]

view = view.sort_values("profit_factor", ascending=False)
view_display = view.copy()
view_display["status"] = view_display["status"].map({"passed": "✅ Aprovada", "failed": "❌ Reprovada"})

with right:
    st.dataframe(
        view_display[[
            "id", "pair_a", "pair_b", "status", "generation", "total_trades",
            "win_rate", "profit_factor", "sharpe_per_trade", "total_return_r",
            "max_drawdown_r", "avg_hold_bars", "bars_tested",
        ]].rename(columns={
            "pair_a": "Par A", "pair_b": "Par B", "status": "Estado",
            "generation": "Geração", "total_trades": "Nº trades",
            "win_rate": "Win rate", "profit_factor": "Profit factor",
            "sharpe_per_trade": "Sharpe/trade", "total_return_r": "Retorno (R)",
            "max_drawdown_r": "Drawdown máx (R)", "avg_hold_bars": "Duração média (barras)",
            "bars_tested": "Barras testadas",
        }),
        use_container_width=True, hide_index=True, height=350,
    )

st.divider()

# ---------------------------------------------------------------------
# Detalhe de uma estratégia
# ---------------------------------------------------------------------
st.subheader("Detalhe da estratégia")

if view.empty:
    st.info("Nenhuma estratégia corresponde ao filtro selecionado.")
    st.stop()

selected_id = st.selectbox("ID da estratégia", view["id"].tolist())
row = df[df["id"] == selected_id].iloc[0]

detail_left, detail_right = st.columns([1, 2])

with detail_left:
    st.markdown(f"**Par:** {row['pair_a']} / {row['pair_b']}")
    st.markdown(f"**Geração:** {row['generation']}" + (f" (mutada de `{row['parent_id']}`)" if row["parent_id"] else " (inicial)"))
    if row["status"] == "passed":
        st.success("✅ Estratégia APROVADA — passou em todos os critérios de validação.")
    else:
        st.error("❌ Estratégia REPROVADA")
        reasons = json.loads(row["fail_reasons"])
        st.markdown("**Motivos:**")
        for r in reasons:
            st.markdown(f"- {r}")

    st.markdown("**Parâmetros usados:**")
    st.json(json.loads(row["params"]))

with detail_right:
    trades = json.loads(row["trades"])
    if trades:
        pnls = [t["pnl_r"] for t in trades]
        equity = pd.Series(pnls).cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            y=equity, mode="lines+markers", name="Equity (R)",
            line=dict(color="#2ca02c" if equity.iloc[-1] > 0 else "#d62728"),
        ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(
            title="Curva de equity (múltiplos de R, normalizado pelo desvio-padrão do spread na entrada)",
            height=350, xaxis_title="Trade nº", yaxis_title="Retorno acumulado (R)",
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Esta estratégia não gerou nenhum trade no período testado — provavelmente os limiares são restritivos demais para este par.")

st.markdown("**Estatísticas completas:**")
stats_cols = {
    "total_trades": "Nº de trades", "win_rate": "Win rate",
    "profit_factor": "Profit factor", "sharpe_per_trade": "Sharpe (por trade)",
    "total_return_r": "Retorno total (R)", "max_drawdown_r": "Drawdown máximo (R)",
    "avg_hold_bars": "Duração média (barras)", "avg_win_r": "Ganho médio (R)",
    "avg_loss_r": "Perda média (R)", "bars_tested": "Barras testadas no total",
}
stats_df = pd.DataFrame({"Métrica": list(stats_cols.values()), "Valor": [row[k] for k in stats_cols]})
st.table(stats_df.set_index("Métrica"))

# ---------------------------------------------------------------------
# Walk-forward (VALID-01) — veredito fold-a-fold + badge real-data
# ---------------------------------------------------------------------
st.divider()
st.markdown("**Walk-forward (out-of-sample):**")

if "wf_passed" not in df.columns:
    # Guard a nível de DataFrame — degrada graciosamente em vez de rebentar
    # se esta base de dados for de antes do plan 01-03 (colunas ainda não
    # existem mesmo depois do init_db() no arranque, ex.: falha de migração).
    st.warning(
        "Esta base de dados não tem as colunas de walk-forward "
        "(`wf_passed`, `wf_fold_results`, `revalidated_on_real_data`) — "
        "provavelmente foi gerada antes do mecanismo de walk-forward existir."
    )
elif pd.isna(row.get("wf_passed")):
    st.info(
        "Esta estratégia ainda não foi revalidada via walk-forward. Corre:\n\n"
        "```\npython src/revalidate_walk_forward.py\n```"
    )
else:
    # A partir de 2026-07-24, wf_fold_results guarda a estrutura COMPLETA
    # de walk_forward_validate() (dict com "fold_results"/"aggregate_stats"),
    # não só a lista de folds — é isso que risk_engine.resolve_kelly_inputs()
    # já esperava desde a Fase 2. Bases de dados mais antigas (antes desta
    # correção) ainda podem ter só a lista nua persistida; tratamos ambas as
    # formas aqui em vez de assumir só a nova (degradar graciosamente).
    wf_parsed = json.loads(row["wf_fold_results"]) if row.get("wf_fold_results") else []
    if isinstance(wf_parsed, dict):
        fold_results = wf_parsed.get("fold_results", [])
        aggregate_stats = wf_parsed.get("aggregate_stats")
    else:
        fold_results = wf_parsed
        aggregate_stats = None
    is_real = row.get("revalidated_on_real_data") == 1

    if is_real:
        st.success("Revalidated on real data (walk-forward OOS)")
    else:
        st.warning(
            "Walk-forward ran on synthetic/fallback data only — does NOT count as VALID-01 revalidation"
        )

    if row["wf_passed"] == 1:
        st.success("✅ Walk-forward APROVADO — todos os folds passaram o gate relaxado E o agregado passou o gate completo.")
    else:
        st.error("❌ Walk-forward REPROVADO — pelo menos um fold ou o agregado falhou o gate.")

    if aggregate_stats:
        st.markdown("**Estatísticas agregadas out-of-sample** (usadas pelo dimensionamento Kelly em produção, não as in-sample da geração original):")
        agg_cols = st.columns(4)
        agg_cols[0].metric("Nº trades (todos os folds)", aggregate_stats["total_trades"])
        agg_cols[1].metric("Win rate", f"{aggregate_stats['win_rate']:.1%}")
        agg_cols[2].metric("Profit factor", f"{aggregate_stats['profit_factor']:.2f}")
        agg_cols[3].metric("Retorno total (R)", f"{aggregate_stats['total_return_r']:.2f}")

    if fold_results:
        fold_rows = []
        for f in fold_results:
            fold_rows.append({
                "fold": f["fold"],
                "total_trades": f["stats"]["total_trades"],
                "total_return_r": f["stats"]["total_return_r"],
                "passed": f["passed"],
            })
        fold_df = pd.DataFrame(fold_rows)
        fold_df_display = fold_df.rename(columns={
            "fold": "Fold", "total_trades": "Nº trades",
            "total_return_r": "Retorno líquido de custos (R)", "passed": "Passou",
        })
        fold_df_display["Passou"] = fold_df_display["Passou"].map({True: "✅", False: "❌"})
        st.dataframe(fold_df_display, use_container_width=True, hide_index=True)

        fold_fig = go.Figure()
        fold_fig.add_trace(go.Bar(
            x=fold_df["fold"], y=fold_df["total_return_r"],
            marker_color=[("#2ca02c" if p else "#d62728") for p in fold_df["passed"]],
            name="Retorno por fold (R)",
        ))
        fold_fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fold_fig.update_layout(
            title="Retorno out-of-sample por fold (líquido de custos)",
            height=350, xaxis_title="Fold", yaxis_title="Retorno (R)",
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fold_fig, use_container_width=True)

if trades:
    st.markdown("**Histórico de trades:**")
    trades_df = pd.DataFrame(trades).rename(columns={
        "entry_bar": "Barra entrada", "exit_bar": "Barra saída",
        "bars_held": "Duração (barras)", "direction": "Direção",
        "pnl_r": "PnL (R)", "exit_reason": "Motivo de saída",
    })
    trades_df["Direção"] = trades_df["Direção"].map({1: "Long spread", -1: "Short spread"})
    trades_df["Motivo de saída"] = trades_df["Motivo de saída"].map({
        "reversion": "Reversão (alvo atingido)",
        "correlation_breakdown": "Quebra de correlação",
        "time_stop": "Stop de tempo",
    })
    st.dataframe(trades_df, use_container_width=True, hide_index=True)
