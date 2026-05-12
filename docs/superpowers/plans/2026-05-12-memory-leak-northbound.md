# 内存泄漏修复 + 北向资金/龙虎榜 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复前端 6 处 ResizeObserver 内存泄漏，新增北向资金实时/历史数据和龙虎榜功能。

**Architecture:** 任务一（内存泄漏）改动集中在 `index.html`，不涉及后端；任务二（北向/龙虎）后端追加 3 条缓存路由到 `market.py`，前端新增一个视图页面。

**Tech Stack:** JavaScript (vanilla, single-file SPA), FastAPI, AKShare, Lightweight Charts, SQLite cache

---

## File Map

| 文件 | 任务 | 操作 |
|------|------|------|
| `src/templates/index.html` | 任务一 | 修改：新增 4 个全局变量 + 6 处 observer 改造 + switchView 兜底清理 |
| `src/templates/index.html` | 任务二 | 修改：新增导航按钮 + 视图容器 + JS 逻辑 |
| `src/routers/market.py` | 任务二 | 修改：追加 3 条路由 |

---

## Task 1: 内存泄漏修复 — 全局变量声明

**Files:**
- Modify: `src/templates/index.html:3275` (在 `_klineResizeObserver` 之后)

- [ ] **Step 1: 在全局变量区新增 4 个 observer 变量**

在 `let _klineResizeObserver = null;`（L3275）之后插入：

```javascript
let _klineMacdObserver = null;
let _klineRsiObserver = null;
let _fundResizeObserver = null;
let _futuresResizeObserver = null;
```

- [ ] **Step 2: 验证无语法错误**

Run: `cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python -c "import app; print('OK')"`
Expected: `OK`（后端启动不依赖前端，但验证项目可加载）

- [ ] **Step 3: Commit**

```bash
git add src/templates/index.html
git commit -m "fix(frontend): 新增 ResizeObserver 全局变量声明"
```

---

## Task 2: 内存泄漏修复 — `_disposeKlineSubCharts()` 改造

**Files:**
- Modify: `src/templates/index.html:3413-3422`

- [ ] **Step 1: 修改 `_disposeKlineSubCharts()`**

将现有函数：

```javascript
function _disposeKlineSubCharts() {
  if (_klineMacdChart) { try { _klineMacdChart.remove(); } catch(e){} _klineMacdChart = null; }
  if (_klineRsiChart) { try { _klineRsiChart.remove(); } catch(e){} _klineRsiChart = null; }
```

替换为：

```javascript
function _disposeKlineSubCharts() {
  if (_klineMacdObserver) { _klineMacdObserver.disconnect(); _klineMacdObserver = null; }
  if (_klineRsiObserver) { _klineRsiObserver.disconnect(); _klineRsiObserver = null; }
  if (_klineMacdChart) { try { _klineMacdChart.remove(); } catch(e){} _klineMacdChart = null; }
  if (_klineRsiChart) { try { _klineRsiChart.remove(); } catch(e){} _klineRsiChart = null; }
```

注意保留原有的 `macdWrap`/`rsiWrap` DOM 清理代码不变。

- [ ] **Step 2: Commit**

```bash
git add src/templates/index.html
git commit -m "fix(frontend): _disposeKlineSubCharts 先 disconnect observer 再销毁图表"
```

---

## Task 3: 内存泄漏修复 — MACD/RSI observer 保存引用

**Files:**
- Modify: `src/templates/index.html:3526`（MACD）、`src/templates/index.html:3561`（RSI）

- [ ] **Step 1: 修改 MACD ResizeObserver（约 L3526）**

将：

```javascript
new ResizeObserver(() => { if (_klineMacdChart) _klineMacdChart.applyOptions({ width: macdEl.clientWidth }); }).observe(macdEl);
```

替换为：

```javascript
_klineMacdObserver = new ResizeObserver(() => { if (_klineMacdChart) _klineMacdChart.applyOptions({ width: macdEl.clientWidth }); });
_klineMacdObserver.observe(macdEl);
```

- [ ] **Step 2: 修改 RSI ResizeObserver（约 L3561）**

将：

```javascript
new ResizeObserver(() => { if (_klineRsiChart) _klineRsiChart.applyOptions({ width: rsiEl.clientWidth }); }).observe(rsiEl);
```

替换为：

```javascript
_klineRsiObserver = new ResizeObserver(() => { if (_klineRsiChart) _klineRsiChart.applyOptions({ width: rsiEl.clientWidth }); });
_klineRsiObserver.observe(rsiEl);
```

- [ ] **Step 3: Commit**

```bash
git add src/templates/index.html
git commit -m "fix(frontend): MACD/RSI ResizeObserver 保存引用以支持 disconnect"
```

---

## Task 4: 内存泄漏修复 — 基金图表 observer 修复

**Files:**
- Modify: `src/templates/index.html:4242`（selectFund）、`src/templates/index.html:4330`（switchFundKlinePeriod）

- [ ] **Step 1: 修改 `selectFund()` — 在函数开头（数据请求之前）添加 disconnect**

在 `selectFund()` 函数体开头（L4243 附近，`const listArea = ...` 之前）插入：

```javascript
if (_fundResizeObserver) { _fundResizeObserver.disconnect(); _fundResizeObserver = null; }
```

- [ ] **Step 2: 修改 `selectFund()` 中的 ResizeObserver 创建（约 L4303）**

将：

```javascript
new ResizeObserver(() => { if (_fundChart) _fundChart.applyOptions({ width: chartEl.clientWidth }); }).observe(chartEl);
```

替换为：

```javascript
_fundResizeObserver = new ResizeObserver(() => { if (_fundChart) _fundChart.applyOptions({ width: chartEl.clientWidth }); });
_fundResizeObserver.observe(chartEl);
```

- [ ] **Step 3: 修改 `switchFundKlinePeriod()` — 在函数开头添加 disconnect**

在 `switchFundKlinePeriod()` 函数体开头（L4331 附近，`document.querySelectorAll(...)` 之前）插入：

```javascript
if (_fundResizeObserver) { _fundResizeObserver.disconnect(); _fundResizeObserver = null; }
```

- [ ] **Step 4: 修改 `switchFundKlinePeriod()` 中的 ResizeObserver 创建（约 L4357）**

将：

```javascript
new ResizeObserver(() => { if (_fundChart) _fundChart.applyOptions({ width: chartEl.clientWidth }); }).observe(chartEl);
```

替换为：

```javascript
_fundResizeObserver = new ResizeObserver(() => { if (_fundChart) _fundChart.applyOptions({ width: chartEl.clientWidth }); });
_fundResizeObserver.observe(chartEl);
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/index.html
git commit -m "fix(frontend): 基金图表 ResizeObserver 泄漏修复 (selectFund + switchFundKlinePeriod)"
```

---

## Task 5: 内存泄漏修复 — 期货图表 observer 修复

**Files:**
- Modify: `src/templates/index.html:4504`（selectFutures）、`src/templates/index.html:4585`（switchFuturesKlinePeriod）

- [ ] **Step 1: 修改 `selectFutures()` — 函数开头添加 disconnect**

在 `selectFutures()` 函数体开头（L4505 附近）插入：

```javascript
if (_futuresResizeObserver) { _futuresResizeObserver.disconnect(); _futuresResizeObserver = null; }
```

- [ ] **Step 2: 修改 `selectFutures()` 中的 ResizeObserver（约 L4554）**

将：

```javascript
new ResizeObserver(() => { if (_futuresChart) _futuresChart.applyOptions({ width: chartEl.clientWidth }); }).observe(chartEl);
```

替换为：

```javascript
_futuresResizeObserver = new ResizeObserver(() => { if (_futuresChart) _futuresChart.applyOptions({ width: chartEl.clientWidth }); });
_futuresResizeObserver.observe(chartEl);
```

- [ ] **Step 3: 修改 `switchFuturesKlinePeriod()` — 函数开头添加 disconnect**

在 `switchFuturesKlinePeriod()` 函数体开头（L4586 附近）插入：

```javascript
if (_futuresResizeObserver) { _futuresResizeObserver.disconnect(); _futuresResizeObserver = null; }
```

- [ ] **Step 4: 修改 `switchFuturesKlinePeriod()` 中的 ResizeObserver（约 L4612）**

将：

```javascript
new ResizeObserver(() => { if (_futuresChart) _futuresChart.applyOptions({ width: chartEl.clientWidth }); }).observe(chartEl);
```

替换为：

```javascript
_futuresResizeObserver = new ResizeObserver(() => { if (_futuresChart) _futuresChart.applyOptions({ width: chartEl.clientWidth }); });
_futuresResizeObserver.observe(chartEl);
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/index.html
git commit -m "fix(frontend): 期货图表 ResizeObserver 泄漏修复 (selectFutures + switchFuturesKlinePeriod)"
```

---

## Task 6: 内存泄漏修复 — `switchView()` 统一兜底清理

**Files:**
- Modify: `src/templates/index.html:2307-2308`

- [ ] **Step 1: 在 `switchView()` 函数体最开头插入清理代码**

在 `function switchView(view, el) {` 之后、现有的博主登录检查之前，插入：

```javascript
function switchView(view, el) {
  // 离开旧视图前清理副作用
  if (_klineMacdObserver) { _klineMacdObserver.disconnect(); _klineMacdObserver = null; }
  if (_klineRsiObserver) { _klineRsiObserver.disconnect(); _klineRsiObserver = null; }
  if (_fundResizeObserver) { _fundResizeObserver.disconnect(); _fundResizeObserver = null; }
  if (_futuresResizeObserver) { _futuresResizeObserver.disconnect(); _futuresResizeObserver = null; }
  // 离开自选股视图时清理定时器
  if (_watchlistInterval && view !== 'watchlist') {
    clearInterval(_watchlistInterval);
    _watchlistInterval = null;
  }
  // 博主管理 & 文章收集 需要登录
  if ((view === 'task' || view === 'bloggers') && !_mpLoggedIn) {
```

- [ ] **Step 2: 验证 — 启动服务并手动切换视图**

Run: `cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python app.py`
手动操作：反复切换 股票→基金→期货→自选股→股票 10 次，DevTools Memory 面板确认 Detached 节点不持续增长。

- [ ] **Step 3: Commit**

```bash
git add src/templates/index.html
git commit -m "fix(frontend): switchView 顶部兜底清理所有 observer 和定时器"
```

---

## Task 7: 北向资金/龙虎榜 — 后端路由

**Files:**
- Modify: `src/routers/market.py`（追加到文件末尾）

- [ ] **Step 1: 添加 import**

在 `market.py` 顶部现有 import 之后追加：

```python
import akshare as ak
from stock_utils import cache_get, cache_set
```

- [ ] **Step 2: 添加 3 条路由**

在 `market.py` 末尾追加：

```python
# ---------- 北向资金 + 龙虎榜 ----------

@router.get("/api/market/north-flow")
async def get_north_flow():
    cached = cache_get("north-flow")
    if cached is not None:
        return {"success": True, "data": cached}
    try:
        df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
        data = df.tail(50).to_dict(orient="records")
        cache_set("north-flow", data, 300)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/market/north-history")
async def get_north_history():
    cached = cache_get("north-history")
    if cached is not None:
        return {"success": True, "data": cached}
    try:
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        data = df.tail(30).to_dict(orient="records")
        cache_set("north-history", data, 300)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/market/dragon-tiger")
async def get_dragon_tiger(date: str = ""):
    cache_key = f"dragon-tiger-{date}" if date else "dragon-tiger"
    cached = cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached, "date": date}
    try:
        kwargs = {"date": date} if date else {}
        df = ak.stock_lhb_detail_daily_sina(**kwargs)
        data = df.to_dict(orient="records") if df is not None and not df.empty else []
        cache_set(cache_key, data, 600)
        return {"success": True, "data": data, "date": date}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
```

- [ ] **Step 3: 验证 — 测试 3 条接口**

Run: `cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python app.py`

```bash
curl -s http://localhost:8000/api/market/north-flow | python -m json.tool | head -20
curl -s http://localhost:8000/api/market/north-history | python -m json.tool | head -20
curl -s http://localhost:8000/api/market/dragon-tiger | python -m json.tool | head -20
```

Expected: 每条返回 `{"success": true, "data": [...]}` 格式。

- [ ] **Step 4: Commit**

```bash
git add src/routers/market.py
git commit -m "feat(backend): 新增北向资金实时/历史 + 龙虎榜 3 条缓存路由"
```

---

## Task 8: 北向资金/龙虎榜 — 前端导航和视图容器

**Files:**
- Modify: `src/templates/index.html`

- [ ] **Step 1: 在侧边栏「期货」按钮之后、「自选股」按钮之前插入导航按钮**

在 L1143 `</button>`（期货导航结束）之后、L1144 自选股导航之前，插入：

```html
<button class="nav-item" data-view="northbound" onclick="switchView('northbound', this)">
  <svg viewBox="0 0 20 20" fill="none">
    <path d="M4 16l4-6 3 3 5-7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M14 6h2v2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
  <span>北向/龙虎</span>
</button>
```

- [ ] **Step 2: 在 `topbarMeta` 中新增条目**

在 `topbarMeta` 对象中（L2303 附近），`futures` 条目之后添加：

```javascript
northbound: { title: '北向/龙虎', sub: '北向资金实时净流入 · 历史趋势 · 龙虎榜' },
```

- [ ] **Step 3: 在 HTML 中添加视图容器**

找到其他 `view-xxx` div 的位置（在 `view-futures` 之后、`view-watchlist` 之前），插入：

```html
<div id="view-northbound" class="view">
  <div class="tab-bar" style="display:flex;gap:8px;margin-bottom:20px;">
    <button class="btn active" onclick="switchNorthboundTab('flow', this)">实时净流入</button>
    <button class="btn" onclick="switchNorthboundTab('history', this)">历史趋势</button>
    <button class="btn" onclick="switchNorthboundTab('dragon', this)">龙虎榜</button>
  </div>
  <div id="northbound-flow-panel">
    <div id="northbound-summary" style="font-size:28px;font-weight:700;margin-bottom:16px;"></div>
    <div id="northbound-flow-chart" style="height:300px;"></div>
  </div>
  <div id="northbound-history-panel" style="display:none">
    <div id="northbound-history-chart" style="height:360px;"></div>
  </div>
  <div id="northbound-dragon-panel" style="display:none">
    <div id="northbound-dragon-table"></div>
  </div>
</div>
```

- [ ] **Step 4: 在 `switchView()` 中添加 northbound 的触发逻辑**

在 `switchView()` 函数中找到其他 view 的加载触发条件（如 `if (view === 'futures') { loadFuturesBoards(); }`），在其后添加：

```javascript
if (view === 'northbound') { loadNorthboundPage(); }
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/index.html
git commit -m "feat(frontend): 新增北向/龙虎导航入口和视图容器"
```

---

## Task 9: 北向资金/龙虎榜 — 前端 JS 逻辑

**Files:**
- Modify: `src/templates/index.html`（在其他页面逻辑函数附近插入）

- [ ] **Step 1: 初始化 V2 命名空间（如果尚未存在）**

在 JS 全局区域（页面初始化代码附近）添加：

```javascript
window.StockPulse = window.StockPulse || {};
window.StockPulse.api = window.StockPulse.api || {};
window.StockPulse.pages = window.StockPulse.pages || {};
```

- [ ] **Step 2: 添加 API 封装**

```javascript
window.StockPulse.api.getNorthFlow = async function() {
  return fetch('/api/market/north-flow').then(r => r.json());
};
window.StockPulse.api.getNorthHistory = async function() {
  return fetch('/api/market/north-history').then(r => r.json());
};
window.StockPulse.api.getDragonTiger = async function(date) {
  return fetch('/api/market/dragon-tiger' + (date ? '?date=' + date : '')).then(r => r.json());
};
```

- [ ] **Step 3: 添加 tab 切换函数**

```javascript
let _northboundChart = null;

function switchNorthboundTab(tab, btn) {
  document.querySelectorAll('#view-northbound .btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('northbound-flow-panel').style.display = tab === 'flow' ? '' : 'none';
  document.getElementById('northbound-history-panel').style.display = tab === 'history' ? '' : 'none';
  document.getElementById('northbound-dragon-panel').style.display = tab === 'dragon' ? '' : 'none';
  if (tab === 'history') loadNorthHistory();
  if (tab === 'dragon') loadDragonTiger();
}
```

- [ ] **Step 4: 添加加载函数 — `loadNorthboundPage()`**

```javascript
async function loadNorthboundPage() {
  const summaryEl = document.getElementById('northbound-summary');
  const chartEl = document.getElementById('northbound-flow-chart');
  summaryEl.innerHTML = '<span class="spinner dark"></span> 加载中...';
  chartEl.innerHTML = '';
  try {
    const json = await window.StockPulse.api.getNorthFlow();
    if (!json.success || !json.data || !json.data.length) {
      summaryEl.textContent = '今日暂无北向资金数据';
      return;
    }
    const latest = json.data[json.data.length - 1];
    const totalFlow = latest['北向资金'] || 0;
    const isPositive = totalFlow >= 0;
    summaryEl.innerHTML = `<span style="color:${isPositive ? '#ef4444' : '#22c55e'}">${isPositive ? '+' : ''}${(totalFlow / 100000000).toFixed(2)} 亿</span> <span style="font-size:14px;color:var(--ink-3);">今日累计净流入</span>`;

    if (_northboundChart) { _northboundChart.remove(); _northboundChart = null; }
    _northboundChart = LightweightCharts.createChart(chartEl, {
      width: chartEl.clientWidth, height: 280,
      layout: { background: { color: '#ffffff' }, textColor: '#64748B', fontSize: 11 },
      grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });
    const series = _northboundChart.addLineSeries({ color: '#3b82f6', lineWidth: 2 });
    const chartData = json.data
      .filter(d => d['时间'] && d['北向资金'] != null)
      .map(d => ({ time: d['日期'] + ' ' + d['时间'], value: d['北向资金'] / 100000000 }));
    if (chartData.length) series.setData(chartData);
    _northboundChart.timeScale().fitContent();
  } catch (e) {
    summaryEl.textContent = '加载失败: ' + e.message;
  }
}
```

- [ ] **Step 5: 添加加载函数 — `loadNorthHistory()`**

```javascript
async function loadNorthHistory() {
  const chartEl = document.getElementById('northbound-history-chart');
  chartEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--ink-3);"><span class="spinner dark"></span></div>';
  try {
    const json = await window.StockPulse.api.getNorthHistory();
    if (!json.success || !json.data || !json.data.length) {
      chartEl.innerHTML = '<div class="empty-state">暂无历史数据</div>';
      return;
    }
    chartEl.innerHTML = '';
    if (_northboundChart) { _northboundChart.remove(); _northboundChart = null; }
    _northboundChart = LightweightCharts.createChart(chartEl, {
      width: chartEl.clientWidth, height: 340,
      layout: { background: { color: '#ffffff' }, textColor: '#64748B', fontSize: 11 },
      grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });
    const series = _northboundChart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      color: 'rgba(59,130,246,0.5)',
    });
    const chartData = json.data
      .filter(d => d['日期'] && d['当日成交净买额'] != null)
      .map(d => ({
        time: d['日期'],
        value: d['当日成交净买额'] / 100000000,
        color: d['当日成交净买额'] >= 0 ? 'rgba(239,68,68,0.6)' : 'rgba(34,197,94,0.6)',
      }));
    if (chartData.length) series.setData(chartData);
    _northboundChart.timeScale().fitContent();
  } catch (e) {
    chartEl.innerHTML = '<div class="empty-state">加载失败</div>';
  }
}
```

- [ ] **Step 6: 添加加载函数 — `loadDragonTiger()`**

```javascript
async function loadDragonTiger() {
  const el = document.getElementById('northbound-dragon-table');
  el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--ink-3);"><span class="spinner dark"></span></div>';
  try {
    const json = await window.StockPulse.api.getDragonTiger();
    if (!json.success || !json.data || !json.data.length) {
      el.innerHTML = '<div class="empty-state">今日暂无龙虎榜数据</div>';
      return;
    }
    const cols = Object.keys(json.data[0]);
    let h = '<table class="data-table"><thead><tr>';
    cols.forEach(c => { h += '<th>' + escapeHtml(c) + '</th>'; });
    h += '</tr></thead><tbody>';
    json.data.forEach(row => {
      h += '<tr>';
      cols.forEach(c => { h += '<td class="num">' + escapeHtml(row[c] != null ? String(row[c]) : '-') + '</td>'; });
      h += '</tr>';
    });
    h += '</tbody></table>';
    el.innerHTML = h;
  } catch (e) {
    el.innerHTML = '<div class="empty-state">加载失败</div>';
  }
}
```

- [ ] **Step 7: 注册到 V2 命名空间**

```javascript
window.StockPulse.pages.northbound = {
  load: loadNorthboundPage,
  switchTab: switchNorthboundTab,
};
```

- [ ] **Step 8: 验证 — 完整功能测试**

重启服务，依次验证：
1. 点击「北向/龙虎」导航，页面加载无报错
2. 「实时净流入」tab 显示数字 + 折线图
3. 切换到「历史趋势」tab，柱状图正常渲染
4. 切换到「龙虎榜」tab，表格正常（或显示空状态）
5. 反复切换其他视图再回来，功能正常

- [ ] **Step 9: Commit**

```bash
git add src/templates/index.html
git commit -m "feat(frontend): 北向资金实时/历史图表 + 龙虎榜表格完整实现"
```

---

## Self-Review

**1. Spec coverage:**
- [x] 6 处 ResizeObserver 泄漏 → Tasks 1-6 覆盖全部
- [x] switchView 兜底清理 + _watchlistInterval → Task 6 覆盖
- [x] 3 条后端路由 → Task 7 覆盖
- [x] 前端导航入口 → Task 8 覆盖
- [x] 前端页面逻辑（3 个 tab） → Task 9 覆盖
- [x] V2 命名空间 → Task 9 Step 1, 2, 7 覆盖
- [x] 空状态处理 → Task 9 Step 4, 5, 6 覆盖

**2. Placeholder scan:** 无 TBD/TODO/模糊描述。

**3. 类型一致性:** `_northboundChart` 在 Task 9 Step 3 声明，Step 4/5 使用，Step 5 中先检查后销毁再创建，模式一致。
