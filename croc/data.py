"""数据层：ETF 日线多源拉取 + 本地 parquet 缓存。

优先级：东财 fund_etf_hist_em(后复权) -> baostock(adjustflag=1 后复权) -> 新浪(不复权)。
用后复权而非前复权：东财 qfq 对上市久的ETF（如510050）会把早年价格错误
压缩到 ~0.005 量级（脏数据），hfq 早期保持真实价格、后期累乘，回测不受此影响。
缓存按 symbol 存 data_cache/{symbol}.parquet，增量更新。
"""
import time
from pathlib import Path

import akshare as ak
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"

_COLMAP = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
}


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=_COLMAP)
    keep = [c for c in ["date", "open", "close", "high", "low", "volume", "amount"] if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "amount" not in df.columns:
        df["amount"] = float("nan")
    if "volume" not in df.columns:
        df["volume"] = float("nan")
    return _clean(df)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """丢弃价格非正或缺失的行（东财早年数据偶有0值/错误行）。"""
    df = df.dropna(subset=["open", "close", "high", "low"])
    return df[(df["open"] > 0) & (df["close"] > 0) & (df["high"] > 0) & (df["low"] > 0)]


def _fetch_em(symbol: str, start: str) -> pd.DataFrame:
    df = ak.fund_etf_hist_em(symbol=symbol, period="daily",
                             start_date=start, adjust="hfq")
    return _norm(df)


def _bs_code(symbol: str) -> str:
    return ("sh." if symbol.startswith("5") else "sz.") + symbol


def _fetch_baostock(symbol: str, start: str) -> pd.DataFrame:
    import baostock as bs
    bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            _bs_code(symbol), "date,open,high,low,close,volume,amount",
            start_date=pd.to_datetime(start).strftime("%Y-%m-%d"),
            frequency="d", adjustflag="1")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    return _norm(df)


def _fetch_sina(symbol: str, start: str) -> pd.DataFrame:
    code = ("sh" if symbol.startswith("5") else "sz") + symbol
    df = ak.fund_etf_hist_sina(symbol=code)
    df = _norm(df)
    return df[df.index >= pd.to_datetime(start)]


def _fetch(symbol: str, start: str = "20050101", retries: int = 4) -> pd.DataFrame:
    """东财优先，失败退避后依次降级到 baostock / 新浪。"""
    for i in range(retries):
        try:
            return _fetch_em(symbol, start)
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (i + 1))
    print(f"[data] {symbol} eastmoney failed({last_err}), trying baostock")
    try:
        return _fetch_baostock(symbol, start)
    except Exception as e:
        last_err = e
        print(f"[data] {symbol} baostock failed({e}), trying sina")
    return _fetch_sina(symbol, start)


def get_etf(symbol: str, refresh: bool = False,
            cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """取某只ETF的后复权日线；有缓存则增量更新。"""
    cache_dir.mkdir(exist_ok=True)
    path = cache_dir / f"{symbol}.parquet"
    cached = None
    if path.exists() and not refresh:
        try:
            cached = _clean(pd.read_parquet(path))
        except Exception:
            cached = None
    if cached is not None and len(cached):
        start = (cached.index[-1] + pd.Timedelta(days=1)).strftime("%Y%m%d")
        try:
            new = _fetch(symbol, start)
        except Exception:
            new = pd.DataFrame()
        if len(new):
            df = pd.concat([cached, new])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df = _clean(df)
            df.to_parquet(path)
            return df
        return cached
    df = _fetch(symbol)
    if len(df):
        df.to_parquet(path)
    return df


def get_pool(symbols: list[str], refresh: bool = False) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        try:
            out[s] = get_etf(s, refresh=refresh)
        except Exception as e:
            print(f"[data] {s} fetch failed: {e}")
        time.sleep(0.6)  # 限流
    return out


def prices_matrix(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """把 {symbol: df} 拼成 (date, price) 二级列的价格矩阵。"""
    frames = {}
    for sym, df in data.items():
        frames[sym] = df[["open", "close", "high", "low"]]
    mat = pd.concat(frames, axis=1)
    mat.columns = pd.MultiIndex.from_tuples(mat.columns, names=["symbol", "price"])
    return mat.sort_index().ffill()
