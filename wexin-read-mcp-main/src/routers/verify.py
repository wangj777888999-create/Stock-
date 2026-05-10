"""博主观点追踪 + 真实账户交易 — 钩子模块。"""
from fastapi import APIRouter, UploadFile
from pydantic import BaseModel
from database import get_db

router = APIRouter(prefix="/api/verify", tags=["verify"])


class BloggerCallAdd(BaseModel):
    blogger_id: str
    symbol: str
    market: str = ""
    call_type: str = "comment"
    call_date: str
    call_price: float | None = None
    target_price: float | None = None
    article_url: str = ""
    notes: str = ""


@router.post("/blogger-call")
async def add_blogger_call(req: BloggerCallAdd):
    db = get_db()
    db.execute(
        """INSERT INTO blogger_calls (blogger_id, symbol, market, call_type, call_date,
           call_price, target_price, article_url, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (req.blogger_id, req.symbol, req.market, req.call_type, req.call_date,
         req.call_price, req.target_price, req.article_url, req.notes),
    )
    db.commit()
    return {"success": True}


@router.get("/blogger-calls")
async def list_blogger_calls(blogger_id: str = "", symbol: str = ""):
    db = get_db()
    where = []
    params = []
    if blogger_id:
        where.append("blogger_id=?")
        params.append(blogger_id)
    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    clause = " AND ".join(where) if where else "1=1"
    rows = db.execute(f"SELECT * FROM blogger_calls WHERE {clause} ORDER BY call_date DESC LIMIT 50", params).fetchall()
    return {"success": True, "data": [dict(zip(
        ["id","blogger_id","symbol","market","call_type","call_date","call_price",
         "target_price","article_url","notes","verified","verified_at","created_at"], r)) for r in rows]}


@router.put("/blogger-call/{call_id}")
async def verify_blogger_call(call_id: int, verified: int = 0):
    db = get_db()
    db.execute("UPDATE blogger_calls SET verified=?, verified_at=datetime('now') WHERE id=?", (verified, call_id))
    db.commit()
    return {"success": True}


class RealTradeAdd(BaseModel):
    symbol: str
    market: str = ""
    direction: str
    price: float
    quantity: float
    fee: float = 0
    trade_date: str
    note: str = ""


@router.post("/real-trade")
async def add_real_trade(req: RealTradeAdd):
    db = get_db()
    db.execute(
        "INSERT INTO real_trades (symbol, market, direction, price, quantity, fee, trade_date, note, source) VALUES (?,?,?,?,?,?,?,?,'manual')",
        (req.symbol, req.market, req.direction, req.price, req.quantity, req.fee, req.trade_date, req.note),
    )
    db.commit()
    return {"success": True}


@router.get("/real-trades")
async def list_real_trades(page: int = 1):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM real_trades ORDER BY trade_date DESC LIMIT 20 OFFSET ?",
        ((page-1)*20,),
    ).fetchall()
    return {"success": True, "data": [dict(zip(
        ["id","symbol","market","direction","price","quantity","fee","trade_date","source","note","created_at"], r)) for r in rows]}


@router.post("/import-csv")
async def import_csv(file: UploadFile):
    import csv, io
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    imported, skipped = 0, 0
    errors = []
    db = get_db()
    for line_num, row in enumerate(reader, start=2):  # 第1行是表头
        try:
            db.execute(
                "INSERT INTO real_trades (symbol, market, direction, price, quantity, fee, trade_date, source) VALUES (?,?,?,?,?,?,?,'csv_import')",
                (row.get("symbol",""), row.get("market","a"), row.get("direction","long"),
                 float(row.get("price",0)), float(row.get("quantity",0)), float(row.get("fee",0)), row.get("trade_date","")),
            )
            imported += 1
        except Exception as e:
            skipped += 1
            errors.append(f"第{line_num}行: {e}")
    db.commit()
    if errors:
        import logging
        logging.getLogger(__name__).warning(f"CSV导入跳过{skipped}行: {'; '.join(errors[:5])}")
    return {"success": True, "imported": imported, "skipped": skipped}
