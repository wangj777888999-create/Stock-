"""个股情绪雷达路由。"""
from fastapi import APIRouter, HTTPException

import sentiment_service

router = APIRouter(prefix="/api/sentiment", tags=["情绪雷达"])

_VALID_MARKETS = {"a", "us"}


@router.get("/stock")
async def api_stock_sentiment(symbol: str, market: str = "a", with_ai: bool = False):
    """个股情绪雷达:5 维度评分 + 综合指数(可选 AI 判读)。"""
    symbol = symbol.strip()
    market = market.strip().lower()
    if not symbol:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    if market not in _VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"market 必须是 {_VALID_MARKETS} 之一")
    result = await sentiment_service.analyze_stock_sentiment(symbol, market, with_ai=with_ai)
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"success": True, "data": result["data"]}
