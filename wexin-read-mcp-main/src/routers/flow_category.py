"""自定义分类资金流向 — 分类 CRUD + 股票管理 + 流向聚合。"""
import asyncio
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from database import get_db
from stock_service import StockService

router = APIRouter(prefix="/api/flow-category", tags=["flow-category"])
logger = logging.getLogger(__name__)
_svc = StockService()


def _parse_yi(s) -> float:
    """将 '1.50亿' / '-0.30万' / 原始数字字符串 解析为亿单位的 float，失败返回 0.0。"""
    if s is None:
        return 0.0
    s = str(s).strip()
    try:
        if s.endswith("亿"):
            return float(s[:-1])
        if s.endswith("万"):
            return float(s[:-1]) / 10000
        return float(s) / 1e8
    except ValueError:
        return 0.0


# ─── Models ───

class CategoryCreate(BaseModel):
    name: str

class CategoryRename(BaseModel):
    name: str

class StockAdd(BaseModel):
    symbol: str
    name: str = ""


# ─── 分类 CRUD ───

@router.get("/list")
async def list_categories():
    """列出所有分类及其股票（单次 JOIN 查询，避免 N+1）。"""
    db = get_db()
    cats = db.execute(
        "SELECT id, name, sort_order, created_at FROM flow_categories ORDER BY sort_order, id"
    ).fetchall()
    if not cats:
        return {"success": True, "data": []}

    cat_ids = tuple(c[0] for c in cats)
    placeholders = ",".join("?" * len(cat_ids))
    stock_rows = db.execute(
        f"SELECT category_id, symbol, name FROM flow_category_stocks"
        f" WHERE category_id IN ({placeholders}) ORDER BY category_id, added_at",
        cat_ids,
    ).fetchall()

    # 按 category_id 分组
    stocks_by_cat: dict[int, list] = {c[0]: [] for c in cats}
    for row in stock_rows:
        stocks_by_cat[row[0]].append({"symbol": row[1], "name": row[2]})

    result = [
        {
            "id": c[0],
            "name": c[1],
            "sort_order": c[2],
            "created_at": c[3],
            "stocks": stocks_by_cat[c[0]],
        }
        for c in cats
    ]
    return {"success": True, "data": result}


@router.post("/create")
async def create_category(req: CategoryCreate):
    name = req.name.strip()
    if not name:
        return {"success": False, "error": "分类名称不能为空"}
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO flow_categories (name) VALUES (?)", (name,)
        )
        db.commit()
        return {"success": True, "id": cur.lastrowid}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.put("/{cat_id}")
async def rename_category(cat_id: int, req: CategoryRename):
    name = req.name.strip()
    if not name:
        return {"success": False, "error": "分类名称不能为空"}
    db = get_db()
    cur = db.execute("UPDATE flow_categories SET name=? WHERE id=?", (name, cat_id))
    if cur.rowcount == 0:
        return {"success": False, "error": "分类不存在"}
    db.commit()
    return {"success": True}


@router.delete("/{cat_id}")
async def delete_category(cat_id: int):
    db = get_db()
    db.execute("DELETE FROM flow_categories WHERE id=?", (cat_id,))
    db.commit()
    return {"success": True}


# ─── 股票管理 ───

@router.post("/{cat_id}/stocks")
async def add_stock(cat_id: int, req: StockAdd):
    symbol = req.symbol.strip().upper()
    if not symbol:
        return {"success": False, "error": "股票代码不能为空"}
    db = get_db()
    try:
        db.execute(
            "INSERT INTO flow_category_stocks (category_id, symbol, name) VALUES (?,?,?)",
            (cat_id, symbol, req.name.strip()),
        )
        db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/{cat_id}/stocks/{symbol}")
async def remove_stock(cat_id: int, symbol: str):
    db = get_db()
    db.execute(
        "DELETE FROM flow_category_stocks WHERE category_id=? AND symbol=?",
        (cat_id, symbol.upper()),
    )
    db.commit()
    return {"success": True}


# ─── 资金流向聚合 ───

@router.get("/{cat_id}/flow")
async def get_category_flow(cat_id: int, period: int = 1):
    """
    period: 1=今日, 3=近3日累计, 5=近5日累计
    返回分类内各股票的聚合资金流向 + 合计。
    """
    if period not in (1, 3, 5):
        period = 1

    db = get_db()
    cat = db.execute(
        "SELECT id, name FROM flow_categories WHERE id=?", (cat_id,)
    ).fetchone()
    if not cat:
        return {"success": False, "error": "分类不存在"}

    stocks = db.execute(
        "SELECT symbol, name FROM flow_category_stocks WHERE category_id=? ORDER BY added_at",
        (cat_id,),
    ).fetchall()
    if not stocks:
        return {"success": True, "data": {
            "category_id": cat_id,
            "category_name": cat[1],
            "period": period,
            "stocks": [],
            "total": {"super_large_net": 0, "large_net": 0, "medium_net": 0,
                      "small_net": 0, "main_net": 0},
        }}

    async def fetch_flow(symbol, stock_name):
        try:
            resp = await _svc.get_money_flow(symbol)
            if not resp.get("success"):
                return {"symbol": symbol, "name": stock_name, "error": resp.get("error"),
                        "super_large_net": 0, "large_net": 0, "medium_net": 0,
                        "small_net": 0, "main_net": 0, "trend": []}
            records = resp["data"][:period]  # 取前 N 条（最新在前）
            sl = sum(_parse_yi(r.get("超大单净流入-净额")) for r in records)
            la = sum(_parse_yi(r.get("大单净流入-净额")) for r in records)
            me = sum(_parse_yi(r.get("中单净流入-净额")) for r in records)
            sm = sum(_parse_yi(r.get("小单净流入-净额")) for r in records)
            mn = sum(_parse_yi(r.get("主力净流入-净额")) for r in records)
            # 近5日主力净流入趋势（不受 period 影响，始终取最新5条）
            trend_records = resp["data"][:5]
            trend = [1 if _parse_yi(r.get("主力净流入-净额")) >= 0 else -1 for r in trend_records]
            return {
                "symbol": symbol,
                "name": stock_name,
                "super_large_net": round(sl, 4),
                "large_net": round(la, 4),
                "medium_net": round(me, 4),
                "small_net": round(sm, 4),
                "main_net": round(mn, 4),
                "trend": trend,
            }
        except Exception as e:
            logger.error(f"获取 {symbol} 资金流向失败: {e}")
            return {"symbol": symbol, "name": stock_name, "error": str(e),
                    "super_large_net": 0, "large_net": 0, "medium_net": 0,
                    "small_net": 0, "main_net": 0, "trend": []}

    results = await asyncio.gather(*[fetch_flow(s[0], s[1]) for s in stocks])

    total = {
        "super_large_net": round(sum(r["super_large_net"] for r in results), 4),
        "large_net": round(sum(r["large_net"] for r in results), 4),
        "medium_net": round(sum(r["medium_net"] for r in results), 4),
        "small_net": round(sum(r["small_net"] for r in results), 4),
        "main_net": round(sum(r["main_net"] for r in results), 4),
    }

    return {"success": True, "data": {
        "category_id": cat_id,
        "category_name": cat[1],
        "period": period,
        "stocks": list(results),
        "total": total,
    }}
