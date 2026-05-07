# 灵活爬取模式 & 问财增强模块 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现三种爬取模式（最新1篇/N篇/时间段）+ 问财条件选股/板块扫描/机构调研模块 + 博主观点交叉验证预留钩子

**Architecture:** 后端新增 IWencaiService 与现有 StockService 并列；前端新增 `view-wencai` 视图独立承载选股/板块/调研三个子模块；爬取模式在 blogger.py 的参数层扩展，前端在任务启动区增加模式选择

**Tech Stack:** Python FastAPI + pywencai + AKShare + 现有 Playwright/httpx 爬虫栈；前端单页 vanilla JS + Lightweight Charts

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/iwencai_service.py` | 创建 | 问财查询/板块/调研全部后端逻辑 |
| `src/blogger.py` | 修改 | `fetch_recent_articles()` 增加 mode/period 参数 |
| `src/app.py` | 修改 | 新增 5 条问财路由；WebSocket 透传 mode 参数 |
| `src/templates/index.html` | 修改 | 新增条件选股视图 + 爬取模式选择 UI |

---

### Task 1: 后端 — 灵活爬取模式

**Files:**
- Modify: `src/blogger.py`
- Modify: `src/app.py`

- [ ] **Step 1: 修改 `fetch_recent_articles()` 签名和逻辑**

在 `src/blogger.py` 中修改 `fetch_recent_articles()` 方法签名，增加 `mode` 和 `period` 参数，并在返回结果前根据模式过滤文章：

```python
async def fetch_recent_articles(
    self, blogger: dict, count: int = 5, mode: str = "latest_n", period: str | None = None
) -> dict:
    """获取博主最新文章列表

    Args:
        blogger: 博主数据字典
        count: 文章数量（latest_n 模式使用）
        mode: 爬取模式 — "latest" | "latest_n" | "period"
        period: 时间段预设（period 模式使用）— "today" | "last_3_days" | "last_week" | "last_month"
    """
```

在方法末尾，三个数据源（mp_publish / mp_backend / getmsg）各自返回 `articles` 列表后，增加模式过滤逻辑。找到三个 return 点，在 `return` 前统一插入过滤：

在 `_fetch_via_mp_backend` 的调用点和 `_fetch_via_getmsg` 的调用点之后，所有成功返回 articles 的地方，增加：

```python
# 在 fetch_recent_articles() 方法内，所有 return mp_result / return api_result 之前
# 插入模式过滤。定位三个成功返回点，在每个 return 前调用:

def _filter_by_mode(articles, mode, count, period):
    from datetime import datetime, timedelta
    if mode == "latest":
        return articles[:1]
    elif mode == "latest_n":
        return articles[:count]
    elif mode == "period":
        now = datetime.now()
        delta_map = {
            "today": timedelta(days=0),
            "last_3_days": timedelta(days=3),
            "last_week": timedelta(days=7),
            "last_month": timedelta(days=30),
        }
        since = now - delta_map.get(period, timedelta(days=7))
        filtered = []
        for a in articles:
            date_str = a.get("date", "")
            if date_str:
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                    if d >= since:
                        filtered.append(a)
                except ValueError:
                    filtered.append(a)
            else:
                filtered.append(a)
        return filtered
    return articles[:count]
```

具体修改位置 — 在 `fetch_recent_articles()` 方法中找到这三处 return 并包装：

```python
# 位置1: mp_result 成功后 (~line 319)
if mp_result["success"] and mp_result["articles"]:
    mp_result["articles"] = _filter_by_mode(mp_result["articles"], mode, count, period)
    # ... 原有更新 blogger 的逻辑

# 位置2: api_result 成功后 (~line 337)  
if api_result["success"] and api_result["articles"]:
    api_result["articles"] = _filter_by_mode(api_result["articles"], mode, count, period)
    # ... 原有更新 blogger 的逻辑
```

同时更新 `_refresh_one_via_api()` 调用，保持 `count=1`（刷新始终取最新1篇）：

```python
async def _refresh_one_via_api(self, blogger: dict) -> dict:
    result = await self.fetch_recent_articles(blogger, count=1, mode="latest")
    return result
```

- [ ] **Step 2: 修改 `_resolve_blogger_urls()` 透传模式参数**

在 `src/app.py` 中找到 `_resolve_blogger_urls()` 函数签名（~line 671），增加 `mode` 和 `period` 参数：

```python
async def _resolve_blogger_urls(ws: WebSocket, blogger_ids: list[str], mode: str = "latest_n", count: int = 5, period: str | None = None) -> list[str]:
```

函数内部调用 `fetch_recent_articles` 处（~line 701），透传参数：

```python
result = await blogger_mgr.fetch_recent_articles(blogger, count=count, mode=mode, period=period)
```

- [ ] **Step 3: 修改 WebSocket 任务入口解析前端参数**

在 `src/app.py` 的 `websocket_task()` 函数中（~line 739），找到接收初始请求的代码：

```python
data = await ws.receive_json()
mode = data.get("mode", "urls")
```

在 `mode == "bloggers"` 分支中，提取新的爬取模式参数：

```python
if mode == "bloggers":
    blogger_ids = data.get("blogger_ids", [])
    extra_urls = data.get("extra_urls", [])
    scrape_mode = data.get("scrape_mode", "latest_n")
    scrape_count = data.get("scrape_count", 5)
    scrape_period = data.get("scrape_period", None)
    # ... 原有逻辑
    if blogger_ids:
        urls = await _resolve_blogger_urls(ws, blogger_ids, mode=scrape_mode, count=scrape_count, period=scrape_period)
```

- [ ] **Step 4: 验证后端改动**

启动服务并测试：

```bash
cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main && source ../.venv/bin/activate && python3 -c "
from blogger import BloggerManager
# 验证方法签名可正常导入
print('blogger.py 导入成功')
print('fetch_recent_articles 签名:', BloggerManager.fetch_recent_articles.__code__.co_varnames[:10])
"
```

---

### Task 2: 后端 — IWencaiService

**Files:**
- Create: `src/iwencai_service.py`

- [ ] **Step 1: 创建 `src/iwencai_service.py`**

```python
"""问财智能选股服务 — 条件筛选、板块扫描、机构调研"""

from __future__ import annotations

import asyncio
import logging
import math

import pywencai

from stock_utils import TTL_DAILY, TTL_REALTIME, cache

logger = logging.getLogger("iwencai-service")


def _clean(v):
    """将 NaN/NaT 转为 None。"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


class IWencaiService:
    """同花顺问财数据服务"""

    async def query(self, query: str, loop: bool = False, perpage: int = 50) -> dict:
        """自然语言条件选股

        Args:
            query: 中文选股条件，如 "市盈率小于20，净利润大于1亿"
            loop: 是否自动翻页获取全部结果
            perpage: 每页条数
        """
        cache_key = f"wencai:query:{query}:{perpage}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                pywencai.get, query=query, loop=loop, perpage=perpage
            )
            if df is None or df.empty:
                return {"success": True, "data": [], "total": 0}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records, "total": len(records)}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"问财查询失败: {e}")
            return {"success": False, "error": f"查询失败: {str(e)}"}

    async def get_sectors(self) -> dict:
        """获取概念板块热力图数据 — 涨跌幅 + 资金流向"""
        cache_key = "wencai:sectors"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            # 通过问财获取概念板块综合数据
            df = await asyncio.to_thread(
                pywencai.get,
                query="概念板块，按涨跌幅排序",
                perpage=100,
            )
            if df is None or df.empty:
                return {"success": True, "data": []}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"板块热力图获取失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_sector_stocks(self, sector_name: str, perpage: int = 100) -> dict:
        """获取某个概念/行业的成分股列表

        Args:
            sector_name: 概念名称，如 "人工智能"
        """
        cache_key = f"wencai:sector:{sector_name}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                pywencai.get,
                query=f"{sector_name}概念",
                perpage=perpage,
            )
            if df is None or df.empty:
                return {"success": True, "data": [], "total": 0}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records, "total": len(records)}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"概念成分股获取失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_stock_visits(self, symbol: str) -> dict:
        """获取某只股票的机构调研记录

        Args:
            symbol: 股票代码，如 "000001"
        """
        cache_key = f"wencai:visits:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                pywencai.get,
                query=f"{symbol} 机构调研",
                perpage=20,
            )
            if df is None or df.empty:
                return {"success": True, "data": []}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"机构调研查询失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_visits_search(self, query: str, perpage: int = 50) -> dict:
        """全市场扫描机构调研

        Args:
            query: 额外筛选条件，如 "近一月有机构调研，股价上涨"
        """
        cache_key = f"wencai:visits_search:{query}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            full_query = f"机构调研，{query}" if query else "近一月有机构调研"
            df = await asyncio.to_thread(
                pywencai.get,
                query=full_query,
                perpage=perpage,
            )
            if df is None or df.empty:
                return {"success": True, "data": [], "total": 0}

            records = []
            for _, row in df.iterrows():
                r = {}
                for col in df.columns:
                    r[col] = _clean(row[col])
                records.append(r)

            resp = {"success": True, "data": records, "total": len(records)}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"机构调研扫描失败: {e}")
            return {"success": False, "error": str(e)}

    # 预留钩子：后续博主观点交叉验证使用
    async def query_for_article(self, stock_names: list[str], concepts: list[str]) -> dict:
        """根据文章提取的股票名和概念关键词执行组合查询（预留）

        Args:
            stock_names: 文章提取的股票名称列表
            concepts: 文章提取的概念关键词列表
        """
        results = {}
        if stock_names:
            names_query = " 或 ".join(stock_names)
            results["stocks"] = await self.query(query=names_query, perpage=20)
        if concepts:
            for concept in concepts:
                results[concept] = await self.get_sector_stocks(concept, perpage=20)
        return {"success": True, "data": results}
```

- [ ] **Step 2: 验证 IWencaiService 可正常导入**

```bash
cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main && source ../.venv/bin/activate && python3 -c "
from iwencai_service import IWencaiService
s = IWencaiService()
print('IWencaiService 导入成功')
print('方法列表:', [m for m in dir(s) if not m.startswith('_')])
"
```

---

### Task 3: 后端 — 新增问财路由

**Files:**
- Modify: `src/app.py`

- [ ] **Step 1: 在 `app.py` 顶部导入 IWencaiService**

在 `src/app.py` 中，找到现有 import 区域（~line 15-21），在 `from stock_service import StockService` 之后添加：

```python
from iwencai_service import IWencaiService
```

在全局状态初始化区域（~line 30-33），`stock_service = StockService()` 之后添加：

```python
wencai_service = IWencaiService()
```

- [ ] **Step 2: 在股票查询 API 区域添加 5 条问财路由**

在 `src/app.py` 中，找到股票查询 API 区域末尾（`/api/agents/personas` 路由之前，约 ~line 226），新增以下路由块：

```python
# ---------- 问财选股API ----------

@app.post("/api/iwencai/query")
async def api_iwencai_query(req: dict):
    """条件选股 — 自然语言或结构化条件"""
    query = req.get("query", "")
    if not query or len(query.strip()) < 2:
        return {"success": False, "error": "请输入选股条件"}
    loop = req.get("loop", False)
    perpage = req.get("perpage", 50)
    return await wencai_service.query(query.strip(), loop=loop, perpage=perpage)


@app.get("/api/iwencai/sectors")
async def api_iwencai_sectors():
    """板块热力图数据"""
    return await wencai_service.get_sectors()


@app.get("/api/iwencai/sector/{name}")
async def api_iwencai_sector_stocks(name: str):
    """概念成分股"""
    return await wencai_service.get_sector_stocks(name)


@app.get("/api/iwencai/visits/{symbol}")
async def api_iwencai_stock_visits(symbol: str):
    """个股机构调研记录"""
    return await wencai_service.get_stock_visits(symbol)


@app.post("/api/iwencai/visits/search")
async def api_iwencai_visits_search(req: dict):
    """全市场机构调研扫描"""
    query = req.get("query", "")
    perpage = req.get("perpage", 50)
    return await wencai_service.get_visits_search(query, perpage=perpage)
```

- [ ] **Step 3: 验证路由**

```bash
cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main && source ../.venv/bin/activate && python3 -c "
from app import app
routes = [(r.path, r.methods) for r in app.routes if hasattr(r, 'path') and 'iwencai' in r.path]
print('问财路由:')
for path, methods in routes:
    print(f'  {methods} {path}')
"
```

---

### Task 4: 前端 — 爬取模式选择 UI

**Files:**
- Modify: `src/templates/index.html`

- [ ] **Step 1: 在博主模式面板中添加爬取模式选择**

在 `index.html` 中，找到 `id="mode-bloggers"` 的 div（~line 1076），在"补充文章链接"的表单组之前（~line 1087 之前），插入以下 HTML：

```html
<div class="form-group">
  <label class="form-label">爬取模式</label>
  <div style="display:flex;gap:8px;flex-wrap:wrap;">
    <label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;padding:6px 12px;border:1px solid var(--border);border-radius:var(--r-xs);"
           id="scrapeModeLatest" class="scrape-mode-opt">
      <input type="radio" name="scrapeMode" value="latest" onchange="onScrapeModeChange('latest')" style="accent-color:var(--blue);">
      最新一篇
    </label>
    <label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;padding:6px 12px;border:1px solid var(--border);border-radius:var(--r-xs);"
           id="scrapeModeLatestN" class="scrape-mode-opt" style="border-color:var(--blue);background:var(--blue-s);">
      <input type="radio" name="scrapeMode" value="latest_n" checked onchange="onScrapeModeChange('latest_n')" style="accent-color:var(--blue);">
      最新 <input type="number" id="scrapeCount" value="5" min="1" max="20"
             style="width:48px;padding:2px 4px;border:1px solid var(--border);border-radius:4px;font-size:13px;text-align:center;"
             onchange="onScrapeCountChange()" onclick="event.stopPropagation()"> 篇
    </label>
    <label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;padding:6px 12px;border:1px solid var(--border);border-radius:var(--r-xs);"
           id="scrapeModePeriod" class="scrape-mode-opt">
      <input type="radio" name="scrapeMode" value="period" onchange="onScrapeModeChange('period')" style="accent-color:var(--blue);">
      时间段
      <select id="scrapePeriod" style="padding:2px 6px;border:1px solid var(--border);border-radius:4px;font-size:13px;" onchange="onScrapePeriodChange()" onclick="event.stopPropagation()">
        <option value="today">今天</option>
        <option value="last_3_days">最近3天</option>
        <option value="last_week" selected>最近一周</option>
        <option value="last_month">最近一月</option>
      </select>
    </label>
  </div>
</div>
```

- [ ] **Step 2: 添加 CSS 样式**

在 `index.html` 的 `<style>` 块中（~style 结尾前），追加：

```css
.scrape-mode-opt { transition: all var(--t) var(--ease); }
.scrape-mode-opt.active-mode { border-color: var(--blue) !important; background: var(--blue-s); }
```

- [ ] **Step 3: 添加 JS 函数**

在 `index.html` 的 `<script>` 块中，`switchMode` 函数附近（~line 1615），添加：

```javascript
let _scrapeMode = 'latest_n';
let _scrapeCount = 5;
let _scrapePeriod = 'last_week';

function onScrapeModeChange(mode) {
  _scrapeMode = mode;
  document.querySelectorAll('.scrape-mode-opt').forEach(el => {
    el.classList.toggle('active-mode', el.querySelector(`input[value="${mode}"]`) !== null);
  });
}

function onScrapeCountChange() {
  const v = parseInt(document.getElementById('scrapeCount').value) || 5;
  _scrapeCount = Math.max(1, Math.min(20, v));
  document.getElementById('scrapeCount').value = _scrapeCount;
}

function onScrapePeriodChange() {
  _scrapePeriod = document.getElementById('scrapePeriod').value;
}
```

- [ ] **Step 4: 修改 `startTask()` 函数传递爬取模式参数**

在 `index.html` 中找到 `startTask()` 函数（搜索 `function startTask`），在构造 WebSocket 发送数据的逻辑中，找到 `mode === 'bloggers'` 分支。在发送的数据对象中增加三个字段：

```javascript
// 在 startTask() 函数中，构造发送数据的 blogger_ids 分支添加:
scrape_mode: _scrapeMode,
scrape_count: _scrapeCount,
scrape_period: _scrapePeriod,
```

完整的 send 数据对象示例：

```javascript
const payload = {
  mode: 'bloggers',
  blogger_ids: selectedIds,
  extra_urls: extraUrls,
  scrape_mode: _scrapeMode,
  scrape_count: _scrapeCount,
  scrape_period: _scrapePeriod,
};
```

---

### Task 5: 前端 — 条件选股视图（基础框架 + 自然语言搜索 + 结果表格）

**Files:**
- Modify: `src/templates/index.html`

- [ ] **Step 1: 在侧边栏添加条件选股导航项**

在 `index.html` 侧边栏中，`<button class="nav-item" data-view="stock">` 之后（~line 996），添加：

```html
<button class="nav-item" data-view="wencai" onclick="switchView('wencai', this)">
  <svg viewBox="0 0 20 20" fill="none"><circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.6"/><path d="M14 14l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
  <span>条件选股</span>
</button>
```

- [ ] **Step 2: 在 `topbarMeta` 中添加映射**

在 `switchView` 函数上方的 `topbarMeta` 对象（~line 1575）中添加：

```javascript
wencai: { title: '条件选股', sub: '自然语言选股 · 板块扫描 · 机构调研' },
```

- [ ] **Step 3: 创建条件选股视图 HTML**

在 `</div><!-- /view-stock -->` 之后（~line 1409），`<!-- ==================== VIEW: CONFIG ==================== -->` 之前（~line 1413），插入完整的 wencai 视图：

```html
<!-- ==================== VIEW: WENCAI ==================== -->
<div id="view-wencai" class="view">

  <!-- 子导航 tabs -->
  <div style="display:flex;gap:4px;margin-bottom:20px;">
    <button class="btn btn-ghost btn-sm active" id="wencaiTabQuery" onclick="switchWencaiTab('query')">条件选股</button>
    <button class="btn btn-ghost btn-sm" id="wencaiTabSectors" onclick="switchWencaiTab('sectors')">板块扫描</button>
    <button class="btn btn-ghost btn-sm" id="wencaiTabVisits" onclick="switchWencaiTab('visits')">机构调研</button>
  </div>

  <!-- ===== Tab 1: 条件选股 ===== -->
  <div id="wencai-panel-query" class="wencai-panel active">

    <!-- 自然语言搜索 -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none"><circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.6"/><path d="M14 14l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
          自然语言选股
        </div>
      </div>
      <div class="card-body">
        <div style="display:flex;gap:8px;">
          <input id="wencaiQueryInput" class="input" placeholder="输入选股条件，如：市盈率小于20，净利润大于1亿，市值大于100亿"
                 style="flex:1;" onkeydown="if(event.key==='Enter')wencaiSearch()">
          <button class="btn btn-primary" onclick="wencaiSearch()">查询</button>
        </div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;font-size:11px;color:var(--ink-3);">
          <span>快捷:</span>
          <a href="#" onclick="wencaiQuick('今日涨停的股票');return false;" style="color:var(--blue);">今日涨停</a>
          <a href="#" onclick="wencaiQuick('MACD金叉，成交量放大');return false;" style="color:var(--blue);">MACD金叉</a>
          <a href="#" onclick="wencaiQuick('净资产收益率大于15%，股息率大于3%');return false;" style="color:var(--blue);">高ROE高股息</a>
          <a href="#" onclick="wencaiQuick('2024年营收增长大于30%');return false;" style="color:var(--blue);">高增长</a>
        </div>
      </div>
    </div>

    <!-- 结构化筛选器 -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none"><path d="M3 4h14M6 10h8M8 16h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
          结构化筛选
        </div>
      </div>
      <div class="card-body">
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;">
          <div>
            <label style="font-size:11px;color:var(--ink-3);display:block;margin-bottom:3px;">行业板块</label>
            <select id="wencaiIndustry" class="input" style="padding:6px 10px;">
              <option value="">不限</option>
              <option value="银行">银行</option>
              <option value="证券">证券</option>
              <option value="保险">保险</option>
              <option value="白酒">白酒</option>
              <option value="医药">医药</option>
              <option value="半导体">半导体</option>
              <option value="新能源车">新能源车</option>
              <option value="光伏">光伏</option>
              <option value="人工智能">人工智能</option>
              <option value="消费电子">消费电子</option>
            </select>
          </div>
          <div>
            <label style="font-size:11px;color:var(--ink-3);display:block;margin-bottom:3px;">市盈率上限</label>
            <input id="wencaiMaxPE" class="input" type="number" placeholder="如 20" style="padding:6px 10px;">
          </div>
          <div>
            <label style="font-size:11px;color:var(--ink-3);display:block;margin-bottom:3px;">市值下限(亿)</label>
            <input id="wencaiMinMC" class="input" type="number" placeholder="如 100" style="padding:6px 10px;">
          </div>
          <div>
            <label style="font-size:11px;color:var(--ink-3);display:block;margin-bottom:3px;">涨跌幅下限(%)</label>
            <input id="wencaiMinChange" class="input" type="number" placeholder="如 3" style="padding:6px 10px;">
          </div>
        </div>
        <div style="margin-top:12px;">
          <button class="btn btn-primary btn-sm" onclick="wencaiStructuredSearch()">应用筛选</button>
        </div>
      </div>
    </div>

    <!-- 结果表格 -->
    <div class="card" id="wencaiResultCard" style="display:none;">
      <div class="card-header">
        <div class="card-title" id="wencaiResultTitle">查询结果</div>
      </div>
      <div class="card-body" style="overflow-x:auto;padding:0;">
        <div id="wencaiResultBody" style="max-height:500px;overflow-y:auto;"></div>
      </div>
    </div>

  </div><!-- /panel-query -->

  <!-- ===== Tab 2: 板块扫描 ===== -->
  <div id="wencai-panel-sectors" class="wencai-panel" style="display:none;">

    <!-- 热力图 -->
    <div class="card" id="sectorHeatmapCard">
      <div class="card-header">
        <div class="card-title">板块热力图</div>
        <button class="btn btn-ghost btn-sm" onclick="loadSectorHeatmap()">刷新</button>
      </div>
      <div class="card-body" id="sectorHeatmapBody">
        <div class="empty-state">点击刷新加载板块热力图</div>
      </div>
    </div>

    <!-- 概念搜索 -->
    <div class="card" style="margin-top:16px;">
      <div class="card-header">
        <div class="card-title">概念成分股查询</div>
      </div>
      <div class="card-body">
        <div style="display:flex;gap:8px;">
          <input id="sectorNameInput" class="input" placeholder="输入概念名称，如 人工智能、新能源车" style="flex:1;"
                 onkeydown="if(event.key==='Enter')loadSectorStocks()">
          <button class="btn btn-primary" onclick="loadSectorStocks()">查询</button>
        </div>
      </div>
    </div>

    <!-- 成分股结果 -->
    <div class="card" id="sectorResultCard" style="display:none;">
      <div class="card-header">
        <div class="card-title" id="sectorResultTitle">成分股</div>
      </div>
      <div class="card-body" style="overflow-x:auto;padding:0;">
        <div id="sectorResultBody" style="max-height:500px;overflow-y:auto;"></div>
      </div>
    </div>

  </div><!-- /panel-sectors -->

  <!-- ===== Tab 3: 机构调研 ===== -->
  <div id="wencai-panel-visits" class="wencai-panel" style="display:none;">

    <!-- 全市场扫描 -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">机构调研扫描</div>
      </div>
      <div class="card-body">
        <div style="display:flex;gap:8px;">
          <input id="visitsQueryInput" class="input" placeholder="筛选条件，如：近一月有机构调研，股价上涨"
                 style="flex:1;" onkeydown="if(event.key==='Enter')wencaiVisitsSearch()">
          <button class="btn btn-primary" onclick="wencaiVisitsSearch()">扫描</button>
        </div>
      </div>
    </div>

    <!-- 结果 -->
    <div class="card" id="visitsResultCard" style="display:none;">
      <div class="card-body" style="overflow-x:auto;padding:0;">
        <div id="visitsResultBody" style="max-height:500px;overflow-y:auto;"></div>
      </div>
    </div>

  </div><!-- /panel-visits -->

</div><!-- /view-wencai -->
```

- [ ] **Step 4: 添加 wencai 视图的 CSS**

在 `<style>` 块末尾追加：

```css
.wencai-panel { display: none; }
.wencai-panel.active { display: block; animation: viewIn var(--t-md) var(--ease); }

/* 快捷链接 hover */
.wencai-quick-link { color: var(--blue); cursor: pointer; text-decoration: none; }
.wencai-quick-link:hover { text-decoration: underline; }

/* 热力图色块 */
.heatmap-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.heatmap-item {
  padding: 8px 12px; border-radius: var(--r-xs);
  font-size: 12px; font-weight: 500;
  cursor: pointer; transition: transform var(--t) var(--ease);
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  min-width: 80px;
}
.heatmap-item:hover { transform: scale(1.05); }
.heatmap-item .sector-name { font-size: 12px; }
.heatmap-item .sector-change { font-size: 14px; font-weight: 700; font-family: var(--mono); }

/* 结果表格通用 */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  padding: 10px 10px; text-align: left; color: var(--ink-3); font-weight: 600;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 2px solid var(--border); position: sticky; top: 0; background: var(--surface);
}
.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.data-table tr:hover td { background: var(--surface-2); }
.data-table .mono { font-family: var(--mono); }
.data-table .up { color: var(--red); }
.data-table .down { color: var(--green); }
```

- [ ] **Step 5: 添加 JS 函数**

在 `<script>` 块末尾（`</script>` 之前）添加所有 wencai 相关的 JS：

```javascript
/* ============================================================
   WENCAI
   ============================================================ */
let _wencaiTab = 'query';

function switchWencaiTab(tab) {
  _wencaiTab = tab;
  ['query', 'sectors', 'visits'].forEach(t => {
    document.getElementById('wencai-panel-' + t).style.display = (t === tab) ? '' : 'none';
    const btn = document.getElementById('wencaiTab' + t.charAt(0).toUpperCase() + t.slice(1));
    if (btn) btn.classList.toggle('active', t === tab);
  });
}

/* --- 自然语言搜索 --- */
async function wencaiSearch() {
  const query = document.getElementById('wencaiQueryInput').value.trim();
  if (!query) { showToast('请输入选股条件', 'warning'); return; }
  await _doWencaiQuery(query);
}

function wencaiQuick(q) {
  document.getElementById('wencaiQueryInput').value = q;
  wencaiSearch();
}

/* --- 结构化筛选 --- */
function wencaiStructuredSearch() {
  const parts = [];
  const industry = document.getElementById('wencaiIndustry').value;
  const maxPE = document.getElementById('wencaiMaxPE').value;
  const minMC = document.getElementById('wencaiMinMC').value;
  const minChange = document.getElementById('wencaiMinChange').value;
  if (industry) parts.push(industry + '行业');
  if (maxPE) parts.push('市盈率小于' + maxPE);
  if (minMC) parts.push('市值大于' + minMC + '亿');
  if (minChange) parts.push('涨跌幅大于' + minChange + '%');
  if (!parts.length) { showToast('请至少设置一个筛选条件', 'warning'); return; }
  _doWencaiQuery(parts.join('，'));
}

async function _doWencaiQuery(query) {
  const card = document.getElementById('wencaiResultCard');
  const body = document.getElementById('wencaiResultBody');
  const title = document.getElementById('wencaiResultTitle');
  card.style.display = '';
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 查询中...</div>';
  title.textContent = '查询: ' + query;
  try {
    const resp = await fetch('/api/iwencai/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query, perpage: 50 }),
    });
    const json = await resp.json();
    if (!json.success) { body.innerHTML = '<div class="empty-state">' + (json.error || '查询失败') + '</div>'; return; }
    if (!json.data?.length) { body.innerHTML = '<div class="empty-state">未找到匹配的股票</div>'; return; }
    title.textContent = '查询: ' + query + '（共 ' + json.total + ' 条）';
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">请求失败: ' + e.message + '</div>';
  }
}

function _renderWencaiTable(container, rows) {
  if (!rows.length) return;
  const columns = Object.keys(rows[0]);
  const displayCols = columns.filter(c => !['market_code', 'code'].includes(c));
  let html = '<table class="data-table"><thead><tr>';
  displayCols.forEach(c => { html += '<th>' + c + '</th>'; });
  html += '</tr></thead><tbody>';
  rows.forEach(row => {
    html += '<tr>';
    displayCols.forEach(c => {
      const v = row[c];
      let val = v != null ? String(v) : '-';
      let cls = '';
      if (typeof v === 'number') {
        if (c.includes('涨跌幅') || c.includes('增长率')) cls = v >= 0 ? 'up' : 'down';
      }
      if (c === '股票代码' && row['股票简称']) {
        val = '<span class="mono">' + val + '</span>';
      }
      html += '<td class="' + cls + '">' + val + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

/* --- 板块热力图 --- */
async function loadSectorHeatmap() {
  const body = document.getElementById('sectorHeatmapBody');
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  try {
    const resp = await fetch('/api/iwencai/sectors');
    const json = await resp.json();
    if (!json.success || !json.data?.length) {
      body.innerHTML = '<div class="empty-state">暂无数据</div>'; return;
    }
    _renderHeatmap(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
  }
}

function _renderHeatmap(container, sectors) {
  let html = '<div class="heatmap-grid">';
  sectors.forEach(s => {
    // 找到涨跌幅列
    const changeCol = Object.keys(s).find(k => k.includes('涨跌幅'));
    const change = changeCol ? parseFloat(s[changeCol]) : 0;
    const up = change >= 0;
    const intensity = Math.min(Math.abs(change) / 10, 1);
    const r = up ? Math.round(220 - intensity * 160) : 220;
    const g = up ? 220 : Math.round(220 - intensity * 160);
    const b = Math.round(220 - intensity * 80);
    const bgColor = `rgb(${r},${g},${b})`;
    const nameCol = Object.keys(s).find(k => k.includes('简称') || k.includes('名称') || k.includes('概念') || k.includes('板块')) || Object.keys(s)[1];
    const name = s[nameCol] || '-';
    html += '<div class="heatmap-item" style="background:' + bgColor + ';color:#18181B;"'
      + ' onclick="loadSectorStocks(\'' + name.replace(/'/g, "\\'") + '\')"'
      + ' title="点击查看 ' + name + ' 成分股">'
      + '<span class="sector-name">' + name + '</span>'
      + '<span class="sector-change ' + (up ? 'up' : 'down') + '">' + (up ? '+' : '') + (changeCol ? s[changeCol] : '') + '</span>'
      + '</div>';
  });
  html += '</div>';
  container.innerHTML = html;
}

/* --- 概念成分股 --- */
async function loadSectorStocks(name) {
  if (!name) {
    name = document.getElementById('sectorNameInput').value.trim();
  } else {
    document.getElementById('sectorNameInput').value = name;
  }
  if (!name) { showToast('请输入概念名称', 'warning'); return; }
  // 切换到板块 tab
  switchWencaiTab('sectors');
  const card = document.getElementById('sectorResultCard');
  const body = document.getElementById('sectorResultBody');
  const title = document.getElementById('sectorResultTitle');
  card.style.display = '';
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  title.textContent = '「' + name + '」成分股';
  try {
    const resp = await fetch('/api/iwencai/sector/' + encodeURIComponent(name));
    const json = await resp.json();
    if (!json.success) { body.innerHTML = '<div class="empty-state">' + (json.error || '查询失败') + '</div>'; return; }
    if (!json.data?.length) { body.innerHTML = '<div class="empty-state">未找到成分股</div>'; return; }
    title.textContent = '「' + name + '」成分股（共 ' + json.total + ' 条）';
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">请求失败: ' + e.message + '</div>';
  }
}

/* --- 机构调研扫描 --- */
async function wencaiVisitsSearch() {
  const query = document.getElementById('visitsQueryInput').value.trim();
  const card = document.getElementById('visitsResultCard');
  const body = document.getElementById('visitsResultBody');
  card.style.display = '';
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 扫描中...</div>';
  try {
    const resp = await fetch('/api/iwencai/visits/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query, perpage: 50 }),
    });
    const json = await resp.json();
    if (!json.success) { body.innerHTML = '<div class="empty-state">' + (json.error || '查询失败') + '</div>'; return; }
    if (!json.data?.length) { body.innerHTML = '<div class="empty-state">未找到符合条件的调研记录</div>'; return; }
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">请求失败: ' + e.message + '</div>';
  }
}
```

- [ ] **Step 5: 在个股详情区接入机构调研**

在 `selectStock()` 函数中（~line 2323），在已有的并行加载调用末尾添加：

```javascript
loadVisits();
```

在 `<script>` 中添加 `loadVisits` 函数：

```javascript
async function loadVisits() {
  if (!_stockCode) return;
  const el = document.getElementById('stockVisitsBody');
  if (!el) return;
  el.innerHTML = '<div class="empty-state">加载中...</div>';
  try {
    const resp = await fetch('/api/iwencai/visits/' + _stockCode);
    const json = await resp.json();
    if (!json.success || !json.data?.length) { el.innerHTML = '<div class="empty-state">暂无机构调研记录</div>'; return; }
    _renderWencaiTable(el, json.data);
  } catch (e) { el.innerHTML = '<div class="empty-state">加载失败</div>'; }
}
```

- [ ] **Step 6: 在个股详情区添加机构调研卡片 HTML**

在 `index.html` 的 `view-stock` 中，个股新闻卡片之后、`</div><!-- /stockDetailArea -->` 之前（~line 1407），添加：

```html
<!-- 机构调研 -->
<div class="card">
  <div class="card-header">
    <div class="card-header-left">
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="7" r="3.5" stroke="currentColor" stroke-width="1.4"/><path d="M3 18c0-3.5 3-6 7-6s7 2.5 7 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
      <span>机构调研</span>
    </div>
  </div>
  <div class="card-body" id="stockVisitsBody" style="max-height:400px;overflow-y:auto;">
    <div class="empty-state">选择股票后加载</div>
  </div>
</div>
```

---

### Task 6: 验证 & 集成测试

- [ ] **Step 1: 启动服务并执行冒烟测试**

```bash
cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main && source ../.venv/bin/activate

# 启动服务（后台）
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 &
sleep 3

# 测试爬取模式 API
curl -s http://localhost:8000/api/blogger/list | python3 -c "import sys,json; d=json.load(sys.stdin); print('博主数:', len(d.get('bloggers',[])))"

# 测试问财条件选股
curl -s -X POST http://localhost:8000/api/iwencai/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"市盈率小于20","perpage":5}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('条件选股:', d.get('success'), ', 条数:', d.get('total',0))
"

# 测试板块热力图
curl -s http://localhost:8000/api/iwencai/sectors | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('板块数据:', d.get('success'), ', 条数:', len(d.get('data',[])))
"

# 测试机构调研
curl -s http://localhost:8000/api/iwencai/visits/000001 | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('机构调研:', d.get('success'), ', 条数:', len(d.get('data',[])))
"

# 停止服务
kill %1 2>/dev/null
```

---

### Task 7: 博主观点头交叉验证 — 预留钩子

**Files:**
- Modify: `src/iwencai_service.py` (已含 `query_for_article` 方法)
- 不新增路由，不新增前端 UI

- [ ] **Step 1: 确认钩子数据结构**

`IWencaiService.query_for_article(stock_names, concepts)` 方法已在 Task 2 中实现。该方法接收：
- `stock_names`: 文章提取的股票名称列表（由文章分析流程产出）
- `concepts`: 文章提取的概念关键词列表

返回每只股票的行情数据和每个概念板块的成分股。

后续接入时，只需在文章分析完成后调用此方法，将返回数据组织为对比视图。本次不实现前端。
```

---

## Self-Review

**1. Spec coverage check:**
- ✅ 爬取三种模式 → Task 1 (后端) + Task 4 (前端)
- ✅ 条件选股（自然语言 + 结构化） → Task 2 + Task 3 + Task 5
- ✅ 板块热力图 + 概念成分股 → Task 2 + Task 5
- ✅ 机构调研（个股 + 全市场） → Task 2 + Task 3 + Task 5
- ✅ 预留钩子 → Task 7

**2. Placeholder scan:** 无 TBD/TODO，每步都有完整代码。

**3. Type consistency:** `IWencaiService` 方法签名与路由调用一致；前端 `_scrapeMode` / `_scrapeCount` / `_scrapePeriod` 变量名与后端 `scrape_mode` / `scrape_count` / `scrape_period` 键名对应；`switchWencaiTab` 的 tab ID 与 HTML 中的 panel ID 匹配。
