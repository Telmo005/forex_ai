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
from strategy_registry import list_strategies  # noqa: E402

st.set_page_config(page_title="Forex AI — Strategy Lab", layout="wide", page_icon="🔬")

DB_PATH = os.environ.get("STRATEGY_DB", os.path.join("output", "strategy_lab.db"))


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
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Estratégias testadas", len(df))
c2.metric("Aprovadas", int((df["status"] == "passed").sum()))
c3.metric("Reprovadas", int((df["status"] == "failed").sum()))
best_pf = df["profit_factor"].max()
c4.metric("Melhor profit factor (net-of-cost)", f"{best_pf:.2f}")
c5.metric("Gerações executadas", int(df["generation"].max()) + 1)

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
