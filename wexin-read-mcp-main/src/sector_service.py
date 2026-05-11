"""板块行情服务 — 东方财富为主 + 同花顺备选，双源容灾。

提供行业板块/概念板块列表、成分股、K线查询。
"""

import asyncio
import logging
import os
import re
from typing import Literal

import pandas as pd
from stock_utils import _clean, cache, TTL_DAILY, TTL_REALTIME

logger = logging.getLogger(__name__)

# ─── 常量 ───

PROXY_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")

PERIOD_MAP = {"daily": "日k", "weekly": "周k", "monthly": "月k"}

BoardType = Literal["industry", "concept"]


# ─── 代理环境工具 ───

def _no_proxy_env():
    """返回被清除的代理环境变量（用于 restore）。"""
    saved = {k: os.environ.pop(k) for k in PROXY_KEYS if k in os.environ}
    return saved


def _restore_env(saved):
    """恢复之前清除的代理环境变量。"""
    for k, v in saved.items():
        os.environ[k] = v


# ─── 数据源层（同步函数，通过 asyncio.to_thread 调用） ───

def _fetch_boards_eastmoney(board_type: str) -> pd.DataFrame:
    """东方财富板块列表。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_name_em()
        else:
            df = ak.stock_board_concept_name_em()
        logger.info(f"东方财富获取 {board_type} 板块 {len(df)} 个")
        return df
    finally:
        _restore_env(saved)


def _fetch_boards_ths(board_type: str) -> pd.DataFrame:
    """同花顺板块列表（备选）。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_name_ths()
        else:
            df = ak.stock_board_concept_name_ths()
        logger.info(f"同花顺获取 {board_type} 板块 {len(df)} 个")
        return df
    finally:
        _restore_env(saved)


def _fetch_stocks_eastmoney(board_name: str, board_type: str) -> pd.DataFrame:
    """东方财富板块成分股。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_cons_em(symbol=board_name)
        else:
            df = ak.stock_board_concept_cons_em(symbol=board_name)
        logger.info(f"东方财富获取 {board_name} 成分股 {len(df)} 只")
        return df
    finally:
        _restore_env(saved)


def _fetch_stocks_ths(board_name: str, board_type: str) -> pd.DataFrame:
    """同花顺板块成分股（备选）。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_cons_ths(symbol=board_name)
        else:
            df = ak.stock_board_concept_cons_ths(symbol=board_name)
        logger.info(f"同花顺获取 {board_name} 成分股 {len(df)} 只")
        return df
    finally:
        _restore_env(saved)


def _fetch_kline_eastmoney(board_name: str, board_type: str, period: str) -> pd.DataFrame:
    """东方财富板块 K 线。"""
    import akshare as ak
    saved = _no_proxy_env()
    try:
        ak_period = PERIOD_MAP.get(period, "日k")
        if board_type == "industry":
            df = ak.stock_board_industry_hist_em(symbol=board_name, period=ak_period)
        else:
            df = ak.stock_board_concept_hist_em(symbol=board_name, period=ak_period)
        logger.info(f"东方财富获取 {board_name} {period} K线 {len(df)} 条")
        return df
    finally:
        _restore_env(saved)


# ─── 标准化层 ───

def _extract_lead_stock_symbol(lead_stock_field) -> str | None:
    """从领涨股票字段中提取 6 位股票代码。"""
    if lead_stock_field is None:
        return None
    text = str(lead_stock_field)
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else None


def _normalize_boards(df: pd.DataFrame, board_type: str) -> list[dict]:
    """标准化板块列表为统一输出格式。"""
    results = []
    # 东方财富列名: 板块名称, 涨跌幅, 总成交额, 上涨家数, 下跌家数, 领涨股票
    # 同花顺列名: 板块名称 (可能不同，兼容处理)
    col_name = None
    col_change = None
    col_turnover = None
    col_up = None
    col_down = None
    col_lead = None

    for c in df.columns:
        if "板块名称" in str(c) or "名称" in str(c):
            col_name = c
        elif "涨跌幅" in str(c):
            col_change = c
        elif "总成交额" in str(c) or "成交额" in str(c):
            col_turnover = c
        elif "上涨" in str(c) and "家" in str(c):
            col_up = c
        elif "下跌" in str(c) and "家" in str(c):
            col_down = c
        elif "领涨" in str(c):
            col_lead = c

    # 降级: 如果没有"板块名称"列，尝试首列或 index
    if col_name is None:
        col_name = df.columns[0] if len(df.columns) > 0 else None

    for _, row in df.iterrows():
        item = {
            "name": _clean(row.get(col_name)) or "",
            "type": board_type,
            "change_pct": _clean(row.get(col_change)) if col_change else None,
            "turnover": _clean(row.get(col_turnover)) if col_turnover else None,
            "up_count": _clean(row.get(col_up)) if col_up else None,
            "down_count": _clean(row.get(col_down)) if col_down else None,
            "lead_stock_name": None,
            "lead_stock_symbol": None,
        }

        # 领涨股票处理
        if col_lead:
            lead_val = row.get(col_lead)
            item["lead_stock_name"] = _clean(lead_val)
            item["lead_stock_symbol"] = _extract_lead_stock_symbol(lead_val)

        # 类型修正
        if item["up_count"] is not None:
            try:
                item["up_count"] = int(item["up_count"])
            except (TypeError, ValueError):
                pass
        if item["down_count"] is not None:
            try:
                item["down_count"] = int(item["down_count"])
            except (TypeError, ValueError):
                pass

        results.append(item)

    return results


def _normalize_stocks(df: pd.DataFrame) -> list[dict]:
    """标准化成分股列表为统一输出格式。"""
    results = []
    # 东方财富列名: 代码, 名称, 最新价, 涨跌幅, 成交量, 成交额
    # 同花顺列名: 代码, 名称
    col_map = {}
    for c in df.columns:
        cs = str(c)
        if cs == "代码" or "代码" in cs:
            col_map.setdefault("symbol", c)
        elif cs == "名称" or "名称" in cs:
            col_map.setdefault("name", c)
        elif "最新价" in cs or cs == "收盘价":
            col_map.setdefault("price", c)
        elif "涨跌幅" in cs:
            col_map.setdefault("change_pct", c)
        elif "成交量" in cs:
            col_map.setdefault("volume", c)
        elif cs == "成交额" or "成交额" in cs:
            col_map.setdefault("turnover", c)

    for _, row in df.iterrows():
        item = {
            "symbol": str(_clean(row.get(col_map.get("symbol", df.columns[0]))) or ""),
            "name": _clean(row.get(col_map.get("name", ""))) or "",
            "price": _clean(row.get(col_map.get("price"))) if "price" in col_map else None,
            "change_pct": _clean(row.get(col_map.get("change_pct"))) if "change_pct" in col_map else None,
            "volume": _clean(row.get(col_map.get("volume"))) if "volume" in col_map else None,
            "turnover": _clean(row.get(col_map.get("turnover"))) if "turnover" in col_map else None,
        }
        results.append(item)

    return results


def _normalize_kline(df: pd.DataFrame) -> list[dict]:
    """标准化 K 线数据为统一输出格式。"""
    results = []
    # 东方财富列名: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 涨跌幅
    col_map = {}
    for c in df.columns:
        cs = str(c)
        if "日期" in cs:
            col_map.setdefault("date", c)
        elif cs == "开盘" or "开盘" in cs:
            col_map.setdefault("open", c)
        elif cs == "收盘" or "收盘" in cs:
            col_map.setdefault("close", c)
        elif cs == "最高" or "最高" in cs:
            col_map.setdefault("high", c)
        elif cs == "最低" or "最低" in cs:
            col_map.setdefault("low", c)
        elif cs == "成交量" or "成交量" in cs:
            col_map.setdefault("volume", c)
        elif cs == "成交额" or "成交额" in cs:
            col_map.setdefault("turnover", c)
        elif "涨跌幅" in cs:
            col_map.setdefault("change_pct", c)

    for _, row in df.iterrows():
        date_val = _clean(row.get(col_map.get("date", df.columns[0])))
        item = {
            "date": str(date_val)[:10] if date_val else "",
            "open": _clean(row.get(col_map.get("open"))) if "open" in col_map else None,
            "close": _clean(row.get(col_map.get("close"))) if "close" in col_map else None,
            "high": _clean(row.get(col_map.get("high"))) if "high" in col_map else None,
            "low": _clean(row.get(col_map.get("low"))) if "low" in col_map else None,
            "volume": _clean(row.get(col_map.get("volume"))) if "volume" in col_map else None,
            "turnover": _clean(row.get(col_map.get("turnover"))) if "turnover" in col_map else None,
            "change_pct": _clean(row.get(col_map.get("change_pct"))) if "change_pct" in col_map else None,
        }
        results.append(item)

    return results


# ─── 主服务类 ───

class SectorService:
    """板块行情服务 — 双源容灾 + 缓存。"""

    async def get_boards(
        self,
        board_type: str = "all",
        sort_by: str = "change_pct",
        ascending: bool = False,
        limit: int = 50,
    ) -> dict:
        """获取板块列表。

        Args:
            board_type: "industry" | "concept" | "all"
            sort_by: 排序字段 ("change_pct" | "turnover" | "name")
            ascending: 是否升序
            limit: 返回数量上限
        """
        try:
            if board_type == "all":
                # 并发获取行业 + 概念
                industry_task = self._get_boards_single("industry")
                concept_task = self._get_boards_single("concept")
                industry_result, concept_result = await asyncio.gather(
                    industry_task, concept_task, return_exceptions=True
                )

                all_boards = []
                if isinstance(industry_result, list):
                    all_boards.extend(industry_result)
                else:
                    logger.error(f"行业板块获取失败: {industry_result}")

                if isinstance(concept_result, list):
                    all_boards.extend(concept_result)
                else:
                    logger.error(f"概念板块获取失败: {concept_result}")

                if not all_boards:
                    return {"success": False, "error": "所有板块数据源均不可用"}

                # 内存排序
                all_boards = self._sort_boards(all_boards, sort_by, ascending)
                return {"success": True, "data": all_boards[:limit], "total": len(all_boards)}
            else:
                boards = await self._get_boards_single(board_type)
                if not boards:
                    return {"success": False, "error": f"{board_type} 板块数据不可用"}
                boards = self._sort_boards(boards, sort_by, ascending)
                return {"success": True, "data": boards[:limit], "total": len(boards)}

        except Exception as e:
            logger.error(f"get_boards error: {e}")
            return {"success": False, "error": str(e)}

    async def _get_boards_single(self, board_type: str) -> list[dict]:
        """获取单类板块列表（带缓存 + 双源降级）。"""
        ck = f"sector:boards:raw:{board_type}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        # 主源: 东方财富
        try:
            df = await asyncio.to_thread(_fetch_boards_eastmoney, board_type)
            if df is not None and not df.empty:
                data = _normalize_boards(df, board_type)
                cache.set(ck, data, TTL_DAILY)
                return data
        except Exception as e:
            logger.warning(f"东方财富 {board_type} 板块失败: {e}")

        # 备选: 同花顺
        logger.info(f"切换到同花顺获取 {board_type} 板块")
        try:
            df = await asyncio.to_thread(_fetch_boards_ths, board_type)
            if df is not None and not df.empty:
                data = _normalize_boards(df, board_type)
                cache.set(ck, data, TTL_DAILY)
                return data
        except Exception as e:
            logger.warning(f"同花顺 {board_type} 板块也失败: {e}")

        return []

    @staticmethod
    def _sort_boards(boards: list[dict], sort_by: str, ascending: bool) -> list[dict]:
        """内存排序板块列表。"""
        if sort_by == "name":
            return sorted(boards, key=lambda x: x.get("name") or "", reverse=not ascending)
        elif sort_by == "turnover":
            return sorted(
                boards,
                key=lambda x: x.get("turnover") if x.get("turnover") is not None else float("-inf"),
                reverse=not ascending,
            )
        else:  # default: change_pct
            return sorted(
                boards,
                key=lambda x: x.get("change_pct") if x.get("change_pct") is not None else float("-inf"),
                reverse=not ascending,
            )

    async def get_board_stocks(self, board_name: str, board_type: str = "industry") -> dict:
        """获取板块成分股。

        Args:
            board_name: 板块名称
            board_type: "industry" | "concept"
        """
        ck = f"sector:stocks:{board_type}:{board_name}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            # 主源: 东方财富
            try:
                df = await asyncio.to_thread(_fetch_stocks_eastmoney, board_name, board_type)
                if df is not None and not df.empty:
                    data = _normalize_stocks(df)
                    resp = {"success": True, "data": data, "total": len(data)}
                    cache.set(ck, resp, TTL_REALTIME)
                    return resp
            except Exception as e:
                logger.warning(f"东方财富成分股失败({board_name}): {e}")

            # 备选: 同花顺
            logger.info(f"切换到同花顺获取 {board_name} 成分股")
            try:
                df = await asyncio.to_thread(_fetch_stocks_ths, board_name, board_type)
                if df is not None and not df.empty:
                    data = _normalize_stocks(df)
                    resp = {"success": True, "data": data, "total": len(data)}
                    cache.set(ck, resp, TTL_REALTIME)
                    return resp
            except Exception as e:
                logger.warning(f"同花顺成分股也失败({board_name}): {e}")

            return {"success": False, "error": f"板块 '{board_name}' 成分股数据不可用"}

        except Exception as e:
            logger.error(f"get_board_stocks error: {e}")
            return {"success": False, "error": str(e)}

    async def get_board_kline(
        self,
        board_name: str,
        board_type: str = "industry",
        period: str = "daily",
        count: int = 100,
    ) -> dict:
        """获取板块 K 线数据。

        Args:
            board_name: 板块名称
            board_type: "industry" | "concept"
            period: "daily" | "weekly" | "monthly"
            count: 返回条数
        """
        ck = f"sector:kline:{board_type}:{board_name}:{period}"
        cached = cache.get(ck)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(_fetch_kline_eastmoney, board_name, board_type, period)
            if df is not None and not df.empty:
                data = _normalize_kline(df)
                if count > 0:
                    data = data[-count:]
                resp = {"success": True, "data": data, "total": len(data)}
                cache.set(ck, resp, TTL_DAILY)
                return resp
            return {"success": False, "error": f"板块 '{board_name}' K线数据为空"}

        except Exception as e:
            logger.error(f"get_board_kline error: {e}")
            return {"success": False, "error": f"K线数据获取失败: {e}"}

    async def search(self, keyword: str, board_type: str = "all") -> dict:
        """按关键词搜索板块。

        Args:
            keyword: 搜索关键词
            board_type: "industry" | "concept" | "all"
        """
        try:
            if board_type == "all":
                industry_task = self._get_boards_single("industry")
                concept_task = self._get_boards_single("concept")
                industry_result, concept_result = await asyncio.gather(
                    industry_task, concept_task, return_exceptions=True
                )
                all_boards = []
                if isinstance(industry_result, list):
                    all_boards.extend(industry_result)
                if isinstance(concept_result, list):
                    all_boards.extend(concept_result)
            else:
                all_boards = await self._get_boards_single(board_type)

            if not all_boards:
                return {"success": False, "error": "板块数据不可用"}

            # 按关键词过滤
            if keyword:
                kw = keyword.lower()
                all_boards = [b for b in all_boards if kw in (b.get("name") or "").lower()]

            return {"success": True, "data": all_boards[:50], "total": len(all_boards)}

        except Exception as e:
            logger.error(f"search error: {e}")
            return {"success": False, "error": str(e)}
