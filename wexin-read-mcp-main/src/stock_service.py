"""
股票数据服务层 — A 股搜索、行情、详情、K线。

数据源：
- 搜索: AKShare stock_info_a_code_name（静态代码表）
- 行情: 腾讯股票 API（qt.gtimg.cn）
- K线: 腾讯前复权日线 API（web.ifzq.gtimg.cn）
- 公司简介: AKShare stock_profile_cninfo（巨潮资讯）
- 财务指标: AKShare stock_financial_abstract_ths（同花顺）
- 资金流向: AKShare stock_individual_fund_flow（东方财富）
- 个股新闻: AKShare stock_news_em（东方财富）

扩展方式：在对应分组下新增方法，然后在 app.py 添加路由即可。
"""

from __future__ import annotations

import asyncio
import logging
import math

import akshare as ak
import requests as _requests

from stock_utils import (
    TTL_COMPANY,
    TTL_DAILY,
    TTL_REALTIME,
    cache,
    get_exchange,
    get_market_name,
    normalize_symbol,
)

logger = logging.getLogger("stock-service")

# ─── 绕过系统代理的请求 ───

_NO_PROXY = {"http": None, "https": None}


def _get(url, **kw):
    kw.setdefault("proxies", _NO_PROXY)
    return _requests.get(url, **kw)


def _patch_requests(func, **kwargs):
    """在绕过代理的环境下调用 AKShare 函数。"""
    orig_get = _requests.get
    orig_post = _requests.post
    _requests.get = lambda url, **kw: (kw.setdefault("proxies", _NO_PROXY), orig_get(url, **kw))[1]
    _requests.post = lambda url, **kw: (kw.setdefault("proxies", _NO_PROXY), orig_post(url, **kw))[1]
    try:
        return func(**kwargs)
    finally:
        _requests.get = orig_get
        _requests.post = orig_post


# ─── 腾讯行情 API ───

_QT_URL = "http://qt.gtimg.cn/q={exchange}{code}"
# 字段索引: 1=名称 3=最新价 4=昨收 5=今开 31=涨跌额 32=涨跌幅
# 33=最高 34=最低 36=成交量(手) 37=成交额(万) 38=换手率
# 39=市盈率 43=振幅 44=总市值(亿) 45=流通市值(亿) 46=市净率


def _parse_tencent_quote(raw: str, symbol: str) -> dict | None:
    """解析腾讯行情 API 返回的单行数据。"""
    start = raw.find('"')
    end = raw.rfind('"')
    if start == -1 or end <= start:
        return None
    fields = raw[start + 1 : end].split("~")
    if len(fields) < 48 or not fields[3]:
        return None

    def _f(idx):
        try:
            return float(fields[idx])
        except (IndexError, ValueError):
            return None

    return {
        "代码": symbol, "名称": fields[1],
        "最新价": _f(3), "昨收": _f(4), "今开": _f(5),
        "最高": _f(33), "最低": _f(34),
        "涨跌额": _f(31), "涨跌幅": _f(32),
        "成交量": _f(36), "成交额": _f(37),
        "换手率": _f(38), "振幅": _f(43),
        "市盈率": _f(39), "总市值": _f(44),
        "流通市值": _f(45), "市净率": _f(46),
    }


def _clean(v):
    """将 NaN/NaT 转为 None，保持 JSON 序列化安全。"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _fmt_amount(v) -> str | None:
    """将金额统一格式化为 X.XX亿 / X.XX万，保持正负号。"""
    v = _clean(v)
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e8:
        return f"{sign}{a / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{sign}{a / 1e4:.2f}万"
    return f"{sign}{a:.2f}"


class StockService:
    """A 股数据服务，按功能分区。"""

    # 类级别缓存：预加载的股票列表 DataFrame
    _stock_list_cache: dict | None = None
    _stock_list_loaded: bool = False

    @classmethod
    async def preload_stock_list(cls) -> bool:
        """预加载股票列表到内存，启动时调用一次即可。返回是否成功。"""
        if cls._stock_list_loaded and cls._stock_list_cache is not None:
            return True

        try:
            logger.info("正在预加载股票列表...")
            df = await asyncio.to_thread(_patch_requests, ak.stock_info_a_code_name)
            cls._stock_list_cache = df
            cls._stock_list_loaded = True
            logger.info(f"股票列表加载完成，共 {len(df)} 只股票")
            return True
        except Exception as e:
            logger.error(f"预加载股票列表失败: {e}")
            return False

    # ─── 1. 搜索 ───

    async def search_stock(self, keyword: str) -> dict:
        """搜索 A 股股票，支持名称/代码模糊匹配。"""
        cache_key = f"search:{keyword}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            # 优先使用预加载的缓存
            if StockService._stock_list_cache is not None:
                df = StockService._stock_list_cache
            else:
                # 降级：实时获取（首次调用时可能发生）
                df = await asyncio.to_thread(_patch_requests, ak.stock_info_a_code_name)

            mask = (
                df["code"].str.contains(keyword, case=False, na=False)
                | df["name"].str.contains(keyword, case=False, na=False)
            )
            matched = df[mask].head(20)
            results = [
                {"code": row["code"], "name": row["name"], "market": get_market_name(row["code"])}
                for _, row in matched.iterrows()
            ]
            resp = {"success": True, "data": results}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"搜索股票失败: {e}")
            return {"success": False, "error": f"搜索失败: {e}"}

    # ─── 2. 实时行情 ───

    async def get_realtime_quote(self, symbol: str) -> dict:
        """通过腾讯 API 获取单只股票的实时行情。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"quote:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = get_exchange(symbol)
            url = _QT_URL.format(exchange=exchange, code=symbol)
            r = await asyncio.to_thread(_get, url, timeout=10)
            r.encoding = "gbk"
            record = _parse_tencent_quote(r.text, symbol)
            if record is None:
                return {"success": False, "error": f"未找到股票 {symbol}"}
            resp = {"success": True, "data": record}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"获取行情失败 {symbol}: {e}")
            return {"success": False, "error": f"获取行情失败: {e}"}

    # ─── 3. K线历史数据 ───

    async def get_kline(self, symbol: str, period: str = "day", count: int = 120) -> dict:
        """通过腾讯 API 获取前复权 K 线数据。period: day/week/month"""
        symbol = normalize_symbol(symbol)
        cache_key = f"kline:{symbol}:{period}:{count}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = get_exchange(symbol)
            url = (
                f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={exchange}{symbol},{period},2020-01-01,,{count},qfq"
            )
            r = await asyncio.to_thread(_get, url, timeout=10)
            data = r.json().get("data", {}).get(f"{exchange}{symbol}", {})
            klines = data.get("qfqday") or data.get("qfqweek") or data.get("qfqmonth") or data.get("day") or []

            records = []
            for k in klines:
                records.append({
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0,
                })
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取K线失败 {symbol}: {e}")
            return {"success": False, "error": f"获取K线失败: {e}"}

    # ─── 4. 公司简介 ───

    async def get_company_profile(self, symbol: str) -> dict:
        """获取公司基本信息（巨潮资讯）。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"profile:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(_patch_requests, ak.stock_profile_cninfo, symbol=symbol)
            if df is None or df.empty:
                return {"success": False, "error": "未找到公司信息"}
            row = df.iloc[0]
            record = {
                "公司名称": row.get("公司名称"),
                "A股简称": row.get("A股简称"),
                "所属行业": row.get("所属行业"),
                "上市日期": row.get("上市日期"),
                "注册资金": row.get("注册资金"),
                "法人代表": row.get("法人代表"),
                "官方网站": row.get("官方网站"),
                "主营业务": row.get("主营业务"),
                "经营范围": row.get("经营范围"),
                "注册地址": row.get("注册地址"),
            }
            record = {k: _clean(v) for k, v in record.items()}
            resp = {"success": True, "data": record}
            cache.set(cache_key, resp, TTL_COMPANY)
            return resp
        except Exception as e:
            logger.error(f"获取公司信息失败 {symbol}: {e}")
            return {"success": False, "error": f"获取公司信息失败: {e}"}

    # ─── 5. 财务指标 ───

    async def get_financial(self, symbol: str) -> dict:
        """获取最近几期核心财务指标（同花顺）。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"financial:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_financial_abstract_ths,
                symbol=symbol, indicator="按报告期",
            )
            if df is None or df.empty:
                return {"success": False, "error": "未找到财务数据"}

            # 按报告期降序（最新在前）
            df = df.sort_values("报告期", ascending=False).head(16)
            pick_cols = [
                "报告期", "净利润", "净利润同比增长率", "营业总收入", "营业总收入同比增长率",
                "基本每股收益", "每股净资产", "每股经营现金流",
                "销售净利率", "销售毛利率",
                "净资产收益率", "资产负债率", "流动比率",
            ]
            existing = [c for c in pick_cols if c in df.columns]
            records = df[existing].to_dict(orient="records")
            # 清理 false/NaN → None
            for r in records:
                for k, v in r.items():
                    if v is False or v == "False" or v == "false":
                        r[k] = None
                    else:
                        r[k] = _clean(v)
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取财务数据失败 {symbol}: {e}")
            return {"success": False, "error": f"获取财务数据失败: {e}"}

    # ─── 6. 资金流向 ───

    async def get_money_flow(self, symbol: str) -> dict:
        """获取近期资金流向（主力/超大单/大单/中单/小单）。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"flow:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = get_exchange(symbol)
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_individual_fund_flow,
                stock=symbol, market=exchange,
            )
            if df is None or df.empty:
                return {"success": False, "error": "未找到资金流向数据"}

            # 取最近 20 天
            df = df.tail(20).iloc[::-1]
            pick_cols = [
                "日期", "收盘价", "涨跌幅",
                "主力净流入-净额", "主力净流入-净占比",
                "超大单净流入-净额", "超大单净流入-净占比",
                "大单净流入-净额", "大单净流入-净占比",
                "中单净流入-净额", "小单净流入-净额",
            ]
            existing = [c for c in pick_cols if c in df.columns]
            amount_cols = {c for c in existing if "净额" in c}
            records = df[existing].to_dict(orient="records")
            for r in records:
                for k, v in r.items():
                    if hasattr(v, "strftime"):
                        r[k] = v.strftime("%Y-%m-%d")
                    elif k in amount_cols:
                        r[k] = _fmt_amount(v)
                    else:
                        r[k] = _clean(v)
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_REALTIME)
            return resp
        except Exception as e:
            logger.error(f"获取资金流向失败 {symbol}: {e}")
            return {"success": False, "error": f"获取资金流向失败: {e}"}

    # ─── 7. 个股新闻 ───

    async def get_news(self, symbol: str) -> dict:
        """获取个股相关新闻。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"news:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_news_em, symbol=symbol,
            )
            if df is None or df.empty:
                return {"success": True, "data": []}

            records = []
            for _, row in df.head(15).iterrows():
                records.append({
                    "title": _clean(row.get("新闻标题")),
                    "time": str(row.get("发布时间", "")),
                    "source": _clean(row.get("文章来源")),
                    "url": _clean(row.get("新闻链接")),
                    "summary": _clean(row.get("新闻内容", ""))[:120] if row.get("新闻内容") else None,
                })
            resp = {"success": True, "data": records}
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取新闻失败 {symbol}: {e}")
            return {"success": False, "error": f"获取新闻失败: {e}"}

    # ─── 8. 十大流通股东 ───

    async def get_shareholders(self, symbol: str) -> dict:
        """获取最新一期十大流通股东。"""
        symbol = normalize_symbol(symbol)
        cache_key = f"holders:{symbol}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = await asyncio.to_thread(
                _patch_requests, ak.stock_circulate_stock_holder, symbol=symbol,
            )
            if df is None or df.empty:
                return {"success": False, "error": "未找到股东数据"}

            # 取最新一期（截止日期最大的 10 条）
            latest_date = df["截止日期"].max()
            latest = df[df["截止日期"] == latest_date].head(10)
            records = []
            for _, row in latest.iterrows():
                records.append({
                    "rank": _clean(row.get("编号")),
                    "name": _clean(row.get("股东名称")),
                    "shares": _clean(row.get("持股数量")),
                    "ratio": _clean(row.get("占流通股比例")),
                    "type": _clean(row.get("股本性质")),
                })
            resp = {
                "success": True,
                "data": {"date": str(latest_date), "holders": records},
            }
            cache.set(cache_key, resp, TTL_DAILY)
            return resp
        except Exception as e:
            logger.error(f"获取股东数据失败 {symbol}: {e}")
            return {"success": False, "error": f"获取股东数据失败: {e}"}
