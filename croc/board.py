"""鳄鱼派评分看板（神似版）：多因子给市场打 S/A/B/C 级，定仓位松紧。

仿「像鳄鱼一样思考」的公开线索：评分看板按"资金表现"给市场分级
（S=罕见强市, A=强, B=中性, C=弱市震荡）。确切配方不公开，本模块用
公开数据近似：指数趋势宽度、动量、量能、市场宽度、两融趋势，加权成
0-100 分再映射评级。弱市(C)全躲三剑客，中性(B)半仓，强市(A/S)进攻。

因子（均有历史，可逐日计算）：
- trend:   宽基池鳄鱼线多头占比（趋势宽度）
- mom:     宽基池等权20日动量
- vol:     成交额5日均/60日均（量能放大=活跃）
- breadth: 宽基池站上20日线占比（价格宽度）
- margin:  两融余额10日环比（杠杆资金态度），数据缺失则权重重分配
"""
import numpy as np
import pandas as pd

from .strategy import alligator_signal
from .universe import DEFENSIVE_POOL

WEIGHTS = {"trend": 0.35, "mom": 0.25, "vol": 0.15, "breadth": 0.15, "margin": 0.10}
GRADES = [(78, "S"), (62, "A"), (45, "B"), (-1, "C")]


def _clip_map(x, lo, hi):
    """把 x 线性映射到 0-100，区间外截断。"""
    return float(np.clip((x - lo) / (hi - lo) * 100, 0, 100))


def factor_scores(data: dict[str, pd.DataFrame],
                  margin: pd.Series | None = None) -> pd.DataFrame:
    """逐日计算各因子 0-100 分。data: {symbol: ohlc df}（宽基池）。"""
    # 公共交易日历
    idx = None
    for df in data.values():
        idx = df.index if idx is None else idx.union(df.index)
    idx = idx.sort_values()

    g = pd.DataFrame({s: alligator_signal(df) for s, df in data.items()}).reindex(idx).ffill()
    close = pd.DataFrame({s: df["close"] for s, df in data.items()}).reindex(idx).ffill()
    amount = pd.DataFrame({s: df["amount"] for s, df in data.items()}).reindex(idx).ffill().fillna(0)

    trend = (g == 1).mean(axis=1) * 100 - (g == -1).mean(axis=1) * 30
    trend = trend.clip(0, 100)

    mom20 = close.pct_change(20).mean(axis=1)
    mom = mom20.map(lambda x: _clip_map(x, -0.08, 0.08))

    amt5 = amount.sum(axis=1).rolling(5).mean()
    amt60 = amount.sum(axis=1).rolling(60).mean()
    vol = (amt5 / amt60.replace(0, np.nan)).map(lambda x: _clip_map(x, 0.75, 1.25))

    ma20 = close.rolling(20).mean()
    breadth = (close > ma20).mean(axis=1) * 100

    out = pd.DataFrame({"trend": trend, "mom": mom, "vol": vol, "breadth": breadth})

    if margin is not None and len(margin):
        m10 = margin.reindex(idx).ffill().pct_change(10)
        out["margin"] = m10.map(lambda x: _clip_map(x, -0.03, 0.03))
    return out


def composite(scores: pd.DataFrame) -> pd.DataFrame:
    """加权合成总分 0-100 与评级。"""
    w = {k: v for k, v in WEIGHTS.items() if k in scores.columns}
    s = sum(v for v in w.values())
    total = sum(scores[k].fillna(50) * v for k, v in w.items()) / s
    res = pd.DataFrame({"score": total.clip(0, 100)})
    res["grade"] = res["score"].map(
        lambda x: next(g for th, g in GRADES if x >= th))
    return res.join(scores)


def board_position(board: pd.DataFrame, agg=False) -> pd.Series:
    """评级 -> 权益进攻仓位比例。S/A=满仓进攻, B=半仓, C=0(全躲三剑客)。"""
    pos = board["grade"].map({"S": 1.0, "A": 1.0, "B": 0.5, "C": 0.0})
    return pos.fillna(0.0)


def board_weights(data: dict[str, pd.DataFrame],
                  def_data: dict[str, pd.DataFrame],
                  board: pd.DataFrame,
                  momentum_n=20,
                  n_attack=2) -> pd.DataFrame:
    """评级驱动的目标权重。

    - A/S: 满仓宽基动量最强的 n_attack 只（等权）
    - B:   半仓最强宽基1只 + 半仓三剑客等权
    - C:   全仓三剑客等权
    """
    idx = board.index
    close_a = pd.DataFrame({s: df["close"] for s, df in data.items()}).reindex(idx).ffill()
    close_d = pd.DataFrame({s: df["close"] for s, df in def_data.items()}).reindex(idx).ffill()
    mom_a = close_a.pct_change(momentum_n)

    w = pd.DataFrame(0.0, index=idx, columns=list(data.keys()) + list(def_data.keys()))
    defs = list(def_data.keys())
    for t in idx:
        gr = board.loc[t, "grade"]
        if gr in ("S", "A"):
            ranks = mom_a.loc[t].dropna().sort_values(ascending=False)
            for s in ranks.index[:n_attack]:
                w.loc[t, s] = 1.0 / n_attack
        elif gr == "B":
            ranks = mom_a.loc[t].dropna().sort_values(ascending=False)
            if len(ranks):
                w.loc[t, ranks.index[0]] = 0.5
            for s in defs:
                w.loc[t, s] = 0.5 / len(defs)
        else:
            for s in defs:
                w.loc[t, s] = 1.0 / len(defs)
    return w


def activity_snapshot() -> dict:
    """乐咕市场活跃度（当日快照：涨/跌家数、涨停、活跃度）。失败返回 {}。"""
    try:
        import akshare as ak
        df = ak.stock_market_activity_legu()
        return dict(zip(df["item"].astype(str), df["value"]))
    except Exception:
        return {}


def sector_fundflow() -> pd.DataFrame:
    """行业主力资金流当日排行（东财，多接口降级）。失败返回空表。"""
    import akshare as ak
    for fn in (
        lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"),
        lambda: ak.stock_fund_flow_industry(symbol="即时"),
        lambda: ak.stock_main_fund_flow(symbol="全部股票"),
    ):
        try:
            df = fn()
            if df is not None and len(df):
                return df
        except Exception:
            continue
    return pd.DataFrame()


def margin_balance() -> pd.Series | None:
    """沪深两融余额合计（日频），失败返回 None。"""
    try:
        import akshare as ak
        sh = ak.stock_margin_sse()
        sh.columns = [str(c) for c in sh.columns]
        col = [c for c in sh.columns if "融资余额" in c][0]
        s = pd.Series(pd.to_numeric(sh[col], errors="coerce").values,
                      index=pd.to_datetime(sh["信用交易日期"]))
        s = s.sort_index()
        if len(s) == 0 or (pd.Timestamp.now() - s.index[-1]).days > 45:
            return None  # 接口停更，剔除该因子
        try:
            sz = ak.stock_margin_szse()
            c2 = [c for c in sz.columns if "融资余额" in c][0]
            dcol = [c for c in sz.columns if "日期" in c][0]
            s2 = pd.Series(pd.to_numeric(sz[c2], errors="coerce").values,
                           index=pd.to_datetime(sz[dcol])).sort_index()
            return (s.add(s2, fill_value=np.nan)).dropna()
        except Exception:
            return s.dropna()
    except Exception:
        return None
