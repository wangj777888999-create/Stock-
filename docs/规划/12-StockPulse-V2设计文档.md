# StockPulse V2 设计文档

> 创建日期：2026-05-08
> 状态：待审批
> 定位：求职作品集 + 个人投资自用工具

---

## 一、项目背景与目标

### 1.1 现状

StockPulse 是一个本地化的个人投资助手平台，核心工作流：监控微信博主 → 抓取文章 → AI 多角色分析 → 跟踪荐股 → 模拟/实盘交易 → 胜率统计。覆盖 7 个市场（A 股/港股/美股/韩/日/期货/加密），数据本地 SQLite 存储。

### 1.2 核心问题

- **前端**：4594 行单文件 SPA，难以维护，无法展示工程能力
- **AI 分析**：3 个角色硬编码，策略不可扩展，无容错机制
- **通知**：仅邮件 SMTP，无主动推送能力
- **差异化未释放**：微信博主×AI 多角色×交易闭环的独特组合没有形成产品化能力

### 1.3 目标

通过 4 周集中冲刺，打造一个：
1. 前端工程化（Vite + React + TS + shadcn/ui）
2. 具有差异化产品能力（博主荐股 AI 评分系统）
3. AI 分析能力升级（YAML 策略 + LLM 容错 + 交易日历）
4. 多渠道通知推送（企微 + Telegram）

---

## 二、技术栈决策

| 层 | 选型 | 理由 |
|---|---|---|
| 前端框架 | Vite + React 18 + TypeScript | 业界主流，TradingView 生态 React 更成熟，作品集展示效果好 |
| UI 组件库 | shadcn/ui + Tailwind CSS | 组件可控可定制，暗色主题天然支持，代码归自己所有 |
| 状态管理 | Zustand | 轻量，和 shadcn/ui 搭配常见，无 Redux 模板代码 |
| 后端 | 不变（FastAPI + SQLite） | 现有架构满足需求，不动 |
| 字体 | 保留 Outfit + PingFang SC | 现有设计系统 |
| 推送 | 企微 Webhook + Telegram Bot API | 纯 HTTP 调用，实现简单 |

### 2.1 视觉风格迁移原则

**迁移后视觉风格完全不变**。现有 CSS Token 系统（`--bg`, `--surface`, `--ink`, `--red/--green/--amber/--blue` 调色板、圆角阶梯、阴影系统、深色侧边栏）原封不动映射到 Tailwind theme config 和 shadcn/ui CSS 变量。改变的只是代码结构，像素级视觉效果不变。

---

## 三、模块设计

### 3.1 前端工程化

#### 目录结构

```
wexin-read-mcp-main/frontend/
├── src/
│   ├── components/
│   │   ├── ui/            # shadcn/ui 基础组件
│   │   ├── layout/        # AppShell, Sidebar, Header
│   │   ├── stock/         # K线图、报价卡、财务数据表
│   │   ├── blogger/       # 文章列表、博主卡片、荐股记录
│   │   ├── trade/         # 持仓表、开仓弹窗、盈亏统计
│   │   └── analysis/      # AI分析面板、分析结果卡片
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── Bloggers.tsx
│   │   ├── Analysis.tsx
│   │   ├── Stocks.tsx
│   │   ├── Watchlist.tsx
│   │   ├── SimTrade.tsx
│   │   ├── Journal.tsx
│   │   ├── Stats.tsx
│   │   ├── Settings.tsx
│   │   └── BloggerRanking.tsx   # 新增：博主排行榜
│   ├── hooks/             # useStock, useBlogger, useAnalysis 等
│   ├── lib/               # api client, escapeHtml, formatters
│   ├── stores/            # Zustand stores
│   └── types/             # TypeScript 类型定义
├── package.json
├── vite.config.ts
├── tailwind.config.ts
└── index.html
```

#### 关键实现细节

- **API 层**：封装 typed API client（`lib/api.ts`），与后端 FastAPI 路由一一对应
- **TradingView Charts**：用 `lightweight-charts` React 包装，封装成 `<KLineChart>` 组件
- **迁移策略**：页面逐个迁移，先做骨架布局 + 1 个页面跑通，再逐页迁移
- **代理配置**：Vite devServer proxy 转发到后端 FastAPI

---

### 3.2 博主荐股 AI 评分系统（核心差异化）

#### 数据流

```
博主文章 → AI分析 → 提取荐股列表 → 用户确认/修正
    → 存入 blogger_recommendations 表
    → 后台定时任务每日收盘后拉取价格
    → 计算四项指标 → 生成综合可信度分
    → 前端卡片排行榜展示
```

#### 数据库设计

**blogger_recommendations**

```sql
CREATE TABLE blogger_recommendations (
    id INTEGER PRIMARY KEY,
    blogger_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    direction TEXT DEFAULT 'buy',
    recommended_price REAL,
    recommended_date TEXT,
    target_price REAL,
    article_url TEXT,
    article_title TEXT,
    ai_reason TEXT,
    status TEXT DEFAULT 'active',      -- active/completed/expired
    user_confirmed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**recommendation_scores**

```sql
CREATE TABLE recommendation_scores (
    id INTEGER PRIMARY KEY,
    recommendation_id INTEGER NOT NULL,
    check_date TEXT NOT NULL,
    current_price REAL,
    return_pct REAL,
    max_gain_pct REAL,
    max_drawdown_pct REAL,
    holding_days INTEGER,
    FOREIGN KEY (recommendation_id) REFERENCES blogger_recommendations(id)
);
```

#### AI 提取增强

在现有 `analyzer.py` 中，给三个 AI 角色的 prompt 增加荐股提取输出字段：

```json
{
  "recommendations": [
    {
      "stock_code": "600519",
      "stock_name": "贵州茅台",
      "direction": "buy",
      "target_price": 1900,
      "reason": "认为当前估值合理，消费复苏预期推动"
    }
  ]
}
```

模式：**AI 提取 + 人工确认**。AI 先自动提取，前端弹出确认卡片，用户确认或修正后入库。

#### 综合评分算法

```
可信度分 = 0.35 × 命中率分 + 0.30 × 风险收益比分 + 0.20 × 跟踪深度分 + 0.15 × 样本量加权

命中率分   = 推荐后10日内最大涨幅>5%的占比，归一化到0-100
风险收益比 = 平均(最大涨幅 / |最大回撤|)，>3得满分
跟踪深度分 = 是否有后续文章更新观点（有=100，无=30）
样本量加权 = min(推荐数/20, 1.0)
```

#### 后台定时任务

每日收盘后（15:30）运行：
1. 查询所有 `status='active'` 的荐股记录
2. 调用 `stock_service` 获取最新价格
3. 计算各项指标，写入 `recommendation_scores`
4. 超过 30 天的荐股自动标记为 `completed`

使用 `APScheduler` 或 FastAPI 后台任务实现。

#### 前端设计

**卡片（排行榜页）**

```
┌─────────────────────────────┐
│  [头像]   半夏投资            │
│           82分    平均+6.4%   │
└─────────────────────────────┘
```

- 网格布局，桌面端一行 3-4 个
- 头像取公众号头像，名字取博主名称
- 评分用蓝色，收益率正绿负红
- hover 上浮阴影，点击进入详情页

**详情页**

顶部：博主头像 + 名称 + 综合评分 + 四项指标柱状图（命中率 / 风险收益比 / 跟踪深度 / 样本量）

中下部：荐股记录列表，每条显示：
- 股票名/代码、推荐日期、推荐价、当前价、收益率
- 持有天数、目标价、AI 提取的理由
- 点击可跳转到该股票 K 线页面，标注推荐点位

---

### 3.3 AI 分析能力升级

#### 3.3.1 YAML 策略 DSL

现状：3 个角色硬编码在 `agents/personas.py`。改为 YAML 文件驱动：

```yaml
# strategies/value_investor.yaml
name: 价值投资
persona_alias: 巴菲特视角
system_prompt: |
  你是巴菲特风格的价值投资者。重点分析企业护城河、ROE稳定性、估值合理性。
required_data: [fundamentals, financials, kline]
output_fields: [recommendation, confidence, target_price, reasoning]
scoring_rules:
  - field: pe_ratio
    condition: "< 15"
    adjustment: +10
  - field: roe
    condition: "> 15%"
    adjustment: +8
```

- 前端新增"策略管理"页面，支持新增/编辑/禁用策略
- 新增策略 = 新增一个 YAML 文件，无需改代码
- `analyzer.py` 加载策略目录，动态构建 prompt

#### 3.3.2 LLM 输出容错

引入 `json-repair` 库：

```python
from json_repair import repair_json

def safe_parse_llm_response(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = repair_json(raw)
        return json.loads(repaired)
```

#### 3.3.3 交易日历集成

引入 `exchange-calendars` 库：

```python
import exchange_calendars as xcals

def is_trading_day(market: str = 'XSHG') -> bool:
    cal = xcals.get_calendar(market)
    now = pd.Timestamp.now(tz='Asia/Shanghai')
    return cal.is_session(now)
```

非交易日跳过行情请求和分析。

---

### 3.4 通知与事件监控

#### 3.4.1 推送渠道插件架构

```
src/notifications/
├── base.py          # 抽象基类 Notifier
├── email.py         # 现有邮件（从 emailer.py 迁移）
├── wecom.py         # 企微机器人（Webhook）
├── telegram.py      # Telegram Bot API
└── dispatcher.py    # 统一调度
```

配置扩展：

```python
@dataclass
class NotificationConfig:
    enabled_channels: list[str] = field(default_factory=lambda: ["email"])
    wecom_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
```

初期只做企微 + Telegram，两者都是纯 HTTP 调用。

#### 3.4.2 事件监控引擎

| 事件类型 | 触发条件 | 推送内容 |
|---------|---------|---------|
| 价格预警 | 自选股价格穿越预警价 | "贵州茅台 现价 1742，突破预警价 1700" |
| 荐股更新 | 关注博主发布新荐股文章 | "半夏投资 推荐了 长江电力，目标价 28" |
| 涨跌幅异动 | 自选股单日涨跌超 N% | "宁德时代 今日 -7.2%，触发异动提醒" |
| 博主评分变化 | 博主可信度评分升降超阈值 | "半夏投资 评分从 82 降至 71" |

后台定时检查：价格事件每 5 分钟，文章更新每小时。

#### 3.4.3 推送消息格式

统一 Markdown 格式，各渠道适配：

```
 **价格预警触发**
股票：贵州茅台 (600519)
当前价：¥1742 | 预警价：¥1700
涨幅：+2.5%
时间：2026-05-08 14:32
```

---

## 四、执行计划与弹性机制

### 4.1 四周冲刺计划

| 周次 | 主线任务 | 弹性插槽 |
|------|---------|---------|
| 第1周 | 前端工程化：项目搭建 + 骨架布局 + 2-3个核心页面迁移 | 2天 |
| 第2周 | 博主荐股评分系统：数据表 + AI提取 + 后台跟踪 + 卡片页 | 2天 |
| 第3周 | AI分析升级 + 通知推送：YAML策略 + LLM容错 + 交易日历 + 企微/Telegram | 2天 |
| 第4周 | 联调 + 收尾 + README更新 | 全周弹性 |

### 4.2 弹性机制运作方式

- 每周末回顾进度，评估新想法是否纳入下周主线或排入插槽
- 某阶段提前完成 → 插槽自动变为"新功能探索时间"
- 新想法记录在 `docs/规划/` 下，标注优先级和影响范围
- 重大方向调整需更新本文档后继续

---

## 五、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 前端迁移工作量超预期 | 第1周延期，挤压后续 | 先迁移 2-3 个核心页面即可，剩余页面后续迭代 |
| AKShare/腾讯行情 API 变动 | 荐股价格跟踪失败 | 现有双源回退机制 + 数据源错误日志 |
| AI 提取荐股准确率不足 | 评分数据质量差 | "AI提取+人工确认"机制兜底 |
| LLM API 不稳定 | 分析流程中断 | LLM 容错 + json-repair，非阻塞 |
| 企微/Telegram API 配置复杂 | 通知功能无法上线 | 先确保邮件通知可用，企微/Telegram 作为可选增强 |

---

## 六、验收标准

### 6.1 前端工程化
- [ ] Vite + React + TS 项目搭建完成，开发服务器可启动
- [ ] 现有 9 个页面至少迁移 3 个为 React 组件
- [ ] 视觉风格与原版一致（CSS Token 映射完成）
- [ ] TradingView K 线图在 React 中正常渲染

### 6.2 博主荐股评分系统
- [ ] 数据库表创建完成
- [ ] AI 分析流程可提取荐股信息
- [ ] 用户可确认/修正荐股记录
- [ ] 后台定时任务每日更新价格
- [ ] 博主排行榜卡片页可展示
- [ ] 详情页展示四项指标和荐股明细

### 6.3 AI 分析升级
- [ ] YAML 策略文件可加载和使用
- [ ] LLM 返回格式错误时自动修复
- [ ] 非交易日自动跳过行情获取

### 6.4 通知与事件监控
- [ ] 企微机器人可推送消息
- [ ] Telegram Bot 可推送消息
- [ ] 价格预警事件可触发推送
- [ ] 新荐股事件可触发推送
