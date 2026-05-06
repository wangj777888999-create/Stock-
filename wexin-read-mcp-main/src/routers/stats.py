"""盈亏统计汇总 — 模拟+真实合并。"""
from fastapi import APIRouter
from database import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _sim_stats(db):
    rows = db.execute("SELECT pnl FROM sim_trades WHERE status='closed' AND pnl IS NOT NULL").fetchall()
    pnls = [r[0] for r in rows]
    if not pnls:
        return {"total_trades": 0, "win_count": 0, "lose_count": 0, "win_rate": 0, "total_pnl": 0}
    wins = [p for p in pnls if p > 0]
    return {
        "total_trades": len(pnls),
        "win_count": len(wins),
        "lose_count": len(pnls) - len(wins),
        "win_rate": round(len(wins) / len(pnls), 3),
        "total_pnl": round(sum(pnls), 2),
    }


@router.get("/dashboard")
async def dashboard():
    db = get_db()
    sim = _sim_stats(db)
    real_rows = db.execute("SELECT COUNT(*) FROM real_trades").fetchone()
    real = {"total_trades": real_rows[0], "total_pnl": 0}
    combined = {
        "total_trades": sim["total_trades"] + real["total_trades"],
        "win_rate": sim["win_rate"],
        "total_pnl": round(sim["total_pnl"] + real["total_pnl"], 2),
    }
    return {"success": True, "data": {"sim": sim, "real": real, "combined": combined}}


@router.get("/monthly")
async def monthly():
    db = get_db()
    rows = db.execute(
        "SELECT substr(closed_at,1,7) as month, SUM(pnl), COUNT(*) FROM sim_trades WHERE status='closed' AND pnl IS NOT NULL GROUP BY month ORDER BY month DESC LIMIT 12"
    ).fetchall()
    return {"success": True, "data": [{"month": r[0], "pnl": round(r[1], 2), "trades": r[2]} for r in rows]}


@router.get("/by-tag")
async def by_tag():
    db = get_db()
    rows = db.execute("SELECT tags FROM trade_journal WHERE tags != ''").fetchall()
    tag_count = {}
    for (tags_str,) in rows:
        for t in tags_str.split(","):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    return {"success": True, "data": [{"tag": k, "count": v} for k, v in sorted(tag_count.items(), key=lambda x: x[1], reverse=True)]}
