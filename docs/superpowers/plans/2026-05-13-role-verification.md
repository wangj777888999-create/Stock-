# 多角色交易验证系统 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建多角色独立账户模拟交易系统，支持手动录入+交割单 CSV 导入，自动标记反向/正向指标。

**Architecture:** 新增 `roles` 表 + `sim_trades.role_id` 外键实现多账户隔离；新建 `routers/roles.py` 提供角色 CRUD 及角色内交易 API；前端新增 `view-roles` 角色卡片墙 + 角色详情（复用现有 sim 交易界面模式）。旧 sim 端点加可选 `role_id` 参数保持兼容。

**Tech Stack:** FastAPI + SQLite + vanilla JS (单文件 SPA)，复用现有 `stock_utils`、`StockService`、设计令牌 CSS 变量。

---

### Task 1: 数据库迁移 — roles 表 + sim_trades.role_id

**Files:**
- Modify: `wexin-read-mcp-main/src/database.py:238-254`（`_migrate()` 函数）
- Modify: `wexin-read-mcp-main/src/database.py:42-224`（`init_db()` 内 executescript）

- [ ] **Step 1: 在 init_db() 的 executescript 中添加 roles 表 DDL**

在 `chart_drawings` 表定义之后（第 196 行 `"""` 结束前），插入：

```sql
CREATE TABLE IF NOT EXISTS roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    avatar_color    TEXT DEFAULT '#2563EB',
    initial_capital REAL NOT NULL DEFAULT 100000.0,
    notes           TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
```

- [ ] **Step 2: 在 _migrate() 中添加 role_id 列 + 数据迁移逻辑**

替换 `_migrate()` 函数（`database.py:238-254`）为：

```python
def _migrate():
    """增量迁移：添加新列。忽略 'duplicate column name' 错误。"""
    migrations = [
        "ALTER TABLE watchlist ADD COLUMN tags TEXT DEFAULT ''",
        "ALTER TABLE watchlist ADD COLUMN alert_price REAL",
        "ALTER TABLE watchlist ADD COLUMN target_price REAL",
        "ALTER TABLE blogger_calls ADD COLUMN ai_reason TEXT",
        "ALTER TABLE blogger_calls ADD COLUMN status TEXT DEFAULT 'pending'",
        "ALTER TABLE blogger_calls ADD COLUMN user_confirmed INTEGER DEFAULT 0",
        # 多角色系统
        "ALTER TABLE sim_trades ADD COLUMN role_id INTEGER REFERENCES roles(id)",
    ]
    for sql in migrations:
        try:
            _db.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.error(f"数据库迁移失败: {sql} — {e}")
                raise

    # 角色数据迁移：创建默认角色并挂入旧交易
    existing = _db.execute("SELECT id FROM roles LIMIT 1").fetchone()
    if not existing:
        _db.execute(
            "INSERT INTO roles (name, initial_capital, notes) VALUES (?, ?, ?)",
            ("默认账户", 100000.0, "从旧版单账户模拟交易迁移"),
        )
        default_id = _db.execute("SELECT last_insert_rowid()").fetchone()[0]
        _db.execute(
            "UPDATE sim_trades SET role_id = ? WHERE role_id IS NULL",
            (default_id,),
        )
        logger.info(f"角色迁移完成：创建默认账户 id={default_id}")
```

- [ ] **Step 3: 重启验证迁移**

```bash
cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src
python -c "
from database import init_db; init_db()
from database import get_db
db = get_db()
# Check roles table
roles = db.execute('SELECT * FROM roles').fetchall()
print('Roles:', roles)
# Check role_id column
cols = [row[1] for row in db.execute('PRAGMA table_info(sim_trades)').fetchall()]
print('role_id exists:', 'role_id' in cols)
# Check migration assigned role_id
null_count = db.execute('SELECT COUNT(*) FROM sim_trades WHERE role_id IS NULL').fetchone()[0]
print('NULL role_id rows:', null_count)
"
```

Expected: 1 role created, `role_id` column exists, 0 NULL role_id rows.

- [ ] **Step 4: Commit**

```bash
git add wexin-read-mcp-main/src/database.py
git commit -m "feat(db): 新增 roles 表 + sim_trades.role_id 迁移"
```

---

### Task 2: 新建角色路由 `routers/roles.py`

**Files:**
- Create: `wexin-read-mcp-main/src/routers/roles.py`

- [ ] **Step 1: 创建路由文件骨架**

```python
"""多角色交易验证 API — 角色 CRUD + 角色内交易操作 + CSV 导入。"""
from fastapi import APIRouter, UploadFile, Query
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
```

- [ ] **Step 2: 角色 CRUD 端点**

```python
def _label(win_rate: float, total_trades: int) -> str:
    if total_trades >= 10:
        if win_rate >= 0.55:
            return "正向指标"
        if win_rate <= 0.35:
            return "反向指标"
    return "随机漫步"


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
        updates["updated_at"] = None  # will use default
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
```

- [ ] **Step 3: 角色内交易操作端点**

```python
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
    db.execute(
        "UPDATE sim_trades SET status='closed', closed_at=?, close_price=?, pnl=?, note=? WHERE id=?",
        (req.close_date, req.close_price, round(pnl, 2), req.note, req.id),
    )
    db.commit()
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
```

- [ ] **Step 4: CSV 导入端点**

```python
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
```

- [ ] **Step 5: Commit**

```bash
git add wexin-read-mcp-main/src/routers/roles.py
git commit -m "feat: 新增角色路由 — CRUD + 角色内交易 + CSV 导入"
```

---

### Task 3: 注册新路由 + 兼容旧 sim

**Files:**
- Modify: `wexin-read-mcp-main/src/app.py`（路由注册区域）
- Modify: `wexin-read-mcp-main/src/routers/sim.py:65-162`（加 role_id 过滤）

- [ ] **Step 1: 注册 roles 路由**

在 `app.py` 中，找到其他 router 注册位置（约第 74-89 行，`include_router` 块），添加：

```python
from routers.roles import router as roles_router
app.include_router(roles_router)
```

- [ ] **Step 2: 更新 sim 路由加 role_id 过滤**

在 `routers/sim.py` 顶部添加辅助函数：

```python
def _get_default_role_id():
    db = get_db()
    row = db.execute("SELECT id FROM roles ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None
```

每个端点通过 `rid = role_id or _get_default_role_id()` 获取角色 ID，并加入 SQL 过滤。以 `get_positions` 为例（`sim.py:65-71`）：

```python
@router.get("/positions")
async def get_positions(role_id: int | None = None):
    db = get_db()
    rid = role_id or _get_default_role_id()
    rows = db.execute(
        "SELECT id, symbol, market, direction, price, quantity, fee, trade_date, note, created_at "
        "FROM sim_trades WHERE status='open' AND role_id=? ORDER BY created_at DESC",
        (rid,),
    ).fetchall()
    # ... rest unchanged
```

同样修改 `get_history`、`get_stats`、`get_account`、`open_trade`、`close_trade`。其中 `open_trade` 需要写入 `role_id`：

```python
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
```

`close_trade` 加 role_id 过滤：

```python
row = db.execute(
    "SELECT price, quantity, fee, direction FROM sim_trades WHERE id=? AND status='open' AND role_id=?",
    (req.id, rid),
).fetchone()
```

`get_account` 使用 `roles.initial_capital`：

```python
@router.get("/account")
async def get_account(role_id: int | None = None):
    db = get_db()
    rid = role_id or _get_default_role_id()
    role = db.execute("SELECT initial_capital FROM roles WHERE id=?", (rid,)).fetchone()
    cap = role[0] if role else INITIAL_CAPITAL
    # ... rest with cap instead of INITIAL_CAPITAL
```

- [ ] **Step 3: 验证旧 sim 端点仍可用**

重启服务后：

```bash
curl -s http://localhost:8000/api/sim/positions | python3 -m json.tool | head -10
curl -s "http://localhost:8000/api/sim/positions?role_id=1" | python3 -m json.tool | head -10
```

两者应返回相同数据（默认角色）。

- [ ] **Step 4: Commit**

```bash
git add wexin-read-mcp-main/src/app.py wexin-read-mcp-main/src/routers/sim.py
git commit -m "feat: 注册角色路由 + sim 端点增加 role_id 过滤"
```

---

### Task 4: 前端 — 角色卡片墙视图

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 添加侧边栏导航按钮**

在 sim 按钮之后（约第 1202 行），插入：

```html
<button class="nav-item" data-view="roles" onclick="switchView('roles', this)">
  <svg viewBox="0 0 20 20" fill="none" width="16" height="16">
    <rect x="2" y="3" width="7" height="9" rx="1.5" stroke="currentColor" stroke-width="1.3"/>
    <rect x="11" y="3" width="7" height="9" rx="1.5" stroke="currentColor" stroke-width="1.3"/>
    <circle cx="5.5" cy="7.5" r="1.5" fill="currentColor"/>
    <circle cx="14.5" cy="7.5" r="1.5" fill="currentColor"/>
  </svg>
  <span>角色验证</span>
</button>
```

- [ ] **Step 2: 添加 topbarMeta 条目**

在 `topbarMeta` 对象中（`sim` 条目之后，约第 2536 行），插入：

```javascript
roles: { title: '角色验证', sub: '多角色模拟交易 · 交割单导入 · 反向指标验证' },
```

- [ ] **Step 3: 添加 view-roles HTML 容器**

在 `view-sim` 结束标签之后（`</div>` 约第 2131 行），插入完整的 `view-roles` 容器。包含三个子面板：

**面板 A — 角色卡片墙（`id="roles-cards-panel"`）：**

```html
<div id="view-roles" class="view">
  <div id="roles-cards-panel">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
      <h2 style="margin:0;font-size:18px;font-weight:600">角色列表</h2>
      <button class="btn btn-primary btn-sm" onclick="rolesShowCreate()">+ 新增角色</button>
    </div>
    <div id="roles-cards-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px"></div>
  </div>

  <!-- 面板 B: 角色详情 (初始隐藏) -->
  <div id="roles-detail-panel" style="display:none">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <button class="btn btn-ghost btn-sm" onclick="rolesBackToCards()">← 返回</button>
      <span id="roles-detail-name" style="font-size:18px;font-weight:600"></span>
      <span id="roles-detail-label" style="font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600"></span>
    </div>
    <div id="roles-detail-account" style="margin-bottom:12px;color:#888;font-size:13px"></div>
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap" id="roles-detail-stats"></div>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button class="btn btn-primary btn-sm" onclick="rolesShowOpenForm()">+ 开仓</button>
      <button class="btn btn-outline btn-sm" onclick="rolesShowImportModal()">📄 CSV 导入</button>
      <button class="btn btn-ghost btn-sm" onclick="rolesShowEdit()">✏️ 编辑</button>
      <button class="btn btn-ghost btn-sm" onclick="rolesDelete()" style="color:var(--red)">删除</button>
    </div>
    <div class="card" style="margin-bottom:16px">
      <div class="card-header"><div class="card-title">当前持仓</div></div>
      <div class="card-body" style="overflow-x:auto;padding:0">
        <table class="data-table"><thead><tr>
          <th>代码</th><th>方向</th><th>买入价</th><th>现价</th><th>数量</th><th>浮动盈亏</th><th>日期</th><th>备注</th><th>操作</th>
        </tr></thead><tbody id="roles-positions-tbody"></tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><div class="card-title">已平仓记录</div></div>
      <div class="card-body" style="overflow-x:auto;padding:0">
        <table class="data-table"><thead><tr>
          <th>代码</th><th>方向</th><th>买入价</th><th>卖出价</th><th>数量</th><th>盈亏</th><th>日期</th><th>备注</th>
        </tr></thead><tbody id="roles-history-tbody"></tbody></table>
      </div>
    </div>
  </div>

  <!-- 角色表单 Modal -->
  <div id="roles-form-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1001;justify-content:center;align-items:center">
    <div style="background:#1a1a2e;padding:24px;border-radius:8px;min-width:360px;border:1px solid #333">
      <h3 style="margin:0 0 16px;color:#eee" id="roles-form-title">新增角色</h3>
      <div style="display:grid;gap:10px">
        <input id="roles-form-name" placeholder="角色名称" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee">
        <input id="roles-form-capital" type="number" placeholder="初始资金 (默认 100000)" value="100000" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee">
        <div style="display:flex;gap:8px;align-items:center">
          <span style="color:#888;font-size:13px">卡片颜色:</span>
          <input id="roles-form-color" type="color" value="#2563EB" style="width:36px;height:28px;border:none;border-radius:4px;cursor:pointer">
        </div>
        <textarea id="roles-form-notes" placeholder="备注 (可选)" rows="2" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee;resize:vertical"></textarea>
      </div>
      <div style="display:flex;gap:10px;margin-top:16px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="document.getElementById('roles-form-modal').style.display='none'">取消</button>
        <button class="btn btn-primary btn-sm" id="roles-form-submit-btn" onclick="rolesSave()">确认创建</button>
      </div>
    </div>
  </div>

  <!-- 开仓 Modal -->
  <div id="roles-trade-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1001;justify-content:center;align-items:center">
    <div style="background:#1a1a2e;padding:24px;border-radius:8px;min-width:380px;border:1px solid #333">
      <h3 style="margin:0 0 16px;color:#eee">开仓</h3>
      <div style="display:grid;gap:10px">
        <input id="roles-trade-symbol" placeholder="股票代码 (例如 600519)" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee">
        <div style="display:flex;gap:10px">
          <select id="roles-trade-direction" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee;flex:1">
            <option value="long">做多</option><option value="short">做空</option>
          </select>
          <input id="roles-trade-price" type="number" step="0.01" placeholder="成交价" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee;flex:1">
        </div>
        <div style="display:flex;gap:10px">
          <input id="roles-trade-qty" type="number" placeholder="数量(股)" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee;flex:1">
          <input id="roles-trade-fee" type="number" step="0.01" placeholder="手续费" value="5" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee;flex:1">
        </div>
        <input id="roles-trade-date" type="date" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee">
        <input id="roles-trade-note" placeholder="备注(可选)" style="padding:8px;background:#111;border:1px solid #444;border-radius:4px;color:#eee">
      </div>
      <div style="display:flex;gap:10px;margin-top:16px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="document.getElementById('roles-trade-modal').style.display='none'">取消</button>
        <button class="btn btn-primary btn-sm" onclick="rolesOpen()">确认开仓</button>
      </div>
    </div>
  </div>

  <!-- CSV 导入 Modal -->
  <div id="roles-import-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1001;justify-content:center;align-items:center">
    <div style="background:#1a1a2e;padding:24px;border-radius:8px;min-width:400px;border:1px solid #333">
      <h3 style="margin:0 0 12px;color:#eee">导入交割单 CSV</h3>
      <p style="color:#888;font-size:12px;margin-bottom:12px">
        列：symbol, direction, price, quantity, fee, trade_date, close_price(可选), close_date(可选), note(可选)
      </p>
      <input type="file" id="roles-import-file" accept=".csv" style="margin-bottom:12px;color:#eee">
      <div id="roles-import-result" style="font-size:12px;margin-bottom:12px"></div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="document.getElementById('roles-import-modal').style.display='none'">关闭</button>
        <button class="btn btn-primary btn-sm" onclick="rolesImportCSV()">开始导入</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(前端): 角色验证视图 — 卡片墙 + 详情页 + 弹窗"
```

---

### Task 5: 前端 JavaScript 逻辑

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`（JS 区域）

- [ ] **Step 1: 添加 switchView 分支**

在 `switchView()` 函数中（约第 2803 行），添加 `roles` case。找到现有 view case（如 `case 'sim':`）的模式，添加：

```javascript
case 'roles':
  _rolesActiveId = null;
  document.getElementById('roles-cards-panel').style.display = 'block';
  document.getElementById('roles-detail-panel').style.display = 'none';
  loadRoles();
  break;
```

- [ ] **Step 2: 加载角色卡片列表**

```javascript
let _rolesActiveId = null;

async function loadRoles() {
  try {
    const r = await fetch('/api/roles/list');
    const j = await r.json();
    if (!j.success) return;
    renderRoleCards(j.data);
  } catch(e) { console.error('loadRoles', e); }
}

function renderRoleCards(roles) {
  const grid = document.getElementById('roles-cards-grid');
  if (!roles.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#888;padding:40px">暂无角色，点击上方按钮创建</div>';
    return;
  }
  const labelColors = {
    '反向指标': { bg: 'rgba(239,68,68,0.15)', color: 'var(--red)' },
    '正向指标': { bg: 'rgba(34,197,94,0.15)', color: 'var(--green)' },
    '随机漫步': { bg: 'rgba(156,163,175,0.15)', color: '#9ca3af' },
  };
  grid.innerHTML = roles.map(r => {
    const lc = labelColors[r.label] || labelColors['随机漫步'];
    return '<div class="card" style="cursor:pointer;border-left:4px solid ' + r.avatar_color + ';padding:20px" onclick="rolesOpenDetail(' + r.id + ',\'' + escapeHtml(r.name) + '\')">'
      + '<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">'
      + '<div style="width:40px;height:40px;border-radius:50%;background:' + r.avatar_color + '20;color:' + r.avatar_color + ';display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px">' + (r.name[0] || '?') + '</div>'
      + '<div><div style="font-weight:600;font-size:15px">' + escapeHtml(r.name) + '</div>'
      + '<div style="font-size:11px;color:var(--ink-3)">' + r.total_trades + ' 笔交易</div></div></div>'
      + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">'
      + '<div><span style="font-size:11px;color:var(--ink-3)">胜率</span> <span style="font-weight:600">' + (r.win_rate * 100).toFixed(0) + '%</span></div>'
      + '<div><span style="font-size:11px;color:var(--ink-3)">收益率</span> <span style="font-weight:600;color:' + (r.total_pnl >= 0 ? 'var(--red)' : 'var(--green)') + '">' + (r.total_pnl_pct >= 0 ? '+' : '') + r.total_pnl_pct.toFixed(1) + '%</span></div>'
      + '<div><span style="font-size:11px;color:var(--ink-3)">总PnL</span> <span style="font-weight:600">' + _fmtYuan(r.total_pnl) + '</span></div>'
      + '<div><span style="font-size:11px;color:var(--ink-3)">持仓</span> <span style="font-weight:600">' + r.open_positions + '</span></div></div>'
      + '<div style="font-size:11px;color:var(--ink-3);margin-bottom:8px">资金: ' + r.initial_capital.toLocaleString() + ' | 权益: ' + r.current_equity.toLocaleString() + '</div>'
      + '<span style="display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600;background:' + lc.bg + ';color:' + lc.color + '">' + r.label + '</span>'
      + '</div>';
  }).join('');
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
```

- [ ] **Step 3: 角色详情视图逻辑**

```javascript
async function rolesOpenDetail(roleId, name) {
  _rolesActiveId = roleId;
  document.getElementById('roles-cards-panel').style.display = 'none';
  document.getElementById('roles-detail-panel').style.display = 'block';
  document.getElementById('roles-detail-name').textContent = name;
  await rolesRefreshDetail();
}

async function rolesRefreshDetail() {
  const id = _rolesActiveId;
  if (!id) return;
  try {
    const [accR, statsR, posR, histR, roleR] = await Promise.all([
      fetch('/api/roles/' + id + '/account'), fetch('/api/roles/' + id + '/stats'),
      fetch('/api/roles/' + id + '/positions'), fetch('/api/roles/' + id + '/history'),
      fetch('/api/roles/' + id),
    ]);
    const [acc, stats, pos, hist, role] = await Promise.all([accR.json(), statsR.json(), posR.json(), histR.json(), roleR.json()]);

    if (acc.success) {
      const a = acc.data;
      document.getElementById('roles-detail-account').innerHTML =
        '资金: ' + a.initial_capital.toLocaleString()
        + ' | 权益: ' + a.total_equity.toLocaleString()
        + ' | 已实现P&L: <span style="color:' + (a.closed_pnl >= 0 ? 'var(--red)' : 'var(--green)') + '">' + _fmtYuan(a.closed_pnl) + '</span>'
        + ' | 收益率: ' + (a.total_return_pct >= 0 ? '+' : '') + a.total_return_pct + '%';
    }
    if (stats.success) {
      const s = stats.data;
      document.getElementById('roles-detail-stats').innerHTML =
        '<div class="card" style="padding:12px;text-align:center;min-width:80px"><div style="font-size:20px;font-weight:700">' + s.total_trades + '</div><div style="font-size:11px;color:#888">总交易</div></div>'
        + '<div class="card" style="padding:12px;text-align:center;min-width:80px"><div style="font-size:20px;font-weight:700">' + (s.win_rate * 100).toFixed(0) + '%</div><div style="font-size:11px;color:#888">胜率</div></div>'
        + '<div class="card" style="padding:12px;text-align:center;min-width:80px"><div style="font-size:20px;font-weight:700;color:' + (s.total_pnl >= 0 ? 'var(--red)' : 'var(--green)') + '">' + _fmtYuan(s.total_pnl) + '</div><div style="font-size:11px;color:#888">总PnL</div></div>'
        + '<div class="card" style="padding:12px;text-align:center;min-width:80px"><div style="font-size:20px;font-weight:700">' + (acc.success ? acc.data.positions_count : 0) + '</div><div style="font-size:11px;color:#888">持仓</div></div>';
    }
    if (pos.success) {
      const tb = document.getElementById('roles-positions-tbody');
      tb.innerHTML = pos.data.map(p => {
        const upnl = p.unrealized_pnl != null ? _fmtYuan(p.unrealized_pnl) : '--';
        const upnlCls = p.unrealized_pnl >= 0 ? 'text-red' : 'text-green';
        return '<tr><td>' + p.symbol + '</td><td>' + (p.direction === 'short' ? '做空' : '做多') + '</td>'
          + '<td>' + (p.price != null ? p.price.toFixed(2) : '--') + '</td>'
          + '<td>' + (p.current_price != null ? p.current_price.toFixed(2) : '--') + '</td>'
          + '<td>' + p.quantity + '</td>'
          + '<td class="' + upnlCls + '">' + upnl + '</td>'
          + '<td>' + (p.trade_date || '') + '</td>'
          + '<td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (p.note || '') + '</td>'
          + '<td><button class="btn btn-ghost btn-sm" onclick="rolesCloseForm(' + p.id + ')">平仓</button></td></tr>';
      }).join('') || '<tr><td colspan="9" style="color:#888;text-align:center">暂无持仓</td></tr>';
    }
    if (hist.success) {
      const tb = document.getElementById('roles-history-tbody');
      tb.innerHTML = hist.data.map(h => {
        const pnlCls = h.pnl >= 0 ? 'text-red' : 'text-green';
        return '<tr><td>' + h.symbol + '</td><td>' + (h.direction === 'short' ? '做空' : '做多') + '</td>'
          + '<td>' + (h.price != null ? h.price.toFixed(2) : '--') + '</td>'
          + '<td>' + (h.close_price != null ? h.close_price.toFixed(2) : '--') + '</td>'
          + '<td>' + h.quantity + '</td>'
          + '<td class="' + pnlCls + '">' + _fmtYuan(h.pnl) + '</td>'
          + '<td>' + (h.closed_at || '') + '</td>'
          + '<td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (h.note || '') + '</td></tr>';
      }).join('') || '<tr><td colspan="8" style="color:#888;text-align:center">暂无记录</td></tr>';
    }
    // Label badge
    if (stats.success) {
      const s = stats.data;
      const lbl = document.getElementById('roles-detail-label');
      const total = s.total_trades;
      let label = '随机漫步', bg = 'rgba(156,163,175,0.15)', color = '#9ca3af';
      if (total >= 10) {
        if (s.win_rate >= 0.55) { label = '正向指标'; bg = 'rgba(34,197,94,0.15)'; color = 'var(--green)'; }
        else if (s.win_rate <= 0.35) { label = '反向指标'; bg = 'rgba(239,68,68,0.15)'; color = 'var(--red)'; }
      }
      lbl.textContent = label;
      lbl.style.background = bg;
      lbl.style.color = color;
    }
  } catch(e) { console.error('rolesRefreshDetail', e); }
}

function rolesBackToCards() {
  _rolesActiveId = null;
  document.getElementById('roles-cards-panel').style.display = 'block';
  document.getElementById('roles-detail-panel').style.display = 'none';
  loadRoles();
}
```

- [ ] **Step 4: 角色 CRUD JS**

```javascript
let _rolesEditingId = null;

function rolesShowCreate() {
  _rolesEditingId = null;
  document.getElementById('roles-form-title').textContent = '新增角色';
  document.getElementById('roles-form-name').value = '';
  document.getElementById('roles-form-capital').value = '100000';
  document.getElementById('roles-form-color').value = '#2563EB';
  document.getElementById('roles-form-notes').value = '';
  document.getElementById('roles-form-submit-btn').textContent = '确认创建';
  document.getElementById('roles-form-modal').style.display = 'flex';
}

function rolesShowEdit() {
  if (!_rolesActiveId) return;
  fetch('/api/roles/' + _rolesActiveId).then(r => r.json()).then(j => {
    if (!j.success) return;
    _rolesEditingId = _rolesActiveId;
    const d = j.data;
    document.getElementById('roles-form-title').textContent = '编辑角色';
    document.getElementById('roles-form-name').value = d.name || '';
    document.getElementById('roles-form-capital').value = d.initial_capital || 100000;
    document.getElementById('roles-form-color').value = d.avatar_color || '#2563EB';
    document.getElementById('roles-form-notes').value = d.notes || '';
    document.getElementById('roles-form-submit-btn').textContent = '保存修改';
    document.getElementById('roles-form-modal').style.display = 'flex';
  });
}

async function rolesSave() {
  const name = document.getElementById('roles-form-name').value.trim();
  if (!name) return alert('请输入角色名称');
  const body = {
    name: name,
    initial_capital: parseFloat(document.getElementById('roles-form-capital').value) || 100000,
    avatar_color: document.getElementById('roles-form-color').value,
    notes: document.getElementById('roles-form-notes').value,
  };
  const url = _rolesEditingId ? '/api/roles/' + _rolesEditingId : '/api/roles/create';
  const method = _rolesEditingId ? 'PUT' : 'POST';
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json();
  if (j.success) {
    document.getElementById('roles-form-modal').style.display = 'none';
    if (_rolesEditingId) rolesRefreshDetail(); else loadRoles();
  }
}

async function rolesDelete() {
  if (!_rolesActiveId) return;
  if (!confirm('确定删除该角色？交易记录会保留。')) return;
  const r = await fetch('/api/roles/' + _rolesActiveId, { method: 'DELETE' });
  const j = await r.json();
  if (j.success) rolesBackToCards();
}
```

- [ ] **Step 5: 交易操作 JS**

```javascript
function rolesShowOpenForm() {
  document.getElementById('roles-trade-modal').style.display = 'flex';
  document.getElementById('roles-trade-date').value = new Date().toISOString().slice(0, 10);
}

async function rolesOpen() {
  const body = {
    symbol: document.getElementById('roles-trade-symbol').value.trim(),
    direction: document.getElementById('roles-trade-direction').value,
    price: parseFloat(document.getElementById('roles-trade-price').value) || 0,
    quantity: parseFloat(document.getElementById('roles-trade-qty').value) || 0,
    fee: parseFloat(document.getElementById('roles-trade-fee').value) || 0,
    trade_date: document.getElementById('roles-trade-date').value,
    note: document.getElementById('roles-trade-note').value,
  };
  if (!body.symbol || !body.price || !body.quantity) return alert('请填写完整信息');
  const r = await fetch('/api/roles/' + _rolesActiveId + '/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const j = await r.json();
  if (j.success) {
    document.getElementById('roles-trade-modal').style.display = 'none';
    rolesRefreshDetail();
  } else { alert(j.error); }
}

function rolesCloseForm(tradeId) {
  const price = prompt('平仓价格:');
  if (!price) return;
  const date = new Date().toISOString().slice(0, 10);
  fetch('/api/roles/' + _rolesActiveId + '/close', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: tradeId, close_price: parseFloat(price), close_date: date, fee: 0, note: '' }),
  }).then(r => r.json()).then(j => {
    if (j.success) rolesRefreshDetail(); else alert(j.error);
  });
}
```

- [ ] **Step 6: CSV 导入 JS**

```javascript
function rolesShowImportModal() {
  document.getElementById('roles-import-modal').style.display = 'flex';
  document.getElementById('roles-import-result').textContent = '';
}

async function rolesImportCSV() {
  const fileInput = document.getElementById('roles-import-file');
  const file = fileInput.files[0];
  if (!file) return alert('请选择 CSV 文件');
  const form = new FormData();
  form.append('file', file);
  const r = await fetch('/api/roles/' + _rolesActiveId + '/import-csv', { method: 'POST', body: form });
  const j = await r.json();
  document.getElementById('roles-import-result').textContent =
    '导入 ' + j.imported + ' 条' + (j.skipped ? '，跳过 ' + j.skipped + ' 条' : '');
  if (j.success) rolesRefreshDetail();
}
```

- [ ] **Step 7: 验证完整流程**

```bash
# 重启服务
lsof -i :8000 -t | xargs kill 2>/dev/null; sleep 1
cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python app.py &

# 测试角色 CRUD
curl -s -X POST http://localhost:8000/api/roles/create -H 'Content-Type: application/json' -d '{"name":"测试角色","notes":"短线"}'
curl -s http://localhost:8000/api/roles/list | python3 -m json.tool | head -20

# 测试角色交易
curl -s -X POST http://localhost:8000/api/roles/2/open -H 'Content-Type: application/json' -d '{"symbol":"600519","price":1500,"quantity":100,"trade_date":"2026-05-10"}'
curl -s http://localhost:8000/api/roles/2/positions | python3 -m json.tool | head -10
curl -s -X POST http://localhost:8000/api/roles/2/close -H 'Content-Type: application/json' -d '{"id":1,"close_price":1550,"close_date":"2026-05-13"}'
curl -s http://localhost:8000/api/roles/2/stats | python3 -m json.tool
```

- [ ] **Step 8: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(前端): 角色验证 JS 逻辑 — 卡片渲染/详情/交易/CSV导入"
```

---

## Verification Checklist

全部 Task 完成后：

1. 启动应用，访问 `http://localhost:8000`，点击"角色验证"进入卡片墙
2. 验证"默认账户"角色存在（来自迁移）
3. 创建新角色，进入详情页，执行开仓/平仓操作
4. 用 CSV 文件测试导入（含开仓和平仓两种模式）
5. 检查标签逻辑：创建 ≥10 笔交易且胜率 ≤35% 的角色 → 应显示"反向指标"
6. 旧模拟交易界面（`/sim`）仍正常显示数据
7. 角色间数据隔离：角色 A 的持仓不出现在角色 B 中
