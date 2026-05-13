# 多角色交易验证系统 — 设计方案

## 背景

用户需要验证他人（或策略）的交易操作在市场上的表现，特别关注是否构成"反向指标"。当前系统只有单账户模拟交易，无法隔离不同来源的操作进行独立统计。

## 核心需求

1. **角色卡片**：每个人/策略一张卡片，展示关键绩效指标
2. **独立账户**：每个角色有独立的初始资金、持仓、交易历史和盈亏
3. **双输入方式**：手动录入交易 + 交割单 CSV 批量导入
4. **实时跟踪**：持仓自动关联实时行情，计算浮动盈亏
5. **自动标签**：根据胜率自动标记"反向指标 / 正向指标 / 随机漫步"
6. **后续扩展**：角色间收益对比图（暂不实现）

## 数据模型

### 新增表：`roles`

```sql
CREATE TABLE IF NOT EXISTS roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,                -- 角色名称
    avatar_color    TEXT DEFAULT '#2563EB',       -- 卡片强调色
    initial_capital REAL NOT NULL DEFAULT 100000.0, -- 初始资金
    notes           TEXT DEFAULT '',              -- 备注
    is_active       INTEGER DEFAULT 1,           -- 软删除标记
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
```

### 扩展现有表：`sim_trades` 加 `role_id`

```sql
ALTER TABLE sim_trades ADD COLUMN role_id INTEGER REFERENCES roles(id);
```

该列为可空。迁移时自动创建"默认账户"角色并将所有旧数据挂入。

### 不修改的表

- `real_trades` — 保持独立，不纳入角色系统
- `backtests`、`recommendation_scores` — 死表，不动

## API 设计

### 新路由：`routers/roles.py`，前缀 `/api/roles`

**角色 CRUD：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/roles/create` | 创建角色 |
| GET | `/api/roles/list` | 列表（含统计数据） |
| GET | `/api/roles/{id}` | 单个角色详情 |
| PUT | `/api/roles/{id}` | 编辑角色 |
| DELETE | `/api/roles/{id}` | 软删除 |

**角色内交易操作：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/roles/{id}/open` | 开仓 |
| POST | `/api/roles/{id}/close` | 平仓 |
| GET | `/api/roles/{id}/positions` | 持仓列表（含实时价） |
| GET | `/api/roles/{id}/history` | 历史交易 |
| GET | `/api/roles/{id}/stats` | 详细统计 |
| GET | `/api/roles/{id}/account` | 账户概览 |
| POST | `/api/roles/{id}/import-csv` | CSV 批量导入 |

**`GET /api/roles/list` 返回结构：**

```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "name": "老王",
      "avatar_color": "#DC2626",
      "initial_capital": 100000.0,
      "notes": "短线追涨",
      "total_trades": 15,
      "win_count": 7,
      "lose_count": 8,
      "win_rate": 0.47,
      "total_pnl": -12300.0,
      "total_pnl_pct": -12.3,
      "open_positions": 2,
      "current_equity": 87700.0,
      "avg_win": 4200.0,
      "avg_lose": -2100.0,
      "label": "反向指标"
    }
  ]
}
```

### 标签规则（`label` 字段）

| 条件 | 标签 |
|------|------|
| 胜率 ≥ 55% 且总交易 ≥ 10 | 正向指标 |
| 胜率 ≤ 35% 且总交易 ≥ 10 | **反向指标** |
| 其他（含交易数不足） | 随机漫步 |

### CSV 导入格式

列：`symbol`, `market`(可选), `direction`(默认long), `price`, `quantity`, `fee`(可选), `trade_date`, `close_price`(可选), `close_date`(可选), `note`(可选)

如果有 `close_price` 则导入为已平仓交易（自动算 PnL），否则导入为开仓。

### 向后兼容

`routers/sim.py` 的现有端点增加可选 `?role_id=` 参数。不传时默认使用第一个角色。旧模拟交易界面无感知。

## 前端设计

### 导航入口

侧边栏新增"角色验证"按钮，`switchView('roles')`。

### 角色卡片墙（主视图）

CSS Grid 自适应布局（`repeat(auto-fill, minmax(280px, 1fr))`）。每张卡片显示：
- 角色名首字头像（彩色圆形）
- 胜率、收益率（红涨绿跌）、总 PnL、持仓数
- 初始资金、当前权益
- 自动标签徽章（反向指标=红底、正向指标=绿底、随机漫步=灰底）
- "进入交易"按钮

顶部"新增角色"按钮 → 弹窗填写名称、初始资金、颜色、备注。

### 角色详情视图

点击卡片后进入，包含：
- 返回按钮
- 账户信息栏（名称、资金、权益、PnL、收益率）
- 统计卡片行（总交易、胜率、总 PnL、持仓数）
- 操作按钮（+开仓、CSV导入、编辑角色、删除角色）
- 持仓表格、历史交易表格
- 开仓弹窗、平仓弹窗、CSV 导入弹窗

样式复用现有驾驶舱/模拟交易的 CSS 变量和组件类。

## 实现步骤

1. **数据库**（`database.py`）：新增 `roles` 表，`sim_trades` 加 `role_id`，一键迁移函数
2. **角色路由**（新建 `routers/roles.py`）：完整 CRUD + 交易操作 + CSV 导入
3. **兼容旧 sim**（`routers/sim.py`）：加可选 `role_id` 参数
4. **注册路由**（`app.py`）
5. **前端**（`templates/index.html`）：视图容器 + 卡片渲染 + 详情页面 + JS 逻辑

## 验证方式

1. 启动应用，检查自动迁移是否创建"默认账户"角色
2. 旧模拟交易界面是否正常显示现有数据
3. 创建新角色，录入交易，检查各角色数据隔离
4. CSV 导入测试（含开仓和平仓两种模式）
5. 检查标签逻辑：分别创建高胜率、低胜率、少交易角色验证标签
6. 持仓表是否显示实时行情和浮动盈亏
