#!/usr/bin/env python3
"""A股行情雷达 —— 命令行入口。

    python3 run.py --task all                     # 四个需求全跑
    python3 run.py --task 1,2                     # 只跑 KDJ + 成交量
    python3 run.py --task 1 --limit 50            # 只测 50 只，验证链路用
    python3 run.py --task 4 --sector industry     # 行业板块而非概念板块
    python3 run.py --task 3 --sentiment-universe watchlist
    python3 run.py --clear-cache
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from radar.config import LOG_DIR, SETTINGS
from radar.http import clear_cache
from radar.scan import scan_market
from radar.tasks import task1_kdj, task2_volume, task3_sentiment, task4_sector

log = logging.getLogger("radar")


def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"run_{datetime.now():%Y%m%d}.log"
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(logfile, encoding="utf-8")]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def run_tag(session: str) -> str:
    """批次标识。midday=午盘，close=收盘，manual=手动。"""
    return f"{datetime.now():%Y%m%d}_{session}"


def infer_session() -> str:
    """按运行时刻自动判断是午盘批还是收盘批。"""
    hour = datetime.now().hour
    if hour < 13:
        return "midday"
    if hour < 18:
        return "close"
    return "manual"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A股行情雷达", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--task", default="all", help="1/2/3/4 或 all，逗号分隔")
    parser.add_argument("--session", default=None, choices=["midday", "close", "manual"],
                        help="批次标识，默认按当前时间推断")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只股票（调试）")
    parser.add_argument("--concurrency", type=int, default=None, help="并发数，默认 12")
    parser.add_argument("--sector", default=None, choices=["concept", "industry"])
    parser.add_argument("--sentiment-universe", default=None,
                        choices=["all", "hs300", "watchlist"])
    parser.add_argument("--output", default=None, help="输出目录，默认 data/output")
    parser.add_argument("--no-cache", action="store_true", help="忽略磁盘缓存")
    parser.add_argument("--clear-cache", action="store_true", help="清空缓存后退出")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.clear_cache:
        log.info("已清除 %s 个缓存文件", clear_cache())
        return 0

    if args.limit:
        SETTINGS.limit_symbols = args.limit
    if args.concurrency:
        SETTINGS.concurrency = args.concurrency
    if args.no_cache:
        SETTINGS.cache_ttl_seconds = 0
    if args.sector:
        SETTINGS.sector_type = args.sector
    if args.sentiment_universe:
        SETTINGS.sentiment_universe = args.sentiment_universe

    output_dir = Path(args.output) if args.output else SETTINGS.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    tasks = {"1", "2", "3", "4"} if args.task == "all" else {t.strip() for t in args.task.split(",")}
    unknown = tasks - {"1", "2", "3", "4"}
    if unknown:
        parser.error(f"未知任务：{','.join(sorted(unknown))}")

    session = args.session or infer_session()
    tag = run_tag(session)
    started = time.monotonic()
    produced = []

    log.info("=" * 72)
    log.info("批次 %s  任务 %s  输出 %s", tag, ",".join(sorted(tasks)), output_dir)
    log.info("=" * 72)

    # 需求 1 和 2 共用一次全市场扫描
    if tasks & {"1", "2"}:
        result = scan_market()
        if result.ok_count == 0:
            log.error("全市场扫描没有拿到任何数据，检查网络/代理后重试")
        else:
            if "1" in tasks:
                produced.append(task1_kdj.build(result, tag, output_dir))
            if "2" in tasks:
                produced.append(task2_volume.build(result, tag, output_dir))

    if "3" in tasks:
        path = task3_sentiment.build(tag, SETTINGS.sentiment_universe, output_dir)
        if path:
            produced.append(path)

    if "4" in tasks:
        path = task4_sector.build(tag, SETTINGS.sector_type, output_dir)
        if path:
            produced.append(path)

    elapsed = time.monotonic() - started
    log.info("=" * 72)
    if produced:
        log.info("完成，耗时 %.1f 秒，生成 %s 个文件：", elapsed, len(produced))
        for p in produced:
            log.info("  %s", p)
    else:
        log.error("没有生成任何文件，耗时 %.1f 秒", elapsed)
    log.info("=" * 72)
    return 0 if produced else 1


if __name__ == "__main__":
    sys.exit(main())
