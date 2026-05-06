"""自选股管理 API — CRUD + 行情批量查询。"""
from fastapi import APIRouter
from pydantic import BaseModel
from database import get_db
from stock_service import StockService
from stock_utils import detect_market, normalize_symbol
import asyncio

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])
stock_service = StockService()


class WatchlistAdd(BaseModel):
    symbol: str
    market: str = ""
    note: str = ""
    tags: str = ""
    target_price: float | None = None
    alert_price: float | None = None


class WatchlistUpdate(BaseModel):
    note: str | None = None
    tags: str | None = None
    target_price: float | None = None
    alert_price: float | None = None
    sort_order: int | None = None


class ReorderItem(BaseModel):
    id: int
    sort_order: int


@router.post("/add")
async def add(req: WatchlistAdd):
    symbol = normalize_symbol(req.symbol)
    market = req.market or detect_market(req.symbol)
    db = get_db()
    try:
        db.execute(
            """INSERT INTO watchlist (symbol, market, name, note, tags, target_price, alert_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, market, "", req.note, req.tags, req.target_price, req.alert_price),
        )
        db.commit()
        return {"success": True, "id": db.execute("SELECT last_insert_rowid()").fetchone()[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/{item_id}")
async def remove(item_id: int):
    db = get_db()
    db.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
    db.commit()
    return {"success": True}


@router.put("/{item_id}")
async def update(item_id: int, req: WatchlistUpdate):
    db = get_db()
    updates = {}
    if req.note is not None:
        updates["note"] = req.note
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.target_price is not None:
        updates["target_price"] = req.target_price
    if req.alert_price is not None:
        updates["alert_price"] = req.alert_price
    if req.sort_order is not None:
        updates["sort_order"] = req.sort_order
    if not updates:
        return {"success": False, "error": "无更新字段"}
    sets = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [item_id]
    db.execute(f"UPDATE watchlist SET {sets} WHERE id=?", values)
    db.commit()
    return {"success": True}


@router.get("/list")
async def get_list():
    db = get_db()
    rows = db.execute(
        "SELECT id, symbol, market, name, note, tags, target_price, alert_price, sort_order, added_at "
        "FROM watchlist ORDER BY sort_order ASC, id ASC"
    ).fetchall()

    items = []
    symbols_to_fetch = []
    for r in rows:
        item = {
            "id": r[0], "symbol": r[1], "market": r[2], "name": r[3],
            "note": r[4], "tags": r[5], "target_price": r[6], "alert_price": r[7],
            "sort_order": r[8], "added_at": r[9], "quote": None,
        }
        items.append(item)
        symbols_to_fetch.append((r[1], r[2]))

    # 批量获取行情
    async def fetch_one(sym, mkt):
        try:
            if mkt in ("kr", "jp"):
                from global_stock_service import global_stock_service
                r = await global_stock_service.get_realtime_quote(sym, mkt)
            else:
                r = await stock_service.get_realtime_quote(sym)
            if r.get("success"):
                return r["data"]
        except Exception:
            pass
        return None

    quotes = await asyncio.gather(*[fetch_one(sym, mkt) for sym, mkt in symbols_to_fetch])
    for item, quote in zip(items, quotes):
        if quote:
            item["quote"] = {
                "price": quote.get("最新价") or quote.get("price"),
                "change_pct": quote.get("涨跌幅") or quote.get("change_pct"),
                "volume": quote.get("成交量") or quote.get("volume"),
                "high": quote.get("最高") or quote.get("high"),
                "low": quote.get("最低") or quote.get("low"),
                "open": quote.get("今开") or quote.get("open"),
                "prev_close": quote.get("昨收") or quote.get("prev_close"),
            }
            name = quote.get("名称") or quote.get("name")
            if name and not item["name"]:
                item["name"] = name
                db.execute("UPDATE watchlist SET name=? WHERE id=?", (name, item["id"]))

    db.commit()
    return {"success": True, "data": items}


@router.post("/reorder")
async def reorder(items: list[ReorderItem]):
    db = get_db()
    for it in items:
        db.execute("UPDATE watchlist SET sort_order=? WHERE id=?", (it.sort_order, it.id))
    db.commit()
    return {"success": True}
