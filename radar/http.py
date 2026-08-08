"""HTTP 层：限流 + 重试 + 代理容错 + 磁盘缓存。

抓 5500 只股票 × 6 个周期 ≈ 3.3 万次请求，这一层的稳定性决定整个任务能不能跑完。
三条保障：
  1. 令牌桶限流，避免被东财封 IP；
  2. 指数退避重试，网络抖动不至于整轮失败；
  3. 磁盘缓存，中途崩溃重跑不用从头开始。
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .config import SETTINGS

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class TokenBucket:
    """全局 QPS 限流。多线程共享一个桶。"""

    def __init__(self, rate: float, capacity: Optional[float] = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            time.sleep(min(wait, 0.5))


_bucket = TokenBucket(SETTINGS.qps_limit)
_local = threading.local()


def _session() -> requests.Session:
    """每个线程一个 Session，复用连接池。"""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(_HEADERS)
        s.trust_env = SETTINGS.use_system_proxy
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=SETTINGS.concurrency * 2,
            pool_maxsize=SETTINGS.concurrency * 2,
            max_retries=0,  # 重试逻辑自己控制，便于退避和日志
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _local.session = s
    return s


def _cache_path(url: str, params: Dict[str, Any]) -> Path:
    key = hashlib.sha1(f"{url}?{sorted(params.items())}".encode()).hexdigest()[:20]
    return SETTINGS.cache_dir / f"{key}.json"


def get_json(
    url: str,
    params: Dict[str, Any],
    *,
    use_cache: bool = True,
    jsonp: bool = False,
) -> Optional[dict]:
    """带缓存和重试的 GET。返回 None 表示彻底失败（调用方决定跳过还是中止）。"""
    cache_file = _cache_path(url, params) if use_cache else None
    if cache_file is not None and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < SETTINGS.cache_ttl_seconds:
            try:
                return json.loads(cache_file.read_text("utf-8"))
            except (ValueError, OSError):
                cache_file.unlink(missing_ok=True)

    last_err: Optional[str] = None
    for attempt in range(SETTINGS.max_retries):
        _bucket.acquire()
        try:
            resp = _session().get(url, params=params, timeout=SETTINGS.timeout)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                raise requests.RequestException(last_err)
            text = resp.text
            if jsonp:  # 形如 cb({...})
                start, end = text.find("("), text.rfind(")")
                if start == -1 or end == -1:
                    raise ValueError("非法 jsonp 响应")
                text = text[start + 1 : end]
            data = json.loads(text)
            if cache_file is not None:
                try:
                    cache_file.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
                except OSError:
                    pass
            return data
        except Exception as exc:  # noqa: BLE001 - 网络层要吞掉所有异常再决定重试
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < SETTINGS.max_retries - 1:
                delay = SETTINGS.backoff_base * (2 ** attempt) + random.uniform(0, 0.4)
                time.sleep(delay)

    log.warning("请求失败 %s params=%s err=%s", url, _brief(params), last_err)
    return None


def _brief(params: Dict[str, Any]) -> str:
    keys = ("secid", "klt", "fs", "pn")
    return ",".join(f"{k}={params[k]}" for k in keys if k in params)


def clear_cache() -> int:
    """清空磁盘缓存，返回删除的文件数。"""
    n = 0
    for f in SETTINGS.cache_dir.glob("*.json"):
        f.unlink(missing_ok=True)
        n += 1
    return n
