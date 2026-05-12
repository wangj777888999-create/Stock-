"""
东方财富 emweb 直连客户端。
用于在 AKShare 不可达时提供公司基本信息和财务数据降级。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from http_client import get_async_client

logger = logging.getLogger("eastmoney")

_EM_BASE = "https://emweb.securities.eastmoney.com/PC_HSF10"
_HEADERS = {
    "Referer": "https://emweb.securities.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def _em_code(symbol: str, market: str) -> str:
    """转换为东方财富格式：SH600519 / SZ000001"""
    if market == "a":
        prefix = "SH" if symbol.startswith(("6", "9")) else "SZ"
        return f"{prefix}{symbol}"
    return symbol


async def get_company_profile(symbol: str, market: str) -> dict | None:
    """从东方财富获取公司基本信息。返回 None 表示不可用。"""
    code = _em_code(symbol, market)
    url = f"{_EM_BASE}/CompanySurvey/PageAjax?code={code}"
    try:
        r = await get_async_client().get(url, headers=_HEADERS, timeout=8)
        d = r.json()
        items = d.get("jbzl")
        if not items:
            return None
        row = items[0] if isinstance(items, list) else items
        return {
            "公司名称": row.get("ORG_NAME"),
            "A股简称": row.get("SECURITY_NAME_ABBR"),
            "所属行业": row.get("INDUSTRYCSRC1") or row.get("EM2016"),
            "上市日期": row.get("LISTDATE"),
            "注册资金": f"{row['REG_CAPITAL']:.0f}万元" if row.get("REG_CAPITAL") else None,
            "法人代表": row.get("LEGAL_PERSON"),
            "官方网站": row.get("ORG_WEB"),
            "主营业务": row.get("ORG_PROFILE"),
            "经营范围": row.get("BUSINESS_SCOPE"),
            "注册地址": row.get("REG_ADDRESS") or row.get("ADDRESS"),
            "员工人数": str(row["EMP_NUM"]) if row.get("EMP_NUM") else None,
            "联系电话": row.get("ORG_TEL"),
        }
    except Exception as e:
        logger.debug(f"emweb 公司信息失败 {symbol}: {e}")
        return None


async def get_financial(symbol: str, market: str) -> list[dict] | None:
    """从东方财富获取最近 8 期核心财务指标。返回 None 表示不可用。"""
    code = _em_code(symbol, market)
    # 生成最近 8 个季度末日期作为请求参数（加速响应）
    dates = _recent_quarter_dates(8)
    url = f"{_EM_BASE}/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={code}&dates={','.join(dates)}"
    try:
        r = await get_async_client().get(url, headers=_HEADERS, timeout=15)
        d = r.json()
        rows = d.get("data", [])
        if not rows:
            return None
        result = []
        for row in rows:
            result.append({
                "报告期": str(row.get("REPORT_DATE", ""))[:10],
                "营业总收入": _fmt_amount(row.get("TOTALOPERATEREVE")),
                "营业总收入同比增长率": _fmt_pct(row.get("TOTALOPERATEREVETZ")),
                "净利润": _fmt_amount(row.get("PARENTNETPROFIT")),
                "净利润同比增长率": _fmt_pct(row.get("PARENTNETPROFITTZ")),
                "基本每股收益": _clean(row.get("EPSJB")),
                "每股净资产": _clean(row.get("BPS")),
                "每股经营现金流": _clean(row.get("MGJYXJJE")),
                "销售净利率": _fmt_pct(row.get("XSJLL")),
                "销售毛利率": _fmt_pct(row.get("XSMLL")),
                "净资产收益率": _fmt_pct(row.get("ROEJQ")),
                "资产负债率": _fmt_pct(row.get("ZCFZL")),
                "流动比率": _clean(row.get("LD")),
            })
        return result
    except Exception as e:
        logger.debug(f"emweb 财务数据失败 {symbol}: {e}")
        return None


def _recent_quarter_dates(n: int) -> list[str]:
    """生成最近 n 个季度末日期列表，格式 YYYY-MM-DD。"""
    quarters = ["03-31", "06-30", "09-30", "12-31"]
    dates = []
    today = datetime.now()
    year = today.year
    for _ in range(n * 2):
        for q in reversed(quarters):
            d = datetime.strptime(f"{year}-{q}", "%Y-%m-%d")
            if d < today - timedelta(days=30):
                dates.append(d.strftime("%Y-%m-%d"))
                if len(dates) >= n:
                    return dates
        year -= 1
    return dates[:n]


def _clean(v):
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, 4)
    except (TypeError, ValueError):
        return str(v)


def _fmt_amount(v) -> str | None:
    if v is None:
        return None
    try:
        v = float(v)
        if abs(v) >= 1e8:
            return f"{v/1e8:.2f}亿"
        if abs(v) >= 1e4:
            return f"{v/1e4:.2f}万"
        return str(round(v, 2))
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v) -> str | None:
    if v is None:
        return None
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)
