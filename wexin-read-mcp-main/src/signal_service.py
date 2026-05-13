"""信号服务层 — 热门股票、行业涨跌排名。

数据源：
- 热门排名: 东方财富 emappdata API (排名) + 腾讯 qt.gtimg.cn (行情)，均绕过代理
- 行业排名: AKShare stock_board_industry_name_em → 东方财富 push2 (需代理)
"""
from __future__ import annotations

import asyncio
import logging
import re

import requests as _requests
from httpx import AsyncClient

from stock_utils import _clean, cache, TTL_REALTIME
from http_client import get_async_client

logger = logging.getLogger("signal-service")

_QT_URL = "https://qt.gtimg.cn/q="


def _fetch_hot_rank_list(limit: int = 100) -> list[dict]:
    """从东方财富 emappdata API 获取热门股排名列表（绕过代理）。"""
    s = _requests.Session()
    s.trust_env = False
    try:
        r = s.post(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
            json={
                "appId": "appId01",
                "globalId": "786e4c21-70dc-435a-93bb-38",
                "marketType": "",
                "pageNo": 1,
                "pageSize": limit,
            },
            timeout=10,
        )
        items = r.json()["data"]
        result = []
        for item in items:
            sc = item["sc"]  # e.g. "SH601991", "SZ000001"
            code = sc[2:]
            # Tencent format: sh600000, sz000001
            exchange = "sh" if "SH" in sc else "sz"
            result.append({
                "rank": int(item["rk"]),
                "code": code,
                "exchange": exchange,
                "qt_code": f"{exchange}{code}",
            })
        return result
    finally:
        s.close()


async def _batch_tencent_quotes(qt_codes: list[str]) -> dict[str, dict]:
    """批量获取腾讯行情数据。返回 {code: {price, change_pct, volume, amount, turnover_rate}}。"""
    if not qt_codes:
        return {}
    client = get_async_client()
    url = _QT_URL + ",".join(qt_codes)
    r = await client.get(url, timeout=10)
    text = r.content.decode("gbk", errors="replace")

    def _tf(fields, idx):
        try:
            return float(fields[idx]) if fields[idx] else None
        except (IndexError, ValueError):
            return None

    result = {}
    lines = [line.strip() for line in text.strip().split(";") if line.strip()]
    for line in lines:
        start = line.find('"')
        end = line.rfind('"')
        if start == -1 or end <= start:
            continue
        fields = line[start + 1: end].split("~")
        if len(fields) < 40 or not fields[2]:
            continue

        code = fields[2]
        # fields[36] = 成交量(手), fields[37] = 成交额(万元), fields[38] = 换手率(%)
        volume = _tf(fields, 36)          # 手
        amount = _tf(fields, 37)          # 万元 → 转为元
        result[code] = {
            "name": fields[1],
            "price": _tf(fields, 3),
            "change_pct": _tf(fields, 32),
            "volume": int(volume * 100) if volume else None,      # 手 → 股
            "amount": int(amount * 10000) if amount else None,    # 万元 → 元
            "turnover_rate": _tf(fields, 38),
        }

    return result


async def get_hot_stocks(limit: int = 20) -> dict:
    """东方财富热门股票排名 + 腾讯行情数据，TTL=30s。"""
    cache_key = f"signal:hot:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        # Step 1: 获取排名（东方财富，绕过代理）
        rank_list = await asyncio.wait_for(
            asyncio.to_thread(_fetch_hot_rank_list, limit),
            timeout=15,
        )
        if not rank_list:
            return {"success": False, "error": "热门股票排名数据为空"}

        # Step 2: 批量获取行情（腾讯 API，绕过代理）
        qt_codes = [item["qt_code"] for item in rank_list]
        quotes = await asyncio.wait_for(
            _batch_tencent_quotes(qt_codes),
            timeout=15,
        )

        # Step 3: 合并数据
        records = []
        for item in rank_list[:limit]:
            code = item["code"]
            q = quotes.get(code, {})
            records.append({
                "rank": item["rank"],
                "code": code,
                "name": q.get("name") or _clean(item.get("name")),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "volume": q.get("volume"),          # 成交量（股）
                "amount": q.get("amount"),           # 成交额（元）
                "turnover_rate": q.get("turnover_rate"),  # 换手率（%）
            })

        resp = {"success": True, "data": records}
        cache.set(cache_key, resp, TTL_REALTIME)
        return resp
    except asyncio.TimeoutError:
        return {"success": False, "error": "热门股票请求超时"}
    except Exception as e:
        logger.error(f"获取热门股票失败: {e}")
        return {"success": False, "error": f"热门股票获取失败: {e}"}


def _fetch_sina_industry_rank():
    """从新浪财经获取行业板块排名（绕过代理）。"""
    s = _requests.Session()
    s.trust_env = False
    try:
        r = s.get(
            "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=industry",
            timeout=10,
        )
        # 格式: var S_Finance_bankuai_industry = {"code":"code,name,count,...", ...}
        text = r.text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end <= start:
            return []
        raw = text[start:end]
        import json
        data = json.loads(raw)
        result = []
        for key, val in data.items():
            parts = val.split(",")
            if len(parts) < 10:
                continue
            # parts: code, name, count, avg_price, price_change, change_pct,
            #        volume, amount, lead_code, lead_price, lead_change, lead_change_pct, lead_name
            result.append({
                "code": parts[0],
                "name": parts[1],
                "change_pct": parts[5],
                "lead_stock": parts[12] if len(parts) > 12 else "",
                "lead_stock_pct": parts[11] if len(parts) > 11 else "",
            })
        # 按涨跌幅降序
        result.sort(key=lambda x: float(x["change_pct"] or -999), reverse=True)
        return result
    finally:
        s.close()


async def get_industry_ranking(limit: int = 30) -> dict:
    """行业板块涨跌排行（新浪财经），TTL=30s。"""
    cache_key = f"signal:industry_rank:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        items = await asyncio.wait_for(
            asyncio.to_thread(_fetch_sina_industry_rank),
            timeout=15,
        )
        if not items:
            return {"success": False, "error": "行业排名数据为空"}

        records = []
        for i, item in enumerate(items[:limit]):
            records.append({
                "rank": i + 1,
                "name": item["name"],
                "code": item["code"],
                "change_pct": item["change_pct"],
                "lead_stock": item["lead_stock"],
                "lead_stock_pct": item["lead_stock_pct"],
            })

        resp = {"success": True, "data": records}
        cache.set(cache_key, resp, TTL_REALTIME)
        return resp
    except asyncio.TimeoutError:
        return {"success": False, "error": "行业排名请求超时"}
    except Exception as e:
        logger.error(f"获取行业排名失败: {e}")
        return {"success": False, "error": f"行业排名获取失败: {e}"}
