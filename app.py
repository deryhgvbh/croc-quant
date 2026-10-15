"""鳄鱼派量化看板 — Streamlit 本地软件。

页面：
- 🐊 今日信号：ETF池状态一览、当前该持有什么、轮动建议
- 📈 行情与鳄鱼线：K线 + 颚/齿/唇三线 + AO + MACD + 信号标注
- 🧪 回测：单标的择时（可拆信号）与 ETF池轮动回测

运行：streamlit run app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from croc.data import get_etf, get_pool, prices_matrix
from croc.indicators import alligator, ao, macd
from croc.strategy import position, sub_signals
from croc.backtest import run_single, run_portfolio, metrics, trade_stats
from croc.rotation import rotation_weights
from croc.universe import DEFAULT_POOL, EXTRA_POOL, BOND_ETF

st.set_page_config(page_title="鳄鱼派量化", page_icon="🐊", layout="wide")

NAMES = {**DEFAULT_POOL, **EXTRA_POOL, **BOND_ETF}


@st.cache_data(ttl=3600, show_spinner="拉取行情数据…")
def load(symbols, refresh=False):
    return get_pool(list(symbols), refresh=refresh)


def state_label(row):
    if row["alligator"] == 1:
        return "🟢 饥饿(多头)"
    if row["alligator"] == -1:
        return "🔴 吃饱(空头)"
    return "⚪ 睡觉(缠绕)"


def fmt_sig(v):
    return {1: "🟢+1", -1: "🔴-1"}.get(int(v), "⚪0")


# ---------- 侧边栏：标的池与参数 ----------
st.sidebar.title("🐊 鳄鱼派量化")
page = st.sidebar.radio("页面", ["今日信号", "行情与鳄鱼线", "回测"])

st.sidebar.markdown("---")
st.sidebar.subheader("ETF 池")
pool_opts = {**DEFAULT_POOL, **EXTRA_POOL}
picked = st.sidebar.multiselect(
    "轮动池（宽基）",
    options=list(pool_opts.keys()),
    default=list(DEFAULT_POOL.keys()),
    format_func=lambda s: f"{s} {pool_opts[s]}",
)
bond_sym = st.sidebar.selectbox(
    "固定收益腿", list(BOND_ETF.keys()),
    format_func=lambda s: f"{s} {BOND_ETF[s]}")
st.sidebar.markdown("---")
st.sidebar.subheader("信号参数")
zero_band = st.sidebar.slider("MACD 零轴带宽(‰)", 0.0, 10.0, 2.0, 0.5) / 1000
exec_price = st.sidebar.selectbox(
    "成交假设", ["next_open", "next_close", "close"],
    format_func=lambda x: {"next_open": "T+1 开盘价", "next_close": "T+1 收盘价",
                           "close": "T 日收盘价(研报口径)"}[x])
fee_bp = st.sidebar.slider("单边手续费(‰)", 0.0, 30.0, 3.0, 0.5)
fee = fee_bp / 10000
refresh = st.sidebar.button("强制刷新数据")


# ---------- 页1：今日信号 ----------
if page == "今日信号":
    st.title("🐊 今日信号 · ETF池状态")
    st.caption("鳄鱼派：大部分时间空仓趴着(配置国债ETF)，趋势明确才出击。")

    syms = picked + [bond_sym]
    data = load(syms, refresh)
    if not data:
        st.error("数据拉取失败")
        st.stop()

    rows = []
    for s in syms:
        df = data.get(s)
        if df is None or not len(df):
            continue
        ss = sub_signals(df, zero_band).iloc[-1]
        pos_now = position(df, zero_band=zero_band).iloc[-1]
        rows.append({
            "代码": s, "名称": NAMES.get(s, s),
            "日期": df.index[-1].date(),
            "收盘": round(df["close"].iloc[-1], 3),
            "鳄鱼状态": state_label(ss),
            "Alligator": fmt_sig(ss["alligator"]),
            "AO": fmt_sig(ss["ao"]),
            "分形": fmt_sig(ss["fractal"]),
            "MACD": fmt_sig(ss["macd"]),
            "当前持仓": "✅ 持有" if pos_now > 0 else "— 空仓",
        })
    tbl = pd.DataFrame(rows)
    st.dataframe(tbl, use_container_width=True, hide_index=True)

    st.subheader("📋 今日应持仓")
    mode = st.radio("轮动方式", ["equal", "top"], horizontal=True,
                    format_func=lambda m: {"equal": "看多者等权", "top": "最强动量单吊"}[m])
    w = rotation_weights({s: data[s] for s in picked if s in data},
                         bond_symbol=bond_sym, mode=mode, zero_band=zero_band)
    last_w = w.iloc[-1]
    hold = last_w[last_w > 0]
    if len(hold) == 0:
        st.info("今日无任何持仓信号")
    else:
        for s, weight in hold.items():
            st.success(f"**{s} {NAMES.get(s, s)}** — 权重 {weight:.0%}")

    with st.expander("近10日权重变化"):
        st.dataframe(w.tail(10).round(2), use_container_width=True)


# ---------- 页2：行情与鳄鱼线 ----------
elif page == "行情与鳄鱼线":
    st.title("📈 行情与鳄鱼线")
    sym = st.selectbox("标的", list(dict.fromkeys(picked + [bond_sym])),
                       format_func=lambda s: f"{s} {NAMES.get(s, s)}")
    days = st.slider("显示最近N个交易日", 60, 1000, 250)
    data = load([sym], refresh)
    df = data.get(sym)
    if df is None:
        st.error("数据拉取失败")
        st.stop()

    d = df.tail(days).copy()
    a = alligator(d)
    ao_v = ao(d)
    diff, dea, hist = macd(d["close"])
    ss = sub_signals(d, zero_band)
    pos = position(d, zero_band=zero_band)

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03,
                        subplot_titles=(f"{sym} {NAMES.get(sym, sym)}", "AO", "MACD"))
    fig.add_trace(go.Candlestick(x=d.index, open=d["open"], high=d["high"],
                                 low=d["low"], close=d["close"], name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=a["jaw"], name="颚(蓝13/8)",
                             line=dict(color="#1f77b4", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=a["teeth"], name="齿(红8/5)",
                             line=dict(color="#d62728", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=a["lips"], name="唇(绿5/3)",
                             line=dict(color="#2ca02c", width=1.5)), row=1, col=1)
    # 持仓区间底色
    in_pos = pos > 0
    seg_start = None
    for i, v in enumerate(in_pos):
        if v and seg_start is None:
            seg_start = d.index[i]
        if seg_start is not None and (not v or i == len(in_pos) - 1):
            end = d.index[i]
            fig.add_vrect(x0=seg_start, x1=end, fillcolor="green", opacity=0.08,
                          line_width=0, row=1, col=1)
            seg_start = None

    colors = ["#d62728" if v < 0 else "#2ca02c" for v in ao_v.fillna(0)]
    fig.add_trace(go.Bar(x=d.index, y=ao_v, name="AO", marker_color=colors), row=2, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=diff, name="DIFF",
                             line=dict(color="#ff7f0e", width=1)), row=3, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=dea, name="DEA",
                             line=dict(color="#1f77b4", width=1)), row=3, col=1)
    fig.add_trace(go.Bar(x=d.index, y=hist, name="柱",
                         marker_color=["#d62728" if v < 0 else "#2ca02c" for v in hist.fillna(0)]),
                  row=3, col=1)
    fig.update_layout(height=720, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("最新状态")
    last = ss.iloc[-1]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("鳄鱼状态", state_label(last))
    c2.metric("Alligator", fmt_sig(last["alligator"]))
    c3.metric("AO", fmt_sig(last["ao"]))
    c4.metric("分形", fmt_sig(last["fractal"]))
    c5.metric("MACD", fmt_sig(last["macd"]))
    st.write(f"当前仓位状态：**{'✅ 满仓持有' if pos.iloc[-1] > 0 else '— 空仓'}**")

    with st.expander("近15日信号明细"):
        st.dataframe(ss.tail(15), use_container_width=True)


# ---------- 页3：回测 ----------
else:
    st.title("🧪 回测")
    tab1, tab2 = st.tabs(["单标的择时", "ETF池轮动"])

    with tab1:
        sym = st.selectbox("标的", list(dict.fromkeys(picked + [bond_sym])),
                           key="bt_sym",
                           format_func=lambda s: f"{s} {NAMES.get(s, s)}")
        variant = st.selectbox(
            "信号组合",
            ["鳄鱼线+AO+分形+MACD(完整)", "鳄鱼线+AO", "鳄鱼线+分形+MACD", "纯鳄鱼线"])
        data = load([sym], refresh)
        df = data.get(sym)
        if df is None:
            st.error("数据拉取失败")
            st.stop()

        kw = dict(zero_band=zero_band)
        if variant == "纯鳄鱼线":
            kw.update(use_ao=False, use_fractal=False, use_macd=False)
        elif variant == "鳄鱼线+AO":
            kw.update(use_ao=True, use_fractal=False, use_macd=False)
        elif variant == "鳄鱼线+分形+MACD":
            kw.update(use_ao=False, use_fractal=True, use_macd=True)
        else:
            kw.update(use_ao=True, use_fractal=True, use_macd=True)

        pos = position(df, **kw)
        res = run_single(df, pos, exec_price=exec_price, fee=fee)
        m = metrics(res["nav"], res["ret"], res["pos"], res["trades"])
        ts = trade_stats(df, res)
        m.update(ts)
        bench_ann = (res["bench_nav"].iloc[-1] ** (244 / len(res)) - 1)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("年化收益", f"{m['年化收益']:.1%}", f"基准 {bench_ann:.1%}")
        c2.metric("最大回撤", f"{m['最大回撤']:.1%}")
        c3.metric("夏普", f"{m['夏普比率']:.2f}")
        c4.metric("空仓占比", f"{m['空仓占比']:.0%}")
        c5.metric("交易次数", m.get("交易次数", 0))

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.7, 0.3], vertical_spacing=0.04,
                            subplot_titles=("净值", "回撤"))
        fig.add_trace(go.Scatter(x=res.index, y=res["nav"], name="策略",
                                 line=dict(color="#2ca02c", width=1.8)), row=1, col=1)
        fig.add_trace(go.Scatter(x=res.index, y=res["bench_nav"], name="买入持有",
                                 line=dict(color="#888", width=1)), row=1, col=1)
        dd = res["nav"] / res["nav"].cummax() - 1
        fig.add_trace(go.Scatter(x=res.index, y=dd, name="回撤", fill="tozeroy",
                                 line=dict(color="#d62728", width=1)), row=2, col=1)
        fig.update_layout(height=560, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(pd.DataFrame([{k: (f"{v:.2%}" if isinstance(v, float) and abs(v) < 10 and k not in ("交易日数", "持仓天数", "交易次数", "平均持有天数") else v)
                                     for k, v in m.items()}]).T.rename(columns={0: "值"}),
                     use_container_width=True)

    with tab2:
        st.caption("多只宽基看多 → 按规则分配；全空 → 国债ETF。研报指数轮动年化约21%。")
        mode = st.radio("轮动方式", ["equal", "top"], key="bt_mode", horizontal=True,
                        format_func=lambda m: {"equal": "看多者等权", "top": "最强动量单吊"}[m])
        mom_n = st.slider("动量窗口(日)", 5, 60, 20)
        syms = picked + [bond_sym]
        data = load(syms, refresh)
        avail = {s: data[s] for s in picked if s in data and len(data[s])}
        if len(avail) < 2:
            st.warning("至少选2只宽基ETF")
            st.stop()

        w = rotation_weights(avail, bond_symbol=bond_sym, mode=mode,
                             momentum_n=mom_n, zero_band=zero_band)
        all_data = dict(avail)
        if bond_sym in data:
            all_data[bond_sym] = data[bond_sym]
        mat = prices_matrix(all_data)
        w = w.reindex(mat.index).ffill().fillna(0)
        res = run_portfolio(mat, w, exec_price=exec_price, fee=fee)
        m = metrics(res["nav"], res["ret"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("年化收益", f"{m['年化收益']:.1%}")
        c2.metric("最大回撤", f"{m['最大回撤']:.1%}")
        c3.metric("夏普", f"{m['夏普比率']:.2f}")
        c4.metric("卡玛", f"{m['卡玛比率']:.2f}")

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=res.index, y=res["nav"], name="轮动组合",
                                 line=dict(color="#2ca02c", width=1.8)))
        for s in picked:
            if s in avail:
                bench = (1 + (all_data[s]["close"].reindex(res.index).ffill()
                              / all_data[s]["close"].reindex(res.index).ffill().iloc[0] - 1))
                fig.add_trace(go.Scatter(x=res.index, y=bench, name=NAMES.get(s, s),
                                         line=dict(width=0.8), opacity=0.5))
        fig.update_layout(height=480, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("当前权重"):
            st.dataframe(w.iloc[-1][w.iloc[-1] > 0].to_frame("权重"),
                         use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.caption("策略：鳄鱼线(Alligator)+AO+分形+MACD\n\n信号体系：招商证券量化技术分析系列\n\n⚠️ 仅供研究，不构成投资建议")
