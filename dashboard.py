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
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
# hedge_engine.py (ao contrário de backtest_engine/strategy_registry/
# trade_ledger, que usam imports bare) importa os seus próprios módulos
# irmãos como `from src.backtest_engine import ...` — precisa da RAIZ do
# repo também no sys.path (não só src/) para "src" resolver como pacote.
sys.path.insert(0, REPO_ROOT)
from backtest_engine import PROFIT_FACTOR_NO_LOSSES_SENTINEL  # noqa: E402
from feed_health import read_feed_health  # noqa: E402
from hedge_engine import load_eligible_strategies, select_strategy_for_pair  # noqa: E402
from process_control import get_pid, is_running, start_process, stop_process  # noqa: E402
from strategy_registry import init_db, list_strategies  # noqa: E402
from trade_ledger import list_all_trades  # noqa: E402

st.set_page_config(page_title="Forex AI — Strategy Lab", layout="wide", page_icon="🔬")

# ---------------------------------------------------------------------
# Gate de password — ANTES de qualquer outra coisa correr (nenhum dado é
# carregado, nenhuma query à base de dados é feita, antes de autenticar).
# Password vem de DASHBOARD_PASSWORD (variável de ambiente, nunca
# hardcoded no ficheiro nem escrita em disco — mesma disciplina de
# CLAUDE.md "Credenciais - NUNCA versionar" para .env/*credentials*).
# Sem essa variável definida, o dashboard continua a funcionar (não
# bloqueia quem só o usa localmente), mas mostra um aviso persistente —
# nunca falha silenciosamente para "sem proteção" sem o utilizador saber.
# ---------------------------------------------------------------------
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

if DASHBOARD_PASSWORD:
    if not st.session_state.get("authenticated", False):
        st.title("🔒 Forex AI — Strategy Lab")
        st.caption("Este dashboard está protegido por password (DASHBOARD_PASSWORD).")
        with st.form("login_form"):
            pwd_input = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Entrar")
        if submitted:
            if pwd_input == DASHBOARD_PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Password incorreta.")
        st.stop()
else:
    st.warning(
        "⚠️ DASHBOARD_PASSWORD não está definida — este dashboard está a correr SEM proteção "
        "por password. Define a variável de ambiente antes de expor isto fora da tua própria "
        "máquina (ex.: numa VPS) — `iniciar_dashboard.bat` já pergunta por ela."
    )

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

CTRL_DATA_PIPELINE = "data_pipeline"
CTRL_CONTINUOUS_SEARCH = "continuous_search"
CTRL_LIVE_LOOP = "live_loop"
CTRL_WATCHDOG = "watchdog"

# ---------------------------------------------------------------------
# ⚙️ Estado do sistema — resposta direta a "não sei se está a correr ou
# não, nem se há proteção contra duplicação". Um resumo único e
# inequívoco de tudo o que está vivo agora, ANTES de qualquer outra
# secção — nada aqui decide nada, só lê o mesmo `is_running`/`get_pid`
# que o Painel de controlo já usa (uma só fonte de verdade).
# ---------------------------------------------------------------------
st.subheader("⚙️ Estado do sistema")
status_cols = st.columns(4)
for status_col, (proc_name, proc_label) in zip(status_cols, [
    (CTRL_DATA_PIPELINE, "🔄 Atualizar dados MT5"),
    (CTRL_CONTINUOUS_SEARCH, "🧬 Busca contínua"),
    (CTRL_LIVE_LOOP, "📡 Ligar ao vivo"),
    (CTRL_WATCHDOG, "🐕 Vigilante"),
]):
    with status_col:
        if is_running(proc_name):
            st.success(f"**{proc_label}**\n\n🟢 A correr (PID {get_pid(proc_name)})")
        else:
            st.info(f"**{proc_label}**\n\n⚪ Parado")
st.caption(
    "🔒 **Proteção contra duplicação**: cada processo só pode ter UMA instância ativa "
    "por vez — `process_control.start_process` recusa arrancar um segundo com o mesmo "
    "nome enquanto o anterior estiver vivo. Nunca correm dois em paralelo por engano.\n\n"
    "♻️ **Reinício automático**: enquanto o 🐕 Vigilante estiver ligado, qualquer processo "
    "que morra sozinho (crash, reinício da VPS) volta a arrancar automaticamente — só "
    "para mesmo quando clicares em \"Parar\" explicitamente."
)
st.divider()

# ---------------------------------------------------------------------
# 🎛️ Painel de controlo — arranca/para os processos de longa duração a
# partir de um clique, em vez de exigir comandos de terminal. Cada botão
# abre a SUA PRÓPRIA janela de consola (process_control.start_process) —
# visível, com logs ao vivo — nunca corre nada escondido nem de forma
# bloqueante dentro do próprio dashboard.
# ---------------------------------------------------------------------
st.subheader("🎛️ Painel de controlo")


def _start_with_feedback(name: str, cmd: list[str], label: str) -> None:
    """Arranca um processo do painel de controlo com feedback GARANTIDO
    — nunca falha em silêncio. `process_control.start_process` pode
    levantar (ex.: RuntimeError se já houver um "name" vivo, ou um erro
    do SO se o comando/pasta for inválido); sem este wrapper, essa
    exceção rebentava o script inteiro do Streamlit com um traceback
    cru, o que pareceu ao utilizador "o botão não faz nada" em vez de um
    erro claro (relatado 2026-08 na VPS). Em sucesso, confirma com
    `st.toast` (visível por alguns segundos, sobrevive ao st.rerun()
    seguinte) para nunca ficar ambíguo se o clique teve efeito."""
    try:
        pid = start_process(name, cmd, cwd=REPO_ROOT)
    except Exception as exc:
        st.error(f"❌ Falha ao iniciar '{label}': {exc}")
        return
    st.toast(f"✅ '{label}' iniciado (PID {pid})", icon="✅")
    st.rerun()


ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)

with ctrl1:
    with st.container(border=True):
        st.markdown("**🔄 Atualizar dados do MT5**")
        st.caption("Corre `data_pipeline.py --mode mt5` — busca preços novos e recalcula pares cointegrados.")
        st.warning("⚠️ Confirma que o terminal MT5 já está aberto e com sessão iniciada antes de clicares.")
        if is_running(CTRL_DATA_PIPELINE):
            st.success(f"🟢 A correr (PID {get_pid(CTRL_DATA_PIPELINE)})")
            if st.button("⏹ Parar", key="stop_data_pipeline"):
                stop_process(CTRL_DATA_PIPELINE)
                st.rerun()
        else:
            if st.button("▶️ Atualizar agora", key="start_data_pipeline"):
                _start_with_feedback(
                    CTRL_DATA_PIPELINE,
                    [sys.executable, os.path.join("src", "data_pipeline.py"), "--mode", "mt5"],
                    "Atualizar dados do MT5",
                )

with ctrl2:
    with st.container(border=True):
        st.markdown("**🧬 Busca contínua de estratégias**")
        st.caption(
            "Corre `run_continuous_strategy_search.py` — gera, testa e revalida estratégias sem "
            "parar, e agora também atualiza os dados do MT5 sozinha (a cada hora) — nunca fica "
            "presa a olhar sempre para os mesmos pares."
        )
        st.warning("⚠️ Por causa da atualização automática, precisa do terminal MT5 aberto e com sessão iniciada.")
        if is_running(CTRL_CONTINUOUS_SEARCH):
            st.success(f"🟢 A correr (PID {get_pid(CTRL_CONTINUOUS_SEARCH)})")
            if st.button("⏹ Parar", key="stop_continuous_search"):
                stop_process(CTRL_CONTINUOUS_SEARCH)
                st.rerun()
        else:
            if st.button("▶️ Iniciar busca contínua", key="start_continuous_search"):
                _start_with_feedback(
                    CTRL_CONTINUOUS_SEARCH,
                    [sys.executable, os.path.join("scripts", "run_continuous_strategy_search.py"),
                     "--confirm-real-data", "--auto-refresh-mt5"],
                    "Busca contínua de estratégias",
                )

with ctrl3:
    with st.container(border=True):
        st.markdown("**📡 Ligar ao MT5 ao vivo**")
        st.caption("Corre `run_live_hedge_loop.py` — negoceia (demo/real) com as estratégias já elegíveis.")
        st.warning("⚠️ Precisa do terminal MT5 aberto E do Expert Advisor já anexado a um gráfico.")

        n_eligible = len(load_eligible_strategies(DB_PATH)) if os.path.exists(DB_PATH) else 0

        if is_running(CTRL_LIVE_LOOP):
            st.success(f"🟢 A correr (PID {get_pid(CTRL_LIVE_LOOP)})")

            # 🔌 Saúde da ligação MT5 (hedge_engine.live_feed) — lido do
            # ficheiro que o próprio loop vai atualizando a cada ciclo de
            # polling (src/feed_health.py). Reconexão é sempre tentada
            # indefinidamente do lado do processo; isto é só o alerta
            # visual pedido (sem Telegram/email).
            health = read_feed_health()
            if health is None:
                st.caption("🔌 Ligação MT5: ainda sem dados de saúde (a aguardar o primeiro ciclo).")
            elif health["status"] == "ok":
                st.caption(f"🔌 Ligação MT5: 🟢 OK (última confirmação: {health['updated_at']})")
            else:
                st.error(
                    f"🔌 Ligação MT5: 🔴 {health['consecutive_failures']} falha(s) consecutiva(s) — "
                    f"a tentar reconectar automaticamente, sem limite de tentativas. "
                    f"Último erro: {health.get('last_error') or 'n/a'}"
                )

            if st.button("⏹ Parar", key="stop_live_loop"):
                stop_process(CTRL_LIVE_LOOP)
                st.rerun()
        elif n_eligible == 0:
            st.error(
                "🔒 Sem estratégias elegíveis (0) — nada para negociar. "
                "A busca contínua tem de encontrar e validar pelo menos uma primeiro."
            )
            st.button("▶️ Ligar ao vivo", key="start_live_loop_disabled", disabled=True)
        else:
            st.info(f"{n_eligible} estratégia(s) elegível(is) — pronta(s) a negociar.")
            confirm_demo = st.checkbox("Confirmo que esta é uma conta DEMO", key="confirm_demo_account")
            if not confirm_demo:
                st.caption("🔒 O botão só desbloqueia depois de marcares a checkbox acima.")
            if st.button("▶️ Ligar ao vivo", key="start_live_loop", disabled=not confirm_demo):
                _start_with_feedback(
                    CTRL_LIVE_LOOP,
                    [sys.executable, os.path.join("scripts", "run_live_hedge_loop.py"),
                     "--reload-every-bars", "50", "--selection-policy", "best_oos_profit_factor"],
                    "Ligar ao MT5 ao vivo",
                )

with ctrl4:
    with st.container(border=True):
        st.markdown("**🐕 Vigilante**")
        st.caption(
            "Corre `watchdog.py` — reinicia sozinho qualquer processo acima que morra "
            "inesperadamente (crash, reinício da VPS). Nunca decide arrancar nada por "
            "conta própria; só devolve à vida o que já foi pedido e ainda não foi parado."
        )
        st.info("💡 Sem pré-requisitos — não precisa do MT5 nem de mais nada para correr.")
        if is_running(CTRL_WATCHDOG):
            st.success(f"🟢 A correr (PID {get_pid(CTRL_WATCHDOG)})")
            if st.button("⏹ Parar", key="stop_watchdog"):
                stop_process(CTRL_WATCHDOG)
                st.rerun()
        else:
            if st.button("▶️ Ligar vigilante", key="start_watchdog"):
                _start_with_feedback(
                    CTRL_WATCHDOG,
                    [sys.executable, os.path.join("scripts", "watchdog.py")],
                    "Vigilante",
                )

st.divider()

# ---------------------------------------------------------------------
# 🏆 Estratégias elegíveis (campeãs) — pedido explícito do utilizador
# (2026-08): "temos estratégias que já funcionam mas eu mal consigo
# ver". Mostra TODAS as `load_eligible_strategies()` (a mesma fonte que
# o Painel de controlo já usa para decidir se "Ligar ao vivo"
# desbloqueia), com a estratégia CAMPEÃ de cada par destacada — exatamente
# a que `hedge_engine.select_strategy_for_pair` resolveria agora, a
# MESMA lógica que `run_live_hedge_loop.py` usa em produção. Nunca
# inventa valores em dinheiro — fica em "R", a unidade já estabelecida
# no resto do projeto (não há execução real ainda para converter).
# ---------------------------------------------------------------------
st.subheader("🏆 Estratégias elegíveis (campeãs)")

eligible_strategies = load_eligible_strategies(DB_PATH) if os.path.exists(DB_PATH) else []

if not eligible_strategies:
    st.info(
        "Ainda 0 estratégias elegíveis — a busca contínua tem de encontrar e validar "
        "(in-sample + walk-forward out-of-sample + dados reais) pelo menos uma primeiro."
    )
else:
    def _oos_stats(record: dict) -> dict:
        """Estatísticas out-of-sample (walk-forward) se existirem — nunca
        as in-sample da geração original, que sobrestimam sistematicamente
        o edge real (mesma disciplina de risk_engine.resolve_kelly_inputs)."""
        raw = record.get("wf_fold_results")
        if raw:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict) and parsed.get("aggregate_stats"):
                return parsed["aggregate_stats"]
        return {
            "total_trades": record.get("total_trades", 0) or 0,
            "win_rate": record.get("win_rate", 0.0) or 0.0,
            "profit_factor": record.get("profit_factor", 0.0) or 0.0,
            "total_return_r": record.get("total_return_r", 0.0) or 0.0,
        }

    unique_pairs = sorted({(s["pair_a"], s["pair_b"]) for s in eligible_strategies})
    champions = {}
    for pair_a, pair_b in unique_pairs:
        champion = select_strategy_for_pair(eligible_strategies, pair_a, pair_b)
        if champion is not None:
            champions[(pair_a, pair_b)] = champion

    champion_oos = {pair: _oos_stats(rec) for pair, rec in champions.items()}
    total_return_champions = sum(s["total_return_r"] for s in champion_oos.values())
    total_trades_champions = sum(s["total_trades"] for s in champion_oos.values())
    total_wins_champions = sum(round(s["win_rate"] * s["total_trades"]) for s in champion_oos.values())
    total_losses_champions = total_trades_champions - total_wins_champions

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Estratégias elegíveis", len(eligible_strategies))
    m2.metric("Pares com campeão", len(champions))
    m3.metric("Retorno total campeões (R, OOS)", f"{total_return_champions:.2f}")
    m4.metric("Trades ganhos (campeões, OOS)", int(total_wins_champions))
    m5.metric("Trades perdidos (campeões, OOS)", int(total_losses_champions))
    st.caption(
        "Métricas dos campeões são out-of-sample (walk-forward) quando disponíveis — "
        "nunca in-sample, que sobrestima o edge real. Unidade 'R' = múltiplos do "
        "desvio-padrão do spread na entrada, a mesma usada em todo o projeto — ainda "
        "não há execução real, por isso não existe valor em dinheiro real para mostrar."
    )

    rows = []
    for s in eligible_strategies:
        pair = (s["pair_a"], s["pair_b"])
        is_champion = champions.get(pair, {}).get("id") == s["id"]
        oos = _oos_stats(s)
        rows.append({
            "🏆": "🏆" if is_champion else "",
            "id": s["id"], "Par A": s["pair_a"], "Par B": s["pair_b"],
            "Tipo": s.get("strategy_type") or "zscore",
            "Nº trades (OOS)": oos["total_trades"],
            "Win rate (OOS)": oos["win_rate"],
            "Profit factor (OOS)": oos["profit_factor"],
            "Retorno (R, OOS)": oos["total_return_r"],
        })
    eligible_df = pd.DataFrame(rows).sort_values(["🏆", "Retorno (R, OOS)"], ascending=[False, False])
    st.dataframe(
        eligible_df, use_container_width=True, hide_index=True,
        height=min(450, 60 + 35 * len(eligible_df)),
    )

st.divider()

df = load(DB_PATH)

# ---------------------------------------------------------------------
# Painel "Ao vivo" — confirma visualmente que uma busca em segundo plano
# (scripts/run_continuous_strategy_search.py ou strategy_generator.py
# --evolutionary) está mesmo a processar, sem precisar de olhar para o
# terminal: última atividade, ritmo recente, % de aprovadas e total de
# trades ganhos/perdidos numa janela recente. Nunca decide nada — é só
# leitura do mesmo `df` já carregado (Don't Hand-Roll).
# ---------------------------------------------------------------------
RECENT_WINDOW_MINUTES = 5

live_left, live_right = st.columns([1, 3])
with live_left:
    auto_refresh = st.checkbox(
        "🔄 Atualizar automaticamente (5s)", value=False, key="live_autorefresh",
        help="Liga isto enquanto uma busca estiver a correr em segundo plano, para veres "
             "as estratégias a aparecer em tempo real.",
    )
with live_right:
    st.caption(
        "Painel ao vivo — confirma se `run_continuous_strategy_search.py` (ou "
        "`strategy_generator.py --evolutionary`) está mesmo a testar estratégias agora."
    )

with st.container(border=True):
    if df.empty:
        st.info("⚪ Sem dados ainda — nenhuma estratégia foi testada nesta base de dados.")
    else:
        created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
        now = pd.Timestamp.now(tz="UTC")
        last_created = created.max()
        seconds_since_last = (now - last_created).total_seconds() if pd.notna(last_created) else None

        recent_mask = created >= (now - pd.Timedelta(minutes=RECENT_WINDOW_MINUTES))
        df_recent = df[recent_mask]
        n_recent = len(df_recent)
        n_recent_passed = int((df_recent["status"] == "passed").sum()) if n_recent else 0
        pct_recent = (n_recent_passed / n_recent * 100) if n_recent else 0.0
        # win/loss de TRADES (não de estratégias) agregados na janela recente —
        # win_rate * total_trades arredondado dá o nº de trades ganhos por
        # estratégia (mesma reconstrução que compute_stats já garante ser
        # consistente internamente, nunca recalculado do zero aqui).
        wins_per_row = (df_recent["win_rate"] * df_recent["total_trades"]).round() if n_recent else pd.Series(dtype=float)
        wins_recent = int(wins_per_row.sum()) if n_recent else 0
        losses_recent = int((df_recent["total_trades"].sum() - wins_recent)) if n_recent else 0

        status_col, m1, m2, m3, m4 = st.columns([1.3, 1, 1, 1, 1])
        with status_col:
            if seconds_since_last is None:
                st.info("⚪ Sem dados ainda")
            elif seconds_since_last < 120:
                st.success(f"🟢 A processar — última há {int(seconds_since_last)}s")
            elif seconds_since_last < 600:
                st.warning(f"🟡 Sem atividade há {int(seconds_since_last / 60)} min")
            else:
                st.error(f"🔴 Parado — última atividade há {int(seconds_since_last / 60)} min")
        m1.metric(f"Testadas (últimos {RECENT_WINDOW_MINUTES} min)", n_recent)
        m2.metric("% aprovadas (recente)", f"{pct_recent:.1f}%")
        m3.metric("Trades ganhos (recente)", wins_recent)
        m4.metric("Trades perdidos (recente)", losses_recent)

        n_available = len(df)
        min_n = min(10, n_available)
        show_n = st.number_input(
            "Mostrar últimas N estratégias testadas", min_value=min_n, max_value=n_available,
            value=max(min(100, n_available), min_n), step=10, key="ao_vivo_show_n",
            help="Sem limite escondido — sobe até ao total de estratégias já testadas.",
        )
        last_n = df.sort_values("created_at", ascending=False).head(int(show_n)).copy()
        if "strategy_type" not in last_n.columns:
            last_n["strategy_type"] = None
        last_n["strategy_type"] = last_n["strategy_type"].fillna("zscore")
        last_n["status"] = last_n["status"].map({"passed": "✅", "failed": "❌"})
        st.dataframe(
            last_n[[
                "created_at", "pair_a", "pair_b", "strategy_type", "generation",
                "status", "total_trades", "win_rate", "profit_factor",
            ]].rename(columns={
                "created_at": "Quando", "pair_a": "Par A", "pair_b": "Par B",
                "strategy_type": "Tipo", "generation": "Geração", "status": "Estado",
                "total_trades": "Nº trades", "win_rate": "Win rate", "profit_factor": "Profit factor",
            }),
            use_container_width=True, hide_index=True, height=280,
        )

# ---------------------------------------------------------------------
# Operações reais — output/live_trades.db (src/trade_ledger.py), gravado
# só quando scripts/run_live_hedge_loop.py está de facto a correr contra
# o MT5. Distinto de tudo acima (que é só o LABORATÓRIO — candidatos
# testados no histórico); isto é o que realmente aconteceu na conta.
# ---------------------------------------------------------------------
st.divider()
st.subheader("📈 Operações (conta ao vivo/demo)")

ledger_db_path = os.path.join(os.path.dirname(DB_PATH) or "output", "live_trades.db")
trades_live = list_all_trades(ledger_db_path)

if not trades_live:
    st.info(
        f"Ainda não há nenhuma operação registada em `{ledger_db_path}`. Isto só passa a "
        "ter dados depois de `python scripts/run_live_hedge_loop.py` estar a correr com o "
        "MT5 ligado — enquanto isso não acontecer, é normal e esperado estar vazio."
    )
else:
    trades_df_live = pd.DataFrame(trades_live)
    open_trades = trades_df_live[trades_df_live["status"] == "open"]
    closed_trades = trades_df_live[trades_df_live["status"] == "closed"]

    oc1, oc2, oc3, oc4 = st.columns(4)
    oc1.metric("Posições abertas agora", len(open_trades))
    oc2.metric("Operações fechadas (histórico)", len(closed_trades))
    if len(closed_trades):
        oc3.metric("Win rate (real)", f"{(closed_trades['pnl_r'] > 0).mean():.1%}")
        oc4.metric("PnL total (R, proxy 1 perna)", f"{closed_trades['pnl_r'].sum():.2f}")
    else:
        oc3.metric("Win rate (real)", "n/a")
        oc4.metric("PnL total (R, proxy 1 perna)", "n/a")

    if len(open_trades):
        st.markdown("**Posições abertas agora:**")
        open_display = open_trades[["pair_a", "pair_b", "direction", "entry_timestamp", "entry_price_a"]].copy()
        open_display["direction"] = open_display["direction"].map({1: "Long spread", -1: "Short spread"})
        st.dataframe(
            open_display.rename(columns={
                "pair_a": "Par A", "pair_b": "Par B", "direction": "Direção",
                "entry_timestamp": "Entrada em", "entry_price_a": "Preço entrada (perna A)",
            }),
            use_container_width=True, hide_index=True,
        )

    if len(closed_trades):
        st.markdown("**Operações fechadas:**")
        n_closed_available = len(closed_trades)
        min_closed_n = min(20, n_closed_available)
        show_closed_n = st.number_input(
            "Mostrar últimas N operações fechadas", min_value=min_closed_n, max_value=n_closed_available,
            value=min_closed_n, step=10, key="operacoes_show_n",
            help="Sem limite escondido — sobe até ao total de operações já fechadas.",
        )
        closed_display = closed_trades.head(int(show_closed_n))[[
            "pair_a", "pair_b", "direction", "entry_timestamp", "exit_timestamp", "exit_reason", "pnl_r",
        ]].copy()
        closed_display["direction"] = closed_display["direction"].map({1: "Long spread", -1: "Short spread"})
        st.dataframe(
            closed_display.rename(columns={
                "pair_a": "Par A", "pair_b": "Par B", "direction": "Direção",
                "entry_timestamp": "Entrada em", "exit_timestamp": "Saída em",
                "exit_reason": "Motivo de saída", "pnl_r": "PnL (R)",
            }),
            use_container_width=True, hide_index=True,
        )

if auto_refresh:
    time.sleep(5)
    st.rerun()

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

# ---------------------------------------------------------------------
# Lógica da estratégia — z-score + bandas de entrada/saída + pontos reais
# ---------------------------------------------------------------------
st.divider()
st.markdown(
    "**Lógica da estratégia** — z-score do spread ao longo do tempo, com as bandas de "
    "entrada/saída e os pontos reais de entrada/saída desta estratégia:"
)

output_dir = os.path.dirname(DB_PATH) or "output"
price_path_a = os.path.join(output_dir, f"features_{row['pair_a']}.parquet")
price_path_b = os.path.join(output_dir, f"features_{row['pair_b']}.parquet")

if not (os.path.exists(price_path_a) and os.path.exists(price_path_b)):
    st.info(
        f"Não encontrei `{price_path_a}` / `{price_path_b}` — corre `python src/data_pipeline.py` "
        "para os gerar antes de veres a lógica desta estratégia."
    )
else:
    from strategy_variants import compute_signal_series_for_type  # noqa: E402

    raw_strategy_type = row.get("strategy_type")
    strategy_type = "zscore" if pd.isna(raw_strategy_type) else raw_strategy_type
    params = json.loads(row["params"])

    try:
        price_a_series = pd.read_parquet(price_path_a)["close"]
        price_b_series = pd.read_parquet(price_path_b)["close"]
        series = compute_signal_series_for_type(strategy_type, price_a_series, price_b_series, params)
    except Exception as exc:
        st.warning(f"Não consegui recalcular a série de sinal desta estratégia ({strategy_type}): {exc}")
        series = None

    if series is not None:
        zscore = series["zscore"]
        n_common = len(zscore)

        logic_fig = go.Figure()
        logic_fig.add_trace(go.Scatter(
            y=zscore, mode="lines", name="Z-score do spread",
            line=dict(color="#1f77b4", width=1),
        ))

        # Bandas tracejadas — asymmetric_bands tem limiares distintos por
        # lado (entry_threshold_long/entry_threshold_short); as restantes
        # variantes são simétricas à volta de zero (entry_threshold único).
        if "entry_threshold_long" in params or "entry_threshold_short" in params:
            entry_short = params.get("entry_threshold_short")
            entry_long = params.get("entry_threshold_long")
            if entry_short is not None:
                logic_fig.add_hline(y=entry_short, line_dash="dash", line_color="#d62728",
                                     annotation_text="entrada (short)")
            if entry_long is not None:
                logic_fig.add_hline(y=-entry_long, line_dash="dash", line_color="#2ca02c",
                                     annotation_text="entrada (long)")
        else:
            entry_threshold = params.get("entry_threshold")
            if entry_threshold is not None:
                logic_fig.add_hline(y=entry_threshold, line_dash="dash", line_color="#d62728",
                                     annotation_text="entrada")
                logic_fig.add_hline(y=-entry_threshold, line_dash="dash", line_color="#2ca02c",
                                     annotation_text="entrada")
        exit_threshold = params.get("exit_threshold")
        if exit_threshold is not None:
            logic_fig.add_hline(y=exit_threshold, line_dash="dot", line_color="gray",
                                 annotation_text="saída (reversão)")
            logic_fig.add_hline(y=-exit_threshold, line_dash="dot", line_color="gray")
        logic_fig.add_hline(y=0, line_color="lightgray")

        # Pontos reais de entrada/saída, a partir dos trades já guardados
        # (mesma lista usada na curva de equity e na tabela de trades
        # mais abaixo) — nunca recalculados, só posicionados sobre a série.
        entries_long = [t["entry_bar"] for t in trades if t["direction"] == 1 and t["entry_bar"] < n_common]
        entries_short = [t["entry_bar"] for t in trades if t["direction"] == -1 and t["entry_bar"] < n_common]
        exits = [t["exit_bar"] for t in trades if t["exit_bar"] < n_common]
        exit_texts = [t["exit_reason"] for t in trades if t["exit_bar"] < n_common]

        if entries_long:
            logic_fig.add_trace(go.Scatter(
                x=entries_long, y=[zscore[i] for i in entries_long],
                mode="markers", name="Entrada (long spread)",
                marker=dict(symbol="triangle-up", size=11, color="#2ca02c"),
            ))
        if entries_short:
            logic_fig.add_trace(go.Scatter(
                x=entries_short, y=[zscore[i] for i in entries_short],
                mode="markers", name="Entrada (short spread)",
                marker=dict(symbol="triangle-down", size=11, color="#d62728"),
            ))
        if exits:
            logic_fig.add_trace(go.Scatter(
                x=exits, y=[zscore[i] for i in exits],
                mode="markers", name="Saída", text=exit_texts, hoverinfo="text+x+y",
                marker=dict(symbol="x", size=10, color="black"),
            ))

        logic_fig.update_layout(
            title=f"Z-score do spread — {row['pair_a']} / {row['pair_b']} ({strategy_type})",
            height=420, xaxis_title="Barra", yaxis_title="Z-score",
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(logic_fig, use_container_width=True)
        st.caption(
            "⚠️ Os pontos de entrada/saída assumem que `output/features_*.parquet` não mudou "
            "desde que esta estratégia foi testada — se os dados foram atualizados entretanto "
            "(nova corrida de `data_pipeline.py`), as posições dos pontos podem já não "
            "corresponder exatamente às barras originais."
        )

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
