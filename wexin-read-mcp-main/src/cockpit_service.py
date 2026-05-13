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
                for idx in indices["data"]:
                    if idx["code"] in ("000001",) and idx.get("amount"):
                        total_yuan += idx["amount"] * 10000
                sz_amt = (indices.get("aux") or {}).get("399001")
                if sz_amt:
                    total_yuan += sz_amt * 10000
                if total_yuan > 0:
                    volume_data = {"total_yuan": total_yuan}
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
        cache.set(cache_key, resp, 15)
        return resp

    except asyncio.TimeoutError:
        logger.warning("情绪聚合超时")
        return {"success": False, "error": "情绪数据获取超时"}
    except Exception as e:
        logger.error(f"情绪聚合失败: {e}")
        return {"success": False, "error": f"情绪数据获取失败: {e}"}


# ─── 指数实时报价 ───

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
                    aux_data[_AUX_INDICES[aux_idx]["code"]] = _tf(fields, 37)
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
