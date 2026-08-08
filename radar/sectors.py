"""板块资金流（需求 4）。

要算「某板块主力净流入额占全市场的比例」，难点在分母：
东财没有直接给"全市场主力净流入总额"的历史序列。

这里用的口径是：**全市场 = 所有行业板块之和**。
东财的行业板块对 A 股是完整划分（每只股票恰好归属一个行业板块），
所以行业板块求和就等于全市场，且和分子取自同一套接口、同一套统计规则，量纲一致。
换成"概念板块求和"会严重高估，因为一只股票可以同时属于几十个概念板块。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import SETTINGS
from .http import get_json

log = logging.getLogger(__name__)

# 延时主机优先：实测正常主机被拒时，delay 主机的板块清单和资金流都完整可用。
# 资金流是日频数据，15 分钟延时对收盘批（15:15）毫无影响。
CLIST_URLS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]
# ⚠️ 资金流历史必须 push2his 优先：delay 主机不管 lmt 传多少都只返回最新 1 天，
# 而我们要的是 A-3 ~ A 四个交易日的趋势。
# （封锁粒度是按路径的：同一台 push2his 上 kline 被拒，fflow 却完全正常。）
FFLOW_URLS = [
    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get",
]

# 板块清单单页上限 100（传 pz=600 也只返回 100），必须分页
BOARD_PAGE_SIZE = 100


def _try_hosts(urls, params):
    """依次试各主机，返回第一个带 data 的响应。"""
    for url in urls:
        payload = get_json(url, params)
        if isinstance(payload, dict) and payload.get("data"):
            return payload
    return None

BOARD_FS = {"industry": "m:90 t:2", "concept": "m:90 t:3"}
_UT = "b2884a393a59ad64002292a3e90d46a5"


@dataclass(frozen=True)
class Board:
    code: str   # BK0465
    name: str   # 创新药

    @property
    def secid(self) -> str:
        return f"90.{self.code}"


def fetch_boards(kind: str = "concept") -> List[Board]:
    """拉板块清单。kind = concept(概念) | industry(行业)。

    概念板块有 500+ 个，单页上限 100，必须翻页。
    不分页会静默只拿到前 100 个 —— 表格看起来正常，但漏掉了 80% 的板块。
    """
    boards: List[Board] = []
    page, total = 1, None
    while True:
        payload = _try_hosts(CLIST_URLS, {
            "pn": page, "pz": BOARD_PAGE_SIZE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f62", "ut": _UT,
            "fs": BOARD_FS[kind],
            "fields": "f12,f14,f62",
        })
        if not payload or not payload.get("data"):
            log.error("板块清单第 %s 页拉取失败 kind=%s", page, kind)
            break

        data = payload["data"]
        if total is None:
            total = data.get("total", 0)
        rows = data.get("diff") or []
        if not rows:
            break
        boards.extend(Board(code=str(r["f12"]), name=str(r["f14"])) for r in rows)

        if len(boards) >= (total or 0) or len(rows) < BOARD_PAGE_SIZE:
            break
        page += 1

    # 分页边界偶有重复
    seen, unique = set(), []
    for b in boards:
        if b.code not in seen:
            seen.add(b.code)
            unique.append(b)

    log.info("%s板块共 %s 个（接口声明 %s）",
             "概念" if kind == "concept" else "行业", len(unique), total)
    return unique


def fetch_board_flow(board: Board, days: int = 10) -> Dict[str, float]:
    """某板块最近 N 个交易日的主力净流入净额（元）。返回 {日期: 净额}。

    fields2 顺序：f51=日期, f52=主力净流入净额, f53=小单, f54=中单, f55=大单, f56=超大单
    """
    payload = _try_hosts(FFLOW_URLS, {
        "lmt": days, "klt": 101, "secid": board.secid, "ut": _UT,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    })
    if not payload or not payload.get("data") or not payload["data"].get("klines"):
        return {}

    out: Dict[str, float] = {}
    for line in payload["data"]["klines"]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            out[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return out


def fetch_many_flows(boards: List[Board], days: int = 10) -> Dict[str, Dict[str, float]]:
    """并发拉多个板块的资金流历史，返回 {板块代码: {日期: 净额}}。"""
    results: Dict[str, Dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=SETTINGS.concurrency) as pool:
        futures = {pool.submit(fetch_board_flow, b, days): b for b in boards}
        for fut in as_completed(futures):
            board = futures[fut]
            try:
                flow = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("板块资金流失败 %s %s: %s", board.code, board.name, exc)
                continue
            if flow:
                results[board.code] = flow
    return results


STOCK_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"


def market_total_today() -> Optional[float]:
    """今日全市场主力净流入总额 = 所有个股之和（元）。

    ⚠️ 不能用「所有行业板块求和」——这是本项目早期文档里写错的口径。
    实测东财 `m:90 t:2` 返回 496 个行业板块，里面混着一级/二级/三级：
    「电子」(423只) 和它的子板块「元件」(62只)、「半导体」(174只) 并列返回。
    496 个板块的成分股总数是 8679，而全市场只有 5547 只 —— 重复计算了。

    实测差距：个股求和 364.4 亿，行业板块求和 1090.5 亿（**放大 2.99 倍**），
    概念板块求和 6216.1 亿（放大 17 倍）。
    用错分母不会报错，只会让所有占比系统性低估 67%。

    个股逐只求和是唯一自洽的口径，而且只要 56 次分页请求。
    """
    total, page, count, declared, seen = 0.0, 1, 0, None, 0
    while True:
        payload = _try_hosts(CLIST_URLS, {
            "pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f62", "ut": _UT, "fs": STOCK_FS, "fields": "f12,f62",
        })
        if not payload or not payload.get("data"):
            log.error("全市场资金流第 %s 页失败", page)
            return None
        data = payload["data"]
        if declared is None:
            declared = data.get("total", 0)
        rows = data.get("diff") or []
        if not rows:
            break
        seen += len(rows)
        for r in rows:
            v = r.get("f62")
            if isinstance(v, (int, float)):
                total += v
                count += 1
        if seen >= (declared or 0) or len(rows) < 100:
            break
        page += 1

    # 分页是否翻完，看的是**遍历到的行数**；count 只是其中有资金流数据的。
    # 停牌股 f62 返回 "-"，不计入求和是对的，不该当成"统计不全"报警。
    if declared and seen < declared:
        log.warning("全市场资金流分页未翻完：%s/%s 行，分母可能失真", seen, declared)
    log.info("全市场主力净流入总额 %.1f 亿元（%s 只有数据 / 共 %s 只）",
             total / 1e8, count, seen)
    return total


def recent_trading_dates(flows: Dict[str, Dict[str, float]], n: int) -> List[str]:
    """从已抓到的数据里取最近 n 个交易日（不猜日历，以数据为准）。"""
    dates = set()
    for per_date in flows.values():
        dates.update(per_date.keys())
    return sorted(dates)[-n:]
