"""腾讯行情 API 的零依赖解析层。

下沉自 stock_service.py，斩断 providers ↔ stock_service 的循环依赖。
仅依赖 stdlib，逻辑/常量值与原实现一字不改。
"""

from __future__ import annotations


# ─── 腾讯行情 API ───

_QT_URL = "https://qt.gtimg.cn/q={exchange}{code}"
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
