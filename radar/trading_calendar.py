"""交易日判断。

不维护本地节假日表 —— 那东西每年都要人工更新，一旦忘了更新就会静默跑错。
改成问数据本身：拉上证指数的最新日线，看它的日期是不是今天。
指数在交易日一开盘就有当日 K 线，收盘后也一直在，所以午盘批和收盘批都适用。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from .config import KLT_DAY
from .kline import fetch_kline

log = logging.getLogger(__name__)

SH_INDEX = "1.000001"  # 上证指数


def latest_index_date() -> Optional[date]:
    """走和行情同一套多源链路，任何一个源能拿到指数日线就能判断。"""
    df = fetch_kline(SH_INDEX, KLT_DAY, limit=5)
    if df is None or df.empty:
        return None
    last = str(df["date"].iloc[-1]).split(" ")[0]
    try:
        return datetime.strptime(last, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_trading_day(today: Optional[date] = None) -> Optional[bool]:
    """True=交易日，False=非交易日，None=判断不了（网络故障）。

    返回 None 时调用方应该**继续执行**而不是跳过：
    宁可在非交易日多生成一份重复报表，也不要在交易日因为网络抖动漏掉一次。
    """
    today = today or date.today()
    if today.weekday() >= 5:
        return False
    latest = latest_index_date()
    if latest is None:
        log.warning("交易日判断失败（拿不到上证指数），按交易日处理")
        return None
    return latest == today


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = is_trading_day()
    print({True: "交易日", False: "非交易日", None: "未知"}[result])
    raise SystemExit(0 if result is not False else 1)
