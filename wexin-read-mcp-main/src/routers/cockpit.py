"""驾驶舱路由 — 市场情绪、指数报价、分时数据。"""
from fastapi import APIRouter
import cockpit_service

router = APIRouter(prefix="/api/cockpit", tags=["驾驶舱"])


@router.get("/sentiment")
async def api_cockpit_sentiment():
    """获取市场情绪聚合数据。"""
    return await cockpit_service.get_sentiment()


@router.get("/indices")
async def api_cockpit_indices():
    """获取主要指数实时报价。"""
    return await cockpit_service.get_indices_quotes()


@router.get("/tick/{code}")
async def api_cockpit_tick(code: str):
    """获取指定指数的分时数据。"""
    return await cockpit_service.get_tick_data(code)
