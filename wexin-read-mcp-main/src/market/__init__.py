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


# Register fund, crypto, and futures providers
from .fund import FundProvider
from .crypto import CryptoProvider
from .futures import FuturesProvider

register(FundProvider())
register(CryptoProvider())
register(FuturesProvider())
