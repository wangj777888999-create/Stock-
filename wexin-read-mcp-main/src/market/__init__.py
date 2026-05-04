"""Market provider registry — register, list, and dispatch to market modules."""

from __future__ import annotations

from .base import MarketProvider

_providers: dict[str, MarketProvider] = {}


def register(provider: MarketProvider) -> None:
    _providers[provider.name] = provider


def get_provider(name: str) -> MarketProvider | None:
    return _providers.get(name)


def list_providers() -> list[dict]:
    return [{"name": p.name, "label": p.label} for p in _providers.values()]


# Import and register all providers at module load
from .a_share import AShareProvider
from .fund import FundProvider
from .us_stock import USStockProvider
from .hk_stock import HKStockProvider
from .global_index import GlobalIndexProvider
from .crypto import CryptoProvider

register(AShareProvider())
register(FundProvider())
register(USStockProvider())
register(HKStockProvider())
register(GlobalIndexProvider())
register(CryptoProvider())
