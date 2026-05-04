#!/usr/bin/env python3
"""
综合性能测试 - 测试所有模块
"""
import asyncio
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "wexin-read-mcp-main" / "src"))

from stock_service import StockService


async def test_scraper():
    """测试微信爬虫"""
    from scraper import WeixinScraper

    scraper = WeixinScraper()

    # 测试文章URL
    test_url = "https://mp.weixin.qq.com/s/test_url"

    print(f"\n{'='*60}")
    print(f"微信爬虫测试")
    print(f"{'='*60}")

    start = time.time()
    result = await scraper.fetch_article(test_url)
    elapsed = time.time() - start

    print(f"耗时: {elapsed:.3f}秒")
    print(f"结果: {result.get('success')}")
    if not result.get("success"):
        print(f"错误: {result.get('error', '')[:100]}")

    await scraper.cleanup()
    return elapsed, result.get("success")


async def test_blogger_concurrent():
    """测试博主管理并发获取"""
    from blogger import BloggerManager
    from config import AppConfig
    from scraper import WeixinScraper

    config = AppConfig.from_env()
    scraper = WeixinScraper()
    manager = BloggerManager(scraper, config)

    print(f"\n{'='*60}")
    print(f"博主管理测试")
    print(f"{'='*60}")

    bloggers = manager.list_bloggers()
    print(f"已有博主数量: {len(bloggers)}")

    if bloggers:
        # 测试获取单个博主的文章
        blogger = bloggers[0]
        start = time.time()
        result = await manager.fetch_recent_articles(blogger, count=3)
        elapsed = time.time() - start
        print(f"获取博主文章: {elapsed:.3f}秒, 成功: {result.get('success')}")
        if result.get("success"):
            print(f"  文章数: {len(result.get('articles', []))}")

    return bloggers


async def test_email():
    """测试邮件发送"""
    from emailer import EmailSender
    from config import AppConfig

    config = AppConfig.from_env()
    sender = EmailSender(config.email)

    print(f"\n{'='*60}")
    print(f"邮件发送测试")
    print(f"{'='*60}")

    # 测试邮件配置是否有效
    if not config.email.sender_email:
        print("未配置邮件，跳过测试")
        return None

    print(f"发件人: {config.email.sender_email}")
    print("（不实际发送邮件，仅验证配置）")

    return True


async def test_concurrent_scraper(n_concurrent: int = 3):
    """并发抓取测试"""
    from scraper import WeixinScraper

    scraper = WeixinScraper()

    # 多个测试URL
    urls = [
        "https://mp.weixin.qq.com/s/xxx1",
        "https://mp.weixin.qq.com/s/xxx2",
        "https://mp.weixin.qq.com/s/xxx3",
    ]

    print(f"\n{'='*60}")
    print(f"并发爬虫测试 - {n_concurrent}并发")
    print(f"{'='*60}")

    start = time.time()
    tasks = [scraper.fetch_article(url) for url in urls[:n_concurrent]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.time() - start

    success = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
    print(f"并发数: {n_concurrent}")
    print(f"成功: {success}/{n_concurrent}")
    print(f"总耗时: {elapsed:.3f}秒")
    print(f"平均每请求: {elapsed/n_concurrent:.3f}秒")

    await scraper.cleanup()
    return elapsed, success


async def main():
    print("="*60)
    print("股票信息平台 - 综合性能测试")
    print("="*60)

    # 1. 股票服务（已优化）
    print("\n[1/4] 股票服务测试...")
    service = StockService()
    await service.preload_stock_list()

    from stock_utils import cache
    cache._store.clear()

    start = time.time()
    result = await service.search_stock("茅台")
    stock_time = time.time() - start
    print(f"  search_stock: {stock_time:.3f}秒, 成功: {result.get('success')}")

    # 2. 微信爬虫
    print("\n[2/4] 微信爬虫测试...")
    await test_scraper()

    # 3. 并发爬虫
    print("\n[3/4] 并发爬虫测试...")
    await test_concurrent_scraper(3)

    # 4. 博主管理
    print("\n[4/4] 博主管理测试...")
    bloggers = await test_blogger_concurrent()

    print("\n" + "="*60)
    print("测试完成")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
