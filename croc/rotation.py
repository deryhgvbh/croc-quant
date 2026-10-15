"""指数轮动 + 股债配置：多只宽基看多时按规则分配，全空时配国债ETF。

研报口径：鳄鱼线策略空仓占比约80%，空仓权益时配置固收；
轮动池用各指数的鳄鱼派信号状态，看多才持有。
"""
import pandas as pd

from .strategy import position


def momentum(df: pd.DataFrame, n=20) -> pd.Series:
    return df["close"] / df["close"].shift(n) - 1


def rotation_weights(data: dict[str, pd.DataFrame],
                     bond_symbol: str = None,
                     mode: str = "equal",
                     momentum_n: int = 20,
                     zero_band: float = 0.002,
                     **sig_kwargs) -> pd.DataFrame:
    """生成每日目标权重矩阵（行=日期, 列=symbol，行和<=1）。

    mode:
    - 'equal': 所有看多标等权持有
    - 'top':   只持有看多的标的中 momentum_n 日动量最强的一只（全仓）
    全空时：bond_symbol 权重=1（股债配置）。
    """
    pos = pd.DataFrame({s: position(df, zero_band=zero_band, **sig_kwargs)
                        for s, df in data.items()})
    pos = pos.ffill().fillna(0)

    mom = pd.DataFrame({s: momentum(df, momentum_n) for s, df in data.items()})
    mom = mom.reindex(pos.index).ffill()

    w = pd.DataFrame(0.0, index=pos.index, columns=list(data.keys()))
    for t in pos.index:
        bulls = pos.columns[pos.loc[t] > 0]
        if len(bulls) == 0:
            continue
        if mode == "top":
            best = mom.loc[t, bulls].idxmax()
            w.loc[t, best] = 1.0
        else:
            w.loc[t, bulls] = 1.0 / len(bulls)

    if bond_symbol is not None:
        w[bond_symbol] = (w.sum(axis=1) == 0).astype(float)
    return w
