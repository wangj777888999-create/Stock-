"""定时任务调度器 — 每日自动抓取文章 + AI扫描"""

import json
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None
BLOGGERS_FILE = Path(__file__).parent.parent / "bloggers.json"
SCHEDULER_CONFIG_FILE = Path(__file__).parent.parent / "scheduler_config.json"


def _load_config() -> dict:
    """加载调度配置"""
    defaults = {"enabled": True, "hour": 15, "minute": 45, "days_of_week": "mon-fri", "articles_per_blogger": 3}
    if SCHEDULER_CONFIG_FILE.exists():
        try:
            return {**defaults, **json.loads(SCHEDULER_CONFIG_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return defaults


async def auto_scrape_job(blogger_mgr, scraper, config):
    """每日自动采集：抓取新文章 → AI扫描 → 存库"""
    from database import get_db
    from analyzer import ArticleAnalyzer

    logger.info("=== 自动采集开始 ===")

    # 检查 Cookie 有效性
    mp_cookie = config.wechat.mp_cookie or ""
    mp_token = config.wechat.mp_token or ""
    if not mp_cookie or not mp_token:
        logger.warning("Cookie 未配置，跳过自动采集")
        return {"success": False, "error": "Cookie 未配置，请先扫码登录"}

    bloggers = blogger_mgr.list_bloggers()
    if not bloggers:
        logger.info("没有关注的博主，跳过采集")
        return {"success": True, "count": 0}

    scfg = _load_config()
    articles_count = scfg.get("articles_per_blogger", 3)

    db = get_db()
    analyzer = ArticleAnalyzer(config)
    total_new = 0
    errors = []

    for blogger in bloggers:
        blogger_id = blogger.get("id", "")
        blogger_name = blogger.get("name", "未知")
        try:
            # 获取最新文章列表
            result = await blogger_mgr.fetch_recent_articles(blogger, count=articles_count, mode="latest_n")
            if not result.get("success"):
                err = result.get("error", "未知错误")
                logger.warning(f"[{blogger_name}] 获取文章列表失败: {err}")
                errors.append(f"{blogger_name}: {err}")
                continue

            articles_meta = result.get("articles", [])

            # 过滤已抓取的（按 url 去重）
            existing_urls = set()
            rows = db.execute(
                "SELECT url FROM scraped_articles WHERE blogger_id = ?", (blogger_id,)
            ).fetchall()
            for row in rows:
                existing_urls.add(row[0])

            new_urls = [a for a in articles_meta if a.get("url") not in existing_urls]
            if not new_urls:
                logger.info(f"[{blogger_name}] 没有新文章")
                continue

            # 抓取新文章内容
            scraped_articles = []
            for meta in new_urls:
                url = meta.get("url", "")
                if not url:
                    continue
                scrape_result = await scraper.fetch_article(url)
                if scrape_result.get("success"):
                    scrape_result["url"] = url
                    scrape_result["date"] = meta.get("date", "")
                    scraped_articles.append(scrape_result)
                else:
                    logger.warning(f"[{blogger_name}] 抓取失败: {url[:50]}")

            if not scraped_articles:
                continue

            # AI 扫描
            scan_result = await analyzer.extract_mentions(scraped_articles)
            mentions = scan_result.get("mentions", []) if scan_result.get("success") else []

            # 按 article_url 分组 mentions
            mentions_by_url = {}
            for m in mentions:
                article_url = m.get("article_url", "")
                if article_url:
                    mentions_by_url.setdefault(article_url, []).append(m)

            # 存入数据库
            for article in scraped_articles:
                url = article.get("url", "")
                article_mentions = mentions_by_url.get(url, [])
                try:
                    db.execute(
                        """INSERT OR IGNORE INTO scraped_articles
                           (blogger_id, title, url, author, publish_time, content, cover_url, ai_mentions)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            blogger_id,
                            article.get("title", "未知标题"),
                            url,
                            article.get("author", ""),
                            article.get("publish_time", article.get("date", "")),
                            article.get("content", ""),
                            article.get("cover_url", ""),
                            json.dumps(article_mentions, ensure_ascii=False),
                        ),
                    )
                    total_new += 1
                except Exception as e:
                    logger.warning(f"存储文章失败: {e}")

            db.commit()
            logger.info(f"[{blogger_name}] 新增 {len(scraped_articles)} 篇文章")

        except Exception as e:
            logger.error(f"[{blogger_name}] 采集异常: {e}", exc_info=True)
            errors.append(f"{blogger_name}: {str(e)}")

    logger.info(f"=== 自动采集完成: 新增 {total_new} 篇文章 ===")
    return {"success": True, "count": total_new, "errors": errors}


def start_scheduler(blogger_mgr, scraper, config):
    """启动定时调度器"""
    global _scheduler
    if _scheduler is not None:
        return

    scfg = _load_config()
    if not scfg.get("enabled", True):
        logger.info("自动采集已禁用，调度器不启动")
        return

    _scheduler = AsyncIOScheduler()
    _add_job(_scheduler, scfg, blogger_mgr, scraper, config)
    _scheduler.start()
    logger.info(f"定时调度器已启动: {scfg['days_of_week']} {scfg['hour']:02d}:{scfg['minute']:02d}")


def _add_job(scheduler, scfg, blogger_mgr, scraper, config):
    """添加采集任务到调度器"""
    scheduler.add_job(
        auto_scrape_job,
        trigger=CronTrigger(
            hour=scfg.get("hour", 15),
            minute=scfg.get("minute", 45),
            day_of_week=scfg.get("days_of_week", "mon-fri"),
        ),
        args=[blogger_mgr, scraper, config],
        id="daily_scrape",
        name="每日文章自动采集",
        replace_existing=True,
        misfire_grace_time=3600,
    )


def reschedule(scfg: dict):
    """热重载调度任务"""
    global _scheduler
    if _scheduler is None:
        return

    from state import blogger_mgr, scraper, config

    if not scfg.get("enabled", True):
        # 移除任务但不关调度器
        try:
            _scheduler.remove_job("daily_scrape")
            logger.info("自动采集已禁用，已移除定时任务")
        except Exception:
            pass
        return

    _add_job(_scheduler, scfg, blogger_mgr, scraper, config)
    logger.info(f"调度任务已更新: {scfg['days_of_week']} {scfg['hour']:02d}:{scfg['minute']:02d}")


def stop_scheduler():
    """停止调度器"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("定时调度器已停止")
