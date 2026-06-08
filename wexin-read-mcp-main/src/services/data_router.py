"""DataRouter — 统一的"数据契约 + 源路由"层。

目标:把"业务想要什么数据"和"用哪个源拿"解耦。
- 业务调用 `router.fetch("contract_name", **params)`
- Router 内部:并发跑所有注册的源,按 EWMA 历史延迟排序;首个成功结果即返回
- 自动:缓存 / stale 兜底 / 统计 / 失败标记

与旧 `source_racer.race_sources` 的区别:
- 这里以"契约"为单位注册,业务调用方不再硬编码源列表
- 统一的统计 + 健康面板(/api/health/sources)
- 缓存集成到一处,业务层 0 改动接入

设计原则:**最小内核**。不抽象不必要的概念,只把"多源 race + 缓存 + 统计"包装成一行调用。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from stock_utils import cache_get, cache_get_stale, cache_set

logger = logging.getLogger("data-router")

_ALPHA = 0.3                  # EWMA 平滑系数
_PENALTY_MULTIPLIER = 3.0     # 失败惩罚:记为 timeout × 此系数


# ─────────────────────────── 数据结构 ───────────────────────────

ProviderFn = Callable[..., Awaitable[Any]]


@dataclass
class Provider:
    """一个数据源的描述。"""
    id: str                       # 唯一 ID,例如 "em_push2_concept"
    fn: ProviderFn                # async 调用函数,签名 fn(**params)
    weight: float = 1.0           # 静态偏好(数据更全/更准的设高一点,只作为 EWMA 初始值)
    handicap_ms: int = 0          # 让步:执行前先 sleep 多少毫秒,让高质量源先返回


@dataclass
class ProviderStats:
    """运行时统计。"""
    ewma_latency: float = float("inf")  # 越小越好,首次调用前设 inf 让所有源公平
    calls: int = 0
    success: int = 0
    last_error: str = ""
    last_ok_at: float = 0.0
    last_err_at: float = 0.0


@dataclass
class Contract:
    """一个数据契约:逻辑接口名 + 多个候选源。"""
    name: str
    providers: list[Provider] = field(default_factory=list)
    default_ttl: int = 30
    default_timeout: float = 10.0
    description: str = ""


# ─────────────────────────── DataRouter ───────────────────────────

class DataRouter:
    def __init__(self):
        self._contracts: dict[str, Contract] = {}
        self._stats: dict[str, ProviderStats] = {}

    # ── 注册 ──
    def register_contract(
        self,
        name: str,
        *,
        default_ttl: int = 30,
        default_timeout: float = 10.0,
        description: str = "",
    ) -> None:
        if name in self._contracts:
            return
        self._contracts[name] = Contract(
            name=name,
            default_ttl=default_ttl,
            default_timeout=default_timeout,
            description=description,
        )

    def register_provider(
        self,
        contract: str,
        provider_id: str,
        fn: ProviderFn,
        *,
        weight: float = 1.0,
        handicap_ms: int = 0,
    ) -> None:
        if contract not in self._contracts:
            raise ValueError(f"未注册的契约: {contract}")
        # 避免重复注册
        existing_ids = {p.id for p in self._contracts[contract].providers}
        if provider_id in existing_ids:
            return
        self._contracts[contract].providers.append(
            Provider(id=provider_id, fn=fn, weight=weight, handicap_ms=handicap_ms)
        )
        self._stats.setdefault(provider_id, ProviderStats())

    # ── 取数 ──
    async def fetch(
        self,
        contract: str,
        *,
        cache_key: Optional[str] = None,
        ttl: Optional[int] = None,
        timeout: Optional[float] = None,
        validate: Optional[Callable[[Any], bool]] = None,
        allow_stale: bool = True,
        stale_max_seconds: int = 7 * 86400,
        **params: Any,
    ) -> dict:
        """从 `contract` 取数。

        返回统一结构:
            {"success": bool, "data": Any|None, "source": str|None,
             "stale": bool, "error": str|None}
        """
        if contract not in self._contracts:
            return {"success": False, "data": None, "source": None,
                    "stale": False, "error": f"未注册的契约: {contract}"}

        c = self._contracts[contract]
        if not c.providers:
            return {"success": False, "data": None, "source": None,
                    "stale": False, "error": f"契约 {contract} 没有可用源"}

        ttl = ttl if ttl is not None else c.default_ttl
        timeout = timeout if timeout is not None else c.default_timeout
        validate = validate or (lambda r: r is not None)

        # 1. 命中新鲜缓存
        if cache_key:
            fresh, is_fresh = cache_get_stale(cache_key, max_stale_seconds=stale_max_seconds)
            if is_fresh and fresh is not None:
                return {"success": True, "data": fresh, "source": "cache",
                        "stale": False, "error": None}

        # 2. 按 EWMA 延迟排序后并发 race
        ordered = sorted(
            c.providers,
            key=lambda p: self._stats[p.id].ewma_latency,
        )

        async def _timed(p: Provider):
            # 让步:数据稀薄的源故意延迟 N ms 启动,给高质量源先返回机会
            if p.handicap_ms > 0:
                await asyncio.sleep(p.handicap_ms / 1000)
            t0 = time.monotonic()
            self._stats[p.id].calls += 1
            try:
                result = await p.fn(**params)
                elapsed = time.monotonic() - t0
                return p.id, elapsed, result, None
            except Exception as e:
                elapsed = time.monotonic() - t0
                return p.id, elapsed, None, e

        tasks: dict[asyncio.Task, str] = {
            asyncio.create_task(_timed(p)): p.id for p in ordered
        }
        pending = set(tasks)
        deadline = time.monotonic() + timeout
        winner_id, winner_result = None, None
        errors: list[tuple[str, str]] = []

        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending, timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in done:
                    pid, elapsed, result, err = t.result()
                    if err is not None:
                        self._record_failure(pid, timeout, str(err))
                        errors.append((pid, f"{type(err).__name__}: {err}"))
                        continue
                    if not validate(result):
                        self._record_failure(pid, timeout, "validate-false")
                        errors.append((pid, "返回无效"))
                        continue
                    # 命中
                    self._record_success(pid, elapsed)
                    winner_id, winner_result = pid, result
                    break
                if winner_id is not None:
                    break
        finally:
            for t in pending:
                t.cancel()

        if winner_id is not None:
            if cache_key:
                cache_set(cache_key, winner_result, ttl=ttl)
            return {"success": True, "data": winner_result, "source": winner_id,
                    "stale": False, "error": None}

        # 3. 全败 → stale 兜底
        if allow_stale and cache_key:
            stale_val, _ = cache_get_stale(cache_key, max_stale_seconds=stale_max_seconds)
            if stale_val is not None:
                return {"success": True, "data": stale_val, "source": "cache(stale)",
                        "stale": True, "error": None}

        err_summary = "; ".join(f"{pid}:{msg[:60]}" for pid, msg in errors) or "所有源均失败"
        return {"success": False, "data": None, "source": None,
                "stale": False, "error": err_summary}

    # ── 统计辅助 ──
    def _record_success(self, pid: str, elapsed: float) -> None:
        st = self._stats[pid]
        if st.ewma_latency == float("inf"):
            st.ewma_latency = elapsed
        else:
            st.ewma_latency = _ALPHA * elapsed + (1 - _ALPHA) * st.ewma_latency
        st.success += 1
        st.last_ok_at = time.time()

    def _record_failure(self, pid: str, timeout: float, msg: str) -> None:
        st = self._stats[pid]
        penalty = timeout * _PENALTY_MULTIPLIER
        if st.ewma_latency == float("inf"):
            st.ewma_latency = penalty
        else:
            st.ewma_latency = _ALPHA * penalty + (1 - _ALPHA) * st.ewma_latency
        st.last_error = msg[:200]
        st.last_err_at = time.time()

    # ── 健康面板 ──
    def snapshot(self) -> dict:
        """供 /api/health/sources 暴露的状态。"""
        out: dict[str, list[dict]] = {}
        for name, c in self._contracts.items():
            rows = []
            for p in c.providers:
                st = self._stats[p.id]
                rows.append({
                    "id": p.id,
                    "weight": p.weight,
                    "ewma_ms": None if st.ewma_latency == float("inf")
                              else round(st.ewma_latency * 1000),
                    "calls": st.calls,
                    "success": st.success,
                    "success_rate": round(st.success / st.calls, 3) if st.calls else None,
                    "last_error": st.last_error,
                    "last_ok_at": st.last_ok_at,
                    "last_err_at": st.last_err_at,
                })
            rows.sort(key=lambda r: r["ewma_ms"] if r["ewma_ms"] is not None else 10**9)
            out[name] = rows
        return out


# ─────────────────────────── 全局单例 ───────────────────────────

router = DataRouter()


def get_router() -> DataRouter:
    return router
