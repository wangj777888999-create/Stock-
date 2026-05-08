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
前端 SPA (templates/index.html，单文件 220K+ 字节，9 个页面)
  → FastAPI app.py → routers/ 下 10 个路由模块
    → 业务服务模块 (stock_service, blogger, analyzer 等)
      → SQLite database.py (WAL 模式，单连接，9 张表)
```

### 核心模块
| 模块 | 职责 |
|------|------|
| `stock_service.py` | A 股/港股/美股行情（腾讯 API + AKShare） |
| `global_stock_service.py` | 韩国/日本股票行情（yfinance） |
| `iwencai_service.py` | 自然语言选股查询（同花顺问财） |
| `blogger.py` | 微信博主管理 + 文章获取 |
| `scraper.py` | 基于 Playwright 的文章抓取 |
| `analyzer.py` | AI 文章分析引擎 |
| `agents/personas.py` | 3 个 AI 投资者人设（价值/成长/趋势） |
| `services/indicators.py` | 技术指标计算（RSI、MACD、KDJ、Bollinger） |
| `market/` | Provider 模式：基金 (AKShare)、加密货币 (CoinGecko)、期货 (AKShare) |
| `database.py` | SQLite 建表、初始化、增量迁移（`_migrate()`） |
| `state.py` | 全局单例状态（config、scraper、blogger_mgr） |
| `stock_utils.py` | TTL 缓存（SQLite 持久化）、股票代码解析、市场识别 |
| `http_client.py` | 共享 httpx/requests 会话，支持绕过代理 |

### 路由层 (routers/)
`stock.py`、`market.py`、`iwencai.py`、`blogger.py`、`config.py`、`watchlist.py`、`sim.py`、`journal.py`、`verify.py`、`stats.py`

### 数据库 (SQLite, data.db, 9 张表)
`cache`、`watchlist`、`portfolios`、`positions`、`backtests`、`trade_journal`、`sim_trades`、`blogger_calls`、`real_trades`。启动时自动建表，增量迁移在 `database.py:_migrate()` 中。

### 市场覆盖
A 股（腾讯 + AKShare）、港股（腾讯，30 只）、美股（腾讯，100+ 只）、韩/日（yfinance）、期货（AKShare）、ETF/基金（AKShare）、加密货币（CoinGecko Top 50）。

## 配置

- `user_config.json` — 运行时配置（SMTP 邮箱、AI API、微信凭证）。环境变量优先于文件。
- `bloggers.json` — 已关注的微信博主元数据。
- `config.py` — 配置数据类，支持环境变量。环境变量：`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`、`SMTP_*`、`WECHAT_*`。
- `financial_rules.json` — 前端财务指标高亮规则。

## 文档

项目文档为中文，位于 `docs/`：
- `docs/文档/` — 架构报告、PRD、技术方案
- `docs/规划/` — 路线图、前端重构方案、V2 设计文档
- `docs/调研/` — 竞品分析、调研报告

### 文档生成规则

用户会要求 Claude 进行调研分析并撰写文档，通常会运用 brainstorming 能力。遵循以下规则：

1. **用户指定文档类型**：用户会说明文档属于哪一类。如果未指定，主动追问文档类型和存放目录。
2. **目录映射**：
   - **调研报告**（竞品分析、技术调研、市场研究） → `docs/调研/`
   - **规划方案**（开发计划、设计方案、迭代规划） → `docs/规划/`
   - **项目文档**（架构说明、PRD、操作指南、技术规格） → `docs/文档/`
   - 如有新的文档类型，在 `docs/` 下新建目录，并更新本规则和对应目录的 README。
3. **文件命名**：`NN-简要描述.md`，NN 为两位数字序号，取该目录下当前最大序号 +1。
4. **同时更新目录索引**：生成文档后，同步更新该目录下 `README.md` 的文件索引表。

## 关键设计模式

- **Provider 模式**（`market/`）：`base.py` 定义 `MarketProvider` 抽象类，各 provider 通过 `market/__init__.py` 注册。
- **多源降级**：`call_with_fallback()` 依次尝试多个数据源。
- **TTL 缓存**：基于 SQLite，定义在 `stock_utils.py`（实时刷新 5s、实时 30s、日线 300s、公司信息 86400s）。
- **单例状态**：`state.py` 持有全局共享实例。

## 注意事项

- 整个前端是一个单独的 `index.html` 文件（220K+ 字节），正在重构中 — 参见 `docs/规划/11-前端重构改进方案.md`。
- `planning-with-files/` 是外部工具（独立 git 仓库），不属于本应用。
- 虚拟环境位于 `.venv/`（仓库根目录）。
