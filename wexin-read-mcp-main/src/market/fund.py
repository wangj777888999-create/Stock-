"""Fund/ETF market provider — 新浪为主 + 同花顺备选，双源容灾。"""

import asyncio
import logging
import os
import sys
from pathlib import Path

_src = str(Path(__file__).parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

import pandas as pd
from stock_utils import TTL_COMPANY, cache
from .base import MarketProvider

logger = logging.getLogger(__name__)

_DF_TTL = 300  # DataFrame 缓存 5 分钟
_PROXY_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")


# ---------- 工具函数 ----------

def _clean(v):
    """Convert NaN/NaT to None, Timestamp to str, numpy types to Python native."""
    import math
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return str(v)
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


# ---------- 数据源（清除代理环境变量调用） ----------

def _no_proxy_env():
    """返回被清除的代理环境变量（用于 restore）。"""
    saved = {k: os.environ.pop(k) for k in _PROXY_KEYS if k in os.environ}
    return saved


def _restore_env(saved):
    """恢复之前清除的代理环境变量。"""
    for k, v in saved.items():
        os.environ[k] = v


def _fetch_sina_etf():
    """主数据源：新浪 ETF 列表。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        df = ak.fund_etf_category_sina(symbol="ETF基金")
        # 统一列名
        col_map = {
            "代码": "代码", "名称": "名称",
            "最新价": "最新价", "涨跌幅": "涨跌幅",
            "成交量": "成交量", "成交额": "成交额",
            "涨跌额": "涨跌额", "买入": "买入", "卖出": "卖出",
            "昨收": "昨收", "今开": "今开", "最高": "最高", "最低": "最低",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        logger.info(f"新浪源获取 {len(df)} 只 ETF")
        return df
    finally:
        _restore_env(saved)


def _fetch_ths_etf():
    """备选数据源：同花顺 ETF 列表。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        df = ak.fund_etf_spot_ths()
        # 统一列名
        col_map = {
            "基金代码": "代码", "基金名称": "名称",
            "当前-单位净值": "最新价", "增长率": "涨跌幅",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        logger.info(f"同花顺源获取 {len(df)} 只 ETF")
        return df
    finally:
        _restore_env(saved)


async def _get_etf_df():
    """获取 ETF DataFrame（带缓存 + 双源切换）。"""
    ck = "market:fund:df"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    # 策略 1：新浪
    try:
        df = await asyncio.to_thread(_fetch_sina_etf)
        if df is not None and not df.empty:
            cache.set(ck, df, _DF_TTL)
            return df
    except Exception as e:
        logger.warning(f"新浪 ETF 源失败: {e}")

    # 策略 2：同花顺
    logger.info("切换到同花顺备选数据源")
    try:
        df = await asyncio.to_thread(_fetch_ths_etf)
        if df is not None and not df.empty:
            cache.set(ck, df, _DF_TTL)
            return df
    except Exception as e:
        logger.warning(f"同花顺备选也失败: {e}")

    raise RuntimeError("所有 ETF 数据源均不可用")


# ---------- 板块分类 ----------

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


# ---------- Provider ----------

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
        ck = f"market:fund:detail:{code}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            # 1. 基本信息
            info = {}
            df = await _get_etf_df()
            # 新浪代码带市场前缀(sz/sh)，需要匹配后缀
            row = df[df["代码"].str.endswith(code, na=False)]
            if not row.empty:
                row = row.iloc[0]
                info = {
                    "代码": code,
                    "名称": _clean(row.get("名称")),
                    "最新价": _clean(row.get("最新价")),
                    "涨跌幅": _clean(row.get("涨跌幅")),
                    "成交额": _clean(row.get("成交额")),
                }

            # 2. K 线数据 — 新浪
            import akshare as ak
            kline = []
            try:
                # 从新浪代码获取完整代码
                full_code = None
                if not row.empty:
                    full_code = _clean(row.iloc[0] if hasattr(row, 'iloc') else row.get("代码"))
                if not full_code:
                    # 推测市场前缀
                    full_code = f"sz{code}" if code.startswith(("0", "3", "15")) else f"sh{code}"

                saved = _no_proxy_env()
                try:
                    kline_df = await asyncio.to_thread(
                        ak.fund_etf_hist_sina, symbol=full_code,
                    )
                finally:
                    _restore_env(saved)

                if kline_df is not None and not kline_df.empty:
                    for _, r in kline_df.iterrows():
                        kline.append({
                            "date": str(r.get("date", ""))[:10],
                            "open": _clean(r.get("open")),
                            "close": _clean(r.get("close")),
                            "high": _clean(r.get("high")),
                            "low": _clean(r.get("low")),
                            "volume": _clean(r.get("volume")),
                        })
            except Exception as e:
                logger.warning(f"K线获取失败({code}): {e}")

            # 3. 前十大持仓 — 尝试 AKShare
            holdings = []
            try:
                saved = _no_proxy_env()
                try:
                    hold_df = await asyncio.to_thread(
                        ak.fund_portfolio_hold_em, symbol=code, date="",
                    )
                finally:
                    _restore_env(saved)
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
