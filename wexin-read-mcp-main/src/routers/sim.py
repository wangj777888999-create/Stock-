"""模拟交易 API — 开仓/平仓/持仓/统计。"""
from fastapi import APIRouter
from pydantic import BaseModel
from database import get_db
from stock_service import StockService
from stock_utils import detect_market, normalize_symbol

router = APIRouter(prefix="/api/sim", tags=["sim"])
stock_service = StockService()

INITIAL_CAPITAL = 100000.0


def _get_default_role_id():
    db = get_db()
    row = db.execute("SELECT id FROM roles ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None


class SimOpen(BaseModel):
    symbol: str
    market: str = ""
    direction: str = "long"
    price: float
    quantity: float
    fee: float = 0
    trade_date: str
    note: str = ""


class SimClose(BaseModel):
    id: int
    close_price: float
    close_date: str
    fee: float = 0
    note: str = ""


@router.post("/open")
async def open_trade(req: SimOpen, role_id: int | None = None):
    symbol = normalize_symbol(req.symbol)
    market = req.market or detect_market(req.symbol)
    rid = role_id or _get_default_role_id()
    db = get_db()
    db.execute(
        """INSERT INTO sim_trades (role_id, symbol, market, direction, price, quantity, fee, trade_date, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rid, symbol, market, req.direction, req.price, req.quantity, req.fee, req.trade_date, req.note),
    )
    db.commit()
    return {"success": True, "id": db.execute("SELECT last_insert_rowid()").fetchone()[0]}


@router.post("/close")
async def close_trade(req: SimClose, role_id: int | None = None):
    rid = role_id or _get_default_role_id()
    db = get_db()
    row = db.execute(
        "SELECT price, quantity, fee, direction FROM sim_trades WHERE id=? AND status='open' AND role_id=?",
        (req.id, rid),
    ).fetchone()
    if not row:
        return {"success": False, "error": "未找到该持仓或已平仓"}
    price, quantity, open_fee, direction = row
    pnl = (req.close_price - price) * quantity - open_fee - req.fee
    if direction == "short":
        pnl = (price - req.close_price) * quantity - open_fee - req.fee
    cur = db.execute(
        "UPDATE sim_trades SET status='closed', closed_at=?, close_price=?, pnl=?, note=? WHERE id=? AND status='open'",
        (req.close_date, req.close_price, round(pnl, 2), req.note, req.id),
    )
    if cur.rowcount == 0:
        return {"success": False, "error": "平仓失败，持仓状态已变更"}
    db.commit()
    return {"success": True, "pnl": round(pnl, 2)}


@router.get("/positions")
async def get_positions(role_id: int | None = None):
    rid = role_id or _get_default_role_id()
    db = get_db()
    rows = db.execute(
        "SELECT id, symbol, market, direction, price, quantity, fee, trade_date, note, created_at "
        "FROM sim_trades WHERE status='open' AND role_id=? ORDER BY created_at DESC",
        (rid,),
    ).fetchall()

    positions = []
    for r in rows:
        pos = {
            "id": r[0], "symbol": r[1], "market": r[2], "direction": r[3],
            "price": r[4], "quantity": r[5], "fee": r[6], "trade_date": r[7],
            "note": r[8], "created_at": r[9], "current_price": None, "unrealized_pnl": None,
        }
        try:
            qr = await stock_service.get_realtime_quote(r[1])
            if qr.get("success"):
                current = qr["data"].get("最新价") or qr["data"].get("price")
                if current:
                    pos["current_price"] = current
                    if r[3] == "short":
                        pos["unrealized_pnl"] = round((r[4] - current) * r[5] - r[6], 2)
                    else:
                        pos["unrealized_pnl"] = round((current - r[4]) * r[5] - r[6], 2)
        except Exception:
            pass
        positions.append(pos)

    return {"success": True, "data": positions}


@router.get("/history")
async def get_history(page: int = 1, role_id: int | None = None):
    rid = role_id or _get_default_role_id()
    db = get_db()
    offset = (page - 1) * 20
    rows = db.execute(
        "SELECT * FROM sim_trades WHERE status='closed' AND role_id=? ORDER BY closed_at DESC LIMIT 20 OFFSET ?",
        (rid, offset),
    ).fetchall()
    data = [{
        "id": r[0], "symbol": r[1], "market": r[2], "direction": r[3],
        "price": r[4], "quantity": r[5], "fee": r[6], "trade_date": r[7],
        "status": r[8], "closed_at": r[9], "close_price": r[10], "pnl": r[11],
        "note": r[12], "created_at": r[13],
    } for r in rows]
    return {"success": True, "data": data}


@router.get("/stats")
async def get_stats(role_id: int | None = None):
    rid = role_id or _get_default_role_id()
    db = get_db()
    rows = db.execute(
        "SELECT pnl FROM sim_trades WHERE status='closed' AND pnl IS NOT NULL AND role_id=?",
        (rid,),
    ).fetchall()
    if not rows:
        return {"success": True, "data": {"total_trades": 0, "win_count": 0, "lose_count": 0, "win_rate": 0, "total_pnl": 0}}

    pnls = [r[0] for r in rows]
    wins = [p for p in pnls if p > 0]
    loses = [p for p in pnls if p <= 0]
    return {"success": True, "data": {
        "total_trades": len(pnls),
        "win_count": len(wins),
        "lose_count": len(loses),
        "win_rate": round(len(wins) / len(pnls), 3),
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_lose": round(sum(loses) / len(loses), 2) if loses else 0,
    }}


@router.get("/account")
async def get_account(role_id: int | None = None):
    rid = role_id or _get_default_role_id()
    db = get_db()
    role = db.execute("SELECT initial_capital FROM roles WHERE id=?", (rid,)).fetchone()
    cap = role[0] if role else INITIAL_CAPITAL
    closed_pnl = db.execute(
        "SELECT COALESCE(SUM(pnl),0) FROM sim_trades WHERE status='closed' AND role_id=?", (rid,)
    ).fetchone()[0] or 0
    open_rows = db.execute(
        "SELECT symbol, direction, price, quantity, fee FROM sim_trades WHERE status='open' AND role_id=?",
        (rid,),
    ).fetchall()

    market_value = 0.0
    for r in open_rows:
        try:
            qr = await stock_service.get_realtime_quote(r[0])
            if qr.get("success"):
                current = qr["data"].get("最新价") or qr["data"].get("price") or r[2]
                market_value += current * r[3]
        except Exception:
            market_value += r[2] * r[3]

    return {"success": True, "data": {
        "initial_capital": cap,
        "closed_pnl": round(closed_pnl, 2),
        "market_value": round(market_value, 2),
        "total_equity": round(cap + closed_pnl, 2),
        "total_return_pct": round(closed_pnl / cap * 100, 2) if cap else 0,
        "positions_count": len(open_rows),
    }}
