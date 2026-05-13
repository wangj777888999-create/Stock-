# 行业调研页面实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「行业调研」页面，用户输入行业名称 + 分析目的，后端以肖璟六步框架为 system prompt 调用 AI API（SSE 流式），前端实时渲染 Markdown 报告；报告可保存至 SQLite 并在历史列表中查看。同时创建 `~/.claude/plugins/industry-analyst/SKILL.md` 供 Claude Code 命令行直接调用。

**Architecture:** 新建 `industry_service.py`（内嵌 system prompt + httpx stream 调用）和 `routers/industry.py`（4 个端点：流式分析 POST + 报告 CRUD）；`database.py` 追加 `industry_reports` 表；`app.py` 注册路由；`index.html` 新增 nav 项、视图 HTML、CSS、JS（fetch + ReadableStream + marked.js 渲染）。

**Tech Stack:** FastAPI `StreamingResponse`, httpx (stream mode), OpenAI-compatible API, `marked.js` CDN, SQLite, Vanilla JS

---

## 文件清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `wexin-read-mcp-main/src/database.py` | 修改 | 新增 industry_reports 表 |
| `wexin-read-mcp-main/src/industry_service.py` | **新建** | system prompt + AI SSE 流 + 报告存档 |
| `wexin-read-mcp-main/src/routers/industry.py` | **新建** | 4 个端点 |
| `wexin-read-mcp-main/src/app.py` | 修改 | 注册 industry 路由 |
| `wexin-read-mcp-main/src/templates/index.html` | 修改 | marked.js + nav + CSS + HTML + JS |
| `~/.claude/plugins/industry-analyst/SKILL.md` | **新建** | Claude Code 命令行 skill |

---

## Task 1: database.py — 新增 industry_reports 表

**文件:** Modify `wexin-read-mcp-main/src/database.py`

- [ ] **Step 1: 在 executescript 中（roles 表之后，约第 235 行的 `"""` 结束前）插入新表 DDL**

找到：
```python
            CREATE TABLE IF NOT EXISTS roles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                avatar_color    TEXT DEFAULT '#2563EB',
                initial_capital REAL NOT NULL DEFAULT 100000.0,
                notes           TEXT DEFAULT '',
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );
        """)
```

替换为：
```python
            CREATE TABLE IF NOT EXISTS roles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                avatar_color    TEXT DEFAULT '#2563EB',
                initial_capital REAL NOT NULL DEFAULT 100000.0,
                notes           TEXT DEFAULT '',
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS industry_reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                industry    TEXT NOT NULL,
                purpose     TEXT DEFAULT 'investment',
                report_text TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );
        """)
```

- [ ] **Step 2: 验证表创建成功**

```bash
cd wexin-read-mcp-main/src && python -c "
from database import init_db, get_db
init_db()
db = get_db()
rows = db.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='industry_reports'\").fetchall()
print('industry_reports exists:', len(rows) > 0)
"
```

期望输出：`industry_reports exists: True`

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/database.py
git commit -m "feat(db): 新增 industry_reports 表"
```

---

## Task 2: industry_service.py — 创建服务层（system prompt + 流式 AI 调用）

**文件:** Create `wexin-read-mcp-main/src/industry_service.py`

- [ ] **Step 1: 创建文件**

```python
"""行业调研服务 — 基于肖璟六步框架的 AI 流式分析 + 报告存档。"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncGenerator

from database import get_db
from http_client import get_async_client
from state import config

logger = logging.getLogger("industry-service")

# ── 行研 System Prompt（肖璟《如何快速了解一个行业》六步框架）──

_PURPOSE_LABELS = {
    "investment": "投资选股",
    "startup": "创业选赛道",
    "career": "择业找方向",
    "full": "完整行研报告",
}

_PURPOSE_FOCUS = {
    "investment": "重点输出：产业生命周期阶段 + 竞争格局 + 估值逻辑 + 景气度跟踪指标",
    "startup": "重点输出：可行性分析（商业模式/Unit Economics）+ 市场规模（TAM/SAM/SOM）+ 护城河构建路径",
    "career": "重点输出：产业生命周期阶段 + 行业前景与薪资水平 + 龙头公司格局",
    "full": "输出完整框架，所有六个维度全覆盖",
}

_SYSTEM_PROMPT = """你是一位专业行业分析师，严格运用肖璟《如何快速了解一个行业》的六步框架进行行业分析。

## 六步分析框架

### 第一步：定义行业边界
- 横向维度：明确行业层级（参考申万行业分类/GICS），一句话说明"本次分析的行业定义是……（含哪些环节/不含哪些）"
- 纵向维度：梳理产业链上下游（原材料→中间品→终端产品→渠道→用户），标注本次重点分析的环节

### 第二步：判断产业生命周期
用渗透率作为核心判断标准（而非时间或增速斜率）：
- 导入期（渗透率<10%）：产品/服务尚未被大众接受
- 成长期（渗透率10%-50%）：快速扩张，竞争格局未定
- 成熟期（渗透率>50%）：增长放缓，竞争加剧，格局趋稳
- 衰退期：替代品出现，需求萎缩
给出当前所处阶段及核心依据（渗透率数据或类比）

### 第三步：按阶段聚焦分析

**3A 可行性分析（导入期必做）**
- 需求：是真实需求还是伪需求？用户愿意付费吗？
- 供给/商业模式：能卖出去（获客成本/转化率/留存）？能赚到钱（毛利率/Unit Economics，LTV>3×CAC）？能规模复制？
- 结论：商业模式可行/有条件可行/不可行

**3B 规模性分析（成长期必做）**
- TAM（潜在市场）/ SAM（可服务市场）/ SOM（3-5年可获得市场）
- 测算：自上而下（总市场×渗透率）+ 自下而上（单价×用户数）
- 三情景预测：高/中/低

**3C 防守性分析（成熟期必做）**
- 护城河类型：成本优势/网络效应/无形资产（品牌/专利/牌照）/转换成本
- 宽度判断：宽（多种壁垒叠加）/窄（单一壁垒）/无（同质化竞争）

**3D 盈利性分析（成熟期必做）**
- 产能周期演化与竞争格局（供不应求→进入→产能激增→出清→寡头）
- 波特五力×议价能力分析
- 财务指标验证：毛利率（定价权）、应收/应付周转（占用上下游能力）、ROE

### 第四步：估值逻辑
按生命周期阶段匹配估值框架：
- 导入期：PS/EV-GMV（营收倍数）
- 成长期：PEG（增长调整后市盈率）
- 成熟期：PE/DCF（现金流折现）
- 衰退期：PB/清算价值
基础公式：市值=净利润×PE，倍数由赔率（基本面）×概率（确定性）决定

### 第五步：PEST 外部因素
分析当前正在成为催化剂或压制因素的外部变量：
- P（政策）：监管趋势、补贴/限制
- E（经济）：利率、汇率、消费能力
- S（社会）：人口结构、消费偏好、ESG
- T（技术）：替代技术威胁、降本增效机会

### 第六步：景气度跟踪指标
设计一套高频跟踪体系：
- 量：销量/出货量/订单
- 价：出厂价/原材料价格/终端价
- 利：毛利率/净利率趋势
- 库存：渠道库存周转天数
- 预期：PMI/行业景气调查
推荐数据来源（国家统计局/行业协会/上市公司财报/Wind等）

## 输出规范

报告结构：
1. 行业定义与范围
2. 产业生命周期判断（现处阶段及依据）
3. [按阶段]核心分析维度
4. 外部因素（PEST）
5. 估值参考
6. 景气度跟踪指标
7. 综合结论与风险提示（包含"若XX发生，结论需修正"的可证伪条件）

重要原则：
- 模糊的正确>精确的错误：市场规模给合理区间，不追求精确数字
- 结论要可证伪：附上修正条件
- 动态视角：说明分析时点，结论需定期更新"""


async def stream_analysis(industry: str, purpose: str) -> AsyncGenerator[str, None]:
    """调用 AI API（SSE 流式），逐 token yield，格式为 SSE data 行。"""
    if not config.ai.api_key:
        yield "data: [ERROR] 未配置 AI API Key，请在系统配置中填写\n\n"
        return
    if not config.ai.base_url:
        yield "data: [ERROR] 未配置 AI Base URL\n\n"
        return

    purpose_label = _PURPOSE_LABELS.get(purpose, "投资选股")
    focus = _PURPOSE_FOCUS.get(purpose, _PURPOSE_FOCUS["investment"])
    user_prompt = f"""请对「{industry}」进行行业分析。

分析目的：{purpose_label}
{focus}

请严格按照六步框架逐步分析，输出结构化的 Markdown 报告。"""

    url = f"{config.ai.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.ai.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.ai.model or "gpt-4o",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 8192,
        "stream": True,
    }

    try:
        client = get_async_client()
        async with client.stream("POST", url, headers=headers, json=payload, timeout=120.0) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:].strip()
                if chunk == "[DONE]":
                    yield "data: [DONE]\n\n"
                    return
                try:
                    data = json.loads(chunk)
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        # 转义 SSE 换行
                        yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except Exception as e:
        logger.error(f"AI 流式调用失败: {e}")
        yield f"data: [ERROR] AI 调用失败: {e}\n\n"


def save_report(industry: str, purpose: str, report_text: str) -> int:
    """保存报告到 industry_reports 表，返回新记录 id。"""
    db = get_db()
    cur = db.execute(
        "INSERT INTO industry_reports (industry, purpose, report_text) VALUES (?, ?, ?)",
        (industry, purpose, report_text),
    )
    db.commit()
    return cur.lastrowid


def list_reports(limit: int = 50) -> list[dict]:
    """列出历史报告（不含正文）。"""
    db = get_db()
    rows = db.execute(
        "SELECT id, industry, purpose, created_at FROM industry_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict | None:
    """获取单条报告全文。"""
    db = get_db()
    row = db.execute(
        "SELECT id, industry, purpose, report_text, created_at FROM industry_reports WHERE id=?",
        (report_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_report(report_id: int) -> bool:
    """删除报告，返回是否找到并删除。"""
    db = get_db()
    deleted = db.execute(
        "DELETE FROM industry_reports WHERE id=?", (report_id,)
    ).rowcount
    db.commit()
    return deleted > 0
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd wexin-read-mcp-main/src && python -c "import industry_service; print('OK, system prompt length:', len(industry_service._SYSTEM_PROMPT))"
```

期望输出：`OK, system prompt length:` 后跟一个大于 1000 的数字

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/industry_service.py
git commit -m "feat(industry): 创建服务层 — 六步框架 system prompt + SSE 流式分析 + 报告 CRUD"
```

---

## Task 3: routers/industry.py — 创建路由层

**文件:** Create `wexin-read-mcp-main/src/routers/industry.py`

- [ ] **Step 1: 创建文件**

```python
"""行业调研路由 — 流式分析 + 报告 CRUD。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import industry_service

router = APIRouter(prefix="/api/industry", tags=["行业调研"])

_VALID_PURPOSES = {"investment", "startup", "career", "full"}


class AnalyzeRequest(BaseModel):
    industry: str
    purpose: str = "investment"


@router.post("/analyze")
async def api_industry_analyze(req: AnalyzeRequest):
    """流式行业分析（SSE）。"""
    if not req.industry.strip():
        raise HTTPException(status_code=400, detail="行业名称不能为空")
    if req.purpose not in _VALID_PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose 必须是 {_VALID_PURPOSES} 之一")
    return StreamingResponse(
        industry_service.stream_analysis(req.industry.strip(), req.purpose),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/reports")
async def api_industry_save(industry: str, purpose: str, report_text: str):
    """保存分析报告。"""
    if not industry.strip() or not report_text.strip():
        raise HTTPException(status_code=400, detail="行业名称和报告内容不能为空")
    report_id = industry_service.save_report(industry.strip(), purpose, report_text)
    return {"success": True, "id": report_id}


@router.get("/reports")
async def api_industry_list_reports():
    """获取历史报告列表（不含正文）。"""
    return {"success": True, "data": industry_service.list_reports()}


@router.get("/reports/{report_id}")
async def api_industry_get_report(report_id: int):
    """获取单条报告全文。"""
    report = industry_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"success": True, "data": report}


@router.delete("/reports/{report_id}")
async def api_industry_delete_report(report_id: int):
    """删除报告。"""
    ok = industry_service.delete_report(report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"success": True}
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd wexin-read-mcp-main/src && python -c "from routers.industry import router; print('routes:', [r.path for r in router.routes])"
```

期望输出包含 `/api/industry/analyze`、`/api/industry/reports` 等路径

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/routers/industry.py
git commit -m "feat(industry): 创建路由层 — 流式分析 POST + 报告 CRUD"
```

---

## Task 4: app.py — 注册路由

**文件:** Modify `wexin-read-mcp-main/src/app.py`

- [ ] **Step 1: 在 signal_router 注册之后（约第 100 行）追加 industry 路由注册**

找到：
```python
from routers.signal import router as signal_router
app.include_router(signal_router)
```

在其后追加：
```python
from routers.industry import router as industry_router
app.include_router(industry_router)
```

- [ ] **Step 2: 启动服务确认端点已注册**

```bash
cd wexin-read-mcp-main/src && python app.py
```

访问 `http://localhost:8000/docs`，搜索 `industry`，确认 5 个端点出现在文档中。

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/app.py
git commit -m "feat(industry): 在 app.py 注册行业调研路由"
```

---

## Task 5: index.html — marked.js + 导航项 + CSS

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 `<head>` 中添加 marked.js（约第 9 行 LightweightCharts script 之后）**

找到：
```html
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
```

在其后追加：
```html
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
```

- [ ] **Step 2: 在侧边栏导航「角色验证」按钮之后（约第 1214 行）添加行业调研导航项**

找到：
```html
      <button class="nav-item" data-view="analysis" onclick="switchView('analysis', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M3 17V8l4-4 3 5 4-6 3 5v9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 14h16" stroke="currentColor" stroke-width="1.1" stroke-dasharray="2 2.5" stroke-linecap="round"/></svg>
        <span>技术分析</span>
      </button>
```

在其前插入：
```html
      <button class="nav-item" data-view="industry" onclick="switchView('industry', this)">
        <svg viewBox="0 0 20 20" fill="none"><path d="M2 17V7l6-4 4 3 4-3v14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 17V11h4v6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
        <span>行业调研</span>
      </button>
```

- [ ] **Step 3: 在 topbarMeta 对象中（约第 2648 行 cockpit 条目之后）追加 industry 条目**

找到：
```javascript
  cockpit: { title: '驾驶舱', sub: '市场情绪 · 核心指数 · 分时行情总览' },
```

在其后追加：
```javascript
  industry: { title: '行业调研', sub: 'AI 六步框架 · 流式分析 · 报告存档' },
```

- [ ] **Step 4: 在现有 cockpit CSS 块之后追加行业调研 CSS**

在 `.ck-closed-tip` CSS 规则之后追加：

```css
/* ── 行业调研 ── */
.industry-layout { display: grid; grid-template-columns: 300px 1fr; gap: 16px; height: calc(100vh - 120px); }
.industry-left { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }
.industry-input-card { flex-shrink: 0; }
.industry-history { flex: 1; overflow-y: auto; min-height: 0; }
.industry-history-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 6px; cursor: pointer; transition: background .12s; font-size: 13px; }
.industry-history-item:hover { background: var(--surface-2); }
.industry-history-item .ind-name { flex: 1; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.industry-history-item .ind-date { font-size: 11px; color: var(--ink-3); }
.industry-right { display: flex; flex-direction: column; min-height: 0; }
.industry-output { flex: 1; overflow-y: auto; padding: 20px 24px; background: var(--surface); border-radius: 10px; border: 1px solid rgba(0,0,0,.06); }
.industry-output .ind-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: var(--ink-3); gap: 8px; }
.industry-output .ind-empty strong { font-size: 14px; color: var(--ink-2); }
.industry-output h1,.industry-output h2,.industry-output h3 { margin: 1em 0 .4em; }
.industry-output p { margin: .4em 0; line-height: 1.7; }
.industry-output ul,.industry-output ol { padding-left: 1.5em; margin: .4em 0; }
.industry-output table { border-collapse: collapse; width: 100%; margin: .8em 0; }
.industry-output th,.industry-output td { border: 1px solid rgba(0,0,0,.1); padding: 6px 10px; font-size: 13px; }
.industry-output th { background: var(--surface-2); font-weight: 600; }
.industry-output blockquote { border-left: 3px solid var(--blue); padding-left: 12px; color: var(--ink-2); margin: .6em 0; }
.industry-output-actions { display: flex; justify-content: flex-end; padding: 8px 0 0; }
.ind-purpose-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.ind-purpose-btn { padding: 4px 12px; border-radius: 20px; border: 1px solid rgba(0,0,0,.12); background: transparent; font-size: 12px; cursor: pointer; transition: background .12s, border-color .12s; }
.ind-purpose-btn.active { background: var(--blue); color: #fff; border-color: var(--blue); }
@media (max-width: 768px) { .industry-layout { grid-template-columns: 1fr; height: auto; } }
```

- [ ] **Step 5: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(industry): index.html — marked.js + 导航项 + CSS"
```

---

## Task 6: index.html — 行业调研 HTML 视图

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 CONFIG 视图之前（约第 2448 行 `<!-- ==================== VIEW: CONFIG ====================` 注释前）插入行业调研视图**

找到：
```html
      <!-- ==================== VIEW: CONFIG ==================== -->
      <div id="view-config" class="view">
```

在其前插入：
```html
      <!-- ==================== VIEW: INDUSTRY ==================== -->
      <div id="view-industry" class="view">
        <div class="industry-layout">

          <!-- 左栏：输入 + 历史 -->
          <div class="industry-left">
            <div class="card industry-input-card">
              <div class="card-header"><div class="card-title">行业调研</div></div>
              <div class="card-body" style="display:flex;flex-direction:column;gap:10px;">
                <input id="ind-input" class="input" placeholder="输入行业名称，如：新能源汽车、医疗器械…" style="font-size:13px;">
                <div style="font-size:12px;color:var(--ink-2);font-weight:500;">分析目的</div>
                <div class="ind-purpose-row">
                  <button class="ind-purpose-btn active" data-purpose="investment" onclick="indSelectPurpose(this)">投资选股</button>
                  <button class="ind-purpose-btn" data-purpose="startup" onclick="indSelectPurpose(this)">创业选赛道</button>
                  <button class="ind-purpose-btn" data-purpose="career" onclick="indSelectPurpose(this)">择业找方向</button>
                  <button class="ind-purpose-btn" data-purpose="full" onclick="indSelectPurpose(this)">完整行研</button>
                </div>
                <button id="ind-run-btn" class="btn btn-primary" onclick="indStartAnalysis()" style="font-size:13px;">开始分析</button>
              </div>
            </div>

            <div class="card industry-history" style="padding:0;">
              <div class="card-header" style="padding:10px 14px;"><div class="card-title" style="font-size:13px;">历史报告</div></div>
              <div id="ind-history-list" style="padding:4px 0;">
                <div style="padding:16px;font-size:12px;color:var(--ink-3);text-align:center;">暂无历史报告</div>
              </div>
            </div>
          </div>

          <!-- 右侧：报告输出 -->
          <div class="industry-right">
            <div class="industry-output" id="ind-output">
              <div class="ind-empty">
                <svg width="40" height="40" viewBox="0 0 20 20" fill="none"><path d="M2 17V7l6-4 4 3 4-3v14" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 17V11h4v6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
                <strong>输入行业名称，开始 AI 分析</strong>
                <span style="font-size:12px;">基于肖璟《如何快速了解一个行业》六步框架</span>
              </div>
            </div>
            <div class="industry-output-actions" id="ind-save-row" style="display:none;">
              <button class="btn btn-primary btn-sm" onclick="indSaveReport()">保存报告</button>
            </div>
          </div>

        </div>
      </div>

```

- [ ] **Step 2: 刷新页面，点击「行业调研」导航项，确认布局正常显示**

访问 `http://localhost:8000`，点击「行业调研」，确认：
- 左侧：输入框 + 分析目的选钮 + 开始按钮 + 历史列表（暂无历史报告）
- 右侧：空态提示（图标 + 文字）

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(industry): index.html — 行业调研视图 HTML"
```

---

## Task 7: index.html — 行业调研 JS

**文件:** Modify `wexin-read-mcp-main/src/templates/index.html`

- [ ] **Step 1: 在 switchView 函数（约第 2963 行 `if (view === 'cockpit')` 条目之后）添加 industry 初始化钩子**

找到：
```javascript
  if (view === 'cockpit') { initCockpit(); }
}
```

替换为：
```javascript
  if (view === 'cockpit') { initCockpit(); }
  if (view === 'industry') { indLoadHistory(); }
}
```

- [ ] **Step 2: 在文件末尾（`</script>` 标签之前）追加行业调研 JS**

找到文件末尾的 `</script>` 标签，在其前追加：

```javascript
/* ============================================================
   行业调研
   ============================================================ */
let _indPurpose = 'investment';
let _indCurrentText = '';
let _indCurrentIndustry = '';
let _indCurrentPurpose = 'investment';
let _indStreaming = false;

function indSelectPurpose(btn) {
  _indPurpose = btn.dataset.purpose;
  document.querySelectorAll('.ind-purpose-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

async function indStartAnalysis() {
  const industry = document.getElementById('ind-input').value.trim();
  if (!industry) { toast('请输入行业名称', 'amber'); return; }
  if (_indStreaming) return;

  _indStreaming = true;
  _indCurrentText = '';
  _indCurrentIndustry = industry;
  _indCurrentPurpose = _indPurpose;

  const btn = document.getElementById('ind-run-btn');
  btn.textContent = '分析中…';
  btn.disabled = true;
  document.getElementById('ind-save-row').style.display = 'none';

  const output = document.getElementById('ind-output');
  output.innerHTML = '<div style="padding:12px;font-size:13px;color:var(--ink-2);">正在分析，请稍候…</div>';

  try {
    const resp = await fetch('/api/industry/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ industry, purpose: _indPurpose }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      output.innerHTML = `<div style="color:var(--red);padding:12px;font-size:13px;">请求失败: ${err.detail || resp.status}</div>`;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // 保留未完整行
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') {
          document.getElementById('ind-save-row').style.display = '';
          break;
        }
        if (payload.startsWith('[ERROR]')) {
          output.innerHTML += `<div style="color:var(--red);font-size:13px;padding:8px 0;">${payload}</div>`;
          break;
        }
        try {
          const token = JSON.parse(payload);
          _indCurrentText += token;
          output.innerHTML = marked.parse(_indCurrentText);
          output.scrollTop = output.scrollHeight;
        } catch(e) {}
      }
    }
  } catch(e) {
    output.innerHTML = `<div style="color:var(--red);padding:12px;font-size:13px;">连接失败: ${e.message}</div>`;
  } finally {
    _indStreaming = false;
    btn.textContent = '开始分析';
    btn.disabled = false;
  }
}

async function indSaveReport() {
  if (!_indCurrentText) return;
  try {
    const params = new URLSearchParams({
      industry: _indCurrentIndustry,
      purpose: _indCurrentPurpose,
      report_text: _indCurrentText,
    });
    const r = await fetch('/api/industry/reports?' + params.toString(), { method: 'POST' });
    const j = await r.json();
    if (j.success) {
      toast('报告已保存', 'green');
      document.getElementById('ind-save-row').style.display = 'none';
      indLoadHistory();
    }
  } catch(e) { toast('保存失败', 'red'); }
}

async function indLoadHistory() {
  try {
    const r = await fetch('/api/industry/reports');
    const j = await r.json();
    const list = document.getElementById('ind-history-list');
    if (!j.success || !j.data || j.data.length === 0) {
      list.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--ink-3);text-align:center;">暂无历史报告</div>';
      return;
    }
    list.innerHTML = j.data.map(item => `
      <div class="industry-history-item" onclick="indViewReport(${item.id})">
        <div class="ind-name">${item.industry}</div>
        <div class="ind-date">${item.created_at.slice(0, 10)}</div>
        <button class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 6px;" onclick="event.stopPropagation();indDeleteReport(${item.id})">删除</button>
      </div>`).join('');
  } catch(e) { console.error('indLoadHistory', e); }
}

async function indViewReport(id) {
  try {
    const r = await fetch('/api/industry/reports/' + id);
    const j = await r.json();
    if (!j.success || !j.data) return;
    _indCurrentText = j.data.report_text || '';
    _indCurrentIndustry = j.data.industry;
    _indCurrentPurpose = j.data.purpose;
    document.getElementById('ind-input').value = j.data.industry;
    document.getElementById('ind-output').innerHTML = marked.parse(_indCurrentText);
    document.getElementById('ind-save-row').style.display = 'none';
  } catch(e) { console.error('indViewReport', e); }
}

async function indDeleteReport(id) {
  if (!confirm('确认删除此报告？')) return;
  try {
    await fetch('/api/industry/reports/' + id, { method: 'DELETE' });
    toast('已删除', 'green');
    indLoadHistory();
  } catch(e) { toast('删除失败', 'red'); }
}
```

- [ ] **Step 2: 完整功能验证**

在浏览器中：
1. 进入「行业调研」页面
2. 输入「新能源汽车」，选择「投资选股」，点击「开始分析」
3. 观察报告是否流式逐字输出（需配置有效 AI API Key）
4. 分析完成后点击「保存报告」，确认 toast 提示「报告已保存」
5. 历史列表出现该记录，点击可重新查看
6. 点击历史记录旁的「删除」，确认记录消失

若未配置 AI API Key：输出框显示 `[ERROR] 未配置 AI API Key` 提示，属正常行为。

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html
git commit -m "feat(industry): index.html — 行业调研完整 JS（流式输出 + 保存 + 历史）"
```

---

## Task 8: 创建 Claude Code Skill 文件

**文件:** Create `~/.claude/plugins/industry-analyst/SKILL.md`

- [ ] **Step 1: 创建目录并写入 skill 文件**

```bash
mkdir -p ~/.claude/plugins/industry-analyst
```

创建文件 `~/.claude/plugins/industry-analyst/SKILL.md`，内容如下：

```markdown
---
name: industry-analyst
description: 行业分析师 — 基于肖璟《如何快速了解一个行业》的完整行研框架。当用户说「分析这个行业」「帮我做行研」「这个赛道怎么样」「这个行业值不值得投」「我想了解XX行业」「帮我做一个行业报告」时触发。适用于创业赛道评估、求职行业选择、投资标的筛选。
triggers:
  - 分析.*行业
  - 帮我做行研
  - 这个赛道怎么样
  - 值不值得投
  - 了解.*行业
  - 行业报告
---

# 行业分析师

基于肖璟《如何快速了解一个行业》的完整方法论，覆盖产业生命周期判断、商业模式可行性、市场规模测算、护城河分析、竞争格局与盈利性、估值逻辑、PEST 外部因素分析和景气度跟踪，输出结构化分析报告。

## Step 0：厘清用户意图

在开始分析前先问一个问题（如果用户已说明则跳过）：

> 「你分析这个行业，主要是为了：**投资选股**、**创业选赛道**、**择业找方向**，还是需要**完整行研报告**？」

## Step 1：定义行业边界

- **横向**：明确行业层级（申万行业分类 / GICS），说明含哪些环节/不含哪些
- **纵向**：梳理产业链（原材料→中间品→终端产品→渠道→用户），标注分析重点

输出：「本次分析的行业定义是……」

## Step 2：判断产业生命周期

以渗透率为核心判断标准：

| 阶段 | 渗透率 | 特征 |
|------|--------|------|
| 导入期 | <10% | 产品尚未被大众接受 |
| 成长期 | 10%-50% | 快速扩张，格局未定 |
| 成熟期 | >50% | 增速放缓，格局趋稳 |
| 衰退期 | 下滑 | 替代品出现 |

## Step 3：按阶段聚焦分析

**导入期 → 可行性（3A）**
- 需求：真实需求 vs 伪需求？用户愿意付费？
- 商业模式：能卖出去（获客成本/留存）？能赚钱（毛利率/LTV>3×CAC）？能复制？

**成长期 → 规模性（3B）**
- TAM / SAM / SOM 三口径测算
- Top-down（总市场×渗透率）+ Bottom-up（单价×用户数）
- 高/中/低三情景预测

**成熟期 → 防守性（3C）+ 盈利性（3D）**
- 护城河：成本优势 / 网络效应 / 无形资产 / 转换成本；宽/窄/无
- 产能周期与格局演化；波特五力×议价能力；毛利率/应收应付/ROE验证

## Step 4：估值逻辑

| 阶段 | 估值框架 |
|------|----------|
| 导入期 | PS / EV-GMV |
| 成长期 | PEG |
| 成熟期 | PE / DCF |
| 衰退期 | PB / 清算价值 |

## Step 5：PEST 外部因素

当前哪些因素是催化剂或压制因素：
- **P**（政策）：监管趋势、补贴/限制
- **E**（经济）：利率、汇率、消费能力
- **S**（社会）：人口结构、消费偏好
- **T**（技术）：替代技术威胁、降本增效

## Step 6：景气度跟踪指标

设计高频跟踪体系（量/价/利/库存/预期）+ 推荐数据来源

## 输出格式

根据分析目的调整侧重：
- **投资选股**：生命周期 + 竞争格局 + 估值 + 景气度
- **创业选赛道**：可行性 + 市场规模 + 护城河路径
- **择业找方向**：生命周期 + 行业前景 + 龙头公司
- **完整行研**：所有维度全覆盖

报告结构：行业定义 → 生命周期 → 核心分析 → PEST → 估值参考 → 景气度指标 → 综合结论与风险提示（含可证伪条件）
```

- [ ] **Step 2: 验证 skill 文件可被 Claude Code 识别**

```bash
cat ~/.claude/plugins/industry-analyst/SKILL.md | head -5
```

期望看到 `name: industry-analyst` 等 frontmatter 字段

- [ ] **Step 3: Commit**

```bash
git add wexin-read-mcp-main/src/templates/index.html 2>/dev/null; true
git add "docs/功能模块/18-美股驾驶舱Tab与行业调研页面设计.md" 2>/dev/null; true
git commit -m "feat(industry): 创建 Claude Code skill — industry-analyst" --allow-empty
```

注：`~/.claude/plugins/` 在仓库外，无法 git 追踪，skill 文件只需本地存在即可。
```
