# llm-wiki-skill 知识沉淀能力调研与 StockPulse 实现分析报告

> 调研日期：2026-05-08
> 调研项目：https://github.com/sdyckjq-lab/llm-wiki-skill (1.4k Stars, MIT License)
> 对比项目：StockPulse（本项目，`wexin-read-mcp-main/`）

---

## 一、llm-wiki-skill 项目概述

### 1.1 项目定位

llm-wiki-skill 是基于 Karpathy 的 llm-wiki 方法论的个人知识库构建工具。核心理念是 **"知识编译一次，持续维护"**——拒绝传统 RAG/聊天记录模式中"每次提问都要重新读原始文件"的做法，将碎片化信息一次性编译为结构化、互相关联的 wiki 页面。

### 1.2 核心架构

```
知识库/
├── raw/                    # 不可变原始材料（按来源分类）
│   ├── articles/           # 网页文章
│   ├── tweets/             # Twitter/X
│   ├── wechat/             # 微信文章
│   ├── pdfs/               # PDF
│   └── notes/              # 笔记
├── wiki/                   # AI 生成的结构化知识
│   ├── entities/           # 实体页（人物、概念、工具）
│   ├── topics/             # 主题页
│   ├── sources/            # 来源摘要
│   ├── comparisons/        # 对比分析
│   └── synthesis/          # 综合分析 & 会话结晶
├── purpose.md              # 研究方向与目标
├── index.md                # 索引
├── .wiki-schema.md         # 配置
└── .wiki-cache.json        # SHA256 去重缓存
```

### 1.3 十个工作流

| 工作流 | 功能 |
|--------|------|
| init | 初始化知识库 |
| ingest | 单条来源 → 结构化分析 → wiki 页面生成 |
| batch-ingest | 批量文件夹处理 |
| query | 知识检索（别名扩展 + 相关性排序） |
| lint | 健康检查（孤立页面、断链、索引一致性） |
| status | 知识库统计与数据源分布 |
| digest | 深度综合报告（默认/对比表/时间线） |
| graph | Mermaid 图表 + 交互式 HTML 知识图谱 |
| delete | 级联删除（影响扫描 + 断链清理） |
| crystallize | 对话洞察结晶为持久页面 |

### 1.4 关键技术特征

| 特征 | 说明 | StockPulse 可借鉴程度 |
|------|------|---------------------|
| 知识编译模式 | 一次编译成 wiki 页面，而非每次重读原文 | ★★★★★ 高度契合 |
| 置信度标注 | EXTRACTED / INFERRED / AMBIGUOUS / UNVERIFIED 四级 | ★★★★★ 直接可用 |
| `[[双向链接]]` | 实体间互相关联，Obsidian 原生支持 | ★★★★★ 直接可用 |
| 来源溯源 | wiki 页面回链 raw 原始材料 | ★★★★☆ 需适配 |
| SHA256 去重 | 相同内容不重复处理 | ★★★★☆ 已有类似机制 |
| 会话结晶 | 对话 → 持久知识页 | ★★★★☆ 高度契合 |
| 知识图谱可视化 | 自包含 HTML 交互图谱 | ★★★☆☆ Obsidian Graph View 可替代 |
| 健康检查 | 检测孤立页面和断链 | ★★★☆☆ 后期需要 |

---

## 二、StockPulse 现有能力与差距分析

### 2.1 StockPulse 已有的知识管理能力

| 能力 | 实现方式 | 评估 |
|------|---------|------|
| 信息采集 | `scraper.py` + `blogger.py` 抓取原文 | ✅ 完善，已覆盖公众号 |
| 结构化分析 | `analyzer.py` + 3 个 AI Persona 并行分析 | ✅ 独特优势，llm-wiki 无此设计 |
| 分析结果存储 | SQLite（blogger_calls, trade_journal） | ⚠️ 结构化数据，非知识页面 |
| 交易验证 | 博主荐股跟踪 + 事后验证 | ✅ 完善 |
| 交易日志 | trade_journal 表 + 标签统计 | ✅ 结构化但非可浏览知识 |
| Obsidian 已安装 | `.obsidian/` 目录存在 | ✅ 已有基础设施 |

### 2.2 当前核心差距

**差距 1：分析结果"一次性消费"**

当前流程：`抓取文章 → AI 分析 → 生成报告 → 发邮件 → 结束`

分析报告以字符串形式存入 SQLite，无法：
- 按股票/板块/博主维度回溯历史分析
- 在实体间建立关联（哪些博主推荐过同一只股票？）
- 形成持续积累的投资知识图谱

**差距 2：缺乏置信度分层**

3 个 Persona 的输出混合在一起，无法区分：
- 博主原文明确说的（事实）→ EXTRACTED
- AI 推断的结论（推理）→ INFERRED
- 模糊表述（如"值得关注"）→ AMBIGUOUS

**差距 3：无主题聚合**

当前以"天"为单位存储分析，缺乏：
- 按股票聚合：比亚迪历史上被推荐过几次？每次分析结论是什么？
- 按板块聚合：消费板块的整体叙事演变
- 按博主聚合：某个博主的历史准确率趋势

**差距 4：会话洞察丢失**

用户与 Claude 讨论"是否加仓 XX"的关键判断没有沉淀机制，讨论结束后洞察消失。

---

## 三、可行性分析：StockPulse 知识沉淀层设计

### 3.1 定位

在现有 `analyzer.py` 输出之后，新增一个 **wiki 沉淀层**，将分析结果自动转化为 Markdown 知识页面。不替代 SQLite 结构化存储，而是作为**可浏览的知识层**与其并存。

```
现有流程：  analyzer.py → SQLite（结构化数据）→ 邮件推送
新增流程：  analyzer.py → wiki_generator.py → wiki/（Markdown 知识页面）→ Obsidian 浏览
```

### 3.2 建议的 wiki 目录结构

```
wexin-read-mcp-main/wiki/          # 项目内知识库，随项目 Git 管理
├── stocks/                        # 按股票聚合
│   ├── 比亚迪.md                  # 实体页：AI 生成的公司画像
│   ├── 贵州茅台.md
│   └── ...
├── sectors/                       # 按板块聚合
│   ├── 消费板块.md
│   ├── AI板块.md
│   └── ...
├── bloggers/                      # 按博主聚合
│   ├── 博主A.md                   # 博主画像 + 历史推荐记录 + 验证胜率
│   └── ...
├── daily/                         # 每日分析报告（保留现有输出形式）
│   ├── 2026-05-08.md
│   └── ...
├── synthesis/                     # 综合分析 & 会话结晶
│   ├── 消费复苏专题.md
│   └── ...
└── .wiki-cache.json               # 去重缓存
```

### 3.3 实体页面示例

以 `wiki/stocks/比亚迪.md` 为例：

```markdown
# 比亚迪 (002594.SZ)

> 最后更新：2026-05-08 | 来源：3 篇文章 | 综合置信度：EXTRACTED

## AI 综合画像

### 价值派视角 🏛️
> [EXTRACTED] 博主A 原文：当前 PE 18x，低于行业均值 25x
> [INFERRED] AI 推断：基于盈利增速，合理估值区间 20-25x
> [AMBIGUOUS] 博主B：「估值不算贵」（未提供数据支撑）

### 成长派视角 🌱
> [EXTRACTED] 2026Q1 营收同比增长 42%
> [INFERRED] 海外市场渗透率仍处早期，具备十倍股潜质

### 趋势派视角 📈
> [EXTRACTED] 近 20 日站稳 60 日均线，量价配合良好
> [INFERRED] 下方支撑位约 240 元

## 推荐记录

| 日期 | 博主 | 观点 | 来源 | 验证结果 |
|------|------|------|------|---------|
| 2026-04-15 | 博主A | 买入 | [[raw/bloggers/博主A/2026-04-15-xxx.md\|原文]] | ✅ +8.2% |
| 2026-03-20 | 博主B | 关注 | [[raw/bloggers/博主B/2026-03-20-xxx.md\|原文]] | ⏳ 待验证 |

## 关联

- 板块：[[sectors/新能源汽车]] | [[sectors/消费板块]]
- 同推荐：[[stocks/宁德时代]] | [[stocks/赛力斯]]
```

### 3.4 实现模块估算

| 新增模块 | 职责 | 预估代码量 |
|---------|------|-----------|
| `src/wiki_generator.py` | 核心：分析结果 → Markdown 页面生成 | ~200 行 |
| `src/wiki_entity_extractor.py` | 从分析报告中提取实体（股票/板块/人名） | ~100 行 |
| `src/routers/wiki.py` | wiki 页面 CRUD + 索引 API | ~150 行 |
| `src/templates/wiki_*.html` | 前端 wiki 浏览界面（可选） | ~300 行 |

**总计：约 750 行新增代码**，主要改动集中在 `analyzer.py` 的输出管道。

### 3.5 关键技术决策

| 决策点 | 选项 A | 选项 B | 建议 |
|--------|--------|--------|------|
| 存储位置 | 项目内 `wiki/` 目录（随 Git） | 独立目录（~/.stockpulse/wiki/） | A：保持 Obsidian Vault 一致 |
| 实体提取 | LLM 提取（准确但慢） | 正则 + 股票代码匹配（快但粗） | B 起步，A 增强 |
| 链接格式 | `[[wiki/stocks/比亚迪]]` | `[[比亚迪]]` | A：避免 Obsidian 链接歧义 |
| 生成时机 | 分析完成后自动 | 用户手动触发 | A：自动，减少操作负担 |
| 去重策略 | 按日期+内容 SHA256 | 按日期+股票+博主组合键 | B：更符合业务语义 |

---

## 四、与 Obsidian 的整合方案

### 4.1 当前状态

项目根目录已是 Obsidian Vault（`.obsidian/` 存在），`.gitignore` 已排除 `.obsidian` 目录。

### 4.2 推荐配置

**目录组织**：在 Obsidian 中使用文件夹分组，`wiki/` 目录下的子文件夹自动成为导航结构。

**推荐插件**：
- **Dataview**：统计知识库规模（如列出推荐次数最多的 Top 10 股票）
- **Graph View**（内置）：可视化股票-板块-博主关联图谱
- **Templater**：创建手动补充的 wiki 页面模板

**CSS 片段**：为四级置信度添加颜色标注
```css
/* EXTRACTED = 绿色, INFERRED = 蓝色, AMBIGUOUS = 黄色, UNVERIFIED = 红色 */
.markdown-preview-view blockquote:contains("EXTRACTED") { border-color: #4caf50; }
.markdown-preview-view blockquote:contains("INFERRED") { border-color: #2196f3; }
.markdown-preview-view blockquote:contains("AMBIGUOUS") { border-color: #ff9800; }
.markdown-preview-view blockquote:contains("UNVERIFIED") { border-color: #f44336; }
```

### 4.3 工作流

```
每日自动流程（现有 + 新增）：
1. blogger.py 抓取新文章                        ← 已有
2. analyzer.py 3 个 Persona 并行分析             ← 已有
3. wiki_generator.py 生成/更新 wiki 页面          ← 新增
4. 邮件推送报告                                  ← 已有
5. 用户在 Obsidian 浏览知识图谱、补充个人批注      ← 新增体验
```

---

## 五、实施建议

### 5.1 分阶段推进

| 阶段 | 内容 | 工作量 | 价值 |
|------|------|--------|------|
| Phase 1：MVP | `wiki_generator.py` 核心逻辑 + 每日报告自动沉淀到 `wiki/daily/` | 2-3 天 | 分析报告不再丢失 |
| Phase 2：实体聚合 | 股票/板块/博主实体页面自动生成 + `[[双向链接]]` | 3-5 天 | 知识图谱形成 |
| Phase 3：置信度标注 | AI 分析 prompt 中加入置信度指令 + 实体页面置信度块 | 2 天 | 信息可靠性可视化 |
| Phase 4：前端浏览 | wiki 页面 Web 界面（嵌入现有 SPA） | 3-5 天 | 应用内可浏览 |
| Phase 5：高级能力 | 会话结晶、知识图谱可视化、健康检查 | 按需 | 长期价值 |

### 5.2 优先级建议

**最值得立即做的**（Phase 1-2，约 1 周）：
- 每日分析报告自动写入 `wiki/daily/YYYY-MM-DD.md`
- 从分析报告中提取提到的股票，生成/更新 `wiki/stocks/xxx.md`
- 所有页面使用 `[[双向链接]]` 关联

**投入产出比最高的增量**（Phase 3，2 天）：
- 在 Persona prompt 中加入"为每个结论标注置信度"指令
- 实体页面中用 blockquote 区分不同置信度

**可以后置的**（Phase 4-5）：
- Web 前端浏览（Obsidian 已能满足）
- 知识图谱可视化（Obsidian Graph View 已能满足）
- 健康检查（wiki 规模到一定程度再做）

### 5.3 复用现有能力

| StockPulse 现有 | 如何复用 |
|----------------|---------|
| `analyzer.py` 报告输出 | 作为 wiki 页面的内容来源 |
| `agents/personas.py` 的 3 个 Persona | 扩展 prompt 加入置信度标注 |
| `blogger.py` 文章元数据 | 用于生成来源追溯链接 |
| `stock_utils.py` 代码规范化 | 用于实体页文件名和链接 |
| SQLite `blogger_calls` 表 | 用于生成推荐记录表格 |
| `.obsidian/` 已配置 | 无需额外设置 Obsidian |

---

## 六、结论

llm-wiki-skill 的核心方法论——**知识编译 + 结构化 wiki + 双向链接 + 置信度标注**——与 StockPulse 的需求高度契合。StockPulse 已具备信息采集和 AI 分析的完整能力，缺的仅是最后一公里：**将分析结果沉淀为可浏览、可追溯、可积累的知识页面**。

实现成本可控（核心约 750 行代码，1 周可交付 MVP），且大部分基础设施已就绪（Obsidian 已装、分析流程已通、数据库已有）。建议从 Phase 1-2 起步，先实现每日报告沉淀 + 股票实体页面自动生成，快速验证价值后再逐步扩展。
