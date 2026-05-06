"""交易日记 API — CRUD。"""
from fastapi import APIRouter
from pydantic import BaseModel
from database import get_db
from stock_utils import detect_market, normalize_symbol

router = APIRouter(prefix="/api/journal", tags=["journal"])


class JournalEntry(BaseModel):
    symbol: str
    market: str = ""
    direction: str = "long"
    entry_date: str = ""
    exit_date: str = ""
    entry_price: float | None = None
    exit_price: float | None = None
    quantity: float | None = None
    reason: str = ""
    reflection: str = ""
    tags: str = ""


@router.post("/add")
async def add(req: JournalEntry):
    symbol = normalize_symbol(req.symbol)
    market = req.market or detect_market(req.symbol)
    db = get_db()
    db.execute(
        """INSERT INTO trade_journal (symbol, market, direction, entry_date, exit_date,
           entry_price, exit_price, quantity, reason, reflection, tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, market, req.direction, req.entry_date, req.exit_date,
         req.entry_price, req.exit_price, req.quantity, req.reason, req.reflection, req.tags),
    )
    db.commit()
    return {"success": True, "id": db.execute("SELECT last_insert_rowid()").fetchone()[0]}


@router.delete("/{item_id}")
async def remove(item_id: int):
    db = get_db()
    db.execute("DELETE FROM trade_journal WHERE id=?", (item_id,))
    db.commit()
    return {"success": True}


@router.put("/{item_id}")
async def update(item_id: int, req: JournalEntry):
    symbol = normalize_symbol(req.symbol)
    market = req.market or detect_market(req.symbol)
    db = get_db()
    db.execute(
        """UPDATE trade_journal SET symbol=?, market=?, direction=?, entry_date=?, exit_date=?,
           entry_price=?, exit_price=?, quantity=?, reason=?, reflection=?, tags=? WHERE id=?""",
        (symbol, market, req.direction, req.entry_date, req.exit_date,
         req.entry_price, req.exit_price, req.quantity, req.reason, req.reflection, req.tags, item_id),
    )
    db.commit()
    return {"success": True}


@router.get("/list")
async def get_list(symbol: str = "", tag: str = "", page: int = 1):
    db = get_db()
    where = []
    params = []
    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag}%")
    where_clause = " AND ".join(where) if where else "1=1"
    offset = (page - 1) * 20
    rows = db.execute(
        f"SELECT * FROM trade_journal WHERE {where_clause} ORDER BY created_at DESC LIMIT 20 OFFSET ?",
        params + [offset],
    ).fetchall()
    data = [{
        "id": r[0], "symbol": r[1], "market": r[2], "direction": r[3],
        "entry_date": r[4], "exit_date": r[5], "entry_price": r[6],
        "exit_price": r[7], "quantity": r[8], "reason": r[9],
        "reflection": r[10], "tags": r[11], "created_at": r[12],
    } for r in rows]
    return {"success": True, "data": data}


@router.get("/stats")
async def get_stats():
    db = get_db()
    rows = db.execute("SELECT tags FROM trade_journal WHERE tags != ''").fetchall()
    tag_count = {}
    for (tags_str,) in rows:
        for t in tags_str.split(","):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)
    return {"success": True, "data": [{"tag": k, "count": v} for k, v in sorted_tags]}
