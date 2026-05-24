# 自定义分类资金流向模块 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"资金追踪"模块，允许用户自定义分类（如机器人、半导体），向分类中添加 A 股并汇总查看小/中/大/超大单资金流向。

**Architecture:** 独立后端路由 `routers/flow_category.py` + 两张新 DB 表 + 前端新增一个 view（卡片总览 + 明细表格）。复用现有 `stock_service.get_money_flow()` 接口，在路由层做并发聚合和金额解析。

**Tech Stack:** FastAPI, SQLite, Vanilla JS (与现有 index.html 保持一致), AKShare（通过已有 StockService）

---

## 文件变更清单

| 操作 | 文件 |
|------|------|
| 修改 | `wexin-read-mcp-main/src/database.py` |
| 新建 | `wexin-read-mcp-main/src/routers/flow_category.py` |
| 修改 | `wexin-read-mcp-main/src/app.py` |
| 修改 | `wexin-read-mcp-main/src/templates/index.html` |

---

## Task 1: 新增数据库表

**Files:**
- Modify: `wexin-read-mcp-main/src/database.py`

- [ ] **Step 1: 在 `executescript` 中追加两张新表**

在 `database.py` 的 `_db.executescript("""` 块内，紧接 `industry_reports` 表定义之后（`CREATE TABLE IF NOT EXISTS industry_reports` 的结尾 `);` 后面）追加：

```sql
            CREATE TABLE IF NOT EXISTS flow_categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                sort_order  INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS flow_category_stocks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES flow_categories(id) ON DELETE CASCADE,
                symbol      TEXT NOT NULL,
                name        TEXT,
                added_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(category_id, symbol)
            );
```

- [ ] **Step 2: 启动验证表已创建**

```bash
cd wexin-read-mcp-main
python -c "
from src.database import init_db, get_db
init_db()
db = get_db()
tables = db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print([t[0] for t in tables])
"
```

预期输出包含：`flow_categories`, `flow_category_stocks`

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/database.py
git commit -m "feat(db): 新增 flow_categories + flow_category_stocks 表"
```

---

## Task 2: 创建后端路由

**Files:**
- Create: `wexin-read-mcp-main/src/routers/flow_category.py`

- [ ] **Step 1: 创建路由文件**

完整内容如下：

```python
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
    db = get_db()
    cats = db.execute(
        "SELECT id, name, sort_order, created_at FROM flow_categories ORDER BY sort_order, id"
    ).fetchall()
    result = []
    for c in cats:
        stocks = db.execute(
            "SELECT symbol, name FROM flow_category_stocks WHERE category_id=? ORDER BY added_at",
            (c[0],),
        ).fetchall()
        result.append({
            "id": c[0],
            "name": c[1],
            "sort_order": c[2],
            "created_at": c[3],
            "stocks": [{"symbol": s[0], "name": s[1]} for s in stocks],
        })
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
    db.execute("UPDATE flow_categories SET name=? WHERE id=?", (name, cat_id))
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
```

- [ ] **Step 2: Commit**

```bash
git add wexin-read-mcp-main/src/routers/flow_category.py
git commit -m "feat(api): flow_category 路由 — 分类CRUD + 股票管理 + 流向聚合"
```

---

## Task 3: 注册路由到 app.py

**Files:**
- Modify: `wexin-read-mcp-main/src/app.py`

- [ ] **Step 1: 在现有路由注册块末尾添加**

在 `app.py` 中 `app.include_router(industry_router)` 这行之后追加：

```python
from routers.flow_category import router as flow_category_router
app.include_router(flow_category_router)
```

- [ ] **Step 2: 启动验证路由注册成功**

```bash
cd wexin-read-mcp-main/src
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from app import app
routes = [r.path for r in app.routes if hasattr(r, 'path') and 'flow-category' in r.path]
print(routes)
"
```

预期输出：包含 `/api/flow-category/list`、`/api/flow-category/create` 等路径

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/app.py
git commit -m "feat(app): 注册 flow_category 路由"
```

---

## Task 4: 前端 — 侧边栏按钮 + topbarMeta

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在侧边栏 "自选股" 按钮之后插入新按钮**

找到：
```html
      <button class="nav-item" data-view="watchlist" onclick="switchView('watchlist', this)">
        <svg viewBox="0 0 20 20" fill="none"><polygon points="10,3 13,9 19,10 14,15 15,21 10,18 5,21 6,15 1,10 7,9" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        <span>自选股</span>
      </button>
```

在其之后插入：
```html
      <button class="nav-item" data-view="flowCategory" onclick="switchView('flowCategory', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M3 10h14M3 6h14M3 14h8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="16" cy="14" r="2.5" stroke="currentColor" stroke-width="1.5"/><path d="M16 11.5v0M16 16.5v0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        <span>资金追踪</span>
      </button>
```

- [ ] **Step 2: 在 topbarMeta 对象中添加条目**

找到：
```javascript
const topbarMeta = {
  task:     { title: '文章收集', sub: '从微信公众号抓取文章并生成分析报告' },
```

在 `task:` 这行之前（`const topbarMeta = {` 之后的第一行）插入：
```javascript
  flowCategory: { title: '资金追踪', sub: '自定义分类 · 小/中/大/超大单资金流向汇总' },
```

- [ ] **Step 3: 在 switchView 函数末尾添加跳转逻辑**

找到：
```javascript
  if (view === 'industry') { indLoadHistory(); }
}
```

改为：
```javascript
  if (view === 'industry') { indLoadHistory(); }
  if (view === 'flowCategory') { loadFlowCategories(); }
}
```

- [ ] **Step 4: Commit（含后续步骤一起提交）**

暂不提交，与 Task 5、6 一起提交前端。

---

## Task 5: 前端 — View 容器 HTML

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 找到插入位置**

在 index.html 中搜索：
```html
      <!-- ==================== VIEW: WATCHLIST ====================
```
在这段 watchlist view 开始注释之前，插入以下完整的 flowCategory view HTML：

```html
      <!-- ==================== VIEW: FLOW CATEGORY ==================== -->
      <div id="view-flowCategory" class="view">
        <!-- 卡片总览 -->
        <div id="fc-overview">
          <div class="page-header" style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
            <h2 style="margin:0;font-size:16px;font-weight:600;">资金追踪分类</h2>
            <button class="btn btn-sm" onclick="fcOpenCreateModal()">+ 新建分类</button>
            <button class="btn btn-sm btn-ghost" onclick="loadFlowCategories()" title="刷新">↻ 刷新</button>
          </div>
          <div id="fc-cards-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;">
            <div class="card" style="display:flex;align-items:center;justify-content:center;min-height:120px;cursor:pointer;border:2px dashed var(--border);color:var(--text-muted);" onclick="fcOpenCreateModal()">
              <span style="font-size:28px;line-height:1;">+</span>
              <span style="margin-left:8px;">新建分类</span>
            </div>
          </div>
        </div>

        <!-- 明细视图 -->
        <div id="fc-detail" style="display:none;">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
            <button class="btn btn-sm btn-ghost" onclick="fcBackToOverview()">← 返回</button>
            <h2 id="fc-detail-title" style="margin:0;font-size:16px;font-weight:600;"></h2>
            <span id="fc-detail-count" style="color:var(--text-muted);font-size:13px;"></span>
            <div style="margin-left:auto;display:flex;gap:8px;">
              <button class="btn btn-sm" onclick="fcOpenAddStockModal()">+ 添加股票</button>
              <button class="btn btn-sm btn-ghost" onclick="fcRenameCategory()">重命名</button>
              <button class="btn btn-sm btn-danger" onclick="fcDeleteCategory()">删除分类</button>
            </div>
          </div>
          <!-- 时间切换 Tab -->
          <div style="display:flex;gap:4px;margin-bottom:12px;">
            <button class="btn btn-sm fc-period-btn active" data-period="1" onclick="fcSwitchPeriod(1)">今日</button>
            <button class="btn btn-sm fc-period-btn btn-ghost" data-period="3" onclick="fcSwitchPeriod(3)">近3日</button>
            <button class="btn btn-sm fc-period-btn btn-ghost" data-period="5" onclick="fcSwitchPeriod(5)">近5日</button>
          </div>
          <!-- 合计栏 -->
          <div id="fc-summary-bar" style="background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-bottom:12px;display:flex;gap:24px;flex-wrap:wrap;">
            <span style="color:var(--text-muted);font-size:13px;">合计</span>
            <span>超大单：<b id="fc-sum-sl" style="color:var(--green)">--</b></span>
            <span>大单：<b id="fc-sum-la" style="color:var(--green)">--</b></span>
            <span>中单：<b id="fc-sum-me">--</b></span>
            <span>小单：<b id="fc-sum-sm">--</b></span>
            <span>主力净流入：<b id="fc-sum-mn" style="font-size:15px;">--</b></span>
          </div>
          <!-- 明细表格 -->
          <div class="table-wrap">
            <table class="data-table" id="fc-detail-table">
              <thead>
                <tr>
                  <th>股票</th>
                  <th>代码</th>
                  <th style="text-align:right;">超大单净流入(亿)</th>
                  <th style="text-align:right;">大单净流入(亿)</th>
                  <th style="text-align:right;">中单净流入(亿)</th>
                  <th style="text-align:right;">小单净流入(亿)</th>
                  <th style="text-align:right;">主力净流入(亿)</th>
                  <th style="text-align:center;">近5日趋势</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="fc-detail-tbody"></tbody>
            </table>
          </div>
        </div>

        <!-- Modal: 新建分类 -->
        <div id="fc-create-modal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)this.style.display='none'">
          <div class="modal-box" style="max-width:380px;">
            <h3 style="margin:0 0 16px;">新建分类</h3>
            <input id="fc-create-name" class="input" placeholder="分类名称，如：机器人" style="width:100%;box-sizing:border-box;margin-bottom:12px;" />
            <div style="display:flex;gap:8px;justify-content:flex-end;">
              <button class="btn btn-ghost" onclick="document.getElementById('fc-create-modal').style.display='none'">取消</button>
              <button class="btn" onclick="fcCreateCategory()">创建</button>
            </div>
          </div>
        </div>

        <!-- Modal: 添加股票 -->
        <div id="fc-stock-modal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)this.style.display='none'">
          <div class="modal-box" style="max-width:420px;">
            <h3 style="margin:0 0 16px;">向分类添加股票</h3>
            <div style="position:relative;margin-bottom:8px;">
              <input id="fc-stock-search" class="input" placeholder="搜索 A 股代码或名称..." style="width:100%;box-sizing:border-box;"
                oninput="fcSearchStock(this.value)" autocomplete="off" />
              <div id="fc-stock-results" style="position:absolute;top:100%;left:0;right:0;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;max-height:200px;overflow-y:auto;z-index:100;display:none;"></div>
            </div>
            <div id="fc-stock-added-list" style="margin-top:8px;display:flex;flex-direction:column;gap:4px;"></div>
            <div style="display:flex;justify-content:flex-end;margin-top:12px;">
              <button class="btn btn-ghost" onclick="document.getElementById('fc-stock-modal').style.display='none'">关闭</button>
            </div>
          </div>
        </div>
      </div>
```

- [ ] **Step 2: 暂不提交，继续 Task 6**

---

## Task 6: 前端 — JavaScript 逻辑

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 `</script>` 闭合标签之前插入以下 JS 代码**

找到文件末尾的 `</script>` 标签，在其前面插入：

```javascript
/* ============================================================
   FLOW CATEGORY — 资金追踪分类
   ============================================================ */
let _fcCurrentCatId = null;
let _fcCurrentPeriod = 1;
let _fcCategories = [];
let _fcSearchTimer = null;

function fcFmtAmt(v) {
  if (v === null || v === undefined) return '--';
  const n = parseFloat(v);
  if (isNaN(n)) return '--';
  const abs = Math.abs(n);
  const sign = n >= 0 ? '+' : '-';
  const color = n >= 0 ? 'var(--green)' : 'var(--red)';
  let text;
  if (abs >= 1) text = sign + abs.toFixed(2) + '亿';
  else if (abs >= 0.0001) text = sign + (abs * 10000).toFixed(0) + '万';
  else text = sign + abs.toFixed(4);
  return `<span style="color:${color}">${text}</span>`;
}

function fcTrend(arr) {
  if (!arr || !arr.length) return '<span style="color:var(--text-muted)">--</span>';
  return arr.map(v => `<span style="color:${v >= 0 ? 'var(--green)' : 'var(--red)'}">${v >= 0 ? '↑' : '↓'}</span>`).join('');
}

async function loadFlowCategories() {
  const grid = document.getElementById('fc-cards-grid');
  grid.innerHTML = '<div style="color:var(--text-muted);padding:20px;">加载中...</div>';
  try {
    const res = await fetch('/api/flow-category/list');
    const data = await res.json();
    if (!data.success) { grid.innerHTML = `<div style="color:var(--red)">${data.error}</div>`; return; }
    _fcCategories = data.data;
    fcRenderCards(_fcCategories);
    // 异步加载每张卡片的流向数据
    _fcCategories.forEach(cat => fcLoadCardFlow(cat.id));
  } catch (e) {
    grid.innerHTML = `<div style="color:var(--red)">加载失败: ${e}</div>`;
  }
}

function fcRenderCards(cats) {
  const grid = document.getElementById('fc-cards-grid');
  let html = '';
  cats.forEach(cat => {
    html += `
      <div class="card" style="cursor:pointer;transition:border-color .2s;" id="fc-card-${cat.id}" onclick="fcOpenDetail(${cat.id})">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
          <div>
            <div style="font-weight:600;font-size:15px;">${cat.name}</div>
            <div style="color:var(--text-muted);font-size:12px;margin-top:2px;">${cat.stocks.length} 只股票</div>
          </div>
          <div id="fc-card-main-${cat.id}" style="font-size:13px;font-weight:600;">--</div>
        </div>
        <div id="fc-card-body-${cat.id}" style="display:flex;gap:12px;font-size:12px;color:var(--text-muted);">
          <span>超大单: <span id="fc-card-sl-${cat.id}">--</span></span>
          <span>大单: <span id="fc-card-la-${cat.id}">--</span></span>
        </div>
        <div id="fc-card-bar-${cat.id}" style="display:flex;gap:2px;height:6px;border-radius:3px;overflow:hidden;margin-top:8px;background:var(--border);">
        </div>
      </div>`;
  });
  html += `
    <div class="card" style="display:flex;align-items:center;justify-content:center;min-height:120px;cursor:pointer;border:2px dashed var(--border);color:var(--text-muted);" onclick="fcOpenCreateModal()">
      <span style="font-size:28px;line-height:1;">+</span>
      <span style="margin-left:8px;">新建分类</span>
    </div>`;
  grid.innerHTML = html;
}

async function fcLoadCardFlow(catId) {
  try {
    const res = await fetch(`/api/flow-category/${catId}/flow?period=1`);
    const data = await res.json();
    if (!data.success) return;
    const t = data.data.total;
    const mainEl = document.getElementById(`fc-card-main-${catId}`);
    const slEl = document.getElementById(`fc-card-sl-${catId}`);
    const laEl = document.getElementById(`fc-card-la-${catId}`);
    const barEl = document.getElementById(`fc-card-bar-${catId}`);
    if (mainEl) mainEl.innerHTML = fcFmtAmt(t.main_net) + '<span style="font-size:11px;color:var(--text-muted);font-weight:400;"> 主力</span>';
    if (slEl) slEl.innerHTML = fcFmtAmt(t.super_large_net);
    if (laEl) laEl.innerHTML = fcFmtAmt(t.large_net);
    // 迷你条形图
    if (barEl) {
      const abs = [Math.abs(t.super_large_net), Math.abs(t.large_net), Math.abs(t.medium_net), Math.abs(t.small_net)];
      const vals = [t.super_large_net, t.large_net, t.medium_net, t.small_net];
      const total = abs.reduce((a, b) => a + b, 0) || 1;
      const colors = ['#a78bfa', '#60a5fa', '#34d399', '#fb923c'];
      barEl.innerHTML = abs.map((a, i) =>
        `<div style="flex:${(a/total*100).toFixed(1)};background:${vals[i]>=0?colors[i]:colors[i]+'88'};opacity:${vals[i]>=0?1:0.5};"></div>`
      ).join('');
    }
  } catch (e) { /* 静默失败 */ }
}

function fcOpenDetail(catId) {
  _fcCurrentCatId = catId;
  _fcCurrentPeriod = 1;
  const cat = _fcCategories.find(c => c.id === catId);
  if (!cat) return;
  document.getElementById('fc-overview').style.display = 'none';
  document.getElementById('fc-detail').style.display = 'block';
  document.getElementById('fc-detail-title').textContent = cat.name;
  document.getElementById('fc-detail-count').textContent = `${cat.stocks.length} 只股票`;
  document.querySelectorAll('.fc-period-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.period) === 1);
    b.classList.toggle('btn-ghost', parseInt(b.dataset.period) !== 1);
  });
  fcLoadDetail(catId, 1);
}

function fcBackToOverview() {
  _fcCurrentCatId = null;
  document.getElementById('fc-detail').style.display = 'none';
  document.getElementById('fc-overview').style.display = 'block';
}

function fcSwitchPeriod(period) {
  _fcCurrentPeriod = period;
  document.querySelectorAll('.fc-period-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.period) === period);
    b.classList.toggle('btn-ghost', parseInt(b.dataset.period) !== period);
  });
  if (_fcCurrentCatId) fcLoadDetail(_fcCurrentCatId, period);
}

async function fcLoadDetail(catId, period) {
  const tbody = document.getElementById('fc-detail-tbody');
  tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:20px;">加载中...</td></tr>';
  ['fc-sum-sl','fc-sum-la','fc-sum-me','fc-sum-sm','fc-sum-mn'].forEach(id => {
    document.getElementById(id).innerHTML = '--';
  });
  try {
    const res = await fetch(`/api/flow-category/${catId}/flow?period=${period}`);
    const data = await res.json();
    if (!data.success) {
      tbody.innerHTML = `<tr><td colspan="9" style="color:var(--red);text-align:center;">${data.error}</td></tr>`;
      return;
    }
    const d = data.data;
    // 合计栏
    document.getElementById('fc-sum-sl').innerHTML = fcFmtAmt(d.total.super_large_net);
    document.getElementById('fc-sum-la').innerHTML = fcFmtAmt(d.total.large_net);
    document.getElementById('fc-sum-me').innerHTML = fcFmtAmt(d.total.medium_net);
    document.getElementById('fc-sum-sm').innerHTML = fcFmtAmt(d.total.small_net);
    document.getElementById('fc-sum-mn').innerHTML = fcFmtAmt(d.total.main_net);
    // 表格行（按主力净流入降序排列）
    const sorted = [...d.stocks].sort((a, b) => b.main_net - a.main_net);
    tbody.innerHTML = sorted.map(s => `
      <tr>
        <td>${s.name || '--'}</td>
        <td style="font-family:monospace;">${s.symbol}</td>
        <td style="text-align:right;">${fcFmtAmt(s.super_large_net)}</td>
        <td style="text-align:right;">${fcFmtAmt(s.large_net)}</td>
        <td style="text-align:right;">${fcFmtAmt(s.medium_net)}</td>
        <td style="text-align:right;">${fcFmtAmt(s.small_net)}</td>
        <td style="text-align:right;font-weight:600;">${fcFmtAmt(s.main_net)}</td>
        <td style="text-align:center;">${fcTrend(s.trend)}</td>
        <td><button class="btn btn-sm btn-danger" onclick="fcRemoveStock(${catId},'${s.symbol}')">×</button></td>
      </tr>
    `).join('') || '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);">暂无股票</td></tr>';
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="9" style="color:var(--red);text-align:center;">加载失败: ${e}</td></tr>`;
  }
}

function fcOpenCreateModal() {
  document.getElementById('fc-create-name').value = '';
  document.getElementById('fc-create-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('fc-create-name').focus(), 50);
}

async function fcCreateCategory() {
  const name = document.getElementById('fc-create-name').value.trim();
  if (!name) return;
  const res = await fetch('/api/flow-category/create', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name}),
  });
  const data = await res.json();
  if (data.success) {
    document.getElementById('fc-create-modal').style.display = 'none';
    loadFlowCategories();
  } else {
    showToast('创建失败: ' + data.error, 'error');
  }
}

async function fcRenameCategory() {
  if (!_fcCurrentCatId) return;
  const cat = _fcCategories.find(c => c.id === _fcCurrentCatId);
  const newName = prompt('输入新名称:', cat?.name || '');
  if (!newName || !newName.trim()) return;
  const res = await fetch(`/api/flow-category/${_fcCurrentCatId}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: newName.trim()}),
  });
  const data = await res.json();
  if (data.success) {
    document.getElementById('fc-detail-title').textContent = newName.trim();
    loadFlowCategories();
  }
}

async function fcDeleteCategory() {
  if (!_fcCurrentCatId) return;
  const cat = _fcCategories.find(c => c.id === _fcCurrentCatId);
  if (!confirm(`确认删除分类「${cat?.name}」？分类内所有股票记录将一并删除。`)) return;
  await fetch(`/api/flow-category/${_fcCurrentCatId}`, {method: 'DELETE'});
  fcBackToOverview();
  loadFlowCategories();
}

function fcOpenAddStockModal() {
  document.getElementById('fc-stock-search').value = '';
  document.getElementById('fc-stock-results').style.display = 'none';
  fcRefreshAddedList();
  document.getElementById('fc-stock-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('fc-stock-search').focus(), 50);
}

function fcRefreshAddedList() {
  if (!_fcCurrentCatId) return;
  const cat = _fcCategories.find(c => c.id === _fcCurrentCatId);
  const list = document.getElementById('fc-stock-added-list');
  if (!cat || !cat.stocks.length) { list.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">暂无股票</div>'; return; }
  list.innerHTML = cat.stocks.map(s =>
    `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 8px;background:var(--hover-bg);border-radius:4px;">
      <span>${s.name || s.symbol} <span style="color:var(--text-muted);font-size:12px;">${s.symbol}</span></span>
      <button class="btn btn-sm btn-danger" onclick="fcRemoveStockFromModal(${_fcCurrentCatId},'${s.symbol}')">×</button>
    </div>`
  ).join('');
}

function fcSearchStock(query) {
  clearTimeout(_fcSearchTimer);
  const results = document.getElementById('fc-stock-results');
  if (!query || query.length < 1) { results.style.display = 'none'; return; }
  _fcSearchTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/stock/search?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      const items = (data.results || data.data || []).slice(0, 8);
      if (!items.length) { results.style.display = 'none'; return; }
      results.innerHTML = items.map(it => {
        const code = it.code || it.symbol || '';
        const name = it.name || '';
        return `<div style="padding:8px 12px;cursor:pointer;display:flex;gap:8px;" onmouseenter="this.style.background='var(--hover-bg)'" onmouseleave="this.style.background=''" onclick="fcAddStockFromSearch('${code}','${name.replace(/'/g,"\\'")}')">
          <span style="font-family:monospace;color:var(--text-muted);font-size:12px;">${code}</span>
          <span>${name}</span>
        </div>`;
      }).join('');
      results.style.display = 'block';
    } catch (e) { results.style.display = 'none'; }
  }, 200);
}

async function fcAddStockFromSearch(symbol, name) {
  document.getElementById('fc-stock-results').style.display = 'none';
  document.getElementById('fc-stock-search').value = '';
  if (!_fcCurrentCatId) return;
  const res = await fetch(`/api/flow-category/${_fcCurrentCatId}/stocks`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({symbol, name}),
  });
  const data = await res.json();
  if (data.success) {
    // 更新本地缓存
    const cat = _fcCategories.find(c => c.id === _fcCurrentCatId);
    if (cat && !cat.stocks.find(s => s.symbol === symbol)) {
      cat.stocks.push({symbol, name});
    }
    fcRefreshAddedList();
    document.getElementById('fc-detail-count').textContent = `${cat?.stocks.length || 0} 只股票`;
  } else {
    showToast('添加失败: ' + data.error, 'error');
  }
}

async function fcRemoveStockFromModal(catId, symbol) {
  await fetch(`/api/flow-category/${catId}/stocks/${symbol}`, {method: 'DELETE'});
  const cat = _fcCategories.find(c => c.id === catId);
  if (cat) cat.stocks = cat.stocks.filter(s => s.symbol !== symbol);
  fcRefreshAddedList();
  document.getElementById('fc-detail-count').textContent = `${cat?.stocks.length || 0} 只股票`;
}

async function fcRemoveStock(catId, symbol) {
  if (!confirm(`确认从分类中移除 ${symbol}？`)) return;
  await fetch(`/api/flow-category/${catId}/stocks/${symbol}`, {method: 'DELETE'});
  const cat = _fcCategories.find(c => c.id === catId);
  if (cat) cat.stocks = cat.stocks.filter(s => s.symbol !== symbol);
  fcLoadDetail(catId, _fcCurrentPeriod);
}

// Enter 键快捷确认新建分类
document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('fc-create-name');
  if (inp) inp.addEventListener('keydown', e => { if (e.key === 'Enter') fcCreateCategory(); });
});
```

- [ ] **Step 2: 提交所有前端修改**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(frontend): 资金追踪模块 — 分类卡片 + 明细表格 + 管理弹窗"
```

---

## Task 7: 端到端验证

- [ ] **Step 1: 启动服务**

```bash
cd wexin-read-mcp-main/src
python app.py
```

确认无 import error，日志显示"数据库初始化完成"

- [ ] **Step 2: API 验证**

```bash
# 创建分类
curl -s -X POST http://localhost:8000/api/flow-category/create -H "Content-Type: application/json" -d "{\"name\":\"测试机器人\"}"
# 预期: {"success":true,"id":1}

# 添加股票（机器人 300024）
curl -s -X POST http://localhost:8000/api/flow-category/1/stocks -H "Content-Type: application/json" -d "{\"symbol\":\"300024\",\"name\":\"机器人\"}"
# 预期: {"success":true}

# 获取列表
curl -s http://localhost:8000/api/flow-category/list
# 预期: {"success":true,"data":[{"id":1,"name":"测试机器人","stocks":[{"symbol":"300024","name":"机器人"}],...}]}

# 获取今日流向
curl -s "http://localhost:8000/api/flow-category/1/flow?period=1"
# 预期: {"success":true,"data":{"stocks":[...],"total":{...}}}

# 获取5日流向
curl -s "http://localhost:8000/api/flow-category/1/flow?period=5"
```

- [ ] **Step 3: 前端验证**

打开 http://localhost:8000，验证：
1. 侧边栏出现"资金追踪"按钮
2. 点击后显示卡片总览页（含"+ 新建分类"卡片）
3. 点击"+ 新建分类"，输入"机器人"，确认创建成功
4. 卡片异步加载流向数据（若无网络可忽略数值，确认 UI 不崩溃）
5. 点击卡片进入明细页，切换今日/近3日/近5日 Tab
6. 点击"添加股票"，搜索"机器人"或"300024"，添加
7. 关闭弹窗后明细表格刷新，显示该股票数据
8. 点击 × 移除股票，确认移除
9. 删除整个分类，确认返回总览

- [ ] **Step 4: Final commit（如有遗漏修复）**

```bash
git add -A
git commit -m "fix: 资金追踪模块微调"
```
