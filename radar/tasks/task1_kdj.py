"""需求 1：沪深两市全部个股的多周期 KDJ 表。

上一月 / 本月 / 上一周 / 本周 / 上一日 / 本日 / 120分钟 / 60分钟 / 30分钟 / 15分钟
每个周期输出 K、D、J 三个值。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from ..config import SETTINGS
from ..excel import new_workbook, save, write_kdj_sheet
from ..scan import ScanResult

log = logging.getLogger(__name__)


def build(result: ScanResult, run_tag: str, output_dir: Optional[Path] = None) -> Path:
    wb = new_workbook()
    ws = wb.create_sheet("KDJ全周期")
    write_kdj_sheet(ws, result.kdj_rows)

    _append_readme(wb, result, run_tag)

    out = (output_dir or SETTINGS.output_dir) / f"01_KDJ全周期_{run_tag}.xlsx"
    return save(wb, out)


def _append_readme(wb, result: ScanResult, run_tag: str) -> None:
    """把口径写进文件里。Excel 传给别人看时，口径不能只存在于文档里。"""
    ws = wb.create_sheet("口径说明")
    lines: Sequence[Sequence[str]] = [
        ("生成批次", run_tag),
        ("样本数", f"{result.ok_count} 只"),
        ("抓取失败", f"{len(result.failed)} 只"),
        ("耗时", f"{result.elapsed:.1f} 秒"),
        ("数据源", "东方财富 push2his K线接口，前复权（fqt=1）"),
        ("KDJ 参数", f"N={SETTINGS.kdj_n}, M1={SETTINGS.kdj_m1}, M2={SETTINGS.kdj_m2}（通达信默认）"),
        ("K/D 平滑", "K=((M1-1)·K_prev+RSV)/M1，初值 50，非简单移动平均"),
        ("120分钟", "东财无原生 120 分钟线，由 60 分钟线每 2 根合成（上午/下午各一根）"),
        ("本月/本周/本日", "指当前尚未走完的周期，盘中会随行情变动"),
        ("空值含义", "该周期 K 线数量不足（新股或长期停牌），留空而非填 0"),
    ]
    for r, (k, v) in enumerate(lines, start=1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 70
