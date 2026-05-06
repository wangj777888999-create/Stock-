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

    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    result = [None] * period + rsi[period:].tolist()
    return result


def calc_macd(close: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD 指标。返回 {dif, dea, macd}，各为等长 list（前期填 None）。"""
    n = len(close)
    if n < slow:
        return {"dif": [None]*n, "dea": [None]*n, "macd": [None]*n}

    close_arr = np.array(close, dtype=float)

    def ema(data, period):
        alpha = 2.0 / (period + 1)
        result = np.full(len(data), np.nan)
        result[period-1] = data[:period].mean()
        for i in range(period, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
        return result

    ema_fast = ema(close_arr, fast)
    ema_slow = ema(close_arr, slow)
    dif = ema_fast - ema_slow
    dea = ema(dif, signal)
    macd = 2.0 * (dif - dea)

    start = slow + signal - 2
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

    k[n-1] = 50.0
    d[n-1] = 50.0

    for t in range(n, length):
        hh = high_arr[t-n+1:t+1].max()
        ll = low_arr[t-n+1:t+1].min()
        rsv = (close_arr[t] - ll) / (hh - ll + 1e-10) * 100.0
        k[t] = 2.0/3.0 * k[t-1] + 1.0/3.0 * rsv
        d[t] = 2.0/3.0 * d[t-1] + 1.0/3.0 * k[t]
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
