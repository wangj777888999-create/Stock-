"""Market provider base class — all market modules implement this interface."""

from abc import ABC, abstractmethod


class MarketProvider(ABC):
    """Unified interface for market data providers."""

    name: str       # internal key: "a_share", "us_stock", "crypto", etc.
    label: str      # display name: "A股", "美股", "币圈", etc.

    @abstractmethod
    async def get_boards(self) -> dict:
        """Return sector/industry boards.
        Returns: {"success": True, "data": [{"name": ..., "code": ...}, ...]}
        """

    @abstractmethod
    async def get_board_stocks(self, board_name: str) -> dict:
        """Return constituent stocks/funds for a board.
        Returns: {"success": True, "data": [{...}, ...], "total": N}
        """

    @abstractmethod
    async def get_spot(self) -> dict:
        """Return real-time quotes (for markets without boards: indices, crypto).
        Returns: {"success": True, "data": [{...}, ...]}
        """

    @abstractmethod
    async def search(self, keyword: str) -> dict:
        """Search by code or name.
        Returns: {"success": True, "data": [{...}, ...]}
        """
