# Multi-Market Sectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fund/ETF, US stock, HK stock, global index, and crypto market data to the existing sector scanner, with independent modules and search capability.

**Architecture:** Modular provider pattern — each market is a self-contained module implementing a `MarketProvider` base class with `get_boards`, `get_board_stocks`, `get_spot`, and `search` methods. A registry dispatches API routes to the correct provider. Frontend adds a market switcher bar inside the existing sector scan tab.

**Tech Stack:** Python (FastAPI, AKShare, httpx, CoinGecko API), JavaScript (vanilla, existing inline-style pattern)

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `src/market/__init__.py` | Registry: register providers, list them, dispatch by name |
| `src/market/base.py` | `MarketProvider` ABC with 4 abstract methods |
| `src/market/a_share.py` | Wraps existing `IWencaiService` (zero changes to original) |
| `src/market/fund.py` | AKShare `fund_etf_spot_em` — ETF boards + constituents |
| `src/market/us_stock.py` | East Money industry API — US sector boards + constituents |
| `src/market/hk_stock.py` | East Money industry API — HK sector boards + constituents |
| `src/market/global_index.py` | AKShare + hardcoded major index list |
| `src/market/crypto.py` | CoinGecko free API — top coins by market cap |

### Modified files

| File | Change |
|------|--------|
| `src/app.py:~260` | Add 5 market routes after existing iwencai routes |
| `src/templates/index.html:~1603` | Replace sector panel with market switcher bar + per-market content |
| `src/templates/index.html:~3016` | Add JS functions for market switching, loading, searching |

### Untouched files

`iwencai_service.py`, `blogger.py`, `scraper.py`, `config.py`, `stock_service.py`, `stock_utils.py` — zero changes.

---

## Task 1: Base class and registry

**Files:**
- Create: `src/market/base.py`
- Create: `src/market/__init__.py`

- [ ] **Step 1: Create `src/market/base.py`**

```python
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
```

- [ ] **Step 2: Create `src/market/__init__.py`**

```python
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
```

- [ ] **Step 3: Create placeholder provider files**

Create `src/market/a_share.py`, `src/market/fund.py`, `src/market/us_stock.py`, `src/market/hk_stock.py`, `src/market/global_index.py`, `src/market/crypto.py` — each with just a class that imports from base and raises `NotImplementedError`:

```python
# template for each file
from .base import MarketProvider

class XxxProvider(MarketProvider):
    name = "xxx"
    label = "Xxx"

    async def get_boards(self):
        return {"success": False, "error": "not implemented"}

    async def get_board_stocks(self, board_name):
        return {"success": False, "error": "not implemented"}

    async def get_spot(self):
        return {"success": False, "error": "not implemented"}

    async def search(self, keyword):
        return {"success": False, "error": "not implemented"}
```

- [ ] **Step 4: Verify import works**

Run: `cd src && python -c "from market import list_providers; print(list_providers())"`
Expected: list of 6 providers with name/label

- [ ] **Step 5: Commit**

```bash
git add src/market/
git commit -feat "add market provider base class and registry"
```

---

## Task 2: A股 provider (wrap existing IWencaiService)

**Files:**
- Modify: `src/market/a_share.py`

- [ ] **Step 1: Implement AShareProvider**

```python
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
```

- [ ] **Step 2: Test**

Run: `cd src && python -c "
import asyncio
from market.a_share import AShareProvider
p = AShareProvider()
r = asyncio.run(p.get_boards())
print('success:', r.get('success'), 'count:', len(r.get('data', [])))
"`
Expected: `success: True count: <number>`

- [ ] **Step 3: Commit**

```bash
git add src/market/a_share.py
git commit -feat "wrap existing IWencaiService as A-share market provider"
```

---

## Task 3: 基金/ETF provider

**Files:**
- Modify: `src/market/fund.py`

- [ ] **Step 1: Implement FundProvider**

```python
"""Fund/ETF market provider — uses AKShare fund_etf_spot_em."""

import asyncio
import logging
import sys
from pathlib import Path

_src = str(Path(__file__).parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

import akshare as ak
from stock_utils import TTL_COMPANY, TTL_REALTIME, cache
from .base import MarketProvider

logger = logging.getLogger(__name__)


def _clean(v):
    """Convert NaN/NaT to None."""
    import math, pandas as pd
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _df_to_dicts(df, columns=None):
    """Convert DataFrame rows to list of dicts, cleaning NaN values."""
    if columns:
        df = df[columns]
    return [{k: _clean(v) for k, v in row.items()} for _, row in df.iterrows()]


class FundProvider(MarketProvider):
    name = "fund"
    label = "基金"

    async def get_boards(self):
        """Group ETFs by type keywords in name."""
        ck = "market:fund:boards"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(ak.fund_etf_spot_em)
            # Classify by name keywords
            categories = {
                "科技ETF": ["科技", "半导体", "芯片", "人工智能", "AI", "计算机", "软件", "通信", "电子"],
                "医药ETF": ["医药", "医疗", "生物", "创新药", "健康"],
                "消费ETF": ["消费", "白酒", "食品", "饮料", "家电"],
                "新能源ETF": ["新能源", "光伏", "锂电", "储能", "碳中和", "电池"],
                "金融ETF": ["银行", "证券", "保险", "金融", "地产"],
                "军工ETF": ["军工", "国防", "航天"],
                "资源ETF": ["资源", "有色", "钢铁", "煤炭", "能源", "石油"],
                "宽基ETF": ["沪深300", "中证500", "中证1000", "上证50", "创业板", "科创50", "科创板"],
                "跨境ETF": ["纳斯达克", "标普", "日经", "恒生", "德国", "法国", "港股", "中概"],
                "债券ETF": ["国债", "债券", "信用债", "利率债"],
                "商品ETF": ["黄金", "白银", "原油", "豆粕", "铜"],
            }
            boards = []
            matched_codes = set()
            for cat_name, keywords in categories.items():
                mask = df["名称"].apply(lambda x: any(kw in str(x) for kw in keywords))
                subset = df[mask]
                if len(subset) > 0:
                    boards.append({"name": cat_name, "code": cat_name, "count": len(subset)})
                    matched_codes.update(subset["代码"].tolist())

            # "其他ETF" for unmatched
            other = df[~df["代码"].isin(matched_codes)]
            if len(other) > 0:
                boards.append({"name": "其他ETF", "code": "其他ETF", "count": len(other)})

            resp = {"success": True, "data": boards}
            cache.set(ck, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"FundProvider.get_boards error: {e}")
            return {"success": False, "error": str(e)}

    async def get_board_stocks(self, board_name: str):
        """Return ETFs matching the board category."""
        ck = f"market:fund:board:{board_name}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(ak.fund_etf_spot_em)
            categories = {
                "科技ETF": ["科技", "半导体", "芯片", "人工智能", "AI", "计算机", "软件", "通信", "电子"],
                "医药ETF": ["医药", "医疗", "生物", "创新药", "健康"],
                "消费ETF": ["消费", "白酒", "食品", "饮料", "家电"],
                "新能源ETF": ["新能源", "光伏", "锂电", "储能", "碳中和", "电池"],
                "金融ETF": ["银行", "证券", "保险", "金融", "地产"],
                "军工ETF": ["军工", "国防", "航天"],
                "资源ETF": ["资源", "有色", "钢铁", "煤炭", "能源", "石油"],
                "宽基ETF": ["沪深300", "中证500", "中证1000", "上证50", "创业板", "科创50", "科创板"],
                "跨境ETF": ["纳斯达克", "标普", "日经", "恒生", "德国", "法国", "港股", "中概"],
                "债券ETF": ["国债", "债券", "信用债", "利率债"],
                "商品ETF": ["黄金", "白银", "原油", "豆粕", "铜"],
            }

            if board_name == "其他ETF":
                all_keywords = [kw for kws in categories.values() for kw in kws]
                mask = df["名称"].apply(lambda x: not any(kw in str(x) for kw in all_keywords))
            elif board_name in categories:
                keywords = categories[board_name]
                mask = df["名称"].apply(lambda x: any(kw in str(x) for kw in keywords))
            else:
                mask = df["名称"].str.contains(board_name, na=False)

            subset = df[mask]
            cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "换手率"]
            available_cols = [c for c in cols if c in subset.columns]
            data = _df_to_dicts(subset, available_cols)
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"FundProvider.get_board_stocks error: {e}")
            return {"success": False, "error": str(e)}

    async def get_spot(self):
        """Return all ETFs as spot data."""
        return await self.search("")

    async def search(self, keyword: str):
        """Search ETFs by code or name."""
        ck = f"market:fund:search:{keyword}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(ak.fund_etf_spot_em)
            if keyword:
                mask = df["名称"].str.contains(keyword, na=False) | df["代码"].str.contains(keyword, na=False)
                df = df[mask]
            cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "换手率"]
            available_cols = [c for c in cols if c in df.columns]
            data = _df_to_dicts(df.head(100), available_cols)
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"FundProvider.search error: {e}")
            return {"success": False, "error": str(e)}
```

- [ ] **Step 2: Test**

Run: `cd src && python -c "
import asyncio
from market.fund import FundProvider
p = FundProvider()
r = asyncio.run(p.get_boards())
print('boards:', r.get('success'), len(r.get('data', [])))
if r.get('data'):
    b = r['data'][0]['name']
    r2 = asyncio.run(p.get_board_stocks(b))
    print('stocks:', r2.get('success'), r2.get('total'))
r3 = asyncio.run(p.search('沪深300'))
print('search:', r3.get('success'), r3.get('total'))
"`
Expected: all three succeed with data

- [ ] **Step 3: Commit**

```bash
git add src/market/fund.py
git commit -feat "add fund/ETF market provider with category boards and search"
```

---

## Task 4: 美股 provider

**Files:**
- Modify: `src/market/us_stock.py`

- [ ] **Step 1: Implement USStockProvider**

Uses East Money (东方财富) industry classification API via httpx.

```python
"""US stock market provider — East Money industry boards."""

import asyncio
import logging
import httpx
from stock_utils import TTL_COMPANY, TTL_REALTIME, cache
from .base import MarketProvider

logger = logging.getLogger(__name__)

# East Money US industry boards API
_US_BOARDS_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_US_BOARD_STOCKS_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# Known US industry sectors (fallback if API changes)
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

# Major US stocks by sector (curated list as fallback)
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
```

Note: This uses a curated list of major US stocks. A future enhancement can call East Money's real-time API for live price data per stock. The curated list ensures reliability and fast response.

- [ ] **Step 2: Test**

Run: `cd src && python -c "
import asyncio
from market.us_stock import USStockProvider
p = USStockProvider()
r = asyncio.run(p.get_boards())
print('boards:', r.get('success'), len(r.get('data', [])))
r2 = asyncio.run(p.get_board_stocks('科技'))
print('tech stocks:', r2.get('success'), r2.get('total'))
r3 = asyncio.run(p.search('AAPL'))
print('search:', r3.get('success'), r3.get('total'))
"`

- [ ] **Step 3: Commit**

```bash
git add src/market/us_stock.py
git commit -feat "add US stock market provider with sector boards and search"
```

---

## Task 5: 港股 provider

**Files:**
- Modify: `src/market/hk_stock.py`

- [ ] **Step 1: Implement HKStockProvider**

```python
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
```

- [ ] **Step 2: Test**

Run: `cd src && python -c "
import asyncio
from market.hk_stock import HKStockProvider
p = HKStockProvider()
r = asyncio.run(p.get_boards())
print('boards:', r.get('success'), len(r.get('data', [])))
r2 = asyncio.run(p.get_board_stocks('科技'))
print('tech:', r2.get('success'), r2.get('total'))
r3 = asyncio.run(p.search('腾讯'))
print('search:', r3.get('success'), r3.get('total'))
"`

- [ ] **Step 3: Commit**

```bash
git add src/market/hk_stock.py
git commit -feat "add HK stock market provider with sector boards and search"
```

---

## Task 6: 全球指数 provider

**Files:**
- Modify: `src/market/global_index.py`

- [ ] **Step 1: Implement GlobalIndexProvider**

```python
"""Global index provider — major world indices with real-time quotes from AKShare."""

import asyncio
import logging
from stock_utils import TTL_REALTIME, cache
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
```

Note: This returns index definitions. Real-time price data can be added later via AKShare `stock_zh_index_spot_em` for Chinese indices or other sources for international indices. The code structure allows easy extension.

- [ ] **Step 2: Test**

Run: `cd src && python -c "
import asyncio
from market.global_index import GlobalIndexProvider
p = GlobalIndexProvider()
r = asyncio.run(p.get_boards())
print('regions:', r.get('success'), len(r.get('data', [])))
r2 = asyncio.run(p.get_board_stocks('美国'))
print('us indices:', r2.get('success'), r2.get('total'))
r3 = asyncio.run(p.search('日经'))
print('search:', r3.get('success'), r3.get('total'))
"`

- [ ] **Step 3: Commit**

```bash
git add src/market/global_index.py
git commit -feat "add global index provider with major world indices"
```

---

## Task 7: 加密货币 provider

**Files:**
- Modify: `src/market/crypto.py`

- [ ] **Step 1: Implement CryptoProvider**

Uses CoinGecko free API (no key required).

```python
"""Crypto market provider — CoinGecko free API."""

import asyncio
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
            data = [{"code": c["symbol"].upper(), "name": c["name"], "rank": c.get("market_cap_rank")} for c in coins]
            resp_data = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp_data, TTL_REALTIME)
            return resp_data
        except Exception as e:
            logger.error(f"CryptoProvider.search error: {e}")
            return {"success": False, "error": str(e)}
```

- [ ] **Step 2: Test**

Run: `cd src && python -c "
import asyncio
from market.crypto import CryptoProvider
p = CryptoProvider()
r = asyncio.run(p.get_spot())
print('spot:', r.get('success'), len(r.get('data', [])))
if r.get('data'):
    print('first:', r['data'][0])
r2 = asyncio.run(p.search('btc'))
print('search:', r2.get('success'), r2.get('total'))
"`
Expected: spot returns 50 coins, search finds BTC

- [ ] **Step 3: Commit**

```bash
git add src/market/crypto.py
git commit -feat "add crypto market provider using CoinGecko free API"
```

---

## Task 8: API routes in app.py

**Files:**
- Modify: `src/app.py` (add ~40 lines after existing iwencai routes, around line 260)

- [ ] **Step 1: Add import**

Add after existing imports (around line 22):

```python
from market import get_provider, list_providers
```

- [ ] **Step 2: Add routes**

Insert after the last iwencai route (around line 260):

```python
# ---------- 多市场板块路由 ----------

@app.get("/api/market/markets")
async def api_market_list():
    """返回可用市场列表"""
    return {"success": True, "data": list_providers()}


@app.get("/api/market/{market}/boards")
async def api_market_boards(market: str):
    """板块列表"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_boards()


@app.get("/api/market/{market}/board/{name}")
async def api_market_board_stocks(market: str, name: str):
    """板块成分股"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_board_stocks(name)


@app.get("/api/market/{market}/spot")
async def api_market_spot(market: str):
    """实时行情"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_spot()


@app.get("/api/market/{market}/search")
async def api_market_search(market: str, q: str = ""):
    """搜索"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.search(q)
```

- [ ] **Step 3: Test routes**

Run server: `cd src && python -m uvicorn app:app --host 0.0.0.0 --port 8000`

Then test in another terminal:
```bash
curl http://localhost:8000/api/market/markets
curl http://localhost:8000/api/market/fund/boards
curl http://localhost:8000/api/market/crypto/spot
curl http://localhost:8000/api/market/us_stock/search?q=AAPL
```

Expected: all return `{"success": true, "data": [...]}`

- [ ] **Step 4: Commit**

```bash
git add src/app.py
git commit -feat "add multi-market API routes"
```

---

## Task 9: Frontend — market switcher bar

**Files:**
- Modify: `src/templates/index.html` (replace sector panel HTML, ~line 1603-1636)
- Modify: `src/templates/index.html` (add JS functions, ~line 3016-3075)

- [ ] **Step 1: Replace sector panel HTML**

Replace the entire `<!-- Tab 2: 板块扫描 -->` block (lines 1603-1636) with:

```html
<!-- Tab 2: 板块扫描 -->
<div id="wencai-panel-sectors" class="wencai-panel">
  <!-- Market switcher bar -->
  <div id="marketSwitcherBar" style="display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap;"></div>

  <!-- Search bar -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-body" style="padding:12px;">
      <div style="display:flex;gap:8px;">
        <input id="marketSearchInput" class="input" placeholder="输入代码或名称搜索" style="flex:1;"
               onkeydown="if(event.key==='Enter')marketSearch()">
        <button class="btn btn-primary" onclick="marketSearch()">搜索</button>
      </div>
    </div>
  </div>

  <!-- Board grid / spot list -->
  <div class="card" id="marketContentCard">
    <div class="card-header">
      <div class="card-title" id="marketContentTitle">板块</div>
      <button class="btn btn-ghost btn-sm" onclick="loadMarketBoards()">刷新</button>
    </div>
    <div class="card-body" id="marketContentBody">
      <div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>
    </div>
  </div>

  <!-- Board stocks result -->
  <div class="card" id="marketResultCard" style="display:none;margin-top:16px;">
    <div class="card-header">
      <div class="card-title" id="marketResultTitle">成分股</div>
    </div>
    <div class="card-body" style="overflow-x:auto;padding:0;">
      <div id="marketResultBody" style="max-height:500px;overflow-y:auto;"></div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add market switcher JS**

Insert before `</script>` (near end of file, after line 3075):

```javascript
// --- Multi-Market Sector Scanner ---

let _currentMarket = 'a_share';

const MARKET_CONFIG = [
  { name: 'a_share', label: 'A股' },
  { name: 'fund', label: '基金' },
  { name: 'us_stock', label: '美股' },
  { name: 'hk_stock', label: '港股' },
  { name: 'global_index', label: '全球指数' },
  { name: 'crypto', label: '币圈' },
];

function initMarketSwitcher() {
  const bar = document.getElementById('marketSwitcherBar');
  if (!bar) return;
  let html = '';
  MARKET_CONFIG.forEach(m => {
    const active = m.name === _currentMarket ? 'active' : '';
    html += '<button class="btn btn-ghost btn-sm market-tab-btn ' + active + '" data-market="' + m.name + '"'
      + ' onclick="switchMarket(\'' + m.name + '\')">'
      + m.label + '</button>';
  });
  bar.innerHTML = html;
}

function switchMarket(market) {
  _currentMarket = market;
  // Update button states
  document.querySelectorAll('.market-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.market === market);
  });
  // Hide result card
  const resultCard = document.getElementById('marketResultCard');
  if (resultCard) resultCard.style.display = 'none';
  // Update search placeholder
  const input = document.getElementById('marketSearchInput');
  if (input) {
    const config = MARKET_CONFIG.find(m => m.name === market);
    input.placeholder = '搜索' + (config ? config.label : '') + '（代码或名称）';
  }
  loadMarketBoards();
}

async function loadMarketBoards() {
  const body = document.getElementById('marketContentBody');
  const title = document.getElementById('marketContentTitle');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';

  try {
    const resp = await fetch('/api/market/' + _currentMarket + '/boards');
    const json = await resp.json();
    if (!json.success) {
      body.innerHTML = '<div class="empty-state">' + (json.error || '加载失败') + '</div>';
      return;
    }
    if (!json.data?.length) {
      // No boards — try spot
      loadMarketSpot();
      return;
    }
    if (title) title.textContent = MARKET_CONFIG.find(m => m.name === _currentMarket)?.label || '板块';
    _renderMarketGrid(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
  }
}

async function loadMarketSpot() {
  const body = document.getElementById('marketContentBody');
  const title = document.getElementById('marketContentTitle');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';

  try {
    const resp = await fetch('/api/market/' + _currentMarket + '/spot');
    const json = await resp.json();
    if (!json.success || !json.data?.length) {
      body.innerHTML = '<div class="empty-state">暂无数据</div>';
      return;
    }
    if (title) title.textContent = MARKET_CONFIG.find(m => m.name === _currentMarket)?.label || '行情';
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
  }
}

function _renderMarketGrid(container, items) {
  let html = '<div style="display:flex;flex-wrap:wrap;gap:8px;padding:4px;">';
  items.forEach(item => {
    const name = item.name || item;
    const label = name + (item.count ? ' (' + item.count + ')' : '');
    html += '<div style="background:#F8F8FA;border:1px solid var(--border);color:var(--ink);cursor:pointer;padding:10px 14px;border-radius:var(--r-xs);font-size:13px;font-weight:500;transition:all var(--t) var(--ease);"'
      + ' onclick="loadMarketBoardStocks(\'' + String(name).replace(/'/g, "\\'") + '\')"'
      + ' onmouseenter="this.style.borderColor=\'var(--blue)\';this.style.background=\'var(--blue-s)\'"'
      + ' onmouseleave="this.style.borderColor=\'var(--border)\';this.style.background=\'#F8F8FA\'"'
      + ' title="点击查看 ' + name + '">'
      + label
      + '</div>';
  });
  html += '</div>';
  container.innerHTML = html;
}

async function loadMarketBoardStocks(name) {
  const card = document.getElementById('marketResultCard');
  const body = document.getElementById('marketResultBody');
  const title = document.getElementById('marketResultTitle');
  if (!card || !body || !title) return;
  card.style.display = '';
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  title.textContent = '「' + name + '」';

  try {
    const resp = await fetch('/api/market/' + _currentMarket + '/board/' + encodeURIComponent(name));
    const json = await resp.json();
    if (!json.success) { body.innerHTML = '<div class="empty-state">' + (json.error || '查询失败') + '</div>'; return; }
    if (!json.data?.length) { body.innerHTML = '<div class="empty-state">未找到数据</div>'; return; }
    title.textContent = '「' + name + '」（共 ' + (json.total || json.data.length) + ' 条）';
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">请求失败: ' + e.message + '</div>';
  }
}

async function marketSearch() {
  const input = document.getElementById('marketSearchInput');
  const keyword = input ? input.value.trim() : '';
  if (!keyword) { showToast('请输入搜索关键词', 'warning'); return; }

  const body = document.getElementById('marketContentBody');
  const title = document.getElementById('marketContentTitle');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 搜索中...</div>';
  if (title) title.textContent = '搜索: ' + keyword;

  try {
    const resp = await fetch('/api/market/' + _currentMarket + '/search?q=' + encodeURIComponent(keyword));
    const json = await resp.json();
    if (!json.success || !json.data?.length) {
      body.innerHTML = '<div class="empty-state">未找到结果</div>';
      return;
    }
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">搜索失败: ' + e.message + '</div>';
  }
}
```

- [ ] **Step 3: Initialize on tab switch**

Modify the existing `switchWencaiTab` function (line ~2858). Add initialization for the sectors panel:

Find:
```javascript
if (tab === 'sectors' && !_sectorsLoaded) {
  loadSectorHeatmap();
}
```

Replace with:
```javascript
if (tab === 'sectors' && !_sectorsLoaded) {
  initMarketSwitcher();
  loadMarketBoards();
  _sectorsLoaded = true;
}
```

- [ ] **Step 4: Add CSS for active market tab**

Add after the existing `.btn-ghost` rule (around line 371):

```css
.market-tab-btn.active {
  background: var(--blue-s);
  color: var(--blue);
  border-color: var(--blue);
  font-weight: 600;
}
```

- [ ] **Step 5: Test manually**

1. Start server: `cd src && python -m uvicorn app:app --host 0.0.0.0 --port 8000`
2. Open http://localhost:8000
3. Click "板块扫描" tab
4. Verify 6 market tabs appear (A股, 基金, 美股, 港股, 全球指数, 币圈)
5. Click each tab — verify boards/spot data loads
6. Click a board — verify constituent data loads
7. Type in search box — verify search works

- [ ] **Step 6: Commit**

```bash
git add src/templates/index.html
git commit -feat "add market switcher UI with search to sector scan tab"
```

---

## Final verification

- [ ] Start server, verify all 6 markets load data
- [ ] Verify A股 tab still works exactly as before (backward compatibility)
- [ ] Verify search works for each market
- [ ] Verify clicking boards shows constituents
- [ ] Verify error states display gracefully (disconnect network, test crypto/index)
