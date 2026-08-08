"""K 线获取与周期合成。

抓取本身委托给 `providers.py` 的多源调度（东财 → 腾讯/新浪自动降级），
这个模块只负责两件事：对外提供统一入口，以及合成东财没有的 120 分钟周期。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from . import providers
from .config import SETTINGS

log = logging.getLogger(__name__)

# 统一 schema，与 providers.COLUMNS 一致
COLUMNS = providers.COLUMNS


def fetch_kline(secid: str, klt: int,
                limit: Optional[int] = None) -> Optional[pd.DataFrame]:
    """拉单只股票单个周期的 K 线（前复权）。

    前复权是硬要求：不复权的话除权日的跳空会让 KDJ 算出一个巨大的假死叉。
    """
    return providers.fetch(secid, klt, limit or SETTINGS.kline_limit)


def fetch_all_timeframes(secid: str, klts: List[int]) -> Dict[int, pd.DataFrame]:
    """一只股票的多个周期。拿不到的周期不放进结果字典。"""
    out: Dict[int, pd.DataFrame] = {}
    for klt in klts:
        df = fetch_kline(secid, klt)
        if df is not None and not df.empty:
            out[klt] = df
    return out


def resample_120m(df60: pd.DataFrame) -> pd.DataFrame:
    """60 分钟线合成 120 分钟线。

    东财和新浪的 60 分钟线时间戳都是**收盘时刻**：10:30 / 11:30 / 14:00 / 15:00。
    按交易日分组后每 2 根合成 1 根，得到 11:30（上午）和 15:00（下午）两根。
    最后一天若只有奇数根（盘中运行），保留未走完的那半根 —— 那正是「当前 120 分钟周期」。
    """
    if df60 is None or df60.empty:
        return pd.DataFrame(columns=COLUMNS)

    df = df60.copy()
    dt = pd.to_datetime(df["date"], errors="coerce")
    df["_day"] = dt.dt.date
    df["_slot"] = df.groupby("_day").cumcount() // 2

    agg = df.groupby(["_day", "_slot"], sort=True).agg(
        date=("date", "last"),
        open=("open", "first"),
        close=("close", "last"),
        high=("high", "max"),
        low=("low", "min"),
        volume=("volume", "sum"),
        amount=("amount", "sum"),
    ).reset_index(drop=True)

    return agg[COLUMNS]
