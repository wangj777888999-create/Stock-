"""多市场板块路由 — 港股/美股/基金/期货"""
from fastapi import APIRouter, Query
from market import get_provider
import akshare as ak
from stock_utils import cache_get, cache_set

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
async def api_futures_kline(symbol: str, count: int = Query(default=120, ge=1, le=1000), all_history: bool = False):
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


# ---------- 北向资金 + 龙虎榜 ----------

@router.get("/api/market/north-flow")
async def get_north_flow():
    cached = cache_get("north-flow")
    if cached is not None:
        return {"success": True, "data": cached}
    try:
        df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
        data = df.tail(50).to_dict(orient="records")
        cache_set("north-flow", data, 300)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/market/north-history")
async def get_north_history():
    cached = cache_get("north-history")
    if cached is not None:
        return {"success": True, "data": cached}
    try:
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        data = df.tail(30).to_dict(orient="records")
        cache_set("north-history", data, 300)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/market/dragon-tiger")
async def get_dragon_tiger(date: str = ""):
    cache_key = f"dragon-tiger-{date}" if date else "dragon-tiger"
    cached = cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached, "date": date}
    try:
        kwargs = {"date": date} if date else {}
        df = ak.stock_lhb_detail_daily_sina(**kwargs)
        data = df.to_dict(orient="records") if df is not None and not df.empty else []
        cache_set(cache_key, data, 600)
        return {"success": True, "data": data, "date": date}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
