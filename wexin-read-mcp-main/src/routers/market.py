"""多市场板块路由 — 港股/美股/基金/期货"""
from fastapi import APIRouter
from market import get_provider

router = APIRouter()


# ---------- 多市场板块路由 ----------

@router.get("/api/market/{market}/boards")
async def api_market_boards(market: str):
    """板块列表"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_boards()


@router.get("/api/market/{market}/board/{name}")
async def api_market_board_stocks(market: str, name: str):
    """板块成分股"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_board_stocks(name)


@router.get("/api/market/{market}/spot")
async def api_market_spot(market: str):
    """实时行情"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_spot()


@router.get("/api/market/{market}/search")
async def api_market_search(market: str, q: str = ""):
    """搜索"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.search(q)


@router.get("/api/fund/detail/{code}")
async def api_fund_detail(code: str):
    """ETF 详情：基本信息 + K 线 + 持仓"""
    provider = get_provider("fund")
    if not provider:
        return {"success": False, "error": "基金服务不可用"}
    return await provider.get_etf_detail(code)


@router.get("/api/futures/kline/{symbol}")
async def api_futures_kline(symbol: str, count: int = 120, all_history: bool = False):
    """期货 K 线"""
    provider = get_provider("futures")
    if not provider:
        return {"success": False, "error": "期货服务不可用"}
    effective_count = 99999 if all_history else count
    return await provider.get_kline(symbol, count=effective_count)


@router.get("/api/futures/rank/{symbol}")
async def api_futures_rank(symbol: str):
    """期货持仓龙虎榜"""
    provider = get_provider("futures")
    if not provider:
        return {"success": False, "error": "期货服务不可用"}
    return await provider.get_rank(symbol)
