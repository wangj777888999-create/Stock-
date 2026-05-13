"""
股票数据服务层 — A 股/美股/港股搜索、行情、详情、K线。

数据源：
- 搜索: A 股用 AKShare stock_info_a_code_name；美股/港股用精选静态列表
- 行情: 腾讯股票 API（qt.gtimg.cn），支持 A 股/美股/港股
- K线: 腾讯前复权日线 API（web.ifzq.gtimg.cn）
- 公司简介: AKShare stock_profile_cninfo（巨潮资讯）
- 财务指标: AKShare stock_financial_abstract_ths（同花顺）
- 资金流向: AKShare stock_individual_fund_flow（东方财富）
- 个股新闻: AKShare stock_news_em（东方财富）

扩展方式：在对应分组下新增方法，然后在 app.py 添加路由即可。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re

import akshare as ak

from stock_utils import (
    TTL_COMPANY,
    TTL_DAILY,
    TTL_KLINE,
    TTL_REALTIME,
    TTL_REALTIME_REFRESH,
    cache,
    detect_market,
    get_exchange,
    get_market_name,
    normalize_symbol,
)

from services.indicators import calc_rsi, calc_macd, calc_kdj, calc_boll
from http_client import session, patch_requests, get_async_client

logger = logging.getLogger("stock-service")

# 兼容旧调用名
_get = session.get
_patch_requests = patch_requests


# ─── 腾讯行情 API ───

_QT_URL = "http://qt.gtimg.cn/q={exchange}{code}"
# 字段索引: 1=名称 3=最新价 4=昨收 5=今开 31=涨跌额 32=涨跌幅
# 33=最高 34=最低 36=成交量(手) 37=成交额(万) 38=换手率
# 39=市盈率 43=振幅 44=总市值(亿) 45=流通市值(亿) 46=市净率


def _parse_tencent_quote(raw: str, symbol: str) -> dict | None:
    """解析腾讯行情 API 返回的单行数据。"""
    start = raw.find('"')
    end = raw.rfind('"')
    if start == -1 or end <= start:
        return None
    fields = raw[start + 1 : end].split("~")
    if len(fields) < 48 or not fields[3]:
        return None

    def _f(idx):
        try:
            return float(fields[idx])
        except (IndexError, ValueError):
            return None

    return {
        "代码": symbol, "名称": fields[1],
        "最新价": _f(3), "昨收": _f(4), "今开": _f(5),
        "最高": _f(33), "最低": _f(34),
        "涨跌额": _f(31), "涨跌幅": _f(32),
        "成交量": _f(36), "成交额": _f(37),
        "换手率": _f(38), "振幅": _f(43),
        "市盈率": _f(39), "总市值": _f(44),
        "流通市值": _f(45), "市净率": _f(46),
    }


def _clean(v):
    """将 NaN/NaT/numpy 类型转为 JSON 安全的 Python 原生类型。"""
    if v is None:
        return None
    # numpy 类型 → Python 原生
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


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


def _fmt_pct(v) -> str | None:
    """格式化百分比数值，保留 2 位小数 + '%'。"""
    v = _clean(v)
    if v is None:
        return None
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmt_amount(v) -> str | None:
    """将金额统一格式化为 X.XX亿 / X.XX万，保持正负号。"""
    v = _clean(v)
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e8:
        return f"{sign}{a / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{sign}{a / 1e4:.2f}万"
    return f"{sign}{a:.2f}"


# ─── 美股精选列表（代码, 名称） ───
_US_STOCKS = [
    {"code": "AAPL", "name": "苹果 Apple"},
    {"code": "MSFT", "name": "微软 Microsoft"},
    {"code": "GOOGL", "name": "谷歌 Alphabet"},
    {"code": "AMZN", "name": "亚马逊 Amazon"},
    {"code": "NVDA", "name": "英伟达 NVIDIA"},
    {"code": "META", "name": "Meta Platforms"},
    {"code": "TSLA", "name": "特斯拉 Tesla"},
    {"code": "AVGO", "name": "博通 Broadcom"},
    {"code": "ORCL", "name": "甲骨文 Oracle"},
    {"code": "CRM", "name": "赛富时 Salesforce"},
    {"code": "AMD", "name": "AMD"},
    {"code": "ADBE", "name": "Adobe"},
    {"code": "INTC", "name": "英特尔 Intel"},
    {"code": "CSCO", "name": "思科 Cisco"},
    {"code": "IBM", "name": "IBM"},
    {"code": "LLY", "name": "礼来 Eli Lilly"},
    {"code": "UNH", "name": "联合健康 UnitedHealth"},
    {"code": "JNJ", "name": "强生 Johnson & Johnson"},
    {"code": "PFE", "name": "辉瑞 Pfizer"},
    {"code": "ABBV", "name": "艾伯维 AbbVie"},
    {"code": "MRK", "name": "默沙东 Merck"},
    {"code": "ABT", "name": "雅培 Abbott"},
    {"code": "JPM", "name": "摩根大通 JPMorgan Chase"},
    {"code": "V", "name": "Visa"},
    {"code": "MA", "name": "万事达 Mastercard"},
    {"code": "BAC", "name": "美国银行 Bank of America"},
    {"code": "WFC", "name": "富国银行 Wells Fargo"},
    {"code": "GS", "name": "高盛 Goldman Sachs"},
    {"code": "MS", "name": "摩根士丹利 Morgan Stanley"},
    {"code": "WMT", "name": "沃尔玛 Walmart"},
    {"code": "COST", "name": "好市多 Costco"},
    {"code": "HD", "name": "家得宝 Home Depot"},
    {"code": "MCD", "name": "麦当劳 McDonald's"},
    {"code": "NKE", "name": "耐克 Nike"},
    {"code": "SBUX", "name": "星巴克 Starbucks"},
    {"code": "XOM", "name": "埃克森美孚 Exxon Mobil"},
    {"code": "CVX", "name": "雪佛龙 Chevron"},
    {"code": "NFLX", "name": "奈飞 Netflix"},
    {"code": "DIS", "name": "迪士尼 Walt Disney"},
    {"code": "PYPL", "name": "PayPal"},
    {"code": "SQ", "name": "Block (Square)"},
    {"code": "COIN", "name": "Coinbase"},
    {"code": "UBER", "name": "Uber"},
    {"code": "ABNB", "name": "Airbnb"},
    {"code": "SPOT", "name": "Spotify"},
    {"code": "SNAP", "name": "Snap"},
    {"code": "PINS", "name": "Pinterest"},
    {"code": "ZM", "name": "Zoom"},
    {"code": "PLTR", "name": "Palantir"},
    {"code": "SNOW", "name": "Snowflake"},
    {"code": "CRWD", "name": "CrowdStrike"},
    {"code": "PANW", "name": "Palo Alto Networks"},
    {"code": "NOW", "name": "ServiceNow"},
    {"code": "SHOP", "name": "Shopify"},
    {"code": "SE", "name": "Sea Limited"},
    {"code": "BABA", "name": "阿里巴巴 Alibaba"},
    {"code": "JD", "name": "京东 JD.com"},
    {"code": "PDD", "name": "拼多多 PDD Holdings"},
    {"code": "NIO", "name": "蔚来 NIO"},
    {"code": "XPEV", "name": "小鹏汽车 XPeng"},
    {"code": "LI", "name": "理想汽车 Li Auto"},
    {"code": "BRK.B", "name": "伯克希尔 Berkshire Hathaway"},
    {"code": "C", "name": "花旗集团 Citigroup"},
    {"code": "GE", "name": "通用电气 GE Aerospace"},
    {"code": "CAT", "name": "卡特彼勒 Caterpillar"},
    {"code": "BA", "name": "波音 Boeing"},
    {"code": "LMT", "name": "洛克希德·马丁 Lockheed Martin"},
    {"code": "RTX", "name": "RTX Corporation"},
    {"code": "DE", "name": "迪尔 Deere & Company"},
    {"code": "UPS", "name": "UPS"},
    {"code": "FDX", "name": "联邦快递 FedEx"},
    {"code": "T", "name": "AT&T"},
    {"code": "VZ", "name": "Verizon"},
    {"code": "KO", "name": "可口可乐 Coca-Cola"},
    {"code": "PEP", "name": "百事可乐 PepsiCo"},
    {"code": "PG", "name": "宝洁 Procter & Gamble"},
    {"code": "CL", "name": "高露洁 Colgate-Palmolive"},
    {"code": "TMO", "name": "赛默飞 Thermo Fisher"},
    {"code": "DHR", "name": "丹纳赫 Danaher"},
    {"code": "AMGN", "name": "安进 Amgen"},
    {"code": "GILD", "name": "吉利德 Gilead Sciences"},
    {"code": "BMY", "name": "百时美施贵宝 BMS"},
    {"code": "CVS", "name": "CVS Health"},
    {"code": "LOW", "name": "劳氏 Lowe's"},
    {"code": "TGT", "name": "塔吉特 Target"},
    {"code": "COP", "name": "康菲石油 ConocoPhillips"},
    {"code": "SLB", "name": "斯伦贝谢 Schlumberger"},
    {"code": "NEE", "name": "新纪元能源 NextEra Energy"},
    {"code": "SO", "name": "南方公司 Southern Company"},
    {"code": "DUK", "name": "杜克能源 Duke Energy"},
    {"code": "PLD", "name": "普洛斯 Prologis"},
    {"code": "AMT", "name": "美国塔 American Tower"},
    {"code": "CCI", "name": "冠城国际 Crown Castle"},
    {"code": "SPG", "name": "西蒙地产 Simon Property"},
    {"code": "ISRG", "name": "直觉外科 Intuitive Surgical"},
    {"code": "REGN", "name": "再生元 Regeneron"},
    {"code": "VRTX", "name": "顶点制药 Vertex"},
    {"code": "ZTS", "name": "硕腾 Zoetis"},
    {"code": "SYK", "name": "史赛克 Stryker"},
    {"code": "BSX", "name": "波士顿科学 Boston Scientific"},
    {"code": "MDT", "name": "美敦力 Medtronic"},
]

# ─── 港股精选列表（代码, 名称） ───
_HK_STOCKS = [
    {"code": "00700", "name": "腾讯控股"},
    {"code": "09988", "name": "阿里巴巴-SW"},
    {"code": "03690", "name": "美团-W"},
    {"code": "09999", "name": "网易-S"},
    {"code": "09618", "name": "京东集团-SW"},
    {"code": "09888", "name": "百度集团-SW"},
    {"code": "01810", "name": "小米集团-W"},
    {"code": "00268", "name": "金蝶国际"},
    {"code": "00241", "name": "阿里健康"},
    {"code": "06060", "name": "众安在线"},
    {"code": "00005", "name": "汇丰控股"},
    {"code": "01398", "name": "工商银行"},
    {"code": "03988", "name": "中国银行"},
    {"code": "00939", "name": "建设银行"},
    {"code": "02318", "name": "中国平安"},
    {"code": "01299", "name": "友邦保险"},
    {"code": "00388", "name": "香港交易所"},
    {"code": "02628", "name": "中国人寿"},
    {"code": "06030", "name": "中信证券"},
    {"code": "01109", "name": "华润置地"},
    {"code": "00688", "name": "中国海外发展"},
    {"code": "00016", "name": "新鸿基地产"},
    {"code": "00012", "name": "恒基地产"},
    {"code": "00883", "name": "中国海洋石油"},
    {"code": "02313", "name": "申洲国际"},
    {"code": "00291", "name": "华润啤酒"},
    {"code": "01929", "name": "周大福"},
    {"code": "00322", "name": "康师傅控股"},
    {"code": "01099", "name": "国药控股"},
    {"code": "02269", "name": "药明生物"},
    {"code": "01177", "name": "中国生物制药"},
    {"code": "00857", "name": "中国石油股份"},
    {"code": "00386", "name": "中国石油化工"},
    {"code": "01088", "name": "中国神华"},
    {"code": "00941", "name": "中国移动"},
    {"code": "00728", "name": "中国电信"},
    {"code": "00762", "name": "中国联通"},
    {"code": "00002", "name": "中电控股"},
    {"code": "00003", "name": "香港中华煤气"},
    {"code": "00006", "name": "电能实业"},
    {"code": "02388", "name": "中银香港"},
    {"code": "00992", "name": "联想集团"},
    {"code": "02018", "name": "瑞声科技"},
    {"code": "00981", "name": "中芯国际"},
    {"code": "02020", "name": "安踏体育"},
    {"code": "02331", "name": "李宁"},
    {"code": "01211", "name": "比亚迪股份"},
    {"code": "09901", "name": "新东方在线"},
    {"code": "09626", "name": "哔哩哔哩-SW"},
    {"code": "09868", "name": "小鹏汽车-W"},
    {"code": "09866", "name": "蔚来-SW"},
    {"code": "02015", "name": "理想汽车-W"},
    {"code": "06618", "name": "京东健康"},
    {"code": "09698", "name": "万国数据-SW"},
    {"code": "09961", "name": "携程集团-S"},
    {"code": "03888", "name": "金山软件"},
    {"code": "01833", "name": "平安好医生"},
    {"code": "06098", "name": "碧桂园服务"},
    {"code": "02202", "name": "万科企业"},
]


class StockService:
    """A 股数据服务，按功能分区。"""

    # 类级别缓存：预加载的股票列表 DataFrame
    _stock_list_cache: dict | None = None
    _stock_list_loaded: bool = False

    @classmethod
    async def preload_stock_list(cls) -> bool:
        """预加载股票列表到内存，启动时调用一次即可。返回是否成功。"""
        if cls._stock_list_loaded and cls._stock_list_cache is not None:
            return True

        import json as _json
        import pandas as pd
        from pathlib import Path

        # 优先加载本地离线列表（由问财生成，无需网络）
        local_path = Path(__file__).parent / "stock_list.json"
        if local_path.exists():
            try:
                data = _json.loads(local_path.read_text(encoding="utf-8"))
                df = pd.DataFrame(data)  # columns: code, name
                cls._stock_list_cache = df
                cls._stock_list_loaded = True
                logger.info(f"从本地文件加载股票列表，共 {len(df)} 只")
                return True
            except Exception as e:
                logger.warning(f"本地股票列表读取失败: {e}，尝试网络获取")

        # 本地不存在时走网络
        return await cls._refresh_stock_list_bg()

    @classmethod
    async def _refresh_stock_list_bg(cls) -> bool:
        """后台从 AKShare 刷新股票列表并写入本地文件。"""
        import json as _json
        import pandas as pd
        from pathlib import Path

        try:
            logger.info("后台刷新股票列表...")
            df = await asyncio.wait_for(
                asyncio.to_thread(_patch_requests, ak.stock_info_a_code_name),
                timeout=15,
            )
            cls._stock_list_cache = df
            cls._stock_list_loaded = True
            logger.info(f"股票列表刷新完成，共 {len(df)} 只")
            # 写回本地文件（供下次离线使用）
            local_path = Path(__file__).parent / "stock_list.json"
            records = df[["code", "name"]].to_dict(orient="records")
            local_path.write_text(_json.dumps(records, ensure_ascii=False), encoding="utf-8")
            return True
        except asyncio.TimeoutError:
            logger.warning("网络刷新股票列表超时")
            cls._stock_list_loaded = True
            return False
        except Exception as e:
            logger.error(f"网络刷新股票列表失败: {e}")
            cls._stock_list_loaded = True
            return False

    # ─── 1. 搜索 ───

    async def search_stock(self, keyword: str) -> dict:
        """搜索股票，支持 A 股/美股/港股。"""
        cache_key = f"search:{keyword}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        results = []

        try:
            # 1. 搜索 A 股（优先用内存缓存，未加载时触发预加载）
            if StockService._stock_list_cache is None:
                await StockService.preload_stock_list()
            df = StockService._stock_list_cache

            if df is not None:
                mask = (
                    df["code"].str.contains(keyword, case=False, na=False)
                    | df["name"].str.contains(keyword, case=False, na=False)
                )
                matched = df[mask].head(15)
                for _, row in matched.iterrows():
                    results.append({"code": row["code"], "name": row["name"], "market": "a"})

            # 2. 搜索美股
            kw_upper = keyword.upper()
            for s in _US_STOCKS:
                if kw_upper in s["code"].upper() or keyword in s["name"]:
                    results.append({"code": s["code"], "name": s["name"], "market": "us"})
                    if len(results) >= 25:
                        break

            # 3. 搜索港股
            if len(results) < 25:
                for s in _HK_STOCKS:
                    if keyword in s["code"] or keyword in s["name"]:
                        results.append({"code": s["code"], "name": s["name"], "market": "hk"})
                        if len(results) >= 25:
                            break

            resp = {"success": True, "data": results[:25]}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"搜索股票失败: {e}")
            return {"success": False, "error": f"搜索失败: {e}"}

    # ─── 2. 实时行情 ───

    async def get_realtime_quote(self, symbol: str, bypass_cache: bool = False) -> dict:
        """通过腾讯 API 获取实时行情，支持 A 股/美股/港股。"""
        market = detect_market(symbol)
        original = str(symbol).strip()
        symbol = normalize_symbol(symbol)
        cache_key = f"quote:{symbol}"
        if not bypass_cache:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            if market == "a":
                exchange = get_exchange(symbol)
                url_code = symbol
            elif market == "hk":
                exchange = "hk"
                url_code = original  # 港股用原始 5 位代码
            else:
                exchange = "us"
                url_code = symbol

            url = _QT_URL.format(exchange=exchange, code=url_code)
            r = await get_async_client().get(url, timeout=10)
            text = r.content.decode("gbk", errors="replace")
            record = _parse_tencent_quote(text, symbol)
            if record is None:
                return {"success": False, "error": f"未找到股票 {symbol}"}

            # 补充市场标识
            record["市场"] = {"a": "A股", "hk": "港股", "us": "美股"}[market]
            resp = {"success": True, "data": record}
            ttl = TTL_REALTIME_REFRESH if bypass_cache else TTL_REALTIME
            cache.set(cache_key, resp, ttl)
            return resp
        except Exception as e:
            logger.error(f"获取行情失败 {symbol}: {e}")
            return {"success": False, "error": f"获取行情失败: {e}"}

    # ─── 3. K线历史数据 ───

    async def get_kline(self, symbol: str, period: str = "day", count: int = 120, indicators: str = "") -> dict:
        """获取前复权 K 线数据。A 股/港股/美股均用 AKShare。period: day/week/month"""
        market = detect_market(symbol)  # 先识别市场（normalize 会补零破坏港股代码）
        original = str(symbol).strip()  # 保留原始代码（港股需要 5 位）
        norm = normalize_symbol(symbol)
        cache_key = f"kline:{norm}:{period}:{count}:{indicators}"
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
                    # A 股日/周/月：三源竞速 (AKShare / 腾讯 / mootdx)
                    from services.mootdx_provider import fetch_mootdx_kline
                    from services.source_racer import race_sources

                    period_map = {"day": "daily", "week": "weekly", "month": "monthly"}
                    ak_period = period_map.get(period, "daily")

                    async def _ak():
                        try:
                            df = await asyncio.wait_for(
                                asyncio.to_thread(
                                    _patch_requests, ak.stock_zh_a_hist,
                                    symbol=norm, period=ak_period,
                                    start_date="20050101", end_date="20300101", adjust="qfq",
                                ),
                                timeout=5.0,
                            )
                            if df is not None and not df.empty:
                                col_map = {f: _resolve_col(df.columns, a) for f, a in KLINE_COL_ALIASES.items()}
                                missing = [f for f, c in col_map.items() if c is None]
                                if missing:
                                    return None
                                if count < 99999:
                                    df = df.tail(count)
                                recs = []
                                for _, row in df.iterrows():
                                    rec = _extract_kline_row(row, col_map)
                                    if rec:
                                        recs.append(rec)
                                return recs if recs else None
                        except Exception:
                            return None

                    async def _tx():
                        try:
                            exchange_prefix = get_exchange(norm)
                            tencent_period = {"day": "day", "week": "week", "month": "month"}[period]
                            url = (
                                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                                f"?param={exchange_prefix}{norm},{tencent_period},,,{min(count, 640)},qfq"
                            )
                            r = await get_async_client().get(url, timeout=10)
                            data = r.json()
                            kdata_node = data.get("data", {}).get(f"{exchange_prefix}{norm}", {})
                            kdata = kdata_node.get(tencent_period, []) or \
                                    kdata_node.get(f"qfq{tencent_period}", []) or \
                                    kdata_node.get("qfqday", []) or \
                                    kdata_node.get("day", [])
                            if kdata:
                                recs = []
                                for item in kdata:
                                    recs.append({
                                        "date": item[0],
                                        "open": float(item[1]) if item[1] else None,
                                        "close": float(item[2]) if item[2] else None,
                                        "high": float(item[3]) if item[3] else None,
                                        "low": float(item[4]) if item[4] else None,
                                        "volume": float(item[5]) if len(item) > 5 and item[5] else None,
                                    })
                                return recs if recs else None
                        except Exception:
                            return None

                    async def _mootdx():
                        return await fetch_mootdx_kline(norm, period, count)

                    winner_sid, records = await race_sources(
                        [("akshare", _ak), ("tencent", _tx), ("mootdx", _mootdx)],
                        timeout=8.0,
                        validate=lambda r: bool(r) and len(r) > 0,
                    )

                    if winner_sid:
                        logger.debug(f"K线竞速胜出: {winner_sid} ({symbol})")

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
            cache.set(cache_key, resp, TTL_KLINE)
            return resp
        except Exception as e:
            logger.error(f"获取K线失败 {symbol}: {e}")
            return {"success": False, "error": f"获取K线失败: {e}"}

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
                from services.source_racer import race_sources
                from services.eastmoney import get_company_profile as em_profile

                async def _akshare_profile():
                    df = await asyncio.wait_for(
                        asyncio.to_thread(_patch_requests, ak.stock_profile_cninfo, symbol=norm),
                        timeout=12,
                    )
                    if df is None or df.empty:
                        return None
                    row = df.iloc[0]
                    return {k: _clean(row.get(k)) for k in [
                        "公司名称", "A股简称", "所属行业", "上市日期",
                        "注册资金", "法人代表", "官方网站", "主营业务",
                        "经营范围", "注册地址",
                    ]}

                async def _emweb_profile():
                    return await em_profile(norm, market)

                sid, record = await race_sources(
                    [("akshare_cninfo", _akshare_profile), ("emweb_profile", _emweb_profile)],
                    timeout=30,
                    validate=lambda r: bool(r),
                )
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
                from services.source_racer import race_sources
                from services.eastmoney import get_financial as em_financial

                async def _akshare_financial():
                    df = await asyncio.wait_for(
                        asyncio.to_thread(
                            _patch_requests, ak.stock_financial_abstract_ths,
                            symbol=norm, indicator="按报告期",
                        ),
                        timeout=12,
                    )
                    if df is None or df.empty:
                        return None
                    df = df.sort_values("报告期", ascending=False).head(16)
                    pick_cols = [
                        "报告期", "净利润", "净利润同比增长率", "营业总收入", "营业总收入同比增长率",
                        "基本每股收益", "每股净资产", "每股经营现金流",
                        "销售净利率", "销售毛利率", "净资产收益率", "资产负债率", "流动比率",
                    ]
                    existing = [c for c in pick_cols if c in df.columns]
                    rows = df[existing].to_dict(orient="records")
                    for r in rows:
                        for k, v in r.items():
                            r[k] = None if v in (False, "False", "false") else _clean(v)
                    return rows

                async def _emweb_financial():
                    return await em_financial(norm, market)

                sid, records = await race_sources(
                    [("akshare_ths", _akshare_financial), ("emweb_financial", _emweb_financial)],
                    timeout=30,
                    validate=lambda r: bool(r),
                )
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

    # ─── 6. 资金流向 ───

    async def get_money_flow(self, symbol: str) -> dict:
        """获取近期资金流向（主力/超大单/大单/中单/小单）。仅 A 股支持。"""
        market = detect_market(symbol)
        if market != "a":
            return {"success": False, "error": "该市场暂不支持资金流向数据"}
        symbol = normalize_symbol(symbol)
        cache_key = f"flow:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = get_exchange(symbol)
            df = await asyncio.wait_for(
                asyncio.to_thread(
                    _patch_requests, ak.stock_individual_fund_flow,
                    stock=symbol, market=exchange,
                ),
                timeout=20,
            )
            if df is None or df.empty:
                return {"success": False, "error": "未找到资金流向数据"}

            # 取最近 20 天
            df = df.tail(20).iloc[::-1]
            pick_cols = [
                "日期", "收盘价", "涨跌幅",
                "主力净流入-净额", "主力净流入-净占比",
                "超大单净流入-净额", "超大单净流入-净占比",
                "大单净流入-净额", "大单净流入-净占比",
                "中单净流入-净额", "小单净流入-净额",
            ]
            existing = [c for c in pick_cols if c in df.columns]
            amount_cols = {c for c in existing if "净额" in c}
            records = df[existing].to_dict(orient="records")
            for r in records:
                for k, v in r.items():
                    if hasattr(v, "strftime"):
                        r[k] = v.strftime("%Y-%m-%d")
                    elif k in amount_cols:
                        r[k] = _fmt_amount(v)
                    else:
                        r[k] = _clean(v)
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except asyncio.TimeoutError:
            logger.error(f"获取资金流向超时 {symbol}")
            return {"success": False, "error": "资金流向请求超时，请稍后重试"}
        except Exception as e:
            logger.error(f"获取资金流向失败 {symbol}: {e}")
            return {"success": False, "error": f"获取资金流向失败: {e}"}

    # ─── 7. 个股新闻 ───

    async def get_news(self, symbol: str) -> dict:
        """获取个股相关新闻。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"news:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_news_em, symbol=symbol,
            )
            if df is None or df.empty:
                return {"success": True, "data": []}

            records = []
            for _, row in df.head(15).iterrows():
                records.append({
                    "title": _clean(row.get("新闻标题")),
                    "time": str(row.get("发布时间", "")),
                    "source": _clean(row.get("文章来源")),
                    "url": _clean(row.get("新闻链接")),
                    "summary": _clean(row.get("新闻内容", ""))[:120] if row.get("新闻内容") else None,
                })
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取新闻失败 {symbol}: {e}")
            return {"success": False, "error": f"获取新闻失败: {e}"}

    # ─── 7b. 巨潮资讯公告 ───

    async def get_announcements(self, symbol: str) -> dict:
        """获取巨潮资讯公告列表（含PDF链接）。仅 A 股。"""
        market = detect_market(symbol)
        if market != "a":
            return {"success": False, "error": "该市场暂不支持公告数据"}
        symbol = normalize_symbol(symbol)
        cache_key = f"announcements:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from datetime import datetime, timedelta
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
            df = await asyncio.wait_for(
                asyncio.to_thread(
                    _patch_requests, ak.stock_zh_a_disclosure_report_cninfo,
                    symbol=symbol, start_date=start_date, end_date=end_date,
                ),
                timeout=30,
            )
            if df is None or df.empty:
                return {"success": True, "data": []}

            records = []
            for _, row in df.head(30).iterrows():
                title = _clean(row.iloc[2]) if len(row) > 2 else None
                date = str(row.iloc[3])[:10] if len(row) > 3 else None
                url = _clean(row.iloc[4]) if len(row) > 4 else None
                if title:
                    records.append({"title": title, "date": date, "url": url})
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except asyncio.TimeoutError:
            logger.error(f"获取公告超时 {symbol}")
            return {"success": False, "error": "公告请求超时，请稍后重试"}
        except Exception as e:
            logger.error(f"获取公告失败 {symbol}: {e}")
            return {"success": False, "error": f"获取公告失败: {e}"}

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
