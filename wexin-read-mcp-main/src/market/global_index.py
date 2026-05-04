from .base import MarketProvider

class GlobalIndexProvider(MarketProvider):
    name = "global_index"
    label = "全球指数"

    async def get_boards(self):
        return {"success": False, "error": "not implemented"}

    async def get_board_stocks(self, board_name):
        return {"success": False, "error": "not implemented"}

    async def get_spot(self):
        return {"success": False, "error": "not implemented"}

    async def search(self, keyword):
        return {"success": False, "error": "not implemented"}
