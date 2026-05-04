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
