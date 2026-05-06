"""问财选股路由 — 条件筛选、板块扫描、机构调研"""
from fastapi import APIRouter
from iwencai_service import IWencaiService

router = APIRouter()
wencai_service = IWencaiService()


# ---------- 问财选股API ----------

@router.post("/api/iwencai/query")
async def api_iwencai_query(req: dict):
    """条件选股 — 自然语言或结构化条件"""
    query = req.get("query", "")
    if not query or len(query.strip()) < 2:
        return {"success": False, "error": "请输入选股条件"}
    loop = req.get("loop", False)
    perpage = req.get("perpage", 50)
    return await wencai_service.query(query.strip(), loop=loop, perpage=perpage)


@router.get("/api/iwencai/sectors")
async def api_iwencai_sectors():
    """板块热力图数据"""
    return await wencai_service.get_sectors()


@router.get("/api/iwencai/sector/{name}")
async def api_iwencai_sector_stocks(name: str):
    """概念成分股"""
    return await wencai_service.get_sector_stocks(name)


@router.get("/api/iwencai/visits/{symbol}")
async def api_iwencai_stock_visits(symbol: str):
    """个股机构调研记录"""
    return await wencai_service.get_stock_visits(symbol)


@router.post("/api/iwencai/visits/search")
async def api_iwencai_visits_search(req: dict):
    """全市场机构调研扫描"""
    query = req.get("query", "")
    perpage = req.get("perpage", 50)
    return await wencai_service.get_visits_search(query, perpage=perpage)
