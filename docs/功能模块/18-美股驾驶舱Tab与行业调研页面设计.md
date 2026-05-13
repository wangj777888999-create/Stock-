# 美股驾驶舱 Tab + 行业调研页面 — 设计文档

**日期：** 2026-05-13  
**状态：** 已批准，待实现

---

## 一、背景

驾驶舱（cockpit）目前只展示 A 股市场数据（6 大指数 + 情绪面板）。本次新增两个独立功能：

1. **美股驾驶舱 Tab**：在现有驾驶舱中加入市场切换，支持 A 股 / 美股两套面板
2. **行业调研页面**：新增独立页面，基于肖璟《如何快速了解一个行业》六步框架，AI 流式生成行研报告，支持历史存档

---

## 二、美股驾驶舱 Tab

### 2.1 整体结构

驾驶舱顶部新增市场切换 Tab 条（`[A股] [美股]`），切换时 show/hide 对应面板，各面板独立维护 5 秒刷新定时器。现有 A 股面板代码零改动，只是包裹进 Tab 容器。

### 2.2 后端

**扩展 `cockpit_service.py`**，新增三个函数：

| 函数 | 数据内容 | 缓存 TTL |
|---|---|---|
| `get_us_sentiment()` | VIX 现值 + 涨跌家数（AKShare `stock_us_spot_em` 统计） | 30s |
| `get_us_indices_quotes()` | 5 大指数批量报价（腾讯 API） | 5s |
| `get_us_tick_data(code)` | 单指数分时 tick（腾讯 API） | 5s |

**扩展 `routers/cockpit.py`**，新增 3 个端点（前缀 `/api/cockpit/us/`）：

```
GET /api/cockpit/us/sentiment       → 情绪聚合数据
GET /api/cockpit/us/indices         → 5 大指数报价列表
GET /api/cockpit/us/tick/{code}     → 单指数分时 tick
```

**5 大指数（腾讯 API 代码）：**

| 指数 | 腾讯代码 | 情绪面板 |
|---|---|---|
| S&P 500 | `us.INX` | |
| NASDAQ 综合 | `us.IXIC` | |
| 道琼斯 | `us.DJI` | |
| 罗素 2000 | `us.RUT` | |
| VIX 恐慌指数 | `us.VIX` | ✓ 情绪卡片 |

> **期货扩展预留**：腾讯 API 不稳定支持 ES/NQ 合约，本期用 5 大现货指数，期货以注释形式预留在代码中。

### 2.3 前端布局

```
驾驶舱
├── Tab 切换条：[A股 ●] [美股]
│
├── #ck-panel-a（A股，现有）
│   ├── 情绪行（4卡）：涨家数 / 跌家数 / 涨停数 / 情绪指数
│   └── 指数网格（3列）：6 张 LightweightCharts 分时卡
│
└── #ck-panel-us（美股，新增，默认隐藏）
    ├── 情绪行（4卡）：VIX / 涨家数 / 跌家数 / 平家数
    └── 指数网格（3列）：5 张 LightweightCharts 分时卡
```

### 2.4 非交易时段处理

美股非交易时段腾讯 API 返回收盘价（无 tick 序列）。前端判断逻辑：
- tick 数组为空或长度 ≤ 1 → 显示「已收盘，显示昨收价」灰色提示
- 正常交易时段 → 与 A 股一致的分时图渲染

---

## 三、行业调研页面

### 3.1 数据库

在 `database.py` 的 `init_db()` 和 `_migrate()` 中新增：

```sql
CREATE TABLE IF NOT EXISTS industry_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    industry    TEXT NOT NULL,
    purpose     TEXT DEFAULT 'investment',
    report_text TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
```

### 3.2 后端

**新建 `industry_service.py`**：

- 内嵌完整行研 system prompt（六步框架 + 输出规范，来自肖璟方法论）
- `async def stream_analysis(industry, purpose)` → 调用项目 AI API（复用 `config.py` 的 `AI_API_KEY / AI_BASE_URL / AI_MODEL`），以 `stream=True` yield SSE chunk
- `save_report(industry, purpose, text)` → 写入 `industry_reports` 表

**新建 `routers/industry.py`**：

```
POST   /api/industry/analyze          → SSE 流式分析（EventSourceResponse）
GET    /api/industry/reports          → 历史报告列表（id, industry, purpose, created_at）
GET    /api/industry/reports/{id}     → 单条报告全文
DELETE /api/industry/reports/{id}     → 删除报告
```

在 `app.py` 注册 `industry` 路由。

### 3.3 System Prompt 框架（内嵌于 industry_service.py）

基于《如何快速了解一个行业》六步框架：

1. **定义行业边界**（横向层级 + 纵向产业链）
2. **判断产业生命周期**（渗透率为核心判断标准）
3. **按阶段分析**：
   - 导入期 → 可行性（商业模式、Unit Economics）
   - 成长期 → 规模性（TAM/SAM/SOM，Top-down + Bottom-up）
   - 成熟期 → 防守性（护城河四类）+ 盈利性（波特五力、产能周期）
   - 衰退期 → 替代品威胁
4. **估值逻辑**（按阶段匹配估值框架）
5. **PEST 外部因素**（催化剂 vs 压制因素）
6. **景气度跟踪指标**（量/价/利/库存/预期）

输出侧重根据 `purpose` 参数切换：
- `investment` → 生命周期 + 竞争格局 + 估值 + 景气度
- `startup` → 可行性 + 市场规模 + 护城河路径
- `career` → 生命周期 + 行业前景 + 龙头格局
- `full` → 全维度覆盖

### 3.4 前端布局

```
行业调研
├── 左栏（320px 固定）
│   ├── 输入区
│   │   ├── 行业名称输入框（placeholder: "新能源汽车、医疗器械..."）
│   │   ├── 分析目的 Radio（投资选股 / 创业选赛道 / 择业找方向 / 完整行研）
│   │   └── [开始分析] 按钮（分析中变为 [停止]）
│   └── 历史报告列表
│       └── 每条：行业名 + 时间 + [查看] [删除]
│
└── 右侧主区（flex-1）
    ├── 空态：框架说明卡片（六步流程简介）
    ├── 分析中：流式 Markdown 渲染（逐字输出）
    └── 完成后：[保存报告] 按钮 出现
```

### 3.5 流式实现细节

- 前端用 `fetch` + `ReadableStream`（而非 `EventSource`，因为需要 POST body 传参）
- 后端用 FastAPI `StreamingResponse`，`media_type="text/event-stream"`
- 每个 SSE chunk 格式：`data: <token>\n\n`
- 后端 AI 调用：复用 `http_client.get_async_client()`，`httpx` 以 `stream=True` 模式请求 OpenAI-compatible `/chat/completions`，逐行解析 SSE delta chunk
- **Markdown 渲染**：`marked.js` 目前未在 `index.html` 中引入，需在 `<head>` 中新增 CDN 引用（`https://cdn.jsdelivr.net/npm/marked/marked.min.js`）；流式输出时用 `marked.parse()` 整体更新渲染容器

### 3.6 Skill 文件

同时创建 Claude Code skill 文件，供命令行直接调用：

```
路径：~/.claude/plugins/industry-analyst/SKILL.md
内容：docx 文档内容转换为标准 skill 格式（触发词 + 六步执行流程 + 输出模板）
```

---

## 四、文件变更清单

| 文件 | 操作 |
|---|---|
| `wexin-read-mcp-main/src/cockpit_service.py` | 修改：新增 3 个美股函数 |
| `wexin-read-mcp-main/src/routers/cockpit.py` | 修改：新增 3 个 US 端点 |
| `wexin-read-mcp-main/src/industry_service.py` | **新建** |
| `wexin-read-mcp-main/src/routers/industry.py` | **新建** |
| `wexin-read-mcp-main/src/app.py` | 修改：注册 industry 路由 |
| `wexin-read-mcp-main/src/database.py` | 修改：新增 industry_reports 表 |
| `wexin-read-mcp-main/src/templates/index.html` | 修改：Tab 切换 + 美股面板 + 行业调研视图 |
| `~/.claude/plugins/industry-analyst/SKILL.md` | **新建**（skill 文件） |

---

## 五、边界与约束

- 美股期货（ES/NQ）本期不实现，代码中预留注释
- 港股 Tab 预留 Tab 位，本期不实现
- 行业调研报告无编辑功能，只读存档
- AI 分析无中途取消功能（SSE 单向流，关闭页面即停止）
- Skill 文件和 Web system prompt 内容保持一致，手动同步维护
