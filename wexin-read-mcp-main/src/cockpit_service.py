"""
驾驶舱服务层 — 市场情绪聚合、指数实时报价、分时数据。

数据源：
- 情绪: AKShare 市场活跃度(stock_market_activity_legu) + 资金流向(stock_market_fund_flow)
- 指数报价: 腾讯行情 API（qt.gtimg.cn）
- 分时数据: 腾讯分钟 API（web.ifzq.gtimg.cn）+ 腾讯昨收价
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import akshare as ak

from stock_utils import _clean, cache
from http_client import patch_requests, get_async_client

logger = logging.getLogger("cockpit-service")

# ─── 主要指数列表 ───

INDICES = [
    {"code": "000001", "name": "上证指数",   "qt": "sh000001"},
    {"code": "399006", "name": "创业板指",   "qt": "sz399006"},
    {"code": "000688", "name": "科创50",     "qt": "sh000688"},
    {"code": "930050", "name": "中证A50",    "qt": "sz159591"},
    {"code": "930500", "name": "中证A500",   "qt": "sz159361"},
    {"code": "932000", "name": "中证2000",   "qt": "sz159531"},
]

# 仅用于两市成交额汇总、不渲染卡片的指数
_AUX_INDICES = [
    {"code": "399001", "name": "深证成指",   "qt": "sz399001"},
]

_AKSHARE_TIMEOUT = 10


def _fetch_breadth_via_sina_spot() -> dict | None:
    """新浪全市场快照降级:东财不可达时,拉新浪 stock_zh_a_spot 算涨跌家数 + 涨停数。

    数据特点:
      - ~5500 只 A 股的实时涨跌幅
      - 全量拉取约 6-20 秒(波动大,新浪并发限制),因此结果自带 60s 缓存
      - 涨停/跌停按 |涨跌幅| >= 9.5% 估算,会漏掉创业板/科创板 20% 涨跌停
        和 ST 5% 涨跌停;比东财官方数稍粗。
    """
    # 自带 60s 缓存:防止前端高频触发全市场拉取
    cached = cache.get("cockpit:sina_breadth")
    if cached is not None:
        return cached
    try:
        import pandas as pd
        df = patch_requests(ak.stock_zh_a_spot)
        if df is None or df.empty:
            return None
        col = "涨跌幅" if "涨跌幅" in df.columns else None
        if not col:
            return None
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        up = int((s > 0).sum())
        down = int((s < 0).sum())
        flat = int(s.eq(0).sum())
        # 涨停/跌停估算 (≥9.5% / ≤-9.5%)
        zt = int((s >= 9.5).sum())
        dt = int((s <= -9.5).sum())
        total = up + down + flat
        result = {
            "rise_fall": {
                "up": up, "down": down, "flat": flat,
                "ratio": round(up / total, 4) if total > 0 else 0,
                "source": "sina_spot",
            },
            "limit": {
                "up_limit": zt, "down_limit": dt,
                "source": "sina_spot",
                "approx": True,  # 标记数值是估算,不含特殊涨跌停板规则
            },
        }
        cache.set("cockpit:sina_breadth", result, 60)
        return result
    except Exception as e:
        logger.warning(f"新浪全市场快照降级失败: {e}")
        return None


async def preload_breadth_fallback():
    """启动预热:后台拉一次新浪全市场快照,让首次访问无等待。失败静默。"""
    try:
        await asyncio.to_thread(_fetch_breadth_via_sina_spot)
        logger.info("涨跌家数(新浪)预热完成")
    except Exception as e:
        logger.warning(f"涨跌家数预热失败: {e}")


# ─── 情绪聚合 ───

async def get_sentiment() -> dict:
    """聚合市场情绪：涨跌家数、涨停/炸板、两市成交额、主力资金流向。

    数据源：
    - 涨跌/涨停/跌停: AKShare stock_market_activity_legu
    - 涨停池 + 炸板池: AKShare stock_zt_pool_em + stock_zt_pool_zbgc_em
    - 两市成交额: 腾讯指数行情（复用 get_indices_quotes 缓存）
    - 资金流向: AKShare stock_market_fund_flow
    """
    from datetime import date

    cache_key = "cockpit:sentiment"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        today_str = date.today().strftime("%Y%m%d")

        # 并行请求 AKShare 数据源
        results = await asyncio.gather(
            asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_market_activity_legu),
                timeout=_AKSHARE_TIMEOUT,
            ),
            asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_zt_pool_em, date=today_str),
                timeout=_AKSHARE_TIMEOUT,
            ),
            asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_zt_pool_zbgc_em, date=today_str),
                timeout=_AKSHARE_TIMEOUT,
            ),
            asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_market_fund_flow),
                timeout=_AKSHARE_TIMEOUT,
            ),
            return_exceptions=True,
        )

        rise_fall_data = None
        limit_data = None
        volume_data = None
        flow_data = None

        # 1. 解析市场活跃度（涨跌家数 + 涨停/跌停）
        df_activity = results[0]
        if not isinstance(df_activity, Exception) and df_activity is not None and hasattr(df_activity, "empty") and not df_activity.empty:
            try:
                items = dict(zip(df_activity["item"], df_activity["value"]))
                up = int(float(items.get("上涨", 0) or 0))
                down = int(float(items.get("下跌", 0) or 0))
                flat = int(float(items.get("平盘", 0) or 0))
                total = up + down + flat
                rise_fall_data = {
                    "up": up,
                    "down": down,
                    "flat": flat,
                    "ratio": round(up / total, 4) if total > 0 else 0,
                }
                up_limit = int(float(items.get("涨停", 0) or 0))
                down_limit = int(float(items.get("跌停", 0) or 0))
                limit_data = {"up_limit": up_limit, "down_limit": down_limit}
            except Exception as e:
                logger.warning(f"解析市场活跃度失败: {e}")
        else:
            logger.warning(f"市场活跃度获取失败: {results[0] if isinstance(results[0], Exception) else '空数据'}")

        # 1b. 东财失败时,经统一 DataRouter 降级(新浪全市场快照等)
        if rise_fall_data is None:
            try:
                from services.data_router import get_router
                fb_resp = await get_router().fetch(
                    "market_breadth",
                    cache_key="cockpit:breadth_via_router",
                    ttl=60,
                    timeout=30.0,
                    validate=lambda x: bool(x and x.get("rise_fall")),
                )
                fb = fb_resp.get("data") if fb_resp.get("success") else None
                if fb:
                    rise_fall_data = fb["rise_fall"]
                    if limit_data is None:
                        limit_data = fb["limit"]
                    else:
                        if not limit_data.get("up_limit"):
                            limit_data["up_limit"] = fb["limit"]["up_limit"]
                        if not limit_data.get("down_limit"):
                            limit_data["down_limit"] = fb["limit"]["down_limit"]
                        limit_data["approx"] = fb["limit"].get("approx", False)
                    logger.info(f"涨跌家数已降级,使用源: {fb_resp.get('source')}")
            except Exception as e:
                logger.warning(f"DataRouter 降级也失败: {e}")

        # 2. 解析涨停池（补充真实涨停数）
        df_zt = results[1]
        if not isinstance(df_zt, Exception) and df_zt is not None and hasattr(df_zt, "empty"):
            try:
                if limit_data is None:
                    limit_data = {"up_limit": 0, "down_limit": 0}
                limit_data["up_limit_pool"] = len(df_zt)
            except Exception as e:
                logger.warning(f"解析涨停池失败: {e}")

        # 3. 解析炸板池（炸板数量）
        df_zb = results[2]
        if not isinstance(df_zb, Exception) and df_zb is not None and hasattr(df_zb, "empty"):
            try:
                if limit_data is None:
                    limit_data = {"up_limit": 0, "down_limit": 0}
                limit_data["broken"] = len(df_zb)
            except Exception as e:
                logger.warning(f"解析炸板池失败: {e}")

        # 4. 解析资金流向
        df_flow = results[3]
        if not isinstance(df_flow, Exception) and df_flow is not None and hasattr(df_flow, "empty") and not df_flow.empty:
            try:
                row = df_flow.iloc[-1]
                main_net = _clean(row.get("主力净流入-净额") or 0)
                flow_data = {"main_net": main_net}
            except Exception as e:
                logger.warning(f"解析资金流向失败: {e}")
        else:
            logger.warning(f"资金流向获取失败: {results[3] if isinstance(results[3], Exception) else '空数据'}")

        # 5. 两市成交额：上证(000001) + 深证(399001, AUX) amount 求和（万元 → 元）
        try:
            indices = await get_indices_quotes()
            if indices.get("success"):
                total_yuan = 0
                # 实时:上证 amount + 深证 amount
                today_vol = 0  # 上证 volume(手),用于和日K对比放缩量
                for idx in indices["data"]:
                    if idx["code"] == "000001":
                        if idx.get("amount"):
                            total_yuan += idx["amount"] * 10000
                        if idx.get("volume"):
                            today_vol += idx["volume"]
                sz_amt = (indices.get("aux") or {}).get("399001")
                if sz_amt:
                    total_yuan += sz_amt * 10000
                sz_vol = (indices.get("aux") or {}).get("399001_volume")
                if sz_vol:
                    today_vol += sz_vol
                if total_yuan > 0:
                    volume_data = {"total_yuan": total_yuan}
                    # 取近 6 日两市成交量,算今日 vs 昨日 + 今日 vs 近5日均量
                    try:
                        prev_vol, avg5_vol = await _fetch_prev_index_volume()
                        if today_vol and prev_vol and prev_vol > 0:
                            volume_data["vs_prev_pct"] = round((today_vol - prev_vol) / prev_vol * 100, 1)
                        if today_vol and avg5_vol and avg5_vol > 0:
                            volume_data["vs_avg5_pct"] = round((today_vol - avg5_vol) / avg5_vol * 100, 1)
                    except Exception as e:
                        logger.debug(f"取昨日成交量失败: {e}")
        except Exception as e:
            logger.warning(f"获取两市成交额失败: {e}")

        resp = {
            "success": True,
            "data": {
                "rise_fall": rise_fall_data,
                "limit": limit_data,
                "volume": volume_data,
                "flow": flow_data,
            },
        }
        # 60s 缓存:新浪降级源全市场快照较慢(~8s),不能高频触发
        cache.set(cache_key, resp, 60)
        return resp

    except asyncio.TimeoutError:
        logger.warning("情绪聚合超时")
        return {"success": False, "error": "情绪数据获取超时"}
    except Exception as e:
        logger.error(f"情绪聚合失败: {e}")
        return {"success": False, "error": f"情绪数据获取失败: {e}"}


# ─── 指数实时报价 ───

async def _fetch_prev_index_volume() -> tuple[float | None, float | None]:
    """从腾讯日 K 取沪深两市近 6 个交易日成交量,返回 (昨日总量, 近5日均量)。

    腾讯日 K 字段: [日期, 开, 收, 高, 低, 成交量(手)]
    """
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,8,qfq"
        r1 = await get_async_client().get(url, timeout=6)
        sh = (r1.json().get("data") or {}).get("sh000001") or {}
        sh_days = sh.get("day") or sh.get("qfqday") or []

        url2 = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz399001,day,,,8,qfq"
        r2 = await get_async_client().get(url2, timeout=6)
        sz = (r2.json().get("data") or {}).get("sz399001") or {}
        sz_days = sz.get("day") or sz.get("qfqday") or []

        if not sh_days or not sz_days:
            return None, None

        # 两市同日成交量加和(取最近6日)
        sh_map = {d[0]: float(d[5]) for d in sh_days[-7:] if len(d) > 5}
        sz_map = {d[0]: float(d[5]) for d in sz_days[-7:] if len(d) > 5}
        common = sorted(set(sh_map) & set(sz_map))[-6:]
        if not common:
            return None, None
        totals = [sh_map[d] + sz_map[d] for d in common]
        # 昨日:最近一根
        prev_total = totals[-1]
        # 近5日均量:倒数第 2-6 根的均值,排除"最新"
        if len(totals) >= 6:
            avg5 = sum(totals[-6:-1]) / 5
        else:
            avg5 = sum(totals[:-1]) / max(1, len(totals) - 1) if len(totals) > 1 else prev_total
        return prev_total, avg5
    except Exception as e:
        logger.debug(f"取指数日K成交量失败: {e}")
        return None, None


async def get_indices_quotes() -> dict:
    """批量获取 6 个主要指数的实时行情。"""
    cache_key = "cockpit:indices"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        all_indices = INDICES + _AUX_INDICES
        codes = ",".join(idx["qt"] for idx in all_indices)
        url = f"https://qt.gtimg.cn/q={codes}"
        r = await get_async_client().get(url, timeout=10)
        text = r.content.decode("gbk", errors="replace")

        def _tf(fields, idx):
            """从腾讯 ~-分隔字段中安全提取浮点数。"""
            try:
                return float(fields[idx])
            except (IndexError, ValueError):
                return None

        data = []
        aux_data = {}
        lines = [line.strip() for line in text.strip().split(";") if line.strip()]
        i = 0
        for line in lines:
            start = line.find('"')
            end = line.rfind('"')
            if start == -1 or end <= start:
                continue
            fields = line[start + 1: end].split("~")
            if len(fields) < 38:
                continue

            if i < len(INDICES):
                data.append({
                    "code": INDICES[i]["code"],
                    "name": INDICES[i]["name"],
                    "price": _tf(fields, 3),
                    "prev_close": _tf(fields, 4),
                    "change": _tf(fields, 31),
                    "change_pct": _tf(fields, 32),
                    "volume": _tf(fields, 36),
                    "amount": _tf(fields, 37),
                })
            else:
                aux_idx = i - len(INDICES)
                if aux_idx < len(_AUX_INDICES):
                    code = _AUX_INDICES[aux_idx]["code"]
                    aux_data[code] = _tf(fields, 37)            # amount
                    aux_data[f"{code}_volume"] = _tf(fields, 36)  # volume
            i += 1

        resp = {"success": True, "data": data, "aux": aux_data}
        cache.set(cache_key, resp, 5)
        return resp

    except Exception as e:
        logger.error(f"获取指数报价失败: {e}")
        return {"success": False, "error": f"获取指数报价失败: {e}"}


# ─── 分时数据（腾讯分钟 API）───

async def get_tick_data(code: str) -> dict:
    """获取指数分时数据（1分钟线），通过腾讯分钟 API。"""
    cache_key = f"cockpit:tick:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # 查找指数信息
    idx_info = None
    for idx in INDICES:
        if idx["code"] == code or idx["qt"] == code:
            idx_info = idx
            break
    if idx_info is None:
        return {"success": False, "error": f"未知指数代码: {code}"}

    qt_code = idx_info["qt"]

    try:
        # 并行获取：腾讯分钟数据 + 腾讯昨收价
        async def _fetch_min():
            url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={qt_code}"
            r = await get_async_client().get(url, timeout=10)
            raw = r.content.decode("gbk", errors="replace")
            # 格式: min_data_xxx={"code":0,"data":{"xxx":{"data":{"data":["0930 price vol amount",...]}}}}
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return None
            payload = json.loads(match.group())
            if payload.get("code") != 0:
                return None
            node = payload.get("data", {}).get(qt_code, {}).get("data", {}).get("data", [])
            return node

        async def _fetch_prev_close():
            url = f"https://qt.gtimg.cn/q={qt_code}"
            r = await get_async_client().get(url, timeout=10)
            text = r.content.decode("gbk", errors="replace")
            start = text.find('"')
            end = text.rfind('"')
            if start == -1 or end <= start:
                return None
            fields = text[start + 1: end].split("~")
            try:
                return float(fields[4])
            except (IndexError, ValueError):
                return None

        min_raw, prev_close = await asyncio.gather(
            _fetch_min(), _fetch_prev_close(), return_exceptions=True,
        )

        # 处理昨收
        if isinstance(prev_close, Exception) or prev_close is None:
            prev_close = 0.0

        # 处理分时数据
        if isinstance(min_raw, Exception) or not min_raw:
            return {"success": False, "error": "分时数据为空"}

        tick_list = []
        prev_vol = 0
        for item in min_raw:
            parts = item.split()
            if len(parts) < 3:
                continue
            try:
                hhmm = parts[0]
                price = float(parts[1])
                cum_vol = float(parts[2])
                minute_vol = max(0, cum_vol - prev_vol)
                prev_vol = cum_vol
                tick_list.append({
                    "time": f"{hhmm[:2]}:{hhmm[2:]}",
                    "price": price,
                    "volume": minute_vol,
                })
            except (ValueError, IndexError):
                continue

        resp = {
            "success": True,
            "data": {
                "code": idx_info["code"],
                "name": idx_info["name"],
                "prev_close": prev_close,
                "data": tick_list,
            },
        }
        cache.set(cache_key, resp, 5)
        return resp

    except asyncio.TimeoutError:
        logger.warning(f"分时数据超时: {code}")
        return {"success": False, "error": "分时数据获取超时"}
    except Exception as e:
        logger.error(f"获取分时数据失败 {code}: {e}")
        return {"success": False, "error": f"获取分时数据失败: {e}"}


# ─── 美股指数列表 ───

US_INDICES = [
    {"code": "INX",  "name": "标普500",  "qt": "us.INX",  "yf": "^GSPC"},
    {"code": "IXIC", "name": "纳斯达克", "qt": "us.IXIC", "yf": "^IXIC"},
    {"code": "DJI",  "name": "道琼斯",   "qt": "us.DJI",  "yf": "^DJI"},
    {"code": "VIX",  "name": "VIX",      "qt": "us.VIX",  "yf": "^VIX"},
]


async def _fetch_us_index_yf(code: str) -> dict | None:
    """yfinance 兜底：腾讯无该指数数据时使用（如罗素2000）。"""
    idx_info = next((idx for idx in US_INDICES if idx["code"] == code), None)
    if idx_info is None or not idx_info.get("yf"):
        return None
    cache_key = f"cockpit:us:yf:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    def _do():
        try:
            import yfinance as yf
            fi = yf.Ticker(idx_info["yf"]).fast_info
            price = float(fi.last_price)
            prev_close = float(fi.previous_close)
            change = price - prev_close
            pct = (change / prev_close * 100) if prev_close else 0.0
            return {"price": price, "prev_close": prev_close, "change": change, "change_pct": pct}
        except Exception as e:
            logger.warning(f"yfinance 指数获取失败 {code}: {e}")
            return None

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_do), timeout=_AKSHARE_TIMEOUT)
    except asyncio.TimeoutError:
        return None
    if result is not None:
        cache.set(cache_key, result, 60)
    return result


async def get_us_sentiment() -> dict:
    """美股情绪：VIX水平 + 涨跌家数（AKShare stock_us_spot_em）。"""
    cache_key = "cockpit:us:sentiment"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async def _fetch_vix():
        try:
            url = "https://qt.gtimg.cn/q=us.VIX"
            r = await get_async_client().get(url, timeout=8)
            text = r.content.decode("gbk", errors="replace")
            start = text.find('"')
            end = text.rfind('"')
            if start == -1 or end <= start:
                return None
            fields = text[start + 1: end].split("~")
            try:
                return float(fields[3])
            except (IndexError, ValueError):
                return None
        except Exception as e:
            logger.warning(f"VIX 获取失败: {e}")
            return None

    async def _fetch_breadth_em():
        """直接调用东方财富分页接口，比 akshare 整表更稳定。"""
        client = get_async_client()
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        }
        up = down = flat = 0
        page_size = 500
        for pn in range(1, 40):  # 最多 40*500 = 20000 只
            url = (
                "https://72.push2.eastmoney.com/api/qt/clist/get"
                f"?pn={pn}&pz={page_size}&po=1&np=1&fltt=2&invt=2&fid=f3"
                "&fs=m:105,m:106,m:107&fields=f3"
            )
            diff = None
            for attempt in range(3):
                try:
                    r = await client.get(url, timeout=8, headers=headers)
                    diff = r.json().get("data", {}).get("diff") or []
                    break
                except Exception:
                    if attempt == 2:
                        return None
                    await asyncio.sleep(0.5)
            if not diff:
                break
            for it in diff:
                v = it.get("f3")
                if v is None or v == "-":
                    flat += 1
                elif isinstance(v, (int, float)):
                    if v > 0:
                        up += 1
                    elif v < 0:
                        down += 1
                    else:
                        flat += 1
                else:
                    flat += 1
            if len(diff) < page_size:
                break
        if up + down + flat == 0:
            return None
        return {"up": up, "down": down, "flat": flat}

    async def _fetch_breadth():
        try:
            return await asyncio.wait_for(_fetch_breadth_em(), timeout=20)
        except Exception as e:
            logger.warning(f"美股涨跌家数（东财）失败: {e}")
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(patch_requests, ak.stock_us_spot_em),
                timeout=_AKSHARE_TIMEOUT,
            )
            if df is None or df.empty:
                return None
            up = int((df["涨跌幅"] > 0).sum())
            down = int((df["涨跌幅"] < 0).sum())
            flat = int((df["涨跌幅"] == 0).sum())
            return {"up": up, "down": down, "flat": flat}
        except Exception as e:
            logger.warning(f"美股涨跌家数（AKShare）失败: {e}")
            return None

    vix, breadth = await asyncio.gather(_fetch_vix(), _fetch_breadth())

    resp = {"success": True, "data": {"vix": vix, "breadth": breadth}}
    cache.set(cache_key, resp, 30)
    return resp


async def get_us_indices_quotes() -> dict:
    """批量获取 5 个美股指数实时报价（腾讯 API）。"""
    cache_key = "cockpit:us:indices"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        codes = ",".join(idx["qt"] for idx in US_INDICES)
        url = f"https://qt.gtimg.cn/q={codes}"
        r = await get_async_client().get(url, timeout=10)
        text = r.content.decode("gbk", errors="replace")

        def _tf(fields, idx):
            try:
                return float(fields[idx])
            except (IndexError, ValueError):
                return None

        # 按变量名 v_us.XXX 映射，避免某些指数无返回时位移错位
        parsed: dict[str, list[str]] = {}
        for line in text.strip().split(";"):
            line = line.strip()
            m = re.match(r'v_(us\.[A-Z0-9]+)="([^"]*)"', line)
            if not m:
                continue
            parsed[m.group(1)] = m.group(2).split("~")

        data = []
        for idx in US_INDICES:
            fields = parsed.get(idx["qt"])
            if fields is None or len(fields) < 38 or not _tf(fields, 3):
                # 腾讯无数据：用 yfinance 兜底
                yf_data = await _fetch_us_index_yf(idx["code"])
                if yf_data is not None:
                    data.append({"code": idx["code"], "name": idx["name"], **yf_data})
                else:
                    data.append({
                        "code": idx["code"], "name": idx["name"],
                        "price": None, "prev_close": None, "change": None, "change_pct": None,
                    })
                continue
            data.append({
                "code": idx["code"],
                "name": idx["name"],
                "price": _tf(fields, 3),
                "prev_close": _tf(fields, 4),
                "change": _tf(fields, 31),
                "change_pct": _tf(fields, 32),
            })

        resp = {"success": True, "data": data}
        cache.set(cache_key, resp, 5)
        return resp

    except Exception as e:
        logger.error(f"获取美股指数报价失败: {e}")
        return {"success": False, "error": f"获取美股指数报价失败: {e}"}


async def get_us_tick_data(code: str) -> dict:
    """获取美股指数分时数据（腾讯美股分钟 API）。"""
    cache_key = f"cockpit:us:tick:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    idx_info = next((idx for idx in US_INDICES if idx["code"] == code), None)
    if idx_info is None:
        return {"success": False, "error": f"未知美股指数代码: {code}"}

    qt_code = idx_info["qt"]

    try:
        async def _fetch_min():
            url = f"https://web.ifzq.gtimg.cn/appstock/app/usMinute/query?code={qt_code}"
            r = await get_async_client().get(url, timeout=10)
            raw = r.content.decode("gbk", errors="replace")
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return None
            payload = json.loads(match.group())
            if payload.get("code") != 0:
                return None
            data_node = (
                payload.get("data", {}).get(qt_code, {}).get("data", {}).get("data", [])
                or payload.get("data", {}).get(qt_code.replace(".", ""), {}).get("data", {}).get("data", [])
            )
            return data_node

        async def _fetch_prev_close():
            url = f"https://qt.gtimg.cn/q={qt_code}"
            r = await get_async_client().get(url, timeout=8)
            text = r.content.decode("gbk", errors="replace")
            start = text.find('"')
            end = text.rfind('"')
            if start == -1 or end <= start:
                return None
            fields = text[start + 1: end].split("~")
            try:
                return float(fields[4])
            except (IndexError, ValueError):
                return None

        min_raw, prev_close = await asyncio.gather(
            _fetch_min(), _fetch_prev_close(), return_exceptions=True
        )
        if isinstance(prev_close, Exception) or prev_close is None:
            prev_close = 0.0

        if isinstance(min_raw, Exception) or not min_raw:
            # 休市时返回上一交易日缓存的分时数据
            last_key = f"cockpit:us:tick:last:{code}"
            last_data = cache.get(last_key)
            if last_data is not None:
                resp = {"success": True, "closed": True, "data": last_data}
                cache.set(cache_key, resp, 30)
                return resp
            resp = {
                "success": True,
                "closed": True,
                "data": {"code": idx_info["code"], "name": idx_info["name"], "prev_close": prev_close, "data": []},
            }
            cache.set(cache_key, resp, 30)
            return resp

        tick_list = []
        prev_vol = 0
        for item in min_raw:
            parts = item.split()
            if len(parts) < 3:
                continue
            try:
                hhmm = parts[0]
                price = float(parts[1])
                cum_vol = float(parts[2])
                minute_vol = max(0, cum_vol - prev_vol)
                prev_vol = cum_vol
                tick_list.append({"time": f"{hhmm[:2]}:{hhmm[2:]}", "price": price, "volume": minute_vol})
            except (ValueError, IndexError):
                continue

        resp = {
            "success": True,
            "closed": False,
            "data": {"code": idx_info["code"], "name": idx_info["name"], "prev_close": prev_close, "data": tick_list},
        }
        cache.set(cache_key, resp, 5)
        # 长期缓存，供休市时展示
        last_key = f"cockpit:us:tick:last:{code}"
        cache.set(last_key, resp["data"], 86400)
        return resp

    except asyncio.TimeoutError:
        return {"success": False, "error": "美股分时数据获取超时"}
    except Exception as e:
        logger.error(f"获取美股分时数据失败 {code}: {e}")
        return {"success": False, "error": f"获取美股分时数据失败: {e}"}
