# 多市场重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构多市场功能：三个独立侧边栏（股票/基金/币圈），股票查询支持 A 股/美股/港股统一搜索，性能优化基金数据加载。

**Architecture:** 在现有 stock_service.py 中扩展多市场搜索和行情（腾讯 API 天然支持三个市场），删除不需要的 market provider 模块，前端改为三个独立侧边栏视图。

**Tech Stack:** Python (FastAPI, AKShare, 腾讯行情 API, CoinGecko API), JavaScript (vanilla)

---

## File Structure

### Modified files

| File | Change |
|------|--------|
| `src/stock_service.py` | 新增美股/港股代码列表 + 多市场搜索 + 多市场行情 |
| `src/stock_utils.py` | 新增 `detect_market()` 辅助函数 |
| `src/market/__init__.py` | 只注册 fund 和 crypto provider |
| `src/market/fund.py` | 重写：共享 DataFrame 缓存 + TTL 300s |
| `src/app.py` | 修改行情路由 + 删除旧 market 路由 + 基金预加载 |
| `src/templates/index.html` | 侧边栏 5 项 + 股票/基金/币圈三个视图 |

### Deleted files

| File | Reason |
|------|--------|
| `src/market/us_stock.py` | 合并到 stock_service.py |
| `src/market/hk_stock.py` | 合并到 stock_service.py |
| `src/market/global_index.py` | 不做日韩，暂不需要 |

### Untouched files

`iwencai_service.py`, `blogger.py`, `scraper.py`, `config.py`, `a_share.py`, `crypto.py`, `base.py`

---

## Task 1: stock_utils.py — 新增市场识别

**Files:**
- Modify: `src/stock_utils.py`

- [ ] **Step 1: 添加 `detect_market()` 函数**

在 `get_market_name()` 函数之后添加：

```python
def detect_market(code: str) -> str:
    """识别股票所属市场: 'us', 'hk', 'a'。"""
    code = str(code).strip()
    # 纯英文字母 + 可选点后缀 → 美股
    cleaned = code.upper().replace(".", "").replace("-", "")
    if cleaned.isalpha():
        return "us"
    # 5 位数字 → 港股
    if code.isdigit() and len(code) == 5:
        return "hk"
    # 6 位数字 → A 股
    if code.isdigit() and len(code) == 6:
        return "a"
    # 混合（如 AAPL.OQ）→ 美股
    if any(c.isalpha() for c in code):
        return "us"
    return "a"
```

- [ ] **Step 2: 测试**

```bash
cd src && python -c "
from stock_utils import detect_market
assert detect_market('600519') == 'a'
assert detect_market('000001') == 'a'
assert detect_market('00700') == 'hk'
assert detect_market('AAPL') == 'us'
assert detect_market('BRK.B') == 'us'
print('All tests passed')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/stock_utils.py
git commit -m "$(cat <<'EOF'
feat: add detect_market() for multi-market code recognition
EOF
)"
```

---

## Task 2: stock_service.py — 多市场搜索和行情

**Files:**
- Modify: `src/stock_service.py`

- [ ] **Step 1: 添加美股和港股代码列表**

在 `StockService` 类之前（约 line 120），添加两个精选股票列表：

```python
# ─── 美股精选列表（代码, 名称） ───
_US_STOCKS = [
    {"code": "AAPL", "name": "苹果 Apple"},
    {"code": "MSFT", "name": "微软 Microsoft"},
    {"code": "GOOGL", "name": "谷歌 Alphabet"},
    {"code": "AMZN", "name": "亚马逊 Amazon"},
    {"code": "NVDA", "name": "英伟达 NVIDIA"},
    {"code": "META", "name": "Meta Platforms"},
    {"code": "TSLA", "name": "特斯拉 Tesla"},
    {"code": "AVGO", "name": "博通 Broadcom"},
    {"code": "ORCL", "name": "甲骨文 Oracle"},
    {"code": "CRM", "name": "赛富时 Salesforce"},
    {"code": "AMD", "name": "AMD"},
    {"code": "ADBE", "name": "Adobe"},
    {"code": "INTC", "name": "英特尔 Intel"},
    {"code": "CSCO", "name": "思科 Cisco"},
    {"code": "IBM", "name": "IBM"},
    {"code": "LLY", "name": "礼来 Eli Lilly"},
    {"code": "UNH", "name": "联合健康 UnitedHealth"},
    {"code": "JNJ", "name": "强生 Johnson & Johnson"},
    {"code": "PFE", "name": "辉瑞 Pfizer"},
    {"code": "ABBV", "name": "艾伯维 AbbVie"},
    {"code": "MRK", "name": "默沙东 Merck"},
    {"code": "ABT", "name": "雅培 Abbott"},
    {"code": "JPM", "name": "摩根大通 JPMorgan Chase"},
    {"code": "V", "name": "Visa"},
    {"code": "MA", "name": "万事达 Mastercard"},
    {"code": "BAC", "name": "美国银行 Bank of America"},
    {"code": "WFC", "name": "富国银行 Wells Fargo"},
    {"code": "GS", "name": "高盛 Goldman Sachs"},
    {"code": "MS", "name": "摩根士丹利 Morgan Stanley"},
    {"code": "WMT", "name": "沃尔玛 Walmart"},
    {"code": "COST", "name": "好市多 Costco"},
    {"code": "HD", "name": "家得宝 Home Depot"},
    {"code": "MCD", "name": "麦当劳 McDonald's"},
    {"code": "NKE", "name": "耐克 Nike"},
    {"code": "SBUX", "name": "星巴克 Starbucks"},
    {"code": "XOM", "name": "埃克森美孚 Exxon Mobil"},
    {"code": "CVX", "name": "雪佛龙 Chevron"},
    {"code": "NFLX", "name": "奈飞 Netflix"},
    {"code": "DIS", "name": "迪士尼 Walt Disney"},
    {"code": "PYPL", "name": "PayPal"},
    {"code": "SQ", "name": "Block (Square)"},
    {"code": "COIN", "name": "Coinbase"},
    {"code": "UBER", "name": "Uber"},
    {"code": "ABNB", "name": "Airbnb"},
    {"code": "SPOT", "name": "Spotify"},
    {"code": "SNAP", "name": "Snap"},
    {"code": "PINS", "name": "Pinterest"},
    {"code": "ZM", "name": "Zoom"},
    {"code": "PLTR", "name": "Palantir"},
    {"code": "SNOW", "name": "Snowflake"},
    {"code": "CRWD", "name": "CrowdStrike"},
    {"code": "PANW", "name": "Palo Alto Networks"},
    {"code": "NOW", "name": "ServiceNow"},
    {"code": "SHOP", "name": "Shopify"},
    {"code": "SE", "name": "Sea Limited"},
    {"code": "BABA", "name": "阿里巴巴 Alibaba"},
    {"code": "JD", "name": "京东 JD.com"},
    {"code": "PDD", "name": "拼多多 PDD Holdings"},
    {"code": "NIO", "name": "蔚来 NIO"},
    {"code": "XPEV", "name": "小鹏汽车 XPeng"},
    {"code": "LI", "name": "理想汽车 Li Auto"},
    {"code": "BRK.B", "name": "伯克希尔 Berkshire Hathaway"},
    {"code": "C", "name": "花旗集团 Citigroup"},
    {"code": "GE", "name": "通用电气 GE Aerospace"},
    {"code": "CAT", "name": "卡特彼勒 Caterpillar"},
    {"code": "BA", "name": "波音 Boeing"},
    {"code": "LMT", "name": "洛克希德·马丁 Lockheed Martin"},
    {"code": "RTX", "name": "RTX Corporation"},
    {"code": "DE", "name": "迪尔 Deere & Company"},
    {"code": "UPS", "name": "UPS"},
    {"code": "FDX", "name": "联邦快递 FedEx"},
    {"code": "T", "name": "AT&T"},
    {"code": "VZ", "name": "Verizon"},
    {"code": "KO", "name": "可口可乐 Coca-Cola"},
    {"code": "PEP", "name": "百事可乐 PepsiCo"},
    {"code": "PG", "name": "宝洁 Procter & Gamble"},
    {"code": "CL", "name": "高露洁 Colgate-Palmolive"},
    {"code": "TMO", "name": "赛默飞 Thermo Fisher"},
    {"code": "DHR", "name": "丹纳赫 Danaher"},
    {"code": "AMGN", "name": "安进 Amgen"},
    {"code": "GILD", "name": "吉利德 Gilead Sciences"},
    {"code": "BMY", "name": "百时美施贵宝 BMS"},
    {"code": "CVS", "name": "CVS Health"},
    {"code": "LOW", "name": "劳氏 Lowe's"},
    {"code": "TGT", "name": "塔吉特 Target"},
    {"code": "COP", "name": "康菲石油 ConocoPhillips"},
    {"code": "SLB", "name": "斯伦贝谢 Schlumberger"},
    {"code": "NEE", "name": "新纪元能源 NextEra Energy"},
    {"code": "SO", "name": "南方公司 Southern Company"},
    {"code": "DUK", "name": "杜克能源 Duke Energy"},
    {"code": "PLD", "name": "普洛斯 Prologis"},
    {"code": "AMT", "name": "美国塔 American Tower"},
    {"code": "CCI", "name": "冠城国际 Crown Castle"},
    {"code": "SPG", "name": "西蒙地产 Simon Property"},
    {"code": "ISRG", "name": "直觉外科 Intuitive Surgical"},
    {"code": "REGN", "name": "再生元 Regeneron"},
    {"code": "VRTX", "name": "顶点制药 Vertex"},
    {"code": "ZTS", "name": "硕腾 Zoetis"},
    {"code": "SYK", "name": "史赛克 Stryker"},
    {"code": "BSX", "name": "波士顿科学 Boston Scientific"},
    {"code": "MDT", "name": "美敦力 Medtronic"},
]

# ─── 港股精选列表（代码, 名称） ───
_HK_STOCKS = [
    {"code": "00700", "name": "腾讯控股"},
    {"code": "09988", "name": "阿里巴巴-SW"},
    {"code": "03690", "name": "美团-W"},
    {"code": "09999", "name": "网易-S"},
    {"code": "09618", "name": "京东集团-SW"},
    {"code": "09888", "name": "百度集团-SW"},
    {"code": "01810", "name": "小米集团-W"},
    {"code": "00268", "name": "金蝶国际"},
    {"code": "00241", "name": "阿里健康"},
    {"code": "06060", "name": "众安在线"},
    {"code": "00005", "name": "汇丰控股"},
    {"code": "01398", "name": "工商银行"},
    {"code": "03988", "name": "中国银行"},
    {"code": "00939", "name": "建设银行"},
    {"code": "02318", "name": "中国平安"},
    {"code": "01299", "name": "友邦保险"},
    {"code": "00388", "name": "香港交易所"},
    {"code": "02628", "name": "中国人寿"},
    {"code": "06030", "name": "中信证券"},
    {"code": "01109", "name": "华润置地"},
    {"code": "00688", "name": "中国海外发展"},
    {"code": "00016", "name": "新鸿基地产"},
    {"code": "00012", "name": "恒基地产"},
    {"code": "00883", "name": "中国海洋石油"},
    {"code": "02313", "name": "申洲国际"},
    {"code": "00291", "name": "华润啤酒"},
    {"code": "01929", "name": "周大福"},
    {"code": "00322", "name": "康师傅控股"},
    {"code": "01099", "name": "国药控股"},
    {"code": "02269", "name": "药明生物"},
    {"code": "01177", "name": "中国生物制药"},
    {"code": "00857", "name": "中国石油股份"},
    {"code": "00386", "name": "中国石油化工"},
    {"code": "01088", "name": "中国神华"},
    {"code": "00941", "name": "中国移动"},
    {"code": "00728", "name": "中国电信"},
    {"code": "00762", "name": "中国联通"},
    {"code": "00002", "name": "中电控股"},
    {"code": "00003", "name": "香港中华煤气"},
    {"code": "00006", "name": "电能实业"},
    {"code": "02388", "name": "中银香港"},
    {"code": "00992", "name": "联想集团"},
    {"code": "02018", "name": "瑞声科技"},
    {"code": "00981", "name": "中芯国际"},
    {"code": "02020", "name": "安踏体育"},
    {"code": "02331", "name": "李宁"},
    {"code": "01211", "name": "比亚迪股份"},
    {"code": "09901", "name": "新东方在线"},
    {"code": "09626", "name": "哔哩哔哩-SW"},
    {"code": "09868", "name": "小鹏汽车-W"},
    {"code": "09866", "name": "蔚来-SW"},
    {"code": "02015", "name": "理想汽车-W"},
    {"code": "06618", "name": "京东健康"},
    {"code": "09698", "name": "万国数据-SW"},
    {"code": "09961", "name": "携程集团-S"},
    {"code": "09988", "name": "阿里巴巴-SW"},
    {"code": "03888", "name": "金山软件"},
    {"code": "01833", "name": "平安好医生"},
    {"code": "06098", "name": "碧桂园服务"},
    {"code": "02202", "name": "万科企业"},
]
```

- [ ] **Step 2: 修改 `search_stock()` 方法**

将 `search_stock()` 替换为多市场版本：

```python
async def search_stock(self, keyword: str) -> dict:
    """搜索股票，支持 A 股/美股/港股。"""
    cache_key = f"search:{keyword}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    results = []

    try:
        # 1. 搜索 A 股
        if StockService._stock_list_cache is not None:
            df = StockService._stock_list_cache
        else:
            df = await asyncio.to_thread(_patch_requests, ak.stock_info_a_code_name)

        mask = (
            df["code"].str.contains(keyword, case=False, na=False)
            | df["name"].str.contains(keyword, case=False, na=False)
        )
        matched = df[mask].head(15)
        for _, row in matched.iterrows():
            results.append({"code": row["code"], "name": row["name"], "market": "a"})

        # 2. 搜索美股
        kw_upper = keyword.upper()
        for s in _US_STOCKS:
            if kw_upper in s["code"].upper() or keyword in s["name"]:
                results.append({"code": s["code"], "name": s["name"], "market": "us"})
                if len(results) >= 25:
                    break

        # 3. 搜索港股
        if len(results) < 25:
            for s in _HK_STOCKS:
                if keyword in s["code"] or keyword in s["name"]:
                    results.append({"code": s["code"], "name": s["name"], "market": "hk"})
                    if len(results) >= 25:
                        break

        resp = {"success": True, "data": results[:25]}
        cache.set(cache_key, resp, TTL_COMPANY)
        return resp
    except Exception as e:
        logger.error(f"搜索股票失败: {e}")
        return {"success": False, "error": f"搜索失败: {e}"}
```

- [ ] **Step 3: 修改 `get_realtime_quote()` 方法**

将 `get_realtime_quote()` 替换为多市场版本：

```python
async def get_realtime_quote(self, symbol: str) -> dict:
    """通过腾讯 API 获取实时行情，支持 A 股/美股/港股。"""
    symbol = normalize_symbol(symbol)
    market = detect_market(symbol)
    cache_key = f"quote:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        if market == "a":
            exchange = get_exchange(symbol)
        elif market == "hk":
            exchange = "hk"
        else:
            exchange = "us"

        url = _QT_URL.format(exchange=exchange, code=symbol)
        r = await asyncio.to_thread(_get, url, timeout=10)
        r.encoding = "gbk"
        record = _parse_tencent_quote(r.text, symbol)
        if record is None:
            return {"success": False, "error": f"未找到股票 {symbol}"}

        # 补充市场标识
        record["市场"] = {"a": "A股", "hk": "港股", "us": "美股"}[market]
        resp = {"success": True, "data": record}
        cache.set(cache_key, resp, TTL_REALTIME)
        return resp
    except Exception as e:
        logger.error(f"获取行情失败 {symbol}: {e}")
        return {"success": False, "error": f"获取行情失败: {e}"}
```

- [ ] **Step 4: 测试**

```bash
cd src && python -c "
import asyncio
from stock_service import StockService

svc = StockService()

# Test multi-market search
r = asyncio.run(svc.search_stock('AAPL'))
print('Search AAPL:', r['success'], len(r['data']), [(d['code'], d['market']) for d in r['data'][:3]])

r = asyncio.run(svc.search_stock('腾讯'))
print('Search 腾讯:', r['success'], len(r['data']), [(d['code'], d['market']) for d in r['data'][:3]])

r = asyncio.run(svc.search_stock('茅台'))
print('Search 茅台:', r['success'], len(r['data']), [(d['code'], d['market']) for d in r['data'][:3]])

# Test multi-market quote
r = asyncio.run(svc.get_realtime_quote('AAPL'))
print('Quote AAPL:', r['success'], r['data'].get('名称'), r['data'].get('最新价'))

r = asyncio.run(svc.get_realtime_quote('00700'))
print('Quote 00700:', r['success'], r['data'].get('名称'), r['data'].get('最新价'))

r = asyncio.run(svc.get_realtime_quote('600519'))
print('Quote 600519:', r['success'], r['data'].get('名称'), r['data'].get('最新价'))
"
```

- [ ] **Step 5: Commit**

```bash
git add src/stock_service.py
git commit -m "$(cat <<'EOF'
feat: add multi-market stock search and quote (A/HK/US)
EOF
)"
```

---

## Task 3: market/__init__.py — 清理注册

**Files:**
- Modify: `src/market/__init__.py`

- [ ] **Step 1: 只注册 fund 和 crypto**

将 `src/market/__init__.py` 替换为：

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


# Only register fund and crypto providers
from .fund import FundProvider
from .crypto import CryptoProvider

register(FundProvider())
register(CryptoProvider())
```

- [ ] **Step 2: 删除不需要的 provider 文件**

```bash
rm src/market/us_stock.py src/market/hk_stock.py src/market/global_index.py src/market/a_share.py
```

- [ ] **Step 3: 验证导入**

```bash
cd src && python -c "from market import list_providers; print(list_providers())"
```

Expected: `[{'name': 'fund', 'label': '基金'}, {'name': 'crypto', 'label': '币圈'}]`

- [ ] **Step 4: Commit**

```bash
git add src/market/
git commit -m "$(cat <<'EOF'
refactor: clean up market registry to fund + crypto only
EOF
)"
```

---

## Task 4: market/fund.py — 性能优化重写

**Files:**
- Modify: `src/market/fund.py`

- [ ] **Step 1: 重写 fund.py**

将整个文件替换为：

```python
"""Fund/ETF market provider — uses AKShare fund_etf_spot_em with shared DataFrame cache."""

import asyncio
import logging
import sys
from pathlib import Path

_src = str(Path(__file__).parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

import akshare as ak
from stock_utils import TTL_COMPANY, cache
from .base import MarketProvider

logger = logging.getLogger(__name__)

_DF_TTL = 300  # DataFrame 缓存 5 分钟


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


_CATEGORIES = {
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


async def _get_etf_df():
    """获取 ETF DataFrame（带缓存）。"""
    ck = "market:fund:df"
    cached = cache.get(ck)
    if cached is not None:
        return cached
    df = await asyncio.to_thread(ak.fund_etf_spot_em)
    cache.set(ck, df, _DF_TTL)
    return df


def _classify_boards(df):
    """将 ETF 按关键词分类为板块列表。"""
    boards = []
    matched_codes = set()
    for cat_name, keywords in _CATEGORIES.items():
        mask = df["名称"].apply(lambda x: any(kw in str(x) for kw in keywords))
        subset = df[mask]
        if len(subset) > 0:
            boards.append({"name": cat_name, "code": cat_name, "count": len(subset)})
            matched_codes.update(subset["代码"].tolist())
    other = df[~df["代码"].isin(matched_codes)]
    if len(other) > 0:
        boards.append({"name": "其他ETF", "code": "其他ETF", "count": len(other)})
    return boards


def _filter_by_board(df, board_name):
    """按板块名过滤 ETF。"""
    if board_name == "其他ETF":
        all_keywords = [kw for kws in _CATEGORIES.values() for kw in kws]
        return df[df["名称"].apply(lambda x: not any(kw in str(x) for kw in all_keywords))]
    elif board_name in _CATEGORIES:
        keywords = _CATEGORIES[board_name]
        return df[df["名称"].apply(lambda x: any(kw in str(x) for kw in keywords))]
    else:
        return df[df["名称"].str.contains(board_name, na=False)]


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
            df = await _get_etf_df()
            boards = _classify_boards(df)
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
            df = await _get_etf_df()
            subset = _filter_by_board(df, board_name)
            cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "换手率"]
            available_cols = [c for c in cols if c in subset.columns]
            data = _df_to_dicts(subset, available_cols)
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, _DF_TTL)
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
            df = await _get_etf_df()
            if keyword:
                mask = df["名称"].str.contains(keyword, na=False) | df["代码"].str.contains(keyword, na=False)
                df = df[mask]
            cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "换手率"]
            available_cols = [c for c in cols if c in df.columns]
            data = _df_to_dicts(df.head(100), available_cols)
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, _DF_TTL)
            return resp
        except Exception as e:
            logger.error(f"FundProvider.search error: {e}")
            return {"success": False, "error": str(e)}
```

- [ ] **Step 2: 测试**

```bash
cd src && python -c "
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
# Second call should be cached (fast)
import time
t = time.time()
r4 = asyncio.run(p.search('沪深300'))
print(f'cached search: {time.time()-t:.3f}s')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/market/fund.py
git commit -m "$(cat <<'EOF'
perf: fund provider with shared DataFrame cache and 5min TTL
EOF
)"
```

---

## Task 5: app.py — 路由和预加载

**Files:**
- Modify: `src/app.py`

- [ ] **Step 1: 删除旧 market 路由**

找到并删除以下代码块（从 `# ---------- 多市场板块路由 ----------` 到最后一个 `api_market_search` 函数结束）：

```python
# ---------- 多市场板块路由 ----------
# ... (delete all 5 market routes)
```

- [ ] **Step 2: 添加基金预加载**

找到 `@app.on_event("startup")` 或 `app = FastAPI(...)` 之后。添加启动事件：

```python
@app.on_event("startup")
async def _startup():
    """启动时预加载数据。"""
    asyncio.create_task(StockService.preload_stock_list())

    async def _preload_fund():
        """后台预加载基金 ETF 数据。"""
        try:
            from market.fund import _get_etf_df
            await _get_etf_df()
            logger.info("基金 ETF 数据预加载完成")
        except Exception as e:
            logger.warning(f"基金数据预加载失败（首次访问时重试）: {e}")

    asyncio.create_task(_preload_fund())
```

如果已有 `@app.on_event("startup")`，在里面追加 `_preload_fund()` 调用即可。

- [ ] **Step 3: 保留 fund/crypto 路由**

保留这些路由（已经存在）：
- `GET /api/market/fund/boards`
- `GET /api/market/fund/board/{name}`
- `GET /api/market/fund/search`
- `GET /api/market/crypto/spot`
- `GET /api/market/crypto/search`

删除：`/api/market/markets`, `/api/market/a_share/*`, `/api/market/us_stock/*`, `/api/market/hk_stock/*`, `/api/market/global_index/*`

只保留 fund 和 crypto 两个 market 的路由。

- [ ] **Step 4: 测试**

```bash
cd src && python -c "
from app import app
market_routes = [r for r in app.routes if hasattr(r, 'path') and 'market' in r.path]
for r in market_routes:
    print(r.path)
"
```

Expected: only fund and crypto routes.

- [ ] **Step 5: Commit**

```bash
git add src/app.py
git commit -m "$(cat <<'EOF'
refactor: clean up market routes, add fund preload
EOF
)"
```

---

## Task 6: index.html — 侧边栏和三个视图

**Files:**
- Modify: `src/templates/index.html`

- [ ] **Step 1: 修改侧边栏导航**

找到侧边栏 `<nav class="sidebar-nav">` 部分（约 line 1047-1071）。

将 `条件选股` 按钮替换为 `基金` 和 `币圈`：

找到：
```html
      <button class="nav-item" data-view="wencai" onclick="switchView('wencai', this)">
        <svg viewBox="0 0 20 20" fill="none"><circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.6"/><path d="M14 14l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
        <span>条件选股</span>
      </button>
```

替换为：
```html
      <button class="nav-item" data-view="fund" onclick="switchView('fund', this)">
        <svg viewBox="0 0 20 20" fill="none"><rect x="3" y="3" width="14" height="14" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M7 7h6M7 10h6M7 13h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        <span>基金</span>
      </button>
      <button class="nav-item" data-view="crypto" onclick="switchView('crypto', this)">
        <svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.6"/><path d="M10 6v8M8 8h4c1.1 0 2 .9 2 2s-.9 2-2 2H8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span>币圈</span>
      </button>
```

- [ ] **Step 2: 添加基金视图 HTML**

找到 `<!-- ==================== VIEW: WENCAI ==================== -->` 部分（原来的问财选股视图）。

将整个 `view-wencai` div 替换为基金视图：

```html
<!-- ==================== VIEW: FUND ==================== -->
<div id="view-fund" class="view">

  <!-- 基金搜索 -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-body" style="padding:12px;">
      <div style="display:flex;gap:8px;">
        <input id="fundSearchInput" class="input" placeholder="搜索基金（代码或名称）" style="flex:1;"
               onkeydown="if(event.key==='Enter')fundSearch()">
        <button class="btn btn-primary" onclick="fundSearch()">搜索</button>
      </div>
    </div>
  </div>

  <!-- ETF 板块网格 -->
  <div class="card" id="fundBoardCard">
    <div class="card-header">
      <div class="card-title">ETF 板块分类</div>
      <button class="btn btn-ghost btn-sm" onclick="loadFundBoards()">刷新</button>
    </div>
    <div class="card-body" id="fundBoardBody">
      <div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>
    </div>
  </div>

  <!-- 板块成分 ETF -->
  <div class="card" id="fundResultCard" style="display:none;margin-top:16px;">
    <div class="card-header">
      <div class="card-title" id="fundResultTitle">成分基金</div>
    </div>
    <div class="card-body" style="overflow-x:auto;padding:0;">
      <div id="fundResultBody" style="max-height:500px;overflow-y:auto;"></div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 添加币圈视图 HTML**

在基金视图之后添加币圈视图：

```html
<!-- ==================== VIEW: CRYPTO ==================== -->
<div id="view-crypto" class="view">

  <!-- 币种搜索 -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-body" style="padding:12px;">
      <div style="display:flex;gap:8px;">
        <input id="cryptoSearchInput" class="input" placeholder="搜索币种（名称或代码，如 BTC）" style="flex:1;"
               onkeydown="if(event.key==='Enter')cryptoSearch()">
        <button class="btn btn-primary" onclick="cryptoSearch()">搜索</button>
      </div>
    </div>
  </div>

  <!-- 币种行情列表 -->
  <div class="card">
    <div class="card-header">
      <div class="card-title">主流币种行情</div>
      <button class="btn btn-ghost btn-sm" onclick="loadCryptoSpot()">刷新</button>
    </div>
    <div class="card-body" style="overflow-x:auto;padding:0;">
      <div id="cryptoSpotBody" style="max-height:600px;overflow-y:auto;">
        <div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: 修改股票查询搜索框 placeholder**

找到股票查询的搜索框，将 placeholder 改为 "搜索 A 股/美股/港股（代码或名称）"

- [ ] **Step 5: 添加基金和币圈 JS 函数**

在 `</script>` 之前添加：

```javascript
// --- Fund View ---
let _fundLoaded = false;

async function loadFundBoards() {
  const body = document.getElementById('fundBoardBody');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  try {
    const resp = await fetch('/api/market/fund/boards');
    const json = await resp.json();
    if (!json.success || !json.data?.length) {
      body.innerHTML = '<div class="empty-state">' + (json.error || '加载失败') + '</div>';
      return;
    }
    let html = '<div style="display:flex;flex-wrap:wrap;gap:8px;padding:4px;">';
    json.data.forEach(item => {
      const label = item.name + ' (' + item.count + ')';
      html += '<div style="background:#F8F8FA;border:1px solid var(--border);color:var(--ink);cursor:pointer;padding:10px 14px;border-radius:var(--r-xs);font-size:13px;font-weight:500;transition:all var(--t) var(--ease);"'
        + ' onclick="loadFundBoardStocks(\'' + item.name.replace(/'/g, "\\'") + '\')"'
        + ' onmouseenter="this.style.borderColor=\'var(--blue)\';this.style.background=\'var(--blue-s)\'"'
        + ' onmouseleave="this.style.borderColor=\'var(--border)\';this.style.background=\'#F8F8FA\'"'
        + ' title="点击查看 ' + item.name + '">'
        + label + '</div>';
    });
    html += '</div>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
  }
}

async function loadFundBoardStocks(name) {
  const card = document.getElementById('fundResultCard');
  const body = document.getElementById('fundResultBody');
  const title = document.getElementById('fundResultTitle');
  if (!card || !body || !title) return;
  card.style.display = '';
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  title.textContent = '「' + name + '」';
  try {
    const resp = await fetch('/api/market/fund/board/' + encodeURIComponent(name));
    const json = await resp.json();
    if (!json.success) { body.innerHTML = '<div class="empty-state">' + (json.error || '查询失败') + '</div>'; return; }
    if (!json.data?.length) { body.innerHTML = '<div class="empty-state">未找到数据</div>'; return; }
    title.textContent = '「' + name + '」（共 ' + (json.total || json.data.length) + ' 条）';
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">请求失败: ' + e.message + '</div>';
  }
}

async function fundSearch() {
  const input = document.getElementById('fundSearchInput');
  const keyword = input ? input.value.trim() : '';
  if (!keyword) { showToast('请输入搜索关键词', 'warning'); return; }
  const card = document.getElementById('fundResultCard');
  const body = document.getElementById('fundResultBody');
  const title = document.getElementById('fundResultTitle');
  if (!card || !body || !title) return;
  card.style.display = '';
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 搜索中...</div>';
  title.textContent = '搜索: ' + keyword;
  try {
    const resp = await fetch('/api/market/fund/search?q=' + encodeURIComponent(keyword));
    const json = await resp.json();
    if (!json.success || !json.data?.length) { body.innerHTML = '<div class="empty-state">未找到结果</div>'; return; }
    title.textContent = '搜索: ' + keyword + '（' + json.total + ' 条）';
    _renderWencaiTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">搜索失败: ' + e.message + '</div>';
  }
}

// --- Crypto View ---

async function loadCryptoSpot() {
  const body = document.getElementById('cryptoSpotBody');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 加载中...</div>';
  try {
    const resp = await fetch('/api/market/crypto/spot');
    const json = await resp.json();
    if (!json.success || !json.data?.length) { body.innerHTML = '<div class="empty-state">加载失败</div>'; return; }
    _renderCryptoTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
  }
}

async function cryptoSearch() {
  const input = document.getElementById('cryptoSearchInput');
  const keyword = input ? input.value.trim() : '';
  if (!keyword) { loadCryptoSpot(); return; }
  const body = document.getElementById('cryptoSpotBody');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--ink-3);"><span class="spinner dark"></span> 搜索中...</div>';
  try {
    const resp = await fetch('/api/market/crypto/search?q=' + encodeURIComponent(keyword));
    const json = await resp.json();
    if (!json.success || !json.data?.length) { body.innerHTML = '<div class="empty-state">未找到结果</div>'; return; }
    _renderCryptoTable(body, json.data);
  } catch (e) {
    body.innerHTML = '<div class="empty-state">搜索失败: ' + e.message + '</div>';
  }
}

function _renderCryptoTable(container, data) {
  let html = '<table style="width:100%;border-collapse:collapse;font-size:13px;">';
  html += '<thead><tr style="border-bottom:2px solid var(--border);text-align:left;">';
  html += '<th style="padding:8px;">#</th><th style="padding:8px;">名称</th><th style="padding:8px;">代码</th>';
  html += '<th style="padding:8px;text-align:right;">价格(USD)</th><th style="padding:8px;text-align:right;">24h涨跌</th>';
  html += '<th style="padding:8px;text-align:right;">市值</th><th style="padding:8px;text-align:right;">24h成交量</th>';
  html += '</tr></thead><tbody>';
  data.forEach(c => {
    const change = c.change_24h || 0;
    const color = change >= 0 ? 'var(--green)' : 'var(--red)';
    const sign = change >= 0 ? '+' : '';
    const mcap = c.market_cap ? (c.market_cap >= 1e12 ? (c.market_cap/1e12).toFixed(2)+'T' : c.market_cap >= 1e9 ? (c.market_cap/1e9).toFixed(2)+'B' : (c.market_cap/1e6).toFixed(2)+'M') : '-';
    const vol = c.volume_24h ? (c.volume_24h >= 1e9 ? (c.volume_24h/1e9).toFixed(2)+'B' : (c.volume_24h/1e6).toFixed(2)+'M') : '-';
    html += '<tr style="border-bottom:1px solid var(--border);cursor:pointer;"'
      + ' onclick="showStockDetail(\'' + c.code + '\')"'
      + ' onmouseenter="this.style.background=\'var(--surface-2)\'"'
      + ' onmouseleave="this.style.background=\'\'">';
    html += '<td style="padding:8px;color:var(--ink-3);">' + (c.rank || '-') + '</td>';
    html += '<td style="padding:8px;font-weight:500;">' + c.name + '</td>';
    html += '<td style="padding:8px;color:var(--ink-3);">' + c.code + '</td>';
    html += '<td style="padding:8px;text-align:right;font-weight:500;">' + (c.price ? c.price.toLocaleString() : '-') + '</td>';
    html += '<td style="padding:8px;text-align:right;color:' + color + ';">' + sign + change.toFixed(2) + '%</td>';
    html += '<td style="padding:8px;text-align:right;color:var(--ink-3);">$' + mcap + '</td>';
    html += '<td style="padding:8px;text-align:right;color:var(--ink-3);">$' + vol + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}
```

- [ ] **Step 6: 修改 switchView 视图标题映射**

找到 `switchView` 函数中的标题映射部分，添加 fund 和 crypto 的标题。

找到类似：
```javascript
const titles = { task: '文章收集', bloggers: '博主管理', stock: '股票查询', wencai: '条件选股', config: '配置设置' };
```

替换为：
```javascript
const titles = { task: '文章收集', bloggers: '博主管理', stock: '股票查询', fund: '基金', crypto: '币圈', config: '配置设置' };
```

同样更新副标题映射（如果有）。

- [ ] **Step 7: 修改 switchView 加载逻辑**

在 `switchView` 函数中，找到 `wencai` 相关的加载逻辑（如果有），替换为：

```javascript
if (view === 'fund' && !_fundLoaded) {
  loadFundBoards();
  _fundLoaded = true;
}
if (view === 'crypto') {
  loadCryptoSpot();
}
```

- [ ] **Step 8: 搜索结果显示市场标签**

找到股票搜索结果显示的 JS 函数，在搜索结果中添加"市场"列。搜索结果渲染函数中，每个结果应该显示市场标识（A股/美股/港股）。

- [ ] **Step 9: 测试**

启动服务器，验证：
1. 侧边栏有 5 项：文章收集、博主管理、股票查询、基金、币圈
2. 点击"基金" → 显示 ETF 板块分类
3. 点击"币圈" → 显示 Top 50 币种行情
4. 股票搜索输入"AAPL" → 显示美股结果
5. 原有文章收集、博主管理功能不受影响

- [ ] **Step 10: Commit**

```bash
git add src/templates/index.html
git commit -m "$(cat <<'EOF'
feat: redesign sidebar with stock/fund/crypto views
EOF
)"
```

---

## Final verification

- [ ] 启动服务器，验证所有功能
- [ ] 股票搜索：输入"茅台"、"AAPL"、"00700"、"腾讯" 都能正确识别并显示
- [ ] 股票行情：点击搜索结果能看到实时行情
- [ ] 基金板块：点击板块能看成分 ETF
- [ ] 币圈：Top 50 币种实时行情
- [ ] 博主管理、文章收集功能不受影响
- [ ] 性能：基金板块第二次加载 < 1 秒（缓存生效）
