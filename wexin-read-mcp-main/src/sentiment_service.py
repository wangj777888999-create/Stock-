"""个股情绪雷达 — 5 维度综合打分。

维度:
  1. price_volume  价量(放量趋势 + 换手率 + 振幅)
  2. money_flow    资金(主力净流入趋势,复用 get_money_flow,缺失时降级)
  3. hot_rank      热度(东财热榜排名 + 排名变化)
  4. blogger       博主关注度(blogger_calls 近 30 天提及次数)
  5. news          舆情(get_news 条数 vs 基线)

输出:每维度 0-100 + 综合指数 + AI 一句话判读(可选)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from database import get_db
from http_client import get_async_proxy_client
from state import config
from stock_utils import TTL_REALTIME, cache_get, cache_set

logger = logging.getLogger("sentiment-service")


# ─────────────────────────── 维度评分 ───────────────────────────

def _score_price_volume(klines: list[dict], realtime: dict) -> tuple[float, str]:
    """价量情绪:看近 5 日量价配合 + 当日量比/换手率/振幅。0-100。"""
    if not klines or len(klines) < 5:
        return 50.0, "K 线数据不足"
    recent = klines[-5:]
    vols = [k.get("volume") or 0 for k in recent]
    closes = [k.get("close") or 0 for k in recent]
    if not all(vols) or not all(closes):
        return 50.0, "数据缺失"

    # 量能趋势:近 5 日均量 vs 前 5 日均量
    if len(klines) >= 10:
        prev_avg = sum(k.get("volume") or 0 for k in klines[-10:-5]) / 5
        cur_avg = sum(vols) / 5
        vol_ratio = cur_avg / prev_avg if prev_avg > 0 else 1.0
    else:
        vol_ratio = 1.0

    # 价格趋势:近 5 日涨跌
    price_change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0

    # 评分:量价齐升最高分
    score = 50.0
    if vol_ratio > 1.5 and price_change > 3:
        score = 90.0
        note = f"放量上涨,5日量能 +{(vol_ratio-1)*100:.0f}%,价格 +{price_change:.1f}%"
    elif vol_ratio > 1.5 and price_change < -3:
        score = 25.0
        note = f"放量下跌,警惕真出货(量能 +{(vol_ratio-1)*100:.0f}%,价格 {price_change:.1f}%)"
    elif vol_ratio > 1.2 and price_change > 1:
        score = 75.0
        note = f"温和放量上涨,价格 +{price_change:.1f}%"
    elif vol_ratio < 0.7 and price_change > 1:
        score = 45.0
        note = f"缩量上涨,持续性存疑(量能 {(vol_ratio-1)*100:.0f}%)"
    elif vol_ratio < 0.7 and price_change < -1:
        score = 55.0
        note = f"缩量下跌,短期可能企稳(量能 {(vol_ratio-1)*100:.0f}%)"
    else:
        score = 50.0 + price_change * 2
        score = max(20.0, min(80.0, score))
        note = f"量价中性(量能 {(vol_ratio-1)*100:+.0f}%,价格 {price_change:+.1f}%)"

    # 当日换手率/振幅加成
    try:
        tr = float(realtime.get("换手率") or 0)
        if tr > 8:
            score = min(100.0, score + 5)
            note += f";换手 {tr:.1f}% 活跃"
        elif tr < 0.5:
            score = max(0.0, score - 5)
            note += f";换手 {tr:.1f}% 冷清"
    except (TypeError, ValueError):
        pass

    return round(score, 1), note


def _score_money_flow(flow_data: list[dict]) -> tuple[float, str]:
    """资金情绪:看近 5 日主力净流入累计 + 占比趋势。0-100。"""
    if not flow_data:
        return 50.0, "资金流数据暂不可用"

    # flow_data 最新在前;取最近 5 日
    recent = flow_data[:5]
    inflows = []
    for r in recent:
        amt = r.get("主力净流入-净额") or ""
        # 字符串"1.23亿" → 解析为浮点(亿)
        try:
            s = str(amt)
            sign = -1 if s.startswith("-") else 1
            s = s.lstrip("-")
            if "亿" in s:
                v = float(s.replace("亿", "")) * sign
            elif "万" in s:
                v = float(s.replace("万", "")) / 10000 * sign
            else:
                v = float(s) / 1e8 * sign
            inflows.append(v)
        except (ValueError, TypeError):
            pass

    if not inflows:
        return 50.0, "资金流解析失败"

    total = sum(inflows)  # 单位:亿
    positive_days = sum(1 for v in inflows if v > 0)

    # 评分:连续净流入 + 总额大 = 高分
    if total > 5 and positive_days >= 4:
        score = 90.0
        note = f"主力强势净流入,近 {len(inflows)} 日累计 +{total:.2f}亿"
    elif total > 1 and positive_days >= 3:
        score = 75.0
        note = f"主力温和净流入,累计 +{total:.2f}亿"
    elif total < -5 and positive_days <= 1:
        score = 15.0
        note = f"主力大幅净流出,累计 {total:.2f}亿"
    elif total < -1:
        score = 35.0
        note = f"主力净流出,累计 {total:.2f}亿"
    else:
        score = 50.0 + total * 2
        score = max(30.0, min(70.0, score))
        note = f"资金中性(累计 {total:+.2f}亿)"

    return round(score, 1), note


def _score_hot_rank(symbol: str, hot_list: list[dict]) -> tuple[float, str]:
    """热度情绪:看东财热榜排名(越靠前越热)。0-100。"""
    if not hot_list:
        return 50.0, "热榜数据暂不可用"
    rank = None
    for item in hot_list:
        if item.get("code") == symbol:
            rank = item.get("rank")
            break
    if rank is None:
        return 30.0, "未上东财热榜 Top 100"
    # 1-10 名 100 分;11-30 名 80 分;31-100 名 60 分
    if rank <= 10:
        score = 100 - (rank - 1) * 2  # 100, 98, 96... 82
        note = f"东财热榜第 {rank} 名,极热"
    elif rank <= 30:
        score = 80 - (rank - 10) * 1.0  # 80..60
        note = f"东财热榜第 {rank} 名,偏热"
    else:
        score = 60 - (rank - 30) * 0.4  # 60..32
        note = f"东财热榜第 {rank} 名"
    return round(max(30.0, score), 1), note


def _score_blogger(symbol: str, days: int = 30) -> tuple[float, str]:
    """博主关注度:近 N 天被 blogger_calls 提及次数。0-100。"""
    db = get_db()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.execute(
        "SELECT COUNT(*) AS cnt, COUNT(DISTINCT blogger_id) AS bcnt "
        "FROM blogger_calls WHERE symbol = ? AND call_date >= ?",
        (symbol, cutoff),
    ).fetchone()
    cnt = rows["cnt"] if rows else 0
    bcnt = rows["bcnt"] if rows else 0
    if cnt == 0:
        return 35.0, f"近 {days} 天无博主提及"
    if cnt == 1:
        score, note = 55.0, f"近 {days} 天 1 位博主提及 1 次"
    elif cnt <= 3:
        score, note = 70.0, f"近 {days} 天 {bcnt} 位博主共 {cnt} 次提及"
    elif cnt <= 6:
        score, note = 85.0, f"近 {days} 天 {bcnt} 位博主共 {cnt} 次提及,关注度高"
    else:
        score, note = 95.0, f"近 {days} 天 {bcnt} 位博主共 {cnt} 次提及,博主共振"
    return round(score, 1), note


def _score_news(news_count: int) -> tuple[float, str]:
    """舆情情绪:近 7 天新闻数。简化版,纯数量驱动。0-100。"""
    if news_count == 0:
        return 30.0, "近期无新闻"
    if news_count < 5:
        return 50.0, f"近期新闻 {news_count} 条"
    if news_count < 15:
        return 65.0, f"近期新闻 {news_count} 条"
    if news_count < 30:
        return 80.0, f"近期新闻 {news_count} 条,关注度高"
    return 92.0, f"近期新闻 {news_count} 条,舆情火热"


# ─────────────────────────── AI 一句话判读(可选) ───────────────────────────

async def _ai_one_liner(scores: dict, name: str) -> str:
    """让 AI 基于 5 维度评分给一句结论性判读。失败返回空串(不影响主流程)。"""
    if not config.ai.api_key or not config.ai.base_url:
        return ""
    prompt = (
        f"基于以下 5 维度情绪评分,用一句中文(不超过 50 字)给「{name}」做综合判读,"
        f"指出最值得关注的维度和潜在风险。\n\n"
        f"价量: {scores['price_volume']['score']} ({scores['price_volume']['note']})\n"
        f"资金: {scores['money_flow']['score']} ({scores['money_flow']['note']})\n"
        f"热度: {scores['hot_rank']['score']} ({scores['hot_rank']['note']})\n"
        f"博主: {scores['blogger']['score']} ({scores['blogger']['note']})\n"
        f"舆情: {scores['news']['score']} ({scores['news']['note']})\n"
        f"综合: {scores['composite']}\n"
    )
    try:
        client = get_async_proxy_client()
        r = await client.post(
            f"{config.ai.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {config.ai.api_key}", "Content-Type": "application/json"},
            json={
                "model": config.ai.model or "gpt-4o",
                "messages": [
                    {"role": "system", "content": "你是简洁的情绪面分析助手,只输出一句话。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 120,
            },
            timeout=30.0,
        )
        r.raise_for_status()
        return (r.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception as e:
        logger.debug(f"AI 情绪判读失败: {e}")
        return ""


# ─────────────────────────── 主入口 ───────────────────────────

async def analyze_stock_sentiment(symbol: str, market: str = "a", with_ai: bool = False) -> dict:
    """聚合 5 维度情绪 + 综合指数。带 30s 缓存。

    Returns:
        {"success": bool, "data": {symbol, market, name, scores, composite,
         level, ai_summary?, generated_at}, "error": str|None}
    """
    market = (market or "a").lower()
    cache_key = f"sentiment:{market}:{symbol}:{int(with_ai)}"
    cached = cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached, "error": None}

    # 数据采集:全部并行,失败的维度降级到 50 分
    from stock_service import StockService
    svc = StockService()

    async def _safe(coro, default):
        try:
            return await coro
        except Exception as e:
            logger.debug(f"维度数据采集失败: {e}")
            return default

    async def _hot():
        try:
            from signal_service import get_hot_stocks
            r = await get_hot_stocks(100)
            return r.get("data") or []
        except Exception:
            return []

    async def _news_count():
        try:
            r = await svc.get_news(symbol)
            return len(r.get("data") or []) if r.get("success") else 0
        except Exception:
            return 0

    realtime_t = _safe(svc.get_realtime_quote(symbol), {"success": False})
    kline_t = _safe(svc.get_kline(symbol, period="day", count=15), {"success": False, "data": []})
    flow_t = _safe(svc.get_money_flow(symbol), {"success": False, "data": []}) if market == "a" else None
    hot_t = _hot() if market == "a" else None
    news_t = _news_count()

    tasks = [realtime_t, kline_t, news_t]
    if flow_t is not None:
        tasks.append(flow_t)
    if hot_t is not None:
        tasks.append(hot_t)

    results = await asyncio.gather(*tasks, return_exceptions=False)
    realtime = results[0]
    kline = results[1]
    news_n = results[2]
    flow = results[3] if flow_t is not None else {"success": False, "data": []}
    hot_list = results[4] if (flow_t is not None and hot_t is not None) else (results[3] if hot_t is not None else [])

    rt_data = realtime.get("data", {}) if isinstance(realtime, dict) and realtime.get("success") else {}
    klines = kline.get("data", []) if isinstance(kline, dict) and kline.get("success") else []
    flow_data = flow.get("data", []) if isinstance(flow, dict) and flow.get("success") else []
    name = rt_data.get("名称", symbol)

    # 打分(所有 5 维)
    pv_score, pv_note = _score_price_volume(klines, rt_data)
    mf_score, mf_note = _score_money_flow(flow_data) if market == "a" else (50.0, "美股暂不支持资金流维度")
    hr_score, hr_note = _score_hot_rank(symbol, hot_list) if market == "a" else (50.0, "美股暂不支持热榜维度")
    bg_score, bg_note = _score_blogger(symbol)
    nw_score, nw_note = _score_news(news_n)

    # 综合:加权(价量 25% + 资金 25% + 热度 15% + 博主 20% + 舆情 15%)
    if market == "a":
        composite = (
            pv_score * 0.25 + mf_score * 0.25 + hr_score * 0.15
            + bg_score * 0.20 + nw_score * 0.15
        )
    else:
        # 美股:无资金流/热榜,价量加权拉大
        composite = pv_score * 0.45 + bg_score * 0.30 + nw_score * 0.25

    if composite < 30:
        level = "❄ 冷清"
    elif composite < 60:
        level = "→ 正常"
    elif composite < 80:
        level = "🔥 偏热"
    else:
        level = "🚨 过热"

    scores = {
        "price_volume": {"score": pv_score, "note": pv_note},
        "money_flow": {"score": mf_score, "note": mf_note},
        "hot_rank": {"score": hr_score, "note": hr_note},
        "blogger": {"score": bg_score, "note": bg_note},
        "news": {"score": nw_score, "note": nw_note},
        "composite": round(composite, 1),
    }

    data = {
        "symbol": symbol,
        "market": market,
        "name": name,
        "scores": scores,
        "composite": round(composite, 1),
        "level": level,
        "ai_summary": "",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if with_ai:
        data["ai_summary"] = await _ai_one_liner(scores, name)

    cache_set(cache_key, data, ttl=TTL_REALTIME)
    return {"success": True, "data": data, "error": None}
