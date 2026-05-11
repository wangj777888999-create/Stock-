# StockPulse — 交易感知系统

> 从一个微信文章阅读器，演变成的全栈个人投资信息平台。

## 这是什么？

StockPulse 是一个**本地部署的个人投资助手**，核心思路是：

**监控微信公众号博主 → 抓取文章 → AI 多视角分析 → 跟踪推荐股票 → 模拟/实盘交易 → 统计胜率**

它不只是一个看行情的工具，而是一套完整的「**信息获取 → 分析判断 → 交易验证**」闭环系统。所有数据存储在本地 SQLite，不依赖任何云服务。

## 解决什么痛点？

| 痛点 | StockPulse 的方案 |
|------|------------------|
| 关注的股票博主分散在不同公众号，手动翻看效率低 | 批量监控博主，一键抓取最新文章 |
| 文章太多没时间逐篇读 | AI 自动分析文章要点，支持多投资视角（价值/成长/趋势） |
| 博主推荐的股票涨跌无法追踪验证 | 博主喊单记录 + 事后验证，用数据说话 |
| 想回测某个想法但没有工具 | 内置模拟交易系统，10万虚拟本金，支持做多做空 |
| 行情数据分散在不同 App | 一个平台覆盖 A 股、港股、美股、韩股、日股、期货、ETF、加密货币 |
| 各种财经 App 广告多、数据不透明 | 本地部署，零广告，数据来源完全可控 |

## 功能全景

### 信息采集层

- **博主监控**：通过微信公众号后台 API 或读者 Cookie 批量监控股票博主
- **文章抓取**：Playwright 浏览器自动化 + 反检测机制，稳定抓取文章全文
- **扫码登录**：WebSocket 实时推送二维码，前端扫码即可登录微信公众号后台

### AI 分析层

- **多角色分析**：同一文章由 3 个 AI 投资人格分别分析
  - 价值派（巴菲特风格）—— 看基本面、护城河
  - 成长派（林奇风格）—— 看增速、行业空间
  - 趋势派 —— 看资金流向、技术形态
- **报告生成**：自动汇总为结构化报告，支持一键发送到邮箱
- **可配置 AI**：兼容任何 OpenAI 格式的 API（GPT-4、Claude、DeepSeek 等）

### 行情数据层

- **A 股/港股/美股**：实时行情 + K 线（1分钟 ~ 月线）+ 技术指标（RSI、MACD、KDJ、布林带）
- **韩股/日股**：通过 yfinance 覆盖，支持日/周/月 K 线
- **基金/ETF**：11 个板块分类，K 线 + 持仓明细
- **期货**：金属/农产品/能化/金融/贵金属板块，K 线 + 龙虎榜
- **加密货币**：CoinGecko 数据源，市值 Top 50
- **问财查询**：自然语言选股（如"市盈率低于10的银行股"）

### 交易验证层

- **自选股**：支持标签、目标价、提醒价，拖拽排序，批量刷新行情
- **模拟交易**：做多/做空，实时浮动盈亏，胜率统计
- **博主喊单跟踪**：记录博主推荐的股票、价格、目标价，事后验证涨跌
- **实盘交易记录**：手动录入或 CSV 导入，关联博主来源
- **交易日记**：每笔交易记录理由、反思、标签，支持按标签统计盈亏
- **统计面板**：模拟 + 实盘合并统计，月度盈亏、胜率分析

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│                   前端 SPA (4500+ 行)                  │
│   index.html — 深色侧边栏 + 浅色内容区，9 个页面        │
│   Lightweight Charts (TradingView) 渲染 K 线          │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────┴──────────────────────────────┐
│              FastAPI + Uvicorn (app.py)               │
│                                                       │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐ │
│  │ stock   │ │ blogger │ │  sim    │ │   stats     │ │
│  │ market  │ │ config  │ │ journal │ │   verify    │ │
│  │ iwencai │ │watchlist│ │         │ │             │ │
│  └────┬────┘ └────┬────┘ └────┬────┘ └──────┬──────┘ │
│       │           │           │              │        │
│  ┌────┴────┐ ┌────┴─────┐ ┌──┴──────┐ ┌────┴─────┐  │
│  │stock_   │ │blogger   │ │database │ │analyzer  │  │
│  │service  │ │scraper   │ │(SQLite) │ │emailer   │  │
│  │global_  │ │parser    │ │WAL mode │ │personas  │  │
│  │stock_   │ │          │ │9 tables │ │          │  │
│  │service  │ │          │ │         │ │          │  │
│  └────┬────┘ └──────────┘ └─────────┘ └──────────┘  │
│       │                                               │
│  ┌────┴──────────────────────────────────────────┐   │
│  │          Market Providers (插件式)              │   │
│  │  Fund(Sina+同花顺) │ Futures(Sina) │ Crypto    │   │
│  └───────────────────────────────────────────────┘   │
│                                                       │
│  外部数据源:  腾讯行情API / AKShare / yfinance /       │
│              pywencai / CoinGecko / 微信公众号API      │
└───────────────────────────────────────────────────────┘
```

### 数据源策略

所有行情接口采用**双源容灾**：

```
主数据源 (腾讯/新浪) ──失败──▶ 备选数据源 (AKShare/同花顺)
      │                              │
      ▼                              ▼
   返回数据                       返回数据
      │                              │
      └──── 写入 SQLite 缓存 ────────┘
```

系统代理（如 VPN）会干扰 Python requests 库，项目通过 monkey-patch 和环境变量清理确保国内数据源直连。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | Python 3.14, FastAPI, Uvicorn |
| 前端 | 原生 HTML/CSS/JS (SPA), Lightweight Charts |
| 数据存储 | SQLite (WAL 模式) |
| 浏览器自动化 | Playwright (Chromium) |
| 行情数据 | AKShare, yfinance, pywencai, 腾讯/新浪 API |
| AI 分析 | OpenAI 兼容 API (可配置) |
| MCP 协议 | FastMCP (Claude Desktop 集成) |
| 邮件 | SMTP (支持 163/QQ/Gmail) |

## 项目结构

```
src/
├── app.py                    # FastAPI 主入口
├── server.py                 # MCP 服务入口 (Claude Desktop)
├── config.py                 # 配置管理 (邮件/AI/微信)
├── database.py               # SQLite 连接 + 建表
├── stock_service.py          # A股/港股/美股行情服务
├── global_stock_service.py   # 韩国/日本股票 (yfinance)
├── iwencai_service.py        # 问财自然语言选股
├── stock_utils.py            # 代码标准化 + 缓存
├── scraper.py                # Playwright 文章抓取
├── blogger.py                # 博主管理 + 微信 API
├── analyzer.py               # AI 文章分析
├── emailer.py                # 邮件发送
├── agents/personas.py        # 3 种投资人格
├── services/indicators.py    # RSI/MACD/KDJ/布林带
├── market/                   # 插件式行情 Provider
│   ├── base.py               # 抽象基类
│   ├── fund.py               # ETF/基金 (新浪+同花顺)
│   ├── futures.py            # 期货 (新浪)
│   └── crypto.py             # 加密货币 (CoinGecko)
├── routers/                  # API 路由
│   ├── stock.py              # 股票查询
│   ├── market.py             # 多市场板块
│   ├── iwencai.py            # 问财
│   ├── blogger.py            # 博主 + WebSocket
│   ├── config.py             # 配置
│   ├── watchlist.py          # 自选股
│   ├── sim.py                # 模拟交易
│   ├── journal.py            # 交易日记
│   ├── verify.py             # 喊单验证 + 实盘记录
│   └── stats.py              # 统计面板
└── templates/index.html      # 前端 SPA (9 个页面)
```

## 快速开始

### 环境要求

- Python 3.10+
- macOS / Linux / Windows

### 安装

```bash
# 1. 克隆项目
git clone <repo-url>
cd wexin-read-mcp-main

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器 (文章抓取需要)
playwright install chromium

# 4. 启动服务
cd src
python -m uvicorn app:app --host 0.0.0.0 --port 8000

# 5. 打开浏览器
# http://localhost:8000
```

### 配置

启动后进入「配置设置」页面：

| 配置项 | 说明 |
|--------|------|
| SMTP 邮件 | 填写发件邮箱，用于接收 AI 分析报告 |
| AI API | 填写 API Key、Base URL、模型名，用于文章分析 |
| 微信 Cookie | 扫码登录或手动粘贴，用于抓取公众号文章 |

也可通过环境变量配置（优先级高于配置文件）：

```bash
export SMTP_SERVER=smtp.163.com
export SMTP_EMAIL=your@163.com
export SMTP_PASSWORD=your_password
export AI_API_KEY=sk-xxx
export AI_BASE_URL=https://api.openai.com/v1
export AI_MODEL=gpt-4o-mini
```

### 作为 MCP 工具使用 (Claude Desktop)

在 Claude Desktop 配置中添加：

```json
{
  "mcpServers": {
    "weixin-reader": {
      "command": "python",
      "args": ["/path/to/src/server.py"]
    }
  }
}
```

之后在 Claude 中直接说"帮我总结这篇文章：<URL>"即可。

## 设计亮点

### 1. 插件式行情 Provider

`market/` 目录使用抽象基类 + 注册机制，新增一个市场（如债券、期权）只需：
1. 继承 `MarketProvider`
2. 实现 `get_boards()` / `get_board_stocks()` / `search()`
3. 在 `__init__.py` 注册

无需改动任何现有代码。

### 2. 双源容灾 + 智能降级

行情数据永远不会因为单一数据源挂掉而不可用：
- A 股实时行情：腾讯 API → AKShare
- 基金 ETF：新浪 → 同花顺
- K 线数据：腾讯 API → AKShare

每条数据都有 TTL 缓存（30s ~ 24h 按数据类型），避免重复请求。

### 3. 多角色 AI 分析

不是让 AI 给一个泛泛的总结，而是让 3 个不同投资风格的 AI 分别分析，最后汇总：
- 避免单一视角的偏见
- 价值派看估值、成长派看增速、趋势派看资金
- 最终报告覆盖多个维度

### 4. 博主验证闭环

市面上的股票博主推荐股票从来不验证，这个系统强制形成闭环：
```
博主推荐 → 记录喊单(价格/时间/方向) → 事后对比实际走势 → 统计胜率
```
用数据告诉你：哪些博主值得关注，哪些是反向指标。

### 5. 全市场覆盖

一个平台覆盖 7 个市场：A 股、港股、美股、韩股、日股、期货、加密货币 + ETF 基金，不用在多个 App 之间切换。

## 未来规划

- **回测引擎**：基于历史数据的策略回测，验证交易策略有效性
- **智能提醒**：价格突破目标价 / 博主发新文章 / 财报发布时推送通知
- **持仓分析**：导入实际持仓，计算组合收益、行业分布、风险敞口
- **多用户支持**：从单机工具升级为可分享的服务
- **移动端适配**：响应式布局，手机上也能用
- **更多数据源**：接入东方财富 Choice、Wind 等专业数据源

## 适用人群

- 有自己关注的股票博主，想系统性跟踪其观点和胜率的投资者
- 需要一个不被广告和推荐算法干扰的独立行情工具的交易者
- 想用 AI 辅助分析但不想把数据交给第三方的用户
- 需要模拟交易环境来验证想法的个人投资者
- 开发者：学习 FastAPI + Playwright + SQLite 全栈开发的参考项目

## 注意事项

- 本项目仅供个人学习和研究使用
- 请遵守微信公众平台服务条款，不要高频爬取
- 模拟交易数据不构成投资建议
- 所有数据存储在本地 `data.db`，注意备份
