"""咨询量（需求 3）：网上对某只股票的资讯讨论热度变化。

口径说明——需求里的"咨询量"没有官方统计口径，这里落地为
**东方财富全站资讯检索命中的文章条数**（财经媒体报道 + 东财自有内容）。
它是"市场注意力"的代理变量，不是精确计数，横向比较（谁更热）比绝对值更有意义。

对比的是：过去 48 小时的日均条数 vs 再往前两周的日均条数。
比值 > 1 说明关注度正在升温。
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .config import SETTINGS
from .http import get_json
from .universe import Stock

log = logging.getLogger(__name__)

SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
PAGE_SIZE = 100
MAX_PAGES = 4  # 400 条足够覆盖绝大多数个股的 16 天窗口


def _build_param(keyword: str, page: int) -> str:
    """返回**未编码**的 JSON 字符串。

    ⚠️ 这里绝对不能自己 urlencode：requests 传 params 时会再编码一次，
    双重转义后东财收到的是 `%257B...` 而不是 `%7B...`，
    接口会返回空结果 —— 而且不报错，只是每只股票都查不到文章。
    这个 bug 的表现是整张表全空但日志一切正常。
    """
    payload = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "time",
                "pageIndex": page,
                "pageSize": PAGE_SIZE,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _fetch_article_dates(keyword: str, cutoff: datetime) -> List[datetime]:
    """按时间倒序翻页，直到翻过 cutoff 或到达页数上限。"""
    dates: List[datetime] = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(
            SEARCH_URL,
            {"cb": "cb", "param": _build_param(keyword, page)},
            jsonp=True,
        )
        if not data or data.get("code") not in (0, "0"):
            break
        articles = (data.get("result") or {}).get("cmsArticleWebOld") or []
        if not articles:
            break

        oldest: Optional[datetime] = None
        for art in articles:
            raw = art.get("date") or art.get("showTime")
            parsed = _parse_time(raw)
            if parsed is None:
                continue
            dates.append(parsed)
            if oldest is None or parsed < oldest:
                oldest = parsed

        if len(articles) < PAGE_SIZE:
            break
        if oldest is not None and oldest < cutoff:
            break  # 已经翻过窗口左边界，不用继续
    return dates


def _parse_time(raw) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def measure_one(stock: Stock, now: datetime) -> Dict[str, Optional[float]]:
    recent_hours = SETTINGS.sentiment_recent_hours
    baseline_days = SETTINGS.sentiment_baseline_days

    recent_start = now - timedelta(hours=recent_hours)
    baseline_start = recent_start - timedelta(days=baseline_days)

    dates = _fetch_article_dates(stock.name, baseline_start)
    if not dates:
        return {"code": stock.code, "name": stock.name,
                "recent_per_day": None, "baseline_per_day": None, "ratio": None}

    recent = [d for d in dates if d >= recent_start]
    baseline = [d for d in dates if baseline_start <= d < recent_start]

    recent_per_day = round(len(recent) / (recent_hours / 24), 2)
    baseline_per_day = round(len(baseline) / baseline_days, 2)
    ratio = round(recent_per_day / baseline_per_day, 2) if baseline_per_day > 0 else None

    return {
        "code": stock.code, "name": stock.name,
        "recent_per_day": recent_per_day,
        "baseline_per_day": baseline_per_day,
        "ratio": ratio,
        "recent_count": len(recent),
        "baseline_count": len(baseline),
        "truncated": len(dates) >= MAX_PAGES * PAGE_SIZE,
    }


def measure_many(stocks: List[Stock], now: Optional[datetime] = None) -> List[dict]:
    now = now or datetime.now()
    # 搜索接口比行情接口脆弱，并发压到一半
    workers = max(2, SETTINGS.concurrency // 2)
    log.info("咨询量检索 %s 只股票，并发 %s", len(stocks), workers)

    rows: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(measure_one, s, now): s for s in stocks}
        for i, fut in enumerate(as_completed(futures), 1):
            stock = futures[fut]
            try:
                rows.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                log.warning("咨询量失败 %s %s: %s", stock.code, stock.name, exc)
            if i % 50 == 0:
                log.info("咨询量进度 %s/%s", i, len(stocks))

    rows.sort(key=lambda r: r["code"])
    return rows
