"""信号路由 — 热门股票、行业涨跌排名。"""
from fastapi import APIRouter, Query
from signal_service import get_hot_stocks, get_industry_ranking

router = APIRouter(prefix="/api/signal", tags=["信号"])


@router.get("/hot")
async def api_hot_stocks(limit: int = Query(default=20, ge=1, le=50)):
    """东方财富热门股票排名。"""
    return await get_hot_stocks(limit=limit)


@router.get("/industry_rank")
async def api_industry_rank(limit: int = Query(default=30, ge=1, le=100)):
    """行业板块涨跌排行（东方财富）。"""
    return await get_industry_ranking(limit=limit)
