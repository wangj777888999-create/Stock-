"""HK stock market provider — curated sector boards with major stocks."""

import logging
from stock_utils import cache, TTL_REALTIME
from .base import MarketProvider

logger = logging.getLogger(__name__)

_HK_SECTORS = [
    {"name": "科技", "code": "tech"},
    {"name": "金融", "code": "finance"},
    {"name": "地产", "code": "property"},
    {"name": "消费", "code": "consumer"},
    {"name": "医药", "code": "healthcare"},
    {"name": "能源", "code": "energy"},
    {"name": "电讯", "code": "telecom"},
    {"name": "工业", "code": "industrial"},
    {"name": "公用事业", "code": "utilities"},
]

_HK_SECTOR_STOCKS = {
    "tech": [
        {"code": "0700", "name": "腾讯控股"},
        {"code": "9988", "name": "阿里巴巴-SW"},
        {"code": "3690", "name": "美团-W"},
        {"code": "9999", "name": "网易-S"},
        {"code": "9618", "name": "京东集团-SW"},
        {"code": "9888", "name": "百度集团-SW"},
        {"code": "1810", "name": "小米集团-W"},
        {"code": "0268", "name": "金蝶国际"},
        {"code": "0241", "name": "阿里健康"},
        {"code": "6060", "name": "众安在线"},
    ],
    "finance": [
        {"code": "0005", "name": "汇丰控股"},
        {"code": "1398", "name": "工商银行"},
        {"code": "3988", "name": "中国银行"},
        {"code": "0939", "name": "建设银行"},
        {"code": "2318", "name": "中国平安"},
        {"code": "1299", "name": "友邦保险"},
        {"code": "0388", "name": "香港交易所"},
        {"code": "2628", "name": "中国人寿"},
        {"code": "6030", "name": "中信证券"},
    ],
    "property": [
        {"code": "1109", "name": "华润置地"},
        {"code": "0688", "name": "中国海外发展"},
        {"code": "1997", "name": "九龙仓置业"},
        {"code": "0016", "name": "新鸿基地产"},
        {"code": "0012", "name": "恒基地产"},
        {"code": "2007", "name": "碧桂园"},
    ],
    "consumer": [
        {"code": "0883", "name": "中国海洋石油"},
        {"code": "2313", "name": "申洲国际"},
        {"code": "0291", "name": "华润啤酒"},
        {"code": "1929", "name": "周大福"},
        {"code": "0322", "name": "康师傅控股"},
        {"code": "1579", "name": "颐海国际"},
    ],
    "healthcare": [
        {"code": "1099", "name": "国药控股"},
        {"code": "2269", "name": "药明生物"},
        {"code": "6060", "name": "众安在线"},
        {"code": "1177", "name": "中国生物制药"},
        {"code": "0241", "name": "阿里健康"},
    ],
    "energy": [
        {"code": "0883", "name": "中国海洋石油"},
        {"code": "0857", "name": "中国石油股份"},
        {"code": "0386", "name": "中国石油化工"},
        {"code": "1088", "name": "中国神华"},
    ],
    "telecom": [
        {"code": "0941", "name": "中国移动"},
        {"code": "0728", "name": "中国电信"},
        {"code": "0762", "name": "中国联通"},
    ],
    "industrial": [
        {"code": "0002", "name": "中电控股"},
        {"code": "0003", "name": "香港中华煤气"},
        {"code": "0006", "name": "电能实业"},
        {"code": "2388", "name": "中银香港"},
    ],
    "utilities": [
        {"code": "0002", "name": "中电控股"},
        {"code": "0003", "name": "香港中华煤气"},
        {"code": "0006", "name": "电能实业"},
    ],
}

_ALL_HK_STOCKS = []
for sector, stocks in _HK_SECTOR_STOCKS.items():
    for s in stocks:
        _ALL_HK_STOCKS.append({**s, "sector": sector})
# Deduplicate by code
_seen = set()
_ALL_HK_STOCKS = [s for s in _ALL_HK_STOCKS if s["code"] not in _seen and not _seen.add(s["code"])]


class HKStockProvider(MarketProvider):
    name = "hk_stock"
    label = "港股"

    async def get_boards(self):
        return {"success": True, "data": _HK_SECTORS}

    async def get_board_stocks(self, board_name: str):
        code = board_name
        for s in _HK_SECTORS:
            if s["name"] == board_name:
                code = s["code"]
                break
        stocks = _HK_SECTOR_STOCKS.get(code, [])
        return {"success": True, "data": stocks, "total": len(stocks)}

    async def get_spot(self):
        return {"success": False, "error": "港股请使用板块查询"}

    async def search(self, keyword: str):
        if not keyword:
            return {"success": True, "data": _ALL_HK_STOCKS[:50], "total": len(_ALL_HK_STOCKS)}
        results = [s for s in _ALL_HK_STOCKS if keyword in s["code"] or keyword in s["name"]]
        return {"success": True, "data": results, "total": len(results)}
