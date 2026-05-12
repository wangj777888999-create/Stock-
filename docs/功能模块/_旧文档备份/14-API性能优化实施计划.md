# API 性能优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过三项改动将重复请求响应时间从 500ms~5s 降至毫秒级：L1 内存缓存 + TTL 重新分级 + 启动预热。

**Architecture:** 在现有 SQLite 缓存（L2）前增加进程内 dict 缓存（L1），热数据命中无需磁盘 I/O；同时将 K 线/板块列表的过期时间从 5 分钟延长至 1~2 小时；服务启动时后台并发预拉取板块列表与自选股行情，消除首次访问延迟。

**Tech Stack:** Python 3.11, FastAPI, SQLite (stock_utils.py), asyncio

---

## 文件改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `wexin-read-mcp-main/src/stock_utils.py` | 修改 | 新增 TTL 常量 + 改写 `_CacheCompat` 为双层缓存 |
| `wexin-read-mcp-main/src/sector_service.py` | 修改 | 更新 import + 改用新 TTL 常量 |
| `wexin-read-mcp-main/src/stock_service.py` | 修改 | K 线缓存改用 `TTL_KLINE` |
| `wexin-read-mcp-main/src/app.py` | 修改 | `_startup` 增加板块列表 + 自选股预热任务 |

---

## Task 1：TTL 重新分级

**Files:**
- Modify: `wexin-read-mcp-main/src/stock_utils.py:127-130`
- Modify: `wexin-read-mcp-main/src/sector_service.py:12`（import 行）+ 若干 cache.set 调用
- Modify: `wexin-read-mcp-main/src/stock_service.py:667`

### 背景

当前 `TTL_DAILY = 300`（5 分钟）被同时用于：
- K 线历史数据（日内不会变，5 分钟太短）
- 板块列表（变化极慢，5 分钟太短）
- 实时行情聚合（合理）

### 步骤

- [ ] **Step 1：在 `stock_utils.py` 新增两个 TTL 常量**

找到（约第 127 行）：
```python
TTL_REALTIME = 30
TTL_REALTIME_REFRESH = 5
TTL_DAILY = 300
TTL_COMPANY = 86400
```
替换为：
```python
TTL_REALTIME = 30          # 实时行情（30s）
TTL_REALTIME_REFRESH = 5   # 强制刷新（5s）
TTL_DAILY = 300            # 日内聚合行情（5min，盘中仍在变化）
TTL_KLINE = 3600           # K 线历史（1h，日内不变）
TTL_BOARDS = 1800          # 板块列表/成分股（30min）
TTL_COMPANY = 86400        # 公司基本面（24h）
```

- [ ] **Step 2：更新 `sector_service.py` 的 import 与缓存调用**

第 12 行 import 改为：
```python
from stock_utils import _clean, cache, TTL_BOARDS, TTL_KLINE, TTL_REALTIME
```

然后替换以下 `cache.set` 调用（共 6 处）：

| 位置（约行号） | 原来 | 改为 | 理由 |
|--------------|------|------|------|
| `_get_boards_single` 两处 `cache.set(ck, data, TTL_DAILY)` | TTL_DAILY | TTL_BOARDS | 板块排行，变化极慢 |
| `get_board_stocks` 两处 `cache.set(ck, resp, TTL_REALTIME)` | TTL_REALTIME | TTL_DAILY | 含实时价格，5min刷新即可，30s太频繁 |
| `get_board_kline` 两处 `cache.set(ck, resp, TTL_DAILY)` | TTL_DAILY | TTL_KLINE | 历史K线日内不变 |

- [ ] **Step 3：更新 `stock_service.py` K 线缓存**

第 27~30 行 import 加入 `TTL_KLINE`：
```python
from stock_utils import (
    TTL_COMPANY,
    TTL_DAILY,
    TTL_KLINE,
    TTL_REALTIME,
    TTL_REALTIME_REFRESH,
    ...
)
```

第 667 行：
```python
# 原来
cache.set(cache_key, resp, TTL_DAILY)
# 改为
cache.set(cache_key, resp, TTL_KLINE)
```

- [ ] **Step 4：验证 TTL 生效**

重启服务后请求一次 K 线：
```
GET http://localhost:8000/api/stock/kline/000001?period=day
```
然后查 SQLite：
```bash
cd wexin-read-mcp-main
python -c "
import sqlite3, time
conn = sqlite3.connect('data.db')
rows = conn.execute('SELECT key, expires_at - ? FROM cache WHERE key LIKE \"%kline%\"', (time.time(),)).fetchall()
for k, rem in rows: print(k[:60], int(rem), 's')
"
```
预期：剩余时间接近 3600s（而非之前的 300s）。

- [ ] **Step 5：提交**

```bash
git add wexin-read-mcp-main/src/stock_utils.py \
        wexin-read-mcp-main/src/sector_service.py \
        wexin-read-mcp-main/src/stock_service.py
git commit -m "perf: TTL 重新分级 — K线1h / 板块30min / 保留行情5min"
```

---

## Task 2：L1 进程内内存缓存

**Files:**
- Modify: `wexin-read-mcp-main/src/stock_utils.py:189-199`（`_CacheCompat` 类）

### 背景

当前每次缓存命中仍需读 SQLite（磁盘 I/O，5~20ms）。新增一个进程内 `dict` 作为 L1，命中时直接返回，不碰磁盘。L1 的过期时间直接来自 SQLite 的 `expires_at`，保证两层一致。

### 步骤

- [ ] **Step 1：在 `stock_utils.py` 添加 L1 存储**

在 `_CacheCompat` 类定义**之前**插入（约第 188 行，`class _CacheCompat` 前）：
```python
# L1 进程内缓存：key → (value, expires_at: float)
_mem: dict[str, tuple[Any, float]] = {}
_mem_lock = threading.Lock()
```

- [ ] **Step 2：改写 `_CacheCompat.get`**

将：
```python
class _CacheCompat:
    """向后兼容旧 TTLCache API，委托给 cache_get/cache_set。"""

    def get(self, key: str):
        return cache_get(key)

    def set(self, key: str, value: Any, ttl: int = TTL_DAILY) -> None:
        cache_set(key, value, ttl)
```

替换为：
```python
class _CacheCompat:
    """两层缓存：L1 进程内 dict（<0.1ms）→ L2 SQLite（5~20ms）→ 未命中。"""

    def get(self, key: str):
        now = time.time()
        # L1 命中
        with _mem_lock:
            entry = _mem.get(key)
            if entry is not None:
                val, exp = entry
                if now < exp:
                    return val
                del _mem[key]
        # L2：直接查 SQLite，同时获取 expires_at 用于回填 L1
        db = get_db()
        row = db.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        raw, expires_at = row
        if now >= expires_at:
            db.execute("DELETE FROM cache WHERE key = ?", (key,))
            db.commit()
            return None
        val = _deserialize(raw)
        # 回填 L1，使用 SQLite 中真实的 expires_at
        with _mem_lock:
            _mem[key] = (val, expires_at)
        return val

    def set(self, key: str, value: Any, ttl: int = TTL_DAILY) -> None:
        exp = time.time() + ttl
        with _mem_lock:
            _mem[key] = (value, exp)
        cache_set(key, value, ttl)
```

- [ ] **Step 3：验证 L1 缓存命中**

重启服务，请求同一接口两次，观察日志中是否出现 AKShare 调用：
- 第一次请求：日志出现 `东方财富获取 xxx` 等字样（L2/API 路径）
- 第二次请求：日志**无** AKShare 字样（L1 命中，直接返回）

也可用计时工具（浏览器 DevTools Network 面板）：
- 第一次：几百ms ~ 几秒
- 第二次：< 50ms（含网络往返）

- [ ] **Step 4：提交**

```bash
git add wexin-read-mcp-main/src/stock_utils.py
git commit -m "perf: 新增 L1 进程内内存缓存，缓存命中从磁盘降为内存"
```

---

## Task 3：启动预热

**Files:**
- Modify: `wexin-read-mcp-main/src/app.py:101-116`（`_startup` 函数）

### 背景

板块列表（行业 + 概念）约 300KB 数据，每次进板块页面都需要等待 AKShare 拉取。自选股行情同理。通过在服务启动时后台异步预拉取，用户进页面时缓存已就绪。

### 步骤

- [ ] **Step 1：在 `app.py` `_startup` 中增加预热任务**

将：
```python
@app.on_event("startup")
async def _startup():
    """启动时预加载数据。"""
    init_db()
    asyncio.create_task(StockService.preload_stock_list())

    async def _preload_fund():
        """后台预加载基金 ETF 数据。"""
        try:
            from market.fund import _get_etf_df
            await _get_etf_df()
            logger.info("基金 ETF 数据预加载完成")
        except Exception as e:
            logger.warning(f"基金数据预加载失败（首次访问时重试）: {e}")

    asyncio.create_task(_preload_fund())

    # 启动定时采集调度器
    try:
        from scheduler import start_scheduler
        start_scheduler(blogger_mgr, scraper, config)
    except Exception as e:
        logger.warning(f"定时调度器启动失败: {e}")
```

替换为：
```python
@app.on_event("startup")
async def _startup():
    """启动时预加载数据。"""
    init_db()
    asyncio.create_task(StockService.preload_stock_list())

    async def _preload_fund():
        try:
            from market.fund import _get_etf_df
            await _get_etf_df()
            logger.info("基金 ETF 数据预加载完成")
        except Exception as e:
            logger.warning(f"基金数据预加载失败（首次访问时重试）: {e}")

    async def _preload_sector_boards():
        """预热板块列表（行业 + 概念），填充 L1+L2 缓存。"""
        try:
            from state import get_sector_service
            svc = get_sector_service()
            await asyncio.gather(
                svc._get_boards_single("industry"),
                svc._get_boards_single("concept"),
            )
            logger.info("板块列表预热完成")
        except Exception as e:
            logger.warning(f"板块列表预热失败（首次访问时重试）: {e}")

    async def _preload_watchlist_quotes():
        """预热自选股实时行情。"""
        try:
            from database import get_db
            from stock_service import StockService as SS
            db = get_db()
            rows = db.execute("SELECT symbol FROM watchlist LIMIT 30").fetchall()
            if not rows:
                return
            svc = SS()
            await asyncio.gather(
                *[svc.get_realtime_quote(r[0]) for r in rows],
                return_exceptions=True,
            )
            logger.info(f"自选股行情预热完成（{len(rows)} 只）")
        except Exception as e:
            logger.warning(f"自选股预热失败: {e}")

    asyncio.create_task(_preload_fund())
    asyncio.create_task(_preload_sector_boards())
    asyncio.create_task(_preload_watchlist_quotes())

    try:
        from scheduler import start_scheduler
        start_scheduler(blogger_mgr, scraper, config)
    except Exception as e:
        logger.warning(f"定时调度器启动失败: {e}")
```

- [ ] **Step 2：验证预热日志**

重启服务，观察启动日志（约 10~30 秒后）：
```
INFO  板块列表预热完成
INFO  自选股行情预热完成（X 只）
INFO  基金 ETF 数据预加载完成
```
出现这三行说明预热成功。随后访问板块页面，首屏应秒开（无需等待 AKShare）。

- [ ] **Step 3：提交**

```bash
git add wexin-read-mcp-main/src/app.py
git commit -m "perf: 启动时后台预热板块列表和自选股行情"
```

---

## 预期效果

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 板块列表（二次访问） | 1~3s（重新拉取） | <5ms（L1 内存命中） |
| K 线（5 分钟内再次访问） | 1~3s（TTL 过期重拉） | <5ms（L1 内存命中） |
| 板块列表（服务重启后首次） | 2~5s | <100ms（L2 SQLite 命中） |
| K 线（服务重启后首次） | 1~3s | <100ms（L2 SQLite 命中，1h 内有效） |
| 进板块页面（首次启动后）| 2~5s | <100ms（预热已填充缓存） |

---

## 注意事项

- **内存占用**：`_mem` dict 会随时间增长。全量板块+K线数据约 5~20MB，对个人本地部署可接受。若需限制，可在 `set` 时做简单计数（超过 500 条 evict 最旧 100 条）。
- **服务重启后 L1 清空**：重启后 L1 为空，L2（SQLite）仍有效。板块列表 TTL 30 分钟，重启后仍从 L2 快速返回（<100ms），预热任务会在后台重建 L1。
- **TTL_DAILY 不改**：`get_realtime_quote`、`get_money_flow` 等日内频繁变化的接口仍使用 TTL_DAILY=300s。
