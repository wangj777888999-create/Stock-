# 多市场板块扩展设计

## 前置状态

以下修改**已完成并生效**，不属于本次变更范围：

| 任务 | 状态 | 改动文件 |
|------|------|---------|
| 任务8: Windows 兼容性修复（公众号登录） | ✅ 已完成 | `app.py`, `scraper.py` |
| A 股行业板块（问财 + AKShare） | ✅ 已有 | `iwencai_service.py` |
| 板块扫描前端（热力图网格 + 成分股） | ✅ 已有 | `index.html` |

**本次变更仅涉及：新增基金/ETF、美股、港股、全球指数、加密货币五个市场的板块数据和搜索功能。**

---

## 概述

在现有 A 股板块扫描基础上，扩展支持五个新市场，每个市场独立模块、独立数据源。

## 目标

- A 股板块功能**完全不动**，复用现有逻辑
- 每个新市场模块独立解耦，可单独更换数据源
- 前端在"板块扫描"tab 内通过市场切换按钮访问各市场
- 每个市场支持按代码/名称搜索

## 范围

| 市场 | 状态 | 数据深度 | 数据源 |
|------|------|---------|--------|
| A 股 | **已有，不动** | 板块 + 成分股 | AKShare + pywencai |
| 基金/ETF | **新增** | ETF 板块分类 + 成分基金列表 | AKShare `fund_etf_spot_em` |
| 美股 | **新增** | 行业板块 + 成分股 | 东方财富行业分类 API |
| 港股 | **新增** | 行业板块 + 成分股 | 东方财富行业分类 API |
| 全球指数 | **新增** | 主要指数实时行情 | AKShare + 硬编码指数代码 |
| 币圈 | **新增** | 主流币种行情 | CoinGecko 免费 API |

## 架构设计

### 1. 模块结构

```
src/market/                    ← 新建目录
├── __init__.py          新建   # 注册所有 provider，暴露 registry
├── base.py              新建   # MarketProvider 基类
├── a_share.py           新建   # 薄封装现有 IWencaiService（不改原文件）
├── fund.py              新建   # 基金/ETF
├── us_stock.py          新建   # 美股
├── hk_stock.py          新建   # 港股
├── global_index.py      新建   # 全球指数
└── crypto.py            新建   # 加密货币
```

### 2. 基类接口 (`base.py`)

```python
from abc import ABC, abstractmethod

class MarketProvider(ABC):
    name: str       # 内部标识: "a_share", "us_stock", "crypto" 等
    label: str      # 前端显示: "A股", "美股", "币圈" 等

    @abstractmethod
    async def get_boards(self) -> list[dict]:
        """返回板块列表 [{name, code, change_pct?}]"""

    @abstractmethod
    async def get_board_stocks(self, board_name: str) -> list[dict]:
        """返回板块成分股 [{code, name, price, change_pct, ...}]"""

    @abstractmethod
    async def get_spot(self) -> list[dict]:
        """返回实时行情（全球指数/币圈用）[{name, code, price, change_pct, ...}]"""

    @abstractmethod
    async def search(self, keyword: str) -> list[dict]:
        """按代码/名称搜索 [{code, name, ...}]"""
```

- 有板块概念的市场（A 股、基金、美股、港股）：`get_boards` + `get_board_stocks`
- 无板块概念的市场（全球指数、币圈）：`get_spot`
- 所有市场都实现 `search`

### 3. 数据源策略

| 模块 | 状态 | 主数据源 | 备注 |
|------|------|---------|------|
| `a_share.py` | **已有逻辑封装** | AKShare + pywencai | 调用现有 `IWencaiService`，零改动 |
| `fund.py` | 新建 | AKShare `fund_etf_spot_em` | 1446 只 ETF，37 字段，按名称分组 |
| `us_stock.py` | 新建 | 东方财富行业分类 API | 通过 httpx 请求，行业板块 + 成分股 |
| `hk_stock.py` | 新建 | 东方财富行业分类 API | 同美股方案 |
| `global_index.py` | 新建 | AKShare + 硬编码 | 纳斯达克、日经225、KOSPI、STOXX600 等 |
| `crypto.py` | 新建 | CoinGecko API | 免费无需 key，httpx 请求 |

### 4. 注册机制 (`__init__.py`)

```python
providers: dict[str, MarketProvider] = {}

def register(provider: MarketProvider):
    providers[provider.name] = provider

def get_provider(name: str) -> MarketProvider | None:
    return providers.get(name)

def list_providers() -> list[dict]:
    return [{"name": p.name, "label": p.label} for p in providers.values()]
```

启动时自动注册所有 provider。

### 5. API 路由

**修改文件**: `src/app.py` — 添加以下路由：

```
GET /api/market/markets               → 返回可用市场列表
GET /api/market/{market}/boards       → 板块列表
GET /api/market/{market}/board/{name} → 板块成分股
GET /api/market/{market}/spot         → 实时行情
GET /api/market/{market}/search?q=xxx → 搜索
```

路由根据 `market` 参数分发到对应 provider。现有路由（`/api/iwencai/*`）保持不变。

### 6. 前端交互

**修改文件**: `src/templates/index.html` — 在"板块扫描"tab 内添加市场切换栏：

```
[A股] [基金] [美股] [港股] [全球指数] [币圈]
─────────────────────────────────────────────
  🔍 搜索框（按代码/名称搜索）
─────────────────────────────────────────────
  板块网格 / 指数行情列表 / 币种行情列表
─────────────────────────────────────────────
  点击板块 → 成分股表格（也可搜索）
```

各 tab 行为：
- **A 股 tab**：直接调用现有 `/api/iwencai/*` 路由，**逻辑和 UI 完全不变**
- **基金 tab**：搜索框 + ETF 分类板块 → 点击看成分基金
- **美股/港股 tab**：搜索框 + 行业板块 → 点击看成分股
- **全球指数 tab**：搜索框 + 指数列表
- **币圈 tab**：搜索框 + 币种列表

样式沿用现有 `.heatmap-grid` 卡片风格。

### 7. 数据隔离

- 每个 provider 独立管理自己的缓存（与 `stock_utils.py` 的 TTL 缓存一致）
- 各 provider 的数据获取互不干扰，一个市场的 API 故障不影响其他市场
- 错误处理在 provider 内部完成，对外只返回成功数据或标准错误格式

### 8. 扩展性

新增一个市场只需三步：
1. 新建 `market/xxx.py` 实现 `MarketProvider`
2. 在 `__init__.py` 中 `register()`
3. 前端加一个 tab 按钮

更换数据源只需修改对应 provider 的内部实现，不影响接口和其他模块。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/market/__init__.py` | **新建** | 注册入口 |
| `src/market/base.py` | **新建** | 基类定义 |
| `src/market/a_share.py` | **新建** | 薄封装现有 IWencaiService |
| `src/market/fund.py` | **新建** | 基金/ETF provider |
| `src/market/us_stock.py` | **新建** | 美股 provider |
| `src/market/hk_stock.py` | **新建** | 港股 provider |
| `src/market/global_index.py` | **新建** | 全球指数 provider |
| `src/market/crypto.py` | **新建** | 加密货币 provider |
| `src/app.py` | **修改** | 添加 market 路由（不改现有路由） |
| `src/templates/index.html` | **修改** | 板块扫描 tab 添加市场切换 + 搜索 |

**不修改的文件**: `iwencai_service.py`, `blogger.py`, `scraper.py`, `config.py`, `stock_service.py` 等现有文件均不受影响。
