# 2026-05-12 设计文档：内存泄漏修复 + 北向资金/龙虎榜

> 日期：2026-05-12
> 状态：待审批
> 预估总工时：3-5 小时

---

## 任务一：前端内存泄漏修复（阶段二）

### 背景

前端 `index.html` 中有 6 处 ResizeObserver 创建后未保存引用，无法在切换视图或重建图表时 disconnect，导致 Detached DOM 节点持续增长，长时间使用后页面变慢。

### 影响文件

- `wexin-read-mcp-main/src/templates/index.html`

### 泄漏清单

| # | 函数 | 行号 | 问题 |
|---|------|------|------|
| L1 | loadKline() 内 MACD 子图 | L3526 | `new ResizeObserver()` 未保存引用 |
| L2 | loadKline() 内 RSI 子图 | L3561 | 同上 |
| L3 | selectFund() | L4303 | 每次切换基金创建新 observer，旧的不释放 |
| L4 | switchFundKlinePeriod() | L4357 | 每次切换周期创建新 observer，旧的不释放 |
| L5 | selectFutures() | L4554 | 每次切换期货创建新 observer，旧的不释放 |
| L6 | switchFuturesKlinePeriod() | L4612 | 每次切换周期创建新 observer，旧的不释放 |

### 修复方案

**步骤 1**：在全局变量区（L3275 附近）新增声明：

```javascript
let _klineMacdObserver = null;
let _klineRsiObserver = null;
let _fundResizeObserver = null;
let _futuresResizeObserver = null;
```

**步骤 2**：修改 `_disposeKlineSubCharts()`（L3413），在销毁子图时同步 disconnect：

```javascript
function _disposeKlineSubCharts() {
  if (_klineMacdObserver) { _klineMacdObserver.disconnect(); _klineMacdObserver = null; }
  if (_klineRsiObserver) { _klineRsiObserver.disconnect(); _klineRsiObserver = null; }
  if (_klineMacdChart) { try { _klineMacdChart.remove(); } catch(e){} _klineMacdChart = null; }
  if (_klineRsiChart) { try { _klineRsiChart.remove(); } catch(e){} _klineRsiChart = null; }
  // ... 原有 DOM 清理继续
}
```

**步骤 3**：L3526 和 L3561，将匿名 `new ResizeObserver()` 改为保存到变量：

```javascript
// L3526 原代码
new ResizeObserver(() => { ... }).observe(macdEl);
// 改为
_klineMacdObserver = new ResizeObserver(() => { if (_klineMacdChart) _klineMacdChart.applyOptions({ width: macdEl.clientWidth }); });
_klineMacdObserver.observe(macdEl);
```

RSI 同理，保存到 `_klineRsiObserver`。

**步骤 4**：基金图表 — `selectFund()`（L4303 附近）和 `switchFundKlinePeriod()`（L4357）：函数顶部先 disconnect 旧 observer，创建新 chart 后保存新 observer。

```javascript
// 函数顶部添加
if (_fundResizeObserver) { _fundResizeObserver.disconnect(); _fundResizeObserver = null; }

// L4303 / L4357 原代码
new ResizeObserver(() => { ... }).observe(chartEl);
// 改为
_fundResizeObserver = new ResizeObserver(() => { if (_fundChart) _fundChart.applyOptions({ width: chartEl.clientWidth }); });
_fundResizeObserver.observe(chartEl);
```

**步骤 5**：期货图表 — `selectFutures()`（L4554）和 `switchFuturesKlinePeriod()`（L4612）：同上模式，使用 `_futuresResizeObserver`，函数顶部先 disconnect 旧的。

同上模式，使用 `_futuresResizeObserver`。

**步骤 6**：`switchView()`（L2307）顶部添加统一清理：

```javascript
function switchView(view, el) {
  // 离开旧视图前清理副作用
  if (_klineMacdObserver) { _klineMacdObserver.disconnect(); _klineMacdObserver = null; }
  if (_klineRsiObserver) { _klineRsiObserver.disconnect(); _klineRsiObserver = null; }
  if (_fundResizeObserver) { _fundResizeObserver.disconnect(); _fundResizeObserver = null; }
  if (_futuresResizeObserver) { _futuresResizeObserver.disconnect(); _futuresResizeObserver = null; }
  // 原有逻辑继续...
```

### 附加项（可选）

`_watchlistInterval` 清理：在 `switchView()` 清理块中加：

```javascript
if (_watchlistInterval && view !== 'watchlist') {
  clearInterval(_watchlistInterval);
  _watchlistInterval = null;
}
```

### 验收标准

DevTools → Memory 面板 → 反复切换视图 10 次（股票→基金→期货→股票...），Detached 节点数不持续增长。

---

## 任务二：北向资金 + 龙虎榜

### 背景

AKShare 已有现成接口，A 股用户的核心需求。接口函数已验证可用：
- `ak.stock_hsgt_fund_min_em(symbol="北向资金")` — 实时净流入
- `ak.stock_hsgt_hist_em(symbol="北向资金")` — 历史趋势
- `ak.stock_lhb_detail_daily_sina()` — 龙虎榜

### 后端实现

追加到 `wexin-read-mcp-main/src/routers/market.py`（现有 71 行）。

#### 路由表

| 路由 | AKShare 函数 | 缓存 TTL | 返回格式 |
|------|-------------|---------|---------|
| `GET /api/market/north-flow` | `ak.stock_hsgt_fund_min_em(symbol="北向资金")` | 300s | `{success, data: [{日期, 时间, 沪股通, 深股通, 北向资金}]}` |
| `GET /api/market/north-history` | `ak.stock_hsgt_hist_em(symbol="北向资金")` | 300s | `{success, data: [{日期, 当日成交净买额, 买入成交额, 卖出成交额, ...}]}` |
| `GET /api/market/dragon-tiger` | `ak.stock_lhb_detail_daily_sina()` | 600s | `{success, data: [{...}], date}` |

#### 路由实现规范

```python
from stock_utils import cache_get, cache_set

@router.get("/api/market/north-flow")
async def get_north_flow():
    cached = cache_get("north-flow")
    if cached is not None:
        return {"success": True, "data": cached}
    try:
        import akshare as ak
        df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
        data = df.tail(50).to_dict(orient="records")
        cache_set("north-flow", data, 300)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### 前端实现

#### 导航入口

在侧边栏「期货」（L1143）下方、「自选股」（L1144）上方新增：

```html
<button class="nav-item" data-view="northbound" onclick="switchView('northbound', this)">
  <svg viewBox="0 0 20 20" fill="none"><path d="M4 16l4-6 3 3 5-7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 6h2v2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
  <span>北向/龙虎</span>
</button>
```

#### 视图容器

新增 `view-northbound` div，包含两个 tab：
- Tab 切换用 CSS class `.active` 控制
- 北向资金 tab：大字显示今日净流入 + lightweight-charts 折线图
- 龙虎榜 tab：数据表格

#### 代码组织（V2 规范）

```javascript
// API 封装
window.StockPulse.api.getNorthFlow = async function() { ... }
window.StockPulse.api.getNorthHistory = async function() { ... }
window.StockPulse.api.getDragonTiger = async function(date) { ... }

// 页面逻辑
window.StockPulse.pages.northbound = {
  load: loadNorthboundPage,
  render: renderNorthboundPage,
  switchTab: switchNorthboundTab,
}
```

#### topbarMeta 新增

```javascript
northbound: { title: '北向/龙虎', sub: '北向资金实时净流入 · 历史趋势 · 龙虎榜' },
```

#### 龙虎榜非交易日处理

```javascript
if (!data || !data.length) {
  el.innerHTML = '<div class="empty-state">今日暂无龙虎榜数据</div>';
  return;
}
```

### 验收标准

- [x] 点击「北向/龙虎」导航，页面正常加载
- [x] 北向资金 tab 显示今日净流入数字
- [x] 北向资金近 30 日折线图正常渲染
- [x] 龙虎榜 tab 表格有数据
- [x] 非交易日显示"今日暂无龙虎榜数据"而非报错
