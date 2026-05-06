"""
全球股票数据服务 — 基于 yfinance，覆盖韩国（KRX）和日本（TSE）股市。

作为 AKShare / 腾讯 API 无法覆盖市场的备选数据源。
"""

from __future__ import annotations

import asyncio
import logging
import math

import yfinance as yf

from stock_utils import TTL_COMPANY, TTL_DAILY, TTL_REALTIME, cache

logger = logging.getLogger("stock-service")


# ─── 工具函数 ───


def _clean(v):
    """将 NaN/NaT/numpy 类型转为 JSON 安全的 Python 原生类型。"""
    if v is None:
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _to_yahoo_symbol(symbol: str, market: str) -> str:
    """内部代码 → Yahoo Finance 格式。"""
    s = symbol.strip().upper()
    if ".KS" in s or ".KQ" in s or ".T" in s:
        return s
    if market == "kr":
        return f"{s}.KS"
    if market == "jp":
        return f"{s}.T"
    return s


# ─── 静态精选列表 ───

_KR_STOCKS = [
    {"code": "000660.KS", "name": "SK海力士 SK Hynix"},
    {"code": "005930.KS", "name": "三星电子 Samsung Electronics"},
    {"code": "005380.KS", "name": "现代汽车 Hyundai Motor"},
    {"code": "051910.KS", "name": "LG化学 LG Chem"},
    {"code": "035420.KS", "name": "NAVER"},
    {"code": "005490.KS", "name": "浦项制铁 POSCO Holdings"},
    {"code": "373220.KS", "name": "LG新能源 LG Energy Solution"},
    {"code": "006400.KS", "name": "三星SDI Samsung SDI"},
    {"code": "028260.KS", "name": "三星物产 Samsung C&T"},
    {"code": "105560.KS", "name": "KB金融 KB Financial"},
    {"code": "055550.KS", "name": "新韩金融 Shinhan Financial"},
    {"code": "096770.KS", "name": "SK创新 SK Innovation"},
    {"code": "017670.KS", "name": "SK电信 SK Telecom"},
    {"code": "034730.KS", "name": "SK控股 SK Inc"},
    {"code": "066570.KS", "name": "LG电子 LG Electronics"},
    {"code": "003550.KS", "name": "LG控股 LG Corp"},
    {"code": "015760.KS", "name": "韩国电力 KEPCO"},
    {"code": "032830.KS", "name": "三星生命 Samsung Life Insurance"},
    {"code": "207940.KS", "name": "三星生物 Samsung Biologics"},
    {"code": "086790.KS", "name": "韩亚金融 Hana Financial"},
]

_JP_STOCKS = [
    {"code": "6758.T", "name": "索尼 Sony"},
    {"code": "7203.T", "name": "丰田汽车 Toyota"},
    {"code": "9984.T", "name": "软银集团 SoftBank Group"},
    {"code": "6861.T", "name": "基恩士 Keyence"},
    {"code": "8306.T", "name": "三菱UFJ金融 MUFG"},
    {"code": "9432.T", "name": "日本电信电话 NTT"},
    {"code": "9433.T", "name": "KDDI"},
    {"code": "8035.T", "name": "东京电子 Tokyo Electron"},
    {"code": "6501.T", "name": "日立 Hitachi"},
    {"code": "6502.T", "name": "东芝 Toshiba"},
    {"code": "7267.T", "name": "本田汽车 Honda"},
    {"code": "7751.T", "name": "佳能 Canon"},
    {"code": "6752.T", "name": "松下 Panasonic"},
    {"code": "6701.T", "name": "NEC"},
    {"code": "8058.T", "name": "三菱商事 Mitsubishi Corp"},
    {"code": "8031.T", "name": "三井物产 Mitsui & Co"},
    {"code": "4502.T", "name": "武田制药 Takeda"},
    {"code": "4503.T", "name": "安斯泰来 Astellas Pharma"},
    {"code": "6367.T", "name": "大金工业 Daikin"},
    {"code": "9020.T", "name": "JR东日本 JR East"},
]


# ─── 服务类 ───


class GlobalStockService:
    """通过 yfinance 获取韩/日股票数据。"""

    async def search(self, keyword: str, market: str | None = None) -> dict:
        """从静态列表搜索韩/日股票。"""
        cache_key = f"global:search:{keyword}:{market}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        results = []
        kw_up = keyword.upper()
        kw_lower = keyword.lower()

        if market in (None, "kr"):
            for s in _KR_STOCKS:
                if kw_up in s["code"].upper() or kw_lower in s["name"].lower():
                    results.append({"code": s["code"], "name": s["name"], "market": "kr"})

        if market in (None, "jp"):
            for s in _JP_STOCKS:
                if kw_up in s["code"].upper() or kw_lower in s["name"].lower():
                    results.append({"code": s["code"], "name": s["name"], "market": "jp"})

        resp = {"success": True, "data": results[:20]}
        cache.set(cache_key, resp, TTL_COMPANY)
        return resp

    async def get_realtime_quote(self, symbol: str, market: str) -> dict:
        """获取韩/日股票实时行情。"""
        yf_symbol = _to_yahoo_symbol(symbol, market)
        cache_key = f"global:quote:{yf_symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        def _fetch():
            ticker = yf.Ticker(yf_symbol)
            info = ticker.fast_info
            last_price = _clean(info.get("lastPrice"))
            prev_close = _clean(info.get("previousClose"))
            change = None
            change_pct = None
            if last_price is not None and prev_close is not None and prev_close != 0:
                change = round(last_price - prev_close, 4)
                change_pct = round(change / prev_close * 100, 2)
            return {
                "代码": yf_symbol,
                "名称": yf_symbol,
                "最新价": last_price,
                "昨收": prev_close,
                "今开": _clean(info.get("open")),
                "最高": _clean(info.get("dayHigh")),
                "最低": _clean(info.get("dayLow")),
                "涨跌额": change,
                "涨跌幅": change_pct,
                "成交量": _clean(info.get("lastVolume")),
                "总市值": _clean(info.get("marketCap")),
                "成交额": None,
                "换手率": None,
                "振幅": None,
                "流通市值": None,
                "市盈率": None,
                "市净率": None,
                "市场": {"kr": "韩股", "jp": "日股"}[market],
            }

        try:
            record = await asyncio.to_thread(_fetch)
            resp = {"success": True, "data": record}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"yfinance 行情获取失败 {yf_symbol}: {e}")
            return {"success": False, "error": f"获取行情失败: {e}"}

    async def get_kline(
        self, symbol: str, market: str, period: str = "day", count: int = 120
    ) -> dict:
        """获取韩/日股票K线数据。"""
        yf_symbol = _to_yahoo_symbol(symbol, market)
        cache_key = f"global:kline:{yf_symbol}:{period}:{count}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        interval_map = {"day": "1d", "week": "1wk", "month": "1mo"}
        interval = interval_map.get(period, "1d")

        def _fetch():
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period="max", interval=interval)
            if hist is None or hist.empty:
                return []
            if count < 99999:
                hist = hist.tail(count)
            records = []
            for date, row in hist.iterrows():
                records.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "open": _clean(row.get("Open")),
                    "close": _clean(row.get("Close")),
                    "high": _clean(row.get("High")),
                    "low": _clean(row.get("Low")),
                    "volume": _clean(row.get("Volume")),
                })
            return records

        try:
            records = await asyncio.to_thread(_fetch)
            if not records:
                return {"success": False, "error": "暂无K线数据"}
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"yfinance K线获取失败 {yf_symbol}: {e}")
            return {"success": False, "error": f"获取K线失败: {e}"}

    async def get_profile(self, symbol: str, market: str) -> dict:
        """获取韩/日股票公司简介。"""
        yf_symbol = _to_yahoo_symbol(symbol, market)
        cache_key = f"global:profile:{yf_symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        def _fetch():
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info
            return {
                "公司名称": info.get("longName") or info.get("shortName"),
                "英文名称": info.get("longName"),
                "所属行业": info.get("industry"),
                "所属板块": info.get("sector"),
                "官方网站": info.get("website"),
                "主营业务": (info.get("longBusinessSummary") or "")[:300],
                "员工人数": _clean(info.get("fullTimeEmployees")),
                "注册地址": info.get("address1"),
                "国家": info.get("country"),
            }

        try:
            record = await asyncio.to_thread(_fetch)
            record = {k: _clean(v) for k, v in record.items()}
            resp = {"success": True, "data": record}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"yfinance 公司信息获取失败 {yf_symbol}: {e}")
            return {"success": False, "error": f"获取公司信息失败: {e}"}


# 单例
global_stock_service = GlobalStockService()
