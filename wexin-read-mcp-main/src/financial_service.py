"""财报分析服务 — 拉取 A 股/美股财报关键指标 → AI 四维度结构化解读 → 入库。

数据源（AKShare）：
- A 股：stock_financial_abstract（财务摘要，宽表）
- 美股：stock_financial_us_analysis_indicator_em（财务指标，长表，YoY 已算好）

设计与 industry_service / analyzer 保持一致：sync 数据源放线程池，AI 走
OpenAI 兼容 /chat/completions，报告入 financial_reports 表。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from database import get_db, write_lock
from http_client import get_async_proxy_client
from state import config
from stock_utils import TTL_COMPANY, cache_get, cache_get_stale, cache_set

logger = logging.getLogger("financial-service")

# 取最近多少期
_A_PERIODS = 8       # A 股按季度，取最近 8 期
_US_PERIODS = 6      # 美股年报，取最近 6 年

# A 股财务摘要里「指标」列的名称 → 标准字段。按子串匹配，取首个命中行。
_A_FIELD_KEYWORDS = [
    ("revenue", ["营业总收入"]),
    ("net_profit", ["归母净利润", "归属于母公司"]),
    ("gross_margin", ["毛利率"]),
    ("net_margin", ["销售净利率"]),
    ("roe", ["净资产收益率(ROE)", "净资产收益率"]),
    ("debt_ratio", ["资产负债率"]),
    ("ocf", ["经营现金流量净额", "经营活动产生的现金流量净额"]),
    ("eps", ["基本每股收益"]),
]


# ─────────────────────────── 数据获取 ───────────────────────────

async def fetch_indicators(symbol: str, market: str) -> dict:
    """按市场取财报关键指标，归一化为标准结构。带 24h 缓存。

    返回:
        {"success": bool, "data": {...}|None, "error": str|None}
        data = {symbol, market, name, currency, periods:[{period, revenue,
                revenue_yoy, net_profit, net_profit_yoy, gross_margin,
                net_margin, roe, debt_ratio, ocf, eps}, ...]}  # 最新在前
    """
    market = (market or "a").lower()
    cache_key = f"fin_ind:{market}:{symbol}"
    cached = cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached, "error": None}

    fetch = _fetch_a if market == "a" else _fetch_us if market == "us" else None
    if fetch is None:
        return {"success": False, "data": None, "error": f"暂不支持的市场: {market}"}

    # 重试 2 次,瞬时错误兜底
    last_err = None
    data = None
    for attempt in range(2):
        try:
            data = await asyncio.to_thread(fetch, symbol)
            if data and data.get("periods"):
                break
        except Exception as e:
            last_err = e
            logger.warning(f"取财报失败 {market}:{symbol} 第{attempt+1}次 — {e}")
            await asyncio.sleep(0.5 * (attempt + 1))

    if not data or not data.get("periods"):
        # 陈旧兜底:7 天内有过成功就返回旧值
        stale, _ = cache_get_stale(cache_key, max_stale_seconds=7 * 86400)
        if stale and stale.get("periods"):
            stale["stale"] = True
            return {"success": True, "data": stale, "error": None}
        msg = f"取财报数据失败: {last_err}" if last_err else "未取到财报数据（代码可能有误或数据源无该股票财报）"
        return {"success": False, "data": None, "error": msg}

    cache_set(cache_key, data, ttl=TTL_COMPANY)
    return {"success": True, "data": data, "error": None}


def _fetch_a(symbol: str) -> dict:
    """A 股：解析 stock_financial_abstract 宽表。"""
    import akshare as ak

    df = ak.stock_financial_abstract(symbol=symbol)
    if df is None or df.empty:
        return {}

    # 日期列：列名形如 20251231（8 位数字），已按时间降序排列
    date_cols = [c for c in df.columns if re.fullmatch(r"\d{8}", str(c))]
    date_cols = date_cols[:_A_PERIODS + 4]  # 多取 4 期用于算同比
    if not date_cols:
        return {}

    ind_col = "指标" if "指标" in df.columns else df.columns[1]

    # 指标名 → {日期: 值}
    def row_for(field: str) -> dict | None:
        for _, keys in [(f, k) for f, k in _A_FIELD_KEYWORDS if f == field]:
            for kw in keys:
                hit = df[df[ind_col].astype(str).str.contains(re.escape(kw), na=False)]
                if not hit.empty:
                    r = hit.iloc[0]
                    return {d: _num(r.get(d)) for d in date_cols}
        return None

    rows = {field: row_for(field) for field, _ in _A_FIELD_KEYWORDS}

    used = date_cols[:_A_PERIODS]
    periods = []
    for i, d in enumerate(used):
        prev_year = date_cols[i + 4] if i + 4 < len(date_cols) else None  # 去年同期
        periods.append({
            "period": _fmt_date(d),
            "revenue": _g(rows, "revenue", d),
            "revenue_yoy": _yoy(rows, "revenue", d, prev_year),
            "net_profit": _g(rows, "net_profit", d),
            "net_profit_yoy": _yoy(rows, "net_profit", d, prev_year),
            "gross_margin": _g(rows, "gross_margin", d),
            "net_margin": _g(rows, "net_margin", d),
            "roe": _g(rows, "roe", d),
            "debt_ratio": _g(rows, "debt_ratio", d),
            "ocf": _g(rows, "ocf", d),
            "eps": _g(rows, "eps", d),
        })

    return {
        "symbol": symbol,
        "market": "a",
        "name": "",  # A 股摘要不含名称，前端已有名称
        "currency": "CNY",
        "periods": periods,
    }


def _fetch_us(symbol: str) -> dict:
    """美股：解析 stock_financial_us_analysis_indicator_em 长表（每期一行）。"""
    import akshare as ak

    df = ak.stock_financial_us_analysis_indicator_em(symbol=symbol, indicator="年报")
    if df is None or df.empty:
        return {}

    df = df.head(_US_PERIODS)
    name = str(df.iloc[0].get("SECURITY_NAME_ABBR", "")) if not df.empty else ""
    currency = str(df.iloc[0].get("CURRENCY_ABBR", "USD")) if not df.empty else "USD"

    periods = []
    for _, r in df.iterrows():
        periods.append({
            "period": _fmt_date(r.get("REPORT_DATE")),
            "revenue": _num(r.get("OPERATE_INCOME")),
            "revenue_yoy": _num(r.get("OPERATE_INCOME_YOY")),
            "net_profit": _num(r.get("PARENT_HOLDER_NETPROFIT")),
            "net_profit_yoy": _num(r.get("PARENT_HOLDER_NETPROFIT_YOY")),
            "gross_margin": _num(r.get("GROSS_PROFIT_RATIO")),
            "net_margin": _num(r.get("NET_PROFIT_RATIO")),
            "roe": _num(r.get("ROE_AVG")),
            "debt_ratio": _num(r.get("DEBT_ASSET_RATIO")),
            "ocf": None,  # 该接口无经营现金流绝对额
            "eps": _num(r.get("BASIC_EPS")),
        })

    return {
        "symbol": symbol,
        "market": "us",
        "name": name,
        "currency": currency,
        "periods": periods,
    }


# ─────────────────────────── AI 解读 ───────────────────────────

_SYSTEM_PROMPT = (
    "你是一位严谨的财报分析师。基于给定的财报关键指标（已是结构化数据），"
    "做客观解读。只依据给出的数字，不编造未提供的数据；数字异常或缺失要如实指出。"
)


def _build_table(data: dict) -> str:
    """把指标拼成给 AI 看的紧凑文本表（最新在前）。"""
    cur = data.get("currency", "")
    lines = [
        f"股票：{data.get('name') or data.get('symbol')}  市场：{data.get('market')}  货币：{cur}",
        "",
        "| 报告期 | 营收 | 营收同比% | 归母净利 | 净利同比% | 毛利率% | 净利率% | ROE% | 资产负债率% | 经营现金流 | EPS |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in data.get("periods", []):
        lines.append(
            f"| {p['period']} | {_fmt(p['revenue'])} | {_fmt(p['revenue_yoy'])} | "
            f"{_fmt(p['net_profit'])} | {_fmt(p['net_profit_yoy'])} | {_fmt(p['gross_margin'])} | "
            f"{_fmt(p['net_margin'])} | {_fmt(p['roe'])} | {_fmt(p['debt_ratio'])} | "
            f"{_fmt(p['ocf'])} | {_fmt(p['eps'])} |"
        )
    return "\n".join(lines)


async def ai_summarize(data: dict) -> dict:
    """四维度结构化解读。非流式，返回 Markdown。

    返回: {"success": bool, "summary": str, "error": str|None}
    """
    if not config.ai.api_key or not config.ai.base_url:
        return {"success": False, "summary": "", "error": "未配置 AI API，无法生成解读"}

    table = _build_table(data)
    prompt = f"""下面是某公司的财报关键指标（最新报告期在最上方）：

{table}

请基于以上数据，用中文输出**四个维度**的结构化解读，每个维度先一句话结论，再用 1-3 句简述依据。最后给一段「综合判断」。严格按以下 Markdown 结构：

## 一、成长性
（看营收、归母净利的同比增速及趋势）

## 二、盈利质量
（看毛利率、净利率的趋势；若有经营现金流，对比净利润判断利润含金量）

## 三、财务健康
（看资产负债率、ROE 水平与变化）

## 四、风险提示
（指出数据里暴露的隐患，如增收不增利、利润率下滑、负债率攀升、现金流为负或缺失等）

## 综合判断
（2-4 句话，给出这份财报反映的整体基本面印象，并附一句可证伪条件，如"若下季营收增速继续下滑则需警惕"）

要求：只依据上表数字；指标缺失（显示为 —）时不要臆测，可注明"数据缺失"。"""

    try:
        client = get_async_proxy_client()
        response = await client.post(
            f"{config.ai.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.ai.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.ai.model or "gpt-4o",
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        if not choices:
            return {"success": False, "summary": "", "error": "AI 返回空结果"}
        summary = choices[0].get("message", {}).get("content", "")
        if not summary:
            return {"success": False, "summary": "", "error": "AI 返回内容为空"}
        return {"success": True, "summary": summary, "error": None}
    except Exception as e:
        logger.error(f"AI 财报解读失败: {e}")
        return {"success": False, "summary": "", "error": f"AI 解读失败: {e}"}


# ─────────────────────────── 编排 + 存储 ───────────────────────────

async def analyze(symbol: str, market: str, force: bool = False) -> dict:
    """取指标 → AI 解读 → 入库。force=True 时忽略已存报告，重新生成。

    返回: {"success": bool, "data": {indicators, summary, period}, "error": str|None}
    """
    market = (market or "a").lower()

    got = await fetch_indicators(symbol, market)
    if not got["success"]:
        return {"success": False, "data": None, "error": got["error"]}
    data = got["data"]
    latest_period = data["periods"][0]["period"] if data["periods"] else None

    # 已有同报告期且 AI 解读非空 → 直接复用，省 token（解读为空说明上次 AI 失败，需重试）
    if not force and latest_period:
        existing = get_report(symbol, market, latest_period)
        if existing and existing.get("ai_summary", "").strip():
            return {
                "success": True,
                "data": {
                    "indicators": data,
                    "summary": existing["ai_summary"],
                    "period": latest_period,
                    "cached": True,
                },
                "error": None,
            }

    ai = await ai_summarize(data)
    summary = ai["summary"] if ai["success"] else ""

    save_report(symbol, market, data.get("name", ""), latest_period, data, summary)

    return {
        "success": True,
        "data": {
            "indicators": data,
            "summary": summary,
            "period": latest_period,
            "cached": False,
            "ai_error": None if ai["success"] else ai["error"],
        },
        "error": None,
    }


def save_report(symbol: str, market: str, name: str, period: str | None,
                indicators: dict, ai_summary: str) -> None:
    db = get_db()
    with write_lock:
        db.execute(
            """INSERT INTO financial_reports (symbol, market, name, period, indicators, ai_summary)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, market, period)
               DO UPDATE SET indicators=excluded.indicators,
                             ai_summary=excluded.ai_summary,
                             name=excluded.name,
                             created_at=datetime('now')""",
            (symbol, market, name, period, json.dumps(indicators, ensure_ascii=False), ai_summary),
        )
        db.commit()


def get_report(symbol: str, market: str, period: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM financial_reports WHERE symbol=? AND market=? AND period=?",
        (symbol, market, period),
    ).fetchone()
    return dict(row) if row else None


def list_reports(symbol: str, market: str, limit: int = 12) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT id, symbol, market, name, period, created_at
           FROM financial_reports WHERE symbol=? AND market=?
           ORDER BY period DESC LIMIT ?""",
        (symbol, market, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── 工具函数 ───────────────────────────

def _num(v):
    """转 float，无效值返回 None。"""
    if v is None:
        return None
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _g(rows: dict, field: str, date_col: str):
    r = rows.get(field)
    return r.get(date_col) if r else None


def _yoy(rows: dict, field: str, cur_col: str, prev_col: str | None):
    """同比增速 %：(本期-去年同期)/|去年同期| × 100。"""
    if not prev_col:
        return None
    cur, prev = _g(rows, field, cur_col), _g(rows, field, prev_col)
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / abs(prev) * 100, 2)


def _fmt_date(d) -> str:
    """20251231 或 2025-12-31 00:00:00 → 2025-12-31。"""
    s = str(d).strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _fmt(v) -> str:
    """给文本表格用：大数转亿/百万，百分比保留两位。"""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.2f}"
