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
    """Convert NaN/NaT to None, numpy types to Python native."""
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

    async def get_etf_detail(self, code: str):
        """获取单只 ETF 详情：基本信息 + K 线 + 前十大持仓。"""
        import requests as _requests
        ck = f"market:fund:detail:{code}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        _NO_PROXY = {"http": None, "https": None}

        def _patch(func, **kw):
            orig_get = _requests.get
            orig_post = _requests.post
            _requests.get = lambda url, **k: (k.setdefault("proxies", _NO_PROXY), orig_get(url, **k))[1]
            _requests.post = lambda url, **k: (k.setdefault("proxies", _NO_PROXY), orig_post(url, **k))[1]
            try:
                return func(**kw)
            finally:
                _requests.get = orig_get
                _requests.post = orig_post

        try:
            # 1. 基本信息（从 spot DataFrame 中获取）
            info = {}
            df = await _get_etf_df()
            row = df[df["代码"] == code]
            if not row.empty:
                row = row.iloc[0]
                info = {
                    "代码": code,
                    "名称": _clean(row.get("名称")),
                    "最新价": _clean(row.get("最新价")),
                    "涨跌幅": _clean(row.get("涨跌幅")),
                    "成交额": _clean(row.get("成交额")),
                    "换手率": _clean(row.get("换手率")),
                }

            # 2. K 线数据
            kline_df = await asyncio.to_thread(
                _patch, ak.fund_etf_hist_em,
                symbol=code, period="daily",
                start_date="20240101", end_date="20300101", adjust="qfq",
            )
            kline = []
            if kline_df is not None and not kline_df.empty:
                kline_df = kline_df.tail(120)
                for _, r in kline_df.iterrows():
                    kline.append({
                        "date": str(r.get("日期", ""))[:10],
                        "open": _clean(r.get("开盘")),
                        "close": _clean(r.get("收盘")),
                        "high": _clean(r.get("最高")),
                        "low": _clean(r.get("最低")),
                        "volume": _clean(r.get("成交量")),
                    })

            # 3. 前十大持仓
            holdings = []
            try:
                hold_df = await asyncio.to_thread(
                    _patch, ak.fund_portfolio_hold_em,
                    symbol=code, date="",
                )
                if hold_df is not None and not hold_df.empty:
                    for _, r in hold_df.head(10).iterrows():
                        holdings.append({
                            "code": _clean(r.iloc[1]) if len(r) > 1 else None,
                            "name": _clean(r.iloc[2]) if len(r) > 2 else None,
                            "ratio": _clean(r.iloc[3]) if len(r) > 3 else None,
                            "shares": _clean(r.iloc[4]) if len(r) > 4 else None,
                            "value": _clean(r.iloc[5]) if len(r) > 5 else None,
                        })
            except Exception:
                pass  # 持仓数据可能不可用

            resp = {"success": True, "data": {"info": info, "kline": kline, "holdings": holdings}}
            cache.set(ck, resp, _DF_TTL)
            return resp
        except Exception as e:
            logger.error(f"FundProvider.get_etf_detail error: {e}")
            return {"success": False, "error": str(e)}
