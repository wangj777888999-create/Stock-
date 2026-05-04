"""A-stock market provider — wraps existing IWencaiService without modification."""

import sys
from pathlib import Path

# Ensure src is on path for existing module imports
_src = str(Path(__file__).parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

from iwencai_service import IWencaiService
from .base import MarketProvider

_wencai = IWencaiService()


class AShareProvider(MarketProvider):
    name = "a_share"
    label = "A股"

    async def get_boards(self):
        return await _wencai.get_sectors()

    async def get_board_stocks(self, board_name: str):
        return await _wencai.get_sector_stocks(board_name)

    async def get_spot(self):
        # A-share uses boards, not spot
        return {"success": False, "error": "A股请使用板块查询"}

    async def search(self, keyword: str):
        return await _wencai.query(keyword)
