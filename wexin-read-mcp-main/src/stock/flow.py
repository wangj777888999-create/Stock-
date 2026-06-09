"""FlowMixin — 资金流向 + 个股新闻（均经统一 DataRouter）。"""

from __future__ import annotations

import logging

from stock_utils import (
    TTL_DAILY,
    TTL_REALTIME,
    detect_market,
    get_exchange,
    normalize_symbol,
)

logger = logging.getLogger("stock-service")


class FlowMixin:
    """资金流向 + 个股新闻。"""

    # ─── 6. 资金流向 ───

    async def get_money_flow(self, symbol: str) -> dict:
        """获取近期资金流向(A股),走统一 DataRouter:
        东财 push2his(20天) > AKShare(20天) > 新浪当日(1行兜底),
        失败时回退 7 天内陈旧缓存。
        """
        market = detect_market(symbol)
        if market != "a":
            return {"success": False, "error": "该市场暂不支持资金流向数据"}
        symbol = normalize_symbol(symbol)
        exchange = get_exchange(symbol)
        cache_key = f"flow:{symbol}"

        from services.data_router import get_router
        r = await get_router().fetch(
            "money_flow_individual",
            cache_key=cache_key,
            ttl=TTL_REALTIME,
            timeout=7.0,
            validate=lambda x: bool(x),
            symbol=symbol,
            exchange=exchange,
        )
        if r["success"]:
            return {"success": True, "data": r["data"], "source": r["source"],
                    "stale": r["stale"]}
        return {
            "success": False,
            "error": "资金流数据源(东方财富)在当前网络环境暂不可达,可尝试切换网络/代理后重试",
        }

    # ─── 7. 个股新闻 ───

    async def get_news(self, symbol: str) -> dict:
        """获取个股新闻,走统一 DataRouter(目前主源:东财;后续可加同花顺/新浪)。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"news:{symbol}"
        from services.data_router import get_router
        r = await get_router().fetch(
            "stock_news",
            cache_key=cache_key,
            ttl=TTL_DAILY,
            validate=lambda x: x is not None,  # 空列表也算成功(没新闻)
            symbol=symbol,
        )
        if r["success"]:
            return {"success": True, "data": r["data"] or [], "source": r["source"]}
        return {"success": True, "data": [], "source": None}
