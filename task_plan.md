# 股票博主文章分析平台 — 总任务计划

## 项目概述

基于 FastAPI + Playwright 的微信公众号文章采集分析平台，功能包括：博主管理、文章抓取、AI 分析、邮件发送、A 股行情查询、问财条件选股。

项目路径：`/Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/`

---

## 已完成任务

### 任务1: 股票服务并发性能优化 (2026-04-21)

| 阶段 | 状态 |
|------|------|
| 环境准备与基线测试 | ✅ |
| 分模块并发测试 | ✅ |
| 瓶颈识别 (search_stock 8.4s) | ✅ |
| 预加载优化 (8.4s → 0.005s，提升1690x) | ✅ |

**改动文件**: `src/stock_service.py` — 添加 `preload_stock_list()` 类方法

---

### 任务2: 微信文章抓取数据准确性修复 (2026-04-21)

**问题**: Cookie 失效时降级返回旧数据 + 标题提取不全

| 阶段 | 状态 |
|------|------|
| 禁用降级路径，Cookie 失效直接报错 | ✅ |
| refresh_all 改用 fetch_recent_articles() API | ✅ |
| 增强标题提取多重兜底 | ✅ |
| 6 个博主验证通过 | ✅ |

**改动文件**: `src/blogger.py`

---

### 任务3: 登录授权失效提醒 (2026-04-21)

| 阶段 | 状态 |
|------|------|
| 新增 `GET /api/config/mp-login-status` 端点 | ✅ |
| `refresh_all()` 返回 `login_required` 标记 | ✅ |
| 前端页面顶部红色警告 banner + "立即登录"按钮 | ✅ |

**改动文件**: `src/app.py`, `src/blogger.py`, `src/templates/index.html`

---

### 任务4: 三个问题修复 (2026-04-21)

#### 4.1 微信接口响应速度慢
**方案**: `asyncio.gather` + `Semaphore(3)` 并发限流，12-30秒 → 4-8秒

**改动文件**: `src/app.py`, `src/blogger.py`

#### 4.2 登录后顶部警告不自动消失
**方案**: 在 3 个入口点主动 `remove()` banner

**改动文件**: `src/templates/index.html`

#### 4.3 抓取遗漏"如图"等图片消息
**根因**: `appmsg` 只返回 type=9（图文），type=10002（图片消息）需要 `appmsgpublish`

**方案**: 新增 `_mp_list_published()` 方法调用 `appmsgpublish` 接口

**改动文件**: `src/blogger.py`

---

### 任务5: 灵活爬取模式 (2026-04-28)

| 阶段 | 状态 |
|------|------|
| `fetch_recent_articles()` 增加 mode/period 参数 | ✅ |
| 新增 `_filter_by_mode()` 静态方法 | ✅ |
| `_resolve_blogger_urls()` 透传模式参数 | ✅ |
| WebSocket 入口解析前端 mode 参数 | ✅ |
| 前端模式选择 UI（单选按钮 + 数字/下拉） | ✅ |

**改动文件**: `src/blogger.py`, `src/app.py`, `src/templates/index.html`

---

### 任务6: 问财条件选股模块 (2026-04-28)

| 阶段 | 状态 |
|------|------|
| 新建 `src/iwencai_service.py` | ✅ |
| 新增 5 条问财路由 | ✅ |
| 前端条件选股视图（3 tab） | ✅ |
| 前端板块行业标签云 | ✅ |
| 前端机构调研（全市场 + 个股） | ✅ |
| 博主观点交叉验证预留钩子 | ✅ |

**改动文件**: `src/iwencai_service.py` (新建), `src/app.py`, `src/templates/index.html`

---

### 任务7: Bug 修复 (2026-04-28)

| Bug | 状态 |
|------|------|
| Tab 切换无反应（CSS/JS 冲突） | ✅ |
| 板块热力图显示个股而非板块 | ✅ |
| 机构调研数据全重复 | ✅ |
| 个股调研返回 dict 而非 DataFrame | ✅ |

**改动文件**: `src/iwencai_service.py`, `src/templates/index.html`

---

### 任务8: Windows 兼容性修复 — 公众号登录异常 (2026-05-05)

**问题**: Windows 上点击"公众号登录"报 `NotImplementedError`，Playwright 无法启动浏览器。

| 阶段 | 状态 |
|------|------|
| 定位根因: uvicorn `--reload` 子进程强制 SelectorEventLoop | ✅ |
| 添加 WindowsProactorEventLoopPolicy | ✅ |
| 去掉 `--reload` 参数 | ✅ |
| 安装 Playwright headless shell 组件 | ✅ |
| Playwright 降级到 1.49.1（兼容 Python 3.13） | ✅ |

**改动文件**: `src/app.py`（事件循环策略 + 去掉 reload）、`src/scraper.py`（headless 参数调整）

---

### 任务9: 多市场板块扩展 (2026-05-05 — 进行中)

**需求**: 扩展板块扫描功能，支持基金/ETF、美股、港股、全球指数、加密货币。

**设计文档**: `docs/superpowers/specs/2026-05-05-multi-market-sectors-design.md`

---

## 核心文件索引

| 文件 | 职责 | 行数 |
|------|------|------|
| `src/app.py` | FastAPI 主应用，路由 + WebSocket | ~870 |
| `src/blogger.py` | 博主管理，文章列表获取 | ~700 |
| `src/iwencai_service.py` | 问财条件选股/板块/调研 | ~220 |
| `src/scraper.py` | Playwright 浏览器抓取文章内容 | ~220 |
| `src/config.py` | 配置管理（环境变量 + JSON） | ~166 |
| `src/analyzer.py` | AI 分析 / 纯汇总 | — |
| `src/emailer.py` | 邮件发送 | — |
| `src/stock_service.py` | A 股行情查询（AKShare + 腾讯） | ~436 |
| `src/stock_utils.py` | 股票代码工具 + TTL 缓存 | ~108 |
| `src/templates/index.html` | 前端单页应用 | ~3020 |
| `bloggers.json` | 博主数据持久化 | — |
| `user_config.json` | 用户配置持久化（非敏感） | — |

---

## API 路由总览

| 方法 | 路径 | 职责 |
|------|------|------|
| GET | `/api/stock/search` | 股票搜索 |
| GET | `/api/stock/quote/{symbol}` | 实时行情 |
| GET | `/api/stock/kline/{symbol}` | K线数据 |
| GET | `/api/stock/profile/{symbol}` | 公司简介 |
| GET | `/api/stock/financial/{symbol}` | 财务指标 |
| GET | `/api/stock/flow/{symbol}` | 资金流向 |
| GET | `/api/stock/news/{symbol}` | 个股新闻 |
| GET | `/api/stock/shareholders/{symbol}` | 十大股东 |
| POST | `/api/iwencai/query` | 条件选股 |
| GET | `/api/iwencai/sectors` | 行业板块列表 |
| GET | `/api/iwencai/sector/{name}` | 行业成分股 |
| GET | `/api/iwencai/visits/{symbol}` | 个股机构调研 |
| POST | `/api/iwencai/visits/search` | 机构调研扫描 |
