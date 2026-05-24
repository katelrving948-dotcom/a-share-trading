"""
数据缓存层 - Data Cache
========================
TTL 缓存，减少重复API请求，加速页面加载。
"""

import time
import functools
from typing import Any, Dict, Optional


class Cache:
    """简单的 TTL 缓存"""

    def __init__(self, ttl: int = 60):
        self._store: Dict[str, dict] = {}
        self.default_ttl = ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry["expires"]:
            del self._store[key]
            return None
        return entry["value"]

    def set(self, key: str, value: Any, ttl: int = None):
        self._store[key] = {
            "value": value,
            "expires": time.time() + (ttl or self.default_ttl),
        }

    def clear(self, prefix: str = None):
        if prefix:
            self._store = {k: v for k, v in self._store.items()
                          if not k.startswith(prefix)}
        else:
            self._store.clear()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


# 全局缓存实例
# 股票列表: 300s | 板块: 300s | K线: 60s | 资金流: 120s
stock_cache = Cache(ttl=300)
kline_cache = Cache(ttl=60)
fund_cache = Cache(ttl=120)


def cached(cache_obj: Cache, key_prefix: str = "", ttl: int = None):
    """缓存装饰器"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{key_prefix}:{args}:{kwargs}" if key_prefix else f"{func.__name__}:{args}:{kwargs}"
            result = cache_obj.get(key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            cache_obj.set(key, result, ttl)
            return result
        return wrapper
    return decorator
