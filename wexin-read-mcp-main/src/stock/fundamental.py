"""FundamentalMixin — 公司简介 / 财务指标 / 巨潮公告 / 十大流通股东。

数据源：
- A 股简介/财务: 统一 DataRouter（巨潮 + 同花顺等）
- 港股/美股简介: AKShare（东财 / 雪球）
- 港股/美股财务: AKShare 东财指标
- 公告: 统一 DataRouter（巨潮资讯）
- 股东: AKShare stock_circulate_stock_holder
"""

from __future__ import annotations

import asyncio
import logging

import akshare as ak

from stock_utils import (
    TTL_COMPANY,
    TTL_DAILY,
    _clean,
    _fmt_amount,
    _fmt_pct,
    cache,
    detect_market,
    normalize_symbol,
)
from http_client import patch_requests

logger = logging.getLogger("stock-service")

_patch_requests = patch_requests


class FundamentalMixin:
    """公司基本面数据。"""

    # ─── 4. 公司简介 ───

    async def get_company_profile(self, symbol: str) -> dict:
        """获取公司基本信息。A 股用巨潮资讯，港股/美股用 AKShare。"""
        market = detect_market(symbol)
        original = str(symbol).strip()
        norm = normalize_symbol(symbol)
        cache_key = f"profile:{norm}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if market == "a":
                from services.data_router import get_router
                rr = await get_router().fetch(
                    "company_profile",
                    cache_key=None,  # 缓存由外层方法继续管
                    timeout=30.0,
                    validate=lambda r: bool(r),
                    symbol=norm, market=market,
                )
                record = rr["data"] if rr.get("success") else None
                sid = rr.get("source")
                if not record:
                    return {"success": False, "error": "未找到公司信息"}

            elif market == "hk":
                df = await asyncio.wait_for(
                    asyncio.to_thread(_patch_requests, ak.stock_hk_company_profile_em, symbol=original),
                    timeout=20,
                )
                if df is None or df.empty:
                    return {"success": False, "error": "未找到公司信息"}
                row = df.iloc[0]
                sid = "akshare_hk"
                record = {
                    "公司名称": row.get("公司名称"),
                    "英文名称": row.get("英文名称"),
                    "所属行业": row.get("所属行业"),
                    "上市日期": row.get("公司成立日期"),
                    "法人代表": row.get("董事长"),
                    "官方网站": row.get("公司网址"),
                    "主营业务": row.get("公司介绍"),
                    "注册地址": row.get("注册地"),
                    "联系电话": row.get("联系电话"),
                    "员工人数": _clean(row.get("员工人数")),
                    "核数师": row.get("核数师"),
                }

            else:
                # 美股：雪球基本信息
                df = await asyncio.wait_for(
                    asyncio.to_thread(_patch_requests, ak.stock_individual_basic_info_us_xq, symbol=norm),
                    timeout=20,
                )
                if df is None or df.empty:
                    return {"success": False, "error": "未找到公司信息"}
                info = dict(zip(df["item"], df["value"]))
                sid = "akshare_us"
                record = {
                    "公司名称": info.get("org_name_en", ""),
                    "英文名称": info.get("org_short_name_en", ""),
                    "所属行业": info.get("org_industry", ""),
                    "法人代表": info.get("chairman", ""),
                    "官方网站": info.get("org_website", ""),
                    "注册地址": info.get("office_address_en", ""),
                    "联系电话": info.get("telephone", ""),
                    "员工人数": _clean(info.get("staff_num")),
                    "主营业务": info.get("org_introduction", ""),
                }

            record = {k: _clean(v) for k, v in record.items()}
            resp = {"success": True, "data": record, "source": sid}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except asyncio.TimeoutError:
            logger.error(f"获取公司信息超时 {symbol}")
            return {"success": False, "error": "公司信息请求超时，请稍后重试"}
        except Exception as e:
            logger.error(f"获取公司信息失败 {symbol}: {e}")
            return {"success": False, "error": f"获取公司信息失败: {e}"}

    # ─── 5. 财务指标 ───

    async def get_financial(self, symbol: str) -> dict:
        """获取最近几期核心财务指标。A 股用同花顺，港股/美股用 AKShare。"""
        market = detect_market(symbol)
        original = str(symbol).strip()
        norm = normalize_symbol(symbol)
        cache_key = f"financial:{norm}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if market == "a":
                from services.data_router import get_router
                rr = await get_router().fetch(
                    "financial",
                    cache_key=None,  # 缓存由外层方法继续管
                    timeout=30.0,
                    validate=lambda r: bool(r),
                    symbol=norm, market=market,
                )
                records = rr["data"] if rr.get("success") else None
                sid = rr.get("source")
                if not records:
                    return {"success": False, "error": "未找到财务数据"}

            elif market == "hk":
                df = await asyncio.wait_for(
                    asyncio.to_thread(
                        _patch_requests, ak.stock_financial_hk_analysis_indicator_em,
                        symbol=original, indicator="按年度",
                    ),
                    timeout=20,
                )
                if df is None or df.empty:
                    return {"success": False, "error": "未找到财务数据"}
                sid = "akshare_hk"
                df = df.sort_values("REPORT_DATE", ascending=False).head(8)
                records = []
                for _, row in df.iterrows():
                    flow_ratio = _clean(row.get("CURRENT_RATIO"))
                    records.append({
                        "报告期": str(row.get("REPORT_DATE", ""))[:10],
                        "基本每股收益": _clean(row.get("BASIC_EPS")),
                        "营业总收入": _fmt_amount(row.get("OPERATE_INCOME")),
                        "营业总收入同比增长率": _fmt_pct(row.get("OPERATE_INCOME_YOY")),
                        "净利润": _fmt_amount(row.get("HOLDER_PROFIT")),
                        "净利润同比增长率": _fmt_pct(row.get("HOLDER_PROFIT_YOY")),
                        "销售毛利率": _fmt_pct(row.get("GROSS_PROFIT_RATIO")),
                        "销售净利率": _fmt_pct(row.get("NET_PROFIT_RATIO")),
                        "净资产收益率": _fmt_pct(row.get("ROE_AVG")),
                        "资产负债率": _fmt_pct(row.get("DEBT_ASSET_RATIO")),
                        "流动比率": f"{flow_ratio:.2f}" if flow_ratio is not None else None,
                    })

            else:
                df = await asyncio.wait_for(
                    asyncio.to_thread(
                        _patch_requests, ak.stock_financial_us_analysis_indicator_em,
                        symbol=norm,
                    ),
                    timeout=20,
                )
                if df is None or df.empty:
                    return {"success": False, "error": "未找到财务数据"}
                sid = "akshare_us"
                df = df.sort_values("REPORT_DATE", ascending=False).head(8)
                records = []
                for _, row in df.iterrows():
                    flow_ratio = _clean(row.get("CURRENT_RATIO"))
                    records.append({
                        "报告期": str(row.get("REPORT_DATE", ""))[:10],
                        "基本每股收益": _clean(row.get("BASIC_EPS")),
                        "营业总收入": _fmt_amount(row.get("OPERATE_INCOME")),
                        "营业总收入同比增长率": _fmt_pct(row.get("OPERATE_INCOME_YOY")),
                        "净利润": _fmt_amount(row.get("HOLDER_PROFIT")),
                        "净利润同比增长率": _fmt_pct(row.get("HOLDER_PROFIT_YOY")),
                        "销售毛利率": _fmt_pct(row.get("GROSS_PROFIT_RATIO")),
                        "销售净利率": _fmt_pct(row.get("NET_PROFIT_RATIO")),
                        "净资产收益率": _fmt_pct(row.get("ROE_AVG")),
                        "资产负债率": _fmt_pct(row.get("DEBT_ASSET_RATIO")),
                        "流动比率": f"{flow_ratio:.2f}" if flow_ratio is not None else None,
                    })

            resp = {"success": True, "data": records, "source": sid}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except asyncio.TimeoutError:
            logger.error(f"获取财务数据超时 {symbol}")
            return {"success": False, "error": "财务数据请求超时，请稍后重试"}
        except Exception as e:
            logger.error(f"获取财务数据失败 {symbol}: {e}")
            return {"success": False, "error": f"获取财务数据失败: {e}"}

    # ─── 7b. 巨潮资讯公告 ───

    async def get_announcements(self, symbol: str) -> dict:
        """获取巨潮资讯公告列表（含PDF链接）。仅 A 股。"""
        market = detect_market(symbol)
        if market != "a":
            return {"success": False, "error": "该市场暂不支持公告数据"}
        symbol = normalize_symbol(symbol)
        cache_key = f"announcements:{symbol}"
        from services.data_router import get_router
        r = await get_router().fetch(
            "stock_announcement",
            cache_key=cache_key,
            ttl=TTL_COMPANY,
            validate=lambda x: x is not None,
            symbol=symbol,
        )
        if r["success"]:
            data = r["data"] or []
            # 字段统一:旧前端用 date/title/url,provider 给 time
            normalized = [{
                "title": it.get("title"),
                "date": (it.get("time") or "")[:10],
                "url": it.get("url"),
            } for it in data]
            return {"success": True, "data": normalized, "source": r["source"]}
        return {"success": True, "data": [], "error": r.get("error")}

    # ─── 8. 十大流通股东 ───

    async def get_shareholders(self, symbol: str) -> dict:
        """获取最新一期十大流通股东。仅 A 股支持。"""
        market = detect_market(symbol)
        if market != "a":
            return {"success": False, "error": "该市场暂不支持股东数据"}
        symbol = normalize_symbol(symbol)
        cache_key = f"holders:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(_patch_requests, ak.stock_circulate_stock_holder, symbol=symbol),
                timeout=30,
            )
            if df is None or df.empty:
                return {"success": False, "error": "未找到股东数据"}

            # 取最新一期（截止日期最大的 10 条）
            latest_date = df["截止日期"].max()
            latest = df[df["截止日期"] == latest_date].head(10)
            records = []
            for _, row in latest.iterrows():
                records.append({
                    "rank": _clean(row.get("编号")),
                    "name": _clean(row.get("股东名称")),
                    "shares": _clean(row.get("持股数量")),
                    "ratio": _clean(row.get("占流通股比例")),
                    "type": _clean(row.get("股本性质")),
                })
            resp = {
                "success": True,
                "data": {"date": str(latest_date), "holders": records},
            }
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except asyncio.TimeoutError:
            logger.error(f"获取股东数据超时 {symbol}")
            return {"success": False, "error": "股东数据请求超时，请稍后重试"}
        except Exception as e:
            logger.error(f"获取股东数据失败 {symbol}: {e}")
            return {"success": False, "error": f"获取股东数据失败: {e}"}
