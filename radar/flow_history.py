"""板块资金流的本地按日快照。

为什么需要它：需求 4 要 A-3 / A-2 / A-1 / A 四个交易日的趋势，但没有一个接口能稳定给到。

    push2his 的 fflow  能给 10 天，但**间歇性** —— 同一分钟内 curl 时通时断
    push2delay 的 fflow 稳定，但不管 lmt 传多少都只返回最新 1 天
    个股资金流求和（分母）  只有当日，没有历史

所以改成：**每天跑的时候把当天的快照存下来，历史自己攒**。
工具本来就每个交易日跑两次，攒 4 个交易日就够了，而且比依赖单次请求可靠得多 ——
某天接口抽风只会丢那一天，不会让整张表出不来。

存储格式：`data/flow_history/YYYY-MM-DD.json`，一天一个文件，人可读、可手工修。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import DATA_DIR

log = logging.getLogger(__name__)

HISTORY_DIR = DATA_DIR / "flow_history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _path(trade_date: str) -> Path:
    return HISTORY_DIR / f"{trade_date}.json"


def save_snapshot(trade_date: str, market_total: Optional[float],
                  boards: Dict[str, float], board_names: Dict[str, str]) -> Path:
    """写入某个交易日的快照。同日重复运行会覆盖（午盘批被收盘批覆盖，这是想要的）。"""
    payload = {
        "trade_date": trade_date,
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "market_total": market_total,
        "boards": boards,
        "board_names": board_names,
    }
    p = _path(trade_date)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    log.info("已存快照 %s（%s 个板块，全市场基准 %s）", trade_date, len(boards),
             f"{market_total / 1e8:.1f} 亿" if market_total else "缺失")
    return p


def load_snapshot(trade_date: str) -> Optional[dict]:
    p = _path(trade_date)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except (ValueError, OSError) as exc:
        log.warning("快照 %s 损坏：%s", trade_date, exc)
        return None


def available_dates() -> List[str]:
    """本地已有的交易日，升序。"""
    return sorted(p.stem for p in HISTORY_DIR.glob("*.json"))


def recent_snapshots(n: int) -> List[Tuple[str, dict]]:
    """最近 n 个有快照的交易日，升序返回 [(日期, 快照), ...]。"""
    out = []
    for d in available_dates()[-n:]:
        snap = load_snapshot(d)
        if snap:
            out.append((d, snap))
    return out


MIN_BACKFILL_COVERAGE = 0.5


def backfill_from_api(flows: Dict[str, Dict[str, float]],
                      board_names: Dict[str, str],
                      total_boards: Optional[int] = None) -> int:
    """把接口偶尔给到的多日历史回填成快照。

    push2his 的 fflow 通的时候能一次给 10 天，顺手把这几天存下来，
    第一次部署就不用干等 4 个交易日。

    两个必须守住的门槛：

    1. **覆盖率门槛**。push2his 是间歇性的，实测一次回填里有的日期只拿到 1 个板块
       （504 个里的 1 个）。这种快照存下来会挤掉好数据，让表格看起来"有 4 天"
       其实全是空格。低于 50% 覆盖率的日期直接丢弃。
    2. **不覆盖已有快照**。真实快照带分母，回填的不带，覆盖了就是降级。

    回填出来的快照**没有 market_total**（分母只有当日接口能给），
    所以这些日期的占比列会留空，等工具每天运行攒出真实快照才会填上。
    """
    by_date: Dict[str, Dict[str, float]] = {}
    for code, per_date in flows.items():
        for d, v in per_date.items():
            by_date.setdefault(d, {})[code] = v

    threshold = int((total_boards or len(flows)) * MIN_BACKFILL_COVERAGE)
    written = skipped = 0
    for d, boards in sorted(by_date.items()):
        if load_snapshot(d) is not None:
            continue  # 已有真实快照，不降级
        if len(boards) < threshold:
            skipped += 1
            continue
        save_snapshot(d, None, boards, board_names)
        written += 1

    if written or skipped:
        log.info("回填历史：写入 %s 天，因覆盖率不足丢弃 %s 天（门槛 %s 个板块）",
                 written, skipped, threshold)
    return written


def prune(keep_days: int = 120) -> int:
    """只保留最近 N 个交易日的快照。"""
    files = sorted(HISTORY_DIR.glob("*.json"))
    stale = files[:-keep_days] if len(files) > keep_days else []
    for f in stale:
        f.unlink(missing_ok=True)
    return len(stale)
