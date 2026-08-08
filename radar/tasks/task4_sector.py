"""需求 4：板块热度变化（主力净流入额占全市场比例，A-3 / A-2 / A-1 / A 四天）。

看的是**占比**而不是绝对额，因为全市场资金总量每天都在变。
同样是净流入 20 亿，大盘净流出的日子里意味着资金在逆势往这个板块集中，
大盘普涨的日子里可能只是被动跟随。占比把这个背景剥离掉了。

数据来源分两部分：
  当日  接口实时拉（板块资金流 + 全市场个股求和）
  历史  本地按日快照（见 flow_history.py，因为没有接口能稳定给多日）
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from .. import flow_history
from ..config import SETTINGS
from ..excel import new_workbook, save, write_flat_sheet
from ..sectors import (fetch_board_flow, fetch_boards, fetch_many_flows,
                       market_total_today)

log = logging.getLogger(__name__)


def build(run_tag: str, kind: Optional[str] = None,
          output_dir: Optional[Path] = None) -> Optional[Path]:
    kind = kind or SETTINGS.sector_type
    days = SETTINGS.sector_days

    boards = fetch_boards(kind)
    if not boards:
        log.error("板块清单为空，跳过需求 4")
        return None
    board_names = {b.code: b.name for b in boards}

    # 多拉几天：push2his 通的时候能一次给 10 天，顺手回填历史，
    # 这样首次部署不用等 4 个交易日才有完整的表。
    flows = fetch_many_flows(boards, days=days + 6)
    if not flows:
        log.error("板块资金流全部失败，跳过需求 4")
        return None

    market_total = market_total_today()

    # --- 今日快照落库 + 历史回填 ---
    today_by_board, today_date = _today_slice(flows)
    if today_date:
        flow_history.save_snapshot(today_date, market_total, today_by_board, board_names)
    flow_history.backfill_from_api(flows, board_names, total_boards=len(boards))
    flow_history.prune()

    snapshots = flow_history.recent_snapshots(days)
    if not snapshots:
        log.error("本地无任何快照，无法出表")
        return None
    if len(snapshots) < days:
        log.warning("只有 %s 个交易日的快照（需要 %s）。工具每天运行会自动补齐，"
                    "再跑 %s 个交易日就完整了", len(snapshots), days, days - len(snapshots))

    dates = [d for d, _ in snapshots]
    labels = ["（A-3）日", "前日（A-2）", "昨日（A-1）", "今日（A）"][-len(dates):]

    headers = ["板块", "板块代码"] + [
        f"{lab}主力净流入额占全市场的比例  [{d}]" for lab, d in zip(labels, dates)
    ] + [f"今日（A）主力净流入额(亿元)  [{dates[-1]}]"]
    widths = [16, 11] + [30] * len(dates) + [28]

    rows = _build_rows(snapshots, board_names, dates)

    wb = new_workbook()
    sheet_name = "概念板块热度" if kind == "concept" else "行业板块热度"
    write_flat_sheet(wb.create_sheet(sheet_name), headers, rows, widths,
                     number_format="0.0000%",
                     col_formats={2 + len(dates) + 1: "0.00"})

    _append_readme(wb, run_tag, kind, dates, len(rows), snapshots, market_total)
    out = (output_dir or SETTINGS.output_dir) / f"04_板块热度_{run_tag}.xlsx"
    return save(wb, out)


def _today_slice(flows: Dict[str, Dict[str, float]]):
    """从多板块资金流里切出最新那个交易日。"""
    all_dates = set()
    for per_date in flows.values():
        all_dates.update(per_date.keys())
    if not all_dates:
        return {}, None
    latest = max(all_dates)
    return ({code: per[latest] for code, per in flows.items() if latest in per},
            latest)


def _build_rows(snapshots, board_names: Dict[str, str], dates: List[str]) -> List[list]:
    # 以最新一天出现过的板块为准，历史里消失的板块那一格留空
    latest_boards = snapshots[-1][1].get("boards", {})
    rows = []
    for code in latest_boards:
        name = board_names.get(code) or snapshots[-1][1].get("board_names", {}).get(code, code)
        ratios = []
        for _, snap in snapshots:
            total = snap.get("market_total")
            value = (snap.get("boards") or {}).get(code)
            if not total or value is None:
                ratios.append(None)   # 分母缺失（回填日）或该板块当天无数据
            else:
                ratios.append(round(value / abs(total), 6))
        today_value = latest_boards.get(code)
        rows.append([name, code] + ratios +
                    [round(today_value / 1e8, 4) if today_value is not None else None])

    # 按今日占比降序，最热的排最上面；无数据的沉底
    idx = 2 + len(dates) - 1
    rows.sort(key=lambda r: (r[idx] is None, -(r[idx] or 0)))
    return rows


def _append_readme(wb, run_tag, kind, dates, n_boards, snapshots, market_total) -> None:
    ws = wb.create_sheet("口径说明")
    lines = [
        ("生成批次", run_tag),
        ("板块类型", "概念板块" if kind == "concept" else "行业板块"),
        ("板块数量", f"{n_boards} 个"),
        ("覆盖交易日", " / ".join(dates) + (f"（不足 {SETTINGS.sector_days} 天，"
                                            f"每天运行会自动补齐）" if len(dates) < SETTINGS.sector_days else "")),
        ("数据源", "东方财富 板块资金流 fflow/daykline + 个股资金流 clist"),
        ("分子", "该板块当日主力净流入净额（超大单+大单）"),
        ("分母", "全市场当日主力净流入总额 = 沪深两市全部个股逐只求和"),
        ("⚠️ 分母不能用板块求和",
         "东财 m:90 t:2 返回的 496 个行业板块混着一级/二级/三级："
         "「电子」(423只) 和子板块「元件」(62只)、「半导体」(174只) 并列。"
         "成分股总数 8679 > 全市场 5547 只。实测板块求和会把分母放大 2.99 倍，"
         "占比系统性低估 67%"),
        ("分母取绝对值", "全市场净流出时分母为负，取 abs 保证占比正负号只反映板块自身方向"),
        ("负值含义", "该板块当日主力净流出"),
        ("占比之和", "不等于 100%，因为概念板块之间互相重叠"),
        ("历史从哪来", "本地按日快照（data/flow_history/）。"
                       "没有接口能稳定返回多日：push2his 间歇性，push2delay 只给 1 天。"
                       "工具每个交易日运行时存一份，历史自己攒"),
        ("空格含义", "该日快照缺分母（接口回填的历史日），或该板块当天无数据"),
    ]
    for d, snap in snapshots:
        mt = snap.get("market_total")
        lines.append((f"全市场基准 {d}", f"{mt / 1e8:.2f} 亿元" if mt else "缺失（回填日）"))
    for r, (k, v) in enumerate(lines, start=1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 90
