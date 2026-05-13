# 驾驶舱（行情总览仪表盘）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增驾驶舱页面，默认首页，一屏展示 A 股市场情绪 + 6 个核心指数分时图，5 秒自动刷新。

**Architecture:** 后端新增 `cockpit_service.py` 数据聚合层 + `routers/cockpit.py` 3 个 API 端点；前端在 `index.html` 中新增导航、视图容器、CSS 和 JS，用 LightweightCharts 渲染分时图，setInterval 驱动 5s 自动刷新。

**Tech Stack:** FastAPI, AKShare, Tencent 行情 API (qt.gtimg.cn), httpx, Lightweight Charts (TradingView), 现有 TTL 缓存体系

---

## 文件清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `wexin-read-mcp-main/src/cockpit_service.py` | **新建** | 情绪数据聚合 + 指数批量报价 |
| `wexin-read-mcp-main/src/routers/cockpit.py` | **新建** | 3 个 API 端点 |
| `wexin-read-mcp-main/src/app.py:93` | **修改** | 注册 cockpit 路由 |
| `wexin-read-mcp-main/src/templates/index.html:1134` | **修改** | 添加导航项 |
| `wexin-read-mcp-main/src/templates/index.html:2422` | **修改** | 添加 topbarMeta |
| `wexin-read-mcp-main/src/templates/index.html:1203` | **修改** | 添加 view-cockpit 容器 |
| `wexin-read-mcp-main/src/templates/index.html:~260` | **修改** | 添加 CSS |
| `wexin-read-mcp-main/src/templates/index.html:~2478` | **修改** | 添加 JS 函数 + switchView 钩子 |

---

## Task 1: 创建 cockpit_service.py — 后端数据聚合层

**文件:** Create `wexin-read-mcp-main/src/cockpit_service.py`

- [ ] **Step 1: 编写 cockpit_service.py**

```python
"""驾驶舱数据聚合服务。"""
import asyncio
import logging
import akshare as ak
from stock_utils import cache
from http_client import patch_requests, get_async_client

logger = logging.getLogger(__name__)

TTL_SENTIMENT = 15
TTL_INDICES = 5
TTL_TICK = 5

# 6 个核心指数的腾讯行情代码
INDICES = [
    {"code": "sh000001", "name": "上证指数",   "qt": "sh000001"},
    {"code": "sz399001", "name": "深证成指",   "qt": "sz399001"},
    {"code": "sz399006", "name": "创业板指",   "qt": "sz399006"},
    {"code": "sh000688", "name": "科创50",     "qt": "sh000688"},
    {"code": "sh000300", "name": "沪深300",    "qt": "sh000300"},
    {"code": "sh000852", "name": "中证1000",   "qt": "sh000852"},
]


async def get_sentiment() -> dict:
    """获取市场情绪聚合数据。"""
    cache_key = "cockpit:sentiment"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        # 并行获取涨跌家数和涨停池
        rise_fall_task = asyncio.to_thread(patch_requests, ak.stock_changes_em)
        zt_task = asyncio.to_thread(patch_requests, ak.stock_zt_pool_em)
        df_rf, df_zt = await asyncio.gather(
            asyncio.wait_for(rise_fall_task, timeout=10),
            asyncio.wait_for(zt_task, timeout=10),
            return_exceptions=True,
        )

        # 涨跌家数
        rise_fall = {"up": 0, "down": 0, "flat": 0, "ratio": 0}
        if not isinstance(df_rf, Exception) and df_rf is not None and len(df_rf) > 0:
            try:
                row = df_rf.iloc[0]
                up = int(row.get("上涨家数", 0) or 0)
                down = int(row.get("下跌家数", 0) or 0)
                flat = int(row.get("平盘家数", 0) or 0)
                ratio = round(up / max(down, 1), 2)
                rise_fall = {"up": up, "down": down, "flat": flat, "ratio": ratio}
            except Exception as e:
                logger.warning(f"解析涨跌家数失败: {e}")

        # 涨跌停
        limit = {"up_limit": 0, "down_limit": 0, "broken": 0, "consecutive_2": 0}
        if not isinstance(df_zt, Exception) and df_zt is not None:
            limit["up_limit"] = len(df_zt)

        # 成交量
        volume = {"total_yuan": 0, "yesterday_yuan": 0, "change_pct": 0}
        if not isinstance(df_rf, Exception) and df_rf is not None and len(df_rf) > 0:
            try:
                row = df_rf.iloc[0]
                vol = float(row.get("成交额", 0) or 0)
                volume["total_yuan"] = vol
            except Exception:
                pass

        # 资金流向
        flow = {"main_net": 0, "super_large_net": 0, "large_net": 0, "mid_net": 0, "small_net": 0}
        try:
            df_flow = await asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_market_fund_flow),
                timeout=10,
            )
            if df_flow is not None and len(df_flow) > 0:
                row = df_flow.iloc[0]
                flow["main_net"] = float(row.get("主力净流入-净额", 0) or 0)
        except Exception as e:
            logger.warning(f"获取资金流向失败: {e}")

        resp = {
            "success": True,
            "data": {
                "rise_fall": rise_fall,
                "limit": limit,
                "volume": volume,
                "flow": flow,
            }
        }
        cache.set(cache_key, resp, TTL_SENTIMENT)
        return resp

    except Exception as e:
        logger.error(f"获取市场情绪失败: {e}")
        return {"success": False, "error": f"获取市场情绪失败: {e}"}


async def get_indices_quotes() -> dict:
    """通过腾讯批量行情接口获取 6 个指数的实时报价。"""
    cache_key = "cockpit:indices"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        codes = ",".join(idx["qt"] for idx in INDICES)
        url = f"http://qt.gtimg.cn/q={codes}"
        r = await get_async_client().get(url, timeout=10)
        text = r.content.decode("gbk", errors="replace")
        lines = [l.strip() for l in text.strip().split(";") if l.strip()]

        results = []
        for i, line in enumerate(lines):
            if i >= len(INDICES):
                break
            idx_info = INDICES[i]
            start = line.find('"')
            end = line.rfind('"')
            if start == -1 or end <= start:
                continue
            fields = line[start + 1:end].split("~")
            if len(fields) < 48:
                continue

            def _f(j):
                try:
                    return float(fields[j])
                except (IndexError, ValueError):
                    return None

            results.append({
                "code": idx_info["code"],
                "name": idx_info["name"],
                "price": _f(3),
                "prev_close": _f(4),
                "change": _f(31),
                "change_pct": _f(32),
                "volume": _f(36),
                "amount": _f(37),
            })

        resp = {"success": True, "data": results}
        cache.set(cache_key, resp, TTL_INDICES)
        return resp

    except Exception as e:
        logger.error(f"获取指数报价失败: {e}")
        return {"success": False, "error": f"获取指数报价失败: {e}"}


# 腾讯行情代码 → AKShare 指数代码映射
_AK_CODE_MAP = {
    "sh000001": "000001",
    "sz399001": "399001",
    "sz399006": "399006",
    "sh000688": "000688",
    "sh000300": "000300",
    "sh000852": "000852",
}


async def get_tick_data(code: str) -> dict:
    """获取单个指数当日分时数据（1 分钟线）。"""
    cache_key = f"cockpit:tick:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    ak_code = _AK_CODE_MAP.get(code, code)

    try:
        # 并行获取分时数据和昨日收盘价（来自腾讯实时行情）
        qt_code = code
        for idx in INDICES:
            if idx["code"] == code:
                qt_code = idx["qt"]
                break

        async def _fetch_prev_close():
            try:
                url = f"http://qt.gtimg.cn/q={qt_code}"
                r = await get_async_client().get(url, timeout=5)
                text = r.content.decode("gbk", errors="replace")
                start = text.find('"')
                end = text.rfind('"')
                if start != -1 and end > start:
                    fields = text[start + 1:end].split("~")
                    if len(fields) > 4:
                        return float(fields[4])  # fields[4] = 昨收
            except Exception:
                pass
            return 0

        df_task = asyncio.to_thread(patch_requests, ak.index_zh_a_min, symbol=ak_code, period="1", adjust="")
        df, prev_close = await asyncio.gather(
            asyncio.wait_for(df_task, timeout=10),
            _fetch_prev_close(),
        )

        if df is None or df.empty:
            return {"success": False, "error": f"未获取到 {code} 的分时数据"}

        records = []
        for _, row in df.iterrows():
            try:
                time_val = row.iloc[0]
                if hasattr(time_val, "strftime"):
                    time_str = time_val.strftime("%H:%M")
                else:
                    time_str = str(time_val)[-5:]
                records.append({
                    "time": time_str,
                    "price": float(row.iloc[2]),
                    "volume": float(row.iloc[1]) if len(row) > 1 else 0,
                })
            except Exception:
                continue

        # 查找该指数名称
        name = code
        for idx in INDICES:
            if idx["code"] == code:
                name = idx["name"]
                break

        resp = {
            "success": True,
            "data": {
                "code": code,
                "name": name,
                "prev_close": prev_close,
                "data": records,
            }
        }
        cache.set(cache_key, resp, TTL_TICK)
        return resp

    except asyncio.TimeoutError:
        logger.error(f"获取分时数据超时: {code}")
        return {"success": False, "error": f"获取 {code} 分时数据超时"}
    except Exception as e:
        logger.error(f"获取分时数据失败 {code}: {e}")
        return {"success": False, "error": f"获取分时数据失败: {e}"}
```

- [ ] **Step 2: 验证导入无语法错误**

Run: `cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python -c "import cockpit_service; print('OK')"`
Expected: `OK`

---

## Task 2: 创建 routers/cockpit.py — API 端点

**文件:** Create `wexin-read-mcp-main/src/routers/cockpit.py`

- [ ] **Step 1: 编写 cockpit.py 路由**

```python
"""驾驶舱 API 路由。"""
from fastapi import APIRouter
from cockpit_service import get_sentiment, get_indices_quotes, get_tick_data

router = APIRouter(prefix="/api/cockpit", tags=["驾驶舱"])


@router.get("/sentiment")
async def cockpit_sentiment():
    """市场情绪聚合数据（涨跌家数、涨跌停、成交量、资金流向）。"""
    return await get_sentiment()


@router.get("/indices")
async def cockpit_indices():
    """6 个核心指数的实时报价快照。"""
    return await get_indices_quotes()


@router.get("/tick/{code}")
async def cockpit_tick(code: str):
    """单个指数的当日分时数据（1 分钟线）。"""
    return await get_tick_data(code)
```

- [ ] **Step 2: 验证导入无语法错误**

Run: `cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python -c "from routers.cockpit import router; print('OK')"`
Expected: `OK`

---

## Task 3: 注册路由到 app.py

**文件:** Modify `wexin-read-mcp-main/src/app.py:93-94`

- [ ] **Step 1: 在 app.py 中添加路由导入和注册**

在 line 94 之后（`app.include_router(analysis_router)` 之后）添加:

```python
from routers.cockpit import router as cockpit_router
app.include_router(cockpit_router)
```

- [ ] **Step 2: 验证服务启动无报错**

Run: `cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && timeout 5 python app.py 2>&1 | grep -E "ERROR|驾驶舱|Cockpit|cockpit"` (不报错即通过)
Expected: 无 ERROR 输出

---

## Task 4: 前端 — 添加导航项和 topbarMeta

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 添加 topbarMeta 条目**

在 line 2432（`analysis: { title: '技术分析', sub: ... }`）之后、`;` 之前添加:

```js
  cockpit:  { title: '驾驶舱', sub: '市场情绪 · 核心指数 · 分时行情总览' },
```

- [ ] **Step 2: 添加导航按钮**

在 line 1133（`<div class="nav-section-label">工作台</div>`）之后、line 1134（`<button class="nav-item active" data-view="task"...`）之前插入:

```html
      <button class="nav-item" data-view="cockpit" onclick="switchView('cockpit', this)">
        <svg viewBox="0 0 20 20" fill="none"><rect x="2" y="2" width="16" height="16" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M2 7h16M7 7v11M13 7v11" stroke="currentColor" stroke-width="1.7"/></svg>
        <span>驾驶舱</span>
      </button>
```

同时把原来 task 上的 `class="nav-item active"` 改为 `class="nav-item"`（因为驾驶舱将成为默认首页）。

---

## Task 5: 前端 — 添加 view-cockpit 容器 HTML

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html:1203`

在 line 1203（`<div class="content-wrap">` 之后）插入整个 view-cockpit div：

```html
      <!-- ==================== VIEW: COCKPIT ==================== -->
      <div id="view-cockpit" class="view active">
        <!-- 情绪面板 -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px;" id="cockpit-sentiment">
          <div class="card" style="min-height:120px;">
            <div class="card-header"><div class="card-header-left"><svg width="16" height="16" viewBox="0 0 20 20" fill="none"><path d="M3 10h14" stroke="currentColor" stroke-width="1.5"/><path d="M10 3v14" stroke="currentColor" stroke-width="1.5"/></svg><span>涨跌家数</span></div></div>
            <div class="card-body" id="ck-rise-fall">
              <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                <span style="color:#ef4444;font-size:22px;font-weight:700;" id="ck-up">--</span>
                <span style="color:#22c55e;font-size:22px;font-weight:700;" id="ck-down">--</span>
              </div>
              <div style="display:flex;justify-content:space-between;font-size:12px;color:#94a3b8;margin-bottom:6px;">
                <span>上涨</span><span>平盘 <span id="ck-flat">--</span></span><span>下跌</span>
              </div>
              <div style="height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden;display:flex;" id="ck-ratio-bar">
                <div style="background:#ef4444;height:100%;width:50%;" id="ck-ratio-up"></div>
                <div style="background:#94a3b8;height:100%;width:5%;" id="ck-ratio-flat"></div>
                <div style="background:#22c55e;height:100%;width:45%;" id="ck-ratio-down"></div>
              </div>
              <div style="text-align:center;font-size:13px;margin-top:6px;color:#64748b;">涨跌比 <span id="ck-ratio" style="font-weight:600;">--</span></div>
            </div>
          </div>
          <div class="card" style="min-height:120px;">
            <div class="card-header"><div class="card-header-left"><svg width="16" height="16" viewBox="0 0 20 20" fill="none"><path d="M10 2l2 6h6l-5 4 2 6-5-4-5 4 2-6-5-4h6z" stroke="currentColor" stroke-width="1.5"/></svg><span>涨跌停</span></div></div>
            <div class="card-body" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;text-align:center;">
              <div><div style="color:#ef4444;font-size:24px;font-weight:700;" id="ck-up-limit">--</div><div style="font-size:11px;color:#94a3b8;">涨停</div></div>
              <div><div style="color:#22c55e;font-size:24px;font-weight:700;" id="ck-down-limit">--</div><div style="font-size:11px;color:#94a3b8;">跌停</div></div>
              <div><div style="color:#f59e0b;font-size:24px;font-weight:700;" id="ck-broken">--</div><div style="font-size:11px;color:#94a3b8;">炸板</div></div>
              <div><div style="color:#8b5cf6;font-size:24px;font-weight:700;" id="ck-consec">--</div><div style="font-size:11px;color:#94a3b8;">连板</div></div>
            </div>
          </div>
          <div class="card" style="min-height:120px;">
            <div class="card-header"><div class="card-header-left"><svg width="16" height="16" viewBox="0 0 20 20" fill="none"><path d="M4 16V8M10 16V4M16 16V10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg><span>成交量</span></div></div>
            <div class="card-body" style="text-align:center;">
              <div style="font-size:22px;font-weight:700;color:#1e293b;" id="ck-vol-total">--</div>
              <div style="font-size:11px;color:#94a3b8;margin-top:4px;">两市成交额</div>
              <div style="margin-top:10px;font-size:14px;" id="ck-vol-change">--</div>
            </div>
          </div>
          <div class="card" style="min-height:120px;">
            <div class="card-header"><div class="card-header-left"><svg width="16" height="16" viewBox="0 0 20 20" fill="none"><path d="M10 2v16M4 10h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="1.5"/></svg><span>资金流向</span></div></div>
            <div class="card-body" style="text-align:center;">
              <div style="font-size:20px;font-weight:700;" id="ck-flow-main">--</div>
              <div style="font-size:11px;color:#94a3b8;margin-top:4px;">主力净流入</div>
              <div style="margin-top:8px;display:flex;justify-content:space-around;font-size:11px;color:#64748b;">
                <span>超大单 <span id="ck-flow-super" style="font-weight:600;">--</span></span>
                <span>大单 <span id="ck-flow-large" style="font-weight:600;">--</span></span>
                <span>中单 <span id="ck-flow-mid" style="font-weight:600;">--</span></span>
                <span>小单 <span id="ck-flow-small" style="font-weight:600;">--</span></span>
              </div>
            </div>
          </div>
        </div>
        <!-- 指数分时图 -->
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;" id="cockpit-indices">
          <div class="card" id="ck-card-sh000001">
            <div class="card-header"><div class="card-header-left"><span class="ck-idx-name">上证指数</span></div><div class="ck-idx-price" id="ck-price-sh000001">--</div></div>
            <div class="card-body" style="padding:0;height:160px;" id="ck-tick-sh000001"></div>
          </div>
          <div class="card" id="ck-card-sz399001">
            <div class="card-header"><div class="card-header-left"><span class="ck-idx-name">深证成指</span></div><div class="ck-idx-price" id="ck-price-sz399001">--</div></div>
            <div class="card-body" style="padding:0;height:160px;" id="ck-tick-sz399001"></div>
          </div>
          <div class="card" id="ck-card-sz399006">
            <div class="card-header"><div class="card-header-left"><span class="ck-idx-name">创业板指</span></div><div class="ck-idx-price" id="ck-price-sz399006">--</div></div>
            <div class="card-body" style="padding:0;height:160px;" id="ck-tick-sz399006"></div>
          </div>
          <div class="card" id="ck-card-sh000688">
            <div class="card-header"><div class="card-header-left"><span class="ck-idx-name">科创50</span></div><div class="ck-idx-price" id="ck-price-sh000688">--</div></div>
            <div class="card-body" style="padding:0;height:160px;" id="ck-tick-sh000688"></div>
          </div>
          <div class="card" id="ck-card-sh000300">
            <div class="card-header"><div class="card-header-left"><span class="ck-idx-name">沪深300</span></div><div class="ck-idx-price" id="ck-price-sh000300">--</div></div>
            <div class="card-body" style="padding:0;height:160px;" id="ck-tick-sh000300"></div>
          </div>
          <div class="card" id="ck-card-sh000852">
            <div class="card-header"><div class="card-header-left"><span class="ck-idx-name">中证1000</span></div><div class="ck-idx-price" id="ck-price-sh000852">--</div></div>
            <div class="card-body" style="padding:0;height:160px;" id="ck-tick-sh000852"></div>
          </div>
        </div>
      </div>
```

同时将原来的 `view-task` 上的 `class="view active"` 改为 `class="view"`（移除 active）。

---

## Task 6: 前端 — 添加 CSS 样式

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html` — 在 `<style>` 块中添加

- [ ] **Step 1: 在 CSS 块末尾（`</style>` 前）添加驾驶舱样式**

```css
/* 驾驶舱 */
.ck-idx-name { font-size: 13px; font-weight: 600; }
.ck-idx-price { font-size: 14px; font-weight: 700; }
#cockpit-sentiment .card { transition: none; }
#cockpit-sentiment .card .card-body { padding: 12px 16px; }
#cockpit-indices .card .card-header { justify-content: space-between; }
#cockpit-indices .card:hover { box-shadow: 0 2px 12px rgba(0,0,0,.08); }
.ck-loading { display: flex; align-items: center; justify-content: center; height: 100%; color: #94a3b8; font-size: 13px; }
@keyframes ck-shimmer { 0% { background-position: -200px 0; } 100% { background-position: calc(200px + 100%) 0; } }
.ck-shimmer { background: linear-gradient(90deg, #f1f5f9 25%, #e2e8f0 50%, #f1f5f9 75%); background-size: 200px 100%; animation: ck-shimmer 1.5s infinite; border-radius: 4px; }
```

---

## Task 7: 前端 — 添加 JS 函数

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html` — 在 `switchView` 函数之后添加

- [ ] **Step 1: 添加状态变量和格式化工具函数**

在 `switchView` 函数之前（约 line 2436）添加:

```js
let _cockpitInterval = null;
let _cockpitCharts = {};  // { code: chartInstance }
let _cockpitSeries = {};  // { code: areaSeries }

function _fmtYuan(n) {
  if (n == null || isNaN(n)) return '--';
  const abs = Math.abs(n);
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + '万亿';
  if (abs >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (abs >= 1e4) return (n / 1e4).toFixed(2) + '万';
  return n.toFixed(0);
}
function _fmtPct(n) {
  if (n == null || isNaN(n)) return '--';
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}
function _flowColor(n) {
  if (n > 0) return '#ef4444';
  if (n < 0) return '#22c55e';
  return '#64748b';
}
```

- [ ] **Step 2: 添加 fetchSentiment + 渲染逻辑**

```js
async function fetchSentiment() {
  try {
    const json = await (await fetch('/api/cockpit/sentiment')).json();
    if (!json.success || !json.data) return;
    const d = json.data;

    // 涨跌家数
    const rf = d.rise_fall;
    const total = rf.up + rf.down + rf.flat || 1;
    document.getElementById('ck-up').textContent = rf.up;
    document.getElementById('ck-down').textContent = rf.down;
    document.getElementById('ck-flat').textContent = rf.flat;
    document.getElementById('ck-ratio').textContent = rf.ratio.toFixed(2);
    document.getElementById('ck-ratio-up').style.width = (rf.up / total * 100) + '%';
    document.getElementById('ck-ratio-flat').style.width = (rf.flat / total * 100) + '%';
    document.getElementById('ck-ratio-down').style.width = (rf.down / total * 100) + '%';

    // 涨跌停
    const lm = d.limit;
    document.getElementById('ck-up-limit').textContent = lm.up_limit;
    document.getElementById('ck-down-limit').textContent = lm.down_limit;
    document.getElementById('ck-broken').textContent = lm.broken;
    document.getElementById('ck-consec').textContent = lm.consecutive_2;

    // 成交量
    const vol = d.volume;
    document.getElementById('ck-vol-total').textContent = _fmtYuan(vol.total_yuan);
    const volChangeEl = document.getElementById('ck-vol-change');
    if (vol.yesterday_yuan > 0) {
      const pct = ((vol.total_yuan - vol.yesterday_yuan) / vol.yesterday_yuan * 100).toFixed(1);
      const arrow = pct >= 0 ? '↑' : '↓';
      const color = pct >= 0 ? '#ef4444' : '#22c55e';
      volChangeEl.innerHTML = `<span style="color:${color}">${arrow} ${Math.abs(pct)}%</span> <span style="color:#94a3b8">较昨日</span>`;
    }

    // 资金流向
    const fl = d.flow;
    const mainEl = document.getElementById('ck-flow-main');
    mainEl.textContent = _fmtYuan(fl.main_net);
    mainEl.style.color = _flowColor(fl.main_net);
    document.getElementById('ck-flow-super').textContent = _fmtYuan(fl.super_large_net);
    document.getElementById('ck-flow-super').style.color = _flowColor(fl.super_large_net);
    document.getElementById('ck-flow-large').textContent = _fmtYuan(fl.large_net);
    document.getElementById('ck-flow-large').style.color = _flowColor(fl.large_net);
    document.getElementById('ck-flow-mid').textContent = _fmtYuan(fl.mid_net);
    document.getElementById('ck-flow-mid').style.color = _flowColor(fl.mid_net);
    document.getElementById('ck-flow-small').textContent = _fmtYuan(fl.small_net);
    document.getElementById('ck-flow-small').style.color = _flowColor(fl.small_net);
  } catch (e) {
    console.error('fetchSentiment error:', e);
  }
}
```

- [ ] **Step 3: 添加 fetchIndices + 渲染报价**

```js
async function fetchIndices() {
  try {
    const json = await (await fetch('/api/cockpit/indices')).json();
    if (!json.success || !json.data) return;
    for (const idx of json.data) {
      const priceEl = document.getElementById('ck-price-' + idx.code);
      if (!priceEl) continue;
      const pctText = _fmtPct(idx.change_pct);
      const color = (idx.change_pct >= 0) ? '#ef4444' : '#22c55e';
      priceEl.innerHTML = `<span style="color:${color}">${idx.price?.toFixed(2) ?? '--'}</span> <span style="font-size:12px;color:${color}">${pctText}</span>`;
      // 更新卡片头背景色微调
      const card = document.getElementById('ck-card-' + idx.code);
      if (card) {
        card.style.borderTop = `3px solid ${color}`;
      }
    }
  } catch (e) {
    console.error('fetchIndices error:', e);
  }
}
```

- [ ] **Step 4: 添加 fetchTick + drawTickChart（LightweightCharts 渲染分时图）**

```js
async function fetchTick(code) {
  try {
    const json = await (await fetch('/api/cockpit/tick/' + code)).json();
    if (!json.success || !json.data) {
      const container = document.getElementById('ck-tick-' + code);
      if (container) container.innerHTML = '<div class="ck-loading">加载失败</div>';
      return;
    }
    drawTickChart(code, json.data);
  } catch (e) {
    console.error('fetchTick error:', code, e);
  }
}

function drawTickChart(code, data) {
  const container = document.getElementById('ck-tick-' + code);
  if (!container) return;

  // 清除已有图表
  if (_cockpitCharts[code]) {
    try { _cockpitCharts[code].remove(); } catch(e) {}
    delete _cockpitCharts[code];
    delete _cockpitSeries[code];
  }

  if (!data.data || !data.data.length) {
    container.innerHTML = '<div class="ck-loading">暂无数据</div>';
    return;
  }

  container.innerHTML = '';
  const isUp = data.data.length > 0 && data.data[data.data.length - 1].price >= (data.prev_close || data.data[0].price);
  const lineColor = isUp ? '#ef4444' : '#22c55e';
  const areaTop = isUp ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
  const areaBottom = isUp ? 'rgba(239,68,68,0.02)' : 'rgba(34,197,94,0.02)';

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 150,
    layout: { background: { color: '#ffffff' }, textColor: '#94a3b8', fontSize: 10 },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    crosshair: { mode: 1, vertLine: { visible: false }, horzLine: { visible: false } },
    rightPriceScale: { visible: false },
    leftPriceScale: { visible: false },
    timeScale: { visible: false, borderVisible: false },
    handleScroll: false,
    handleScale: false,
  });

  const areaSeries = chart.addAreaSeries({
    lineColor: lineColor,
    topColor: areaTop,
    bottomColor: areaBottom,
    lineWidth: 1.5,
    priceLineVisible: false,
    lastValueVisible: false,
  });

  // 转换数据格式: LightweightCharts area series 需要 { time, value }
  const chartData = data.data.map((d, i) => ({ time: i, value: d.price }));
  areaSeries.setData(chartData);

  // 昨日收盘价水平线
  if (data.prev_close > 0) {
    areaSeries.createPriceLine({
      price: data.prev_close,
      color: '#94a3b8',
      lineWidth: 1,
      lineStyle: 2,  // dashed
      axisLabelVisible: false,
    });
  }

  _cockpitCharts[code] = chart;
  _cockpitSeries[code] = areaSeries;

  // ResizeObserver 保持宽度
  const ro = new ResizeObserver(() => {
    if (_cockpitCharts[code]) {
      _cockpitCharts[code].applyOptions({ width: container.clientWidth });
    }
  });
  ro.observe(container);
}
```

- [ ] **Step 5: 添加 initCockpit 和自动刷新**

```js
const _CK_CODES = ['sh000001', 'sz399001', 'sz399006', 'sh000688', 'sh000300', 'sh000852'];

async function initCockpit() {
  // 并行加载情绪 + 报价
  await Promise.all([fetchSentiment(), fetchIndices()]);
  // 逐个加载分时图（前 2 个优先，其余异步）
  for (let i = 0; i < _CK_CODES.length; i++) {
    if (i < 2) {
      await fetchTick(_CK_CODES[i]);
    } else {
      fetchTick(_CK_CODES[i]);
    }
  }
  // 启动 5s 自动刷新
  if (_cockpitInterval) clearInterval(_cockpitInterval);
  _cockpitInterval = setInterval(() => {
    const panel = document.getElementById('view-cockpit');
    if (!panel || !panel.classList.contains('active')) {
      clearInterval(_cockpitInterval);
      _cockpitInterval = null;
      return;
    }
    fetchSentiment();
    fetchIndices();
    _CK_CODES.forEach(code => fetchTick(code));
  }, 5000);
}

function cleanupCockpit() {
  if (_cockpitInterval) { clearInterval(_cockpitInterval); _cockpitInterval = null; }
  Object.keys(_cockpitCharts).forEach(code => {
    try { _cockpitCharts[code].remove(); } catch(e) {}
  });
  _cockpitCharts = {};
  _cockpitSeries = {};
}
```

---

## Task 8: 前端 — 接入 switchView 和清理逻辑

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 switchView 中添加 cockpit 的 view-specific loader**

在 line 2478（`if (view === 'analysis') { taInitView(); }`）之后添加:

```js
  if (view === 'cockpit') { initCockpit(); }
```

- [ ] **Step 2: 在 switchView 清理逻辑中添加 cockpit 清理**

在 line 2454（`_watchlistInterval = null; }`）之后、line 2456（博主管理登录检查）之前添加:

```js
  // 离开驾驶舱视图时清理定时器和图表
  cleanupCockpit();
```

- [ ] **Step 3: 更新 view-task 的 class（移除 active）**

将 line 1204 附近的 `<div id="view-task" class="view active">` 改为 `<div id="view-task" class="view">`。

---

## Task 9: 端到端验证

- [ ] **Step 1: 重启服务，检查无启动报错**

Run: `lsof -ti:8000 | xargs kill -9 2>/dev/null; sleep 1; cd /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/src && python app.py &`

- [ ] **Step 2: 测试 3 个 API 端点**

Run:
```bash
curl -s http://localhost:8000/api/cockpit/sentiment | python3 -m json.tool
curl -s http://localhost:8000/api/cockpit/indices | python3 -m json.tool
curl -s http://localhost:8000/api/cockpit/tick/sh000001 | python3 -m json.tool
```

Expected: 每个返回 `{"success": true, "data": {...}}` 格式的 JSON。

- [ ] **Step 3: 浏览器打开 http://localhost:8000 验证**

- 驾驶舱应为默认首页
- 情绪面板 4 张卡片应显示数据
- 6 个指数卡片应显示名称 + 价格 + 涨跌幅
- 分时图应渲染为面积图
- 等待 10 秒确认自动刷新工作

- [ ] **Step 4: 提交所有变更**

```bash
git add wexin-read-mcp-main/src/cockpit_service.py wexin-read-mcp-main/src/routers/cockpit.py wexin-read-mcp-main/src/app.py wexin-read-mcp-main/src/templates/index.html
git commit -m "feat: 驾驶舱页面 — 市场情绪面板 + 6指数分时图 + 5s自动刷新"
```
