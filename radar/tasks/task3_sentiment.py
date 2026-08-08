"""需求 3：A 股上市公司网上咨询量的变化。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..config import SETTINGS
from ..excel import new_workbook, save, write_flat_sheet
from ..sentiment import measure_many
from ..universe import Stock, fetch_index_members, fetch_universe

log = logging.getLogger(__name__)

HEADERS = [
    "个股代码", "个股名称",
    "过去48小时内的咨询量（每天）",
    "过去2周且在48小时之前的平均咨询量（每天）",
    "升温倍数",
    "48小时内条数", "基准期条数", "是否触顶截断",
]
WIDTHS = [11, 14, 24, 32, 12, 14, 14, 14]


def resolve_universe(mode: Optional[str] = None) -> List[Stock]:
    """需求 3 的候选池。

    全市场 5500 只逐个走搜索接口会被限流（约 2.2 万次请求），
    所以默认只跑沪深300。要跑全市场用 --sentiment-universe all，并预留 1 小时以上。
    """
    mode = mode or SETTINGS.sentiment_universe
    if mode == "all":
        return fetch_universe()
    if mode == "hs300":
        members = fetch_index_members("1.000300")
        if members:
            return members
        log.warning("沪深300 成分股拉取失败，退回全市场前 300 只")
        return fetch_universe()[:300]
    if mode == "watchlist":
        return _load_watchlist()
    raise ValueError(f"未知的候选池：{mode}")


def _load_watchlist() -> List[Stock]:
    """自选股清单：data/watchlist.txt，每行 `代码,名称`。"""
    path = SETTINGS.output_dir.parent / "watchlist.txt"
    if not path.exists():
        log.error("自选股文件不存在：%s", path)
        return []
    stocks: List[Stock] = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        code = parts[0]
        name = parts[1] if len(parts) > 1 else code
        market = 1 if code.startswith(("6", "9")) else 0
        stocks.append(Stock(code=code, name=name, market=market, price=1.0))
    return stocks


def build(run_tag: str, mode: Optional[str] = None,
          output_dir: Optional[Path] = None) -> Optional[Path]:
    stocks = resolve_universe(mode)
    if not stocks:
        log.error("候选池为空，跳过需求 3")
        return None

    now = datetime.now()
    rows_raw = measure_many(stocks, now)
    rows = [
        [r["code"], r["name"], r.get("recent_per_day"), r.get("baseline_per_day"),
         r.get("ratio"), r.get("recent_count"), r.get("baseline_count"),
         "是" if r.get("truncated") else ""]
        for r in rows_raw
    ]

    wb = new_workbook()
    write_flat_sheet(wb.create_sheet("咨询量变化"), HEADERS, rows, WIDTHS, number_format="0.00")

    hot = sorted((r for r in rows if isinstance(r[4], (int, float))),
                 key=lambda r: r[4], reverse=True)[:100]
    write_flat_sheet(wb.create_sheet("升温TOP100"), HEADERS, hot, WIDTHS, number_format="0.00")

    _append_readme(wb, run_tag, mode or SETTINGS.sentiment_universe, len(rows), now)
    out = (output_dir or SETTINGS.output_dir) / f"03_咨询量变化_{run_tag}.xlsx"
    return save(wb, out)


def _append_readme(wb, run_tag, mode, n, now) -> None:
    ws = wb.create_sheet("口径说明")
    lines = [
        ("生成批次", run_tag),
        ("统计时刻", now.strftime("%Y-%m-%d %H:%M:%S")),
        ("候选池", {"all": "全市场 A 股", "hs300": "沪深300 成分股",
                    "watchlist": "自选股清单"}.get(mode, mode)),
        ("样本数", f"{n} 只"),
        ("数据源", "东方财富全站资讯检索（search-api-web），按股票名称匹配"),
        ("咨询量定义", "检索命中的资讯条数，代表市场注意力，不是精确的讨论人次"),
        ("近期窗口", f"最近 {SETTINGS.sentiment_recent_hours} 小时，换算成每日条数"),
        ("基准窗口", f"48 小时之前往前 {SETTINGS.sentiment_baseline_days} 天的日均条数"),
        ("升温倍数", "近期日均 ÷ 基准日均。>1 关注度上升，>3 通常已有明确事件驱动"),
        ("触顶截断", f"翻页上限 {4 * 100} 条。标『是』的说明实际条数更多，其倍数被低估"),
        ("已知偏差", "名称含通用词的公司会误命中（如『中国平安』与『平安』），横向比较时留意"),
        ("为何默认不跑全市场", "5500 只 × 多页检索约 2.2 万次请求，会被限流；默认沪深300"),
    ]
    for r, (k, v) in enumerate(lines, start=1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 80
