"""数据源健康面板 — 暴露各 contract 下每个 provider 的 EWMA 延迟、成功率、最近错误。"""
from fastapi import APIRouter

from services.data_router import get_router

router = APIRouter(prefix="/api/health", tags=["健康"])


@router.get("/sources")
async def api_sources():
    """每个数据契约下各源的健康度。

    前端展示用,也是面试时的可观察性展示。
    返回:
        {"contracts": {
            "concept_rank": [
                {"id": "sina_class", "ewma_ms": 230, "calls": 12, "success": 12, "success_rate": 1.0, ...},
                {"id": "em_push2",   "ewma_ms": 30000, "calls": 12, "success": 0, "last_error": "..."},
            ],
            ...
        }}
    """
    return {"success": True, "contracts": get_router().snapshot()}
