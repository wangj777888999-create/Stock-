"""信号服务层 — 热门股票、行业涨跌排名。

数据源：
- 热门排名: 东方财富 emappdata API (排名) + 腾讯 qt.gtimg.cn (行情)，均绕过代理
- 行业排名: AKShare stock_board_industry_name_em → 东方财富 push2 (需代理)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time as _time

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
        items = r.json().get("data") or []
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
    """A股热门股榜单,经统一 DataRouter。"""
    cache_key = f"signal:hot:{limit}"
    from services.data_router import get_router
    r = await get_router().fetch(
        "hot_stocks",
        cache_key=cache_key,
        ttl=TTL_REALTIME,
        validate=lambda x: bool(x),
        limit=limit,
    )
    if not r["success"]:
        return {"success": False, "error": r["error"]}
    return {"success": True, "data": r["data"], "source": r["source"], "stale": r["stale"]}


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


def _fetch_sina_concept_rank() -> list[dict]:
    """从新浪获取 A 股市场概念板块排名(绕过代理,东财不可达时的降级源)。

    数据源:money.finance.sina.com.cn 的 newFLJK.php?param=class
    返回的是用户日常关注的"市场概念"(华为汽车、BC电池、AI 手机等),
    而非 ?param=concept(那是国民经济分类,不是市场概念)。
    字段同 industry: [code, name, count, avg_price, price_change,
    change_pct, volume, amount, lead_code, lead_price, lead_change,
    lead_change_pct, lead_name]。
    """
    s = _requests.Session()
    s.trust_env = False
    try:
        r = s.get(
            "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class",
            timeout=10,
        )
        # 返回 GBK 编码,需要先 decode
        text = r.content.decode("gbk", errors="replace") if r.encoding else r.text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end <= start:
            return []
        import json
        data = json.loads(text[start:end])
        result = []
        for key, val in data.items():
            parts = val.split(",")
            if len(parts) < 10:
                continue
            try:
                cp = float(parts[5])
            except (ValueError, IndexError):
                cp = None
            result.append({
                "code": parts[0],
                "name": parts[1],
                "change_pct": cp,
                "lead_stock": parts[12] if len(parts) > 12 else "",
                "lead_stock_pct": parts[11] if len(parts) > 11 else "",
            })
        result.sort(key=lambda x: x["change_pct"] if x["change_pct"] is not None else -999, reverse=True)
        return result
    finally:
        s.close()


def _fetch_em_concept_rank(limit: int = 30) -> list[dict]:
    """从东方财富 push2 获取概念板块排行（按涨跌幅降序，绕过代理）。"""
    s = _requests.Session()
    s.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": max(limit, 1), "po": 1, "np": 1,
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:90 t:3 f:!50",
        "fields": "f3,f12,f14,f128,f136",
    }
    # 东财 push2 偶发反爬式断连，重试若干次
    last_exc = None
    try:
        for attempt in range(5):
            try:
                r = s.get(url, params=params, timeout=10, headers=headers)
                diff = r.json().get("data", {}).get("diff") or []
                return [
                    {
                        "name": it.get("f14"),
                        "code": it.get("f12"),
                        "change_pct": it.get("f3"),
                        "lead_stock": it.get("f128"),
                        "lead_stock_pct": it.get("f136"),
                    }
                    for it in diff
                ]
            except Exception as e:
                last_exc = e
                _time.sleep(0.6 * (attempt + 1))
        if last_exc:
            raise last_exc
        return []
    finally:
        s.close()


async def get_concept_ranking(limit: int = 30) -> dict:
    """概念板块涨跌排行（东方财富 push2，绕过代理），TTL=30s。"""
    cache_key = f"signal:concept_rank:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # 走统一 DataRouter:多源 race + EWMA 排序 + stale 兜底 + 统计
    from services.data_router import get_router
    r = await get_router().fetch(
        "concept_rank",
        cache_key=cache_key,
        ttl=TTL_REALTIME,
        validate=lambda x: bool(x),
        limit=limit,
    )
    if not r["success"]:
        return {"success": False, "error": r["error"]}
    items = r["data"] or []
    records = [{"rank": i + 1, **item} for i, item in enumerate(items[:limit])]
    return {"success": True, "data": records, "source": r["source"], "stale": r["stale"]}


async def get_industry_ranking(limit: int = 30) -> dict:
    """行业板块涨跌排行,经统一 DataRouter:新浪(主)+ 同花顺汇总(备)。"""
    cache_key = f"signal:industry_rank:{limit}"
    from services.data_router import get_router
    r = await get_router().fetch(
        "industry_rank",
        cache_key=cache_key,
        ttl=TTL_REALTIME,
        validate=lambda x: bool(x),
        limit=limit,
    )
    if not r["success"]:
        return {"success": False, "error": r["error"]}
    items = r["data"] or []
    records = [{
        "rank": i + 1,
        "name": item.get("name"),
        "code": item.get("code"),
        "change_pct": item.get("change_pct"),
        "lead_stock": item.get("lead_stock"),
        "lead_stock_pct": item.get("lead_stock_pct"),
    } for i, item in enumerate(items[:limit])]
    return {"success": True, "data": records, "source": r["source"], "stale": r["stale"]}
