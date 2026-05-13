"""mootdx K 线数据源 — TDX 协议 TCP 直连（7709 端口），典型延迟 ~50ms。"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("mootdx-provider")

_FREQ_MAP = {
    "day": 9, "week": 5, "month": 6,
    "60min": 3, "30min": 2, "15min": 1, "5min": 0,
}


def _market(symbol: str) -> int:
    """1=上交所, 0=深交所"""
    return 1 if symbol.startswith(("6", "9")) else 0


def _fetch_sync(symbol: str, period: str, count: int) -> list[dict] | None:
    """同步获取 K 线，通过 TCP 连接 TDX 行情服务器。"""
    freq = _FREQ_MAP.get(period)
    if freq is None:
        return None

    try:
        from mootdx.quotes import Quotes
        from mootdx import HQ_HOSTS
        # 使用第一个可用服务器
        name, ip, port = HQ_HOSTS[0]
        client = Quotes.factory(market="std", server=(ip, port))
        df = client.bars(symbol=symbol, frequency=freq, start=0, offset=count)
        if df is None or df.empty:
            return None

        records = []
        for _, row in df.iterrows():
            dt = str(row.get("datetime", ""))
            # mootdx datetime: "YYYY-MM-DD HH:MM" -> 取日期部分
            date_str = dt[:10] if dt else ""
            records.append({
                "date": date_str,
                "open": float(row["open"]) if row.get("open") is not None else None,
                "close": float(row["close"]) if row.get("close") is not None else None,
                "high": float(row["high"]) if row.get("high") is not None else None,
                "low": float(row["low"]) if row.get("low") is not None else None,
                "volume": float(row["volume"]) if row.get("volume") is not None else None,
            })
        return records if records else None
    except Exception as e:
        logger.warning(f"mootdx TCP 获取失败 {symbol}: {e}")
        return None


async def fetch_mootdx_kline(symbol: str, period: str = "day", count: int = 120) -> list[dict] | None:
    """异步获取 mootdx K 线，超时 8s。"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_sync, symbol, period, count),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"mootdx 超时 {symbol} {period}")
        return None
    except Exception as e:
        logger.warning(f"mootdx 错误 {symbol} {period}: {e}")
        return None
