"""股票池：拉取沪深两市全部 A 股。

东财的市场代码：
    m:1 t:2   上交所主板      m:1 t:23  科创板
    m:0 t:6   深交所主板      m:0 t:80  创业板
    m:0 t:81  北交所（需求只要深沪，默认剔除）
secid 的写法是 `{市场}.{代码}`，市场 1=上海 0=深圳。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

from .config import SETTINGS
from .http import get_json

log = logging.getLogger(__name__)

# push2delay 是东财的延时行情主机。实测：正常主机 push2 的 clist 被拒时
# （TCP+TLS 握手成功但服务端直接关连接），delay 主机上的同一接口仍返回完整数据。
# 延时 15 分钟，而股票池本身是静态数据，完全不受影响。
CLIST_URL_PRIMARY = "https://push2.eastmoney.com/api/qt/clist/get"
CLIST_URL_DELAY = "https://push2delay.eastmoney.com/api/qt/clist/get"


def _clist_urls():
    """优先级顺序。开启 use_delay_host 时把 delay 排前面。"""
    if SETTINGS.use_delay_host:
        return [CLIST_URL_DELAY, CLIST_URL_PRIMARY]
    return [CLIST_URL_PRIMARY, CLIST_URL_DELAY]

MARKET_FILTERS = {
    "sh_main": "m:1 t:2",
    "sh_star": "m:1 t:23",
    "sz_main": "m:0 t:6",
    "sz_gem": "m:0 t:80",
    "bj": "m:0 t:81 s:2048",
}


@dataclass(frozen=True)
class Stock:
    code: str        # 600000
    name: str        # 浦发银行
    market: int      # 1=上海 0=深圳
    price: float     # 最新价，停牌时东财返回 "-"

    @property
    def secid(self) -> str:
        return f"{self.market}.{self.code}"

    @property
    def is_st(self) -> bool:
        return "ST" in self.name.upper()


def _fs_expression() -> str:
    keys = ["sh_main", "sh_star", "sz_main", "sz_gem"]
    if not SETTINGS.exclude_bj:
        keys.append("bj")
    return ",".join(MARKET_FILTERS[k] for k in keys)


def fetch_universe(use_cache: bool = True) -> List[Stock]:
    """分页拉全市场清单。东财单页上限 100，5500 只约 56 页。"""
    fs = _fs_expression()
    stocks: List[Stock] = []
    page, page_size, total = 1, 100, None

    while True:
        params = {
            "pn": page, "pz": page_size, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f12",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fs": fs,
            "fields": "f12,f13,f14,f2",
        }
        payload = None
        for url in _clist_urls():
            payload = get_json(url, params, use_cache=use_cache)
            if payload and payload.get("data"):
                break
        if not payload or not payload.get("data"):
            log.error("股票池第 %s 页拉取失败（主机全部不可用）", page)
            break

        data = payload["data"]
        if total is None:
            total = data.get("total", 0)
            log.info("全市场 A 股共 %s 只", total)

        rows = data.get("diff") or []
        if not rows:
            break

        for row in rows:
            price = row.get("f2")
            stocks.append(
                Stock(
                    code=str(row["f12"]),
                    name=str(row["f14"]),
                    market=int(row["f13"]),
                    price=float(price) if isinstance(price, (int, float)) else 0.0,
                )
            )

        if len(stocks) >= (total or 0) or len(rows) < page_size:
            break
        page += 1

    return _apply_filters(stocks)


def _apply_filters(stocks: List[Stock]) -> List[Stock]:
    before = len(stocks)
    if SETTINGS.exclude_suspended:
        stocks = [s for s in stocks if s.price > 0]
    if SETTINGS.exclude_st:
        stocks = [s for s in stocks if not s.is_st]

    # 去重：东财偶尔在分页边界返回重复行
    seen, unique = set(), []
    for s in stocks:
        if s.secid not in seen:
            seen.add(s.secid)
            unique.append(s)

    if SETTINGS.limit_symbols and len(unique) > SETTINGS.limit_symbols:
        # 等距抽样而不是取前 N 只。
        # 接口按代码排序返回，直接切前 N 只会拿到清一色 688xxx 科创板，
        # 样本有偏 —— 演示和自检时看不出主板/创业板的问题。
        unique.sort(key=lambda s: s.code)
        step = len(unique) / SETTINGS.limit_symbols
        unique = [unique[int(i * step)] for i in range(SETTINGS.limit_symbols)]

    log.info("股票池过滤：%s -> %s", before, len(unique))
    return unique


# 指数成分股要用**板块代码**，不是指数代码。
# `fs=i:1.000300` 返回的是沪深300**指数本身**（1 条记录），不是它的 300 只成分股 ——
# 这个坑不会报错，只会让需求 3 静默地只查 1 只股票。
INDEX_BOARD = {
    "hs300": "BK0500",   # 沪深300
    "sz50": "BK0611",    # 上证50
    "zz500": "BK0701",   # 中证500
}


def fetch_index_members(index_key: str = "hs300") -> List[Stock]:
    """指数成分股，用于需求 3 的候选池（默认沪深300）。"""
    board = INDEX_BOARD.get(index_key, index_key)
    # 单页上限 100（传 pz=500 也只返回 100），沪深300 要翻 3 页。
    # 不分页会静默只拿到前 100 只 —— 表看起来正常，但漏了 2/3。
    members: List[Stock] = []
    page, total = 1, None
    while True:
        params = {
            "pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fs": f"b:{board}",
            "fields": "f12,f13,f14,f2",
        }
        payload = None
        for url in _clist_urls():
            payload = get_json(url, params)
            if payload and payload.get("data"):
                break
        if not payload or not payload.get("data"):
            if page == 1:
                log.warning("指数成分股拉取失败 %s (板块 %s)", index_key, board)
                return []
            break

        data = payload["data"]
        if total is None:
            total = data.get("total", 0)
        rows = data.get("diff") or []
        if not rows:
            break
        members.extend(
            Stock(code=str(r["f12"]), name=str(r["f14"]), market=int(r["f13"]),
                  price=float(r["f2"]) if isinstance(r.get("f2"), (int, float)) else 0.0)
            for r in rows
        )
        if len(members) >= (total or 0) or len(rows) < 100:
            break
        page += 1

    # 分页边界偶有重复
    seen = set()
    unique_members = []
    for m in members:
        if m.code not in seen:
            seen.add(m.code)
            unique_members.append(m)
    members = unique_members
    # 只拿到个位数几乎肯定是表达式写错了（比如误用指数代码），不是真的成分股少
    if 0 < len(members) < 10:
        log.warning("指数 %s 只返回 %s 只成分股，疑似表达式有误", index_key, len(members))
    else:
        log.info("指数 %s 成分股 %s 只", index_key, len(members))
    return members
