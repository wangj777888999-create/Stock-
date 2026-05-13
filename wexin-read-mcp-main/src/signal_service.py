"""信号服务层 — 热门股票、行业涨跌排名。

数据源：
- 热门排名: AKShare stock_hot_rank_em (东方财富人气榜)
- 行业排名: AKShare stock_board_industry_name_em (东方财富板块列表)
"""
from __future__ import annotations

import asyncio
import logging
import time

from stock_utils import cache, _clean, TTL_REALTIME

logger = logging.getLogger("signal-service")


def _proxy_session():
    """创建使用系统代理的 requests.Session，用于 eastmoney API。"""
    import requests
    s = requests.Session()
    s.trust_env = True
    return s


def _call_with_session(func, *args, **kwargs):
    """用独立 session 包装 AKShare 调用，避免 patch_requests 的代理绕过问题。"""
    import requests as _r
    s = _proxy_session()
    old_get, old_post, old_Session = _r.get, _r.post, _r.Session
    _r.get, _r.post = s.get, s.post
    try:
        return func(*args, **kwargs)
    finally:
        _r.get, _r.post, _r.Session = old_get, old_post, old_Session
        s.close()


def _call_with_retry(func, max_retries=2, delay=1.0, *args, **kwargs):
    """带重试的调用，处理 eastmoney 偶发连接失败。"""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return _call_with_session(func, *args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                logger.debug(f"{func.__name__} 第{attempt+1}次失败，{delay}s后重试: {e}")
                time.sleep(delay)
    raise last_err


def _call_hot_stocks():
    import akshare as ak
    return _call_with_retry(ak.stock_hot_rank_em)


def _call_industry_ranking():
    import akshare as ak
    return _call_with_retry(ak.stock_board_industry_name_em, max_retries=3, delay=2.0)


async def get_hot_stocks(limit: int = 20) -> dict:
    """东方财富热门股票排名，TTL=30s。"""
    cache_key = f"signal:hot:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(_call_hot_stocks),
            timeout=20,
        )
        if df is None or df.empty:
            return {"success": False, "error": "热门股票数据为空"}

        records = []
        for _, row in df.head(limit).iterrows():
            code_raw = str(_clean(row.get("代码")) or "")
            code = code_raw.replace("SH", "").replace("SZ", "").replace("BJ", "")
            records.append({
                "rank": _clean(row.get("当前排名")),
                "code": code,
                "name": _clean(row.get("股票名称")),
                "price": _clean(row.get("最新价")),
                "change_pct": _clean(row.get("涨跌幅")),
            })

        resp = {"success": True, "data": records}
        cache.set(cache_key, resp, TTL_REALTIME)
        return resp
    except asyncio.TimeoutError:
        return {"success": False, "error": "热门股票请求超时"}
    except Exception as e:
        logger.error(f"获取热门股票失败: {e}")
        return {"success": False, "error": f"热门股票获取失败: {e}"}


async def get_industry_ranking(limit: int = 30) -> dict:
    """行业板块涨跌排行（东方财富板块列表），TTL=30s。"""
    cache_key = f"signal:industry_rank:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(_call_industry_ranking),
            timeout=30,
        )
        if df is None or df.empty:
            return {"success": False, "error": "行业排名数据为空"}

        records = []
        for _, row in df.head(limit).iterrows():
            records.append({
                "rank": _clean(row.get("排名")),
                "name": _clean(row.get("板块名称")),
                "code": _clean(row.get("板块代码")),
                "change_pct": _clean(row.get("涨跌幅")),
                "turnover_rate": _clean(row.get("换手率")),
                "up_count": _clean(row.get("上涨家数")),
                "down_count": _clean(row.get("下跌家数")),
                "lead_stock": _clean(row.get("领涨股票")),
                "lead_stock_pct": _clean(row.get("领涨股票-涨跌幅")),
            })

        resp = {"success": True, "data": records}
        cache.set(cache_key, resp, TTL_REALTIME)
        return resp
    except asyncio.TimeoutError:
        return {"success": False, "error": "行业排名请求超时"}
    except Exception as e:
        logger.error(f"获取行业排名失败: {e}")
        return {"success": False, "error": f"行业排名获取失败: {e}"}
