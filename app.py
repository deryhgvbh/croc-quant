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
from croc.universe import DEFAULT_POOL, EXTRA_POOL, BOND_ETF, DEFENSIVE_POOL
from croc import board as gator

st.set_page_config(page_title="鳄鱼派量化", page_icon="🐊", layout="wide")

NAMES = {**DEFAULT_POOL, **EXTRA_POOL, **BOND_ETF, **DEFENSIVE_POOL}

GRADE_COLOR = {"S": "#00c853", "A": "#64dd17", "B": "#ffab00", "C": "#d50000"}


@st.cache_data(ttl=3600, show_spinner="拉取行情数据…")
def load(symbols, refresh=False):
    return get_pool(list(symbols), refresh=refresh)


@st.cache_data(ttl=3600, show_spinner="计算评分看板…")
def load_board(pick_key, def_key, refresh=False):
    """宽基池+三剑客数据 -> 评分看板(df含score/grade/因子分)。"""
    data = get_pool(list(pick_key), refresh=refresh)
    def_data = get_pool(list(def_key), refresh=refresh)
    margin = gator.margin_balance()
    scores = gator.factor_scores(data, margin)
    return gator.composite(scores), data, def_data


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
page = st.sidebar.radio("页面", ["今日信号", "评分看板", "行情与鳄鱼线", "回测"])

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

    # 先算两套系统的今日结论，置顶一行看懂
    w_alli = rotation_weights({s: data[s] for s in picked if s in data},
                              bond_symbol=bond_sym, mode="equal", zero_band=zero_band)
    hold_alli = w_alli.iloc[-1]
    hold_alli = hold_alli[hold_alli > 0]
    bdf, bdata, def_data = load_board(tuple(picked), tuple(DEFENSIVE_POOL.keys()), refresh)
    bw_today = gator.board_weights(bdata, def_data, bdf).iloc[-1]
    hold_bd = bw_today[bw_today > 0]
    grade_today = bdf.iloc[-1]["grade"]

    def _fmt_hold(h):
        return "、".join(f"{NAMES.get(s, s)} {v:.0%}" for s, v in h.items()) or "空仓"

    d_last = max(df.index[-1] for df in data.values() if len(df))
    st.info(f"📣 **今日结论（{d_last:%m-%d} 收盘）**\n\n"
            f"① 鳄鱼线系统 → **{_fmt_hold(hold_alli)}**\n\n"
            f"② 评分看板 → **{grade_today}级**（{bdf.iloc[-1]['score']:.0f}分）→ {_fmt_hold(hold_bd)}")

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
    c_a, c_b = st.columns(2)
    with c_a:
        st.markdown("**① 鳄鱼线系统**（Alligator+AO+分形+MACD 机械择时）")
        mode = st.radio("轮动方式", ["equal", "top"], horizontal=True,
                        format_func=lambda m: {"equal": "看多者等权", "top": "最强动量单吊"}[m])
        w = rotation_weights({s: data[s] for s in picked if s in data},
                             bond_symbol=bond_sym, mode=mode, zero_band=zero_band)
        last_w = w.iloc[-1]
        hold = last_w[last_w > 0]
        if len(hold) == 0:
            st.info("无任何持仓信号")
        else:
            for s, weight in hold.items():
                st.success(f"**{s} {NAMES.get(s, s)}** — {weight:.0%}")
        with st.expander("近10日权重变化"):
            st.dataframe(w.tail(10).round(2), use_container_width=True)

    with c_b:
        st.markdown("**② 评分看板系统**（鳄鱼派风格：评级定仓位+三剑客防守）")
        last = bdf.iloc[-1]
        grade = last["grade"]
        st.metric("今日评级", grade, f"得分 {last['score']:.0f}/100")
        lhold = hold_bd
        if len(lhold) == 0:
            st.info("无持仓")
        else:
            tag = {"S": "强市满仓进攻", "A": "强势进攻", "B": "半仓+半防守", "C": "全躲三剑客"}[grade]
            st.caption(f"{grade}级 → {tag}")
            for s, weight in lhold.items():
                st.success(f"**{s} {NAMES.get(s, s)}** — {weight:.0%}")


# ---------- 页2：评分看板 ----------
elif page == "评分看板":
    st.title("📊 大盘评分看板（鳄鱼派风格）")
    st.caption("仿「像鳄鱼一样思考」评分看板：多因子加权打分定 S/A/B/C 级，"
               "评级决定仓位松紧，弱市(C)躲进三剑客（红利/黄金/国债）。"
               "确切配方未公开，此为公开数据的神似实现。")

    bdf, bdata, def_data = load_board(tuple(picked), tuple(DEFENSIVE_POOL.keys()), refresh)
    last = bdf.iloc[-1]
    grade = last["grade"]

    g1, g2, g3 = st.columns([1, 2, 2])
    g1.metric("今日评级", grade, f"得分 {last['score']:.0f}/100")
    factor_cols = {"trend": "趋势宽度", "mom": "动量", "vol": "量能",
                   "breadth": "价格宽度", "margin": "两融趋势"}
    g2.markdown("**因子得分**")
    for k, label in factor_cols.items():
        if k in bdf.columns:
            g2.progress(int(last[k]) / 100,
                        text=f"{label}: {last[k]:.0f}")
    snap = gator.activity_snapshot()
    if snap:
        g3.markdown("**市场快照（乐咕）**")
        g3.dataframe(pd.DataFrame(list(snap.items()),
                                  columns=["项目", "值"]).set_index("项目"),
                     use_container_width=True)

    # 评分历史（最近250日）
    days = st.slider("显示最近N日", 60, 750, 250, key="board_days")
    bd = bdf.tail(days)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.65, 0.35], vertical_spacing=0.05,
                        subplot_titles=("综合评分与评级", "因子得分"))
    fig.add_trace(go.Scatter(x=bd.index, y=bd["score"], name="综合评分",
                             line=dict(color="#2ca02c", width=1.8)), row=1, col=1)
    for th, lab in [(78, "S线"), (62, "A线"), (45, "B线")]:
        fig.add_hline(y=th, line_dash="dot", line_color="#999", row=1, col=1,
                      annotation_text=lab)
    # 评级背景色
    seg_start, cur = None, None
    for i, g in enumerate(bd["grade"]):
        if g != cur:
            if seg_start is not None:
                fig.add_vrect(x0=seg_start, x1=bd.index[i - 1],
                              fillcolor=GRADE_COLOR[cur], opacity=0.10,
                              line_width=0, row=1, col=1)
            seg_start, cur = bd.index[i], g
    if seg_start is not None:
        fig.add_vrect(x0=seg_start, x1=bd.index[-1], fillcolor=GRADE_COLOR[cur],
                      opacity=0.10, line_width=0, row=1, col=1)
    for k, label in factor_cols.items():
        if k in bd.columns:
            fig.add_trace(go.Scatter(x=bd.index, y=bd[k], name=label,
                                     line=dict(width=1)), row=2, col=1)
    fig.update_layout(height=620, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("💰 板块资金流（今日主力净流入）")
    ff = gator.sector_fundflow()
    if len(ff):
        st.dataframe(ff.head(15), use_container_width=True, hide_index=True)
    else:
        st.info("板块资金流接口暂不可用")

    st.subheader("🛡️ 三剑客（防守资产）")
    for s, name in DEFENSIVE_POOL.items():
        df = def_data.get(s)
        if df is not None and len(df):
            st.write(f"- **{s} {name}** 最新收盘 {df['close'].iloc[-1]:.3f}")


# ---------- 页3：行情与鳄鱼线 ----------
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
    tab1, tab2, tab3 = st.tabs(["单标的择时", "ETF池轮动", "评分看板策略"])

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

    with tab3:
        st.caption("鳄鱼派风格：评级定仓位——A/S 满仓动量最强的宽基、"
                   "B 半仓+半三剑客、C 全躲三剑客（红利/黄金/国债）。")
        bdf, bdata, def_data = load_board(tuple(picked), tuple(DEFENSIVE_POOL.keys()), refresh)
        mom_n3 = st.slider("动量窗口(日)", 5, 60, 20, key="mom3")
        bw = gator.board_weights(bdata, def_data, bdf, momentum_n=mom_n3)
        all_d = {**bdata, **def_data}
        mat = prices_matrix(all_d)
        bw = bw.reindex(mat.index).ffill().fillna(0)
        res3 = run_portfolio(mat, bw, exec_price=exec_price, fee=fee)
        m3 = metrics(res3["nav"], res3["ret"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("年化收益", f"{m3['年化收益']:.1%}")
        c2.metric("最大回撤", f"{m3['最大回撤']:.1%}")
        c3.metric("夏普", f"{m3['夏普比率']:.2f}")
        c4.metric("卡玛", f"{m3['卡玛比率']:.2f}")

        fig3 = make_subplots(rows=2, cols=1, shared_xaxes=True,
                             row_heights=[0.7, 0.3], vertical_spacing=0.04,
                             subplot_titles=("净值", "评级"))
        fig3.add_trace(go.Scatter(x=res3.index, y=res3["nav"], name="评分策略",
                                  line=dict(color="#2ca02c", width=1.8)), row=1, col=1)
        eq = mat[picked[0]] if picked else mat.iloc[:, 0]
        bch = eq / eq.iloc[0]
        fig3.add_trace(go.Scatter(x=bch.index, y=bch, name=f"{NAMES.get(picked[0],'基准')}",
                                  line=dict(color="#888", width=1)), row=1, col=1)
        gmap = {"S": 4, "A": 3, "B": 2, "C": 1}
        gl = bdf["grade"].reindex(res3.index).ffill().map(gmap)
        fig3.add_trace(go.Scatter(x=res3.index, y=gl, name="评级(S=4..C=1)",
                                  line=dict(color="#ffab00", width=1),
                                  fill="tozeroy"), row=2, col=1)
        fig3.update_layout(height=520, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig3, use_container_width=True)

        grade_dist = bdf["grade"].value_counts()
        st.write("历史评级分布：" +
                 "　".join(f"{g}级 {grade_dist.get(g, 0)}天" for g in ["S", "A", "B", "C"]))
        with st.expander("当前权重"):
            st.dataframe(bw.iloc[-1][bw.iloc[-1] > 0].to_frame("权重"),
                         use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.caption("① 鳄鱼线(Alligator)+AO+分形+MACD：招商证券研报体系\n\n② 评分看板：仿「像鳄鱼一样思考」S/A/B/C评级+三剑客防守\n\n⚠️ 仅供研究，不构成投资建议")
