"""行业调研路由 — 流式分析 + 报告 CRUD。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import industry_service

router = APIRouter(prefix="/api/industry", tags=["行业调研"])

_VALID_PURPOSES = {"investment", "startup", "career", "full"}


class AnalyzeRequest(BaseModel):
    industry: str
    purpose: str = "investment"


class SaveReportRequest(BaseModel):
    industry: str
    purpose: str = "investment"
    report_text: str


@router.post("/analyze")
async def api_industry_analyze(req: AnalyzeRequest):
    """流式行业分析（SSE）。"""
    if not req.industry.strip():
        raise HTTPException(status_code=400, detail="行业名称不能为空")
    if req.purpose not in _VALID_PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose 必须是 {_VALID_PURPOSES} 之一")
    return StreamingResponse(
        industry_service.stream_analysis(req.industry.strip(), req.purpose),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/reports")
async def api_industry_save(req: SaveReportRequest):
    """保存分析报告。"""
    if not req.industry.strip() or not req.report_text.strip():
        raise HTTPException(status_code=400, detail="行业名称和报告内容不能为空")
    if req.purpose not in _VALID_PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose 必须是 {_VALID_PURPOSES} 之一")
    report_id = industry_service.save_report(req.industry.strip(), req.purpose, req.report_text)
    return {"success": True, "id": report_id}


@router.get("/reports")
async def api_industry_list_reports():
    """获取历史报告列表（不含正文）。"""
    return {"success": True, "data": industry_service.list_reports()}


@router.get("/reports/{report_id}")
async def api_industry_get_report(report_id: int):
    """获取单条报告全文。"""
    report = industry_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"success": True, "data": report}


@router.delete("/reports/{report_id}")
async def api_industry_delete_report(report_id: int):
    """删除报告。"""
    ok = industry_service.delete_report(report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"success": True}
