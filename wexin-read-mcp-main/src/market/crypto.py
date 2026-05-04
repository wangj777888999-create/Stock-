"""Crypto market provider — CoinGecko free API."""

import logging

import httpx

from stock_utils import TTL_REALTIME, cache

from .base import MarketProvider

logger = logging.getLogger(__name__)

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"


async def _fetch_coins(per_page: int = 50, page: int = 1) -> list[dict]:
    """Fetch top coins from CoinGecko."""
    url = f"{_COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": page,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def _parse_coin(c: dict) -> dict:
    return {
        "code": c.get("symbol", "").upper(),
        "name": c.get("name", ""),
        "price": c.get("current_price"),
        "change_24h": c.get("price_change_percentage_24h"),
        "market_cap": c.get("market_cap"),
        "volume_24h": c.get("total_volume"),
        "rank": c.get("market_cap_rank"),
        "image": c.get("image", ""),
    }


class CryptoProvider(MarketProvider):
    name = "crypto"
    label = "币圈"

    async def get_boards(self):
        """Crypto has no boards — return empty."""
        return {"success": True, "data": []}

    async def get_board_stocks(self, board_name: str):
        return {"success": False, "error": "币圈无板块，请使用行情或搜索"}

    async def get_spot(self):
        """Top 50 coins by market cap."""
        ck = "market:crypto:spot"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            raw = await _fetch_coins(per_page=50)
            data = [_parse_coin(c) for c in raw]
            resp = {"success": True, "data": data}
            cache.set(ck, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"CryptoProvider.get_spot error: {e}")
            return {"success": False, "error": str(e)}

    async def search(self, keyword: str):
        """Search coins by name or symbol via CoinGecko search API."""
        if not keyword:
            return await self.get_spot()

        ck = f"market:crypto:search:{keyword}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            url = f"{_COINGECKO_BASE}/search"
            params = {"query": keyword}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                result = resp.json()

            coins = result.get("coins", [])[:20]
            data = [
                {
                    "code": c["symbol"].upper(),
                    "name": c["name"],
                    "rank": c.get("market_cap_rank"),
                }
                for c in coins
            ]
            resp_data = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp_data, TTL_REALTIME)
            return resp_data
        except Exception as e:
            logger.error(f"CryptoProvider.search error: {e}")
            return {"success": False, "error": str(e)}
