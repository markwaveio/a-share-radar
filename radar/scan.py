"""全市场扫描：需求 1（KDJ）和需求 2（成交量）共用同一次抓取。

关键设计：**按股票并发，而不是按周期并发**。
每只股票拉完 6 个周期后立刻算出指标、丢掉原始 DataFrame，
否则 5500 只 × 6 周期 × 120 根 K 线会把内存吃光。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import providers
from .config import FETCH_KLTS, KLT_60M, SETTINGS, TIMEFRAME_COLUMNS
from .indicators import kdj_at, volume_metrics
from .kline import fetch_kline, resample_120m
from .universe import Stock, fetch_universe

log = logging.getLogger(__name__)

KLT_DAY = 101


@dataclass
class ScanResult:
    kdj_rows: List[dict] = field(default_factory=list)
    volume_rows: List[dict] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def ok_count(self) -> int:
        return len(self.kdj_rows)


def _scan_one(stock: Stock) -> Tuple[Optional[dict], Optional[dict]]:
    """抓一只股票的全部周期，返回 (KDJ 行, 成交量行)。"""
    frames: Dict[object, object] = {}
    for klt in FETCH_KLTS:
        df = fetch_kline(stock.secid, klt)
        if df is not None and not df.empty:
            frames[klt] = df

    if KLT_60M in frames:
        frames["120m"] = resample_120m(frames[KLT_60M])

    if not frames:
        return None, None

    # --- 需求 1：每个周期取对应位置的 KDJ ---
    kdj_row = {"code": stock.code, "name": stock.name}
    for label, source, offset in TIMEFRAME_COLUMNS:
        df = frames.get(source)
        k, d, j = kdj_at(df, offset, SETTINGS.kdj_n, SETTINGS.kdj_m1, SETTINGS.kdj_m2)
        kdj_row[f"{label}_K"] = k
        kdj_row[f"{label}_D"] = d
        kdj_row[f"{label}_J"] = j

    # --- 需求 2：日线成交量统计（复用同一份日线，不额外请求）---
    vol_row = {"code": stock.code, "name": stock.name}
    vol_row.update(
        volume_metrics(
            frames.get(KLT_DAY),
            cv_windows=SETTINGS.cv_windows,
            cv_range=SETTINGS.cv_range_window,
            spike_days=SETTINGS.spike_recent_days,
        )
    )
    return kdj_row, vol_row


class _Progress:
    """线程安全的进度打印。抓 3 万次请求时没有进度条会让人以为程序挂了。"""

    def __init__(self, total: int, every: int = 200):
        self.total, self.every = total, every
        self.done = 0
        self.start = time.monotonic()
        self._lock = threading.Lock()

    def tick(self) -> None:
        with self._lock:
            self.done += 1
            if self.done % self.every == 0 or self.done == self.total:
                elapsed = time.monotonic() - self.start
                rate = self.done / elapsed if elapsed else 0
                eta = (self.total - self.done) / rate if rate else 0
                log.info("进度 %s/%s  %.1f 只/秒  预计剩余 %.0f 秒",
                         self.done, self.total, rate, eta)


def scan_market(stocks: Optional[List[Stock]] = None) -> ScanResult:
    if stocks is None:
        stocks = fetch_universe()
    if not stocks:
        log.error("股票池为空，检查网络或代理设置")
        return ScanResult()

    log.info("开始扫描 %s 只股票 × %s 个周期，并发 %s",
             len(stocks), len(FETCH_KLTS), SETTINGS.concurrency)

    providers.reset_breakers()  # 每轮给所有数据源一次重新证明自己的机会
    result = ScanResult()
    progress = _Progress(len(stocks))
    started = time.monotonic()

    with ThreadPoolExecutor(max_workers=SETTINGS.concurrency) as pool:
        futures = {pool.submit(_scan_one, s): s for s in stocks}
        for fut in as_completed(futures):
            stock = futures[fut]
            try:
                kdj_row, vol_row = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("扫描失败 %s %s: %s", stock.code, stock.name, exc)
                result.failed.append(stock.code)
            else:
                if kdj_row is None:
                    result.failed.append(stock.code)
                else:
                    result.kdj_rows.append(kdj_row)
                    result.volume_rows.append(vol_row)
            progress.tick()

    # 按代码排序，保证两次运行的 Excel 行序一致，方便 学员做差异对比
    result.kdj_rows.sort(key=lambda r: r["code"])
    result.volume_rows.sort(key=lambda r: r["code"])
    result.elapsed = time.monotonic() - started

    log.info("扫描完成：成功 %s，失败 %s，耗时 %.1f 秒",
             result.ok_count, len(result.failed), result.elapsed)
    log.info("数据源命中：%s", providers.hit_summary())
    return result
