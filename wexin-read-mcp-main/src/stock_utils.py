"""
股票数据工具函数 — 来自 cn-financial-mcp 项目，本地化副本。

包含：股票代码规范化、TTL 缓存、多源降级调用。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger("stock-service")

# ─── 股票代码工具 ───

def normalize_symbol(code: str) -> str:
    """6 位标准化股票代码，去除交易所前缀。"""
    code = str(code).strip().upper()
    for prefix in ("SH", "SZ", "BJ", "SH.", "SZ.", "BJ."):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    code = code.replace(".", "").replace("-", "")
    if code.isdigit():
        code = code.zfill(6)
    return code


def get_exchange(code: str) -> str:
    code = normalize_symbol(code)
    if code.startswith("6"):
        return "sh"
    elif code.startswith(("0", "1", "2", "3")):
        return "sz"
    elif code.startswith(("4", "8")):
        return "bj"
    return "sh"


def format_with_exchange(code: str) -> str:
    code = normalize_symbol(code)
    return f"{get_exchange(code)}{code}"


def get_market_name(code: str) -> str:
    code = normalize_symbol(code)
    if code.startswith("688"):
        return "科创板"
    elif code.startswith("6"):
        return "沪主板"
    elif code.startswith(("300", "301")):
        return "创业板"
    elif code.startswith(("0", "1")):
        return "深主板"
    elif code.startswith(("4", "8")):
        return "北交所"
    return "未知"


def detect_market(code: str) -> str:
    """识别股票所属市场: 'us', 'hk', 'a'。"""
    code = str(code).strip()
    # 纯英文字母 + 可选点后缀 → 美股
    cleaned = code.upper().replace(".", "").replace("-", "")
    if cleaned.isalpha():
        return "us"
    # 5 位数字 → 港股
    if code.isdigit() and len(code) == 5:
        return "hk"
    # 6 位数字 → A 股
    if code.isdigit() and len(code) == 6:
        return "a"
    # 混合（如 AAPL.OQ）→ 美股
    if any(c.isalpha() for c in code):
        return "us"
    return "a"


# ─── TTL 缓存 ───

TTL_REALTIME = 30
TTL_DAILY = 300
TTL_COMPANY = 86400


class TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        if key in self._store:
            value, expires_at = self._store[key]
            if time.time() < expires_at:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any, ttl: int = TTL_DAILY) -> None:
        self._store[key] = (value, time.time() + ttl)


cache = TTLCache()


# ─── 多源降级调用 ───

def call_with_fallback(
    *sources: tuple[str, Callable, dict[str, Any]],
) -> pd.DataFrame:
    """依次尝试多个数据源，返回第一个成功的 DataFrame。"""
    last_error: Exception | None = None
    for name, func, kwargs in sources:
        try:
            df = func(**kwargs)
            if df is not None and not df.empty:
                logger.debug(f"[{name}] 成功, {len(df)} 行")
                return df
        except Exception as e:
            last_error = e
            logger.debug(f"[{name}] 失败: {e}")
    if last_error:
        raise last_error
    return pd.DataFrame()
