"""A 股板块路由 — 行业板块 + 概念板块"""
from fastapi import APIRouter, Query
from state import get_sector_service

router = APIRouter(prefix="/api/sector", tags=["板块"])


@router.get("/boards")
async def get_boards(
    board_type: str = Query(default="all", regex="^(industry|concept|all)$"),
    sort_by: str = Query(default="change_pct", regex="^(change_pct|turnover|up_count)$"),
    ascending: bool = False,
    limit: int = Query(default=100, ge=1, le=999),
):
    """板块列表（行业/概念），支持排序和数量限制"""
    svc = get_sector_service()
    return await svc.get_boards(board_type=board_type, sort_by=sort_by, ascending=ascending, limit=limit)


@router.get("/board/{board_type}/{board_name}")
async def get_board_stocks(board_type: str, board_name: str):
    """板块成分股"""
    svc = get_sector_service()
    return await svc.get_board_stocks(board_name=board_name, board_type=board_type)


@router.get("/board_kline/{board_type}/{board_name}")
async def get_board_kline(
    board_type: str,
    board_name: str,
    period: str = Query(default="daily", regex="^(daily|weekly|monthly)$"),
    count: int = Query(default=120, ge=1, le=1000),
):
    """板块 K 线（仅东方财富，无兜底）"""
    svc = get_sector_service()
    return await svc.get_board_kline(board_name=board_name, board_type=board_type, period=period, count=count)


@router.get("/search")
async def search_boards(
    keyword: str = "",
    board_type: str = Query(default="all", regex="^(industry|concept|all)$"),
):
    """搜索板块"""
    svc = get_sector_service()
    return await svc.search(keyword=keyword, board_type=board_type)
