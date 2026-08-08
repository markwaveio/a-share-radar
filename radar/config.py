"""全局配置。所有可调参数集中在这里，课程里讲"改哪里"只指这一个文件。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = DATA_DIR / "output"
LOG_DIR = DATA_DIR / "logs"

for _d in (CACHE_DIR, OUTPUT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 周期定义
# 需求表头的 10 个列组，顺序即 Excel 里的列顺序。
# source: 东财原生周期 klt；derive: 由更细周期合成。
KLT_MONTH = 103
KLT_WEEK = 102
KLT_DAY = 101
KLT_60M = 60
KLT_30M = 30
KLT_15M = 15

# (表头名, 数据来源 klt, 取倒数第几根)  offset=-1 是最新一根，-2 是上一根
TIMEFRAME_COLUMNS = [
    ("上一月", KLT_MONTH, -2),
    ("本月", KLT_MONTH, -1),
    ("上一周", KLT_WEEK, -2),
    ("本周", KLT_WEEK, -1),
    ("上一日", KLT_DAY, -2),
    ("本日", KLT_DAY, -1),
    ("120分钟", "120m", -1),  # 由 60 分钟线合成
    ("60分钟", KLT_60M, -1),
    ("30分钟", KLT_30M, -1),
    ("15分钟", KLT_15M, -1),
]

# 需要向东财实际请求的原生周期
FETCH_KLTS = [KLT_MONTH, KLT_WEEK, KLT_DAY, KLT_60M, KLT_30M, KLT_15M]


@dataclass
class Settings:
    # ---- KDJ 参数（通达信/东财默认 9,3,3）----
    kdj_n: int = 9
    kdj_m1: int = 3
    kdj_m2: int = 3

    # 拉多少根 K 线。KDJ 的 K/D 是 α=1/3 的递推平滑，
    # 30 根之后初始值 50 的影响衰减到 (2/3)^30 ≈ 5e-6，120 根足够收敛。
    kline_limit: int = 120

    # ---- 成交量变异系数窗口（需求 2）----
    cv_windows: tuple = (20, 30)
    cv_range_window: tuple = (40, 10)  # 过去 40~10 日
    spike_recent_days: int = 10        # 过去 10 日单日最大量

    # ---- 网络 ----
    concurrency: int = 12          # 并发请求数，东财会限流，别调太高
    timeout: float = 15.0
    max_retries: int = 3
    backoff_base: float = 0.8      # 指数退避基数（秒）
    qps_limit: float = 30.0        # 全局令牌桶上限
    use_system_proxy: bool = field(
        default_factory=lambda: os.getenv("RADAR_USE_PROXY", "1") != "0"
    )

    # ---- 股票池过滤 ----
    exclude_st: bool = False        # 是否剔除 ST/*ST
    exclude_suspended: bool = True  # 剔除停牌（无最新价）
    exclude_bj: bool = True         # 剔除北交所，需求只要深沪两市

    # ---- 需求 3：咨询量 ----
    # 全市场 5500 只逐个搜资讯会被限流，默认只跑候选池。
    sentiment_universe: str = "hs300"   # all | hs300 | watchlist
    sentiment_recent_hours: int = 48
    sentiment_baseline_days: int = 14

    # ---- 需求 4：板块热度 ----
    sector_type: str = "concept"   # concept(概念板块) | industry(行业板块)
    sector_days: int = 4           # A-3 / A-2 / A-1 / A

    # ---- 数据源 ----
    # 东财的批量/历史类接口（clist / ulist / kline）在部分网络下会被拒绝，
    # 表现为 TCP+TLS 握手成功但服务端直接关连接。所以走多源降级。
    # 把源名写进这里可以临时禁用某个源，用于对比验证：{"tencent"} / {"eastmoney"}
    disabled_providers: frozenset = frozenset()

    # 某个源连续失败多少次就本轮熔断。太小会把偶发抖动误判成宕机，
    # 太大则一个挂掉的源会拖垮整轮（每次失败要 3 次重试 + 退避 ≈ 2~4 秒）。
    provider_failure_threshold: int = 8
    # 熔断后冷却多久放探针重试。限流类故障通常几十秒就恢复。
    provider_cooldown_seconds: float = 90.0

    # 股票池和板块资金流走延时主机 push2delay —— 实测正常主机被拒时它仍可用。
    # 延时 15 分钟，而本项目最早的批次是 11:40（午盘收盘后 10 分钟），不受影响。
    use_delay_host: bool = True

    # ---- 输出 ----
    output_dir: Path = OUTPUT_DIR
    cache_dir: Path = CACHE_DIR
    cache_ttl_seconds: int = 60 * 30   # 同一次运行崩溃后可复用，避免重头再来

    # 调试：只跑前 N 只股票，0 表示全市场
    limit_symbols: int = 0


SETTINGS = Settings()
