"""需求 2：成交量异动跟踪（变异系数 CV + 放量倍数）。

四个指标：
    过去 20 日 CV、过去 30 日 CV、过去 40~10 日 CV、
    （过去 10 日单日最大量）/（过去 40~10 日平均量）

前三个衡量"平时波动有多大"，第四个衡量"最近这一下有多突然"。
两者结合才有意义：一只平时 CV 就很高的股票放量，信息量远低于一只平时很安静的股票放量。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..config import SETTINGS
from ..excel import new_workbook, save, write_flat_sheet
from ..scan import ScanResult

log = logging.getLogger(__name__)

HEADERS = [
    "个股代码", "个股名称",
    "过去20日成交量变异系数(CV)",
    "过去30日成交量变异系数(CV)",
    "过去40~10日间的成交量变异系数(CV)",
    "(过去10日中单日成交量的最大值)/(过去40~10日间的平均成交量)",
    "近30日成交量标准差(手)",
]
WIDTHS = [11, 14, 24, 24, 30, 46, 20]


def build(result: ScanResult, run_tag: str, output_dir: Optional[Path] = None) -> Path:
    rows = [
        [r["code"], r["name"], r.get("cv_20"), r.get("cv_30"),
         r.get("cv_range"), r.get("spike_ratio"), r.get("vol_std")]
        for r in result.volume_rows
    ]

    # CV 和倍数保留 4 位小数；成交量标准差是「手」，4 位小数没有意义
    formats = {7: "#,##0"}

    wb = new_workbook()
    write_flat_sheet(wb.create_sheet("成交量异动"), HEADERS, rows, WIDTHS, col_formats=formats)

    # 放量榜：按 spike_ratio 降序，学员日常只看这一页
    ranked = sorted(
        (r for r in rows if isinstance(r[5], (int, float))),
        key=lambda r: r[5], reverse=True,
    )[:200]
    write_flat_sheet(wb.create_sheet("放量TOP200"), HEADERS, ranked, WIDTHS, col_formats=formats)

    _append_readme(wb, result, run_tag)
    out = (output_dir or SETTINGS.output_dir) / f"02_成交量异动_{run_tag}.xlsx"
    return save(wb, out)


def _append_readme(wb, result: ScanResult, run_tag: str) -> None:
    ws = wb.create_sheet("口径说明")
    far, near = SETTINGS.cv_range_window
    lines = [
        ("生成批次", run_tag),
        ("样本数", f"{result.ok_count} 只"),
        ("数据源", "东方财富日线成交量（手），与需求 1 复用同一次抓取"),
        ("CV 定义", "变异系数 = 样本标准差(ddof=1) / 均值，无量纲，可跨股票横向比较"),
        ("过去40~10日", f"第 {far} 个交易日前 到 第 {near} 个交易日前，共 {far - near} 根，不含最近 {near} 天"),
        ("放量倍数", f"最近 {SETTINGS.spike_recent_days} 日单日最大成交量 ÷ 过去 {far}~{near} 日平均成交量"),
        ("为什么排除最近10天", "基准期必须干净。若把异动本身算进基准，倍数会被自己稀释"),
        ("空值含义", "上市不足所需交易日数，样本不够时不给数，避免小样本失真"),
    ]
    for r, (k, v) in enumerate(lines, start=1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 76
