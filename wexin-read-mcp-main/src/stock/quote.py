"""QuoteMixin — 股票搜索 + 实时行情 + 股票列表预加载。

数据源：
- 搜索: A 股用预加载列表（本地 stock_list.json，缺失走 AKShare stock_info_a_code_name）；
        美股/港股用精选静态列表。
- 行情: 统一 DataRouter（腾讯主 + 新浪兜底）。
"""

from __future__ import annotations

import asyncio
import logging

import akshare as ak

from stock_utils import (
    TTL_COMPANY,
    TTL_REALTIME,
    TTL_REALTIME_REFRESH,
    cache,
    detect_market,
    normalize_symbol,
)
from http_client import patch_requests

logger = logging.getLogger("stock-service")

_patch_requests = patch_requests


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


class QuoteMixin:
    """股票搜索 + 实时行情 + 股票列表预加载缓存。"""

    # 类级别缓存：预加载的股票列表 DataFrame。
    # 唯一定义处：StockService 经 MRO 命中此处，preload/_refresh/search 共享同一份。
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
        local_path = Path(__file__).resolve().parent.parent / "stock_list.json"
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
            local_path = Path(__file__).resolve().parent.parent / "stock_list.json"
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
        if not keyword or not keyword.strip():
            return {"success": True, "data": []}

        cache_key = f"search:{keyword}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        results = []

        try:
            # 1. 搜索 A 股（优先用内存缓存，未加载时触发预加载）
            if type(self)._stock_list_cache is None:
                await type(self).preload_stock_list()
            df = type(self)._stock_list_cache

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
        """通过统一 DataRouter 获取实时行情(腾讯主 + 新浪兜底)。"""
        market = detect_market(symbol)
        original = str(symbol).strip()
        symbol = normalize_symbol(symbol)
        cache_key = f"quote:{symbol}"
        ttl = TTL_REALTIME_REFRESH if bypass_cache else TTL_REALTIME

        # bypass_cache 时跳过缓存直接 race
        from services.data_router import get_router
        r = await get_router().fetch(
            "stock_quote",
            cache_key=None if bypass_cache else cache_key,
            ttl=ttl,
            validate=lambda x: x is not None and x.get("最新价") is not None,
            symbol=symbol, market=market, original=original,
        )
        if not r["success"]:
            return {"success": False, "error": r["error"]}
        return {"success": True, "data": r["data"], "source": r["source"]}
