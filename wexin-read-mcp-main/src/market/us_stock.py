"""US stock market provider — curated sector boards with major stocks."""

import logging

from .base import MarketProvider

logger = logging.getLogger(__name__)

# Known US industry sectors
_US_SECTORS = [
    {"name": "科技", "code": "technology"},
    {"name": "医疗保健", "code": "healthcare"},
    {"name": "金融", "code": "financial"},
    {"name": "消费", "code": "consumer"},
    {"name": "能源", "code": "energy"},
    {"name": "工业", "code": "industrial"},
    {"name": "通信", "code": "communication"},
    {"name": "公用事业", "code": "utilities"},
    {"name": "房地产", "code": "real_estate"},
    {"name": "材料", "code": "materials"},
    {"name": "必需消费", "code": "consumer_staples"},
]

# Major US stocks by sector (curated list)
_US_SECTOR_STOCKS = {
    "technology": [
        {"code": "AAPL", "name": "Apple"},
        {"code": "MSFT", "name": "Microsoft"},
        {"code": "GOOGL", "name": "Alphabet"},
        {"code": "AMZN", "name": "Amazon"},
        {"code": "NVDA", "name": "NVIDIA"},
        {"code": "META", "name": "Meta Platforms"},
        {"code": "TSLA", "name": "Tesla"},
        {"code": "AVGO", "name": "Broadcom"},
        {"code": "ORCL", "name": "Oracle"},
        {"code": "CRM", "name": "Salesforce"},
        {"code": "AMD", "name": "AMD"},
        {"code": "ADBE", "name": "Adobe"},
        {"code": "INTC", "name": "Intel"},
        {"code": "CSCO", "name": "Cisco"},
        {"code": "IBM", "name": "IBM"},
    ],
    "healthcare": [
        {"code": "LLY", "name": "Eli Lilly"},
        {"code": "UNH", "name": "UnitedHealth"},
        {"code": "JNJ", "name": "Johnson & Johnson"},
        {"code": "PFE", "name": "Pfizer"},
        {"code": "ABBV", "name": "AbbVie"},
        {"code": "MRK", "name": "Merck"},
        {"code": "TMO", "name": "Thermo Fisher"},
        {"code": "ABT", "name": "Abbott"},
    ],
    "financial": [
        {"code": "BRK.B", "name": "Berkshire Hathaway"},
        {"code": "JPM", "name": "JPMorgan Chase"},
        {"code": "V", "name": "Visa"},
        {"code": "MA", "name": "Mastercard"},
        {"code": "BAC", "name": "Bank of America"},
        {"code": "WFC", "name": "Wells Fargo"},
        {"code": "GS", "name": "Goldman Sachs"},
        {"code": "MS", "name": "Morgan Stanley"},
    ],
    "consumer": [
        {"code": "WMT", "name": "Walmart"},
        {"code": "COST", "name": "Costco"},
        {"code": "HD", "name": "Home Depot"},
        {"code": "MCD", "name": "McDonald's"},
        {"code": "NKE", "name": "Nike"},
        {"code": "SBUX", "name": "Starbucks"},
    ],
    "energy": [
        {"code": "XOM", "name": "Exxon Mobil"},
        {"code": "CVX", "name": "Chevron"},
        {"code": "COP", "name": "ConocoPhillips"},
        {"code": "SLB", "name": "Schlumberger"},
    ],
    "communication": [
        {"code": "GOOG", "name": "Alphabet (C)"},
        {"code": "DIS", "name": "Walt Disney"},
        {"code": "NFLX", "name": "Netflix"},
        {"code": "CMCSA", "name": "Comcast"},
        {"code": "T", "name": "AT&T"},
    ],
}

# Flatten for search
_ALL_US_STOCKS = []
for sector, stocks in _US_SECTOR_STOCKS.items():
    for s in stocks:
        _ALL_US_STOCKS.append({**s, "sector": sector})


class USStockProvider(MarketProvider):
    name = "us_stock"
    label = "美股"

    async def get_boards(self):
        return {"success": True, "data": _US_SECTORS}

    async def get_board_stocks(self, board_name: str):
        # Find by name or code
        code = board_name
        for s in _US_SECTORS:
            if s["name"] == board_name:
                code = s["code"]
                break
        stocks = _US_SECTOR_STOCKS.get(code, [])
        return {"success": True, "data": stocks, "total": len(stocks)}

    async def get_spot(self):
        return {"success": False, "error": "美股请使用板块查询"}

    async def search(self, keyword: str):
        if not keyword:
            return {"success": True, "data": _ALL_US_STOCKS[:50], "total": len(_ALL_US_STOCKS)}
        kw = keyword.upper()
        results = [s for s in _ALL_US_STOCKS if kw in s["code"].upper() or kw in s["name"].upper()]
        return {"success": True, "data": results, "total": len(results)}
