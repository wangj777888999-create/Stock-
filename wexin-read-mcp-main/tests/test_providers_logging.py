"""行为验证: providers.py 的 P1 改动 (静默 except -> except + logger.debug).

断言: 当 provider 内部依赖抛异常时, provider 函数:
  (1) 返回 None (控制流/返回语义不变)
  (2) 在 "providers" logger 上以 DEBUG 级别记录恰好 1 条含该源标识的日志

不依赖 pytest, 用 stdlib (asyncio + unittest.mock + logging handler) 即可运行:
    .venv/bin/python wexin-read-mcp-main/tests/test_providers_logging.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from unittest import mock

# 把 src 加入 sys.path, 使 `import services.providers` 可用
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import services.providers as providers  # noqa: E402


class _CapturingHandler(logging.Handler):
    """捕获 'providers' logger 在 DEBUG 级别发出的全部记录。"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


class _RaisingClient:
    """桩 async client: .get 一被调用就抛异常, 模拟取数失败。"""

    async def get(self, *a, **k):
        raise RuntimeError("boom-from-stub")


def _run_provider_expect_none(coro_factory, marker: str) -> None:
    """运行一个 provider, 断言返回 None 且记录了 1 条含 marker 的 DEBUG 日志。"""
    logger = logging.getLogger("providers")
    handler = _CapturingHandler()
    prev_level = logger.level
    prev_propagate = logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        # 把 providers 模块内引用的 get_async_client 替换成返回抛异常 client 的桩
        with mock.patch.object(providers, "get_async_client",
                               return_value=_RaisingClient()):
            result = asyncio.run(coro_factory())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.propagate = prev_propagate

    # (1) 返回值语义: None
    assert result is None, f"[{marker}] 期望返回 None, 实得: {result!r}"

    # (2) DEBUG 日志: 恰好 1 条, 含源标识
    debug_recs = [r for r in handler.records if r.levelno == logging.DEBUG]
    matching = [r for r in debug_recs if marker in r.getMessage()]
    assert len(matching) == 1, (
        f"[{marker}] 期望恰好 1 条含 '{marker}' 的 DEBUG 日志, "
        f"实得 {len(matching)} 条; 全部 DEBUG 记录: "
        f"{[r.getMessage() for r in debug_recs]}"
    )
    # 异常对象确实被串进了日志 (%s 格式化)
    assert "boom-from-stub" in matching[0].getMessage(), (
        f"[{marker}] 日志未包含底层异常文本, 实得: {matching[0].getMessage()!r}"
    )
    print(f"PASS [{marker}] -> 返回 None, 记录 1 条 DEBUG: {matching[0].getMessage()!r}")


def test_quote_tencent_logs_and_returns_none():
    _run_provider_expect_none(
        lambda: providers._quote_tencent(symbol="000001", market="a", original="000001"),
        marker="stock_quote tencent",
    )


def test_flow_em_direct_logs_and_returns_none():
    _run_provider_expect_none(
        lambda: providers._flow_em_direct(symbol="000001", exchange="sz"),
        marker="money_flow em_direct",
    )


def test_no_log_when_level_above_debug():
    """对照: 当 logger 级别高于 DEBUG 时不应有可见记录(仅证明用的是 debug 级)。"""
    logger = logging.getLogger("providers")
    handler = _CapturingHandler()
    handler.setLevel(logging.INFO)  # 只收 INFO 及以上
    prev_level = logger.level
    prev_propagate = logger.propagate
    logger.setLevel(logging.DEBUG)  # logger 放行 DEBUG, 但 handler 过滤掉
    logger.propagate = False
    logger.addHandler(handler)
    try:
        with mock.patch.object(providers, "get_async_client",
                               return_value=_RaisingClient()):
            result = asyncio.run(
                providers._quote_tencent(symbol="000001", market="a", original="000001")
            )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.propagate = prev_propagate

    assert result is None
    info_plus = [r for r in handler.records if r.levelno >= logging.INFO]
    assert not info_plus, f"改动应只用 DEBUG 级, 却出现 INFO+: {[r.getMessage() for r in info_plus]}"
    print("PASS [debug-level-only] -> 异常路径未产生 INFO+ 日志(确认是 debug 级)")


if __name__ == "__main__":
    failures = 0
    for fn in (
        test_quote_tencent_logs_and_returns_none,
        test_flow_em_direct_logs_and_returns_none,
        test_no_log_when_level_above_debug,
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
