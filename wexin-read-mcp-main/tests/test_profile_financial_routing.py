"""行为验证: 重构阶段 B —— stock_service.get_company_profile / get_financial 的
A 股分支由 source_racer 迁移到 DataRouter `company_profile` / `financial` 契约。

不打真实 API: 全程 mock `services.data_router.get_router().fetch` 以及 `cache`。
覆盖:
  ① 成功路径: fetch 返回 success+data+source(主源 id) -> 方法响应透传 source/success/data,
     且 fetch 以 cache_key=None, timeout=30.0, market="a", validate(bool语义) 调用。
  ② 全源失败: fetch 返回 success=False -> 方法恰好返回原有 "未找到..." 结构(不含 source 字段)。
  ③ 备源透传: fetch 返回 source=emweb_* -> 方法响应 source 字段透传为备源 id。

纯 stdlib(asyncio + unittest.mock),自行注入 src 到 sys.path。
运行:
    .venv/bin/python wexin-read-mcp-main/tests/test_profile_financial_routing.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest import mock

# 把 src 加入 sys.path
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import stock_service  # noqa: E402
import stock.fundamental as fundamental  # noqa: E402  实现已拆到 stock/ 子包,cache 在此读取
import services.data_router as data_router  # noqa: E402

# 被测方法在 StockService 实例上
SVC = stock_service.StockService()

# 样例数据(结构贴合各方法对 A 股的预期)
PROFILE_DATA = {
    "公司名称": "平安银行股份有限公司",
    "A股简称": "平安银行",
    "所属行业": "银行",
    "上市日期": "1991-04-03",
    "主营业务": "吸收公众存款；发放贷款……",
}
FINANCIAL_DATA = [
    {"报告期": "2024-12-31", "净利润": "445.08亿", "营业总收入": "1466.95亿",
     "基本每股收益": "2.15", "净资产收益率": "10.08%"},
    {"报告期": "2023-12-31", "净利润": "464.55亿", "营业总收入": "1646.99亿",
     "基本每股收益": "2.27", "净资产收益率": "11.38%"},
]


def _make_fetch(result: dict):
    """构造一个记录调用参数的异步 fetch 桩。"""
    calls = []

    async def _fetch(contract, *args, **kwargs):
        calls.append({"contract": contract, "args": args, "kwargs": kwargs})
        return result

    return _fetch, calls


def _patched_router_and_cache(fetch_stub):
    """同时 patch get_router().fetch(返回桩) 与 cache(get->None 强制未命中)。

    返回一个上下文管理器列表的合并 ExitStack 不好用 stdlib 简写,这里返回
    已进入的 patcher 句柄列表,调用方负责 stop。
    """
    fake_router = mock.Mock()
    fake_router.fetch = fetch_stub
    p_router = mock.patch.object(data_router, "get_router", return_value=fake_router)
    # stock_service 内是 `from services.data_router import get_router`,在方法体内 import,
    # 因此 patch data_router.get_router 即可被局部 import 获取到。
    # 实现已拆到 stock.fundamental,cache 在该模块命名空间读取,故 patch 目标随之迁移。
    p_cache = mock.patch.object(fundamental, "cache", mock.Mock(get=mock.Mock(return_value=None),
                                                                set=mock.Mock()))
    return p_router, p_cache


def _run_profile(fetch_result: dict, symbol: str = "000001"):
    fetch_stub, calls = _make_fetch(fetch_result)
    p_router, p_cache = _patched_router_and_cache(fetch_stub)
    with p_router, p_cache:
        resp = asyncio.run(SVC.get_company_profile(symbol))
    return resp, calls


def _run_financial(fetch_result: dict, symbol: str = "000001"):
    fetch_stub, calls = _make_fetch(fetch_result)
    p_router, p_cache = _patched_router_and_cache(fetch_stub)
    with p_router, p_cache:
        resp = asyncio.run(SVC.get_financial(symbol))
    return resp, calls


# ───────────────────────── company_profile ─────────────────────────

def test_profile_success_primary_source():
    """① 成功路径: 主源 akshare_cninfo 透传。"""
    resp, calls = _run_profile(
        {"success": True, "data": dict(PROFILE_DATA), "source": "akshare_cninfo"}
    )
    assert resp["success"] is True, f"期望 success=True, 实得 {resp!r}"
    assert resp["source"] == "akshare_cninfo", f"source 未透传: {resp!r}"
    # data 内容: 方法对每个值再跑 _clean,字符串值应原样保留
    assert resp["data"]["公司名称"] == PROFILE_DATA["公司名称"], f"data 内容错误: {resp!r}"
    assert resp["data"]["所属行业"] == "银行", f"data 内容错误: {resp!r}"
    # fetch 调用参数
    assert len(calls) == 1, f"fetch 应恰好被调 1 次, 实得 {len(calls)}"
    c = calls[0]
    assert c["contract"] == "company_profile", f"契约名错误: {c['contract']}"
    kw = c["kwargs"]
    assert kw.get("cache_key") is None, f"cache_key 应为 None: {kw}"
    assert kw.get("timeout") == 30.0, f"timeout 应为 30.0: {kw}"
    assert kw.get("market") == "a", f"market 应为 'a': {kw}"
    assert kw.get("symbol") == "000001", f"symbol 透传错误: {kw}"
    # validate 应为 bool 语义: 非空真, 空假
    v = kw.get("validate")
    assert callable(v), f"validate 应可调用: {kw}"
    assert bool(v({"x": 1})) is True and bool(v({})) is False and bool(v(None)) is False, \
        "validate 非 bool 语义"
    print("PASS [profile ① success akshare_cninfo]")


def test_profile_all_sources_fail():
    """② 全源失败: 恰好返回原有 '未找到公司信息' 结构,无 source/data 键。"""
    resp, calls = _run_profile({"success": False, "data": None, "source": None})
    assert resp == {"success": False, "error": "未找到公司信息"}, \
        f"失败结构不一致, 实得: {resp!r}"
    print("PASS [profile ② all-fail -> 未找到公司信息]")


def test_profile_success_backup_source():
    """③ 备源 emweb_profile 透传。"""
    resp, calls = _run_profile(
        {"success": True, "data": dict(PROFILE_DATA), "source": "emweb_profile"}
    )
    assert resp["success"] is True
    assert resp["source"] == "emweb_profile", f"备源 source 未透传: {resp!r}"
    print("PASS [profile ③ backup emweb_profile]")


# ───────────────────────── financial ─────────────────────────

def test_financial_success_primary_source():
    """① 成功路径: 主源 akshare_ths 透传。"""
    resp, calls = _run_financial(
        {"success": True, "data": [dict(r) for r in FINANCIAL_DATA], "source": "akshare_ths"}
    )
    assert resp["success"] is True, f"期望 success=True, 实得 {resp!r}"
    assert resp["source"] == "akshare_ths", f"source 未透传: {resp!r}"
    assert isinstance(resp["data"], list) and len(resp["data"]) == 2, f"data 错误: {resp!r}"
    assert resp["data"][0]["报告期"] == "2024-12-31", f"data 内容错误: {resp!r}"
    assert len(calls) == 1, f"fetch 应恰好被调 1 次, 实得 {len(calls)}"
    c = calls[0]
    assert c["contract"] == "financial", f"契约名错误: {c['contract']}"
    kw = c["kwargs"]
    assert kw.get("cache_key") is None, f"cache_key 应为 None: {kw}"
    assert kw.get("timeout") == 30.0, f"timeout 应为 30.0: {kw}"
    assert kw.get("market") == "a", f"market 应为 'a': {kw}"
    assert kw.get("symbol") == "000001", f"symbol 透传错误: {kw}"
    v = kw.get("validate")
    assert callable(v)
    assert bool(v([{"x": 1}])) is True and bool(v([])) is False and bool(v(None)) is False, \
        "validate 非 bool 语义"
    print("PASS [financial ① success akshare_ths]")


def test_financial_all_sources_fail():
    """② 全源失败: 恰好返回原有 '未找到财务数据' 结构。"""
    resp, calls = _run_financial({"success": False, "data": None, "source": None})
    assert resp == {"success": False, "error": "未找到财务数据"}, \
        f"失败结构不一致, 实得: {resp!r}"
    print("PASS [financial ② all-fail -> 未找到财务数据]")


def test_financial_success_backup_source():
    """③ 备源 emweb_financial 透传。"""
    resp, calls = _run_financial(
        {"success": True, "data": [dict(r) for r in FINANCIAL_DATA], "source": "emweb_financial"}
    )
    assert resp["success"] is True
    assert resp["source"] == "emweb_financial", f"备源 source 未透传: {resp!r}"
    print("PASS [financial ③ backup emweb_financial]")


if __name__ == "__main__":
    tests = [
        test_profile_success_primary_source,
        test_profile_all_sources_fail,
        test_profile_success_backup_source,
        test_financial_success_primary_source,
        test_financial_all_sources_fail,
        test_financial_success_backup_source,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    if failures:
        print(f"\n{failures} 个用例失败")
        sys.exit(1)
    print("\n全部用例通过")
