"""
股票数据服务层 — A 股搜索、行情、详情、K线。

数据源：
- 搜索: AKShare stock_info_a_code_name（静态代码表）
- 行情: 腾讯股票 API（qt.gtimg.cn）
- K线: 腾讯前复权日线 API（web.ifzq.gtimg.cn）
- 公司简介: AKShare stock_profile_cninfo（巨潮资讯）
- 财务指标: AKShare stock_financial_abstract_ths（同花顺）
- 资金流向: AKShare stock_individual_fund_flow（东方财富）
- 个股新闻: AKShare stock_news_em（东方财富）

扩展方式：在对应分组下新增方法，然后在 app.py 添加路由即可。
"""

from __future__ import annotations

import asyncio
import logging
import math

import akshare as ak
import requests as _requests

from stock_utils import (
    TTL_COMPANY,
    TTL_DAILY,
    TTL_REALTIME,
    cache,
    detect_market,
    get_exchange,
    get_market_name,
    normalize_symbol,
)

logger = logging.getLogger("stock-service")

# ─── 绕过系统代理的请求 ───

_NO_PROXY = {"http": None, "https": None}


def _get(url, **kw):
    kw.setdefault("proxies", _NO_PROXY)
    return _requests.get(url, **kw)


def _patch_requests(func, **kwargs):
    """在绕过代理的环境下调用 AKShare 函数。"""
    orig_get = _requests.get
    orig_post = _requests.post
    _requests.get = lambda url, **kw: (kw.setdefault("proxies", _NO_PROXY), orig_get(url, **kw))[1]
    _requests.post = lambda url, **kw: (kw.setdefault("proxies", _NO_PROXY), orig_post(url, **kw))[1]
    try:
        return func(**kwargs)
    finally:
        _requests.get = orig_get
        _requests.post = orig_post


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
    """将 NaN/NaT 转为 None，保持 JSON 序列化安全。"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


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

        try:
            logger.info("正在预加载股票列表...")
            df = await asyncio.to_thread(_patch_requests, ak.stock_info_a_code_name)
            cls._stock_list_cache = df
            cls._stock_list_loaded = True
            logger.info(f"股票列表加载完成，共 {len(df)} 只股票")
            return True
        except Exception as e:
            logger.error(f"预加载股票列表失败: {e}")
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
            # 1. 搜索 A 股
            if StockService._stock_list_cache is not None:
                df = StockService._stock_list_cache
            else:
                df = await asyncio.to_thread(_patch_requests, ak.stock_info_a_code_name)

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

    async def get_realtime_quote(self, symbol: str) -> dict:
        """通过腾讯 API 获取实时行情，支持 A 股/美股/港股。"""
        market = detect_market(symbol)
        symbol = normalize_symbol(symbol)
        cache_key = f"quote:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            code = symbol
            if market == "a":
                exchange = get_exchange(symbol)
            elif market == "hk":
                exchange = "hk"
                # normalize_symbol 会将 5 位港股代码补零为 6 位，需还原
                if len(symbol) == 6 and symbol.startswith("0"):
                    code = symbol[1:]
            else:
                exchange = "us"

            url = _QT_URL.format(exchange=exchange, code=code)
            r = await asyncio.to_thread(_get, url, timeout=10)
            r.encoding = "gbk"
            record = _parse_tencent_quote(r.text, symbol)
            if record is None:
                return {"success": False, "error": f"未找到股票 {symbol}"}

            # 补充市场标识
            record["市场"] = {"a": "A股", "hk": "港股", "us": "美股"}[market]
            resp = {"success": True, "data": record}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"获取行情失败 {symbol}: {e}")
            return {"success": False, "error": f"获取行情失败: {e}"}

    # ─── 3. K线历史数据 ───

    async def get_kline(self, symbol: str, period: str = "day", count: int = 120) -> dict:
        """通过腾讯 API 获取前复权 K 线数据。period: day/week/month"""
        symbol = normalize_symbol(symbol)
        cache_key = f"kline:{symbol}:{period}:{count}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = get_exchange(symbol)
            url = (
                f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={exchange}{symbol},{period},2020-01-01,,{count},qfq"
            )
            r = await asyncio.to_thread(_get, url, timeout=10)
            data = r.json().get("data", {}).get(f"{exchange}{symbol}", {})
            klines = data.get("qfqday") or data.get("qfqweek") or data.get("qfqmonth") or data.get("day") or []

            records = []
            for k in klines:
                records.append({
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0,
                })
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取K线失败 {symbol}: {e}")
            return {"success": False, "error": f"获取K线失败: {e}"}

    # ─── 4. 公司简介 ───

    async def get_company_profile(self, symbol: str) -> dict:
        """获取公司基本信息（巨潮资讯）。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"profile:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(_patch_requests, ak.stock_profile_cninfo, symbol=symbol)
            if df is None or df.empty:
                return {"success": False, "error": "未找到公司信息"}
            row = df.iloc[0]
            record = {
                "公司名称": row.get("公司名称"),
                "A股简称": row.get("A股简称"),
                "所属行业": row.get("所属行业"),
                "上市日期": row.get("上市日期"),
                "注册资金": row.get("注册资金"),
                "法人代表": row.get("法人代表"),
                "官方网站": row.get("官方网站"),
                "主营业务": row.get("主营业务"),
                "经营范围": row.get("经营范围"),
                "注册地址": row.get("注册地址"),
            }
            record = {k: _clean(v) for k, v in record.items()}
            resp = {"success": True, "data": record}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"获取公司信息失败 {symbol}: {e}")
            return {"success": False, "error": f"获取公司信息失败: {e}"}

    # ─── 5. 财务指标 ───

    async def get_financial(self, symbol: str) -> dict:
        """获取最近几期核心财务指标（同花顺）。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"financial:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_financial_abstract_ths,
                symbol=symbol, indicator="按报告期",
            )
            if df is None or df.empty:
                return {"success": False, "error": "未找到财务数据"}

            # 按报告期降序（最新在前）
            df = df.sort_values("报告期", ascending=False).head(16)
            pick_cols = [
                "报告期", "净利润", "净利润同比增长率", "营业总收入", "营业总收入同比增长率",
                "基本每股收益", "每股净资产", "每股经营现金流",
                "销售净利率", "销售毛利率",
                "净资产收益率", "资产负债率", "流动比率",
            ]
            existing = [c for c in pick_cols if c in df.columns]
            records = df[existing].to_dict(orient="records")
            # 清理 false/NaN → None
            for r in records:
                for k, v in r.items():
                    if v is False or v == "False" or v == "false":
                        r[k] = None
                    else:
                        r[k] = _clean(v)
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取财务数据失败 {symbol}: {e}")
            return {"success": False, "error": f"获取财务数据失败: {e}"}

    # ─── 6. 资金流向 ───

    async def get_money_flow(self, symbol: str) -> dict:
        """获取近期资金流向（主力/超大单/大单/中单/小单）。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"flow:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = get_exchange(symbol)
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_individual_fund_flow,
                stock=symbol, market=exchange,
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

    # ─── 8. 十大流通股东 ───

    async def get_shareholders(self, symbol: str) -> dict:
        """获取最新一期十大流通股东。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"holders:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_circulate_stock_holder, symbol=symbol,
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
        except Exception as e:
            logger.error(f"获取股东数据失败 {symbol}: {e}")
            return {"success": False, "error": f"获取股东数据失败: {e}"}
