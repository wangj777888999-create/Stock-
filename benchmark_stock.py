#!/usr/bin/env python3
"""
股票服务基线性能测试
"""
import asyncio
import time
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / "wexin-read-mcp-main" / "src"))

from stock_service import StockService

async def test_single_stock():
    """单次请求基线测试"""
    service = StockService()
    symbol = "600519"

    tests = [
        ("search_stock", lambda: service.search_stock("茅台")),
        ("get_realtime_quote", lambda: service.get_realtime_quote(symbol)),
        ("get_kline", lambda: service.get_kline(symbol, count=30)),
        ("get_company_profile", lambda: service.get_company_profile(symbol)),
        ("get_financial", lambda: service.get_financial(symbol)),
        ("get_money_flow", lambda: service.get_money_flow(symbol)),
        ("get_news", lambda: service.get_news(symbol)),
        ("get_shareholders", lambda: service.get_shareholders(symbol)),
    ]

    print(f"{'='*60}")
    print(f"股票服务基线性能测试 - 单次请求")
    print(f"{'='*60}")
    print(f"{'接口':<25} {'状态':<10} {'耗时(秒)':<10}")
    print(f"{'-'*60}")

    results = []
    for name, func in tests:
        start = time.time()
        try:
            # 清除缓存确保每次都真实请求
            from stock_utils import cache
            cache._store.clear()

            result = await func()
            elapsed = time.time() - start

            if result.get("success"):
                print(f"{name:<25} {'✓ 成功':<10} {elapsed:<10.3f}")
                results.append((name, elapsed, True, None))
            else:
                print(f"{name:<25} {'✗ 失败':<10} {elapsed:<10.3f}")
                print(f"  错误: {result.get('error', 'Unknown')[:50]}")
                results.append((name, elapsed, False, result.get('error')))
        except Exception as e:
            elapsed = time.time() - start
            print(f"{name:<25} {'✗ 异常':<10} {elapsed:<10.3f}")
            print(f"  异常: {str(e)[:50]}")
            results.append((name, elapsed, False, str(e)))

    print(f"{'='*60}")
    print(f"\n汇总:")
    success_results = [(n, t) for n, t, s, _ in results if s]
    if success_results:
        avg_time = sum(t for _, t in success_results) / len(success_results)
        max_time = max(success_results, key=lambda x: x[1])
        print(f"  成功: {len(success_results)}/{len(results)}")
        print(f"  平均耗时: {avg_time:.3f}秒")
        print(f"  最慢: {max_time[0]} ({max_time[1]:.3f}秒)")

    return results


async def test_concurrent_stock(n_concurrent: int = 10):
    """并发请求测试"""
    service = StockService()
    symbols = ["600519", "000001", "000002", "300750", "688981"]

    print(f"\n{'='*60}")
    print(f"股票服务并发测试 - {n_concurrent}并发请求")
    print(f"{'='*60}")

    start = time.time()

    tasks = []
    for i in range(n_concurrent):
        symbol = symbols[i % len(symbols)]
        tasks.append(service.get_realtime_quote(symbol))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - start
    success_count = sum(1 for r in results if isinstance(r, dict) and r.get("success"))

    print(f"并发数: {n_concurrent}")
    print(f"成功: {success_count}/{n_concurrent}")
    print(f"总耗时: {elapsed:.3f}秒")
    print(f"平均每请求: {elapsed/n_concurrent:.3f}秒")
    print(f"吞吐量: {n_concurrent/elapsed:.2f} 请求/秒")

    return elapsed, success_count


async def main():
    from database import init_db
    init_db()
    print("开始股票服务性能测试...\n")

    # 1. 单次基线测试
    await test_single_stock()

    # 2. 并发测试
    for n in [5, 10, 20]:
        await test_concurrent_stock(n)
        await asyncio.sleep(1)

    print(f"\n{'='*60}")
    print("测试完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
