# 美股驾驶舱 Tab 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在驾驶舱页面顶部新增「A股 / 美股」Tab 切换，美股面板展示 5 大指数（标普500、纳斯达克、道琼斯、罗素2000、VIX）的实时报价与分时图，以及涨跌家数情绪卡片；数据源全部走腾讯行情 API + AKShare，5 秒自动刷新。

**Architecture:** 在 `cockpit_service.py` 追加 3 个 US 版函数（复用现有 `get_indices_quotes` 的腾讯 API 解析逻辑）；`routers/cockpit.py` 追加 3 个 `/api/cockpit/us/` 端点；前端 `index.html` 将现有驾驶舱 HTML 包裹进 `#ck-panel-a`，并平行添加 `#ck-panel-us` 和 Tab 切换条；JS 层两套面板各自独立刷新，共用 `_cockpitInterval`。

**Tech Stack:** FastAPI, AKShare (`stock_us_spot_em`), 腾讯行情 API (`qt.gtimg.cn`, `web.ifzq.gtimg.cn`), LightweightCharts (已引入), Vanilla JS

---

## 文件清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `wexin-read-mcp-main/src/cockpit_service.py` | 修改 | 追加 US_INDICES、get_us_sentiment()、get_us_indices_quotes()、get_us_tick_data() |
| `wexin-read-mcp-main/src/routers/cockpit.py` | 修改 | 追加 3 个 /api/cockpit/us/ 端点 |
| `wexin-read-mcp-main/src/templates/index.html` | 修改 | Tab CSS + US 面板 HTML + US JS 函数 + initCockpit/cleanupCockpit 更新 |

---

## Task 1: cockpit_service.py — 新增美股数据层

**文件:** Modify `wexin-read-mcp-main/src/cockpit_service.py`

- [ ] **Step 1: 在文件末尾（`get_tick_data` 函数后）追加美股常量和三个函数**

在 `cockpit_service.py` 末尾（第 344 行后）追加：

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

    # 并行：VIX 报价 + 美股涨跌家数
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

    resp = {
        "success": True,
        "data": {
            "vix": vix,
            "breadth": breadth,
        },
    }
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
            # 尝试两种路径（指数 vs 个股格式）
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
            # 非交易时段——返回空 tick，前端显示已收盘提示
            resp = {
                "success": True,
                "closed": True,
                "data": {
                    "code": idx_info["code"],
                    "name": idx_info["name"],
                    "prev_close": prev_close,
                    "data": [],
                },
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
                tick_list.append({
                    "time": f"{hhmm[:2]}:{hhmm[2:]}",
                    "price": price,
                    "volume": minute_vol,
                })
            except (ValueError, IndexError):
                continue

        resp = {
            "success": True,
            "closed": False,
            "data": {
                "code": idx_info["code"],
                "name": idx_info["name"],
                "prev_close": prev_close,
                "data": tick_list,
            },
        }
        cache.set(cache_key, resp, 5)
        return resp

    except asyncio.TimeoutError:
        return {"success": False, "error": "美股分时数据获取超时"}
    except Exception as e:
        logger.error(f"获取美股分时数据失败 {code}: {e}")
        return {"success": False, "error": f"获取美股分时数据失败: {e}"}
```

- [ ] **Step 2: 手动验证服务函数可导入**

```bash
cd wexin-read-mcp-main/src
python -c "import cockpit_service; print('US_INDICES:', [i['code'] for i in cockpit_service.US_INDICES])"
```

期望输出：`US_INDICES: ['INX', 'IXIC', 'DJI', 'RUT', 'VIX']`

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/cockpit_service.py
git commit -m "feat(cockpit): 新增美股服务层 — US_INDICES + get_us_sentiment/indices/tick"
```

---

## Task 2: routers/cockpit.py — 新增美股端点

**文件:** Modify `wexin-read-mcp-main/src/routers/cockpit.py`

- [ ] **Step 1: 在文件末尾追加 3 个 US 端点**

现有文件最后一行是 `return await cockpit_service.get_tick_data(code)`（第 28 行）。在其后追加：

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

- [ ] **Step 2: 启动服务验证端点可访问**

```bash
cd wexin-read-mcp-main/src && python app.py
```

在浏览器或 curl 访问：
```
http://localhost:8000/api/cockpit/us/sentiment
http://localhost:8000/api/cockpit/us/indices
http://localhost:8000/api/cockpit/us/tick/INX
```

期望：返回 JSON，`success: true`（即使数据为空也不报 500）

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/routers/cockpit.py
git commit -m "feat(cockpit): 新增美股 API 端点 /api/cockpit/us/{sentiment,indices,tick}"
```

---

## Task 3: index.html — Tab CSS + 美股面板 CSS

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在现有驾驶舱 CSS 块末尾（约第 1109 行）追加 Tab 和美股面板 CSS**

定位到以下内容（约第 1107-1108 行）：
```css
  .cockpit-sentiment { grid-template-columns: 1fr; }
  .cockpit-indices { grid-template-columns: 1fr; }
```

在这两行的 `}` 之后（约第 1109 行的 `@media` 结束括号后），找到下一个 `*/` 注释块或 CSS 规则，在驾驶舱相关 CSS 的末尾追加：

```css
/* ── 驾驶舱 Tab 切换 ── */
.ck-market-tabs { display: flex; gap: 4px; margin-bottom: 16px; background: var(--surface-2); border-radius: 8px; padding: 4px; width: fit-content; }
.ck-market-tab { padding: 6px 18px; font-size: 13px; font-weight: 500; border-radius: 6px; border: none; background: transparent; color: var(--ink-2); cursor: pointer; transition: background .15s, color .15s; }
.ck-market-tab.active { background: var(--surface); color: var(--ink); box-shadow: 0 1px 3px rgba(0,0,0,.08); }
/* 美股面板复用 .cockpit-sentiment / .cockpit-indices，无需额外样式 */
.ck-closed-tip { font-size: 12px; color: var(--ink-3); text-align: center; padding: 20px 0; }
```

- [ ] **Step 2: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(cockpit): 新增 Tab CSS"
```

---

## Task 4: index.html — 重构驾驶舱 HTML（添加 Tab 条 + 包裹 A股面板 + 新增美股面板）

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 替换 view-cockpit 整个 HTML 块**

定位到第 1251 行：
```html
      <!-- ==================== VIEW: COCKPIT ==================== -->
      <div id="view-cockpit" class="view active">
```

将从 `<div id="view-cockpit"...>` 到最后的 `</div><!-- /view-cockpit -->` （约第 1251-1316 行）整体替换为：

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

- [ ] **Step 2: 打开浏览器，切换到驾驶舱页面，检查 Tab 条可见，A股内容正常显示**

访问 `http://localhost:8000`，点击驾驶舱，确认：
- 顶部有「A股」「美股」两个 Tab 按钮
- 默认显示 A股面板，内容与之前一致
- 点击「美股」Tab 切换（此时无数据，空白状态即可）

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(cockpit): 重构驾驶舱 HTML — Tab 条 + A股/美股双面板结构"
```

---

## Task 5: index.html — 美股 JS 函数 + 修改 initCockpit/cleanupCockpit

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 `/* ===== 驾驶舱 =====*/` 块（约第 2651 行）中，`let _cockpitInterval = null;` 之前插入美股状态变量**

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

- [ ] **Step 2: 在 `cleanupCockpit()` 函数定义后（约第 2908 行）追加 3 个美股函数**

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
  // 清理美股图表
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
  if (market === 'us') {
    fetchUsSentiment();
    fetchUsIndices();
  }
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
      el.textContent = lvl;
      el.className = 'sentiment-sub ' + cls;
    }
    if (d.breadth) {
      document.getElementById('us-up').textContent = d.breadth.up ?? '--';
      document.getElementById('us-down').textContent = d.breadth.down ?? '--';
      document.getElementById('us-flat').textContent = d.breadth.flat ?? '--';
      const total = (d.breadth.up || 0) + (d.breadth.down || 0) + (d.breadth.flat || 0);
      document.getElementById('us-breadth-sub').textContent = total > 0
        ? '涨跌比 ' + (d.breadth.up / total).toFixed(4) : '';
    }
  } catch(e) { console.error('fetchUsSentiment error', e); }
}

function _usBuildIndexCards(data) {
  const grid = document.getElementById('us-indices-grid');
  if (!grid) return;
  grid.innerHTML = '';
  data.forEach(item => {
    const code = item.code;
    const changePct = item.prev_close && item.price
      ? ((item.price - item.prev_close) / item.prev_close * 100) : null;
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
      <div class="card-body">
        <div class="ck-chart-wrap" id="us-chart-${code}"></div>
      </div>`;
    grid.appendChild(card);
  });
}

async function fetchUsIndices() {
  try {
    const r = await fetch('/api/cockpit/us/indices');
    const j = await r.json();
    if (!j.success || !j.data) return;
    const grid = document.getElementById('us-indices-grid');
    const needsBuild = grid.children.length === 0;
    if (needsBuild) {
      _usBuildIndexCards(j.data);
    } else {
      j.data.forEach(item => {
        const code = item.code;
        const priceEl = document.getElementById('us-price-' + code);
        const changeEl = document.getElementById('us-change-' + code);
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
  if (_usCharts[code]) {
    try { _usCharts[code].remove(); } catch(e) {}
    delete _usCharts[code];
    delete _usSeries[code];
  }
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
  _usCharts[code] = chart;
  _usSeries[code] = series;
  const ro = new ResizeObserver(() => {
    if (_usCharts[code]) chart.applyOptions({ width: container.clientWidth });
  });
  ro.observe(container);
  _usObservers[code] = ro;
}
```

- [ ] **Step 3: 修改 `initCockpit()` 使其重置市场 Tab 到 A股并启动双市场刷新**

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
  // 每次进入驾驶舱重置到 A股 Tab
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
      clearInterval(_cockpitInterval);
      _cockpitInterval = null;
      return;
    }
    if (_ckActiveMarket === 'a') {
      fetchSentiment(); fetchIndices(); fetchHotStocks(); fetchIndustryRank();
    } else {
      fetchUsSentiment(); fetchUsIndices();
    }
  }, 5000);
}
```

- [ ] **Step 4: 浏览器完整验证**

访问 `http://localhost:8000`，进入驾驶舱：
1. 默认显示 A股 Tab，情绪卡片和指数分时图正常
2. 点击「美股」Tab：VIX 卡片和 5 张指数卡片出现
3. 如当前为美股非交易时段：指数卡显示"已收盘"提示
4. 离开驾驶舱再回来：Tab 重置到 A股，数据重新加载

- [ ] **Step 5: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(cockpit): 美股面板 JS — Tab 切换 + 5大指数 + VIX情绪卡"
```
