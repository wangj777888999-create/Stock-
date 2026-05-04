#!/usr/bin/env python3
"""
股票服务性能测试 - 优化后验证
"""
import asyncio
import time
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / "wexin-read-mcp-main" / "src"))

from stock_service import StockService

async def test_with_preload():
    """使用预加载的股票列表进行测试"""
    service = StockService()

    print(f"{'='*60}")
    print(f"步骤1: 预加载股票列表（模拟服务启动时执行）")
    print(f"{'='*60}")
    start = time.time()
    success = await service.preload_stock_list()
    preload_time = time.time() - start
    print(f"预加载: {'✓ 成功' if success else '✗ 失败'} ({preload_time:.3f}秒)")

    if not success:
        print("预加载失败，无法继续测试")
        return

    print(f"\n{'='*60}")
    print(f"步骤2: 第一次搜索（无缓存）")
    print(f"{'='*60}")
    # 清除缓存
    from stock_utils import cache
    cache._store.clear()

    start = time.time()
    result = await service.search_stock("茅台")
    elapsed = time.time() - start
    print(f"search_stock 耗时: {elapsed:.3f}秒")
    print(f"结果: {result.get('success')}, 找到 {len(result.get('data', []))} 只")

    print(f"\n{'='*60}")
    print(f"步骤3: 第二次搜索（走缓存）")
    print(f"{'='*60}")
    start = time.time()
    result = await service.search_stock("茅台")
    elapsed = time.time() - start
    print(f"search_stock 耗时: {elapsed:.3f}秒")

    print(f"\n{'='*60}")
    print(f"步骤4: 其他接口测试（均无缓存）")
    print(f"{'='*60}")
    cache._store.clear()

    symbol = "600519"
    tests = [
        ("get_realtime_quote", lambda: service.get_realtime_quote(symbol)),
        ("get_kline", lambda: service.get_kline(symbol, count=30)),
        ("get_company_profile", lambda: service.get_company_profile(symbol)),
        ("get_financial", lambda: service.get_financial(symbol)),
        ("get_money_flow", lambda: service.get_money_flow(symbol)),
        ("get_news", lambda: service.get_news(symbol)),
        ("get_shareholders", lambda: service.get_shareholders(symbol)),
    ]

    for name, func in tests:
        start = time.time()
        result = await func()
        elapsed = time.time() - start
        status = "✓" if result.get("success") else "✗"
        print(f"  {name:<25} {status} {elapsed:.3f}秒")


async def test_concurrent_improvement():
    """测试并发性能改进"""
    service = StockService()
    await service.preload_stock_list()

    print(f"\n{'='*60}")
    print(f"步骤5: 并发搜索测试（预加载后）")
    print(f"{'='*60}")

    from stock_utils import cache

    keywords = ["茅台", "平安", "银行", "科技", "医药"]

    for n_concurrent in [5, 10, 20]:
        cache._store.clear()  # 清除缓存
        start = time.time()
        tasks = [service.search_stock(kw) for kw in keywords * (n_concurrent // 5 + 1)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - start
        success = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
        print(f"  {n_concurrent}并发: 总耗时 {elapsed:.3f}秒, 成功 {success}/{len(results)}, 吞吐量 {len(results)/elapsed:.1f}/秒")


async def main():
    print("股票服务性能测试 - 优化后验证\n")

    await test_with_preload()
    await test_concurrent_improvement()

    print(f"\n{'='*60}")
    print("测试完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
