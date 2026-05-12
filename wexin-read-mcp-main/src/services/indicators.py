"""技术指标计算 — 纯 NumPy 实现，不依赖 talib。"""

import numpy as np


def calc_rsi(close: list[float], period: int = 14) -> list[float]:
    """RSI 相对强弱指标，返回与输入等长的 list（前期填 None）。"""
    n = len(close)
    if n < period + 1:
        return [None] * n

    close_arr = np.array(close, dtype=float)
    delta = np.diff(close_arr, prepend=close_arr[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.full(n, np.nan)
    avg_loss = np.full(n, np.nan)
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()

    for i in range(period + 1, n):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period

    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.inf), where=(avg_loss != 0))
    rsi = np.where(avg_loss == 0, 100.0, 100.0 - (100.0 / (1.0 + rs)))

    result = [None] * period + rsi[period:].tolist()
    return result


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均，自适应有效数据量（DIF 可能带前导 NaN）。"""
    alpha = 2.0 / (period + 1)
    result = np.full(len(data), np.nan)
    mask = ~np.isnan(data)
    valid_idx = np.where(mask)[0]
    if len(valid_idx) < 2:
        return result
    actual_period = min(period, len(valid_idx))
    seed_idx = valid_idx[actual_period - 1]
    result[seed_idx] = data[valid_idx[:actual_period]].mean()
    for i in range(actual_period, len(valid_idx)):
        idx = valid_idx[i]
        prev = valid_idx[i - 1]
        result[idx] = alpha * data[idx] + (1 - alpha) * result[prev]
    return result


def calc_macd(close: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD 指标。返回 {dif, dea, macd}，各为等长 list（前期填 None）。"""
    n = len(close)
    if n < slow:
        return {"dif": [None]*n, "dea": [None]*n, "macd": [None]*n}

    close_arr = np.array(close, dtype=float)

    ema_fast = _ema(close_arr, fast)
    ema_slow = _ema(close_arr, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)
    macd = 2.0 * (dif - dea)

    mask = ~np.isnan(dif) & ~np.isnan(dea) & ~np.isnan(macd)
    start = int(np.argmax(mask)) if mask.any() else n
    return {
        "dif": [None]*start + dif[start:].tolist(),
        "dea": [None]*start + dea[start:].tolist(),
        "macd": [None]*start + macd[start:].tolist(),
    }


def calc_kdj(high: list[float], low: list[float], close: list[float], n: int = 9) -> dict:
    """KDJ 指标。返回 {k, d, j}，各为等长 list。"""
    length = len(close)
    if length < n:
        return {"k": [None]*length, "d": [None]*length, "j": [None]*length}

    high_arr = np.array(high, dtype=float)
    low_arr = np.array(low, dtype=float)
    close_arr = np.array(close, dtype=float)

    k = np.full(length, np.nan)
    d = np.full(length, np.nan)
    j = np.full(length, np.nan)

    for t in range(n - 1, length):
        hh = high_arr[t - n + 1 : t + 1].max()
        ll = low_arr[t - n + 1 : t + 1].min()
        rsv = (close_arr[t] - ll) / (hh - ll + 1e-10) * 100.0
        if t == n - 1:
            k[t] = 50.0
            d[t] = 50.0
        else:
            k[t] = 2.0 / 3.0 * k[t - 1] + 1.0 / 3.0 * rsv
            d[t] = 2.0 / 3.0 * d[t - 1] + 1.0 / 3.0 * k[t]
        j[t] = 3.0 * k[t] - 2.0 * d[t]

    return {
        "k": [None]*(n-1) + k[n-1:].tolist(),
        "d": [None]*(n-1) + d[n-1:].tolist(),
        "j": [None]*(n-1) + j[n-1:].tolist(),
    }


def calc_boll(close: list[float], period: int = 20, std: int = 2) -> dict:
    """布林带。返回 {mid, upper, lower}，各为等长 list。"""
    n = len(close)
    if n < period:
        return {"mid": [None]*n, "upper": [None]*n, "lower": [None]*n}

    close_arr = np.array(close, dtype=float)
    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = close_arr[i-period+1:i+1]
        m = window.mean()
        s = window.std(ddof=1)
        mid[i] = m
        upper[i] = m + std * s
        lower[i] = m - std * s

    return {
        "mid": [None]*(period-1) + mid[period-1:].tolist(),
        "upper": [None]*(period-1) + upper[period-1:].tolist(),
        "lower": [None]*(period-1) + lower[period-1:].tolist(),
    }


# ── 形态识别 ─────────────────────────────────────────────────────────────────

def _find_peaks(arr: np.ndarray, min_distance: int = 5) -> list[int]:
    """返回局部极大值索引（窗口半径 min_distance）。"""
    peaks = []
    n = len(arr)
    for i in range(min_distance, n - min_distance):
        if np.isnan(arr[i]):
            continue
        window = arr[max(0, i - min_distance): i + min_distance + 1]
        if not np.any(np.isnan(window)) and arr[i] == window.max():
            peaks.append(i)
    return peaks


def _find_troughs(arr: np.ndarray, min_distance: int = 5) -> list[int]:
    """返回局部极小值索引（窗口半径 min_distance）。"""
    troughs = []
    n = len(arr)
    for i in range(min_distance, n - min_distance):
        if np.isnan(arr[i]):
            continue
        window = arr[max(0, i - min_distance): i + min_distance + 1]
        if not np.any(np.isnan(window)) and arr[i] == window.min():
            troughs.append(i)
    return troughs


def detect_candle_patterns(
    dates: list[str],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[dict]:
    """检测蜡烛图形态，返回 LightweightCharts setMarkers() 兼容的 list。

    支持：十字星、孕线、锤头、射击之星、多头吞噬、空头吞噬。
    """
    n = len(closes)
    if n < 2:
        return []

    o = np.array(opens,  dtype=float)
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)

    body       = np.abs(c - o)
    full_range = h - l
    eps        = 1e-10

    results: list[dict] = []

    for i in range(1, n):
        body_i  = body[i]
        range_i = full_range[i]
        o_i, h_i, l_i, c_i = o[i], h[i], l[i], c[i]

        upper_shadow = h_i - max(o_i, c_i)
        lower_shadow = min(o_i, c_i) - l_i

        # 十字星 Doji
        if range_i > eps and body_i / (range_i + eps) < 0.1:
            results.append({
                "time": dates[i],
                "position": "belowBar",
                "color": "#f59e0b",
                "shape": "circle",
                "text": "十",
                "pattern_type": "doji",
            })
            continue  # 不叠加其他形态

        # 孕线 Harami（前根为长实体）
        prev_body = body[i - 1]
        if prev_body > 0.01 * closes[i - 1]:
            prev_top = max(o[i-1], c[i-1])
            prev_bot = min(o[i-1], c[i-1])
            curr_top = max(o_i, c_i)
            curr_bot = min(o_i, c_i)
            if curr_top < prev_top and curr_bot > prev_bot:
                results.append({
                    "time": dates[i],
                    "position": "aboveBar",
                    "color": "#8b5cf6",
                    "shape": "circle",
                    "text": "孕",
                    "pattern_type": "harami",
                })
                continue

        # 多头吞噬 Bullish Engulfing
        if (c[i-1] < o[i-1] and c_i > o_i and
                c_i > o[i-1] and o_i < c[i-1]):
            results.append({
                "time": dates[i],
                "position": "belowBar",
                "color": "#22c55e",
                "shape": "arrowUp",
                "text": "多吞",
                "pattern_type": "bullish_engulf",
            })
            continue

        # 空头吞噬 Bearish Engulfing
        if (c[i-1] > o[i-1] and c_i < o_i and
                c_i < o[i-1] and o_i > c[i-1]):
            results.append({
                "time": dates[i],
                "position": "aboveBar",
                "color": "#ef4444",
                "shape": "arrowDown",
                "text": "空吞",
                "pattern_type": "bearish_engulf",
            })
            continue

        # 锤头 Hammer（下影线 ≥ 2×实体，上影线 < 实体）
        if body_i > eps and lower_shadow >= 2 * body_i and upper_shadow < body_i:
            results.append({
                "time": dates[i],
                "position": "belowBar",
                "color": "#22c55e",
                "shape": "arrowUp",
                "text": "锤",
                "pattern_type": "hammer",
            })
            continue

        # 射击之星 Shooting Star（上影线 ≥ 2×实体，下影线 < 实体）
        if body_i > eps and upper_shadow >= 2 * body_i and lower_shadow < body_i:
            results.append({
                "time": dates[i],
                "position": "aboveBar",
                "color": "#ef4444",
                "shape": "arrowDown",
                "text": "星",
                "pattern_type": "shooting_star",
            })

    return results


def detect_macd_signals(
    dates: list[str],
    closes: list[float],
    macd_data: dict,
) -> list[dict]:
    """检测 MACD 金叉/死叉/顶背离/底背离。

    macd_data 格式：{"dif": [...], "dea": [...], "macd": [...]}（与 calc_macd 一致）。
    """
    n = len(dates)
    dif_raw = macd_data.get("dif", [])
    dea_raw = macd_data.get("dea", [])

    if len(dif_raw) != n or len(dea_raw) != n:
        return []

    dif = np.array([v if v is not None else np.nan for v in dif_raw], dtype=float)
    dea = np.array([v if v is not None else np.nan for v in dea_raw], dtype=float)
    cls = np.array(closes, dtype=float)

    results: list[dict] = []

    # 金叉 / 死叉
    for i in range(1, n):
        if np.isnan(dif[i]) or np.isnan(dea[i]) or np.isnan(dif[i-1]) or np.isnan(dea[i-1]):
            continue
        # 金叉：DIF 从下穿上
        if dif[i-1] < dea[i-1] and dif[i] >= dea[i]:
            results.append({
                "time": dates[i],
                "position": "belowBar",
                "color": "#22c55e",
                "shape": "arrowUp",
                "text": "金叉",
                "pattern_type": "macd_golden",
            })
        # 死叉：DIF 从上穿下
        elif dif[i-1] > dea[i-1] and dif[i] <= dea[i]:
            results.append({
                "time": dates[i],
                "position": "aboveBar",
                "color": "#ef4444",
                "shape": "arrowDown",
                "text": "死叉",
                "pattern_type": "macd_death",
            })

    # 顶背离 / 底背离（基于局部高低点）
    valid_mask = ~np.isnan(dif)
    if valid_mask.sum() >= 10:
        # 顶背离：价格创新高，但 DIF 未创新高
        price_peaks = _find_peaks(cls, min_distance=5)
        dif_at_peaks = dif[price_peaks] if price_peaks else np.array([])
        for idx in range(1, len(price_peaks)):
            pi, pj = price_peaks[idx - 1], price_peaks[idx]
            if np.isnan(dif[pi]) or np.isnan(dif[pj]):
                continue
            if cls[pj] > cls[pi] and dif[pj] < dif[pi]:
                results.append({
                    "time": dates[pj],
                    "position": "aboveBar",
                    "color": "#f43f5e",
                    "shape": "arrowDown",
                    "text": "顶背离",
                    "pattern_type": "macd_bearish_div",
                })

        # 底背离：价格创新低，但 DIF 未创新低
        price_troughs = _find_troughs(cls, min_distance=5)
        for idx in range(1, len(price_troughs)):
            ti, tj = price_troughs[idx - 1], price_troughs[idx]
            if np.isnan(dif[ti]) or np.isnan(dif[tj]):
                continue
            if cls[tj] < cls[ti] and dif[tj] > dif[ti]:
                results.append({
                    "time": dates[tj],
                    "position": "belowBar",
                    "color": "#06b6d4",
                    "shape": "arrowUp",
                    "text": "底背离",
                    "pattern_type": "macd_bullish_div",
                })

    return results
