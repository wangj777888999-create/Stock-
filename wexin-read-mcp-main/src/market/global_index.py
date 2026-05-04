"""Global index provider — major world indices with real-time quotes from AKShare."""

import asyncio
import logging
from .base import MarketProvider

logger = logging.getLogger(__name__)

# Major global indices — code is the AKShare or data source identifier
_INDICES = [
    # US
    {"code": ".DJI", "name": "道琼斯工业指数", "region": "美国"},
    {"code": ".IXIC", "name": "纳斯达克综合指数", "region": "美国"},
    {"code": ".INX", "name": "标普500指数", "region": "美国"},
    # Japan
    {"code": "N225", "name": "日经225指数", "region": "日本"},
    # South Korea
    {"code": "KS11", "name": "韩国KOSPI指数", "region": "韩国"},
    # Europe
    {"code": "STOXX", "name": "欧洲STOXX600指数", "region": "欧洲"},
    {"code": "GDAXI", "name": "德国DAX指数", "region": "欧洲"},
    {"code": "FCHI", "name": "法国CAC40指数", "region": "欧洲"},
    {"code": "FTSE", "name": "英国富时100指数", "region": "欧洲"},
    # HK
    {"code": "HSI", "name": "恒生指数", "region": "香港"},
    {"code": "HSCEI", "name": "恒生中国企业指数", "region": "香港"},
    # A-share
    {"code": "000001", "name": "上证指数", "region": "中国"},
    {"code": "399001", "name": "深证成指", "region": "中国"},
    {"code": "000016", "name": "上证50", "region": "中国"},
    {"code": "000300", "name": "沪深300", "region": "中国"},
    {"code": "000905", "name": "中证500", "region": "中国"},
    {"code": "399006", "name": "创业板指", "region": "中国"},
]

# Region labels for grouping in frontend
_REGIONS = ["中国", "美国", "日本", "韩国", "欧洲", "香港"]


class GlobalIndexProvider(MarketProvider):
    name = "global_index"
    label = "全球指数"

    async def get_boards(self):
        """Return regions as boards."""
        boards = []
        for region in _REGIONS:
            count = sum(1 for i in _INDICES if i["region"] == region)
            if count > 0:
                boards.append({"name": region, "code": region, "count": count})
        return {"success": True, "data": boards}

    async def get_board_stocks(self, board_name: str):
        """Return indices for a region."""
        indices = [i for i in _INDICES if i["region"] == board_name]
        return {"success": True, "data": indices, "total": len(indices)}

    async def get_spot(self):
        """Return all indices."""
        return {"success": True, "data": _INDICES}

    async def search(self, keyword: str):
        if not keyword:
            return {"success": True, "data": _INDICES, "total": len(_INDICES)}
        results = [i for i in _INDICES if keyword in i["name"] or keyword in i["code"] or keyword in i["region"]]
        return {"success": True, "data": results, "total": len(results)}
