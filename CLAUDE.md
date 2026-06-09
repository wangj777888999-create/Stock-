# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作提供指引。

## 项目概述

StockPulse (交易感知系统) — 本地部署的个人投资信息平台。核心流程：监控微信公众号博主 → 抓取文章 → AI 多视角分析 → 跟踪推荐股票 → 模拟/实盘交易 → 胜率统计。

## 常用命令

```bash
# 启动 Web 服务（在 wexin-read-mcp-main/src/ 下）
python app.py

# 启动 MCP 服务
python server.py

# 安装依赖（在 wexin-read-mcp-main/ 下）
pip install -r requirements.txt

# 运行性能测试（在仓库根目录）
python benchmark_stock.py
python benchmark_stock_optimized.py
python benchmark_all_modules.py
```

项目目前没有测试套件，也没有 CI/CD、Docker 或 Makefile。

## 架构

所有应用代码位于 `wexin-read-mcp-main/src/` 下。

### 双入口
- **`app.py`** — FastAPI Web 服务（端口 8000），提供 SPA 前端和 REST API
- **`server.py`** — FastMCP 服务，用于 Model Context Protocol 集成

### 请求链路
```
前端 SPA (templates/index.html，单文件 437K 字节 / 8923 行，9+ 页面)
  → FastAPI app.py → routers/ 下 22 个路由模块
    → 业务服务模块 (stock_service, cockpit_service, blogger, analyzer 等)
      → services/ 数据路由层 (DataRouter 契约 + 多源 race + stale 兜底)
        → SQLite database.py (WAL 模式，单连接，21 张表)
```

### 核心模块
| 模块 | 职责 |
|------|------|
| `stock_service.py` | **Facade**（~33 行）：聚合 `stock/` 子包的 4 个 Mixin 为 `StockService`，保持 `from stock_service import StockService` 向后兼容 |
| `stock/` | A 股/港股/美股数据服务子包：`quote`（搜索/实时报价/股票列表）、`kline`（K线+聚合）、`fundamental`（公司信息/财务/公告/股东）、`flow`（资金流/新闻） |
| `services/quote_parser.py` | 腾讯报价解析纯函数（`_QT_URL`/`_parse_tencent_quote`，零依赖底层模块，斩断 providers↔stock_service 循环依赖） |
| `cockpit_service.py` | 大盘驾驶舱（A股/美股指数、涨跌家数、资金流、热门股聚合） |
| `sector_service.py` | A 股板块（行业/概念排名、成分股，多级缓存预热） |
| `signal_service.py` | 信号层（强势股、热门榜、概念/行业排名底层抓取） |
| `industry_service.py` | 行业调研（AI 流式分析 + 报告存档） |
| `global_stock_service.py` | 韩国/日本股票行情（yfinance） |
| `iwencai_service.py` | 自然语言选股查询（同花顺问财） |
| `blogger.py` | 微信博主管理 + 文章获取 |
| `scraper.py` | 基于 Playwright 的文章抓取 |
| `scheduler.py` | 定时采集调度器（启动时挂起，后台抓取博主文章） |
| `article_storage.py` | 抓取文章持久化（scraped_articles 表） |
| `analyzer.py` | AI 文章分析引擎（多篇博主文章 → 结构化投资分析报告 + 提及股票抽取） |
| `financial_service.py` | 财报分析（A/美股关键指标 → AI 四维度解读） |
| `sentiment_service.py` | 个股情绪雷达（价量/资金/热度/博主/舆情 五维评分） |
| `emailer.py` | SMTP 邮件推送 |
| `services/data_router.py` | **数据路由内核**：契约注册 + EWMA 延迟排序 + 并发 race + stale 兜底 + 健康面板 |
| `services/providers.py` | 数据源注册中心（所有契约的候选源在此集中注册） |
| `services/eastmoney.py` | 东财公司信息/财务取数（作为 DataRouter `company_profile`/`financial` 契约的备用源） |
| `services/mootdx_provider.py` | mootdx 行情源 |
| `services/indicators.py` | 技术指标计算（RSI、MACD、KDJ、Bollinger） |
| `market/` | Provider 模式：基金 (AKShare)、加密货币 (CoinGecko)、期货 (AKShare) |
| `database.py` | SQLite 建表、初始化、增量迁移（`_migrate()`） |
| `state.py` | 全局单例状态（config、scraper、blogger_mgr、sector_svc） |
| `stock_utils.py` | 两层缓存（L1 内存 + L2 SQLite）、股票代码解析、市场识别 |
| `http_client.py` | 共享 httpx/requests 会话，支持绕过代理 |

### 路由层 (routers/，22 个)
`stock`、`market`、`iwencai`、`blogger`、`config`、`watchlist`、`sim`、`journal`、`verify`、`stats`、`articles`、`roles`、`sector`、`analysis`、`cockpit`、`signal`、`industry`、`flow_category`、`review`、`financial`、`sentiment`、`health`

### 数据库 (SQLite, data.db, 21 张表)
`database.py` 建表（16）：`cache`、`watchlist`、`portfolios`、`positions`、`backtests`、`trade_journal`、`sim_trades`、`blogger_calls`、`real_trades`、`recommendation_scores`、`scraped_articles`、`chart_drawings`、`stock_notes`、`custom_pattern_rules`、`roles`、`industry_reports`。
`_migrate()` 增量建表（5）：`flow_categories`、`flow_category_stocks`、`review_notes`、`review_drawings`、`financial_reports`。
启动时自动建表，增量迁移在 `database.py:_migrate()` 中。

### 市场覆盖
A 股（腾讯 + AKShare）、港股（腾讯，30 只）、美股（腾讯，100+ 只）、韩/日（yfinance）、期货（AKShare）、ETF/基金（AKShare）、加密货币（CoinGecko Top 50）。

## 配置

- `user_config.json` — 运行时配置（SMTP 邮箱、AI API、微信凭证）。环境变量优先于文件。
- `bloggers.json` — 已关注的微信博主元数据。
- `config.py` — 配置数据类，支持环境变量。环境变量：`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`、`SMTP_*`、`WECHAT_*`。
- `financial_rules.json` — 前端财务指标高亮规则。

## 文档

项目文档为中文，位于 `docs/`，分三个目录：
- `docs/调研/` — 调研报告（竞品分析、技术选型、市场研究、能力差距分析）
- `docs/项目计划/` — 项目整体规划、产品方向、技术路线、里程碑、进度记录
- `docs/功能模块/` — 各功能模块的设计方案、实现细节、问题分析、优化记录

### 文档生成规则

用户会要求 Claude 进行调研分析并撰写文档，通常会运用 brainstorming 能力。遵循以下规则：

1. **用户指定文档类型**：用户会说明文档属于哪一类。如果未指定，主动追问文档类型和存放目录。
2. **目录映射**：
   - **调研报告**（竞品分析、技术调研、市场研究、能力差距分析） → `docs/调研/`
   - **项目计划**（整体规划、产品方向、技术路线、里程碑、进度日志） → `docs/项目计划/`
   - **功能模块**（具体功能的设计方案、实现细节、问题根因、优化方案） → `docs/功能模块/`
   - 不使用 `docs/superpowers/` 或其他工具专属目录存放业务文档。
3. **文件命名**：`NN-简要描述.md`，NN 为两位数字序号，取该目录下当前最大序号 +1。
4. **同时更新目录索引**：生成文档后，同步更新该目录下 `README.md` 的文件索引表。

## 关键设计模式

- **DataRouter 契约路由**（`services/data_router.py` + `providers.py`，**推荐**）：业务调用 `router.fetch("契约名", **params)`，内部并发跑所有注册源、按 EWMA 历史延迟排序、首个成功即返回、自动缓存 + stale 兜底 + 健康面板（`/api/health/sources`）。新增源只需在 `providers.py` 加函数 + `register_provider`。
- **Provider 模式**（`market/`）：`base.py` 定义 `MarketProvider` 抽象类，各 provider 通过 `market/__init__.py` 注册。
- **TTL 缓存**：两层（L1 进程内 dict + L2 SQLite 持久化），定义在 `stock_utils.py`（实时刷新 5s、实时 30s、日线 300s、公司信息 86400s）。
- **单例状态**：`state.py` 持有全局共享实例。
- **降级机制**：已统一为 `DataRouter.fetch`（旧的 `source_racer`、`call_with_fallback` 均已移除）。数据取数一律走 DataRouter 契约。详见 `docs/项目计划/12-全项目架构审查与调优报告.md`。

## 注意事项

- 整个前端是一个单独的 `index.html` 文件（437K 字节 / 8923 行），正在重构中 — 参见 `docs/项目计划/09-前端重构改进方案.md`。
- 配置安全：敏感字段（邮箱密码、微信 Cookie/Token、AI api_key）**只从环境变量读取，永不写入 JSON 文件**。
- `planning-with-files/` 是外部工具（独立 git 仓库），不属于本应用。
- `docs/文档/`、`docs/superpowers/`、`docs/learning/` 为历史游离/工具产物目录，不属于"调研/项目计划/功能模块"三目录体系，新文档勿入。
- 虚拟环境位于 `.venv/`（仓库根目录）。
