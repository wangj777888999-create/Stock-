# 美股驾驶舱 Tab + 行业调研页面 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ① 在驾驶舱顶部新增「A股 / 美股」Tab 切换，美股面板展示 5 大指数实时报价与分时图及情绪数据，5 秒自动刷新；② 新增「行业调研」页面，AI 基于肖璟六步框架流式生成行研报告，支持历史存档；③ 创建 `industry-analyst` Claude Code skill 供命令行直接调用。

**Architecture:**
- **Feature 1**：`cockpit_service.py` 追加 3 个 US 函数；`routers/cockpit.py` 追加 3 个 `/api/cockpit/us/` 端点；`index.html` 将现有驾驶舱 HTML 包裹进 `#ck-panel-a` 并平行添加 `#ck-panel-us` + Tab 切换条，JS 层两套面板共用 5 秒定时器。
- **Feature 2**：新建 `industry_service.py`（system prompt + httpx stream）和 `routers/industry.py`（4 端点）；`database.py` 追加 `industry_reports` 表；`app.py` 注册路由；`index.html` 新增 nav、CSS、HTML、JS（fetch + ReadableStream + marked.js 渲染）。

**Tech Stack:** FastAPI, AKShare, 腾讯行情 API, LightweightCharts, httpx stream, marked.js CDN, SQLite, Vanilla JS

---

## 文件清单

| 文件 | 操作 | 功能 |
|------|------|------|
| `wexin-read-mcp-main/src/cockpit_service.py` | 修改 | 追加 US_INDICES + 3 个美股数据函数 |
| `wexin-read-mcp-main/src/routers/cockpit.py` | 修改 | 追加 3 个 /api/cockpit/us/ 端点 |
| `wexin-read-mcp-main/src/database.py` | 修改 | 新增 industry_reports 表 |
| `wexin-read-mcp-main/src/industry_service.py` | **新建** | 六步框架 system prompt + SSE 流式分析 + 报告 CRUD |
| `wexin-read-mcp-main/src/routers/industry.py` | **新建** | 4 个行业调研端点 |
| `wexin-read-mcp-main/src/app.py` | 修改 | 注册 industry 路由 |
| `wexin-read-mcp-main/src/templates/index.html` | 修改 | marked.js + Tab CSS/HTML/JS + 行业调研 CSS/HTML/JS |
| `~/.claude/plugins/industry-analyst/SKILL.md` | **新建** | Claude Code 命令行 skill |

---

# Feature 1: 美股驾驶舱 Tab

---

## Task 1: cockpit_service.py — 新增美股数据层

**文件:** Modify `wexin-read-mcp-main/src/cockpit_service.py`

- [ ] **Step 1: 在文件末尾（`get_tick_data` 函数后，第 344 行后）追加美股常量和三个函数**

```python
# ─── 美股指数列表 ───

US_INDICES = [
    {"code": "INX",  "name": "标普500",  "qt": "us.INX"},
    {"code": "IXIC", "name": "纳斯达克", "qt": "us.IXIC"},
    {"code": "DJI",  "name": "道琼斯",   "qt": "us.DJI"},
    {"code": "RUT",  "name": "罗素2000", "qt": "us.RUT"},
    {"code": "VIX",  "name": "VIX",      "qt": "us.VIX"},
]


async def get_us_sentiment() -> dict:
    """美股情绪：VIX水平 + 涨跌家数（AKShare stock_us_spot_em）。"""
    cache_key = "cockpit:us:sentiment"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async def _fetch_vix():
        url = "https://qt.gtimg.cn/q=us.VIX"
        r = await get_async_client().get(url, timeout=8)
        text = r.content.decode("gbk", errors="replace")
        start = text.find('"')
        end = text.rfind('"')
        if start == -1 or end <= start:
            return None
        fields = text[start + 1: end].split("~")
        try:
            return float(fields[3])
        except (IndexError, ValueError):
            return None

    async def _fetch_breadth():
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_us_spot_em),
                timeout=_AKSHARE_TIMEOUT,
            )
            if df is None or df.empty:
                return None
            up = int((df["涨跌幅"] > 0).sum())
            down = int((df["涨跌幅"] < 0).sum())
            flat = int((df["涨跌幅"] == 0).sum())
            return {"up": up, "down": down, "flat": flat}
        except Exception as e:
            logger.warning(f"美股涨跌家数获取失败: {e}")
            return None

    vix, breadth = await asyncio.gather(
        _fetch_vix(), _fetch_breadth(), return_exceptions=True
    )
    if isinstance(vix, Exception):
        vix = None
    if isinstance(breadth, Exception):
        breadth = None

    resp = {"success": True, "data": {"vix": vix, "breadth": breadth}}
    cache.set(cache_key, resp, 30)
    return resp


async def get_us_indices_quotes() -> dict:
    """批量获取 5 个美股指数实时报价（腾讯 API）。"""
    cache_key = "cockpit:us:indices"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        codes = ",".join(idx["qt"] for idx in US_INDICES)
        url = f"https://qt.gtimg.cn/q={codes}"
        r = await get_async_client().get(url, timeout=10)
        text = r.content.decode("gbk", errors="replace")

        def _tf(fields, idx):
            try:
                return float(fields[idx])
            except (IndexError, ValueError):
                return None

        data = []
        lines = [line.strip() for line in text.strip().split(";") if line.strip()]
        i = 0
        for line in lines:
            if i >= len(US_INDICES):
                break
            start = line.find('"')
            end = line.rfind('"')
            if start == -1 or end <= start:
                i += 1
                continue
            fields = line[start + 1: end].split("~")
            if len(fields) < 38:
                i += 1
                continue
            data.append({
                "code": US_INDICES[i]["code"],
                "name": US_INDICES[i]["name"],
                "price": _tf(fields, 3),
                "prev_close": _tf(fields, 4),
                "change": _tf(fields, 31),
                "change_pct": _tf(fields, 32),
            })
            i += 1

        resp = {"success": True, "data": data}
        cache.set(cache_key, resp, 5)
        return resp

    except Exception as e:
        logger.error(f"获取美股指数报价失败: {e}")
        return {"success": False, "error": f"获取美股指数报价失败: {e}"}


async def get_us_tick_data(code: str) -> dict:
    """获取美股指数分时数据（腾讯美股分钟 API）。"""
    cache_key = f"cockpit:us:tick:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    idx_info = next((idx for idx in US_INDICES if idx["code"] == code), None)
    if idx_info is None:
        return {"success": False, "error": f"未知美股指数代码: {code}"}

    qt_code = idx_info["qt"]

    try:
        async def _fetch_min():
            url = f"https://web.ifzq.gtimg.cn/appstock/app/usaminute/query?code={qt_code}"
            r = await get_async_client().get(url, timeout=10)
            raw = r.content.decode("gbk", errors="replace")
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return None
            payload = json.loads(match.group())
            if payload.get("code") != 0:
                return None
            data_node = (
                payload.get("data", {}).get(qt_code, {}).get("data", {}).get("data", [])
                or payload.get("data", {}).get(qt_code.replace(".", ""), {}).get("data", {}).get("data", [])
            )
            return data_node

        async def _fetch_prev_close():
            url = f"https://qt.gtimg.cn/q={qt_code}"
            r = await get_async_client().get(url, timeout=8)
            text = r.content.decode("gbk", errors="replace")
            start = text.find('"')
            end = text.rfind('"')
            if start == -1 or end <= start:
                return None
            fields = text[start + 1: end].split("~")
            try:
                return float(fields[4])
            except (IndexError, ValueError):
                return None

        min_raw, prev_close = await asyncio.gather(
            _fetch_min(), _fetch_prev_close(), return_exceptions=True
        )
        if isinstance(prev_close, Exception) or prev_close is None:
            prev_close = 0.0

        if isinstance(min_raw, Exception) or not min_raw:
            resp = {
                "success": True,
                "closed": True,
                "data": {"code": idx_info["code"], "name": idx_info["name"], "prev_close": prev_close, "data": []},
            }
            cache.set(cache_key, resp, 30)
            return resp

        tick_list = []
        prev_vol = 0
        for item in min_raw:
            parts = item.split()
            if len(parts) < 3:
                continue
            try:
                hhmm = parts[0]
                price = float(parts[1])
                cum_vol = float(parts[2])
                minute_vol = max(0, cum_vol - prev_vol)
                prev_vol = cum_vol
                tick_list.append({"time": f"{hhmm[:2]}:{hhmm[2:]}", "price": price, "volume": minute_vol})
            except (ValueError, IndexError):
                continue

        resp = {
            "success": True,
            "closed": False,
            "data": {"code": idx_info["code"], "name": idx_info["name"], "prev_close": prev_close, "data": tick_list},
        }
        cache.set(cache_key, resp, 5)
        return resp

    except asyncio.TimeoutError:
        return {"success": False, "error": "美股分时数据获取超时"}
    except Exception as e:
        logger.error(f"获取美股分时数据失败 {code}: {e}")
        return {"success": False, "error": f"获取美股分时数据失败: {e}"}
```

- [ ] **Step 2: 验证可导入**

```bash
cd wexin-read-mcp-main/src
python -c "import cockpit_service; print('US_INDICES:', [i['code'] for i in cockpit_service.US_INDICES])"
```

期望：`US_INDICES: ['INX', 'IXIC', 'DJI', 'RUT', 'VIX']`

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/cockpit_service.py
git commit -m "feat(cockpit): 新增美股服务层 — US_INDICES + get_us_sentiment/indices/tick"
```

---

## Task 2: routers/cockpit.py — 新增美股端点

**文件:** Modify `wexin-read-mcp-main/src/routers/cockpit.py`

- [ ] **Step 1: 在文件末尾（第 28 行 `return await cockpit_service.get_tick_data(code)` 后）追加**

```python

_US_CODE_RE = re.compile(r"^[A-Z]{1,6}$")


@router.get("/us/sentiment")
async def api_cockpit_us_sentiment():
    """获取美股市场情绪（VIX + 涨跌家数）。"""
    return await cockpit_service.get_us_sentiment()


@router.get("/us/indices")
async def api_cockpit_us_indices():
    """获取美股主要指数实时报价。"""
    return await cockpit_service.get_us_indices_quotes()


@router.get("/us/tick/{code}")
async def api_cockpit_us_tick(code: str):
    """获取指定美股指数分时数据。"""
    code = code.upper()
    if not _US_CODE_RE.match(code):
        raise HTTPException(status_code=400, detail=f"无效美股指数代码: {code}")
    return await cockpit_service.get_us_tick_data(code)
```

- [ ] **Step 2: 启动服务验证**

```bash
cd wexin-read-mcp-main/src && python app.py
```

访问以下地址，期望返回 JSON 且 `success: true`：
```
http://localhost:8000/api/cockpit/us/sentiment
http://localhost:8000/api/cockpit/us/indices
http://localhost:8000/api/cockpit/us/tick/INX
```

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/routers/cockpit.py
git commit -m "feat(cockpit): 新增美股 API 端点 /api/cockpit/us/{sentiment,indices,tick}"
```

---

## Task 3: index.html — 驾驶舱 Tab CSS + HTML + JS

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在驾驶舱 CSS 末尾（`.cockpit-indices { grid-template-columns: 1fr; }` 的 `@media` 结束括号后）追加 Tab CSS**

```css
/* ── 驾驶舱 Tab 切换 ── */
.ck-market-tabs { display: flex; gap: 4px; margin-bottom: 16px; background: var(--surface-2); border-radius: 8px; padding: 4px; width: fit-content; }
.ck-market-tab { padding: 6px 18px; font-size: 13px; font-weight: 500; border-radius: 6px; border: none; background: transparent; color: var(--ink-2); cursor: pointer; transition: background .15s, color .15s; }
.ck-market-tab.active { background: var(--surface); color: var(--ink); box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.ck-closed-tip { font-size: 12px; color: var(--ink-3); text-align: center; padding: 20px 0; }
```

- [ ] **Step 2: 将 `<div id="view-cockpit"...>` 到其结束 `</div>` 整体替换（约第 1251-1316 行）**

找到：
```html
      <!-- ==================== VIEW: COCKPIT ==================== -->
      <div id="view-cockpit" class="view active">

        <!-- 情绪面板 -->
        <div class="cockpit-sentiment">
```

将整个 `view-cockpit` div（含情绪、热门、指数网格）替换为：

```html
      <!-- ==================== VIEW: COCKPIT ==================== -->
      <div id="view-cockpit" class="view active">

        <!-- 市场切换 Tab -->
        <div class="ck-market-tabs">
          <button class="ck-market-tab active" data-market="a" onclick="switchCkMarket('a')">A股</button>
          <button class="ck-market-tab" data-market="us" onclick="switchCkMarket('us')">美股</button>
        </div>

        <!-- ── A股面板 ── -->
        <div id="ck-panel-a">
          <div class="cockpit-sentiment">
            <div class="card">
              <div class="card-header"><div class="card-title">涨跌家数</div></div>
              <div class="card-body">
                <div class="sentiment-label">上涨 / 下跌 / 平盘</div>
                <div class="sentiment-value">
                  <span class="text-red" id="ck-up">--</span> / <span class="text-green" id="ck-down">--</span> / <span id="ck-flat">--</span>
                </div>
                <div class="sentiment-sub">涨跌比 <span id="ck-ratio">--</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-header"><div class="card-title">涨跌停</div></div>
              <div class="card-body">
                <div class="sentiment-label">涨停 / 跌停</div>
                <div class="sentiment-value">
                  <span class="text-red" id="ck-up-limit">--</span> / <span class="text-green" id="ck-down-limit">--</span>
                </div>
                <div class="sentiment-sub">涨停池 <span id="ck-up-pool">--</span> · 炸板 <span id="ck-broken">--</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-header"><div class="card-title">成交量</div></div>
              <div class="card-body">
                <div class="sentiment-label">两市成交额</div>
                <div class="sentiment-value" id="ck-volume">--</div>
                <div class="sentiment-sub" id="ck-vol-sub"></div>
              </div>
            </div>
            <div class="card">
              <div class="card-header"><div class="card-title">资金流向</div></div>
              <div class="card-body">
                <div class="sentiment-label">主力净流入</div>
                <div class="sentiment-value" id="ck-main-net">--</div>
                <div class="sentiment-sub" id="ck-flow-sub"></div>
              </div>
            </div>
          </div>
          <div class="cockpit-hot-row">
            <div class="card">
              <div class="card-header"><div class="card-title">今日热门股 Top10</div></div>
              <div class="card-body" style="padding:0">
                <table class="hot-table"><thead><tr><th>#</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>成交额</th><th>主力净流入</th></tr></thead><tbody id="ck-hot-body"></tbody></table>
              </div>
            </div>
            <div class="card">
              <div class="card-header"><div class="card-title">行业涨幅榜 Top10</div></div>
              <div class="card-body" style="padding:0">
                <table class="hot-table"><thead><tr><th>#</th><th>板块</th><th>涨跌幅</th><th>领涨股</th></tr></thead><tbody id="ck-industry-body"></tbody></table>
              </div>
            </div>
          </div>
          <div class="cockpit-indices" id="ck-indices-grid"></div>
        </div>

        <!-- ── 美股面板 ── -->
        <div id="ck-panel-us" style="display:none">
          <div class="cockpit-sentiment">
            <div class="card">
              <div class="card-header"><div class="card-title">VIX 恐慌指数</div></div>
              <div class="card-body">
                <div class="sentiment-label">当前水平</div>
                <div class="sentiment-value" id="us-vix">--</div>
                <div class="sentiment-sub" id="us-vix-level">--</div>
              </div>
            </div>
            <div class="card">
              <div class="card-header"><div class="card-title">涨跌家数</div></div>
              <div class="card-body">
                <div class="sentiment-label">上涨 / 下跌 / 平盘</div>
                <div class="sentiment-value">
                  <span class="text-red" id="us-up">--</span> / <span class="text-green" id="us-down">--</span> / <span id="us-flat">--</span>
                </div>
                <div class="sentiment-sub" id="us-breadth-sub"></div>
              </div>
            </div>
          </div>
          <div class="cockpit-indices" id="us-indices-grid"></div>
        </div>

      </div>
```

- [ ] **Step 3: 修改 JS 状态变量块（约第 2651 行）**

找到：
```javascript
/* ===== 驾驶舱 ===== */
let _cockpitInterval = null;
let _cockpitCharts = {};
let _cockpitSeries = {};
let _cockpitObservers = {};
```

替换为：
```javascript
/* ===== 驾驶舱 ===== */
let _ckActiveMarket = 'a';
let _cockpitInterval = null;
let _cockpitCharts = {};
let _cockpitSeries = {};
let _cockpitObservers = {};
let _usCharts = {};
let _usSeries = {};
let _usObservers = {};
```

- [ ] **Step 4: 替换 `cleanupCockpit()` 并在其后追加美股面板函数**

找到：
```javascript
function cleanupCockpit() {
  if (_cockpitInterval) { clearInterval(_cockpitInterval); _cockpitInterval = null; }
  Object.keys(_cockpitObservers).forEach(code => {
    try { _cockpitObservers[code].disconnect(); } catch(e) {}
  });
  _cockpitObservers = {};
  Object.keys(_cockpitCharts).forEach(code => {
    try { _cockpitCharts[code].remove(); } catch(e) {}
  });
  _cockpitCharts = {};
  _cockpitSeries = {};
}
```

替换为：
```javascript
function cleanupCockpit() {
  if (_cockpitInterval) { clearInterval(_cockpitInterval); _cockpitInterval = null; }
  Object.keys(_cockpitObservers).forEach(code => {
    try { _cockpitObservers[code].disconnect(); } catch(e) {}
  });
  _cockpitObservers = {};
  Object.keys(_cockpitCharts).forEach(code => {
    try { _cockpitCharts[code].remove(); } catch(e) {}
  });
  _cockpitCharts = {};
  _cockpitSeries = {};
  Object.keys(_usObservers).forEach(code => {
    try { _usObservers[code].disconnect(); } catch(e) {}
  });
  _usObservers = {};
  Object.keys(_usCharts).forEach(code => {
    try { _usCharts[code].remove(); } catch(e) {}
  });
  _usCharts = {};
  _usSeries = {};
}

/* ── 美股面板函数 ── */

function switchCkMarket(market) {
  _ckActiveMarket = market;
  document.getElementById('ck-panel-a').style.display = market === 'a' ? '' : 'none';
  document.getElementById('ck-panel-us').style.display = market === 'us' ? '' : 'none';
  document.querySelectorAll('.ck-market-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.market === market)
  );
  if (market === 'us') { fetchUsSentiment(); fetchUsIndices(); }
}

async function fetchUsSentiment() {
  try {
    const r = await fetch('/api/cockpit/us/sentiment');
    const j = await r.json();
    if (!j.success || !j.data) return;
    const d = j.data;
    if (d.vix != null) {
      document.getElementById('us-vix').textContent = d.vix.toFixed(2);
      const lvl = d.vix < 15 ? '低恐慌' : d.vix < 25 ? '中等恐慌' : '高恐慌';
      const cls = d.vix < 15 ? 'text-green' : d.vix < 25 ? '' : 'text-red';
      const el = document.getElementById('us-vix-level');
      el.textContent = lvl; el.className = 'sentiment-sub ' + cls;
    }
    if (d.breadth) {
      document.getElementById('us-up').textContent = d.breadth.up ?? '--';
      document.getElementById('us-down').textContent = d.breadth.down ?? '--';
      document.getElementById('us-flat').textContent = d.breadth.flat ?? '--';
      const total = (d.breadth.up || 0) + (d.breadth.down || 0) + (d.breadth.flat || 0);
      document.getElementById('us-breadth-sub').textContent = total > 0 ? '涨跌比 ' + (d.breadth.up / total).toFixed(4) : '';
    }
  } catch(e) { console.error('fetchUsSentiment error', e); }
}

function _usBuildIndexCards(data) {
  const grid = document.getElementById('us-indices-grid');
  if (!grid) return;
  grid.innerHTML = '';
  data.forEach(item => {
    const code = item.code;
    const changePct = item.prev_close && item.price ? ((item.price - item.prev_close) / item.prev_close * 100) : null;
    const isUp = changePct == null ? true : changePct >= 0;
    const cls = isUp ? 'text-red' : 'text-green';
    const sign = isUp ? '+' : '';
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="card-header">
        <div class="ck-index-header">
          <span style="font-weight:600;color:#1e293b;">${item.name}</span>
          <span class="ck-index-price" id="us-price-${code}">${item.price != null ? item.price.toFixed(2) : '--'}</span>
          <span class="ck-index-change ${cls}" id="us-change-${code}">${changePct != null ? sign + changePct.toFixed(2) + '%' : '--'}</span>
        </div>
      </div>
      <div class="card-body"><div class="ck-chart-wrap" id="us-chart-${code}"></div></div>`;
    grid.appendChild(card);
  });
}

async function fetchUsIndices() {
  try {
    const r = await fetch('/api/cockpit/us/indices');
    const j = await r.json();
    if (!j.success || !j.data) return;
    const grid = document.getElementById('us-indices-grid');
    if (grid.children.length === 0) {
      _usBuildIndexCards(j.data);
    } else {
      j.data.forEach(item => {
        const priceEl = document.getElementById('us-price-' + item.code);
        const changeEl = document.getElementById('us-change-' + item.code);
        if (priceEl && item.price != null) priceEl.textContent = item.price.toFixed(2);
        if (changeEl && item.price != null && item.prev_close) {
          const pct = (item.price - item.prev_close) / item.prev_close * 100;
          const isUp = pct >= 0;
          changeEl.textContent = (isUp ? '+' : '') + pct.toFixed(2) + '%';
          changeEl.className = 'ck-index-change ' + (isUp ? 'text-red' : 'text-green');
        }
      });
    }
    j.data.forEach(item => fetchUsTick(item.code));
  } catch(e) { console.error('fetchUsIndices error', e); }
}

async function fetchUsTick(code) {
  try {
    const r = await fetch('/api/cockpit/us/tick/' + code);
    const j = await r.json();
    if (!j.success) return;
    const container = document.getElementById('us-chart-' + code);
    if (!container) return;
    if (j.closed || !j.data || j.data.data.length === 0) {
      container.innerHTML = '<div class="ck-closed-tip">已收盘 · 昨收 ' + (j.data?.prev_close ?? '--') + '</div>';
      return;
    }
    _drawUsTickChart(code, j.data.data, j.data.prev_close);
  } catch(e) { console.error('fetchUsTick error', code, e); }
}

function _drawUsTickChart(code, data, prevClose) {
  const container = document.getElementById('us-chart-' + code);
  if (!container || !data || data.length === 0) return;
  if (_usCharts[code]) { try { _usCharts[code].remove(); } catch(e) {} delete _usCharts[code]; delete _usSeries[code]; }
  const isUp = prevClose ? data[data.length - 1].price >= prevClose : true;
  const lineColor = isUp ? '#ef4444' : '#22c55e';
  const topColor = isUp ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
  const bottomColor = isUp ? 'rgba(239,68,68,0.02)' : 'rgba(34,197,94,0.02)';
  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth, height: 120,
    layout: { background: { color: '#ffffff' }, textColor: '#64748B', fontSize: 10 },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    rightPriceScale: { visible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
    crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
  });
  const series = chart.addAreaSeries({ lineColor, topColor, bottomColor, lineWidth: 1.5 });
  if (prevClose) series.createPriceLine({ price: prevClose, color: '#94a3b8', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
  const today = new Date().toISOString().slice(0, 10);
  const chartData = data.map(d => {
    if (d.price == null) return null;
    return { time: Math.floor(new Date(today + ' ' + d.time + ':00').getTime() / 1000), value: d.price };
  }).filter(Boolean);
  if (chartData.length > 0) series.setData(chartData);
  _usCharts[code] = chart; _usSeries[code] = series;
  const ro = new ResizeObserver(() => { if (_usCharts[code]) chart.applyOptions({ width: container.clientWidth }); });
  ro.observe(container); _usObservers[code] = ro;
}
```

- [ ] **Step 5: 替换 `initCockpit()` 函数**

找到：
```javascript
async function initCockpit() {
  await fetchIndices();
  await fetchSentiment();
  fetchHotStocks();
  fetchIndustryRank();
  // start auto-refresh
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
    fetchHotStocks();
    fetchIndustryRank();
  }, 5000);
}
```

替换为：
```javascript
async function initCockpit() {
  _ckActiveMarket = 'a';
  document.getElementById('ck-panel-a').style.display = '';
  document.getElementById('ck-panel-us').style.display = 'none';
  document.querySelectorAll('.ck-market-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.market === 'a')
  );
  await fetchIndices();
  await fetchSentiment();
  fetchHotStocks();
  fetchIndustryRank();
  if (_cockpitInterval) clearInterval(_cockpitInterval);
  _cockpitInterval = setInterval(() => {
    const panel = document.getElementById('view-cockpit');
    if (!panel || !panel.classList.contains('active')) {
      clearInterval(_cockpitInterval); _cockpitInterval = null; return;
    }
    if (_ckActiveMarket === 'a') {
      fetchSentiment(); fetchIndices(); fetchHotStocks(); fetchIndustryRank();
    } else {
      fetchUsSentiment(); fetchUsIndices();
    }
  }, 5000);
}
```

- [ ] **Step 6: 浏览器验证**

访问 `http://localhost:8000`，进入驾驶舱：
1. 默认显示 A股 Tab，情绪卡片和指数分时图正常
2. 点击「美股」Tab：VIX 卡片和 5 张指数卡片出现
3. 非交易时段：指数卡显示「已收盘」提示
4. 离开再回来：Tab 重置到 A股

- [ ] **Step 7: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(cockpit): 美股 Tab — CSS + HTML + JS（Tab 切换 + 5大指数 + VIX）"
```

---

# Feature 2: 行业调研页面

---

## Task 4: database.py — 新增 industry_reports 表

**文件:** Modify `wexin-read-mcp-main/src/database.py`

- [ ] **Step 1: 在 executescript 的 roles 表之后（约第 235 行 `"""` 结束前）插入**

找到：
```python
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
        """)
```

替换为：
```python
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

            CREATE TABLE IF NOT EXISTS industry_reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                industry    TEXT NOT NULL,
                purpose     TEXT DEFAULT 'investment',
                report_text TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );
        """)
```

- [ ] **Step 2: 验证**

```bash
cd wexin-read-mcp-main/src && python -c "
from database import init_db, get_db
init_db()
db = get_db()
rows = db.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='industry_reports'\").fetchall()
print('industry_reports exists:', len(rows) > 0)
"
```

期望：`industry_reports exists: True`

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/database.py
git commit -m "feat(db): 新增 industry_reports 表"
```

---

## Task 5: industry_service.py — 创建服务层

**文件:** Create `wexin-read-mcp-main/src/industry_service.py`

- [ ] **Step 1: 创建文件**

```python
"""行业调研服务 — 基于肖璟六步框架的 AI 流式分析 + 报告存档。"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from database import get_db
from http_client import get_async_client
from state import config

logger = logging.getLogger("industry-service")

_PURPOSE_LABELS = {
    "investment": "投资选股",
    "startup": "创业选赛道",
    "career": "择业找方向",
    "full": "完整行研报告",
}

_PURPOSE_FOCUS = {
    "investment": "重点输出：产业生命周期阶段 + 竞争格局 + 估值逻辑 + 景气度跟踪指标",
    "startup": "重点输出：可行性分析（商业模式/Unit Economics）+ 市场规模（TAM/SAM/SOM）+ 护城河构建路径",
    "career": "重点输出：产业生命周期阶段 + 行业前景与薪资水平 + 龙头公司格局",
    "full": "输出完整框架，所有六个维度全覆盖",
}

_SYSTEM_PROMPT = """你是一位专业行业分析师，严格运用肖璟《如何快速了解一个行业》的六步框架进行行业分析。

## 六步分析框架

### 第一步：定义行业边界
- 横向维度：明确行业层级（参考申万行业分类/GICS），一句话说明"本次分析的行业定义是……（含哪些环节/不含哪些）"
- 纵向维度：梳理产业链上下游（原材料→中间品→终端产品→渠道→用户），标注本次重点分析的环节

### 第二步：判断产业生命周期
用渗透率作为核心判断标准（而非时间或增速斜率）：
- 导入期（渗透率<10%）：产品/服务尚未被大众接受
- 成长期（渗透率10%-50%）：快速扩张，竞争格局未定
- 成熟期（渗透率>50%）：增长放缓，竞争加剧，格局趋稳
- 衰退期：替代品出现，需求萎缩
给出当前所处阶段及核心依据（渗透率数据或类比）

### 第三步：按阶段聚焦分析

**3A 可行性分析（导入期必做）**
- 需求：是真实需求还是伪需求？用户愿意付费吗？
- 供给/商业模式：能卖出去（获客成本/转化率/留存）？能赚到钱（毛利率/Unit Economics，LTV>3×CAC）？能规模复制？
- 结论：商业模式可行/有条件可行/不可行

**3B 规模性分析（成长期必做）**
- TAM（潜在市场）/ SAM（可服务市场）/ SOM（3-5年可获得市场）
- 测算：自上而下（总市场×渗透率）+ 自下而上（单价×用户数）
- 三情景预测：高/中/低

**3C 防守性分析（成熟期必做）**
- 护城河类型：成本优势/网络效应/无形资产（品牌/专利/牌照）/转换成本
- 宽度判断：宽（多种壁垒叠加）/窄（单一壁垒）/无（同质化竞争）

**3D 盈利性分析（成熟期必做）**
- 产能周期演化与竞争格局（供不应求→进入→产能激增→出清→寡头）
- 波特五力×议价能力分析
- 财务指标验证：毛利率（定价权）、应收/应付周转（占用上下游能力）、ROE

### 第四步：估值逻辑
按生命周期阶段匹配估值框架：
- 导入期：PS/EV-GMV（营收倍数）
- 成长期：PEG（增长调整后市盈率）
- 成熟期：PE/DCF（现金流折现）
- 衰退期：PB/清算价值
基础公式：市值=净利润×PE，倍数由赔率（基本面）×概率（确定性）决定

### 第五步：PEST 外部因素
分析当前正在成为催化剂或压制因素的外部变量：
- P（政策）：监管趋势、补贴/限制
- E（经济）：利率、汇率、消费能力
- S（社会）：人口结构、消费偏好、ESG
- T（技术）：替代技术威胁、降本增效机会

### 第六步：景气度跟踪指标
设计一套高频跟踪体系：
- 量：销量/出货量/订单
- 价：出厂价/原材料价格/终端价
- 利：毛利率/净利率趋势
- 库存：渠道库存周转天数
- 预期：PMI/行业景气调查
推荐数据来源（国家统计局/行业协会/上市公司财报/Wind等）

## 输出规范

报告结构：
1. 行业定义与范围
2. 产业生命周期判断（现处阶段及依据）
3. [按阶段]核心分析维度
4. 外部因素（PEST）
5. 估值参考
6. 景气度跟踪指标
7. 综合结论与风险提示（包含"若XX发生，结论需修正"的可证伪条件）

重要原则：
- 模糊的正确>精确的错误：市场规模给合理区间，不追求精确数字
- 结论要可证伪：附上修正条件
- 动态视角：说明分析时点，结论需定期更新"""


async def stream_analysis(industry: str, purpose: str) -> AsyncGenerator[str, None]:
    """调用 AI API（SSE 流式），逐 token yield。"""
    if not config.ai.api_key:
        yield "data: [ERROR] 未配置 AI API Key，请在系统配置中填写\n\n"
        return
    if not config.ai.base_url:
        yield "data: [ERROR] 未配置 AI Base URL\n\n"
        return

    purpose_label = _PURPOSE_LABELS.get(purpose, "投资选股")
    focus = _PURPOSE_FOCUS.get(purpose, _PURPOSE_FOCUS["investment"])
    user_prompt = f"""请对「{industry}」进行行业分析。

分析目的：{purpose_label}
{focus}

请严格按照六步框架逐步分析，输出结构化的 Markdown 报告。"""

    url = f"{config.ai.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {config.ai.api_key}", "Content-Type": "application/json"}
    payload = {
        "model": config.ai.model or "gpt-4o",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 8192,
        "stream": True,
    }

    try:
        client = get_async_client()
        async with client.stream("POST", url, headers=headers, json=payload, timeout=120.0) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:].strip()
                if chunk == "[DONE]":
                    yield "data: [DONE]\n\n"
                    return
                try:
                    data = json.loads(chunk)
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except Exception as e:
        logger.error(f"AI 流式调用失败: {e}")
        yield f"data: [ERROR] AI 调用失败: {e}\n\n"


def save_report(industry: str, purpose: str, report_text: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO industry_reports (industry, purpose, report_text) VALUES (?, ?, ?)",
        (industry, purpose, report_text),
    )
    db.commit()
    return cur.lastrowid


def list_reports(limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, industry, purpose, created_at FROM industry_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, industry, purpose, report_text, created_at FROM industry_reports WHERE id=?",
        (report_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_report(report_id: int) -> bool:
    db = get_db()
    deleted = db.execute("DELETE FROM industry_reports WHERE id=?", (report_id,)).rowcount
    db.commit()
    return deleted > 0
```

- [ ] **Step 2: 验证**

```bash
cd wexin-read-mcp-main/src && python -c "import industry_service; print('OK, prompt length:', len(industry_service._SYSTEM_PROMPT))"
```

期望：`OK, prompt length:` 后跟大于 1000 的数字

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/industry_service.py
git commit -m "feat(industry): 创建服务层 — 六步框架 system prompt + SSE 流式分析 + 报告 CRUD"
```

---

## Task 6: routers/industry.py — 创建路由层

**文件:** Create `wexin-read-mcp-main/src/routers/industry.py`

- [ ] **Step 1: 创建文件**

```python
"""行业调研路由 — 流式分析 + 报告 CRUD。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import industry_service

router = APIRouter(prefix="/api/industry", tags=["行业调研"])

_VALID_PURPOSES = {"investment", "startup", "career", "full"}


class AnalyzeRequest(BaseModel):
    industry: str
    purpose: str = "investment"


@router.post("/analyze")
async def api_industry_analyze(req: AnalyzeRequest):
    """流式行业分析（SSE）。"""
    if not req.industry.strip():
        raise HTTPException(status_code=400, detail="行业名称不能为空")
    if req.purpose not in _VALID_PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose 必须是 {_VALID_PURPOSES} 之一")
    return StreamingResponse(
        industry_service.stream_analysis(req.industry.strip(), req.purpose),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/reports")
async def api_industry_save(industry: str, purpose: str, report_text: str):
    """保存分析报告。"""
    if not industry.strip() or not report_text.strip():
        raise HTTPException(status_code=400, detail="行业名称和报告内容不能为空")
    report_id = industry_service.save_report(industry.strip(), purpose, report_text)
    return {"success": True, "id": report_id}


@router.get("/reports")
async def api_industry_list_reports():
    """获取历史报告列表（不含正文）。"""
    return {"success": True, "data": industry_service.list_reports()}


@router.get("/reports/{report_id}")
async def api_industry_get_report(report_id: int):
    """获取单条报告全文。"""
    report = industry_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"success": True, "data": report}


@router.delete("/reports/{report_id}")
async def api_industry_delete_report(report_id: int):
    """删除报告。"""
    ok = industry_service.delete_report(report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"success": True}
```

- [ ] **Step 2: 验证**

```bash
cd wexin-read-mcp-main/src && python -c "from routers.industry import router; print('routes:', [r.path for r in router.routes])"
```

期望输出包含 `/api/industry/analyze`、`/api/industry/reports`

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/routers/industry.py
git commit -m "feat(industry): 创建路由层 — 流式分析 POST + 报告 CRUD"
```

---

## Task 7: app.py — 注册路由

**文件:** Modify `wexin-read-mcp-main/src/app.py`

- [ ] **Step 1: 在 signal_router 注册之后（约第 100 行）追加**

找到：
```python
from routers.signal import router as signal_router
app.include_router(signal_router)
```

在其后追加：
```python
from routers.industry import router as industry_router
app.include_router(industry_router)
```

- [ ] **Step 2: 验证端点已注册**

```bash
cd wexin-read-mcp-main/src && python app.py
```

访问 `http://localhost:8000/docs`，搜索 `industry`，确认 5 个端点出现。

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/app.py
git commit -m "feat(industry): 在 app.py 注册行业调研路由"
```

---

## Task 8: index.html — 行业调研 CSS + nav + marked.js + HTML + JS

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 `<head>` 中 LightweightCharts script 之后添加 marked.js**

找到：
```html
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
```

在其后追加：
```html
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
```

- [ ] **Step 2: 在侧边栏「技术分析」按钮之前插入行业调研导航项**

找到：
```html
      <button class="nav-item" data-view="analysis" onclick="switchView('analysis', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M3 17V8l4-4 3 5 4-6 3 5v9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 14h16" stroke="currentColor" stroke-width="1.1" stroke-dasharray="2 2.5" stroke-linecap="round"/></svg>
        <span>技术分析</span>
      </button>
```

在其前插入：
```html
      <button class="nav-item" data-view="industry" onclick="switchView('industry', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M2 17V7l6-4 4 3 4-3v14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 17V11h4v6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
        <span>行业调研</span>
      </button>
```

- [ ] **Step 3: 在 topbarMeta 中（cockpit 条目后）追加 industry 条目**

找到：
```javascript
  cockpit: { title: '驾驶舱', sub: '市场情绪 · 核心指数 · 分时行情总览' },
```

在其后追加：
```javascript
  industry: { title: '行业调研', sub: 'AI 六步框架 · 流式分析 · 报告存档' },
```

- [ ] **Step 4: 在 `.ck-closed-tip` CSS 之后追加行业调研 CSS**

```css
/* ── 行业调研 ── */
.industry-layout { display: grid; grid-template-columns: 300px 1fr; gap: 16px; height: calc(100vh - 120px); }
.industry-left { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }
.industry-input-card { flex-shrink: 0; }
.industry-history { flex: 1; overflow-y: auto; min-height: 0; }
.industry-history-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 6px; cursor: pointer; transition: background .12s; font-size: 13px; }
.industry-history-item:hover { background: var(--surface-2); }
.industry-history-item .ind-name { flex: 1; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.industry-history-item .ind-date { font-size: 11px; color: var(--ink-3); }
.industry-right { display: flex; flex-direction: column; min-height: 0; }
.industry-output { flex: 1; overflow-y: auto; padding: 20px 24px; background: var(--surface); border-radius: 10px; border: 1px solid rgba(0,0,0,.06); }
.industry-output .ind-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: var(--ink-3); gap: 8px; }
.industry-output .ind-empty strong { font-size: 14px; color: var(--ink-2); }
.industry-output h1,.industry-output h2,.industry-output h3 { margin: 1em 0 .4em; }
.industry-output p { margin: .4em 0; line-height: 1.7; }
.industry-output ul,.industry-output ol { padding-left: 1.5em; margin: .4em 0; }
.industry-output table { border-collapse: collapse; width: 100%; margin: .8em 0; }
.industry-output th,.industry-output td { border: 1px solid rgba(0,0,0,.1); padding: 6px 10px; font-size: 13px; }
.industry-output th { background: var(--surface-2); font-weight: 600; }
.industry-output blockquote { border-left: 3px solid var(--blue); padding-left: 12px; color: var(--ink-2); margin: .6em 0; }
.industry-output-actions { display: flex; justify-content: flex-end; padding: 8px 0 0; }
.ind-purpose-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.ind-purpose-btn { padding: 4px 12px; border-radius: 20px; border: 1px solid rgba(0,0,0,.12); background: transparent; font-size: 12px; cursor: pointer; transition: background .12s, border-color .12s; }
.ind-purpose-btn.active { background: var(--blue); color: #fff; border-color: var(--blue); }
@media (max-width: 768px) { .industry-layout { grid-template-columns: 1fr; height: auto; } }
```

- [ ] **Step 5: 在 CONFIG 视图之前插入行业调研 HTML 视图**

找到：
```html
      <!-- ==================== VIEW: CONFIG ==================== -->
      <div id="view-config" class="view">
```

在其前插入：
```html
      <!-- ==================== VIEW: INDUSTRY ==================== -->
      <div id="view-industry" class="view">
        <div class="industry-layout">
          <div class="industry-left">
            <div class="card industry-input-card">
              <div class="card-header"><div class="card-title">行业调研</div></div>
              <div class="card-body" style="display:flex;flex-direction:column;gap:10px;">
                <input id="ind-input" class="input" placeholder="输入行业名称，如：新能源汽车、医疗器械…" style="font-size:13px;">
                <div style="font-size:12px;color:var(--ink-2);font-weight:500;">分析目的</div>
                <div class="ind-purpose-row">
                  <button class="ind-purpose-btn active" data-purpose="investment" onclick="indSelectPurpose(this)">投资选股</button>
                  <button class="ind-purpose-btn" data-purpose="startup" onclick="indSelectPurpose(this)">创业选赛道</button>
                  <button class="ind-purpose-btn" data-purpose="career" onclick="indSelectPurpose(this)">择业找方向</button>
                  <button class="ind-purpose-btn" data-purpose="full" onclick="indSelectPurpose(this)">完整行研</button>
                </div>
                <button id="ind-run-btn" class="btn btn-primary" onclick="indStartAnalysis()" style="font-size:13px;">开始分析</button>
              </div>
            </div>
            <div class="card industry-history" style="padding:0;">
              <div class="card-header" style="padding:10px 14px;"><div class="card-title" style="font-size:13px;">历史报告</div></div>
              <div id="ind-history-list" style="padding:4px 0;">
                <div style="padding:16px;font-size:12px;color:var(--ink-3);text-align:center;">暂无历史报告</div>
              </div>
            </div>
          </div>
          <div class="industry-right">
            <div class="industry-output" id="ind-output">
              <div class="ind-empty">
                <svg width="40" height="40" viewBox="0 0 20 20" fill="none"><path d="M2 17V7l6-4 4 3 4-3v14" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 17V11h4v6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
                <strong>输入行业名称，开始 AI 分析</strong>
                <span style="font-size:12px;">基于肖璟《如何快速了解一个行业》六步框架</span>
              </div>
            </div>
            <div class="industry-output-actions" id="ind-save-row" style="display:none;">
              <button class="btn btn-primary btn-sm" onclick="indSaveReport()">保存报告</button>
            </div>
          </div>
        </div>
      </div>

```

- [ ] **Step 6: 在 switchView 函数末尾添加 industry 初始化钩子**

找到：
```javascript
  if (view === 'cockpit') { initCockpit(); }
}
```

替换为：
```javascript
  if (view === 'cockpit') { initCockpit(); }
  if (view === 'industry') { indLoadHistory(); }
}
```

- [ ] **Step 7: 在 `</script>` 标签前追加行业调研 JS**

```javascript
/* ============================================================
   行业调研
   ============================================================ */
let _indPurpose = 'investment';
let _indCurrentText = '';
let _indCurrentIndustry = '';
let _indCurrentPurpose = 'investment';
let _indStreaming = false;

function indSelectPurpose(btn) {
  _indPurpose = btn.dataset.purpose;
  document.querySelectorAll('.ind-purpose-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

async function indStartAnalysis() {
  const industry = document.getElementById('ind-input').value.trim();
  if (!industry) { toast('请输入行业名称', 'amber'); return; }
  if (_indStreaming) return;
  _indStreaming = true;
  _indCurrentText = '';
  _indCurrentIndustry = industry;
  _indCurrentPurpose = _indPurpose;
  const btn = document.getElementById('ind-run-btn');
  btn.textContent = '分析中…'; btn.disabled = true;
  document.getElementById('ind-save-row').style.display = 'none';
  const output = document.getElementById('ind-output');
  output.innerHTML = '<div style="padding:12px;font-size:13px;color:var(--ink-2);">正在分析，请稍候…</div>';
  try {
    const resp = await fetch('/api/industry/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ industry, purpose: _indPurpose }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      output.innerHTML = `<div style="color:var(--red);padding:12px;font-size:13px;">请求失败: ${err.detail || resp.status}</div>`;
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') { document.getElementById('ind-save-row').style.display = ''; break; }
        if (payload.startsWith('[ERROR]')) { output.innerHTML += `<div style="color:var(--red);font-size:13px;padding:8px 0;">${payload}</div>`; break; }
        try {
          const token = JSON.parse(payload);
          _indCurrentText += token;
          output.innerHTML = marked.parse(_indCurrentText);
          output.scrollTop = output.scrollHeight;
        } catch(e) {}
      }
    }
  } catch(e) {
    output.innerHTML = `<div style="color:var(--red);padding:12px;font-size:13px;">连接失败: ${e.message}</div>`;
  } finally {
    _indStreaming = false; btn.textContent = '开始分析'; btn.disabled = false;
  }
}

async function indSaveReport() {
  if (!_indCurrentText) return;
  try {
    const params = new URLSearchParams({ industry: _indCurrentIndustry, purpose: _indCurrentPurpose, report_text: _indCurrentText });
    const r = await fetch('/api/industry/reports?' + params.toString(), { method: 'POST' });
    const j = await r.json();
    if (j.success) { toast('报告已保存', 'green'); document.getElementById('ind-save-row').style.display = 'none'; indLoadHistory(); }
  } catch(e) { toast('保存失败', 'red'); }
}

async function indLoadHistory() {
  try {
    const r = await fetch('/api/industry/reports');
    const j = await r.json();
    const list = document.getElementById('ind-history-list');
    if (!j.success || !j.data || j.data.length === 0) {
      list.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--ink-3);text-align:center;">暂无历史报告</div>';
      return;
    }
    list.innerHTML = j.data.map(item => `
      <div class="industry-history-item" onclick="indViewReport(${item.id})">
        <div class="ind-name">${item.industry}</div>
        <div class="ind-date">${item.created_at.slice(0, 10)}</div>
        <button class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 6px;" onclick="event.stopPropagation();indDeleteReport(${item.id})">删除</button>
      </div>`).join('');
  } catch(e) { console.error('indLoadHistory', e); }
}

async function indViewReport(id) {
  try {
    const r = await fetch('/api/industry/reports/' + id);
    const j = await r.json();
    if (!j.success || !j.data) return;
    _indCurrentText = j.data.report_text || '';
    _indCurrentIndustry = j.data.industry;
    _indCurrentPurpose = j.data.purpose;
    document.getElementById('ind-input').value = j.data.industry;
    document.getElementById('ind-output').innerHTML = marked.parse(_indCurrentText);
    document.getElementById('ind-save-row').style.display = 'none';
  } catch(e) { console.error('indViewReport', e); }
}

async function indDeleteReport(id) {
  if (!confirm('确认删除此报告？')) return;
  try {
    await fetch('/api/industry/reports/' + id, { method: 'DELETE' });
    toast('已删除', 'green');
    indLoadHistory();
  } catch(e) { toast('删除失败', 'red'); }
}
```

- [ ] **Step 8: 浏览器完整验证**

1. 进入「行业调研」页面，确认左栏输入区和右侧空态正常
2. 输入「新能源汽车」，选「投资选股」，点「开始分析」
3. 观察流式输出（需配置 AI API Key；未配置时显示 `[ERROR]` 提示属正常）
4. 完成后点「保存报告」，确认出现在历史列表
5. 点击历史条目，报告重新显示在右侧
6. 点「删除」，确认从列表消失

- [ ] **Step 9: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(industry): index.html — marked.js + nav + CSS + HTML + JS 全栈"
```

---

## Task 9: 创建 Claude Code Skill 文件

**文件:** Create `~/.claude/plugins/industry-analyst/SKILL.md`

- [ ] **Step 1: 创建目录和文件**

```bash
mkdir -p ~/.claude/plugins/industry-analyst
```

写入 `~/.claude/plugins/industry-analyst/SKILL.md`：

```markdown
---
name: industry-analyst
description: 行业分析师 — 基于肖璟《如何快速了解一个行业》的完整行研框架。当用户说「分析这个行业」「帮我做行研」「这个赛道怎么样」「这个行业值不值得投」「我想了解XX行业」「帮我做一个行业报告」时触发。适用于创业赛道评估、求职行业选择、投资标的筛选。
triggers:
  - 分析.*行业
  - 帮我做行研
  - 这个赛道怎么样
  - 值不值得投
  - 了解.*行业
  - 行业报告
---

# 行业分析师

基于肖璟《如何快速了解一个行业》的完整方法论，覆盖产业生命周期判断、商业模式可行性、市场规模测算、护城河分析、竞争格局与盈利性、估值逻辑、PEST 外部因素分析和景气度跟踪，输出结构化分析报告。

## Step 0：厘清用户意图

在开始分析前先问一个问题（如果用户已说明则跳过）：

> 「你分析这个行业，主要是为了：**投资选股**、**创业选赛道**、**择业找方向**，还是需要**完整行研报告**？」

## Step 1：定义行业边界

- **横向**：明确行业层级（申万行业分类 / GICS），说明含哪些环节/不含哪些
- **纵向**：梳理产业链（原材料→中间品→终端产品→渠道→用户），标注分析重点

输出：「本次分析的行业定义是……」

## Step 2：判断产业生命周期

以渗透率为核心判断标准：

| 阶段 | 渗透率 | 特征 |
|------|--------|------|
| 导入期 | <10% | 产品尚未被大众接受 |
| 成长期 | 10%-50% | 快速扩张，格局未定 |
| 成熟期 | >50% | 增速放缓，格局趋稳 |
| 衰退期 | 下滑 | 替代品出现 |

## Step 3：按阶段聚焦分析

**导入期 → 可行性（3A）**
- 需求：真实需求 vs 伪需求？用户愿意付费？
- 商业模式：能卖出去（获客成本/留存）？能赚钱（毛利率/LTV>3×CAC）？能复制？

**成长期 → 规模性（3B）**
- TAM / SAM / SOM 三口径测算
- Top-down（总市场×渗透率）+ Bottom-up（单价×用户数）
- 高/中/低三情景预测

**成熟期 → 防守性（3C）+ 盈利性（3D）**
- 护城河：成本优势 / 网络效应 / 无形资产 / 转换成本；宽/窄/无
- 产能周期与格局演化；波特五力×议价能力；毛利率/应收应付/ROE验证

## Step 4：估值逻辑

| 阶段 | 估值框架 |
|------|----------|
| 导入期 | PS / EV-GMV |
| 成长期 | PEG |
| 成熟期 | PE / DCF |
| 衰退期 | PB / 清算价值 |

## Step 5：PEST 外部因素

当前哪些因素是催化剂或压制因素：
- **P**（政策）：监管趋势、补贴/限制
- **E**（经济）：利率、汇率、消费能力
- **S**（社会）：人口结构、消费偏好
- **T**（技术）：替代技术威胁、降本增效

## Step 6：景气度跟踪指标

设计高频跟踪体系（量/价/利/库存/预期）+ 推荐数据来源

## 输出格式

根据分析目的调整侧重：
- **投资选股**：生命周期 + 竞争格局 + 估值 + 景气度
- **创业选赛道**：可行性 + 市场规模 + 护城河路径
- **择业找方向**：生命周期 + 行业前景 + 龙头公司
- **完整行研**：所有维度全覆盖

报告结构：行业定义 → 生命周期 → 核心分析 → PEST → 估值参考 → 景气度指标 → 综合结论与风险提示（含可证伪条件）
```

- [ ] **Step 2: 验证**

```bash
head -3 ~/.claude/plugins/industry-analyst/SKILL.md
```

期望：`name: industry-analyst` 等 frontmatter 字段

- [ ] **Step 3: Commit（skill 文件在仓库外，仅 commit 空占位）**

```bash
git commit -m "feat(industry): industry-analyst skill 文件已创建（~/.claude/plugins/，仓库外）" --allow-empty
```
