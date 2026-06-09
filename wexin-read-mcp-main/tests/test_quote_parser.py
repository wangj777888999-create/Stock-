"""行为验证: 阶段 A —— 把 _QT_URL / _parse_tencent_quote 从 stock_service.py
下沉到 services/quote_parser.py 后, 行为/常量逐字不变。

断言:
  (1) 同源: providers / stock_service 引用的函数与 quote_parser 是同一对象。
  (2) 常量值不变: _QT_URL == "https://qt.gtimg.cn/q={exchange}{code}"。
  (3) 合法腾讯报价文本 -> 解析出名称/最新价等关键字段为合理值, 不抛异常。
  (4) 非法/过短输入 -> 按函数实际容错语义返回 None (字段数 < 48 或 fields[3] 为空)。

不依赖 pytest, 纯 stdlib, 仿 test_providers_logging.py 注入 src 到 sys.path:
    .venv/bin/python wexin-read-mcp-main/tests/test_quote_parser.py
"""
from __future__ import annotations

import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import services.quote_parser as qp  # noqa: E402
import services.providers as providers  # noqa: E402
import stock_service  # noqa: E402


def _make_a_quote_text() -> str:
    """构造一段真实格式的 A 股腾讯报价文本 (~ 分隔, >=48 字段)。
    字段索引参考 quote_parser 注释:
      1=名称 3=最新价 4=昨收 5=今开 31=涨跌额 32=涨跌幅
      33=最高 34=最低 36=成交量 37=成交额 38=换手率
      39=市盈率 43=振幅 44=总市值 45=流通市值 46=市净率
    """
    fields = [""] * 50
    fields[0] = "51"
    fields[1] = "平安银行"
    fields[2] = "000001"
    fields[3] = "11.50"   # 最新价
    fields[4] = "11.30"   # 昨收
    fields[5] = "11.35"   # 今开
    fields[31] = "0.20"   # 涨跌额
    fields[32] = "1.77"   # 涨跌幅
    fields[33] = "11.60"  # 最高
    fields[34] = "11.28"  # 最低
    fields[36] = "850000" # 成交量(手)
    fields[37] = "97500"  # 成交额(万)
    fields[38] = "0.44"   # 换手率
    fields[39] = "5.20"   # 市盈率
    fields[43] = "2.83"   # 振幅
    fields[44] = "2230.0" # 总市值(亿)
    fields[45] = "2230.0" # 流通市值(亿)
    fields[46] = "0.55"   # 市净率
    body = "~".join(fields)
    return f'v_sz000001="{body}";'


def test_same_object_identity():
    assert providers._parse_tencent_quote is qp._parse_tencent_quote, \
        "providers._parse_tencent_quote 与 quote_parser 不同源"
    assert stock_service._parse_tencent_quote is qp._parse_tencent_quote, \
        "stock_service._parse_tencent_quote 与 quote_parser 不同源"
    # _QT_URL 是不可变字符串, 用值相等断言 facade 透传一致
    assert providers._QT_URL == qp._QT_URL, "providers._QT_URL 与 quote_parser 值不一致"
    assert stock_service._QT_URL == qp._QT_URL, "stock_service._QT_URL 与 quote_parser 值不一致"
    print("PASS [identity] providers/stock_service._parse_tencent_quote 与 quote_parser 同源; _QT_URL 值一致")


def test_qt_url_constant_value():
    assert qp._QT_URL == "https://qt.gtimg.cn/q={exchange}{code}", \
        f"_QT_URL 值被改动: {qp._QT_URL!r}"
    print(f"PASS [const] _QT_URL == {qp._QT_URL!r}")


def test_parse_valid_a_quote():
    text = _make_a_quote_text()
    rec = qp._parse_tencent_quote(text, "000001")
    assert rec is not None, "合法报价应解析成功, 实得 None"
    assert rec["名称"] == "平安银行", f"名称错误: {rec['名称']!r}"
    assert rec["代码"] == "000001", f"代码错误: {rec['代码']!r}"
    assert rec["最新价"] == 11.50, f"最新价错误: {rec['最新价']!r}"
    assert rec["昨收"] == 11.30, f"昨收错误: {rec['昨收']!r}"
    assert rec["最高"] == 11.60 and rec["最低"] == 11.28, "最高/最低错误"
    assert rec["涨跌幅"] == 1.77, f"涨跌幅错误: {rec['涨跌幅']!r}"
    assert rec["市净率"] == 0.55, f"市净率错误: {rec['市净率']!r}"
    print(f"PASS [valid] 解析出 名称={rec['名称']} 最新价={rec['最新价']} 涨跌幅={rec['涨跌幅']} 市净率={rec['市净率']}")


def test_parse_invalid_inputs_return_none():
    # 无引号 -> start==-1 -> None
    assert qp._parse_tencent_quote("no quotes here", "x") is None, "无引号串应返回 None"
    # 有引号但字段过短 (<48) -> None
    short = 'v_sz000001="51~平安银行~000001~11.5";'
    assert qp._parse_tencent_quote(short, "000001") is None, "字段过短应返回 None"
    # 空引号内容 -> 单字段, <48 -> None
    assert qp._parse_tencent_quote('v_x="";', "x") is None, "空内容应返回 None"
    # 字段够长但 fields[3] (最新价) 为空 -> None
    f = [""] * 50
    f[1] = "某股"
    f[3] = ""  # 关键: fields[3] 为空触发 None
    blank_price = 'v_x="' + "~".join(f) + '";'
    assert qp._parse_tencent_quote(blank_price, "x") is None, "fields[3] 为空应返回 None"
    print("PASS [invalid] 无引号/过短/空内容/最新价为空 均返回 None (与函数容错语义一致)")


if __name__ == "__main__":
    failures = 0
    for fn in (
        test_same_object_identity,
        test_qt_url_constant_value,
        test_parse_valid_a_quote,
        test_parse_invalid_inputs_return_none,
    ):
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failures:
        print(f"\n{failures} 个用例失败")
        sys.exit(1)
    print("\n全部用例通过")
