"""复盘模块 — 股票列表 + 备注 CRUD。"""
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
