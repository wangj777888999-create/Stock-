"""KlineMixin — 前复权 K 线（日/周/月/分钟）+ 技术指标。

数据源：
- A 股日/周/月: 统一 DataRouter（腾讯 + AKShare）
- A 股分钟级: 新浪 API
- 港股: AKShare stock_hk_hist
- 美股: AKShare stock_us_daily（日线，周/月本地聚合）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import akshare as ak

from stock_utils import (
    TTL_KLINE,
    _clean,
    cache,
    detect_market,
    get_exchange,
    normalize_symbol,
)
from services.indicators import calc_rsi, calc_macd, calc_kdj, calc_boll
from http_client import patch_requests, get_async_client

logger = logging.getLogger("stock-service")

_patch_requests = patch_requests


# ─── K 线列名别名映射（防御 AKShare 版本漂移）───
KLINE_COL_ALIASES = {
    "date":   ["日期", "date", "Date", "交易日"],
    "open":   ["开盘", "open", "Open", "开盘价"],
    "close":  ["收盘", "close", "Close", "收盘价"],
    "high":   ["最高", "high", "High", "最高价"],
    "low":    ["最低", "low", "Low", "最低价"],
    "volume": ["成交量", "volume", "Volume", "成交量(手)"],
}


def _resolve_col(df_columns, aliases):
    """从候选列名列表中找到 DataFrame 实际存在的列名，找不到返回 None。"""
    for name in aliases:
        if name in df_columns:
            return name
    return None


def _extract_kline_row(row, col_map):
    """用已解析的列映射从行中提取 OHLCV，任一关键列缺失则返回 None。"""
    date_col = col_map.get("date")
    date_val = row.get(date_col) if date_col else None
    if date_val is None:
        return None
    return {
        "date": str(date_val)[:10],
        "open": _clean(row.get(col_map.get("open"))),
        "close": _clean(row.get(col_map.get("close"))),
        "high": _clean(row.get(col_map.get("high"))),
        "low": _clean(row.get(col_map.get("low"))),
        "volume": _clean(row.get(col_map.get("volume"))),
    }


def _aggregate_kline(df, period: str):
    """将日线数据聚合为周线或月线。df 需有 date/open/high/low/close/volume 列。"""
    import pandas as pd
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    rule = "W" if period == "week" else "ME"
    agg = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    agg = agg.reset_index()
    agg["date"] = agg["date"].dt.strftime("%Y-%m-%d")
    return agg


class KlineMixin:
    """K 线历史数据。"""

    # ─── 3. K线历史数据 ───

    async def get_kline(self, symbol: str, period: str = "day", count: int = 120, indicators: str = "", bypass_cache: bool = False) -> dict:
        """获取前复权 K 线数据。A 股/港股/美股均用 AKShare。period: day/week/month"""
        market = detect_market(symbol)  # 先识别市场（normalize 会补零破坏港股代码）
        original = str(symbol).strip()  # 保留原始代码（港股需要 5 位）
        norm = normalize_symbol(symbol)
        cache_key = f"kline:{norm}:{period}:{count}:{indicators}"
        if not bypass_cache:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            if market == "a":
                minute_periods = {"1min", "5min", "15min", "30min", "60min"}
                if period in minute_periods:
                    # A 股分钟级：新浪 API（支持历史分钟数据）
                    exchange_prefix = get_exchange(norm)
                    scale_map = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
                    scale = scale_map[period]
                    sina_url = (
                        f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_1m_data="
                        f"/CN_MarketDataService.getKLineData"
                        f"?symbol={exchange_prefix}{norm}&scale={scale}&ma=no&datalen={count}"
                    )
                    r = await get_async_client().get(sina_url, timeout=10)
                    text = r.text
                    # 解析 JSONP：var _1m_data=(JSON);
                    m = re.search(r'=\((\[.*?\])\);', text, re.DOTALL)
                    if not m:
                        return {"success": False, "error": "暂无K线数据"}
                    raw_items = json.loads(m.group(1))
                    if not raw_items:
                        return {"success": False, "error": "暂无K线数据"}
                    records = []
                    for item in raw_items:
                        records.append({
                            "date": item["day"][:16],  # "2026-05-06 09:35"
                            "open": float(item["open"]),
                            "close": float(item["close"]),
                            "high": float(item["high"]),
                            "low": float(item["low"]),
                            "volume": int(float(item["volume"])),
                        })
                else:
                    # A 股日/周/月:经统一 DataRouter(腾讯 + AKShare)
                    from services.data_router import get_router
                    exchange_prefix = get_exchange(norm)
                    rr = await get_router().fetch(
                        "stock_kline_a",
                        cache_key=None,  # 外层 cache.set 处理
                        timeout=8.0,
                        validate=lambda x: bool(x) and len(x) > 0,
                        symbol=norm, period=period, count=count,
                        exchange_prefix=exchange_prefix,
                    )
                    records = rr.get("data") if rr.get("success") else None
                    if not records:
                        return {"success": False, "error": "暂无K线数据"}

            elif market == "hk":
                # 港股：AKShare stock_hk_hist
                period_map = {"day": "daily", "week": "weekly", "month": "monthly"}
                ak_period = period_map.get(period, "daily")
                try:
                    df = await asyncio.wait_for(
                        asyncio.to_thread(
                            _patch_requests, ak.stock_hk_hist,
                            symbol=original, period=ak_period,
                            start_date="20100101", end_date="20300101", adjust="qfq",
                        ),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"AKShare 请求超时 ({symbol})")
                    return {"success": False, "error": "行情数据源请求超时，请稍后重试"}
                if df is None or df.empty:
                    return {"success": False, "error": "暂无K线数据"}
                col_map = {f: _resolve_col(df.columns, a) for f, a in KLINE_COL_ALIASES.items()}
                missing = [f for f, c in col_map.items() if c is None]
                if missing:
                    logger.error(f"K线列名映射失败 ({symbol}): 缺少 {missing}，实际列={list(df.columns)}")
                    return {"success": False, "error": f"数据源列名变更，缺少: {missing}"}
                if count < 99999:
                    df = df.tail(count)
                records = []
                for _, row in df.iterrows():
                    rec = _extract_kline_row(row, col_map)
                    if rec:
                        records.append(rec)

            else:
                # 美股：AKShare stock_us_daily（仅支持日线）
                try:
                    df = await asyncio.wait_for(
                        asyncio.to_thread(
                            _patch_requests, ak.stock_us_daily,
                            symbol=norm, adjust="qfq",
                        ),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"AKShare 请求超时 ({symbol})")
                    return {"success": False, "error": "行情数据源请求超时，请稍后重试"}
                if df is None or df.empty:
                    return {"success": False, "error": "暂无K线数据"}
                # 美股列名已是英文，但仍走统一映射
                col_map = {f: _resolve_col(df.columns, a) for f, a in KLINE_COL_ALIASES.items()}
                missing = [f for f, c in col_map.items() if c is None]
                if missing:
                    logger.error(f"K线列名映射失败 ({symbol}): 缺少 {missing}，实际列={list(df.columns)}")
                    return {"success": False, "error": f"数据源列名变更，缺少: {missing}"}
                # 日线：直接取最近 count 条；周/月：需要聚合
                if period in ("week", "month"):
                    # 聚合需要英文列名
                    agg_df = df.rename(columns={col_map[k]: k for k in ("date","open","high","low","close","volume") if col_map.get(k)})
                    agg_df = _aggregate_kline(agg_df, period)
                    col_map = {f: _resolve_col(agg_df.columns, a) for f, a in KLINE_COL_ALIASES.items()}
                    df = agg_df
                if count < 99999:
                    df = df.tail(count)
                records = []
                for _, row in df.iterrows():
                    rec = _extract_kline_row(row, col_map)
                    if rec:
                        records.append(rec)

            # 计算技术指标
            ind_data = {}
            if indicators:
                close_prices = [r["close"] for r in records]
                requested = [x.strip().lower() for x in indicators.split(",") if x.strip()]
                if "rsi" in requested:
                    ind_data["rsi"] = {"period": 14, "values": calc_rsi(close_prices)}
                if "macd" in requested:
                    ind_data["macd"] = calc_macd(close_prices)
                if "kdj" in requested:
                    high_prices = [r["high"] for r in records]
                    low_prices = [r["low"] for r in records]
                    ind_data["kdj"] = calc_kdj(high_prices, low_prices, close_prices)
                if "boll" in requested:
                    ind_data["boll"] = calc_boll(close_prices)

            # 验证记录有效性：防止全 None 数据被缓存
            valid_records = [r for r in records if r.get("close") is not None and r.get("open") is not None]
            if not valid_records:
                logger.error(f"K线记录全部无效 ({symbol})，不写缓存")
                return {"success": False, "error": "K线数据解析失败，所有OHLC字段为空"}

            resp = {"success": True, "data": valid_records}
            if ind_data:
                resp["indicators"] = ind_data
            if not bypass_cache:
                cache.set(cache_key, resp, TTL_KLINE)
            return resp
        except Exception as e:
            logger.error(f"获取K线失败 {symbol}: {e}")
            return {"success": False, "error": f"获取K线失败: {e}"}
