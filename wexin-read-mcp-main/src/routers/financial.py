"""财报分析路由 — 个股财报关键指标 + AI 四维度解读。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import financial_service

router = APIRouter(prefix="/api/financial", tags=["财报分析"])

_VALID_MARKETS = {"a", "us"}


class AnalyzeRequest(BaseModel):
    symbol: str
    market: str = "a"
    force: bool = False


@router.post("/analyze")
async def api_financial_analyze(req: AnalyzeRequest):
    """取财报指标 + AI 四维度解读（已有同报告期报告则复用，force=true 强制刷新）。"""
    symbol = req.symbol.strip()
    market = req.market.strip().lower()
    if not symbol:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    if market not in _VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"market 必须是 {_VALID_MARKETS} 之一")

    result = await financial_service.analyze(symbol, market, force=req.force)
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"success": True, "data": result["data"]}


@router.get("/indicators")
async def api_financial_indicators(symbol: str, market: str = "a"):
    """只取结构化指标，不调 AI（用于前端先渲染表格）。"""
    market = market.strip().lower()
    if market not in _VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"market 必须是 {_VALID_MARKETS} 之一")
    result = await financial_service.fetch_indicators(symbol.strip(), market)
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"success": True, "data": result["data"]}


@router.get("/reports")
async def api_financial_reports(symbol: str, market: str = "a"):
    """该股票的历史财报报告列表（不含正文）。"""
    return {"success": True, "data": financial_service.list_reports(symbol.strip(), market.strip().lower())}
