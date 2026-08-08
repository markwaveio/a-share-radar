"""指标计算：KDJ 和成交量变异系数。

这是整个项目里唯一不能出错的地方——数据抓错了会报错，指标算错了只会安静地给出错误结论。
所以这两个函数都有对应的单元测试（tests/test_indicators.py）。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


def kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """通达信/东财口径的 KDJ(9,3,3)。

        RSV = (C - LLV(L,n)) / (HHV(H,n) - LLV(L,n)) × 100
        K   = SMA(RSV, m1, 1)  即  K_t = ((m1-1)·K_{t-1} + RSV_t) / m1
        D   = SMA(K,   m2, 1)
        J   = 3K - 2D

    注意两个容易踩的坑：
      1. 这里的 SMA 是通达信的"移动平均"，不是简单均值，而是权重 1/m 的递推平滑；
         用 pandas 的 rolling().mean() 会算出完全不同的值。
      2. K/D 的初值是 50，不是 0。前 30 根内的值不可信，所以要多拉历史（见 kline_limit）。
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["K", "D", "J"])

    high, low, close = df["high"], df["low"], df["close"]
    llv = low.rolling(n, min_periods=1).min()
    hhv = high.rolling(n, min_periods=1).max()

    span = hhv - llv
    # 一字板/停牌时最高=最低，分母为 0。东财口径此时 RSV 记为 100（收盘价等于区间上下沿）。
    rsv = np.where(span == 0, 100.0, (close - llv) / span.replace(0, np.nan) * 100.0)
    rsv = pd.Series(rsv, index=df.index).fillna(100.0).clip(0, 100)

    k = np.empty(len(rsv))
    d = np.empty(len(rsv))
    k_prev = d_prev = 50.0
    rsv_values = rsv.to_numpy()
    for i in range(len(rsv_values)):
        k_prev = ((m1 - 1) * k_prev + rsv_values[i]) / m1
        d_prev = ((m2 - 1) * d_prev + k_prev) / m2
        k[i], d[i] = k_prev, d_prev

    return pd.DataFrame({"K": k, "D": d, "J": 3 * k - 2 * d}, index=df.index)


def kdj_at(df: pd.DataFrame, offset: int = -1, n: int = 9,
           m1: int = 3, m2: int = 3) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """取指定位置那一根 K 线的 (K, D, J)。offset=-1 最新，-2 上一根。

    数据不够时返回 (None, None, None)，让 Excel 那一格留空而不是填 0——
    填 0 会被误读成"J 值极低、超卖"，这是投资场景里代价很高的错误。
    """
    if df is None or len(df) < abs(offset):
        return None, None, None
    values = kdj(df, n, m1, m2)
    row = values.iloc[offset]
    return round(float(row["K"]), 2), round(float(row["D"]), 2), round(float(row["J"]), 2)


# ------------------------------------------------------------ 成交量（需求 2）

def coefficient_of_variation(series: pd.Series) -> Optional[float]:
    """变异系数 CV = 标准差 / 均值。用样本标准差（ddof=1）。

    CV 是无量纲的，所以能横向比较不同流通盘的股票——
    这正是它比"成交量标准差"更适合做全市场排序的原因。
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return None
    mean = s.mean()
    if mean <= 0:
        return None
    return round(float(s.std(ddof=1) / mean), 4)


def volume_metrics(df_daily: pd.DataFrame, cv_windows=(20, 30),
                   cv_range=(40, 10), spike_days: int = 10) -> dict:
    """需求 2 的四个指标。df_daily 需按时间升序，最后一行是最近交易日。"""
    out = {
        "cv_20": None, "cv_30": None, "cv_range": None,
        "spike_ratio": None, "vol_std": None,
    }
    if df_daily is None or df_daily.empty:
        return out

    vol = pd.to_numeric(df_daily["volume"], errors="coerce").dropna()
    if vol.empty:
        return out

    for w in cv_windows:
        if len(vol) >= w:
            out[f"cv_{w}"] = coefficient_of_variation(vol.iloc[-w:])

    # 过去 40~10 日：即 40 个交易日前到 10 个交易日前，共 30 根，不含最近 10 天
    far, near = cv_range
    if len(vol) >= far:
        window = vol.iloc[-far:-near]
        out["cv_range"] = coefficient_of_variation(window)
        # （过去10日单日最大量）/（过去40~10日平均量）—— 放量突破的强度
        base = window.mean()
        if base > 0 and len(vol) >= spike_days:
            out["spike_ratio"] = round(float(vol.iloc[-spike_days:].max() / base), 4)

    if len(vol) >= max(cv_windows):
        out["vol_std"] = round(float(vol.iloc[-max(cv_windows):].std(ddof=1)), 2)

    return out
