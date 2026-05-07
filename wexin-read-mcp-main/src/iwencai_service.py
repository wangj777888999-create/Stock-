"""问财智能选股服务 — 条件筛选、板块扫描、机构调研"""

from __future__ import annotations

import asyncio
import logging
import math

import akshare as ak
import pywencai

from stock_utils import TTL_COMPANY, TTL_DAILY, TTL_REALTIME, cache
from http_client import patch_requests as _patch_requests

logger = logging.getLogger("iwencai-service")


def _clean(v):
    """将 NaN/NaT 转为 None。"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


class IWencaiService:
    """同花顺问财数据服务"""

    async def query(self, query: str, loop: bool = False, perpage: int = 50) -> dict:
        """自然语言条件选股"""
        cache_key = f"wencai:query:{query}:{perpage}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                pywencai.get, query=query, loop=loop, perpage=perpage
            )
            if df is None or df.empty:
                return {"success": True, "data": [], "total": 0}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records, "total": len(records)}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"问财查询失败: {e}")
            return {"success": False, "error": f"查询失败: {str(e)}"}

    async def get_sectors(self) -> dict:
        """获取行业板块列表（名称 + 代码）"""
        cache_key = "wencai:sectors"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_board_industry_name_ths
            )
            if df is None or df.empty:
                return {"success": True, "data": []}

            records = [{"name": row["name"], "code": row["code"]} for _, row in df.iterrows()]
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"行业列表获取失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_sector_stocks(self, sector_name: str, perpage: int = 100) -> dict:
        """获取某个概念的成分股列表"""
        cache_key = f"wencai:sector:{sector_name}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                pywencai.get,
                query=sector_name,
                perpage=perpage,
            )
            if df is None or df.empty:
                return {"success": True, "data": [], "total": 0}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records, "total": len(records)}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"概念成分股获取失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_stock_visits(self, symbol: str) -> dict:
        """获取某只股票的机构调研记录（从问财详情提取）"""
        cache_key = f"wencai:visits:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = await asyncio.to_thread(
                pywencai.get,
                query=f"{symbol} 机构调研",
            )
            # 个股查询返回 dict，从"近半年机构调研明细"提取
            if isinstance(result, dict):
                visits = result.get("近半年机构调研明细", [])
                if isinstance(visits, list) and visits:
                    records = [{k: _clean(v) for k, v in item.items()} for item in visits]
                    resp = {"success": True, "data": records}
                    cache.set(cache_key, resp, TTL_DAILY)
                    return resp
                return {"success": True, "data": []}

            # 如果返回 DataFrame（备选路径）
            if result is not None and not result.empty:
                records = self._dedup_visits(result)
                resp = {"success": True, "data": records}
                cache.set(cache_key, resp, TTL_DAILY)
                return resp
            return {"success": True, "data": []}
        except Exception as e:
            logger.error(f"机构调研查询失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_visits_search(self, query: str, perpage: int = 50) -> dict:
        """全市场扫描机构调研（按股票去重）"""
        cache_key = f"wencai:visits_search:{query}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            full_query = f"近一月有机构调研，{query}" if query else "近一月机构调研家数大于5家，按调研日期降序"
            df = await asyncio.to_thread(
                pywencai.get,
                query=full_query,
                perpage=100,
            )
            if df is None or df.empty:
                return {"success": True, "data": [], "total": 0}

            records = self._dedup_visits(df)
            resp = {"success": True, "data": records, "total": len(records)}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"机构调研扫描失败: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _dedup_visits(df) -> list[dict]:
        """按股票代码去重，每只股票保留第一条（最新调研）"""
        seen = set()
        records = []
        visit_cols = [c for c in df.columns if '调研' in c and '次数' not in c]
        meta_cols = ['market_code', 'code', '明细数据']
        keep_cols = [c for c in df.columns if c not in meta_cols]

        for _, row in df.iterrows():
            code = str(row.get('股票代码', '')).strip()
            if not code or code == 'nan':
                continue
            if code in seen:
                continue
            seen.add(code)
            r = {}
            for col in keep_cols:
                r[col] = _clean(row[col])
            records.append(r)

        return records

    async def query_for_article(self, stock_names: list[str], concepts: list[str]) -> dict:
        """根据文章提取的股票名和概念关键词执行组合查询（预留钩子）"""
        results = {}
        if stock_names:
            names_query = " 或 ".join(stock_names)
            results["stocks"] = await self.query(query=names_query, perpage=20)
        if concepts:
            for concept in concepts:
                results[concept] = await self.get_sector_stocks(concept, perpage=20)
        return {"success": True, "data": results}
