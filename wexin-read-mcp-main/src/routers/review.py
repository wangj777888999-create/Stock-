"""复盘模块 — 股票列表 + 备注 + 画线 CRUD。"""
import json
import logging
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from database import get_db

router = APIRouter(prefix="/api/review", tags=["review"])
logger = logging.getLogger(__name__)

_STOCK_LIST_PATH = Path(__file__).parent.parent / "stock_list.json"


@router.get("/stocks")
async def list_stocks():
    """返回全部 A 股列表（code, name），供前端洗牌使用。"""
    try:
        data = json.loads(_STOCK_LIST_PATH.read_text(encoding="utf-8"))
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"读取股票列表失败: {e}")
        return {"success": False, "error": str(e), "data": []}


# ─── 备注 ───

class NoteCreate(BaseModel):
    symbol: str
    name: str = ""
    note: str


@router.get("/notes")
async def list_notes(symbol: str = ""):
    db = get_db()
    if symbol:
        rows = db.execute(
            "SELECT id, symbol, name, note, created_at FROM review_notes"
            " WHERE symbol=? ORDER BY created_at DESC",
            (symbol.upper(),),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, symbol, name, note, created_at FROM review_notes"
            " ORDER BY created_at DESC"
        ).fetchall()
    return {"success": True, "data": [
        {"id": r[0], "symbol": r[1], "name": r[2], "note": r[3], "created_at": r[4]}
        for r in rows
    ]}


@router.post("/notes")
async def add_note(req: NoteCreate):
    note = req.note.strip()
    if not note:
        return {"success": False, "error": "备注不能为空"}
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO review_notes (symbol, name, note) VALUES (?,?,?)",
            (req.symbol.upper(), req.name.strip(), note),
        )
        db.commit()
        return {"success": True, "id": cur.lastrowid}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int):
    db = get_db()
    db.execute("DELETE FROM review_notes WHERE id=?", (note_id,))
    db.commit()
    return {"success": True}


# ─── 画线 ───

class DrawingCreate(BaseModel):
    symbol: str
    period: str
    type: str   # 'hline' | 'trendline'
    data: str   # JSON 字符串
    color: str = "#ef4444"


@router.get("/drawings")
async def list_drawings(symbol: str, period: str = ""):
    db = get_db()
    if period:
        rows = db.execute(
            "SELECT id, symbol, period, type, data, color, created_at FROM review_drawings"
            " WHERE symbol=? AND period=? ORDER BY created_at",
            (symbol.upper(), period),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, symbol, period, type, data, color, created_at FROM review_drawings"
            " WHERE symbol=? ORDER BY created_at",
            (symbol.upper(),),
        ).fetchall()
    return {"success": True, "data": [
        {"id": r[0], "symbol": r[1], "period": r[2], "type": r[3],
         "data": r[4], "color": r[5], "created_at": r[6]}
        for r in rows
    ]}


@router.post("/drawings")
async def add_drawing(req: DrawingCreate):
    symbol = req.symbol.strip().upper()
    if not symbol or not req.type or not req.data:
        return {"success": False, "error": "参数不完整"}
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO review_drawings (symbol, period, type, data, color) VALUES (?,?,?,?,?)",
            (symbol, req.period.strip(), req.type.strip(), req.data, req.color),
        )
        db.commit()
        return {"success": True, "id": cur.lastrowid}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/drawings")
async def clear_drawings(symbol: str, period: str):
    """删除指定股票+周期的全部画线。"""
    db = get_db()
    db.execute(
        "DELETE FROM review_drawings WHERE symbol=? AND period=?",
        (symbol.upper(), period),
    )
    db.commit()
    return {"success": True}


@router.delete("/drawings/{drawing_id}")
async def delete_drawing(drawing_id: int):
    db = get_db()
    db.execute("DELETE FROM review_drawings WHERE id=?", (drawing_id,))
    db.commit()
    return {"success": True}
