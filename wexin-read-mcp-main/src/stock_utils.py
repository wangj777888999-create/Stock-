"""
股票数据工具函数 — 来自 cn-financial-mcp 项目，本地化副本。

包含：股票代码规范化、持久化缓存（SQLite）、多源降级调用。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
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
    """识别股票所属市场: 'us', 'hk', 'a', 'kr', 'jp'。"""
    raw = str(code).strip()
    upper = raw.upper()

    # 显式后缀优先
    if upper.endswith(".KS") or upper.endswith(".KQ"):
        return "kr"
    if upper.endswith(".T"):
        return "jp"

    raw_cleaned = upper.replace(".", "").replace("-", "")

    # 4 位纯数字 → 日股（A 股 normalize 后始终 6 位）
    if raw_cleaned.isdigit() and len(raw_cleaned) == 4:
        return "jp"

    # 5 位纯数字 → 港股
    if raw_cleaned.isdigit() and len(raw_cleaned) == 5:
        return "hk"

    code = normalize_symbol(code)
    # 纯英文字母 → 美股
    if code.isalpha():
        return "us"
    # 6 位数字 → A 股
    if code.isdigit() and len(code) == 6:
        return "a"
    # 混合（如 AAPL.OQ）→ 美股
    if any(c.isalpha() for c in code):
        return "us"
    return "a"


# ─── NaN 清洗 ───

import math


def _clean(v):
    """Convert NaN/NaT to None, Timestamp to str, numpy types to Python native."""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return str(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    return v


# ─── TTL 缓存 ───

TTL_REALTIME = 30          # 实时行情（30s）
TTL_REALTIME_REFRESH = 5   # 强制刷新（5s）
TTL_DAILY = 300            # 日内聚合行情（5min，盘中仍在变化）
TTL_KLINE = 3600           # K 线历史（1h，日内不变）
TTL_BOARDS = 30            # 板块列表/成分股（30s，盘中行情快速变化）
TTL_COMPANY = 86400        # 公司基本面（24h）


# ─── 持久化缓存（SQLite）───

from database import get_db


def cache_get(key: str):
    """从 cache 表读取缓存值。过期返回 None 并删除旧行。"""
    db = get_db()
    row = db.execute(
        "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None
    raw, expires_at = row
    if time.time() >= expires_at:
        db.execute("DELETE FROM cache WHERE key = ?", (key,))
        db.commit()
        return None
    return _deserialize(raw)


def cache_get_stale(key: str, max_stale_seconds: int = 7 * 86400):
    """陈旧兜底:即使过期也返回旧值,只要在 max_stale_seconds 内。

    返回 (value, is_fresh):
        - is_fresh=True : 命中新鲜缓存,等同 cache_get
        - is_fresh=False: 缓存已过期但仍在陈旧期内,作为降级兜底
        - 返回 (None, False) 表示无缓存或陈旧太久

    用法:数据源临时挂掉时,先返回上次成功结果给用户,不让前端报错。
    """
    db = get_db()
    row = db.execute(
        "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None, False
    raw, expires_at = row
    now = time.time()
    if now < expires_at:
        return _deserialize(raw), True
    if now - expires_at > max_stale_seconds:
        return None, False
    return _deserialize(raw), False


def cache_set(key: str, value: Any, ttl: int = TTL_DAILY) -> None:
    """写入 cache 表，自动处理 DataFrame 序列化。"""
    db = get_db()
    raw = _serialize(value)
    for attempt in range(3):
        try:
            db.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, raw, time.time() + ttl),
            )
            db.commit()
            return
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e).lower() or attempt == 2:
                raise
            time.sleep(0.1)


def _serialize(value: Any) -> str:
    if isinstance(value, pd.DataFrame):
        return json.dumps(
            {"__type": "DataFrame", "data": value.to_dict(orient="records")},
            ensure_ascii=False,
            default=str,
        )
    return json.dumps(value, ensure_ascii=False, default=str)


def _deserialize(raw: str):
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("__type") == "DataFrame":
        return pd.DataFrame(data["data"])
    return data


# L1 进程内缓存：key → (value, expires_at: float)
_mem: dict[str, tuple[Any, float]] = {}
_mem_lock = threading.Lock()


class _CacheCompat:
    """两层缓存：L1 进程内 dict（<0.1ms）→ L2 SQLite（5~20ms）→ 未命中。"""

    def get(self, key: str):
        now = time.time()
        # L1 命中
        with _mem_lock:
            entry = _mem.get(key)
            if entry is not None:
                val, exp = entry
                if now < exp:
                    return val
                del _mem[key]
        # L2：直接查 SQLite，同时获取 expires_at 用于回填 L1
        db = get_db()
        row = db.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        raw, expires_at = row
        if now >= expires_at:
            db.execute("DELETE FROM cache WHERE key = ?", (key,))
            db.commit()
            return None
        val = _deserialize(raw)
        # 回填 L1，使用 SQLite 中真实的 expires_at
        with _mem_lock:
            _mem[key] = (val, expires_at)
        return val

    def set(self, key: str, value: Any, ttl: int = TTL_DAILY) -> None:
        exp = time.time() + ttl
        with _mem_lock:
            _mem[key] = (value, exp)
        cache_set(key, value, ttl)

    def clear(self) -> None:
        with _mem_lock:
            _mem.clear()
        db = get_db()
        db.execute("DELETE FROM cache")
        db.commit()

    # 兼容旧 benchmark 脚本的 cache._store.clear() 调用
    _store = _mem


cache = _CacheCompat()


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
