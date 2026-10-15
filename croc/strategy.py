"""鳄鱼派信号引擎：Alligator + AO + Fractal + MACD 的合成状态机。

规则（招商证券研报版）：
- Alligator: 多头排列(唇>齿>颚) -> +1；空头排列(唇<齿<颚) -> -1；缠绕 -> 0
- AO: 连续3日上行 -> +1；连续3日下行 -> -1
- Fractal: 收盘向上突破最近已确认上分形高点 -> +1；向下突破最近下分形低点 -> -1
- MACD: 水上/零轴金叉 -> +1；水下/零轴死叉 -> -1；水下金叉、水上死叉 -> 0

合成（带粘性，仓位只有满仓/空仓两态）：
- 看多: Alligator=1 且 (Fractal=1 或 MACD=1)
- 看空: Alligator/AO/Fractal/MACD 任一 = -1
- 其他: 维持前一交易日仓位
"""
import numpy as np
import pandas as pd

from .indicators import alligator, ao, fractals, macd


def alligator_signal(df: pd.DataFrame) -> pd.Series:
    a = alligator(df)
    sig = pd.Series(0, index=df.index)
    sig[(a["lips"] > a["teeth"]) & (a["teeth"] > a["jaw"])] = 1
    sig[(a["lips"] < a["teeth"]) & (a["teeth"] < a["jaw"])] = -1
    return sig


def ao_signal(df: pd.DataFrame, n_up=3) -> pd.Series:
    d = ao(df).diff()
    sig = pd.Series(0, index=df.index)
    sig[(d > 0).rolling(n_up).sum() == n_up] = 1
    sig[(d < 0).rolling(n_up).sum() == n_up] = -1
    return sig


def fractal_signal(df: pd.DataFrame) -> pd.Series:
    """收盘价突破最近已确认分形。分形中心柱需滞后2根确认，不会用到未来数据。"""
    up, dn = fractals(df)
    # 最近已确认的分形：中心柱 i 在 i+2 才确认 -> 在 T 日可用的是中心<=T-2 的分形
    last_up = up.shift(2).ffill()
    last_dn = dn.shift(2).ffill()
    sig = pd.Series(0, index=df.index)
    close = df["close"]
    sig[close > last_up] = 1
    sig[close < last_dn] = -1
    return sig


def macd_signal(df: pd.DataFrame, zero_band=0.002) -> pd.Series:
    """水上/零轴看多 -> +1；水下/零轴看空 -> -1。

    zero_band: |DIFF/close| 在此带宽内视为"零轴附近"。
    - 金叉 且 DIFF/close > -zero_band  -> +1 (水上或零轴看多)
    - 死叉 且 DIFF/close < +zero_band  -> -1 (水下或零轴看空)
    """
    diff, dea, _ = macd(df["close"])
    golden = (diff > dea) & (diff.shift(1) <= dea.shift(1))
    death = (diff < dea) & (diff.shift(1) >= dea.shift(1))
    diff_pct = diff / df["close"]
    sig = pd.Series(0, index=df.index)
    sig[golden & (diff_pct > -zero_band)] = 1
    sig[death & (diff_pct < zero_band)] = -1
    return sig


def sub_signals(df: pd.DataFrame, zero_band=0.002) -> pd.DataFrame:
    """四个子信号表，便于界面上展示每只 ETF 的状态。"""
    return pd.DataFrame({
        "alligator": alligator_signal(df),
        "ao": ao_signal(df),
        "fractal": fractal_signal(df),
        "macd": macd_signal(df, zero_band),
    })


def position(df: pd.DataFrame, zero_band=0.002,
             use_ao=True, use_fractal=True, use_macd=True) -> pd.Series:
    """合成仓位状态机：1=满仓 0=空仓，带粘性（无信号时维持前仓）。

    use_ao/use_fractal/use_macd 可拆解子策略做消融对比：
    - 纯鳄鱼线: 全关（Alligator 多空直接决定仓位，缠绕维持）
    - +AO: 开仓仍需 Alligator=1；AO=-1 参与平仓
    - +分形/MACD: 开仓条件 Alligator=1 且 (Fractal=1 或 MACD=1)
    """
    ss = sub_signals(df, zero_band)
    g, a, f, m = ss["alligator"], ss["ao"], ss["fractal"], ss["macd"]

    if not (use_ao or use_fractal or use_macd):
        # 纯鳄鱼线：多头满仓、空头空仓、缠绕维持
        long_cond = g == 1
        exit_cond = g == -1
    else:
        entry_filters = pd.Series(False, index=df.index)
        if use_fractal:
            entry_filters = entry_filters | (f == 1)
        if use_macd:
            entry_filters = entry_filters | (m == 1)
        if not (use_fractal or use_macd):
            # 只开 AO：研报版为 Alligator=1 且 AO 连续3日上行
            entry_filters = a == 1
        long_cond = (g == 1) & entry_filters

        exit_cond = (g == -1) | (f == -1) | (m == -1)
        if use_ao:
            exit_cond = exit_cond | (a == -1)

    pos = pd.Series(np.nan, index=df.index, dtype=float)
    pos[long_cond] = 1.0
    pos[exit_cond] = 0.0
    pos = pos.ffill().fillna(0.0)
    return pos
