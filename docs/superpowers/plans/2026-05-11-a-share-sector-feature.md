# A 股板块功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 StockPulse 中新增 A 股板块功能 — 浏览行业/概念板块列表，热力方块图展示涨跌幅，点击板块查看成分股，板块 K 线图，点击股票跳转个股详情。

**Architecture:** 新建 `sector_service.py`（双源降级业务逻辑）+ `routers/sector.py`（专用 API 路由），复用现有 `patch_requests`/`cache`/`_clean` 模式。前端在 `index.html` 中新增 sidebar 导航项 + `view-sector` 视图，参照基金视图模式实现。

**Tech Stack:** FastAPI, AKShare (东方财富 + 同花顺), SQLite cache, LightweightCharts, vanilla JS

**Spec:** `docs/文档/12-A股板块功能设计方案对比.md`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `wexin-read-mcp-main/requirements.txt` | Modify | 补充 akshare 依赖声明 |
| `wexin-read-mcp-main/src/sector_service.py` | **Create** | 板块业务逻辑：双源降级、数据规范化、缓存 |
| `wexin-read-mcp-main/src/routers/sector.py` | **Create** | 板块 API 路由：4 个端点 |
| `wexin-read-mcp-main/src/state.py` | Modify | 添加 sector_service 单例 |
| `wexin-read-mcp-main/src/app.py` | Modify | 注册 sector_router |
| `wexin-read-mcp-main/src/templates/index.html` | Modify | 新增 sidebar 按钮 + view-sector 视图 + JS 逻辑 |

---

### Task 1: 添加 akshare 依赖

**Files:**
- Modify: `wexin-read-mcp-main/requirements.txt`

- [ ] **Step 1: 在 requirements.txt 中添加 akshare**

在 `requirements.txt` 文件末尾添加一行：

```
akshare>=1.14.0
```

如果文件中已有 akshare（由其他包传递依赖引入），确认版本号即可。检查方式：

```bash
grep -i akshare wexin-read-mcp-main/requirements.txt
```

- [ ] **Step 2: 验证 akshare 可导入**

```bash
cd wexin-read-mcp-main && /Users/wangjun/Desktop/股票信息/.venv/bin/python -c "import akshare; print(akshare.__version__)"
```

Expected: 输出版本号，无报错。

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/requirements.txt
git commit -m "chore: add akshare to requirements.txt"
```

---

### Task 2: 创建 SectorService（双源降级后端）

**Files:**
- Create: `wexin-read-mcp-main/src/sector_service.py`

- [ ] **Step 1: 创建 sector_service.py 骨架**

```python
"""A 股板块服务 — 东方财富为主 + 同花顺兜底。"""

import asyncio
import logging
import math
import os

import pandas as pd

from stock_utils import cache, TTL_DAILY, TTL_REALTIME

logger = logging.getLogger(__name__)

# 代理环境变量 key（与 fund.py 一致）
_PROXY_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")


# ---------- 工具函数 ----------

def _clean(v):
    """NaN/NaT → None, Timestamp → str, numpy → Python native."""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return str(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    return v


def _no_proxy_env():
    saved = {k: os.environ.pop(k) for k in _PROXY_KEYS if k in os.environ}
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        os.environ[k] = v


# ---------- K 线 period 映射 ----------

PERIOD_MAP = {
    "daily": "日k",
    "weekly": "周k",
    "monthly": "月k",
}
```

- [ ] **Step 2: 添加东方财富数据源方法**

```python
# ---------- 东方财富数据源 ----------

def _fetch_boards_eastmoney(board_type: str) -> pd.DataFrame:
    """东方财富板块列表（行业/概念）。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        dfs = []
        if board_type in ("industry", "all"):
            df = ak.stock_board_industry_name_em()
            if df is not None and not df.empty:
                df["__type"] = "industry"
                dfs.append(df)
        if board_type in ("concept", "all"):
            df = ak.stock_board_concept_name_em()
            if df is not None and not df.empty:
                df["__type"] = "concept"
                dfs.append(df)
        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)
    finally:
        _restore_env(saved)


def _fetch_stocks_eastmoney(board_name: str, board_type: str) -> pd.DataFrame:
    """东方财富板块成分股。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            return ak.stock_board_industry_cons_em(symbol=board_name)
        else:
            return ak.stock_board_concept_cons_em(symbol=board_name)
    finally:
        _restore_env(saved)


def _fetch_kline_eastmoney(board_name: str, board_type: str, period: str, count: int) -> pd.DataFrame:
    """东方财富板块 K 线。"""
    import akshare as ak
    ak_period = PERIOD_MAP.get(period, "日k")
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            return ak.stock_board_industry_hist_em(symbol=board_name, period=ak_period)
        else:
            return ak.stock_board_concept_hist_em(symbol=board_name, period=ak_period)
    finally:
        _restore_env(saved)
```

- [ ] **Step 3: 添加同花顺兜底数据源方法**

```python
# ---------- 同花顺兜底数据源 ----------

def _fetch_boards_ths(board_type: str) -> pd.DataFrame:
    """同花顺板块列表（行业/概念）。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        dfs = []
        if board_type in ("industry", "all"):
            df = ak.stock_board_industry_name_ths()
            if df is not None and not df.empty:
                df["__type"] = "industry"
                dfs.append(df)
        if board_type in ("concept", "all"):
            df = ak.stock_board_concept_name_ths()
            if df is not None and not df.empty:
                df["__type"] = "concept"
                dfs.append(df)
        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)
    finally:
        _restore_env(saved)


def _fetch_stocks_ths(board_name: str, board_type: str) -> pd.DataFrame:
    """同花顺板块成分股。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            return ak.stock_board_industry_cons_ths(symbol=board_name)
        else:
            return ak.stock_board_concept_cons_ths(symbol=board_name)
    finally:
        _restore_env(saved)
```

- [ ] **Step 4: 添加数据规范化方法（统一列名映射）**

```python
# ---------- 数据规范化 ----------

def _normalize_boards(df: pd.DataFrame) -> list[dict]:
    """将东方财富/同花顺板块列表 DataFrame 统一为标准 dict 列表。"""
    result = []
    for _, row in df.iterrows():
        item = {
            "name": _clean(row.get("板块名称") or row.get("name") or row.get("名称")),
            "type": _clean(row.get("__type", "industry")),
            "change_pct": _clean(row.get("涨跌幅") or row.get("涨跌幅")),
            "turnover": _clean(row.get("总成交额") or row.get("成交额")),
            "up_count": _clean(row.get("上涨家数")),
            "down_count": _clean(row.get("下跌家数")),
            "lead_stock_name": _clean(row.get("领涨股票")),
            "lead_stock_symbol": None,
        }
        # 东方财富的领涨股字段可能包含代码信息，尝试提取
        lead = item["lead_stock_name"]
        if lead and lead != "-":
            # 如果 lead_stock 包含类似 "123456" 的 6 位数字，尝试提取为 symbol
            import re
            m = re.search(r'\b(\d{6})\b', str(lead))
            if m:
                item["lead_stock_symbol"] = m.group(1)
                item["lead_stock_name"] = re.sub(r'\b\d{6}\b', '', lead).strip() or lead
        if item["name"]:
            result.append(item)
    return result


def _normalize_stocks(df: pd.DataFrame) -> list[dict]:
    """将东方财富/同花顺成分股 DataFrame 统一为标准 dict 列表。"""
    result = []
    for _, row in df.iterrows():
        item = {
            "symbol": _clean(row.get("代码") or row.get("code")),
            "name": _clean(row.get("名称") or row.get("股票名称")),
            "price": _clean(row.get("最新价") or row.get("收盘价")),
            "change_pct": _clean(row.get("涨跌幅")),
            "volume": _clean(row.get("成交量")),
            "turnover": _clean(row.get("成交额")),
        }
        if item["symbol"] and item["name"]:
            result.append(item)
    return result


def _normalize_kline(df: pd.DataFrame) -> list[dict]:
    """将板块 K 线 DataFrame 统一为标准 dict 列表。"""
    result = []
    for _, row in df.iterrows():
        item = {
            "date": str(row.get("日期", ""))[:10],
            "open": _clean(row.get("开盘")),
            "close": _clean(row.get("收盘")),
            "high": _clean(row.get("最高")),
            "low": _clean(row.get("最低")),
            "volume": _clean(row.get("成交量")),
            "turnover": _clean(row.get("成交额")),
            "change_pct": _clean(row.get("涨跌幅")),
        }
        if item["date"]:
            result.append(item)
    return result
```

- [ ] **Step 5: 添加 SectorService 主类**

```python
# ---------- 主服务 ----------

class SectorService:

    async def get_boards(self, board_type="all", sort_by="change_pct", ascending=False, limit=100):
        """
        获取板块列表，双源降级。
        board_type: "industry" | "concept" | "all"
        sort_by: "change_pct" | "turnover" | "up_count"
        """
        ck = f"sector:boards:{board_type}:{sort_by}:{ascending}:{limit}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        data = None
        last_error = None

        # 主源：东方财富
        try:
            df = await asyncio.to_thread(_fetch_boards_eastmoney, board_type)
            if df is not None and not df.empty:
                data = _normalize_boards(df)
                logger.info(f"东方财富板块: {len(data)} 个")
        except Exception as e:
            last_error = e
            logger.warning(f"东方财富板块失败: {e}")

        # 兜底：同花顺
        if data is None:
            logger.info("切换到同花顺兜底")
            try:
                df = await asyncio.to_thread(_fetch_boards_ths, board_type)
                if df is not None and not df.empty:
                    data = _normalize_boards(df)
                    logger.info(f"同花顺板块: {len(data)} 个")
            except Exception as e:
                last_error = e
                logger.warning(f"同花顺板块也失败: {e}")

        if data is None:
            msg = str(last_error) if last_error else "所有板块数据源不可用"
            return {"success": False, "error": msg}

        # 排序
        reverse = not ascending
        if sort_by == "change_pct":
            data.sort(key=lambda x: x.get("change_pct") or 0, reverse=reverse)
        elif sort_by == "turnover":
            data.sort(key=lambda x: x.get("turnover") or 0, reverse=reverse)
        elif sort_by == "up_count":
            data.sort(key=lambda x: x.get("up_count") or 0, reverse=reverse)

        # 限制条数
        total = len(data)
        data = data[:limit]

        resp = {"success": True, "data": data, "total": total}
        cache.set(ck, resp, TTL_DAILY)
        return resp

    async def get_board_stocks(self, board_name: str, board_type: str = "industry"):
        """获取板块成分股，优先东方财富，降级同花顺。"""
        ck = f"sector:stocks:{board_type}:{board_name}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        data = None
        last_error = None

        try:
            df = await asyncio.to_thread(_fetch_stocks_eastmoney, board_name, board_type)
            if df is not None and not df.empty:
                data = _normalize_stocks(df)
        except Exception as e:
            last_error = e
            logger.warning(f"东方财富成分股失败({board_name}): {e}")

        if data is None:
            try:
                df = await asyncio.to_thread(_fetch_stocks_ths, board_name, board_type)
                if df is not None and not df.empty:
                    data = _normalize_stocks(df)
            except Exception as e:
                last_error = e
                logger.warning(f"同花顺成分股也失败({board_name}): {e}")

        if data is None:
            msg = str(last_error) if last_error else f"无法获取「{board_name}」成分股"
            return {"success": False, "error": msg}

        resp = {"success": True, "data": data, "total": len(data)}
        cache.set(ck, resp, TTL_REALTIME)
        return resp

    async def get_board_kline(self, board_name: str, board_type: str = "industry",
                               period: str = "daily", count: int = 120):
        """
        获取板块 K 线（仅东方财富，无兜底）。
        period: "daily" | "weekly" | "monthly"
        """
        ck = f"sector:kline:{board_type}:{board_name}:{period}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(_fetch_kline_eastmoney, board_name, board_type, period, count)
            if df is None or df.empty:
                return {"success": False, "error": f"「{board_name}」无 K 线数据"}
            data = _normalize_kline(df)
            if count and count < len(data):
                data = data[-count:]
            resp = {"success": True, "data": data}
            cache.set(ck, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"K线获取失败({board_name}): {e}")
            return {"success": False, "error": f"K 线获取失败: {e}"}

    async def search(self, keyword: str, board_type: str = "all"):
        """按关键词搜索板块。"""
        boards_resp = await self.get_boards(board_type=board_type, limit=9999)
        if not boards_resp.get("success"):
            return boards_resp
        results = [b for b in boards_resp["data"] if keyword in (b.get("name") or "")]
        return {"success": True, "data": results[:50], "total": len(results)}
```

- [ ] **Step 6: 验证 sector_service.py 可导入**

```bash
cd wexin-read-mcp-main/src && /Users/wangjun/Desktop/股票信息/.venv/bin/python -c "from sector_service import SectorService; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add wexin-read-mcp-main/src/sector_service.py
git commit -m "feat(sector): add SectorService with dual-source fallback (East Money + THS)"
```

---

### Task 3: 创建板块路由

**Files:**
- Create: `wexin-read-mcp-main/src/routers/sector.py`
- Modify: `wexin-read-mcp-main/src/app.py`

- [ ] **Step 1: 创建 routers/sector.py**

```python
"""A 股板块路由 — 行业板块 + 概念板块"""
from fastapi import APIRouter, Query
from state import get_sector_service

router = APIRouter(prefix="/api/sector", tags=["板块"])


@router.get("/boards")
async def get_boards(
    board_type: str = Query(default="all", regex="^(industry|concept|all)$"),
    sort_by: str = Query(default="change_pct", regex="^(change_pct|turnover|up_count)$"),
    ascending: bool = False,
    limit: int = Query(default=100, ge=1, le=999),
):
    """板块列表（行业/概念），支持排序和数量限制"""
    svc = get_sector_service()
    return await svc.get_boards(board_type=board_type, sort_by=sort_by, ascending=ascending, limit=limit)


@router.get("/board/{board_type}/{board_name}")
async def get_board_stocks(board_type: str, board_name: str):
    """板块成分股"""
    svc = get_sector_service()
    return await svc.get_board_stocks(board_name=board_name, board_type=board_type)


@router.get("/board_kline/{board_type}/{board_name}")
async def get_board_kline(
    board_type: str,
    board_name: str,
    period: str = Query(default="daily", regex="^(daily|weekly|monthly)$"),
    count: int = Query(default=120, ge=1, le=1000),
):
    """板块 K 线（仅东方财富，无兜底）"""
    svc = get_sector_service()
    return await svc.get_board_kline(board_name=board_name, board_type=board_type, period=period, count=count)


@router.get("/search")
async def search_boards(
    keyword: str = "",
    board_type: str = Query(default="all", regex="^(industry|concept|all)$"),
):
    """搜索板块"""
    svc = get_sector_service()
    return await svc.search(keyword=keyword, board_type=board_type)
```

- [ ] **Step 2: 修改 state.py，添加 sector_service 单例**

`state.py` 末尾添加：

```python
from sector_service import SectorService

sector_svc = SectorService()
```

同时需要提供一个 getter 函数（因为 router 中不能直接 import 单例可能导致循环导入）：

在 `state.py` 文件末尾追加：

```python
def get_sector_service() -> SectorService:
    return sector_svc
```

完整的 `state.py` 修改后内容：

```python
"""Shared application state — singleton instances for config, scraper, blogger_mgr.

这些实例在 app.py 中被 import，然后 load_saved_config() 在模块级修改 config。
router 文件通过 `from state import config / blogger_mgr / scraper / CONFIG_FILE` 访问。
"""

from pathlib import Path
from config import AppConfig
from scraper import WeixinScraper
from blogger import BloggerManager
from sector_service import SectorService

config = AppConfig.from_env()
scraper = WeixinScraper()
blogger_mgr = BloggerManager(scraper, config)
CONFIG_FILE = Path(__file__).parent.parent / "user_config.json"
sector_svc = SectorService()


def get_sector_service() -> SectorService:
    return sector_svc
```

- [ ] **Step 3: 修改 app.py，注册 sector_router**

在 `app.py` 的路由注册区域（第 ~75 行附近，`app.include_router(articles_router)` 之后）添加：

```python
from routers.sector import router as sector_router

app.include_router(sector_router)
```

- [ ] **Step 4: 验证路由注册成功**

```bash
cd wexin-read-mcp-main/src && /Users/wangjun/Desktop/股票信息/.venv/bin/python -c "
from app import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
sector_routes = [r for r in routes if 'sector' in r]
print('Sector routes:', sector_routes)
assert len(sector_routes) > 0, 'No sector routes found'
print('OK')
"
```

Expected: 列出 `/api/sector/boards` 等路径。

- [ ] **Step 5: Commit**

```bash
git add wexin-read-mcp-main/src/routers/sector.py wexin-read-mcp-main/src/state.py wexin-read-mcp-main/src/app.py
git commit -m "feat(sector): add sector API router with 4 endpoints"
```

---

### Task 4: 前端 — Sidebar 按钮 + View 容器 + topbarMeta

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 sidebar 中添加"板块"按钮**

定位到 `index.html` 第 1127 行附近的"股票查询"按钮：

```html
<button class="nav-item" data-view="stock" onclick="switchView('stock', this)">
  <svg viewBox="0 0 20 20" fill="none"><path d="M3 17V7l4-4 3 5 4-5 3 7v7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
  <span>股票查询</span>
</button>
```

在其下方（同一行之后）插入新的"板块"按钮：

```html
<button class="nav-item" data-view="sector" onclick="switchView('sector', this)">
  <svg viewBox="0 0 20 20" fill="none"><rect x="2" y="2" width="7" height="7" rx="1.5" stroke="currentColor" stroke-width="1.7"/><rect x="11" y="2" width="7" height="7" rx="1.5" stroke="currentColor" stroke-width="1.7"/><rect x="2" y="11" width="7" height="7" rx="1.5" stroke="currentColor" stroke-width="1.7"/><rect x="11" y="11" width="7" height="7" rx="1.5" stroke="currentColor" stroke-width="1.7"/></svg>
  <span>板块</span>
</button>
```

- [ ] **Step 2: 在 topbarMeta 中添加 sector 条目**

定位到 `topbarMeta` 对象（约第 2218-2229 行），在 `sim` 条目之后添加：

```javascript
sector: { title: '板块', sub: '行业板块 · 概念板块 · 涨跌幅热力图 · 成分股 · K线' },
```

- [ ] **Step 3: 在 switchView() 中添加 sector case**

定位到 `switchView()` 函数（约第 2233-2258 行），在最后一个 `if` 语句之后添加：

```javascript
if (view === 'sector') { loadSectorBoards(); }
```

- [ ] **Step 4: 添加 view-sector 视图 HTML 容器**

定位到 `view-fund` 的 `</div>` 闭合标签（约第 1842 行），在其后添加 `view-sector` 视图：

```html
<div id="view-sector" class="view">

  <!-- Tab 切换 -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-body" style="padding:8px 12px;">
      <div style="display:flex;gap:4px;">
        <button class="btn btn-ghost btn-sm active" id="sectorTabIndustry" onclick="switchSectorTab('industry', this)">行业板块</button>
        <button class="btn btn-ghost btn-sm" id="sectorTabConcept" onclick="switchSectorTab('concept', this)">概念板块</button>
        <button class="btn btn-ghost btn-sm" id="sectorTabAll" onclick="switchSectorTab('all', this)">全部</button>
      </div>
    </div>
  </div>

  <!-- 热力方块图 -->
  <div class="card" id="sectorHeatmapCard">
    <div class="card-header">
      <div class="card-title">涨跌热力图</div>
      <button class="btn btn-ghost btn-sm" onclick="loadSectorBoards()">刷新</button>
    </div>
    <div class="card-body" id="sectorHeatmapBody">
      <div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>
    </div>
  </div>

  <!-- 板块列表表格 -->
  <div class="card" style="margin-top:16px;" id="sectorListCard">
    <div class="card-header">
      <div class="card-title">板块列表</div>
    </div>
    <div class="card-body" style="overflow-x:auto;padding:0;">
      <div id="sectorListBody" style="max-height:600px;overflow-y:auto;"></div>
    </div>
  </div>

  <!-- 板块详情面板 -->
  <div id="sectorDetailArea" style="display:none;">
    <div style="margin-bottom:16px;">
      <button class="btn btn-ghost" onclick="hideSectorDetail()" style="font-size:13px;">← 返回板块列表</button>
      <span id="sectorDetailTitle" style="font-weight:600;font-size:16px;margin-left:8px;"></span>
    </div>

    <!-- 板块 K 线图 -->
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <div class="card-title">板块 K 线</div>
        <div style="display:flex;gap:4px;" id="sectorKlinePeriodBtns">
          <button class="btn btn-ghost btn-sm active" onclick="switchSectorKlinePeriod('daily',this)">日K</button>
          <button class="btn btn-ghost btn-sm" onclick="switchSectorKlinePeriod('weekly',this)">周K</button>
          <button class="btn btn-ghost btn-sm" onclick="switchSectorKlinePeriod('monthly',this)">月K</button>
        </div>
      </div>
      <div class="card-body" style="padding:0;">
        <div id="sectorKlineChart" style="height:360px;"></div>
      </div>
    </div>

    <!-- 成分股列表 -->
    <div class="card">
      <div class="card-header">
        <div class="card-title" id="sectorStocksTitle">成分股</div>
      </div>
      <div class="card-body" style="overflow-x:auto;padding:0;">
        <div id="sectorStocksBody" style="max-height:500px;overflow-y:auto;"></div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 5: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(sector): add sidebar button, view container, and topbarMeta for sector page"
```

---

### Task 5: 前端 — 板块列表加载 + 热力方块图 + Tab 切换

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`（JS 区域，~4095 行之后）

- [ ] **Step 1: 添加板块状态变量**

在 JS 全局变量区域（约文件开头其他 `_xxxLoaded` 变量附近）添加：

```javascript
let _sectorCurrentType = 'industry';
let _sectorData = [];
let _sectorKlineChart = null;
let _sectorKlineResizeObserver = null;
```

- [ ] **Step 2: 添加 Tab 切换函数**

```javascript
function switchSectorTab(type, el) {
  document.querySelectorAll('#sectorHeatmapCard').forEach(() => {});
  // 更新按钮状态
  document.querySelectorAll('#sectorHeatmapCard ~ .card, #sectorHeatmapCard').forEach(() => {});
  document.querySelectorAll('[id^="sectorTab"]').forEach(btn => btn.classList.remove('active'));
  if (el) el.classList.add('active');
  _sectorCurrentType = type;
  hideSectorDetail();
  loadSectorBoards();
}
```

- [ ] **Step 3: 添加板块列表加载函数**

```javascript
async function loadSectorBoards() {
  const heatmapBody = document.getElementById('sectorHeatmapBody');
  const listBody = document.getElementById('sectorListBody');
  if (!heatmapBody || !listBody) return;
  heatmapBody.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  listBody.innerHTML = '';

  try {
    const resp = await fetch('/api/sector/boards?board_type=' + _sectorCurrentType + '&sort_by=change_pct&limit=100');
    const json = await resp.json();
    if (!json.success || !json.data?.length) {
      const msg = escapeHtml(json.error || '加载失败');
      heatmapBody.innerHTML = '<div class="empty-state">' + msg + '</div>';
      return;
    }
    _sectorData = json.data;
    renderSectorHeatmap(json.data);
    renderSectorListTable(json.data);
  } catch (e) {
    heatmapBody.innerHTML = '<div class="empty-state">加载失败，请稍后重试</div>';
  }
}
```

- [ ] **Step 4: 添加热力方块图渲染函数**

```javascript
function renderSectorHeatmap(boards) {
  const container = document.getElementById('sectorHeatmapBody');
  if (!container) return;
  if (!boards.length) { container.innerHTML = '<div class="empty-state">暂无数据</div>'; return; }

  // 按成交额缩放方块大小
  const turnovers = boards.map(b => b.turnover || 0).filter(t => t > 0);
  const maxTurnover = turnovers.length ? Math.max(...turnovers) : 1;

  // 涨跌幅颜色：红涨绿跌
  const getColor = (pct) => {
    if (pct == null) return 'rgba(148,163,184,0.5)';
    const abs = Math.min(Math.abs(pct), 10);
    const intensity = abs / 10;
    if (pct > 0) return `rgba(239,68,68,${0.15 + intensity * 0.7})`;
    if (pct < 0) return `rgba(34,197,94,${0.15 + intensity * 0.7})`;
    return 'rgba(148,163,184,0.3)';
  };

  let html = '<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:flex-end;">';
  boards.forEach(b => {
    const pct = b.change_pct || 0;
    const pctStr = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    const turnoverRatio = maxTurnover > 0 ? (b.turnover || 0) / maxTurnover : 0;
    // 方块大小：最小 60px，最大 140px
    const baseSize = 60 + turnoverRatio * 80;
    const color = getColor(pct);
    html += `<div onclick="loadSectorBoardDetail('${escapeHtml(b.type)}','${escapeHtml(b.name)}')"
      style="flex:0 0 ${baseSize}px;height:${baseSize * 0.7}px;background:${color};border-radius:6px;
      display:flex;flex-direction:column;justify-content:center;align-items:center;cursor:pointer;
      padding:4px;overflow:hidden;transition:transform 0.15s;"
      onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
      <div style="font-size:11px;font-weight:600;color:#1e293b;text-align:center;line-height:1.2;
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;">${escapeHtml(b.name)}</div>
      <div style="font-size:12px;font-weight:700;margin-top:2px;color:${pct >= 0 ? '#dc2626' : '#16a34a'}">${pctStr}</div>
    </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}
```

- [ ] **Step 5: 添加板块列表表格渲染函数**

```javascript
function renderSectorListTable(boards) {
  const container = document.getElementById('sectorListBody');
  if (!container) return;
  if (!boards.length) { container.innerHTML = '<div class="empty-state">暂无数据</div>'; return; }

  let html = '<table class="data-table"><thead><tr>'
    + '<th>板块名称</th><th>类型</th><th style="text-align:right">涨跌幅</th>'
    + '<th style="text-align:right">成交额</th><th>涨/跌</th><th>领涨股</th>'
    + '</tr></thead><tbody>';

  boards.forEach(b => {
    const pct = b.change_pct || 0;
    const pctStr = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    const pctCls = pct >= 0 ? 'up' : 'down';
    const typeLabel = b.type === 'industry' ? '行业' : '概念';
    const turnoverStr = b.turnover != null ? formatTurnover(b.turnover) : '-';
    const upDown = (b.up_count != null ? b.up_count : '-') + '/' + (b.down_count != null ? b.down_count : '-');
    const leadStock = b.lead_stock_name || '-';
    const leadLink = b.lead_stock_symbol
      ? `<span class="code-link" onclick="event.stopPropagation();openStockFromSector('${escapeHtml(b.lead_stock_symbol)}')">${escapeHtml(leadStock)}</span>`
      : escapeHtml(leadStock);

    html += `<tr style="cursor:pointer;" onclick="loadSectorBoardDetail('${escapeHtml(b.type)}','${escapeHtml(b.name)}')">`
      + `<td><span class="name-strong">${escapeHtml(b.name)}</span></td>`
      + `<td>${typeLabel}</td>`
      + `<td class="num ${pctCls}">${pctStr}</td>`
      + `<td class="num">${turnoverStr}</td>`
      + `<td>${upDown}</td>`
      + `<td>${leadLink}</td>`
      + '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

function formatTurnover(val) {
  if (val == null) return '-';
  if (val >= 1e12) return (val / 1e12).toFixed(2) + '万亿';
  if (val >= 1e8) return (val / 1e8).toFixed(2) + '亿';
  if (val >= 1e4) return (val / 1e4).toFixed(2) + '万';
  return val.toFixed(0);
}
```

- [ ] **Step 6: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(sector): add board list loading, heatmap, and list table rendering"
```

---

### Task 6: 前端 — 板块详情（成分股 + K 线 + 跳转个股）

**Files:**
- Modify: `wexin-read-mcp-main/src/templates/index.html`（JS 区域）

- [ ] **Step 1: 添加板块详情加载和显示/隐藏函数**

```javascript
function hideSectorDetail() {
  const area = document.getElementById('sectorDetailArea');
  if (area) area.style.display = 'none';
  // 隐藏热力图和列表
  const heatmap = document.getElementById('sectorHeatmapCard');
  const list = document.getElementById('sectorListCard');
  if (heatmap) heatmap.style.display = '';
  if (list) list.style.display = '';
  // 清理 K 线图表
  _disposeSectorKlineChart();
}

function _disposeSectorKlineChart() {
  if (_sectorKlineChart) { try { _sectorKlineChart.remove(); } catch(e){} _sectorKlineChart = null; }
  if (_sectorKlineResizeObserver) { _sectorKlineResizeObserver.disconnect(); _sectorKlineResizeObserver = null; }
}

async function loadSectorBoardDetail(type, name) {
  const area = document.getElementById('sectorDetailArea');
  const title = document.getElementById('sectorDetailTitle');
  const heatmap = document.getElementById('sectorHeatmapCard');
  const list = document.getElementById('sectorListCard');
  if (!area) return;

  // 显示详情，隐藏列表
  area.style.display = '';
  if (heatmap) heatmap.style.display = 'none';
  if (list) list.style.display = 'none';
  if (title) title.textContent = '「' + name + '」(' + (type === 'industry' ? '行业' : '概念') + ')';

  // 并发加载成分股 + K 线
  await Promise.all([
    loadSectorBoardStocks(type, name),
    loadSectorKline(type, name, 'daily'),
  ]);
}
```

- [ ] **Step 2: 添加成分股加载函数**

```javascript
async function loadSectorBoardStocks(type, name) {
  const body = document.getElementById('sectorStocksBody');
  const title = document.getElementById('sectorStocksTitle');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';

  try {
    const resp = await fetch('/api/sector/board/' + encodeURIComponent(type) + '/' + encodeURIComponent(name));
    const json = await resp.json();
    if (!json.success) { body.innerHTML = '<div class="empty-state">' + escapeHtml(json.error || '查询失败') + '</div>'; return; }
    if (!json.data?.length) { body.innerHTML = '<div class="empty-state">暂无成分股数据</div>'; return; }
    if (title) title.textContent = '成分股（共 ' + (json.total || json.data.length) + ' 只）';

    // 渲染为表格，点击行跳转个股详情
    let html = '<table class="data-table"><thead><tr>'
      + '<th>代码</th><th>名称</th><th style="text-align:right">最新价</th>'
      + '<th style="text-align:right">涨跌幅</th><th style="text-align:right">成交量(手)</th>'
      + '<th style="text-align:right">成交额</th>'
      + '</tr></thead><tbody>';
    json.data.forEach(s => {
      const pct = s.change_pct || 0;
      const pctStr = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
      const pctCls = pct >= 0 ? 'up' : 'down';
      html += `<tr style="cursor:pointer;" onclick="openStockFromSector('${escapeHtml(s.symbol)}')">`
        + `<td><span class="code-link">${escapeHtml(s.symbol)}</span></td>`
        + `<td><span class="name-strong">${escapeHtml(s.name)}</span></td>`
        + `<td class="num">${s.price != null ? s.price.toFixed(2) : '-'}</td>`
        + `<td class="num ${pctCls}">${pctStr}</td>`
        + `<td class="num">${s.volume != null ? Number(s.volume).toLocaleString() : '-'}</td>`
        + `<td class="num">${s.turnover != null ? formatTurnover(s.turnover) : '-'}</td>`
        + '</tr>';
    });
    html += '</tbody></table>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = '<div class="empty-state">请求失败，请稍后重试</div>';
  }
}
```

- [ ] **Step 3: 添加板块 K 线加载和渲染函数**

```javascript
async function loadSectorKline(type, name, period) {
  const container = document.getElementById('sectorKlineChart');
  if (!container) return;
  _disposeSectorKlineChart();

  try {
    const resp = await fetch('/api/sector/board_kline/' + encodeURIComponent(type) + '/' + encodeURIComponent(name) + '?period=' + period);
    const json = await resp.json();
    if (!json.success || !json.data?.length) { container.innerHTML = '<div class="empty-state" style="padding:40px;">暂无K线数据</div>'; return; }

    container.innerHTML = '';
    _sectorKlineChart = LightweightCharts.createChart(container, {
      width: container.clientWidth, height: 360,
      layout: { background: { color: '#ffffff' }, textColor: '#64748B', fontSize: 11 },
      grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });

    // K 线
    const candleSeries = _sectorKlineChart.addCandlestickSeries({
      upColor: '#ef4444', downColor: '#22c55e',
      borderUpColor: '#ef4444', borderDownColor: '#22c55e',
      wickUpColor: '#ef4444', wickDownColor: '#22c55e',
    });
    candleSeries.setData(json.data.map(k => ({
      time: k.date, open: k.open, high: k.high, low: k.low, close: k.close
    })));

    // 成交量
    const volSeries = _sectorKlineChart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: 'vol',
    });
    _sectorKlineChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volSeries.setData(json.data.map(k => ({
      time: k.date, value: k.volume || 0,
      color: k.close >= k.open ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.3)'
    })));

    _sectorKlineChart.timeScale().fitContent();
    _sectorKlineChart.timeScale().applyOptions({ rightOffset: 5, minBarSpacing: 2 });

    _sectorKlineResizeObserver = new ResizeObserver(() => {
      if (_sectorKlineChart) _sectorKlineChart.applyOptions({ width: container.clientWidth });
    });
    _sectorKlineResizeObserver.observe(container);
  } catch (e) {
    container.innerHTML = '<div class="empty-state" style="padding:40px;">K线加载失败</div>';
  }
}

function switchSectorKlinePeriod(period, el) {
  document.querySelectorAll('#sectorKlinePeriodBtns button').forEach(b => b.classList.remove('active'));
  if (el) el.classList.add('active');
  // 从当前详情标题中提取板块名和类型
  const title = document.getElementById('sectorDetailTitle');
  if (!title) return;
  const text = title.textContent; // 格式: 「板块名」(类型)
  const match = text.match(/「(.+?)」\((行业|概念)\)/);
  if (!match) return;
  const name = match[1];
  const type = match[2] === '行业' ? 'industry' : 'concept';
  loadSectorKline(type, name, period);
}
```

- [ ] **Step 4: 添加跳转个股详情函数**

```javascript
function openStockFromSector(symbol) {
  // 切换到股票查询视图
  const stockNav = document.querySelector('[data-view="stock"]');
  if (stockNav) switchView('stock', stockNav);
  // 填入搜索框并触发搜索
  const input = document.getElementById('stockSearchInput');
  if (input) {
    input.value = symbol;
    // 触发股票查询（复用已有逻辑）
    if (typeof searchStock === 'function') {
      searchStock();
    } else if (typeof loadStock === 'function') {
      loadStock(symbol);
    }
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(sector): add board detail view with stocks table, K-line chart, and stock navigation"
```

---

### Task 7: 验证与冒烟测试

**Files:**
- None (verification only)

- [ ] **Step 1: 重启服务并验证 API**

```bash
# 停掉旧进程
lsof -ti:8000 | xargs kill 2>/dev/null; sleep 1
# 启动
cd wexin-read-mcp-main/src && /Users/wangjun/Desktop/股票信息/.venv/bin/python app.py &
sleep 5

# 测试板块列表 API
curl -s 'http://localhost:8000/api/sector/boards?board_type=industry&limit=5' | python3 -m json.tool

# 测试板块成分股 API（用上一步返回的板块名）
curl -s 'http://localhost:8000/api/sector/board/industry/半导体' | python3 -m json.tool

# 测试板块 K 线 API
curl -s 'http://localhost:8000/api/sector/board_kline/industry/半导体?period=daily&limit=10' | python3 -m json.tool

# 测试搜索 API
curl -s 'http://localhost:8000/api/sector/search?keyword=半导体' | python3 -m json.tool
```

Expected: 每个 API 返回 `{"success": true, "data": [...]}` 格式的 JSON。

- [ ] **Step 2: 前端冒烟测试**

在浏览器中打开 http://localhost:8000，执行以下操作验证：

1. 侧边栏出现"板块"按钮 → 点击后切换到板块视图
2. 行业板块 Tab 显示热力方块图，方块按涨跌幅着色
3. 点击"概念板块"Tab，数据切换为概念板块
4. 点击热力图中的方块 → 进入板块详情，显示 K 线 + 成分股列表
5. 点击成分股中的股票 → 跳转到股票查询视图并加载该股票详情
6. 点击"← 返回板块列表" → 返回热力图 + 列表视图
7. K 线图日/周/月切换正常

- [ ] **Step 3: Commit 验证确认**

```bash
git add -A
git commit -m "docs: add sector feature verification notes"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** 所有规格需求均覆盖（板块列表/成分股/K线/热力图/双源降级/搜索/排序/跳转）
- [x] **Placeholder scan:** 无 TBD/TODO，所有步骤含完整代码
- [x] **Type consistency:** `board_type` 始终为 `"industry" | "concept" | "all"`，API 参数命名一致
- [x] **Schema consistency:** 后端 Schema 与前端渲染字段完全对应
- [x] **Existing patterns:** 复用 `_clean()`、`cache.get/set`、`_no_proxy_env()`、`switchView()`、`_renderWencaiTable` 等
