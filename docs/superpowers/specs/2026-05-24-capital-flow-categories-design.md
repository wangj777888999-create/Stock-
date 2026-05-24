# 自定义分类资金流向模块 — 设计文档

**日期：** 2026-05-24  
**状态：** 待审阅

---

## 背景与目标

用户需要自定义股票分类（如"机器人"、"半导体"、"MLCC PCB"），并对分类内的所有股票进行资金流向（小单/中单/大单/超大单）的汇总观察，以判断某一主题方向的整体资金动向。

现有系统已有 `stock_service.get_money_flow(symbol)` 接口（A 股，东方财富数据），可获取单支股票近 20 天的分单类资金流数据，本模块在此基础上增加分类管理和聚合展示层。

---

## 范围

- **支持市场：** 仅 A 股（现有资金流接口限制）
- **不涉及：** 港股、美股、期货
- **不修改：** 现有自选股（watchlist）、板块（sector）模块

---

## 数据模型

### 新增 DB 表（在 `database.py` 中添加，支持增量迁移）

```sql
-- 用户自定义分类
CREATE TABLE IF NOT EXISTS flow_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 分类内的股票
CREATE TABLE IF NOT EXISTS flow_category_stocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES flow_categories(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,   -- A股代码，如 "300024"
    name        TEXT,            -- 股票名称（添加时写入，避免每次查询）
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(category_id, symbol)
);
```

---

## 后端 API

**新文件：** `wexin-read-mcp-main/src/routers/flow_category.py`  
**路由前缀：** `/api/flow-category`  
**注册：** 在 `app.py` 中 include_router

### 分类管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/list` | 获取所有分类（含每类股票列表） |
| POST | `/create` | 创建分类 `{name: str}` |
| PUT | `/{id}` | 修改分类名 `{name: str}` |
| DELETE | `/{id}` | 删除分类（级联删除股票） |

### 股票管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/{id}/stocks` | 添加股票 `{symbol: str, name: str}` |
| DELETE | `/{id}/stocks/{symbol}` | 从分类中移除股票 |

### 资金流数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/{id}/flow` | 获取分类的汇总资金流向 |

**`GET /{id}/flow` 查询参数：**
- `period`: `1`（今日）/ `3`（近3日累计）/ `5`（近5日累计），默认 `1`

**响应结构：**
```json
{
  "success": true,
  "data": {
    "category_id": 1,
    "category_name": "机器人",
    "period": 1,
    "stocks": [
      {
        "symbol": "300024",
        "name": "机器人",
        "super_large_net": 1.1,
        "large_net": 0.3,
        "medium_net": -0.1,
        "small_net": -0.2,
        "main_net": 1.4,
        "trend": [1, 1, -1, 1, 1]
      }
    ],
    "total": {
      "super_large_net": 2.5,
      "large_net": 0.8,
      "medium_net": -0.3,
      "small_net": -0.5,
      "main_net": 3.3
    }
  }
}
```

### 数据处理逻辑（`flow_category.py` 内部）

1. 取出分类内所有股票
2. `asyncio.gather()` 并发调用 `stock_service.get_money_flow(symbol)` （已有缓存，30s TTL）
3. 解析"亿"字符串为 float（`1.5亿` → `1.5`，`-0.3亿` → `-0.3`）
4. 取前 N 条数据（period=1/3/5），对各字段累加求和
5. 计算 trend：取近 5 日主力净流入正负方向 → `[1, 1, -1, 1, 1]`
6. 如某股票请求失败，返回该股票的错误标记，不影响其他股票

---

## 前端设计

**文件：** `wexin-read-mcp-main/src/templates/index.html`

### 入口（侧边栏）

```html
<button class="nav-item" data-view="flowCategory" onclick="switchView('flowCategory', this)">
  <!-- 资金追踪 图标 + 文字 -->
</button>
```

### 主视图：分类卡片总览

- `<div id="view-flowCategory" class="view">` 容器
- 网格布局（2列或自适应），每个分类一张卡片
- **卡片内容：**
  - 分类名称 + 股票数量
  - 主力净流入总额（大字，绿/红色）
  - 超大/大/中/小单净流入各值
  - 迷你横条图（比例可视化，颜色区分四种单类）
  - 编辑 ✏️ / 删除 🗑 按钮（hover 显示）
- **末尾：** "+ 新建分类"卡片

### 明细视图：点击卡片展开

- 返回按钮 + 分类名 + 股票数
- 时间切换 Tab：今日 / 3日 / 5日（切换后重新请求）
- 数据表格：
  | 股票 | 超大单净流入 | 大单净流入 | 中单净流入 | 小单净流入 | 主力净流入 | 近5日趋势 |
  |------|------|------|------|------|------|------|
- 合计行固定在底部
- "添加股票" 按钮（打开 Modal）

### Modals

1. **新建/重命名分类 Modal**：输入框 + 确认/取消
2. **添加股票 Modal**：
   - 搜索框复用 `/api/stock/search` 接口（已有自动完成逻辑）
   - 搜索结果列表，点击添加
   - 显示已添加股票列表（可删除）
3. **删除分类确认 Modal**：提示将同时删除分类内所有股票记录

### 数据刷新

- 进入主视图时自动拉取所有分类数据（不含流向，先展示骨架）
- 每张卡片异步请求 `GET /{id}/flow?period=1`
- 在明细页切换 Tab 时请求对应 period
- 不做自动轮询（资金流数据已有 30s 缓存，用户手动刷新即可）

---

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `src/database.py` | 新增 `flow_categories` + `flow_category_stocks` 表，加入 `_migrate()` |
| `src/routers/flow_category.py` | 新建路由文件（分类 CRUD + 股票 CRUD + 流向聚合） |
| `src/app.py` | include_router(flow_category_router) |
| `src/templates/index.html` | 新增侧边栏按钮 + 视图容器 + JS 逻辑 |

**复用（不修改）：**
- `stock_service.get_money_flow(symbol)` — 流向数据获取
- `/api/stock/search` — 股票搜索自动完成

---

## 验证方案

1. 启动 `python app.py`，确认新 router 注册成功（`/docs` 页面可见接口）
2. 创建分类"测试机器人"，添加 2-3 只 A 股
3. 访问 `GET /api/flow-category/list` 确认返回正确
4. 访问 `GET /api/flow-category/1/flow?period=1` 确认流向聚合数值合理
5. 访问 `GET /api/flow-category/1/flow?period=5` 确认多日累计正确
6. 打开前端，切换到"资金追踪"，确认卡片渲染、明细展开、Modal 操作全部正常
7. 删除分类，确认关联股票一并删除
