"""回测引擎：T+1 成交、手续费、绩效指标。

执行模型（信号于 T 日收盘产生）：
- 'close':      T 日收盘价调仓。当日收益 = w(t-1) * ret_cc
- 'next_open':  T+1 开盘价调仓。当日收益 = w(t-1)*ret_co + w(t)*ret_oc
- 'next_close': T+1 收盘价调仓。当日收益 = w(t-1) * ret_cc
其中 ret_co=open/close(t-1)-1（隔夜）, ret_oc=close/open-1（日内）。
"""
import numpy as np
import pandas as pd


def _returns(df: pd.DataFrame):
    ret_cc = df["close"] / df["close"].shift(1) - 1
    ret_co = df["open"] / df["close"].shift(1) - 1
    ret_oc = df["close"] / df["open"] - 1
    return ret_cc.fillna(0), ret_co.fillna(0), ret_oc.fillna(0)


def run_single(df: pd.DataFrame, pos_signal: pd.Series,
               exec_price="next_open", fee=0.0003, slippage=0.0) -> pd.DataFrame:
    """单标的择时回测。pos_signal: T 日收盘后确定的目标仓位(0/1)。"""
    df = df.copy()
    ret_cc, ret_co, ret_oc = _returns(df)

    if exec_price == "next_open":
        w = pos_signal.shift(1).fillna(0.0)
        strat_ret = w.shift(1).fillna(0.0) * ret_co + w * ret_oc
    elif exec_price == "next_close":
        w = pos_signal.shift(1).fillna(0.0)
        strat_ret = w.shift(1).fillna(0.0) * ret_cc
    else:  # 'close'
        w = pos_signal
        strat_ret = w.shift(1).fillna(0.0) * ret_cc

    trades = w.diff().abs().fillna(w.abs())
    cost = trades * (fee + slippage)
    strat_ret = strat_ret - cost

    out = pd.DataFrame(index=df.index)
    out["pos"] = w
    out["ret"] = strat_ret.fillna(0)
    out["nav"] = (1 + out["ret"]).cumprod()
    out["bench_nav"] = (1 + ret_cc).cumprod()
    out["trades"] = trades
    return out


def run_portfolio(prices: pd.DataFrame, target_w: pd.DataFrame,
                  exec_price="next_open", fee=0.0003, slippage=0.0) -> pd.DataFrame:
    """轮动组合回测：target_w 为 T 日收盘确定的目标权重 (date x symbol)。"""
    closes = prices.xs("close", axis=1, level="price")
    opens = prices.xs("open", axis=1, level="price")
    ret_cc = closes / closes.shift(1) - 1
    ret_co = opens / closes.shift(1) - 1
    ret_oc = closes / opens - 1
    ret_cc, ret_co, ret_oc = ret_cc.fillna(0), ret_co.fillna(0), ret_oc.fillna(0)

    target_w = target_w.reindex(closes.index).ffill().fillna(0.0)
    if exec_price == "next_open":
        w = target_w.shift(1).fillna(0.0)
        strat_ret = (w.shift(1).fillna(0.0) * ret_co).sum(axis=1) + (w * ret_oc).sum(axis=1)
    elif exec_price == "next_close":
        w = target_w.shift(1).fillna(0.0)
        strat_ret = (w.shift(1).fillna(0.0) * ret_cc).sum(axis=1)
    else:
        w = target_w
        strat_ret = (w.shift(1).fillna(0.0) * ret_cc).sum(axis=1)

    turnover = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1)
    strat_ret = strat_ret - turnover * (fee + slippage)

    out = pd.DataFrame(index=closes.index)
    out["ret"] = strat_ret.fillna(0)
    out["nav"] = (1 + out["ret"]).cumprod()
    out["turnover"] = turnover
    out["n_hold"] = (w > 0).sum(axis=1)
    return out


def metrics(nav: pd.Series, ret: pd.Series, pos: pd.Series = None,
            trades: pd.Series = None, rf=0.02) -> dict:
    """绩效指标：年化、最大回撤、夏普、卡玛、胜率等。"""
    n = len(nav)
    years = n / 244.0
    total = nav.iloc[-1] / nav.iloc[0] - 1 if n else 0
    ann = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    vol = ret.std() * np.sqrt(244)
    sharpe = (ann - rf) / vol if vol > 0 else np.nan
    calmar = ann / abs(mdd) if mdd < 0 else np.nan

    m = {"年化收益": ann, "累计收益": total, "最大回撤": mdd,
         "年化波动": vol, "夏普比率": sharpe, "卡玛比率": calmar,
         "交易日数": n}
    if pos is not None:
        m["空仓占比"] = float((pos == 0).mean())
        m["持仓天数"] = int((pos > 0).sum())
    return m


def trade_stats(df: pd.DataFrame, result: pd.DataFrame) -> dict:
    """按仓位变化切割成一笔笔交易，算胜率/盈亏比/平均持有天数。"""
    pos = result["pos"]
    entries = df.index[(pos.diff() > 0)]
    exits = df.index[(pos.diff() < 0)]
    if len(pos) and pos.iloc[0] > 0:
        entries = entries.insert(0, df.index[0])
    if len(pos) and pos.iloc[-1] > 0:
        exits = exits.append(pd.DatetimeIndex([df.index[-1]]))
    rets, holds = [], []
    for e, x in zip(entries, exits[: len(entries)]):
        seg = result.loc[e:x, "ret"]
        rets.append((1 + seg).prod() - 1)
        holds.append(len(seg))
    if not rets:
        return {"交易次数": 0}
    rets = np.array(rets)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    return {
        "交易次数": len(rets),
        "胜率": float((rets > 0).mean()),
        "盈亏比": float(wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() != 0 else np.nan,
        "平均持有天数": float(np.mean(holds)),
        "单次平均收益": float(rets.mean()),
    }
