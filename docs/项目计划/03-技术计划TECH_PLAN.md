# 股票信息平台 - 整体技术规划与改进方案

| 项目 | 内容 |
|------|------|
| 项目名称 | StockPulse (wexin-read-mcp-main) |
| 文档版本 | v2.0 |
| 创建日期 | 2026-04-21 |
| 修改日期 | 2026-05-12 |
| 状态 | 持续迭代中 |

---

## 一、项目现状总览

### 1.1 当前技术架构

```
前端 SPA (templates/index.html，5541 行)
  ↓
FastAPI app.py (176 行，纯路由组装)
  ↓
routers/ (12 个独立路由模块)
  ↓
业务服务层
  ├── stock_service.py    (1011 行) A/H/U 股行情、K 线、财务
  ├── sector_service.py   (590 行)  A 股板块行情（三源降级）
  ├── blogger.py          (723 行)  博主管理 + 文章采集
  ├── analyzer.py         (317 行)  多视角 AI 分析
  ├── scheduler.py        (184 行)  定时采集调度
  └── market/             (基金/期货/加密)
  ↓
基础设施层
  ├── database.py         SQLite WAL 模式，11 张表
  ├── stock_utils.py      L1(内存) + L2(SQLite) 双层缓存
  ├── http_client.py      全局连接池 + 代理绕过
  └── state.py            全局单例状态
```

### 1.2 代码规模统计（2026-05-12 实际）

| 模块 | 行数 | 职责 |
|------|------|------|
| stock_service.py | 1011 | A/H/U 股行情、K 线、财务、技术指标 |
| blogger.py | 723 | 博主 CRUD、文章获取、HTML 解析 |
| sector_service.py | 590 | 板块列表、成分股、K 线 |
| analyzer.py | 317 | 多视角 AI 分析 |
| global_stock_service.py | 255 | 韩/日股（yfinance） |
| stock_utils.py | 256 | 双层缓存、代码工具 |
| scraper.py | 234 | Playwright 爬虫 |
| database.py | 223 | SQLite 建表/迁移 |
| iwencai_service.py | 200 | 问财选股 |
| scheduler.py | 184 | 定时调度 |
| **app.py** | **176** | **Web 入口（已拆分）** |
| config.py | 165 | 配置管理 |
| routers/（12 模块）| ~800 | 路由层 |
| index.html | 5541 | Web UI 单文件 |

---

## 二、已完成的改进（历史记录）

### ✅ Phase 0：敏感信息保护

- `.gitignore` 已创建，`user_config.json` 不提交
- 所有敏感配置（SMTP 密码、AI Key、微信 Cookie）通过环境变量读取
- API 响应自动脱敏

### ✅ Phase 2：性能优化（连接池 + 缓存持久化）

**HTTP 连接池**（`http_client.py`）：
- 全局 `requests.Session`，`trust_env=False` 绕过 VPN 代理
- `patch_requests()` 同时 patch `requests.get/post` 和 `requests.Session` 类（覆盖 AKShare 内部新建 Session 的路径）

**双层缓存**（`stock_utils.py`）：
- L1：进程内 dict + threading.Lock，< 0.1ms 命中
- L2：SQLite `cache` 表，持久化，重启不丢失，5~20ms
- TTL 分 6 档：REALTIME_REFRESH 5s / REALTIME 30s / DAILY 300s / KLINE 3600s / BOARDS 1800s / COMPANY 86400s

**启动预热**（`app.py _startup`）：
- 并发预拉取基金 ETF、板块列表（行业+概念）、自选股行情
- 首次访问命中缓存，无需等待 AKShare

### ✅ Phase 3：路由解耦（app.py 拆分）

app.py 从 750 行拆分为 12 个独立路由模块：

| 路由模块 | 功能 |
|------|------|
| `routers/stock.py` | 股票查询、K 线、财务 |
| `routers/market.py` | 基金/期货/加密货币 |
| `routers/iwencai.py` | 问财选股 |
| `routers/blogger.py` | 博主管理 API |
| `routers/config.py` | 配置管理 API |
| `routers/watchlist.py` | 自选股 |
| `routers/sim.py` | 模拟交易 + 回测 |
| `routers/journal.py` | 交易日记 |
| `routers/verify.py` | 喊单验证 + 实盘记录 |
| `routers/stats.py` | 博主胜率统计 |
| `routers/articles.py` | 文章管理 |
| `routers/sector.py` | A 股板块行情 |

### ✅ Phase 5：SQLite 数据库

`database.py` 实现 11 张表，WAL 模式，单连接，`busy_timeout=5000`，增量迁移（`_migrate()`）：

| 表名 | 用途 |
|------|------|
| cache | TTL 缓存持久化 |
| watchlist | 自选股列表 |
| portfolios | 投资组合 |
| positions | 持仓记录 |
| backtests | 回测结果 |
| trade_journal | 交易日记 |
| sim_trades | 模拟交易流水 |
| blogger_calls | 博主喊单记录 |
| real_trades | 实盘交易记录 |
| recommendation_scores | 推荐评分统计 |
| scraped_articles | 抓取文章存储 |

---

## 三、待完成的改进

### Phase 4：内容安全（🟡 中等优先级）

**目标**：防止 AI Prompt 注入 + XSS

```python
# analyzer.py - AI Prompt 输入净化
def _sanitize_for_prompt(text: str) -> str:
    import html
    text = html.escape(text)
    text = text.replace("```", "").replace("---", "")
    if len(text) > 50000:
        text = text[:50000] + "\n...[内容已截断]"
    return text
```

### Phase 6：API 认证（🟡 中等优先级）

**目标**：防止未授权访问

```python
# 新建 middleware/auth.py
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(key: str = Security(api_key_header)):
    if key != os.getenv("API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return key
```

### Phase 7：单元测试（🟡 中等优先级）

**目标**：核心业务逻辑可测

优先覆盖：
- `stock_utils.py` 缓存逻辑（L1/L2 命中、TTL 过期）
- `sector_service.py` 三源降级逻辑
- `stock_service.py` 市场识别、代码规范化

### Phase 8：前端模块化（🟢 长期）

当前 `index.html` 5541 行，按 5 阶段重构：
1. 安全加固（XSS 防护）
2. 内存泄漏修复
3. JS 代码组织（模块化）
4. CSS 规范化
5. 工程化（构建工具）

详见 `09-前端重构改进方案.md`。

---

## 四、实施状态总览

| 阶段 | 内容 | 状态 | 完成时间 |
|------|------|------|------|
| **Phase 0** | .gitignore + 敏感信息保护 | ✅ 已完成 | 2026-04 |
| **Phase 1** | 环境变量配置 | ✅ 已完成 | 2026-04 |
| **Phase 2** | 连接池 + 双层缓存 + TTL 分级 + 预热 | ✅ 已完成 | 2026-05-12 |
| **Phase 3** | app.py 拆分为 12 个路由模块 | ✅ 已完成 | 2026-05 |
| **Phase 4** | 内容安全（Prompt 净化）| 🔲 待开始 | — |
| **Phase 5** | SQLite 数据库（11 张表）| ✅ 已完成 | 2026-05 |
| **Phase 6** | API 认证机制 | 🔲 待开始 | — |
| **Phase 7** | 单元测试 | 🔲 待开始 | — |
| **Phase 8** | 前端模块化 | 🔲 进行中 | — |

---

## 五、验收标准

### Phase 0-1 验收 ✅

- [x] `.gitignore` 包含 `user_config.json`
- [x] 敏感配置可通过环境变量读取
- [x] 现有功能正常

### Phase 2 验收 ✅

- [x] 全局 Session 统一管理，trust_env=False 绕过 VPN 代理
- [x] requests.Session 类同时被 patch（覆盖 AKShare 内部新建 Session）
- [x] L1 内存缓存命中无磁盘 I/O
- [x] L2 SQLite 缓存持久化，重启后数据保留
- [x] TTL 分 6 档，K 线 1h，板块 30min，行情 5min
- [x] 启动预热：基金/板块/自选股并发预拉取

### Phase 3 验收 ✅

- [x] app.py 从 750 行缩减至 176 行
- [x] 12 个独立路由模块，职责清晰
- [x] WebSocket 任务管道保留在 app.py

### Phase 5 验收 ✅

- [x] 11 张表覆盖完整业务数据
- [x] WAL 模式 + busy_timeout 防锁
- [x] `_migrate()` 支持增量迁移

### Phase 4 待验收

- [ ] AI prompt 含用户内容时不被注入
- [ ] 文章内容渲染前经过 XSS 过滤

### Phase 6 待验收

- [ ] 未携带 X-API-Key 的请求返回 403
- [ ] 环境变量 `API_KEY` 控制认证开关

---

## 六、风险评估

| 风险 | 影响 | 概率 | 应对 |
|------|------|------|------|
| 无认证 API 被扫描滥用 | 高 | 中（本地部署低风险） | Phase 6 优先实现 |
| index.html 继续膨胀 | 中 | 高 | 控制新增 JS，推进前端重构 |
| AKShare 接口变更 | 中 | 中 | 三源降级架构缓解 |
| SQLite 并发写冲突 | 低 | 低 | WAL 模式 + busy_timeout 已处理 |
