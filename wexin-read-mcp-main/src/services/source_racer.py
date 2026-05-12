"""
多源竞速工具：并发启动所有数据源，取最先成功返回的结果。

工作原理：
- 所有源同时启动（asyncio.create_task），不等待上一个完成
- 第一个通过校验的结果立即返回，其余任务取消
- 用 EWMA 记录各源历史延迟，按历史快慢排序（快的先获得 event loop 调度权）
- 失败/超时的源会记录一个惩罚延迟，下次被排到后面
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger("source-racer")

_ALPHA = 0.3  # EWMA 平滑系数：0.3 表示近期结果权重 30%
_PENALTY_MULTIPLIER = 3.0  # 失败时记为 timeout * 此系数，作为惩罚
_latency: dict[str, float] = {}  # source_id → EWMA 延迟（秒）


def record_latency(source_id: str, elapsed: float) -> None:
    if source_id not in _latency:
        _latency[source_id] = elapsed
    else:
        _latency[source_id] = _ALPHA * elapsed + (1 - _ALPHA) * _latency[source_id]


def get_source_stats() -> dict[str, float]:
    """返回所有源的 EWMA 延迟（秒），按延迟升序排列。"""
    return dict(sorted(_latency.items(), key=lambda x: x[1]))


async def race_sources(
    sources: list[tuple[str, Callable[[], Awaitable[Any]]]],
    timeout: float = 15.0,
    validate: Callable[[Any], bool] | None = None,
) -> tuple[str | None, Any]:
    """
    并发启动所有源，返回最先成功的 (source_id, result)。

    Args:
        sources:  [(source_id, async_callable), ...] — 顺序无关，内部按历史延迟重排
        timeout:  整体超时（秒），超时后取消所有剩余任务
        validate: 可选结果校验函数；返回 False 视为无效，继续等其他源

    Returns:
        (winner_source_id, result)，全部失败时返回 (None, None)
    """
    if not sources:
        return None, None

    validate = validate or (lambda r: r is not None)

    # 按历史延迟排序：快的源先被 event loop 调度到
    ordered = sorted(sources, key=lambda s: _latency.get(s[0], float("inf")))

    async def _timed(sid: str, fn: Callable[[], Awaitable[Any]]):
        t = time.monotonic()
        try:
            result = await fn()
            return sid, time.monotonic() - t, result
        except Exception as exc:
            logger.debug(f"[race] {sid} 异常 ({time.monotonic()-t:.2f}s): {exc}")
            return sid, time.monotonic() - t, None

    tasks: dict[asyncio.Task, str] = {
        asyncio.create_task(_timed(sid, fn)): sid for sid, fn in ordered
    }
    pending = set(tasks)
    winner_sid: str | None = None
    winner_result: Any = None
    deadline = time.monotonic() + timeout

    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                sid, elapsed, result = task.result()
                if validate(result):
                    record_latency(sid, elapsed)
                    winner_sid = sid
                    winner_result = result
                    logger.debug(f"[race] 胜出: {sid} ({elapsed:.2f}s)")
                else:
                    record_latency(sid, timeout * _PENALTY_MULTIPLIER)
                    logger.debug(f"[race] {sid} 无效结果 ({elapsed:.2f}s)")
            if winner_sid is not None:
                break
    finally:
        for t in pending:
            t.cancel()

    if winner_sid is None:
        logger.warning(f"[race] 所有源均失败: {[s[0] for s in sources]}")

    return winner_sid, winner_result
