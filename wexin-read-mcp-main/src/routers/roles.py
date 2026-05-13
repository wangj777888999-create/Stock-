"""多角色交易验证 API — 角色 CRUD + 角色内交易操作 + CSV 导入。"""
from fastapi import APIRouter, UploadFile
from pydantic import BaseModel
from database import get_db
from stock_service import StockService
from stock_utils import detect_market, normalize_symbol

router = APIRouter(prefix="/api/roles", tags=["roles"])
stock_service = StockService()


# ── Pydantic models ──

class RoleCreate(BaseModel):
    name: str
    initial_capital: float = 100000.0
    avatar_color: str = "#2563EB"
    notes: str = ""


class RoleUpdate(BaseModel):
    name: str | None = None
    initial_capital: float | None = None
    avatar_color: str | None = None
    notes: str | None = None
    is_active: int | None = None


class RoleTradeOpen(BaseModel):
    symbol: str
    market: str = ""
    direction: str = "long"
    price: float
    quantity: float
    fee: float = 0
    trade_date: str
    note: str = ""


class RoleTradeClose(BaseModel):
    id: int
    close_price: float
    close_date: str
    fee: float = 0
    note: str = ""


# ── Helpers ──

def _label(win_rate: float, total_trades: int) -> str:
    if total_trades >= 10:
        if win_rate >= 0.55:
            return "正向指标"
        if win_rate <= 0.35:
            return "反向指标"
    return "随机漫步"


# ── 角色 CRUD ──

@router.post("/create")
async def create_role(req: RoleCreate):
    db = get_db()
    db.execute(
        "INSERT INTO roles (name, avatar_color, initial_capital, notes) VALUES (?, ?, ?, ?)",
        (req.name, req.avatar_color, req.initial_capital, req.notes),
    )
    db.commit()
    rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"success": True, "id": rid}


@router.get("/list")
async def list_roles():
    db = get_db()
    rows = db.execute("""
        SELECT r.*,
            COUNT(CASE WHEN t.status='closed' THEN 1 END) as total_trades,
            COUNT(CASE WHEN t.status='closed' AND t.pnl > 0 THEN 1 END) as win_count,
            COUNT(CASE WHEN t.status='closed' AND t.pnl <= 0 THEN 1 END) as lose_count,
            COALESCE(SUM(CASE WHEN t.status='closed' THEN t.pnl END), 0) as total_pnl,
            COALESCE(SUM(CASE WHEN t.status='closed' AND t.pnl > 0 THEN t.pnl ELSE 0 END), 0) as total_win_pnl,
            COALESCE(SUM(CASE WHEN t.status='closed' AND t.pnl <= 0 THEN t.pnl ELSE 0 END), 0) as total_lose_pnl,
            COUNT(CASE WHEN t.status='open' THEN 1 END) as open_positions
        FROM roles r
        LEFT JOIN sim_trades t ON r.id = t.role_id
        WHERE r.is_active = 1
        GROUP BY r.id
        ORDER BY r.created_at DESC
    """).fetchall()

    data = []
    for r in rows:
        total = r["total_trades"]
        wins = r["win_count"]
        wr = wins / total if total > 0 else 0
        pnl = r["total_pnl"]
        cap = r["initial_capital"]
        avg_win = round(r["total_win_pnl"] / wins, 2) if wins > 0 else 0
        avg_lose = round(r["total_lose_pnl"] / r["lose_count"], 2) if r["lose_count"] > 0 else 0
        data.append({
            "id": r["id"],
            "name": r["name"],
            "avatar_color": r["avatar_color"],
            "initial_capital": cap,
            "notes": r["notes"],
            "total_trades": total,
            "win_count": wins,
            "lose_count": r["lose_count"],
            "win_rate": round(wr, 3),
            "total_pnl": round(pnl, 2),
            "total_pnl_pct": round(pnl / cap * 100, 2) if cap > 0 else 0,
            "open_positions": r["open_positions"],
            "current_equity": round(cap + pnl, 2),
            "avg_win": avg_win,
            "avg_lose": avg_lose,
            "label": _label(wr, total),
        })
    return {"success": True, "data": data}


@router.get("/{role_id}")
async def get_role(role_id: int):
    db = get_db()
    r = db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not r:
        return {"success": False, "error": "角色不存在"}
    return {"success": True, "data": dict(r)}


@router.put("/{role_id}")
async def update_role(role_id: int, req: RoleUpdate):
    db = get_db()
    existing = db.execute("SELECT id FROM roles WHERE id=?", (role_id,)).fetchone()
    if not existing:
        return {"success": False, "error": "角色不存在"}
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [role_id]
        db.execute(
            f"UPDATE roles SET {set_clause}, updated_at=datetime('now') WHERE id=?",
            values,
        )
        db.commit()
    return {"success": True}


@router.delete("/{role_id}")
async def delete_role(role_id: int):
    db = get_db()
    db.execute("UPDATE roles SET is_active=0, updated_at=datetime('now') WHERE id=?", (role_id,))
    db.commit()
    return {"success": True}


# ── 角色内交易操作 ──

@router.post("/{role_id}/open")
async def role_open(role_id: int, req: RoleTradeOpen):
    db = get_db()
    role = db.execute("SELECT id FROM roles WHERE id=? AND is_active=1", (role_id,)).fetchone()
    if not role:
        return {"success": False, "error": "角色不存在"}

    symbol = normalize_symbol(req.symbol)
    market = req.market or detect_market(req.symbol)
    db.execute(
        """INSERT INTO sim_trades
           (role_id, symbol, market, direction, price, quantity, fee, trade_date, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (role_id, symbol, market, req.direction, req.price, req.quantity, req.fee, req.trade_date, req.note),
    )
    db.commit()
    rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"success": True, "id": rid}


@router.post("/{role_id}/close")
async def role_close(role_id: int, req: RoleTradeClose):
    db = get_db()
    row = db.execute(
        "SELECT price, quantity, fee, direction FROM sim_trades WHERE id=? AND role_id=? AND status='open'",
        (req.id, role_id),
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
    db.commit()
    if cur.rowcount == 0:
        return {"success": False, "error": "平仓失败，持仓状态已变更"}
    return {"success": True, "pnl": round(pnl, 2)}


@router.get("/{role_id}/positions")
async def role_positions(role_id: int):
    db = get_db()
    rows = db.execute(
        "SELECT id, symbol, market, direction, price, quantity, fee, trade_date, note, created_at "
        "FROM sim_trades WHERE status='open' AND role_id=? ORDER BY created_at DESC",
        (role_id,),
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


@router.get("/{role_id}/history")
async def role_history(role_id: int, page: int = 1):
    db = get_db()
    offset = (page - 1) * 20
    rows = db.execute(
        "SELECT * FROM sim_trades WHERE status='closed' AND role_id=? ORDER BY closed_at DESC LIMIT 20 OFFSET ?",
        (role_id, offset),
    ).fetchall()
    data = [{
        "id": r[0], "symbol": r[1], "market": r[2], "direction": r[3],
        "price": r[4], "quantity": r[5], "fee": r[6], "trade_date": r[7],
        "status": r[8], "closed_at": r[9], "close_price": r[10], "pnl": r[11],
        "note": r[12], "created_at": r[13],
    } for r in rows]
    return {"success": True, "data": data}


@router.get("/{role_id}/stats")
async def role_stats(role_id: int):
    db = get_db()
    rows = db.execute(
        "SELECT pnl FROM sim_trades WHERE status='closed' AND pnl IS NOT NULL AND role_id=?",
        (role_id,),
    ).fetchall()
    if not rows:
        return {"success": True, "data": {
            "total_trades": 0, "win_count": 0, "lose_count": 0,
            "win_rate": 0, "total_pnl": 0, "avg_win": 0, "avg_lose": 0,
        }}

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


@router.get("/{role_id}/account")
async def role_account(role_id: int):
    db = get_db()
    role = db.execute("SELECT initial_capital FROM roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        return {"success": False, "error": "角色不存在"}
    cap = role[0]
    closed_pnl = db.execute(
        "SELECT COALESCE(SUM(pnl),0) FROM sim_trades WHERE status='closed' AND role_id=?", (role_id,)
    ).fetchone()[0] or 0
    open_rows = db.execute(
        "SELECT symbol, direction, price, quantity, fee FROM sim_trades WHERE status='open' AND role_id=?",
        (role_id,),
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
        "total_return_pct": round(closed_pnl / cap * 100, 2) if cap > 0 else 0,
        "positions_count": len(open_rows),
    }}


# ── CSV 导入 ──

@router.post("/{role_id}/import-csv")
async def import_csv_for_role(role_id: int, file: UploadFile):
    import csv, io

    db = get_db()
    role = db.execute("SELECT id FROM roles WHERE id=? AND is_active=1", (role_id,)).fetchone()
    if not role:
        return {"success": False, "error": "角色不存在"}

    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    imported, skipped = 0, 0
    errors = []

    for line_num, row in enumerate(reader, start=2):
        try:
            sym = normalize_symbol(row.get("symbol", ""))
            mkt = row.get("market", "") or detect_market(sym)
            direction = row.get("direction", "long")
            price = float(row.get("price", 0))
            qty = float(row.get("quantity", 0))
            fee = float(row.get("fee", 0))
            trade_date = row.get("trade_date", "")
            close_price_str = (row.get("close_price") or "").strip()
            close_date = (row.get("close_date") or "").strip()
            note = row.get("note", "")

            status = "open"
            closed_at = None
            close_price = None
            pnl = None

            if close_price_str:
                close_price = float(close_price_str)
                status = "closed"
                closed_at = close_date
                # CSV fee is treated as total round-trip fee (open + close)
                pnl = (close_price - price) * qty - fee
                if direction == "short":
                    pnl = (price - close_price) * qty - fee
                pnl = round(pnl, 2)

            db.execute(
                """INSERT INTO sim_trades
                   (role_id, symbol, market, direction, price, quantity, fee,
                    trade_date, status, closed_at, close_price, pnl, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (role_id, sym, mkt, direction, price, qty, fee, trade_date,
                 status, closed_at, close_price, pnl, note),
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
