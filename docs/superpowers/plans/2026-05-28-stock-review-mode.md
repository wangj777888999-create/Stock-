# 个股快速复盘模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立"复盘"侧边栏页面，支持随机翻阅全市场 A 股（5522 只）K 线图（日/周/月/分时）+ 均线 + 量能，并可一键加入自选股或记录备注。

**Architecture:** 后端新增 `routers/review.py`（股票列表接口 + 备注 CRUD）及 `review_notes` 表；前端在 `index.html` 新增 `view-review` 视图，复用 `LightweightCharts` + 已有 `computeMA()` 做 K 线渲染，通过 `GET /api/review/stocks` 加载股票列表后在浏览器内 Fisher-Yates 洗牌。

**Tech Stack:** Python/FastAPI, SQLite, Vanilla JS, LightweightCharts (已内嵌)

---

## 文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `wexin-read-mcp-main/src/database.py` — 新增 `review_notes` 表 |
| 新建 | `wexin-read-mcp-main/src/routers/review.py` — 路由文件 |
| 修改 | `wexin-read-mcp-main/src/app.py` — 注册路由 |
| 修改 | `wexin-read-mcp-main/src/templates/index.html` — 侧边栏 + 视图 HTML + JS |

---

## Task 1: 数据库 — 新增 review_notes 表

**Files:**
- Modify: `wexin-read-mcp-main/src/database.py:268-283`

- [ ] **Step 1: 在 `_migrate()` 的 `new_tables` 列表末尾追加 `review_notes` 表**

在 `database.py` 中找到 `new_tables` 列表（当前最后一项是 `flow_category_stocks`），在列表末尾追加一项：

```python
        """CREATE TABLE IF NOT EXISTS review_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            name       TEXT,
            note       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
```

修改后 `new_tables` 完整形如：
```python
    new_tables = [
        """CREATE TABLE IF NOT EXISTS flow_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            sort_order  INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS flow_category_stocks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES flow_categories(id) ON DELETE CASCADE,
            symbol      TEXT NOT NULL,
            name        TEXT,
            added_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(category_id, symbol)
        )""",
        """CREATE TABLE IF NOT EXISTS review_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            name       TEXT,
            note       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    ]
```

- [ ] **Step 2: 验证语法**

```bash
cd wexin-read-mcp-main && python -c "import database; print('OK')"
```

Expected: `OK`（无报错）

---

## Task 2: 后端 — 创建 routers/review.py

**Files:**
- Create: `wexin-read-mcp-main/src/routers/review.py`

- [ ] **Step 1: 创建文件，写入完整内容**

```python
"""复盘模块 — 股票列表 + 备注 CRUD。"""
import json
import logging
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from database import get_db

router = APIRouter(prefix="/api/review", tags=["review"])
logger = logging.getLogger(__name__)

_STOCK_LIST_PATH = Path(__file__).parent.parent / "stock_list.json"


@router.get("/stocks")
async def list_stocks():
    """返回全部 A 股列表（code, name），供前端洗牌使用。"""
    try:
        data = json.loads(_STOCK_LIST_PATH.read_text(encoding="utf-8"))
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"读取股票列表失败: {e}")
        return {"success": False, "error": str(e), "data": []}


class NoteCreate(BaseModel):
    symbol: str
    name: str = ""
    note: str


@router.get("/notes")
async def list_notes(symbol: str = ""):
    db = get_db()
    if symbol:
        rows = db.execute(
            "SELECT id, symbol, name, note, created_at FROM review_notes"
            " WHERE symbol=? ORDER BY created_at DESC",
            (symbol.upper(),),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, symbol, name, note, created_at FROM review_notes"
            " ORDER BY created_at DESC"
        ).fetchall()
    return {"success": True, "data": [
        {"id": r[0], "symbol": r[1], "name": r[2], "note": r[3], "created_at": r[4]}
        for r in rows
    ]}


@router.post("/notes")
async def add_note(req: NoteCreate):
    note = req.note.strip()
    if not note:
        return {"success": False, "error": "备注不能为空"}
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO review_notes (symbol, name, note) VALUES (?,?,?)",
            (req.symbol.upper(), req.name.strip(), note),
        )
        db.commit()
        return {"success": True, "id": cur.lastrowid}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int):
    db = get_db()
    db.execute("DELETE FROM review_notes WHERE id=?", (note_id,))
    db.commit()
    return {"success": True}
```

- [ ] **Step 2: 验证语法**

```bash
cd wexin-read-mcp-main/src && python -c "from routers.review import router; print('OK')"
```

Expected: `OK`

---

## Task 3: 后端 — 注册路由

**Files:**
- Modify: `wexin-read-mcp-main/src/app.py:103-104`

- [ ] **Step 1: 在 `app.py` 末尾（flow_category_router 注册之后）追加**

在文件 `app.py` 中，找到：
```python
from routers.flow_category import router as flow_category_router
app.include_router(flow_category_router)
```

在其后追加：
```python
from routers.review import router as review_router
app.include_router(review_router)
```

- [ ] **Step 2: 启动服务验证接口已注册**

```bash
cd wexin-read-mcp-main/src && python -c "from app import app; routes=[r.path for r in app.routes]; print([r for r in routes if 'review' in r])"
```

Expected（含 review 路由）：
```
['/api/review/stocks', '/api/review/notes', '/api/review/notes', '/api/review/notes/{note_id}']
```

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/database.py wexin-read-mcp-main/src/routers/review.py wexin-read-mcp-main/src/app.py
git commit -m "feat(backend): 复盘模块 — review_notes 表 + review 路由"
```

---

## Task 4: 前端 HTML — 侧边栏按钮 + topbarMeta + 视图骨架

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

项目没有测试套件，以下步骤每步完成后在浏览器中目视验证。

### Step 0: 添加侧边栏收起 CSS

在 `index.html` 找到（约 986 行）媒体查询结束处：
```css
  .nav-item { justify-content: center; padding: 12px; }
  .content-wrap { padding: 20px 16px; }
  .form-row { grid-template-columns: 1fr; }
  .blogger-grid { grid-template-columns: 1fr 1fr; }
  .topbar { padding: 0 16px; }
}
```

在 `}` 之后（约 986 行）追加：
```css
.sidebar.rv-collapsed { width: 60px; }
.sidebar.rv-collapsed .brand span,
.sidebar.rv-collapsed .tagline,
.sidebar.rv-collapsed .nav-item span,
.sidebar.rv-collapsed .nav-section-label,
.sidebar.rv-collapsed .sidebar-footer { display: none; }
.sidebar.rv-collapsed .nav-item { justify-content: center; padding: 12px; }
```

### Step 1: 侧边栏增加"复盘"按钮

在 `index.html` 中找到（约 1269 行）：
```html
      <button class="nav-item" data-view="analysis" onclick="switchView('analysis', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M3 17V8l4-4 3 5 4-6 3 5v9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 14h16" stroke="currentColor" stroke-width="1.1" stroke-dasharray="2 2.5" stroke-linecap="round"/></svg>
        <span>技术分析</span>
      </button>
      <div class="nav-section-label" style="margin-top:8px;">系统</div>
```

在 `</button>` 和 `<div class="nav-section-label"` 之间插入：
```html
      <button class="nav-item" data-view="review" onclick="switchView('review', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M4 4h12v12H4z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M7 8h6M7 11h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M14 15l2 2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        <span>复盘</span>
      </button>
```

### Step 2: 在 topbarMeta 中添加 review 条目

在 `index.html` 中找到（约 2916 行）：
```js
  analysis: { title: '技术分析', sub: '画图工具 · 形态识别 · 分析笔记' },
```

在其后追加：
```js
  review:   { title: '复盘', sub: '随机翻阅全市场 A 股 · K线 + 均线 + 量能 · 备注' },
```

### Step 3: 在 view-analysis 之后插入 view-review HTML

在 `index.html` 中找到（约 2658 行）：
```html
      </div><!-- /view-analysis -->

      <!-- ==================== VIEW: INDUSTRY ==================== -->
```

在 `<!-- /view-analysis -->` 和 `<!-- VIEW: INDUSTRY -->` 之间插入：

```html
      <!-- ==================== VIEW: REVIEW ==================== -->
      <div id="view-review" class="view">

        <!-- 顶部导航栏 -->
        <div class="card" style="margin-bottom:12px;">
          <div class="card-body" style="padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;">
            <button class="btn btn-ghost btn-sm" onclick="rvPrev()">← 上一只</button>
            <div style="display:flex;flex-direction:column;align-items:center;gap:2px;">
              <span id="rv-stock-name" style="font-weight:600;font-size:15px;color:var(--ink);">加载中...</span>
              <span id="rv-progress" style="font-size:12px;color:var(--ink-3);">— / —</span>
            </div>
            <button class="btn btn-ghost btn-sm" onclick="rvNext()">下一只 →</button>
          </div>
        </div>

        <!-- 周期 Tab + 放大按钮 -->
        <div class="card" style="margin-bottom:8px;">
          <div class="card-body" style="padding:8px 16px;display:flex;align-items:center;gap:6px;">
            <button class="btn btn-ghost btn-sm" id="rv-tab-timeshare" onclick="rvSwitchPeriod('1min',this)">分时</button>
            <button class="btn btn-ghost btn-sm active" id="rv-tab-day" onclick="rvSwitchPeriod('day',this)">日线</button>
            <button class="btn btn-ghost btn-sm" id="rv-tab-week" onclick="rvSwitchPeriod('week',this)">周线</button>
            <button class="btn btn-ghost btn-sm" id="rv-tab-month" onclick="rvSwitchPeriod('month',this)">月线</button>
            <div style="flex:1;"></div>
            <button class="btn btn-ghost btn-sm" onclick="rvToggleSidebar()" title="收起/展开侧边栏，放大图表">
              <svg width="14" height="14" viewBox="0 0 20 20" fill="none"><path d="M3 3h5M3 3v5M17 17h-5M17 17v-5M3 17h5M3 17v-5M17 3h-5M17 3v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
        </div>

        <!-- 主图（K线 + 量能） -->
        <div class="card" style="margin-bottom:8px;">
          <div class="card-body" style="padding:0;">
            <div id="rv-chart" style="width:100%;height:460px;"></div>
          </div>
        </div>

        <!-- 操作栏 + 备注 -->
        <div class="card">
          <div class="card-body" style="padding:12px 16px;display:flex;flex-direction:column;gap:10px;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <button class="btn btn-ghost btn-sm" onclick="rvAddWatchlist()">
                <svg width="13" height="13" viewBox="0 0 20 20" fill="none"><path d="M10 2l2.4 5.1 5.6.5-4.1 3.8 1.2 5.6L10 14.3l-5.1 2.7 1.2-5.6L2 7.6l5.6-.5z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
                加入自选股
              </button>
              <div style="flex:1;display:flex;gap:6px;align-items:center;min-width:200px;">
                <input id="rv-note-input" class="input" placeholder="写备注… (Enter 保存)" style="flex:1;"
                       onkeydown="if(event.key==='Enter'){event.preventDefault();rvSaveNote();}">
                <button class="btn btn-primary btn-sm" onclick="rvSaveNote()">保存</button>
              </div>
            </div>
            <div id="rv-notes-list" style="display:flex;flex-direction:column;gap:6px;"></div>
          </div>
        </div>

      </div><!-- /view-review -->
```

- [ ] **验证 HTML 结构**：启动服务后打开浏览器，侧边栏出现"复盘"入口，点击后进入复盘视图（此时图表区为空，因 JS 尚未写入）。

---

## Task 5: 前端 JS — 全局状态 + switchView 接入

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

### Step 1: 在 TA 状态块之后添加 RV 状态变量

在 `index.html` 中找到（约 4426 行）：
```js
let _taPatternFilters = new Set([...]);
```

在其后插入：
```js

/* ============================================================
   REVIEW — 复盘视图全局状态
   ============================================================ */
let _rvQueue = [], _rvIndex = 0;
let _rvSymbol = '', _rvName = '', _rvPeriod = 'day';
let _rvChart = null, _rvSeries = null, _rvVolSeries = null;
let _rvResizeObserver = null;
let _rvInited = false;
```

### Step 2: 在 switchView 的视图离开清理块中追加 RV 清理

在 `index.html` 找到（约 3375 行）：
```js
  _taCanvas = null; _taCtx = null; _taDrawing = null;
```

在其后插入：
```js
  // 复盘视图清理
  if (_rvResizeObserver) { _rvResizeObserver.disconnect(); _rvResizeObserver = null; }
  if (_rvChart) { try { _rvChart.remove(); } catch(e){} _rvChart = null; _rvSeries = null; _rvVolSeries = null; }
```

### Step 3: 在 switchView 的视图初始化块中追加 RV 初始化

在 `index.html` 找到（约 3415 行）：
```js
  if (view === 'flowCategory') { loadFlowCategories(); }
```

在其后插入：
```js
  if (view === 'review') { initReview(); }
```

---

## Task 6: 前端 JS — 核心函数（initReview + rvLoadStock + rvRenderChart）

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

在 `taRenderChart` 函数块结束之后（搜索 `// Canvas 叠加层` 附近，约 6917 行之后）找到合适位置插入以下全部代码块。实践中找到 `taInitView` 函数定义，在 TA 函数块结束处（搜索 `/* ===== COCKPIT`，在此注释之前）插入全部复盘 JS。

插入以下完整代码段：

```js
/* ============================================================
   REVIEW — 复盘视图
   ============================================================ */

async function initReview() {
  if (_rvInited && _rvQueue.length > 0) {
    // 已初始化过，直接显示当前股票
    _rvUpdateNav();
    return;
  }
  document.getElementById('rv-stock-name').textContent = '加载中...';
  document.getElementById('rv-progress').textContent = '— / —';
  try {
    const json = await (await fetch('/api/review/stocks')).json();
    if (!json.success || !json.data?.length) {
      document.getElementById('rv-stock-name').textContent = '股票列表加载失败';
      return;
    }
    // Fisher-Yates 洗牌
    const arr = json.data.slice();
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    _rvQueue = arr;
    _rvIndex = 0;
    _rvInited = true;
    await rvLoadStock(_rvQueue[0]);
  } catch(e) {
    document.getElementById('rv-stock-name').textContent = '加载失败: ' + e.message;
  }
}

function _rvUpdateNav() {
  const s = _rvQueue[_rvIndex];
  if (!s) return;
  document.getElementById('rv-stock-name').textContent = s.name + '（' + s.code + '）';
  document.getElementById('rv-progress').textContent = (_rvIndex + 1) + ' / ' + _rvQueue.length;
}

async function rvLoadStock(stock) {
  _rvSymbol = stock.code;
  _rvName = stock.name;
  _rvUpdateNav();
  await rvRenderChart();
  rvLoadNotes();
  document.getElementById('rv-note-input').value = '';
}

async function rvRenderChart() {
  if (!_rvSymbol) return;

  // 销毁旧图表实例
  if (_rvResizeObserver) { _rvResizeObserver.disconnect(); _rvResizeObserver = null; }
  if (_rvChart) { try { _rvChart.remove(); } catch(e){} _rvChart = null; _rvSeries = null; _rvVolSeries = null; }

  const container = document.getElementById('rv-chart');
  container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:460px;color:var(--ink-3);"><span>加载中...</span></div>';

  const isIntraday = _rvPeriod === '1min';
  const url = isIntraday
    ? `/api/stock/kline/${_rvSymbol}?period=1min&count=240`
    : `/api/stock/kline/${_rvSymbol}?period=${_rvPeriod}&all_history=true`;

  let json;
  try {
    json = await (await fetch(url)).json();
  } catch(e) {
    container.innerHTML = '<div class="empty-state" style="padding:40px;">K线加载失败</div>';
    return;
  }
  if (!json.success || !json.data?.length) {
    container.innerHTML = '<div class="empty-state" style="padding:40px;">暂无K线数据</div>';
    return;
  }
  container.innerHTML = '';

  _rvChart = LightweightCharts.createChart(container, {
    width: container.clientWidth, height: 460,
    layout: { background: { color: '#ffffff' }, textColor: '#64748B', fontSize: 11 },
    grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: isIntraday },
  });

  if (isIntraday) {
    _rvSeries = _rvChart.addAreaSeries({
      lineColor: '#2563EB', topColor: 'rgba(37,99,235,0.2)', bottomColor: 'rgba(37,99,235,0)',
      lineWidth: 1.5,
    });
    _rvSeries.setData(json.data.map(k => ({ time: Math.floor(new Date(k.date).getTime()/1000), value: k.close })));
  } else {
    _rvSeries = _rvChart.addCandlestickSeries({
      upColor: '#ef4444', downColor: '#22c55e',
      borderUpColor: '#ef4444', borderDownColor: '#22c55e',
      wickUpColor: '#ef4444', wickDownColor: '#22c55e',
    });
    _rvSeries.setData(json.data.map(k => ({ time: k.date, open: k.open, high: k.high, low: k.low, close: k.close })));
    // 均线 MA5/10/20/60
    const candleData = json.data.map(k => ({ time: k.date, open: k.open, high: k.high, low: k.low, close: k.close }));
    [[5,'#f59e0b'],[10,'#3b82f6'],[20,'#a855f7'],[60,'#6b7280']].forEach(([p, c]) => {
      const s = _rvChart.addLineSeries({ color: c, lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
      s.setData(computeMA(candleData, p));
    });
  }

  // 成交量
  _rvVolSeries = _rvChart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
  _rvChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
  _rvVolSeries.setData(json.data.map(k => ({
    time: isIntraday ? Math.floor(new Date(k.date).getTime()/1000) : k.date,
    value: k.volume,
    color: k.close >= k.open ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.3)',
  })));

  _rvChart.timeScale().fitContent();
  _rvChart.timeScale().applyOptions({ rightOffset: 5, fixRightEdge: true });

  _rvResizeObserver = new ResizeObserver(() => {
    if (_rvChart) _rvChart.applyOptions({ width: container.clientWidth });
  });
  _rvResizeObserver.observe(container);
}

async function rvSwitchPeriod(period, btn) {
  document.querySelectorAll('#view-review .btn.active').forEach(b => {
    if (['rv-tab-timeshare','rv-tab-day','rv-tab-week','rv-tab-month'].includes(b.id)) b.classList.remove('active');
  });
  btn.classList.add('active');
  _rvPeriod = period;
  await rvRenderChart();
}

async function rvNext() {
  if (!_rvQueue.length) return;
  _rvIndex = (_rvIndex + 1) % _rvQueue.length;
  await rvLoadStock(_rvQueue[_rvIndex]);
}

async function rvPrev() {
  if (!_rvQueue.length) return;
  _rvIndex = (_rvIndex - 1 + _rvQueue.length) % _rvQueue.length;
  await rvLoadStock(_rvQueue[_rvIndex]);
}

function rvToggleSidebar() {
  document.querySelector('.sidebar').classList.toggle('rv-collapsed');
  // 等 CSS transition 完成后触发 chart resize
  setTimeout(() => { if (_rvChart) _rvChart.applyOptions({ width: document.getElementById('rv-chart').clientWidth }); }, 50);
}

async function rvAddWatchlist() {
  if (!_rvSymbol) return;
  const res = await fetch('/api/watchlist/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol: _rvSymbol }),
  });
  const j = await res.json();
  if (j.success) {
    alert(_rvName + '（' + _rvSymbol + '）已加入自选股');
  } else {
    alert('加入失败: ' + (j.error || '未知错误'));
  }
}

async function rvSaveNote() {
  const inp = document.getElementById('rv-note-input');
  const note = inp.value.trim();
  if (!note || !_rvSymbol) return;
  const res = await fetch('/api/review/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol: _rvSymbol, name: _rvName, note }),
  });
  const j = await res.json();
  if (j.success) {
    inp.value = '';
    rvLoadNotes();
  } else {
    alert('保存失败: ' + (j.error || '未知错误'));
  }
}

async function rvLoadNotes() {
  if (!_rvSymbol) return;
  const json = await (await fetch('/api/review/notes?symbol=' + encodeURIComponent(_rvSymbol))).json();
  const list = document.getElementById('rv-notes-list');
  if (!json.success || !json.data?.length) {
    list.innerHTML = '';
    return;
  }
  list.innerHTML = json.data.map(n => `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;padding:6px 8px;background:var(--surface-2,#f8fafc);border-radius:6px;font-size:13px;">
      <div>
        <span style="color:var(--ink-3);font-size:11px;">${n.created_at.slice(0,10)}</span>
        <span style="margin-left:8px;color:var(--ink);">${n.note.replace(/</g,'&lt;')}</span>
      </div>
      <button class="btn btn-ghost btn-sm" style="padding:2px 6px;font-size:11px;flex-shrink:0;" onclick="rvDeleteNote(${n.id})">删除</button>
    </div>
  `).join('');
}

async function rvDeleteNote(id) {
  await fetch('/api/review/notes/' + id, { method: 'DELETE' });
  rvLoadNotes();
}
```

---

## Task 7: 前端 JS — 键盘快捷键

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

在 `index.html` 中搜索已有的全局 `keydown` 事件监听器（搜索 `document.addEventListener('keydown'` 或 `window.addEventListener('keydown'`），在其中增加复盘快捷键分支；若无现有监听器则新建一个。

搜索以下模式（约在 `keydown` 相关区域，搜索 `ArrowLeft\|ArrowRight\|keydown` 找到现有键盘逻辑）：

```bash
grep -n "addEventListener.*keydown\|keydown.*addEventListener" wexin-read-mcp-main/src/templates/index.html
```

若已有 `document.addEventListener('keydown', ...)` 全局监听，在其内部合适位置追加：
```js
  // 复盘视图快捷键
  const activeView = document.querySelector('.view.active');
  if (activeView && activeView.id === 'view-review') {
    if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); rvNext(); return; }
    if (e.key === 'ArrowLeft') { e.preventDefault(); rvPrev(); return; }
    if (e.key === '1') { rvSwitchPeriod('1min', document.getElementById('rv-tab-timeshare')); return; }
    if (e.key === '2') { rvSwitchPeriod('day',  document.getElementById('rv-tab-day'));       return; }
    if (e.key === '3') { rvSwitchPeriod('week', document.getElementById('rv-tab-week'));      return; }
    if (e.key === '4') { rvSwitchPeriod('month',document.getElementById('rv-tab-month'));     return; }
    if (e.key === 'w' || e.key === 'W') { rvAddWatchlist(); return; }
    if (e.key === 'n' || e.key === 'N') { e.preventDefault(); document.getElementById('rv-note-input').focus(); return; }
  }
```

若无现有 `keydown` 监听器，新建：
```js
document.addEventListener('keydown', function(e) {
  // 有输入框聚焦时不触发导航快捷键（N 键除外，其本身就是聚焦输入框）
  const tag = document.activeElement?.tagName;
  if ((tag === 'INPUT' || tag === 'TEXTAREA') && e.key !== 'Escape') return;

  const activeView = document.querySelector('.view.active');
  if (activeView && activeView.id === 'view-review') {
    if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); rvNext(); return; }
    if (e.key === 'ArrowLeft') { e.preventDefault(); rvPrev(); return; }
    if (e.key === '1') { rvSwitchPeriod('1min', document.getElementById('rv-tab-timeshare')); return; }
    if (e.key === '2') { rvSwitchPeriod('day',  document.getElementById('rv-tab-day'));       return; }
    if (e.key === '3') { rvSwitchPeriod('week', document.getElementById('rv-tab-week'));      return; }
    if (e.key === '4') { rvSwitchPeriod('month',document.getElementById('rv-tab-month'));     return; }
    if (e.key === 'w' || e.key === 'W') { rvAddWatchlist(); return; }
    if (e.key === 'n' || e.key === 'N') { e.preventDefault(); document.getElementById('rv-note-input').focus(); return; }
  }
});
```

- [ ] **Step 2: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(frontend): 复盘视图 — 侧边栏入口 + 图表渲染 + 导航 + 备注 + 键盘快捷键"
```

---

## Task 8: 手动端到端验证

启动服务（在 `wexin-read-mcp-main/src/` 下）：
```bash
python app.py
```

- [ ] **验证 1**：打开浏览器访问 `http://localhost:8000`，侧边栏可见"复盘"入口

- [ ] **验证 2**：点击"复盘"，顶部显示股票名 + 进度（如 `平安银行（000001） 1 / 5522`），主图渲染日线 K 线、MA 线可见、量能柱可见

- [ ] **验证 3**：点击"下一只 →" 或按 `→` 键，切换到随机下一只股票

- [ ] **验证 4**：按 `1` 键切换到分时，图表变为折线图；按 `2` 切回日线，蜡烛图恢复

- [ ] **验证 5**：按 `3` 切换周线，按 `4` 切换月线，均正常渲染

- [ ] **验证 6**：按 `W` 键，弹出"已加入自选股"提示；前往自选股页面确认该股已添加

- [ ] **验证 7**：在备注框输入文字，按 `Enter`，备注出现在列表中；点击"删除"，备注消失

- [ ] **验证 8**：切换到其他页面再切回"复盘"，图表正常显示（无报错、无残留旧图表）

- [ ] **验证 9**：点击"⛶"放大按钮，侧边栏收起，图表宽度扩大

- [ ] **Final Commit**

```bash
git add -A
git commit -m "feat: 个股快速复盘模块 — 完整实现"
```
