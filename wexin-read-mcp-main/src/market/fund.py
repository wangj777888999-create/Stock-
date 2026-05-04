from .base import MarketProvider

class FundProvider(MarketProvider):
    name = "fund"
    label = "基金"

    async def get_boards(self):
        return {"success": False, "error": "not implemented"}

    async def get_board_stocks(self, board_name):
        return {"success": False, "error": "not implemented"}

    async def get_spot(self):
        return {"success": False, "error": "not implemented"}

    async def search(self, keyword):
        return {"success": False, "error": "not implemented"}
