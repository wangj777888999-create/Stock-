"""文章管理 API — 抓取文章列表 + 手动触发采集 + 调度配置"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/articles", tags=["articles"])

SCHEDULER_CONFIG_FILE = Path(__file__).parent.parent.parent / "scheduler_config.json"

DEFAULT_SCHEDULER_CONFIG = {
    "enabled": True,
    "hour": 15,
    "minute": 45,
    "days_of_week": "mon-fri",
    "articles_per_blogger": 3,
}


def _load_scheduler_config() -> dict:
    """加载调度配置，文件不存在则返回默认值"""
    if SCHEDULER_CONFIG_FILE.exists():
        try:
            return {**DEFAULT_SCHEDULER_CONFIG, **json.loads(SCHEDULER_CONFIG_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_SCHEDULER_CONFIG)


def _save_scheduler_config(cfg: dict):
    """持久化调度配置"""
    SCHEDULER_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("")
async def list_articles(
    blogger_id: str = "",
    processed: int = -1,
    page: int = 1,
    page_size: int = 20,
):
    """获取抓取文章列表"""
    db = get_db()
    conditions = []
    params = []

    if blogger_id:
        conditions.append("a.blogger_id = ?")
        params.append(blogger_id)
    if processed >= 0:
        conditions.append("a.processed = ?")
        params.append(processed)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # 总数
    total = db.execute(
        f"SELECT COUNT(*) FROM scraped_articles a{where}", params
    ).fetchone()[0]

    # 分页查询，关联博主名称
    offset = (page - 1) * page_size
    rows = db.execute(
        f"""SELECT a.*, COALESCE(a.ai_mentions, '[]') as mentions_json
            FROM scraped_articles a{where}
            ORDER BY a.created_at DESC
            LIMIT ? OFFSET ?""",
        params + [page_size, offset],
    ).fetchall()

    columns = [
        "id", "blogger_id", "title", "url", "author", "publish_time",
        "content", "cover_url", "ai_mentions", "scrape_date", "processed",
        "created_at", "mentions_json",
    ]
    articles = []
    for row in rows:
        article = dict(zip(columns, row))
        # 解析 ai_mentions JSON
        try:
            article["mentions"] = json.loads(article.pop("mentions_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            article["mentions"] = []
        # 截断 content 用于列表预览
        article["content_preview"] = (article.get("content") or "")[:200]
        article.pop("content", None)
        articles.append(article)

    return {
        "success": True,
        "articles": articles,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --- 特定路由必须在 /{article_id} 之前 ---

@router.post("/scrape-now")
async def trigger_scrape():
    """手动触发一次自动采集"""
    from state import blogger_mgr, scraper, config
    from scheduler import auto_scrape_job

    try:
        result = await auto_scrape_job(blogger_mgr, scraper, config)
        return result
    except Exception as e:
        logger.error(f"手动采集失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@router.get("/scheduler-config")
async def get_scheduler_config():
    """获取自动采集调度配置"""
    cfg = _load_scheduler_config()
    from scheduler import _scheduler
    running = _scheduler is not None and _scheduler.running
    next_run = None
    if running:
        try:
            job = _scheduler.get_job("daily_scrape")
            if job and job.next_run_time:
                next_run = str(job.next_run_time)
        except Exception:
            pass
    return {"success": True, "config": cfg, "running": running, "next_run": next_run}


class SchedulerConfigUpdate(BaseModel):
    enabled: bool | None = None
    hour: int | None = None
    minute: int | None = None
    days_of_week: str | None = None
    articles_per_blogger: int | None = None


@router.post("/scheduler-config")
async def update_scheduler_config(body: SchedulerConfigUpdate):
    """更新自动采集调度配置并热重载调度器"""
    cfg = _load_scheduler_config()

    if body.enabled is not None:
        cfg["enabled"] = body.enabled
    if body.hour is not None and 0 <= body.hour <= 23:
        cfg["hour"] = body.hour
    if body.minute is not None and 0 <= body.minute <= 59:
        cfg["minute"] = body.minute
    if body.days_of_week is not None:
        cfg["days_of_week"] = body.days_of_week
    if body.articles_per_blogger is not None and body.articles_per_blogger > 0:
        cfg["articles_per_blogger"] = body.articles_per_blogger

    _save_scheduler_config(cfg)

    try:
        from scheduler import reschedule
        reschedule(cfg)
    except Exception as e:
        logger.warning(f"调度器热重载失败: {e}")

    return {"success": True, "config": cfg}


# --- 参数化路由 ---

@router.get("/{article_id}")
async def get_article(article_id: int):
    """获取单篇文章详情"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM scraped_articles WHERE id = ?", (article_id,)
    ).fetchone()

    if not row:
        return {"success": False, "error": "文章不存在"}

    columns = [desc[0] for desc in db.execute("SELECT * FROM scraped_articles LIMIT 0").description]
    article = dict(zip(columns, row))

    try:
        article["mentions"] = json.loads(article.get("ai_mentions") or "[]")
    except (json.JSONDecodeError, TypeError):
        article["mentions"] = []

    return {"success": True, "article": article}


@router.put("/{article_id}/processed")
async def mark_processed(article_id: int, processed: int = 1):
    """标记文章为已处理"""
    db = get_db()
    db.execute(
        "UPDATE scraped_articles SET processed = ? WHERE id = ?",
        (processed, article_id),
    )
    db.commit()
    return {"success": True}


@router.delete("/{article_id}")
async def delete_article(article_id: int):
    """删除文章"""
    db = get_db()
    db.execute("DELETE FROM scraped_articles WHERE id = ?", (article_id,))
    db.commit()
    return {"success": True}
