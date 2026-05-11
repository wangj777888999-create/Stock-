"""板块行情服务 — 东方财富为主 + 同花顺备选，双源容灾。

提供行业板块/概念板块列表、成分股、K线查询。
"""

import asyncio
import logging
import re
from typing import Literal

import pandas as pd
from stock_utils import _clean, cache, TTL_DAILY, TTL_REALTIME
from http_client import patch_requests

logger = logging.getLogger(__name__)

# ─── 常量 ───

PERIOD_MAP = {"daily": "日k", "weekly": "周k", "monthly": "月k"}

BoardType = Literal["industry", "concept"]


# ─── 数据源层（同步函数，通过 asyncio.to_thread 调用） ───

def _fetch_boards_eastmoney(board_type: str) -> pd.DataFrame:
    """东方财富板块列表。"""
    import akshare as ak
    if board_type == "industry":
        df = patch_requests(ak.stock_board_industry_name_em)
    else:
        df = patch_requests(ak.stock_board_concept_name_em)
    logger.info(f"东方财富获取 {board_type} 板块 {len(df)} 个")
    return df


def _fetch_boards_ths(board_type: str) -> pd.DataFrame:
    """同花顺板块列表（备选，仅 name/code）。"""
    import akshare as ak
    if board_type == "industry":
        df = patch_requests(ak.stock_board_industry_name_ths)
    else:
        df = patch_requests(ak.stock_board_concept_name_ths)
    logger.info(f"同花顺获取 {board_type} 板块 {len(df)} 个")
    return df


def _fetch_boards_ths_summary() -> pd.DataFrame:
    """同花顺行业板块行情汇总（含涨跌幅、成交额、领涨股等）。"""
    import akshare as ak
    df = patch_requests(ak.stock_board_industry_summary_ths)
    logger.info(f"同花顺行业板块汇总 {len(df)} 个")
    return df


def _fetch_stocks_eastmoney(board_name: str, board_type: str) -> pd.DataFrame:
    """东方财富板块成分股。"""
    import akshare as ak
    if board_type == "industry":
        df = patch_requests(ak.stock_board_industry_cons_em, symbol=board_name)
    else:
        df = patch_requests(ak.stock_board_concept_cons_em, symbol=board_name)
    logger.info(f"东方财富获取 {board_name} 成分股 {len(df)} 只")
    return df


def _fetch_stocks_ths(board_name: str, board_type: str) -> pd.DataFrame:
    """同花顺板块成分股（akshare 无此接口，始终失败）。"""
    raise NotImplementedError("akshare 无同花顺板块成分股接口")


def _fetch_kline_eastmoney(board_name: str, board_type: str, period: str) -> pd.DataFrame:
    """东方财富板块 K 线。"""
    import akshare as ak
    ak_period = PERIOD_MAP.get(period, "日k")
    if board_type == "industry":
        df = patch_requests(ak.stock_board_industry_hist_em, symbol=board_name, period=ak_period)
    else:
        df = patch_requests(ak.stock_board_concept_hist_em, symbol=board_name, period=ak_period)
    logger.info(f"东方财富获取 {board_name} {period} K线 {len(df)} 条")
    return df


def _fetch_kline_ths(board_name: str, board_type: str, count: int) -> pd.DataFrame:
    """同花顺板块 K 线（备选）。"""
    import akshare as ak
    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
    if board_type == "industry":
        df = patch_requests(ak.stock_board_industry_index_ths, symbol=board_name, start_date=start, end_date=end)
    else:
        df = patch_requests(ak.stock_board_concept_index_ths, symbol=board_name, start_date=start, end_date=end)
    logger.info(f"同花顺获取 {board_name} K线 {len(df)} 条")
    return df


# ─── 标准化层 ───

def _extract_lead_stock_symbol(lead_stock_field) -> str | None:
    """从领涨股票字段中提取 6 位股票代码。"""
    if lead_stock_field is None:
        return None
    text = str(lead_stock_field)
    match = re.search(r"(\d{6})", text)
    if match:
        return match.group(1)
    # 如果没有代码（如同花顺只给名称），尝试从全局股票列表查找
    return _lookup_symbol_by_name(text)


def _lookup_symbol_by_name(name: str) -> str | None:
    """通过股票名称查找代码（使用预加载的股票列表）。"""
    if not name or name == "--":
        return None
    try:
        from stock_service import StockService
        df = StockService._stock_list_cache
        if df is not None and not df.empty:
            matches = df[df["名称"] == name]
            if not matches.empty:
                return str(matches.iloc[0]["代码"])
    except Exception:
        pass
    return None


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
        cs = str(c)
        if col_name is None and ("板块名称" in cs or cs == "板块" or cs == "名称" or "概念名称" in cs):
            col_name = c
        elif col_change is None and "涨跌幅" in cs:
            col_change = c
        elif col_turnover is None and ("总成交额" in cs or "成交额" in cs or "总市值" in cs):
            col_turnover = c
        elif col_up is None and "上涨" in cs and "家" in cs:
            col_up = c
        elif col_down is None and "下跌" in cs and "家" in cs:
            col_down = c
        elif col_lead is None and "领涨" in cs:
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
    # 同花顺列名: 日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额
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

    has_change_pct = "change_pct" in col_map
    prev_close = None

    for _, row in df.iterrows():
        date_val = _clean(row.get(col_map.get("date", df.columns[0])))
        close_val = _clean(row.get(col_map.get("close"))) if "close" in col_map else None

        # 如果数据源没有涨跌幅列，从收盘价计算
        if has_change_pct:
            change_pct = _clean(row.get(col_map.get("change_pct")))
        elif close_val is not None and prev_close is not None and prev_close != 0:
            change_pct = round((float(close_val) - float(prev_close)) / float(prev_close) * 100, 2)
        else:
            change_pct = None

        item = {
            "date": str(date_val)[:10] if date_val else "",
            "open": _clean(row.get(col_map.get("open"))) if "open" in col_map else None,
            "close": close_val,
            "high": _clean(row.get(col_map.get("high"))) if "high" in col_map else None,
            "low": _clean(row.get(col_map.get("low"))) if "low" in col_map else None,
            "volume": _clean(row.get(col_map.get("volume"))) if "volume" in col_map else None,
            "turnover": _clean(row.get(col_map.get("turnover"))) if "turnover" in col_map else None,
            "change_pct": change_pct,
        }
        results.append(item)

        if close_val is not None:
            prev_close = float(close_val)

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
            if board_type == "industry":
                # 同花顺行业汇总有完整行情数据（涨跌幅、成交额、领涨股）
                df = await asyncio.to_thread(_fetch_boards_ths_summary)
            else:
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
            # 主源: 东方财富
            try:
                df = await asyncio.to_thread(_fetch_kline_eastmoney, board_name, board_type, period)
                if df is not None and not df.empty:
                    data = _normalize_kline(df)
                    if count > 0:
                        data = data[-count:]
                    resp = {"success": True, "data": data, "total": len(data)}
                    cache.set(ck, resp, TTL_DAILY)
                    return resp
            except Exception as e:
                logger.warning(f"东方财富K线失败({board_name}): {e}")

            # 备选: 同花顺
            logger.info(f"切换到同花顺获取 {board_name} K线")
            try:
                df = await asyncio.to_thread(_fetch_kline_ths, board_name, board_type, count or 120)
                if df is not None and not df.empty:
                    data = _normalize_kline(df)
                    if count > 0:
                        data = data[-count:]
                    resp = {"success": True, "data": data, "total": len(data)}
                    cache.set(ck, resp, TTL_DAILY)
                    return resp
            except Exception as e:
                logger.warning(f"同花顺K线也失败({board_name}): {e}")

            return {"success": False, "error": f"板块 '{board_name}' K线数据不可用"}

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
