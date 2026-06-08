"""数据源 Provider 注册中心 — 把所有源的取数函数集中注册到 DataRouter。

新增数据源只需在这里加一个 async 函数 + register_provider 调用。
业务层用 router.fetch("contract_name") 即可,不再关心源细节。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import akshare as ak
import pandas as pd

from http_client import get_async_client, patch_requests
from services.data_router import get_router

logger = logging.getLogger("providers")

router = get_router()


# ─────────────────────────── 契约定义 ───────────────────────────

router.register_contract(
    "concept_rank",
    default_ttl=30, default_timeout=12.0,
    description="A股市场概念板块涨幅排名(返回 list[{rank, code, name, change_pct, lead_stock, ...}])",
)

router.register_contract(
    "industry_rank",
    default_ttl=30, default_timeout=12.0,
    description="A股行业板块涨幅排名",
)

router.register_contract(
    "market_breadth",
    default_ttl=60, default_timeout=30.0,
    description="A股全市场涨跌家数 + 涨跌停统计(返回 {rise_fall, limit})",
)

router.register_contract(
    "money_flow_individual",
    default_ttl=30, default_timeout=8.0,
    description="个股资金流(返回 list[{日期, 主力净流入-净额, ...}])",
)

router.register_contract(
    "stock_quote",
    default_ttl=30, default_timeout=6.0,
    description="个股实时报价(A/HK/US 通用,返回 dict[代码,名称,最新价,涨跌幅,...])",
)

router.register_contract(
    "stock_news",
    default_ttl=300, default_timeout=10.0,
    description="个股新闻列表(返回 list[{title, url, time, source}])",
)

router.register_contract(
    "stock_kline_a",
    default_ttl=3600, default_timeout=8.0,
    description="A 股日/周/月 K 线(返回 list[{date, open, close, high, low, volume}])",
)

router.register_contract(
    "hot_stocks",
    default_ttl=30, default_timeout=12.0,
    description="A股热门股榜单(返回 list[{rank, code, name, price, change_pct, ...}])",
)

router.register_contract(
    "stock_announcement",
    default_ttl=3600, default_timeout=10.0,
    description="A股公司公告(返回 list[{title, time, url, type}])",
)


# ─────────────────────────── 概念排名 Providers ───────────────────────────

async def _concept_em(limit: int = 30, **_) -> list[dict] | None:
    """东财 push2 — 概念板块排名(数据最准、有 5 档分类)。"""
    from signal_service import _fetch_em_concept_rank
    items = await asyncio.to_thread(_fetch_em_concept_rank, limit)
    return items if items else None


async def _concept_sina(limit: int = 30, **_) -> list[dict] | None:
    """新浪 newFLJK?param=class — 市场概念榜(华为汽车/BC电池/AI 手机...)。"""
    from signal_service import _fetch_sina_concept_rank
    items = await asyncio.to_thread(_fetch_sina_concept_rank)
    return items[:limit] if items else None


router.register_provider("concept_rank", "em_push2", _concept_em, weight=10.0)
router.register_provider("concept_rank", "sina_class", _concept_sina, weight=8.0)


# ─────────────────────────── 行业排名 Providers ───────────────────────────

async def _industry_sina(limit: int = 30, **_) -> list[dict] | None:
    """新浪 — 行业排名(目前你系统主源,直连可达)。"""
    from signal_service import _fetch_sina_industry_rank
    items = await asyncio.to_thread(_fetch_sina_industry_rank)
    return items[:limit] if items else None


async def _industry_ths(limit: int = 30, **_) -> list[dict] | None:
    """同花顺 — 行业板块汇总(走 AKShare)。"""
    df = await asyncio.to_thread(patch_requests, ak.stock_board_industry_summary_ths)
    if df is None or df.empty:
        return None
    # 字段映射:同花顺汇总没"领涨股"列名一致,容错取
    out = []
    for _, row in df.iterrows():
        cp_raw = row.get("涨跌幅") if "涨跌幅" in df.columns else None
        try:
            cp = float(str(cp_raw).replace("%", "")) if cp_raw is not None else None
        except (ValueError, TypeError):
            cp = None
        out.append({
            "code": str(row.get("代码", "")),
            "name": str(row.get("板块", row.get("名称", ""))),
            "change_pct": cp,
            "lead_stock": str(row.get("领涨股", row.get("龙头股", ""))),
            "lead_stock_pct": None,
        })
    out = [x for x in out if x["change_pct"] is not None]
    out.sort(key=lambda x: x["change_pct"], reverse=True)
    return out[:limit] if out else None


router.register_provider("industry_rank", "sina", _industry_sina, weight=10.0)
router.register_provider("industry_rank", "ths_summary", _industry_ths, weight=7.0)


# ─────────────────────────── 涨跌家数(全市场宽度) Providers ───────────────────────────

async def _breadth_em_legu(**_) -> dict | None:
    """东财 — stock_market_activity_legu(官方涨跌家数 + 涨停)。"""
    try:
        df = await asyncio.to_thread(patch_requests, ak.stock_market_activity_legu)
        if df is None or df.empty:
            return None
        items = dict(zip(df["item"], df["value"]))
        up = int(float(items.get("上涨", 0) or 0))
        down = int(float(items.get("下跌", 0) or 0))
        flat = int(float(items.get("平盘", 0) or 0))
        total = up + down + flat
        return {
            "rise_fall": {"up": up, "down": down, "flat": flat,
                          "ratio": round(up / total, 4) if total > 0 else 0,
                          "source_kind": "official"},
            "limit": {
                "up_limit": int(float(items.get("涨停", 0) or 0)),
                "down_limit": int(float(items.get("跌停", 0) or 0)),
                "approx": False,
            },
        }
    except Exception:
        return None


async def _breadth_sina_spot(**_) -> dict | None:
    """新浪 stock_zh_a_spot — 全市场快照,本地按涨跌幅统计(估算)。"""
    try:
        df = await asyncio.to_thread(patch_requests, ak.stock_zh_a_spot)
        if df is None or df.empty or "涨跌幅" not in df.columns:
            return None
        s = pd.to_numeric(df["涨跌幅"], errors="coerce").dropna()
        up = int((s > 0).sum())
        down = int((s < 0).sum())
        flat = int(s.eq(0).sum())
        zt = int((s >= 9.5).sum())
        dt = int((s <= -9.5).sum())
        total = up + down + flat
        return {
            "rise_fall": {"up": up, "down": down, "flat": flat,
                          "ratio": round(up / total, 4) if total > 0 else 0,
                          "source_kind": "estimate"},
            "limit": {"up_limit": zt, "down_limit": dt, "approx": True},
        }
    except Exception:
        return None


router.register_provider("market_breadth", "em_legu", _breadth_em_legu, weight=10.0)
router.register_provider("market_breadth", "sina_spot", _breadth_sina_spot, weight=6.0)


# ─────────────────────────── 个股资金流 Providers ───────────────────────────

def _fmt_amount(v) -> str | None:
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


def _to_float(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _flow_em_direct(symbol: str, exchange: str, **_) -> list[dict] | None:
    """东财 push2his — 资金流日线 20 天(数据最全)。"""
    try:
        secid = ("1." if exchange == "sh" else "0.") + symbol
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f7"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        )
        r = await get_async_client().get(url, timeout=4.0)
        if r.status_code != 200:
            return None
        klines = (r.json().get("data") or {}).get("klines") or []
        if not klines:
            return None
        recs = []
        for line in klines[-20:]:
            parts = line.split(",")
            if len(parts) < 13:
                continue
            recs.append({
                "日期": parts[0],
                "主力净流入-净额": _fmt_amount(_to_float(parts[1])),
                "小单净流入-净额": _fmt_amount(_to_float(parts[2])),
                "中单净流入-净额": _fmt_amount(_to_float(parts[3])),
                "大单净流入-净额": _fmt_amount(_to_float(parts[4])),
                "超大单净流入-净额": _fmt_amount(_to_float(parts[5])),
                "主力净流入-净占比": _to_float(parts[6]),
                "收盘价": _to_float(parts[11]),
                "涨跌幅": _to_float(parts[12]),
            })
        recs.reverse()
        return recs if recs else None
    except Exception:
        return None


async def _flow_akshare(symbol: str, exchange: str, **_) -> list[dict] | None:
    """AKShare — stock_individual_fund_flow(底层也是东财)。"""
    try:
        df = await asyncio.to_thread(
            patch_requests, ak.stock_individual_fund_flow,
            stock=symbol, market=exchange,
        )
        if df is None or df.empty:
            return None
        df = df.tail(20).iloc[::-1]
        pick = [
            "日期", "收盘价", "涨跌幅",
            "主力净流入-净额", "主力净流入-净占比",
            "超大单净流入-净额", "超大单净流入-净占比",
            "大单净流入-净额", "大单净流入-净占比",
            "中单净流入-净额", "小单净流入-净额",
        ]
        cols = [c for c in pick if c in df.columns]
        amount_cols = {c for c in cols if "净额" in c}
        recs = df[cols].to_dict(orient="records")
        for r in recs:
            for k, v in r.items():
                if hasattr(v, "strftime"):
                    r[k] = v.strftime("%Y-%m-%d")
                elif k in amount_cols:
                    r[k] = _fmt_amount(v)
        return recs
    except Exception:
        return None


async def _flow_sina_today(symbol: str, exchange: str, **_) -> list[dict] | None:
    """新浪 vFinance — 当日 1 行(最低兜底,东财全挂时)。"""
    try:
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"MoneyFlow.ssi_ssfx_flzjtj?daima={exchange}{symbol}"
        )
        r = await get_async_client().get(
            url, timeout=4.0,
            headers={"Referer": "https://finance.sina.com.cn/"},
        )
        if r.status_code != 200 or not r.text:
            return None
        txt = r.text.strip()
        if txt.startswith("var "):
            txt = txt.split("=", 1)[1].strip().rstrip(";")
        data = json.loads(txt)
        if not data or "r0_in" not in data:
            return None
        main_net = _to_float(data.get("netamount"))
        r0_net = (_to_float(data.get("r0_in")) or 0) - (_to_float(data.get("r0_out")) or 0)
        r1_net = (_to_float(data.get("r1_in")) or 0) - (_to_float(data.get("r1_out")) or 0)
        r2_net = (_to_float(data.get("r2_in")) or 0) - (_to_float(data.get("r2_out")) or 0)
        r3_net = (_to_float(data.get("r3_in")) or 0) - (_to_float(data.get("r3_out")) or 0)
        chg_pct = round((_to_float(data.get("changeratio")) or 0) * 100, 2)
        return [{
            "日期": datetime.now().strftime("%Y-%m-%d") + "(新浪当日)",
            "收盘价": _to_float(data.get("trade")),
            "涨跌幅": chg_pct,
            "主力净流入-净额": _fmt_amount(main_net),
            "主力净流入-净占比": _to_float(data.get("r0x_ratio")),
            "超大单净流入-净额": _fmt_amount(r0_net),
            "大单净流入-净额": _fmt_amount(r1_net),
            "中单净流入-净额": _fmt_amount(r2_net),
            "小单净流入-净额": _fmt_amount(r3_net),
        }]
    except Exception:
        return None


router.register_provider("money_flow_individual", "em_direct", _flow_em_direct, weight=10.0)
router.register_provider("money_flow_individual", "akshare", _flow_akshare, weight=9.0)
router.register_provider("money_flow_individual", "sina_today", _flow_sina_today, weight=4.0)


# ─────────────────────────── 实时报价 Providers ───────────────────────────

_TENCENT_QT_FMT = "https://qt.gtimg.cn/q={exchange}{code}"


async def _quote_tencent(symbol: str, market: str, original: str, **_) -> dict | None:
    """腾讯 qt.gtimg.cn — 报价主源,字段最全(PE/PB/市值/换手率)。"""
    try:
        from stock_service import _parse_tencent_quote, _QT_URL
        if market == "a":
            from stock_utils import get_exchange
            exchange = get_exchange(symbol)
            url_code = symbol
        elif market == "hk":
            exchange = "hk"
            url_code = original
        else:
            exchange = "us"
            url_code = symbol
        url = _QT_URL.format(exchange=exchange, code=url_code)
        r = await get_async_client().get(url, timeout=4.0)
        text = r.content.decode("gbk", errors="replace")
        record = _parse_tencent_quote(text, symbol)
        if record is None:
            return None
        record["市场"] = {"a": "A股", "hk": "港股", "us": "美股"}[market]
        return record
    except Exception:
        return None


async def _quote_sina(symbol: str, market: str, original: str, **_) -> dict | None:
    """新浪 hq.sinajs.cn — 仅 A 股,字段少(无 PE/市值),作为腾讯不可用时兜底。"""
    if market != "a":
        return None
    try:
        from stock_utils import get_exchange
        exchange = get_exchange(symbol)
        sina_code = f"{exchange}{symbol}"
        url = f"https://hq.sinajs.cn/list={sina_code}"
        r = await get_async_client().get(
            url, timeout=4.0,
            headers={"Referer": "https://finance.sina.com.cn/"},
        )
        text = r.content.decode("gbk", errors="replace")
        # 提取 var=xxx="..."; 里的内容
        start = text.find('"')
        end = text.rfind('"')
        if start == -1 or end <= start:
            return None
        parts = text[start + 1:end].split(",")
        if len(parts) < 32:
            return None
        # 字段:0=名称,1=今开,2=昨收,3=现价,4=最高,5=最低,8=成交量(股),9=成交额(元)
        def _f(i):
            try:
                return float(parts[i])
            except (ValueError, IndexError):
                return None
        cur = _f(3)
        prev = _f(2)
        chg = (cur - prev) if (cur is not None and prev is not None) else None
        chg_pct = (chg / prev * 100) if (chg is not None and prev) else None
        amount_wan = _f(9) / 10000 if _f(9) else None
        vol_lots = int(_f(8) / 100) if _f(8) else None  # 股 → 手
        return {
            "代码": symbol,
            "名称": parts[0],
            "最新价": cur,
            "今开": _f(1),
            "昨收": prev,
            "最高": _f(4),
            "最低": _f(5),
            "涨跌额": round(chg, 2) if chg is not None else None,
            "涨跌幅": round(chg_pct, 2) if chg_pct is not None else None,
            "成交量": vol_lots,
            "成交额": round(amount_wan, 0) if amount_wan else None,
            "换手率": None,   # 新浪不给
            "振幅": None,
            "市盈率": None,
            "市净率": None,
            "总市值": None,
            "流通市值": None,
            "市场": "A股",
        }
    except Exception:
        return None


router.register_provider("stock_quote", "tencent", _quote_tencent, weight=10.0)
# sina_hq 字段不全(无 PE/市值),让步 800ms 给腾讯先机;腾讯失败时 sina 才接管
router.register_provider("stock_quote", "sina_hq", _quote_sina, weight=5.0, handicap_ms=800)


# ─────────────────────────── 新闻 Providers ───────────────────────────

async def _news_em(symbol: str, **_) -> list[dict] | None:
    """东财 — stock_news_em(A股/美股/港股)。"""
    try:
        df = await asyncio.to_thread(patch_requests, ak.stock_news_em, symbol=symbol)
        if df is None or df.empty:
            return None
        out = []
        for _, row in df.head(20).iterrows():
            out.append({
                "title": str(row.get("新闻标题", "")),
                "url": str(row.get("新闻链接", "")),
                "time": str(row.get("发布时间", "")),
                "source": str(row.get("文章来源", "")),
            })
        return out if out else None
    except Exception:
        return None


router.register_provider("stock_news", "em", _news_em, weight=10.0)


# ─────────────────────────── A股 K 线 Providers ───────────────────────────

_KLINE_PERIOD_MAP_AK = {"day": "daily", "week": "weekly", "month": "monthly"}


async def _kline_tencent(symbol: str, period: str, count: int, exchange_prefix: str, **_) -> list[dict] | None:
    """腾讯 K 线 — 主源,延迟低、稳定。"""
    try:
        tencent_period = {"day": "day", "week": "week", "month": "month"}.get(period, "day")
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={exchange_prefix}{symbol},{tencent_period},,,{min(count, 640)},qfq"
        )
        r = await get_async_client().get(url, timeout=4.0)
        data = r.json().get("data", {}).get(f"{exchange_prefix}{symbol}", {})
        kdata = (
            data.get(tencent_period, []) or
            data.get(f"qfq{tencent_period}", []) or
            data.get("qfqday", []) or
            data.get("day", [])
        )
        if not kdata:
            return None
        recs = []
        for item in kdata:
            recs.append({
                "date": item[0],
                "open":  float(item[1]) if item[1] else None,
                "close": float(item[2]) if item[2] else None,
                "high":  float(item[3]) if item[3] else None,
                "low":   float(item[4]) if item[4] else None,
                "volume": float(item[5]) if len(item) > 5 and item[5] else None,
            })
        return recs if recs else None
    except Exception:
        return None


async def _kline_akshare(symbol: str, period: str, count: int, **_) -> list[dict] | None:
    """AKShare stock_zh_a_hist — 备选,底层东财。"""
    try:
        ak_period = _KLINE_PERIOD_MAP_AK.get(period, "daily")
        df = await asyncio.to_thread(
            patch_requests, ak.stock_zh_a_hist,
            symbol=symbol, period=ak_period,
            start_date="20050101", end_date="20300101", adjust="qfq",
        )
        if df is None or df.empty:
            return None
        df = df.tail(count) if count < 99999 else df
        recs = []
        for _, row in df.iterrows():
            try:
                d = row.get("日期")
                recs.append({
                    "date": str(d) if not hasattr(d, "strftime") else d.strftime("%Y-%m-%d"),
                    "open":  float(row.get("开盘")) if row.get("开盘") is not None else None,
                    "close": float(row.get("收盘")) if row.get("收盘") is not None else None,
                    "high":  float(row.get("最高")) if row.get("最高") is not None else None,
                    "low":   float(row.get("最低")) if row.get("最低") is not None else None,
                    "volume": float(row.get("成交量")) if row.get("成交量") is not None else None,
                })
            except (ValueError, TypeError):
                continue
        return recs if recs else None
    except Exception:
        return None


router.register_provider("stock_kline_a", "tencent", _kline_tencent, weight=10.0)
router.register_provider("stock_kline_a", "akshare", _kline_akshare, weight=8.0)


# ─────────────────────────── 热门股榜 Providers ───────────────────────────

async def _hot_em(limit: int = 20, **_) -> list[dict] | None:
    """东财 emappdata + 腾讯批量行情 — 当前主路径(已可达)。"""
    try:
        from signal_service import _fetch_hot_rank_list, _batch_tencent_quotes
        rank_list = await asyncio.to_thread(_fetch_hot_rank_list, limit)
        if not rank_list:
            return None
        qt_codes = [item["qt_code"] for item in rank_list]
        quotes = await _batch_tencent_quotes(qt_codes)
        records = []
        for item in rank_list[:limit]:
            code = item["code"]
            q = quotes.get(code, {})
            records.append({
                "rank": item["rank"],
                "code": code,
                "name": q.get("name") or item.get("name"),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "volume": q.get("volume"),
                "amount": q.get("amount"),
                "turnover_rate": q.get("turnover_rate"),
            })
        return records if records else None
    except Exception:
        return None


router.register_provider("hot_stocks", "em_emappdata", _hot_em, weight=10.0)


# ─────────────────────────── 公告 Providers ───────────────────────────

async def _announcement_cninfo(symbol: str, **_) -> list[dict] | None:
    """巨潮咨询 — A股公告主源(AKShare stock_zh_a_disclosure_report_cninfo)。"""
    try:
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
        df = await asyncio.to_thread(
            patch_requests, ak.stock_zh_a_disclosure_report_cninfo,
            symbol=symbol, start_date=start_date, end_date=end_date,
        )
        if df is None or df.empty:
            return None
        recs = []
        for _, row in df.head(30).iterrows():
            recs.append({
                "title": str(row.get("公告标题", "")),
                "time": str(row.get("公告时间", "")),
                "url": str(row.get("公告链接", "")),
                "type": str(row.get("公告类别", "")),
            })
        return recs if recs else None
    except Exception:
        return None


router.register_provider("stock_announcement", "cninfo", _announcement_cninfo, weight=10.0)


# ─────────────────────────── 初始化标志 ───────────────────────────

_initialized = False


def ensure_initialized() -> None:
    """幂等的初始化函数(供 app.py 启动时调用)。"""
    global _initialized
    if _initialized:
        return
    _initialized = True
    logger.info(
        f"DataRouter providers ready: "
        f"{', '.join(sorted(router._contracts.keys()))}"
    )
