"""行业调研服务 — 基于肖璟六步框架的 AI 流式分析 + 报告存档。"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import httpx

from database import get_db
from http_client import get_async_proxy_client
from state import config

logger = logging.getLogger("industry-service")

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
    """调用 AI API（SSE 流式），逐 token yield。"""
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
    headers = {"Authorization": f"Bearer {config.ai.api_key}", "Content-Type": "application/json"}
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
        client = get_async_proxy_client()
        async with client.stream("POST", url, headers=headers, json=payload, timeout=120.0) as response:
            if response.status_code >= 400:
                # 读取错误体并透传给前端（去掉敏感字段）
                body = await response.aread()
                try:
                    err_json = json.loads(body.decode("utf-8", errors="replace"))
                    err_msg = (
                        err_json.get("error", {}).get("message")
                        or err_json.get("message")
                        or err_json.get("error")
                        or body.decode("utf-8", errors="replace")[:300]
                    )
                except Exception:
                    err_msg = body.decode("utf-8", errors="replace")[:300]
                logger.error(f"AI API {response.status_code}: {err_msg}")
                safe = json.dumps(f"[{response.status_code}] {err_msg}", ensure_ascii=False)
                yield f"data: [ERROR] AI 调用失败 — {safe}\n\n"
                return
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
                        yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except httpx.HTTPStatusError as e:
        logger.error(f"AI API 返回错误: {e.response.status_code}")
        yield f"data: [ERROR] AI 服务返回 {e.response.status_code} 错误\n\n"
    except Exception as e:
        logger.error(f"AI 流式调用失败: {e}", exc_info=True)
        msg = json.dumps(f"{type(e).__name__}: {str(e)[:300]}", ensure_ascii=False)
        yield f"data: [ERROR] AI 调用异常 — {msg}\n\n"


def save_report(industry: str, purpose: str, report_text: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO industry_reports (industry, purpose, report_text) VALUES (?, ?, ?)",
        (industry, purpose, report_text),
    )
    db.commit()
    return cur.lastrowid


def list_reports(limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, industry, purpose, created_at FROM industry_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, industry, purpose, report_text, created_at FROM industry_reports WHERE id=?",
        (report_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_report(report_id: int) -> bool:
    db = get_db()
    deleted = db.execute("DELETE FROM industry_reports WHERE id=?", (report_id,)).rowcount
    db.commit()
    return deleted > 0
