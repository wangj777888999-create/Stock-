# 03 - Claude for Financial Services 调研报告

> 调研日期：2026-05-17
> 来源：[GitHub anthropics/financial-services](https://github.com/anthropics/financial-services)
> 许可证：Apache 2.0 | Stars：23.9k | Forks：3.3k

---

## 项目定位

Anthropic 官方发布的**金融服务领域参考实现**，提供面向投行、股票研究、私募、财富管理等垂直场景的 Agent 集合、技能插件和数据连接器。同一套系统提示词和技能可部署在 Claude Cowork UI、Claude Code CLI 或 Managed Agents API（无头模式）三个环境中。

---

## 核心架构：三层模型

```
┌──────────────────────────────────────────────┐
│  AGENTS（命名工作流，11 个）                  │
│  Pitch Agent / Market Researcher /            │
│  GL Reconciler / Month-End Closer / ...       │
└──────────────────────────────────────────────┘
              ↓ 调用
┌──────────────────────────────────────────────┐
│  VERTICAL PLUGINS（垂直插件，含技能+命令）    │
│  financial-analysis（核心）                   │
│  investment-banking / equity-research /       │
│  private-equity / wealth-management /         │
│  fund-admin / operations                      │
└──────────────────────────────────────────────┘
              ↓ 接入数据
┌──────────────────────────────────────────────┐
│  MCP CONNECTORS（11 个数据源）                │
│  Daloopa / Morningstar / S&P Global /         │
│  FactSet / Moody's / LSEG / PitchBook / ...   │
└──────────────────────────────────────────────┘
```

---

## Agent 生态（11 个）

| 类别 | Agent | 核心工作流 | 输出物 |
|------|-------|-----------|--------|
| 咨询覆盖 | **Pitch Agent** | 可比交易/LBO 分析 | 带品牌的完整 Pitch Deck |
| 咨询覆盖 | **Meeting Prep Agent** | 会前研究 | 简报包 |
| 研究建模 | **Market Researcher** | 行业/主题分析 | 行业综述 + 同业比较 |
| 研究建模 | **Earnings Reviewer** | 财报电话 + 公告 | 模型更新 + 研报草稿 |
| 研究建模 | **Model Builder** | 多模型构建 | DCF / LBO / 三报表（Excel） |
| 基金运营 | **Valuation Reviewer** | GP 投资包 → 估值 | LP 报告就绪材料 |
| 基金运营 | **GL Reconciler** | 科目差异追踪 | 根因分析 + 审批路由 |
| 基金运营 | **Month-End Closer** | 月末关账流程 | 计提 / 滚动表 / 差异说明 |
| 基金运营 | **Statement Auditor** | LP 对账单审计 | 分配就绪对账单 |
| 运营合规 | **KYC Screener** | 开户文件解析 | 规则引擎标记 + 缺口识别 |

---

## 垂直插件及命令速览

### 核心：financial-analysis
所有垂直共用的建模层，包含 11 个 MCP 连接器：

| 命令 | 功能 |
|------|------|
| `/comps` | 交易可比（Trading Multiples vs. 同业） |
| `/dcf` | DCF 模型（含 WACC、敏感性分析） |
| `/lbo` | 杠杆收购模型 |
| `/3-statement-model` | 完整三报表模型 |
| `/debug-model` | Excel 公式审计 + 硬编码检测 |

### investment-banking
`/cim` / `/teaser` / `/buyer-list` / `/merger-model` / `/process-letter` / `/deal-tracker`

### equity-research
`/earnings` / `/earnings-preview` / `/initiate` / `/model-update` / `/morning-note` / `/sector` / `/thesis` / `/catalysts` / `/screen`

### private-equity
`/source` / `/screen-deal` / `/dd-checklist` / `/ic-memo` / `/portfolio` / `/value-creation` / `/ai-readiness`

### wealth-management
`/client-review` / `/financial-plan` / `/rebalance` / `/client-report` / `/proposal` / `/tlh`（税损收割）

---

## MCP 数据连接器（11 个）

| 数据源 | 数据类型 |
|--------|---------|
| Daloopa | 市场 / 公司数据 |
| Morningstar | 基金 / ETF |
| S&P Global (Kensho) | Capital IQ 数据 |
| FactSet | 股票研究 / 预测 |
| Moody's | 信用评级 / 分析 |
| MT Newswires | 市场新闻 |
| Aiera | 事件情报 |
| LSEG (Refinitiv) | 债券 / 外汇 / 期权数据 |
| PitchBook | PE / VC 数据 |
| Chronograph | PE SaaS 指标 |
| Egnyte | 文档存储 |

---

## 技术栈

| 维度 | 详情 |
|------|------|
| 语言 | Python 80% / Shell 9.9% / JavaScript 5.7% / PowerShell 4.4% |
| AI 引擎 | Claude 模型（Anthropic API） |
| 集成协议 | MCP（Model Context Protocol） |
| 部署接口 | Claude Managed Agents API (`/v1/agents`) |
| 配置格式 | Markdown + YAML（无需构建步骤） |
| 无头生成 | 支持 Excel（xlsx-author）/ PPT（pptx-author）无头输出 |
| M365 集成 | Excel / PowerPoint / Word / Outlook 插件 |

---

## 三种部署方式

### 方式一：Claude Cowork（UI）
```
Settings → Plugins → Add plugin
→ 粘贴：https://github.com/anthropics/financial-services
→ 从市场选择所需 Agent / 垂直插件
```

### 方式二：Claude Code（CLI）
```bash
claude plugin marketplace add anthropics/financial-services
claude plugin install financial-analysis@claude-for-financial-services
claude plugin install pitch-agent@claude-for-financial-services
claude plugin install equity-research@claude-for-financial-services
```

### 方式三：Managed Agents API（无头/生产环境）
```bash
export ANTHROPIC_API_KEY=sk-ant-...
scripts/deploy-managed-agent.sh gl-reconciler
```

部署脚本自动完成：解析文件引用 → 上传技能 → 创建子 Agent → POST 到 `/v1/agents`。

---

## 扩展与定制

### 替换数据连接器
编辑任意垂直插件的 `.mcp.json`，指向内部系统：
```json
{
  "mcpServers": {
    "your-internal-db": {
      "command": "python3",
      "args": ["../path/to/your/mcp_server.py"]
    }
  }
}
```

### 注入机构上下文
直接在技能 Markdown 中添加机构标准、术语规范、格式模板。

### 新增 Agent
1. 创建 `plugins/agent-plugins/<slug>/agents/<slug>.md`（系统提示词）
2. 在 `plugins/agent-plugins/<slug>/skills/` 组合所需技能
3. 创建对应 `managed-agent-cookbooks/<slug>/`
4. 运行 `python3 scripts/sync-agent-skills.py`

---

## 重要限制与合规声明

> ⚠️ **本项目不构成投资、法律、税务或会计建议。所有 Agent 输出均为草稿，须经有资质的专业人员审核后方可使用。**

Agent **不会**：
- 做出投资推荐
- 执行交易
- 绑定风险敞口
- 过账分录
- 批准开户申请

使用方负责：输出验证、适用法规合规、模型假设准确性、数据治理与安全。

---

## 与 StockPulse 的整合价值

| 本项目现有模块 | 可借鉴方向 |
|--------------|-----------|
| `analyzer.py`（AI 文章分析） | 参考 equity-research 垂直插件的 `/earnings` / `/thesis` 技能设计，优化分析提示词结构 |
| `agents/personas.py`（3 个 AI 人设） | 参考多 Agent 子 Agent 委托模式（callable_agents），实现人设间互相调用 |
| `market/` Provider 模式 | 参考 11 个 MCP 连接器设计，规范化对接 AKShare / CoinGecko / 腾讯 API |
| `routers/`（REST API） | 参考 Managed Agents API 事件循环（`orchestrate.py`）设计异步任务调度 |
| 模拟/实盘交易 | 参考 `/rebalance` / `/tlh` 技能设计调仓建议与税损规则引擎 |
| 胜率统计模块 | 参考 `/portfolio` / `/value-creation` 的 KPI 追踪设计持仓监控 dashboard |

**最高价值参考**：`scripts/orchestrate.py` 的多 Agent 编排事件循环，以及技能系统的 Markdown+YAML 纯文件配置模式（零构建、热更新），值得在 StockPulse 重构中借鉴。

---

## 目录结构

```
financial-services/
├── plugins/
│   ├── agent-plugins/          # 11 个命名 Agent
│   ├── vertical-plugins/       # 垂直插件（含技能+MCP 配置）
│   │   ├── financial-analysis/ # 核心（共用建模层）
│   │   ├── investment-banking/
│   │   ├── equity-research/
│   │   ├── private-equity/
│   │   ├── wealth-management/
│   │   ├── fund-admin/
│   │   └── operations/
│   └── partner-built/          # LSEG、S&P Global
├── managed-agent-cookbooks/    # 各 Agent 无头部署模板
├── claude-for-msft-365-install/ # M365 插件管理工具
├── scripts/
│   ├── deploy-managed-agent.sh
│   ├── orchestrate.py          # 多 Agent 编排参考实现
│   ├── sync-agent-skills.py    # 技能同步
│   └── check.py                # Lint + 校验
└── CLAUDE.md
```

---

## 参考资料

- [GitHub - anthropics/financial-services](https://github.com/anthropics/financial-services)
- [Anthropic 官方文档 - Managed Agents API](https://docs.anthropic.com)
