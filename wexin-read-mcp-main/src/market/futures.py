"""Futures market provider — 期货行情、K线、持仓龙虎榜。"""

import asyncio
import logging
import sys
from pathlib import Path

_src = str(Path(__file__).parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

import akshare as ak
import requests as _requests
from stock_utils import TTL_REALTIME, TTL_DAILY, TTL_COMPANY, cache
from .base import MarketProvider

logger = logging.getLogger(__name__)

_NO_PROXY = {"http": None, "https": None}


def _patch(func, **kw):
    """绕过系统代理调用 AKShare。"""
    orig_get = _requests.get
    orig_post = _requests.post
    _requests.get = lambda url, **k: (k.setdefault("proxies", _NO_PROXY), orig_get(url, **k))[1]
    _requests.post = lambda url, **k: (k.setdefault("proxies", _NO_PROXY), orig_post(url, **k))[1]
    try:
        return func(**kw)
    finally:
        _requests.get = orig_get
        _requests.post = orig_post


def _clean(v):
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
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    return v


_CATEGORIES = {
    "金属": ["铜", "铝", "锌", "铅", "镍", "锡", "不锈钢", "螺纹", "线材", "热卷", "铁矿"],
    "农产品": ["豆一", "豆二", "豆粕", "豆油", "玉米", "淀粉", "棕榈油", "菜油", "菜粕", "棉花", "白糖", "鸡蛋", "苹果", "花生", "红枣", "生猪", "粳米"],
    "能源化工": ["原油", "燃油", "沥青", "橡胶", "塑料", "PVC", "聚丙烯", "聚乙烯", "甲醇", "乙二醇", "苯乙烯", "尿素", "纯碱", "玻璃", "PTA", "短纤", "LPG"],
    "金融期货": ["沪深300", "上证50", "中证500", "中证1000", "国债", "10年期", "5年期", "2年期", "30年期"],
    "贵金属": ["黄金", "白银"],
}

_DF_TTL = 300  # 原始 DataFrame 缓存 5 分钟


async def _get_futures_df():
    """获取期货主力合约 DataFrame（带共享缓存）。"""
    ck = "market:futures:df"
    cached = cache.get(ck)
    if cached is not None:
        return cached
    df = await asyncio.to_thread(_patch, ak.futures_display_main_sina)
    cache.set(ck, df, _DF_TTL)
    return df


class FuturesProvider(MarketProvider):
    name = "futures"
    label = "期货"

    async def get_boards(self):
        """按品种类别分组。"""
        ck = "market:futures:boards"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            df = await _get_futures_df()
            if df is None or df.empty:
                return {"success": False, "error": "获取期货列表失败"}

            boards = []
            matched = set()
            for cat_name, keywords in _CATEGORIES.items():
                mask = df["name"].apply(lambda x: any(kw in str(x) for kw in keywords))
                subset = df[mask]
                if len(subset) > 0:
                    boards.append({"name": cat_name, "code": cat_name, "count": len(subset)})
                    matched.update(subset["symbol"].tolist())
            other = df[~df["symbol"].isin(matched)]
            if len(other) > 0:
                boards.append({"name": "其他", "code": "其他", "count": len(other)})

            resp = {"success": True, "data": boards}
            cache.set(ck, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"FuturesProvider.get_boards error: {e}")
            return {"success": False, "error": str(e)}

    async def get_board_stocks(self, board_name: str):
        """返回某类别下的主力合约列表。"""
        ck = f"market:futures:board:{board_name}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            df = await _get_futures_df()
            if df is None or df.empty:
                return {"success": False, "error": "获取期货列表失败"}

            if board_name in _CATEGORIES:
                keywords = _CATEGORIES[board_name]
                mask = df["name"].apply(lambda x: any(kw in str(x) for kw in keywords))
                df = df[mask]
            elif board_name == "其他":
                all_kw = [kw for kws in _CATEGORIES.values() for kw in kws]
                df = df[df["name"].apply(lambda x: not any(kw in str(x) for kw in all_kw))]

            data = []
            for _, row in df.iterrows():
                data.append({
                    "代码": _clean(row.get("symbol")),
                    "名称": _clean(row.get("name")),
                    "交易所": _clean(row.get("exchange")),
                })
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"FuturesProvider.get_board_stocks error: {e}")
            return {"success": False, "error": str(e)}

    async def get_spot(self):
        """全市场主力合约行情（新浪源只提供代码和名称，不含实时价格）。"""
        ck = "market:futures:spot"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            df = await _get_futures_df()
            if df is None or df.empty:
                return {"success": False, "error": "获取期货列表失败"}

            data = []
            for _, row in df.iterrows():
                data.append({
                    "代码": _clean(row.get("symbol")),
                    "名称": _clean(row.get("name")),
                    "交易所": _clean(row.get("exchange")),
                })
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"FuturesProvider.get_spot error: {e}")
            return {"success": False, "error": str(e)}

    async def search(self, keyword: str):
        """按品种名/代码搜索。"""
        ck = f"market:futures:search:{keyword}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            df = await _get_futures_df()
            if df is None or df.empty:
                return {"success": False, "error": "获取期货列表失败"}
            if keyword:
                mask = (
                    df["name"].str.contains(keyword, case=False, na=False)
                    | df["symbol"].str.contains(keyword, case=False, na=False)
                )
                df = df[mask]
            data = []
            for _, row in df.head(50).iterrows():
                data.append({
                    "代码": _clean(row.get("symbol")),
                    "名称": _clean(row.get("name")),
                    "交易所": _clean(row.get("exchange")),
                })
            resp = {"success": True, "data": data, "total": len(data)}
            cache.set(ck, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"FuturesProvider.search error: {e}")
            return {"success": False, "error": str(e)}

    async def get_kline(self, symbol: str, period: str = "day", count: int = 120):
        """获取日 K 线数据。新浪源仅支持日线。"""
        ck = f"market:futures:kline:{symbol}:{count}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            from datetime import datetime, timedelta
            # 只拉取约 count*2 个交易日的数据（约1.5年），而非6年
            start = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
            end = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")
            df = await asyncio.to_thread(
                _patch, ak.futures_main_sina,
                symbol=symbol, start_date=start, end_date=end,
            )
            if df is None or df.empty:
                return {"success": False, "error": "暂无K线数据"}
            df = df.tail(count)
            records = []
            for _, row in df.iterrows():
                records.append({
                    "date": str(row.iloc[0])[:10],
                    "open": _clean(row.iloc[1]),
                    "high": _clean(row.iloc[2]),
                    "low": _clean(row.iloc[3]),
                    "close": _clean(row.iloc[4]),
                    "volume": _clean(row.iloc[5]),
                    "oi": _clean(row.iloc[6]) if len(row) > 6 else None,
                })
            resp = {"success": True, "data": records}
            cache.set(ck, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"FuturesProvider.get_kline error: {e}")
            return {"success": False, "error": str(e)}

    async def get_rank(self, symbol: str):
        """获取持仓龙虎榜汇总（前5/10/15/20名）。"""
        # symbol 如 RB0 → 品种代码 RB
        variety = symbol.rstrip("0123456789")
        ck = f"market:futures:rank:{variety}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            from datetime import datetime, timedelta
            df = None
            date_str = None
            # 尝试近10天（应对节假日）
            for delta in range(0, 10):
                d = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
                try:
                    tmp = await asyncio.to_thread(
                        _patch, ak.get_rank_sum,
                        date=d, vars_list=[variety],
                    )
                    if tmp is not None and not tmp.empty:
                        df = tmp
                        date_str = d
                        break
                except Exception:
                    continue
            if df is None or df.empty:
                return {"success": False, "error": "暂无持仓数据"}

            records = []
            for _, row in df.iterrows():
                records.append({
                    "合约": _clean(row.get("symbol")),
                    "品种": _clean(row.get("variety")),
                    "成交量前5": _clean(row.get("vol_top5")),
                    "成交量增减": _clean(row.get("vol_chg_top5")),
                    "持买量前5": _clean(row.get("long_open_interest_top5")),
                    "持买增减": _clean(row.get("long_open_interest_chg_top5")),
                    "持卖量前5": _clean(row.get("short_open_interest_top5")),
                    "持卖增减": _clean(row.get("short_open_interest_chg_top5")),
                    "成交量前10": _clean(row.get("vol_top10")),
                    "持买量前10": _clean(row.get("long_open_interest_top10")),
                    "持卖量前10": _clean(row.get("short_open_interest_top10")),
                })
            resp = {"success": True, "data": records, "date": date_str}
            cache.set(ck, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"FuturesProvider.get_rank error: {e}")
            return {"success": False, "error": str(e)}
