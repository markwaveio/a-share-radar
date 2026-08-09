"""多源 K 线提供方。

为什么需要多个源：2026-08-08 东财的批量/历史类接口（clist、ulist、kline）开始
对国内直连 IP 返回「Empty reply from server」—— TCP+TLS 握手成功，服务端直接关连接。
同一时刻单只实时类接口（stock/get、trends2）完全正常，所以不是网络故障，是接口级风控。

单一数据源是这类系统最大的单点故障。现在的分工：

    月/周/日   腾讯 fqkline（前复权）
    60/30/15   新浪 getKLineData
    120 分钟   由 60 分钟合成（逻辑不变）
    东财       仍然是首选，恢复后自动切回

**所有 provider 必须返回同一套 schema**，否则下游指标层要为每个源写一遍。
统一口径：列名固定，成交量统一为「手」。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

import pandas as pd

from .config import (KLT_15M, KLT_30M, KLT_60M, KLT_DAY, KLT_MONTH, KLT_WEEK,
                     SETTINGS)
from .http import get_json

log = logging.getLogger(__name__)

# 所有 provider 统一输出这套列
COLUMNS = ["date", "open", "close", "high", "low", "volume", "amount"]

EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_DELAY_KLINE_URL = "https://push2delay.eastmoney.com/api/qt/stock/kline/get"
# ⚠️ 必须用不带 web. 前缀的 ifzq.gtimg.cn：
#   - web.ifzq 上分钟线路径是 301 跳转
#   - web.ifzq 在稍高频率下会触发腾讯 WAF（返回跳转 waf.tencent.com 的 HTML 而非 JSON）
#   - ifzq.gtimg.cn 实测 20 次串行 0 失败
TENCENT_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_MINUTE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
SINA_KLINE_URL = ("https://money.finance.sina.com.cn/quotes_service/api/"
                  "json_v2.php/CN_MarketData.getKLineData")


def secid_to_symbol(secid: str) -> Optional[str]:
    """东财 secid -> 腾讯/新浪的 symbol。`1.600000` -> `sh600000`。"""
    try:
        market, code = secid.split(".", 1)
    except ValueError:
        return None
    prefix = {"1": "sh", "0": "sz"}.get(market)
    return f"{prefix}{code}" if prefix else None


def _finalize(rows: List[list], volume_divisor: float = 1.0) -> pd.DataFrame:
    """把原始行转成统一 schema。volume_divisor 用来把「股」折算成「手」。"""
    df = pd.DataFrame(rows, columns=COLUMNS)
    numeric = COLUMNS[1:]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce")
    if volume_divisor != 1.0:
        df["volume"] = df["volume"] / volume_divisor
    return df


# ------------------------------------------------------------------ 东方财富

_EM_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"


def eastmoney_kline(secid: str, klt: int, limit: int,
                    url: str = EASTMONEY_KLINE_URL) -> Optional[pd.DataFrame]:
    """东财原生接口。字段序：日期,开,收,高,低,量(手),额。"""
    payload = get_json(url, {
        "secid": secid, "klt": klt, "fqt": 1, "end": "20500101", "lmt": limit,
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": _EM_FIELDS2,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    })
    if not isinstance(payload, dict):
        return None
    # 注意 `or {}`：这台主机"没数据"有两种长相，两天内都观察到过
    #   {"rc":0,  ..., "data":{"code":"600000","klines":[]}}   业务码正常但列表为空
    #   {"rc":102,..., "data":null}                            data 直接是 null
    # 所以不能只判 rc，也不能假设 data 一定是 dict。最终判据只有一个：klines 非空。
    klines = (payload.get("data") or {}).get("klines")
    if not klines:
        return None
    return _finalize([line.split(",")[:7] for line in klines])


def eastmoney_delay_kline(secid: str, klt: int, limit: int) -> Optional[pd.DataFrame]:
    return eastmoney_kline(secid, klt, limit, url=EASTMONEY_DELAY_KLINE_URL)


# ---------------------------------------------------------------------- 腾讯

_TENCENT_PERIOD = {KLT_DAY: "day", KLT_WEEK: "week", KLT_MONTH: "month"}


def tencent_kline(secid: str, klt: int, limit: int) -> Optional[pd.DataFrame]:
    """腾讯前复权日/周/月线。

    返回体里的 key 带 qfq 前缀（qfqday / qfqweek / qfqmonth），
    字段序和东财完全一致：[日期, 开, 收, 高, 低, 量(手)]。
    """
    period = _TENCENT_PERIOD.get(klt)
    symbol = secid_to_symbol(secid)
    if period is None or symbol is None:
        return None

    payload = get_json(TENCENT_KLINE_URL,
                       {"param": f"{symbol},{period},,,{limit},qfq"})
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0"):
        return None

    block = (payload.get("data") or {}).get(symbol)
    if not isinstance(block, dict):
        return None
    # 优先带 qfq 前缀的 key；某些标的（如指数）只返回不带前缀的
    bars = block.get(f"qfq{period}") or block.get(period)
    if not bars:
        return None

    rows = []
    for b in bars:
        if len(b) < 6:
            continue
        rows.append([b[0], b[1], b[2], b[3], b[4], b[5], None])
    return _finalize(rows) if rows else None


_TENCENT_MINUTE = {KLT_60M: "m60", KLT_30M: "m30", KLT_15M: "m15"}


def tencent_minute_kline(secid: str, klt: int, limit: int) -> Optional[pd.DataFrame]:
    """腾讯分钟线。

    返回格式和日线不同：[时间, 开, 收, 高, 低, 量(手), {}, 额]，
    时间是 `202608071500` 这种紧凑格式，要转成标准时间戳才能给 resample_120m 用。

    实测每根 60 分钟量之和等于当日日线量（171496+70893+183907+139162 = 565458 vs 日线 565457），
    说明和日线是同一套口径。
    """
    period = _TENCENT_MINUTE.get(klt)
    symbol = secid_to_symbol(secid)
    if period is None or symbol is None:
        return None

    payload = get_json(TENCENT_MINUTE_URL, {"param": f"{symbol},{period},,{limit}"})
    if not isinstance(payload, dict):
        return None
    block = (payload.get("data") or {}).get(symbol)
    if not isinstance(block, dict):
        return None
    bars = block.get(period)
    if not bars:
        return None

    rows = []
    for b in bars:
        if len(b) < 6:
            continue
        ts = str(b[0])
        # 202608071500 -> 2026-08-07 15:00
        if len(ts) == 12 and ts.isdigit():
            ts = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
        rows.append([ts, b[1], b[2], b[3], b[4], b[5], None])
    return _finalize(rows) if rows else None


# ---------------------------------------------------------------------- 新浪

_SINA_SCALE = {KLT_15M: 15, KLT_30M: 30, KLT_60M: 60, KLT_DAY: 240}


def sina_kline(secid: str, klt: int, limit: int) -> Optional[pd.DataFrame]:
    """新浪分钟线（也支持 scale=240 的日线，作为腾讯的兜底）。

    ⚠️ 新浪的 volume 单位是**股**，东财和腾讯是**手**。
    不折算的话成交量 CV 本身不受影响（CV 无量纲），但「成交量标准差」列会差 100 倍，
    而且两个源混用时同一只股票前后不一致。所以这里统一 ÷100 折算成手。
    """
    scale = _SINA_SCALE.get(klt)
    symbol = secid_to_symbol(secid)
    if scale is None or symbol is None:
        return None

    payload = get_json(SINA_KLINE_URL,
                       {"symbol": symbol, "scale": scale, "ma": "no", "datalen": limit})
    if not isinstance(payload, list) or not payload:
        return None

    rows = []
    for bar in payload:
        if not isinstance(bar, dict):
            continue
        rows.append([bar.get("day"), bar.get("open"), bar.get("close"),
                     bar.get("high"), bar.get("low"), bar.get("volume"), None])
    return _finalize(rows, volume_divisor=100.0) if rows else None


# ------------------------------------------------------------------ 调度

Provider = Callable[[str, int, int], Optional[pd.DataFrame]]

# 每个周期的 provider 优先级。前一个拿不到就试下一个。
# 东财排第一：它恢复后无需改代码自动切回。
# 实测：全市场 5200 只压力下新浪 28 秒就被限流打死（20 次串行全失败），
# 腾讯 20 次串行 0 失败。所以腾讯做主源，新浪降为兜底。
PROVIDER_CHAIN: Dict[int, List[tuple]] = {
    KLT_MONTH: [("eastmoney", eastmoney_kline), ("tencent", tencent_kline)],
    KLT_WEEK:  [("eastmoney", eastmoney_kline), ("tencent", tencent_kline)],
    KLT_DAY:   [("eastmoney", eastmoney_kline), ("tencent", tencent_kline),
                ("sina", sina_kline)],
    KLT_60M:   [("eastmoney", eastmoney_kline), ("tencent_min", tencent_minute_kline),
                ("sina", sina_kline)],
    KLT_30M:   [("eastmoney", eastmoney_kline), ("tencent_min", tencent_minute_kline),
                ("sina", sina_kline)],
    KLT_15M:   [("eastmoney", eastmoney_kline), ("tencent_min", tencent_minute_kline),
                ("sina", sina_kline)],
}

# 统计每个 provider 的命中次数，运行结束时打日志 —— 用来发现"东财悄悄恢复了"
HIT_COUNTER: Dict[str, int] = {}

# ---------------------------------------------------------------- 熔断器
#
# 没有熔断器时，一个挂掉的源会让整轮跑不完：
# 每次请求要经过 3 次重试 + 指数退避 ≈ 2~4 秒，
# 5500 只 × 6 周期 = 3.3 万次无效等待 ≈ 20 小时。
#
# 规则：连续失败达到阈值就**暂时**摘掉，冷却一段时间后放一个探针请求过去。
# 探针成功就完全恢复，失败就继续冷却（半开状态）。
#
# 为什么必须可恢复：实测新浪在全市场负载下 28 秒就触发限流，
# 但它不是宕机 —— 缓一会儿就能用。永久熔断会白白丢掉一个可用的源。
# 区分"限流"和"宕机"的唯一办法就是过一会儿再试。
_consecutive_failures: Dict[str, int] = {}
_tripped_at: Dict[str, float] = {}   # name -> 熔断时刻
_probing: set = set()                # 冷却结束后正在放行的探针
_lock = threading.Lock()


def _is_open(name: str) -> bool:
    """True 表示当前应该跳过这个源。"""
    with _lock:
        t = _tripped_at.get(name)
        if t is None:
            return False
        if time.monotonic() - t < SETTINGS.provider_cooldown_seconds:
            return True
        # 冷却结束，同一时刻只放一个探针，避免所有线程一起冲上去又被限流
        if name in _probing:
            return True
        _probing.add(name)
        return False


def _record(name: str, ok: bool) -> None:
    with _lock:
        _probing.discard(name)
        if ok:
            if name in _tripped_at:
                log.info("数据源 %s 已恢复", name)
            _consecutive_failures[name] = 0
            _tripped_at.pop(name, None)
            HIT_COUNTER[name] = HIT_COUNTER.get(name, 0) + 1
            return
        n = _consecutive_failures.get(name, 0) + 1
        _consecutive_failures[name] = n
        if n >= SETTINGS.provider_failure_threshold:
            was_tripped = name in _tripped_at
            _tripped_at[name] = time.monotonic()
            if not was_tripped:
                log.warning("数据源 %s 连续失败 %s 次，熔断 %s 秒后重试",
                            name, n, SETTINGS.provider_cooldown_seconds)


def reset_breakers() -> None:
    """每轮运行开始时调用，给所有源一次重新证明自己的机会。"""
    with _lock:
        _consecutive_failures.clear()
        _tripped_at.clear()
        _probing.clear()
        HIT_COUNTER.clear()


def fetch(secid: str, klt: int, limit: Optional[int] = None) -> Optional[pd.DataFrame]:
    """按优先级依次尝试，返回第一个拿到数据的源。"""
    limit = limit or SETTINGS.kline_limit
    for name, provider in PROVIDER_CHAIN.get(klt, []):
        if name in SETTINGS.disabled_providers or _is_open(name):
            continue
        try:
            df = provider(secid, klt, limit)
        except Exception as exc:  # noqa: BLE001 - 单个源异常不该拖垮整条链
            log.debug("provider %s 异常 %s klt=%s: %s", name, secid, klt, exc)
            _record(name, False)
            continue
        if df is not None and not df.empty:
            _record(name, True)
            return df
        _record(name, False)
    return None


def hit_summary() -> str:
    if not HIT_COUNTER:
        return "无成功请求"
    total = sum(HIT_COUNTER.values())
    parts = [f"{k} {v}次({v / total:.0%})" for k, v in sorted(HIT_COUNTER.items(),
                                                              key=lambda x: -x[1])]
    return "  ".join(parts)
