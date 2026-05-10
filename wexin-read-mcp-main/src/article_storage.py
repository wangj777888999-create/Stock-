"""统一文章存储服务 — 所有文章持久化的唯一入口"""

import json
import logging
import re
from database import get_db

logger = logging.getLogger(__name__)

# 手动 URL 采集时的固定 blogger_id，避免空串语义歧义
MANUAL_BLOGGER_ID = "__manual__"


def make_preview(content: str, length: int = 150) -> str:
    """从 HTML 正文中提取纯文本并截取前 length 个字符作为预览"""
    if not content:
        return ""
    text = re.sub(r"<[^>]+>", "", content)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:length]


def get_existing_urls(blogger_id: str = "") -> set[str]:
    """获取已存储的文章 URL 集合，用于去重检查"""
    db = get_db()
    if blogger_id:
        rows = db.execute(
            "SELECT url FROM scraped_articles WHERE blogger_id = ?", (blogger_id,)
        ).fetchall()
    else:
        rows = db.execute("SELECT url FROM scraped_articles").fetchall()
    return {row[0] for row in rows}


def save_articles(
    articles: list[dict],
    blogger_id: str = "",
    ai_mentions: dict[str, list] | None = None,
    skip_existing: bool = True,
) -> dict:
    """
    将抓取的文章批量写入 scraped_articles 表。

    articles: scraper.fetch_article() 返回的 dict 列表，
              需包含 url, title，可选 author, publish_time, content, cover_url
    blogger_id: 博主 ID，手动 URL 采集时使用 MANUAL_BLOGGER_ID
    ai_mentions: {url: [mention, ...]} 按文章 URL 分组的 AI 提及数据，可为 None
    skip_existing: True 则 INSERT OR IGNORE（按 URL 去重），False 强制写入

    返回: {"success": True, "inserted": N, "skipped": N}

    遇到 DB 错误时抛出异常，由调用方负责 catch。
    """
    db = get_db()
    inserted = 0
    skipped = 0

    # 如果需要去重，先获取已有 URL
    existing_urls: set[str] = set()
    if skip_existing:
        existing_urls = get_existing_urls(blogger_id)

    for article in articles:
        url = article.get("url", "")
        if not url:
            skipped += 1
            continue

        if skip_existing and url in existing_urls:
            skipped += 1
            continue

        mentions_data = None
        if ai_mentions is not None:
            mentions_data = ai_mentions.get(url, [])

        db.execute(
            """INSERT OR IGNORE INTO scraped_articles
               (blogger_id, title, url, author, publish_time, content, cover_url, ai_mentions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                blogger_id or MANUAL_BLOGGER_ID,
                article.get("title", "未知标题"),
                url,
                article.get("author", ""),
                article.get("publish_time", article.get("date", "")),
                article.get("content", ""),
                article.get("cover_url", ""),
                json.dumps(mentions_data, ensure_ascii=False) if mentions_data is not None else None,
            ),
        )
        if db.execute("SELECT changes()").fetchone()[0] > 0:
            inserted += 1
        else:
            skipped += 1

    db.commit()
    logger.info(f"存储完成: inserted={inserted}, skipped={skipped}")
    return {"success": True, "inserted": inserted, "skipped": skipped}


def update_mentions(url: str, mentions: list[dict]) -> bool:
    """更新指定文章的 ai_mentions 字段"""
    db = get_db()
    db.execute(
        "UPDATE scraped_articles SET ai_mentions = ? WHERE url = ?",
        (json.dumps(mentions, ensure_ascii=False), url),
    )
    db.commit()
    return True
