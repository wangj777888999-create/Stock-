"""股票行情路由"""
from fastapi import APIRouter, Query
from pathlib import Path
import json
from stock_service import StockService
from stock_utils import detect_market
from global_stock_service import global_stock_service

router = APIRouter()
stock_service = StockService()

_RULES_PATH = Path(__file__).parent.parent / "financial_rules.json"


# ---------- 股票查询API ----------

@router.get("/api/stock/search")
async def api_stock_search(keyword: str = ""):
    if not keyword or len(keyword.strip()) < 1:
        return {"success": False, "error": "请输入搜索关键词"}
    kw = keyword.strip()
    result = await stock_service.search_stock(kw)
    # 追加韩/日股搜索结果
    global_result = await global_stock_service.search(kw)
    if global_result.get("success") and global_result.get("data"):
        result.setdefault("data", []).extend(global_result["data"])
        result["data"] = result["data"][:25]
    return result


@router.get("/api/stock/quote/{symbol}")
async def api_stock_quote(symbol: str, auto: int = 0):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return await global_stock_service.get_realtime_quote(symbol, market)
    bypass = auto == 1 and market == "a"
    return await stock_service.get_realtime_quote(symbol, bypass_cache=bypass)


@router.get("/api/stock/kline/{symbol}")
async def api_stock_kline(
    symbol: str,
    period: str = "day",
    count: int = Query(default=120, ge=1, le=1000),
    all_history: bool = False,
    indicators: str = "",
    bypass_cache: bool = False,
):
    effective_count = 99999 if all_history else count
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return await global_stock_service.get_kline(symbol, market, period, effective_count)
    return await stock_service.get_kline(symbol, period, effective_count, indicators=indicators, bypass_cache=bypass_cache)


@router.get("/api/stock/profile/{symbol}")
async def api_stock_profile(symbol: str):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return await global_stock_service.get_profile(symbol, market)
    return await stock_service.get_company_profile(symbol)


@router.get("/api/stock/financial/{symbol}")
async def api_stock_financial(symbol: str):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持详细财务数据"}
    return await stock_service.get_financial(symbol)


@router.get("/api/stock/flow/{symbol}")
async def api_stock_flow(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持资金流向"}
    return await stock_service.get_money_flow(symbol)


@router.get("/api/stock/news/{symbol}")
async def api_stock_news(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持新闻"}
    return await stock_service.get_news(symbol)


@router.get("/api/stock/announcements/{symbol}")
async def api_stock_announcements(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持公告"}
    return await stock_service.get_announcements(symbol)


@router.get("/api/stock/shareholders/{symbol}")
async def api_stock_shareholders(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持股东信息"}
    return await stock_service.get_shareholders(symbol)


@router.get("/api/stock/financial-rules")
async def api_financial_rules():
    """返回财务指标高亮规则（读取 financial_rules.json）。"""
    try:
        return json.loads(_RULES_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return {"rules": []}
