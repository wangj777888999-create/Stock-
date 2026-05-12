"""技术分析 API — 画线存储 + 分析笔记 CRUD + K 线形态识别。"""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from database import get_db

router = APIRouter()


# ── Pydantic 模型 ──────────────────────────────────────────────────────────────

class DrawingIn(BaseModel):
    symbol: str
    market: str = "a"
    period: str = "day"
    type: str
    data: dict
    color: str = "#2563EB"
    label: str = ""


class DrawingUpdate(BaseModel):
    data: Optional[dict] = None
    color: Optional[str] = None
    label: Optional[str] = None
    visible: Optional[int] = None


class NoteIn(BaseModel):
    symbol: str
    market: str = "a"
    title: str = ""
    content: str
    tags: str = ""
    note_date: str = ""


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[str] = None
    note_date: Optional[str] = None


# ── 画线接口 ───────────────────────────────────────────────────────────────────

@router.get("/api/analysis/drawings")
async def get_drawings(
    symbol: str,
    market: str = "a",
    period: str = "day",
):
    db = get_db()
    rows = db.execute(
        "SELECT id, type, data, color, label, visible, created_at "
        "FROM chart_drawings "
        "WHERE symbol=? AND market=? AND period=? AND visible=1 "
        "ORDER BY id",
        (symbol.upper(), market, period),
    ).fetchall()
    return {
        "success": True,
        "data": [
            {
                "id": r[0],
                "type": r[1],
                "data": json.loads(r[2]),
                "color": r[3],
                "label": r[4],
                "visible": r[5],
                "created_at": r[6],
            }
            for r in rows
        ],
    }


@router.post("/api/analysis/drawings")
async def create_drawing(req: DrawingIn):
    db = get_db()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO chart_drawings (symbol, market, period, type, data, color, label, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            req.symbol.upper(),
            req.market,
            req.period,
            req.type,
            json.dumps(req.data),
            req.color,
            req.label,
            now,
            now,
        ),
    )
    db.commit()
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"success": True, "id": row_id}


@router.put("/api/analysis/drawings/{drawing_id}")
async def update_drawing(drawing_id: int, req: DrawingUpdate):
    db = get_db()
    sets, params = [], []
    if req.data is not None:
        sets.append("data=?"); params.append(json.dumps(req.data))
    if req.color is not None:
        sets.append("color=?"); params.append(req.color)
    if req.label is not None:
        sets.append("label=?"); params.append(req.label)
    if req.visible is not None:
        sets.append("visible=?"); params.append(req.visible)
    if not sets:
        return {"success": True}
    sets.append("updated_at=?"); params.append(time.strftime("%Y-%m-%d %H:%M:%S"))
    params.append(drawing_id)
    db.execute(f"UPDATE chart_drawings SET {', '.join(sets)} WHERE id=?", params)
    db.commit()
    return {"success": True}


@router.delete("/api/analysis/drawings/clear")
async def clear_drawings(symbol: str, market: str = "a", period: str = "day"):
    db = get_db()
    db.execute(
        "DELETE FROM chart_drawings WHERE symbol=? AND market=? AND period=?",
        (symbol.upper(), market, period),
    )
    db.commit()
    return {"success": True}


@router.delete("/api/analysis/drawings/{drawing_id}")
async def delete_drawing(drawing_id: int):
    db = get_db()
    db.execute("DELETE FROM chart_drawings WHERE id=?", (drawing_id,))
    db.commit()
    return {"success": True}


# ── 笔记接口 ───────────────────────────────────────────────────────────────────

@router.get("/api/analysis/notes/tags")
async def get_note_tags(symbol: str, market: str = "a"):
    db = get_db()
    rows = db.execute(
        "SELECT tags FROM stock_notes WHERE symbol=? AND market=? AND tags != ''",
        (symbol.upper(), market),
    ).fetchall()
    tag_count: dict[str, int] = {}
    for (tags_str,) in rows:
        for t in tags_str.split(","):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    result = [{"tag": k, "count": v} for k, v in sorted(tag_count.items(), key=lambda x: -x[1])]
    return {"success": True, "data": result}


@router.get("/api/analysis/notes")
async def get_notes(
    symbol: str,
    market: str = "a",
    tag: str = "",
    page: int = Query(default=1, ge=1),
):
    db = get_db()
    where, params = ["symbol=?", "market=?"], [symbol.upper(), market]
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag}%")
    offset = (page - 1) * 20
    rows = db.execute(
        f"SELECT id, title, content, tags, note_date, created_at "
        f"FROM stock_notes "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY note_date DESC, created_at DESC "
        f"LIMIT 20 OFFSET ?",
        params + [offset],
    ).fetchall()
    total = db.execute(
        f"SELECT COUNT(*) FROM stock_notes WHERE {' AND '.join(where)}",
        params,
    ).fetchone()[0]
    return {
        "success": True,
        "data": [
            {
                "id": r[0],
                "title": r[1],
                "content": r[2],
                "tags": r[3],
                "note_date": r[4],
                "created_at": r[5],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
    }


@router.post("/api/analysis/notes")
async def create_note(req: NoteIn):
    db = get_db()
    from datetime import date as _date
    note_date = req.note_date or str(_date.today())
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO stock_notes (symbol, market, title, content, tags, note_date, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (req.symbol.upper(), req.market, req.title, req.content, req.tags, note_date, now, now),
    )
    db.commit()
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"success": True, "id": row_id}


@router.put("/api/analysis/notes/{note_id}")
async def update_note(note_id: int, req: NoteUpdate):
    db = get_db()
    sets, params = [], []
    if req.title is not None:
        sets.append("title=?"); params.append(req.title)
    if req.content is not None:
        sets.append("content=?"); params.append(req.content)
    if req.tags is not None:
        sets.append("tags=?"); params.append(req.tags)
    if req.note_date is not None:
        sets.append("note_date=?"); params.append(req.note_date)
    if not sets:
        return {"success": True}
    sets.append("updated_at=?"); params.append(time.strftime("%Y-%m-%d %H:%M:%S"))
    params.append(note_id)
    db.execute(f"UPDATE stock_notes SET {', '.join(sets)} WHERE id=?", params)
    db.commit()
    return {"success": True}


@router.delete("/api/analysis/notes/{note_id}")
async def delete_note(note_id: int):
    db = get_db()
    db.execute("DELETE FROM stock_notes WHERE id=?", (note_id,))
    db.commit()
    return {"success": True}


# ── 形态识别接口 ───────────────────────────────────────────────────────────────

@router.get("/api/analysis/patterns/{symbol}")
async def get_patterns(
    symbol: str,
    period: str = "day",
    lookback: int = Query(default=500, ge=20, le=1000),
):
    from stock_service import StockService
    from services.indicators import calc_macd, detect_candle_patterns, detect_macd_signals

    svc = StockService()
    kline_resp = await svc.get_kline(symbol, period, lookback, indicators="macd")
    if not kline_resp.get("success"):
        return {"success": False, "error": "K线获取失败", "candle_patterns": [], "macd_signals": []}

    data = kline_resp["data"]
    if not data:
        return {"success": True, "candle_patterns": [], "macd_signals": []}

    closes = [float(k["close"]) for k in data]
    opens  = [float(k["open"])  for k in data]
    highs  = [float(k["high"])  for k in data]
    lows   = [float(k["low"])   for k in data]
    dates  = [k["date"]         for k in data]

    candle_signals = detect_candle_patterns(dates, opens, highs, lows, closes)

    macd_raw = (kline_resp.get("indicators") or {}).get("macd")
    if not macd_raw:
        macd_raw = calc_macd(closes)
    macd_signals = detect_macd_signals(dates, closes, macd_raw)

    return {
        "success": True,
        "candle_patterns": candle_signals,
        "macd_signals": macd_signals,
    }
