"""技术指标：SMMA、鳄鱼线(Alligator)、AO、分形(Fractal)、MACD。

数据约定：df 为日线 DataFrame，列 open/high/low/close，索引为日期。
"""
import numpy as np
import pandas as pd


def smma(s: pd.Series, n: int) -> pd.Series:
    """平滑移动平均（Smoothed MA，比尔·威廉斯原始定义）。

    首值 = 前 n 项简单平均；之后 SMMA(t) = (SMMA(t-1)*(n-1) + x(t)) / n。
    """
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    if len(vals) < n:
        return pd.Series(out, index=s.index)
    out[n - 1] = np.nanmean(vals[:n])
    for i in range(n, len(vals)):
        out[i] = (out[i - 1] * (n - 1) + vals[i]) / n
    return pd.Series(out, index=s.index)


def alligator(df: pd.DataFrame,
              jaw_n=13, jaw_s=8, teeth_n=8, teeth_s=5, lips_n=5, lips_s=3):
    """鳄鱼线三条线，对中位价 MP=(H+L)/2 取 SMMA 并向未来平移。

    返回 DataFrame：jaw(蓝/下颚) teeth(红/牙齿) lips(绿/上唇)。
    平移后第 t 根的值对应 SMMA(t - shift)，即绘图上的右移。
    """
    mp = (df["high"] + df["low"]) / 2
    return pd.DataFrame({
        "jaw": smma(mp, jaw_n).shift(jaw_s),
        "teeth": smma(mp, teeth_n).shift(teeth_s),
        "lips": smma(mp, lips_n).shift(lips_s),
    })


def ao(df: pd.DataFrame, fast=5, slow=34) -> pd.Series:
    """动量震荡指标 AO = SMA(MP,5) - SMA(MP,34)，MP=(H+L)/2。"""
    mp = (df["high"] + df["low"]) / 2
    return mp.rolling(fast).mean() - mp.rolling(slow).mean()


def ac(ao_s: pd.Series) -> pd.Series:
    """加速震荡指标 AC = AO - SMA(AO,5)。"""
    return ao_s - ao_s.rolling(5).mean()


def fractals(df: pd.DataFrame):
    """五柱分形。返回 (上分形高点序列, 下分形低点序列)，中心柱位置取值，其余 NaN。

    上分形：中心柱最高价高于左右各两根柱的最高价。
    注意：中心柱 i 的分形要到 i+2 收盘后才能确认。
    """
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    up = np.full(len(df), np.nan)
    dn = np.full(len(df), np.nan)
    for i in range(2, len(df) - 2):
        if h[i] > h[i - 1] and h[i] > h[i - 2] and h[i] >= h[i + 1] and h[i] >= h[i + 2]:
            up[i] = h[i]
        if l[i] < l[i - 1] and l[i] < l[i - 2] and l[i] <= l[i + 1] and l[i] <= l[i + 2]:
            dn[i] = l[i]
    return pd.Series(up, index=df.index), pd.Series(dn, index=df.index)


def macd(close: pd.Series, fast=12, slow=26, sig=9):
    """MACD：DIFF=EMA12-EMA26，DEA=DIFF 的 9 日 EMA，柱=2*(DIFF-DEA)。"""
    diff = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    dea = diff.ewm(span=sig, adjust=False).mean()
    hist = (diff - dea) * 2
    return diff, dea, hist
