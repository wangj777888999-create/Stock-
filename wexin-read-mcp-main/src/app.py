"""FastAPI Web应用 — 交易感知系统"""
import asyncio
import sys
import json
import logging
from pathlib import Path

# Windows 上 Playwright 需要 ProactorEventLoop 来创建子进程
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from database import init_db, close_db
from stock_service import StockService
from state import config, CONFIG_FILE, blogger_mgr, scraper

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="交易感知系统")


def load_saved_config():
    """加载已保存的用户配置（仅覆盖未从环境变量设置的项）

    安全策略：
    - 敏感信息（密码、Cookie、Token）只从环境变量读取
    - 非敏感配置可从 JSON 文件加载作为兜底
    """
    if not CONFIG_FILE.exists():
        return

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

        # 邮箱配置 - 敏感字段只从环境变量
        email_data = data.get("email", {})
        if not config.email.smtp_server and email_data.get("smtp_server"):
            config.email.smtp_server = email_data["smtp_server"]
        if not config.email.sender_email and email_data.get("sender_email"):
            config.email.sender_email = email_data["sender_email"]
        # sender_password 永不从文件加载，只从环境变量

        # AI配置 - api_key 只从环境变量
        ai_data = data.get("ai", {})
        if not config.ai.base_url and ai_data.get("base_url"):
            config.ai.base_url = ai_data["base_url"]
        if not config.ai.model and ai_data.get("model"):
            config.ai.model = ai_data["model"]
        # api_key 永不从文件加载，只从环境变量

        # 微信配置 - 敏感字段只从环境变量
        wx_data = data.get("wechat", {})
        # cookie, mp_cookie, mp_token 永不从文件加载，只从环境变量

        logger.info("已加载保存的非敏感配置")
    except Exception as e:
        logger.warning(f"加载配置失败: {e}")


load_saved_config()


# 注册所有路由模块
from routers.stock import router as stock_router
from routers.market import router as market_router
from routers.iwencai import router as iwencai_router
from routers.blogger import router as blogger_router
from routers.config import router as config_router
from routers.watchlist import router as watchlist_router
from routers.sim import router as sim_router
from routers.journal import router as journal_router
from routers.verify import router as verify_router
from routers.stats import router as stats_router
from routers.articles import router as articles_router

app.include_router(stock_router)
app.include_router(market_router)
app.include_router(iwencai_router)
app.include_router(blogger_router)
app.include_router(config_router)
app.include_router(watchlist_router)
app.include_router(sim_router)
app.include_router(journal_router)
app.include_router(verify_router)
app.include_router(stats_router)
app.include_router(articles_router)
from routers.sector import router as sector_router
app.include_router(sector_router)
from routers.analysis import router as analysis_router
app.include_router(analysis_router)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.on_event("startup")
async def _startup():
    """启动时预加载数据。"""
    init_db()
    asyncio.create_task(StockService.preload_stock_list())
    asyncio.create_task(StockService._refresh_stock_list_bg())

    async def _preload_fund():
        try:
            from market.fund import _get_etf_df
            await _get_etf_df()
            logger.info("基金 ETF 数据预加载完成")
        except Exception as e:
            logger.warning(f"基金数据预加载失败（首次访问时重试）: {e}")

    async def _preload_sector_boards():
        """预热板块列表（行业 + 概念），填充 L1+L2 缓存。"""
        try:
            from state import get_sector_service
            svc = get_sector_service()
            await asyncio.gather(
                svc._get_boards_single("industry"),
                svc._get_boards_single("concept"),
            )
            logger.info("板块列表预热完成")
        except Exception as e:
            logger.warning(f"板块列表预热失败（首次访问时重试）: {e}")

    async def _preload_watchlist_quotes():
        """预热自选股实时行情。"""
        try:
            from database import get_db
            from stock_service import StockService as SS
            db = get_db()
            rows = db.execute("SELECT symbol FROM watchlist LIMIT 30").fetchall()
            if not rows:
                return
            svc = SS()
            await asyncio.gather(
                *[svc.get_realtime_quote(r[0]) for r in rows],
                return_exceptions=True,
            )
            logger.info(f"自选股行情预热完成（{len(rows)} 只）")
        except Exception as e:
            logger.warning(f"自选股预热失败: {e}")

    asyncio.create_task(_preload_fund())
    asyncio.create_task(_preload_sector_boards())
    asyncio.create_task(_preload_watchlist_quotes())

    # 启动定时采集调度器
    try:
        from scheduler import start_scheduler
        start_scheduler(blogger_mgr, scraper, config)
    except Exception as e:
        logger.warning(f"定时调度器启动失败: {e}")


@app.on_event("shutdown")
async def _shutdown():
    """关闭时清理资源。"""
    try:
        from scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    try:
        await scraper.cleanup()
    except Exception:
        pass
    close_db()
    logger.info("应用已关闭")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
