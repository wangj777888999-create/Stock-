"""FastAPI Web应用 - 股票博主文章收集分析平台"""

import asyncio
import sys
import base64
import logging
import json
import re
from pathlib import Path

# Windows 上 Playwright 需要 ProactorEventLoop 来创建子进程
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright

from agents import list_personas
from config import AppConfig, EmailConfig, AIConfig, WeChatConfig
from scraper import WeixinScraper
from analyzer import ArticleAnalyzer
from emailer import EmailSender
from blogger import BloggerManager
from stock_service import StockService
from stock_utils import detect_market
from iwencai_service import IWencaiService
from global_stock_service import global_stock_service
from market import get_provider
from database import init_db, close_db
from routers.watchlist import router as watchlist_router
from routers.sim import router as sim_router

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="股票博主文章分析平台")
app.include_router(watchlist_router)
app.include_router(sim_router)

# 全局状态
scraper = WeixinScraper()
config = AppConfig.from_env()
blogger_mgr = BloggerManager(scraper, config)
stock_service = StockService()
wencai_service = IWencaiService()

# 配置持久化路径
CONFIG_FILE = Path(__file__).parent.parent / "user_config.json"


def load_saved_config():
    """加载已保存的用户配置（仅覆盖未从环境变量设置的项）

    安全策略：
    - 敏感信息（密码、Cookie、Token）只从环境变量读取
    - 非敏感配置可从 JSON 文件加载作为兜底
    """
    global config
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


# ---------- 数据模型 ----------

class ConfigRequest(BaseModel):
    smtp_server: str
    smtp_port: int = 465
    sender_email: str
    sender_password: str
    use_ssl: bool = True
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    wechat_cookie: str = ""
    mp_cookie: str = ""
    mp_token: str = ""


class IdentifyRequest(BaseModel):
    url: str


# ---------- 页面路由 ----------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ---------- 配置API ----------

@app.post("/api/config")
async def save_config(req: ConfigRequest):
    """
    保存配置到文件（仅保存非敏感配置）

    安全策略：
    - 敏感信息（密码、API密钥、Cookie、Token）永不持久化到文件
    - 这些敏感值必须通过环境变量提供
    - 仅保存 smtp_server, smtp_port, sender_email, use_ssl, base_url, model 等非敏感配置
    """
    # 更新内存中的配置（敏感字段保持从环境变量读取的值）
    if req.sender_password != "__KEEP__":
        config.email.sender_password = req.sender_password
    if req.ai_api_key != "__KEEP__":
        config.ai.api_key = req.ai_api_key
    if req.wechat_cookie != "__KEEP__":
        config.wechat.cookie = req.wechat_cookie
    if req.mp_cookie != "__KEEP__":
        config.wechat.mp_cookie = req.mp_cookie
    if req.mp_token != "__KEEP__":
        config.wechat.mp_token = req.mp_token

    # 仅保存非敏感配置到文件
    save_data = {
        "email": {
            "smtp_server": req.smtp_server,
            "smtp_port": req.smtp_port,
            "sender_email": req.sender_email,
            "use_ssl": req.use_ssl,
            # sender_password 不保存，只从环境变量读取
        },
        "ai": {
            "base_url": req.ai_base_url,
            "model": req.ai_model,
            # api_key 不保存，只从环境变量读取
        },
        "wechat": {
            # cookie, mp_cookie, mp_token 不保存，只从环境变量读取
        },
    }
    CONFIG_FILE.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": "配置已保存（非敏感配置）"}


@app.get("/api/config")
async def get_config():
    return {
        "smtp_server": config.email.smtp_server,
        "smtp_port": config.email.smtp_port,
        "sender_email": config.email.sender_email,
        "sender_password": "******" if config.email.sender_password else "",
        "use_ssl": config.email.use_ssl,
        "ai_api_key": "******" if config.ai.api_key else "",
        "ai_base_url": config.ai.base_url,
        "ai_model": config.ai.model,
        "wechat_cookie": "******" if config.wechat.cookie else "",
        "mp_cookie": "******" if config.wechat.mp_cookie else "",
        "mp_token": config.wechat.mp_token or "",
    }


# ---------- 股票查询API ----------

@app.get("/api/stock/search")
async def api_stock_search(keyword: str = ""):
    if not keyword or len(keyword.strip()) < 1:
        return {"success": False, "error": "请输入搜索关键词"}
    kw = keyword.strip()
    result = await stock_service.search_stock(kw)
    # 追加韩/日股搜索结果
    global_result = await global_stock_service.search(kw)
    if global_result.get("success") and global_result.get("data"):
        result.setdefault("data", []).extend(global_result["data"])
        result["data"] = result["data"][:25]
    return result


@app.get("/api/stock/quote/{symbol}")
async def api_stock_quote(symbol: str, auto: int = 0):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return await global_stock_service.get_realtime_quote(symbol, market)
    bypass = auto == 1 and market == "a"
    return await stock_service.get_realtime_quote(symbol, bypass_cache=bypass)


@app.get("/api/stock/kline/{symbol}")
async def api_stock_kline(symbol: str, period: str = "day", count: int = 120, indicators: str = ""):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return await global_stock_service.get_kline(symbol, market, period, count)
    return await stock_service.get_kline(symbol, period, count, indicators=indicators)


@app.get("/api/stock/profile/{symbol}")
async def api_stock_profile(symbol: str):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return await global_stock_service.get_profile(symbol, market)
    return await stock_service.get_company_profile(symbol)


@app.get("/api/stock/financial/{symbol}")
async def api_stock_financial(symbol: str):
    market = detect_market(symbol)
    if market in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持详细财务数据"}
    return await stock_service.get_financial(symbol)


@app.get("/api/stock/flow/{symbol}")
async def api_stock_flow(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持资金流向"}
    return await stock_service.get_money_flow(symbol)


@app.get("/api/stock/news/{symbol}")
async def api_stock_news(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持新闻"}
    return await stock_service.get_news(symbol)


@app.get("/api/stock/announcements/{symbol}")
async def api_stock_announcements(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持公告"}
    return await stock_service.get_announcements(symbol)


@app.get("/api/stock/shareholders/{symbol}")
async def api_stock_shareholders(symbol: str):
    if detect_market(symbol) in ("kr", "jp"):
        return {"success": False, "error": "韩股/日股暂不支持股东信息"}
    return await stock_service.get_shareholders(symbol)


# ---------- 问财选股API ----------

@app.post("/api/iwencai/query")
async def api_iwencai_query(req: dict):
    """条件选股 — 自然语言或结构化条件"""
    query = req.get("query", "")
    if not query or len(query.strip()) < 2:
        return {"success": False, "error": "请输入选股条件"}
    loop = req.get("loop", False)
    perpage = req.get("perpage", 50)
    return await wencai_service.query(query.strip(), loop=loop, perpage=perpage)


@app.get("/api/iwencai/sectors")
async def api_iwencai_sectors():
    """板块热力图数据"""
    return await wencai_service.get_sectors()


@app.get("/api/iwencai/sector/{name}")
async def api_iwencai_sector_stocks(name: str):
    """概念成分股"""
    return await wencai_service.get_sector_stocks(name)


@app.get("/api/iwencai/visits/{symbol}")
async def api_iwencai_stock_visits(symbol: str):
    """个股机构调研记录"""
    return await wencai_service.get_stock_visits(symbol)


@app.post("/api/iwencai/visits/search")
async def api_iwencai_visits_search(req: dict):
    """全市场机构调研扫描"""
    query = req.get("query", "")
    perpage = req.get("perpage", 50)
    return await wencai_service.get_visits_search(query, perpage=perpage)


# ---------- 多市场板块路由 ----------

@app.get("/api/market/{market}/boards")
async def api_market_boards(market: str):
    """板块列表"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_boards()


@app.get("/api/market/{market}/board/{name}")
async def api_market_board_stocks(market: str, name: str):
    """板块成分股"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_board_stocks(name)


@app.get("/api/market/{market}/spot")
async def api_market_spot(market: str):
    """实时行情"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.get_spot()


@app.get("/api/market/{market}/search")
async def api_market_search(market: str, q: str = ""):
    """搜索"""
    provider = get_provider(market)
    if not provider:
        return {"success": False, "error": f"未知市场: {market}"}
    return await provider.search(q)


@app.get("/api/fund/detail/{code}")
async def api_fund_detail(code: str):
    """ETF 详情：基本信息 + K 线 + 持仓"""
    provider = get_provider("fund")
    if not provider:
        return {"success": False, "error": "基金服务不可用"}
    return await provider.get_etf_detail(code)


@app.get("/api/futures/kline/{symbol}")
async def api_futures_kline(symbol: str, count: int = 120):
    """期货 K 线"""
    provider = get_provider("futures")
    if not provider:
        return {"success": False, "error": "期货服务不可用"}
    return await provider.get_kline(symbol, count=count)


@app.get("/api/futures/rank/{symbol}")
async def api_futures_rank(symbol: str):
    """期货持仓龙虎榜"""
    provider = get_provider("futures")
    if not provider:
        return {"success": False, "error": "期货服务不可用"}
    return await provider.get_rank(symbol)


_RULES_PATH = Path(__file__).parent / "financial_rules.json"

@app.get("/api/stock/financial-rules")
async def api_financial_rules():
    """返回财务指标高亮规则（读取 financial_rules.json）。"""
    try:
        return json.loads(_RULES_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return {"rules": []}


@app.get("/api/agents/personas")
async def api_list_personas():
    """返回可用的投资人格列表（前端 AI 分析多选用）。"""
    return {"personas": list_personas()}


# ---------- 博主管理API ----------

@app.post("/api/blogger/identify")
async def identify_blogger(req: IdentifyRequest):
    """通过URL识别博主"""
    return await blogger_mgr.identify_from_url(req.url)


@app.post("/api/blogger/add")
async def add_blogger(info: dict):
    """保存博主"""
    return blogger_mgr.add_blogger(info)


@app.get("/api/blogger/list")
async def list_bloggers():
    return {"bloggers": blogger_mgr.list_bloggers()}


@app.delete("/api/blogger/{blogger_id}")
async def remove_blogger(blogger_id: str):
    return blogger_mgr.remove_blogger(blogger_id)


@app.post("/api/config/test-cookie")
async def test_wechat_cookie():
    """测试微信Cookie是否有效"""
    cookie = config.wechat.cookie
    if not cookie:
        return {"success": False, "status": "未配置", "message": "请先在配置页面填入微信Cookie"}
    # 用第一个博主的 biz 测试，没有博主就用固定 biz
    test_biz = ""
    if blogger_mgr.bloggers:
        test_biz = blogger_mgr.bloggers[0].get("biz", "")
    if not test_biz:
        test_biz = "MzA3MzQ2NDcyMw=="  # 任意公众号
    result = await blogger_mgr._fetch_via_getmsg(test_biz, cookie, count=1)
    if result["success"]:
        return {"success": True, "status": "有效", "message": f"Cookie有效，成功获取到文章"}
    return {"success": False, "status": "失效", "message": result.get("error", "未知错误")}


@app.post("/api/config/test-mp")
async def test_mp_credentials():
    """测试公众号后台 Cookie + Token 是否有效"""
    mp_cookie = config.wechat.mp_cookie
    mp_token = config.wechat.mp_token
    if not mp_cookie or not mp_token:
        return {"success": False, "status": "未配置", "message": "请先填入公众号后台的 Cookie 和 Token"}

    # 用一个知名公众号搜索来验证凭证
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": mp_cookie,
        "Referer": "https://mp.weixin.qq.com/",
    }
    params = {
        "action": "search_biz", "begin": "0", "count": "1",
        "query": "人民日报", "token": mp_token,
        "lang": "zh_CN", "f": "json", "ajax": "1",
    }
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
            resp = await client.get("https://mp.weixin.qq.com/cgi-bin/searchbiz", params=params)
            data = resp.json()

        ret = data.get("base_resp", {}).get("ret", -1)
        if ret == 0:
            biz_list = data.get("list", [])
            if biz_list:
                return {"success": True, "status": "有效", "message": f"凭证有效！搜索到「{biz_list[0].get('nickname', '')}」等公众号"}
            return {"success": True, "status": "有效", "message": "凭证有效（搜索返回0结果，可能被限频）"}
        elif ret == 200013:
            return {"success": False, "status": "限频", "message": "操作频率过快，请稍后再试（凭证本身可能有效）"}
        else:
            return {"success": False, "status": "失效", "message": f"凭证无效(ret={ret})，请重新登录 mp.weixin.qq.com 获取"}
    except Exception as e:
        return {"success": False, "status": "错误", "message": f"测试失败: {str(e)}"}


@app.get("/api/config/mp-login-status")
async def get_mp_login_status():
    """主动检测公众号后台登录状态，返回详细状态信息"""
    mp_cookie = config.wechat.mp_cookie
    mp_token = config.wechat.mp_token

    if not mp_cookie or not mp_token:
        return {
            "logged_in": False,
            "status": "未配置",
            "message": "请先登录公众号后台"
        }

    # 验证凭证有效性
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": mp_cookie,
        "Referer": "https://mp.weixin.qq.com/",
    }
    params = {
        "action": "search_biz", "begin": "0", "count": "1",
        "query": "测试", "token": mp_token,
        "lang": "zh_CN", "f": "json", "ajax": "1",
    }
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
            resp = await client.get("https://mp.weixin.qq.com/cgi-bin/searchbiz", params=params)
            data = resp.json()

        ret = data.get("base_resp", {}).get("ret", -1)
        if ret == 0:
            return {"logged_in": True, "status": "有效", "message": "已登录"}
        elif ret == 200013:
            return {"logged_in": True, "status": "有效", "message": "已登录（限频中）"}
        else:
            return {"logged_in": False, "status": "失效", "message": "登录已失效，请重新扫码登录"}
    except Exception as e:
        return {"logged_in": False, "status": "错误", "message": f"检测失败: {str(e)}"}


@app.websocket("/ws/mp-login")
async def websocket_mp_login(ws: WebSocket):
    """WebSocket 扫码登录公众号后台，自动获取 Cookie + Token"""
    await ws.accept()
    page = None
    context = None
    try:
        await ws.send_json({"type": "status", "message": "正在启动浏览器..."})

        # 复用 scraper 的 Playwright 实例，避免每次启动浏览器
        await scraper.initialize()
        # 登录需要新 context（无 cookies）
        context = await scraper.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await context.new_page()

        await ws.send_json({"type": "status", "message": "正在打开公众号后台登录页..."})
        await page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=30000)

        # 等待二维码出现
        qr_selector = ".login__type__container__scan__qrcode img, .qrcode img, .login_qrcode_area img, .wrp_code img"
        try:
            await page.wait_for_selector(qr_selector, state="visible", timeout=15000)
        except Exception:
            # 尝试备用：直接截取登录区域
            pass

        # 截取二维码图片
        qr_b64 = await _capture_qr_code(page, qr_selector)
        if not qr_b64:
            await ws.send_json({"type": "error", "message": "未能获取到登录二维码，请稍后重试"})
            return

        await ws.send_json({"type": "qrcode", "image": qr_b64, "message": "请用微信扫描二维码"})

        # 轮询等待登录成功（最长 120 秒）
        logged_in = False
        for i in range(120):
            await asyncio.sleep(1)

            # 检查 WebSocket 是否断开
            try:
                # 非阻塞检查是否有客户端消息（如取消）
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.05)
                    if msg.get("action") == "cancel":
                        await ws.send_json({"type": "cancelled", "message": "已取消登录"})
                        return
                except asyncio.TimeoutError:
                    pass
            except WebSocketDisconnect:
                return

            # 每 3 秒发一次心跳
            if i % 3 == 0 and i > 0:
                await ws.send_json({"type": "waiting", "elapsed": i, "message": f"等待扫码中... ({i}s)"})

            # 检查是否已经登录成功（URL 变化或页面元素变化）
            current_url = page.url
            if "cgi-bin/home" in current_url or "token=" in current_url:
                logged_in = True
                break

            # 检查是否进入了需要确认的阶段（已扫码待确认）
            try:
                scan_status = await page.evaluate("""() => {
                    // 检查是否有「已扫码」的提示
                    const body = document.body.innerText;
                    if (body.includes('已扫码') || body.includes('请在手机上确认')) return 'scanned';
                    if (body.includes('二维码已失效') || body.includes('过期')) return 'expired';
                    return 'waiting';
                }""")
                if scan_status == "scanned" and i % 3 == 0:
                    await ws.send_json({"type": "scanned", "message": "已扫码，请在手机上确认登录"})
                elif scan_status == "expired":
                    # 刷新二维码
                    await page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                    try:
                        await page.wait_for_selector(qr_selector, state="visible", timeout=10000)
                    except Exception:
                        pass
                    qr_b64 = await _capture_qr_code(page, qr_selector)
                    if qr_b64:
                        await ws.send_json({"type": "qrcode", "image": qr_b64, "message": "二维码已刷新，请重新扫码"})
            except Exception:
                pass

        if not logged_in:
            # 最后再检查一次
            current_url = page.url
            if "token=" not in current_url and "cgi-bin/home" not in current_url:
                await ws.send_json({"type": "error", "message": "登录超时（120秒），请重试"})
                return

        await ws.send_json({"type": "status", "message": "登录成功！正在提取凭证..."})

        # 等一下让页面完全加载
        await asyncio.sleep(2)
        current_url = page.url

        # 提取 token
        token_match = re.search(r"token=(\d+)", current_url)
        mp_token = token_match.group(1) if token_match else ""

        if not mp_token:
            # 尝试从页面内容或其他地方获取
            try:
                mp_token = await page.evaluate("""() => {
                    const m = document.cookie.match(/token=(\\d+)/);
                    if (m) return m[1];
                    const scripts = document.querySelectorAll('script');
                    for (const s of scripts) {
                        const tm = s.textContent.match(/token['"\\s:=]+(\\d{6,})/);
                        if (tm) return tm[1];
                    }
                    return '';
                }""")
            except Exception:
                pass

        # 提取 cookies
        cookies = await context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "qq.com" in c.get("domain", ""))

        if not cookie_str:
            await ws.send_json({"type": "error", "message": "登录成功但未能提取到 Cookie，请手动获取"})
            return

        if not mp_token:
            await ws.send_json({"type": "error", "message": "登录成功但未能提取到 Token，请手动从地址栏复制"})
            return

        # 自动保存到配置
        config.wechat.mp_cookie = cookie_str
        config.wechat.mp_token = mp_token
        # 持久化
        save_data = {}
        if CONFIG_FILE.exists():
            try:
                save_data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        save_data.setdefault("wechat", {})
        save_data["wechat"]["mp_cookie"] = cookie_str
        save_data["wechat"]["mp_token"] = mp_token
        CONFIG_FILE.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")

        await ws.send_json({
            "type": "success",
            "message": "登录成功！Cookie 和 Token 已自动保存",
            "token": mp_token,
            "cookie_len": len(cookie_str),
        })

    except WebSocketDisconnect:
        logger.info("扫码登录：客户端断开连接")
    except Exception as e:
        logger.error(f"扫码登录异常: {e}", exc_info=True)
        try:
            await ws.send_json({"type": "error", "message": f"登录异常: {str(e)}"})
        except Exception:
            pass
    finally:
        if page:
            await page.close()
        if context:
            await context.close()


async def _capture_qr_code(page, selector: str) -> str:
    """截取二维码图片，返回 base64 字符串"""
    try:
        # 方式1: 直接获取二维码 img 的 src
        qr_el = await page.query_selector(selector)
        if qr_el:
            src = await qr_el.get_attribute("src")
            if src and src.startswith("data:"):
                # 已经是 base64
                return src
            if src and src.startswith("http"):
                # 下载图片并转 base64
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(src)
                    if resp.status_code == 200:
                        b64 = base64.b64encode(resp.content).decode()
                        return f"data:image/png;base64,{b64}"
            # 方式2: 截图该元素
            screenshot = await qr_el.screenshot()
            b64 = base64.b64encode(screenshot).decode()
            return f"data:image/png;base64,{b64}"
    except Exception:
        pass

    # 方式3: 截取整个登录区域
    try:
        login_area = await page.query_selector(".login__type__container__scan, .login_qrcode_area, .wrp_code, .login_box")
        if login_area:
            screenshot = await login_area.screenshot()
            b64 = base64.b64encode(screenshot).decode()
            return f"data:image/png;base64,{b64}"
    except Exception:
        pass

    # 方式4: 截取整个页面
    try:
        screenshot = await page.screenshot()
        b64 = base64.b64encode(screenshot).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


@app.post("/api/blogger/refresh")
async def refresh_all_bloggers():
    """批量刷新所有博主的文章状态（获取最新文章标题/日期）"""
    return await blogger_mgr.refresh_all()


@app.post("/api/blogger/{blogger_id}/articles")
async def fetch_blogger_articles(blogger_id: str):
    """获取某个博主的最近文章"""
    blogger = blogger_mgr.get_blogger(blogger_id)
    if not blogger:
        return {"success": False, "error": "博主不存在"}
    return await blogger_mgr.fetch_recent_articles(blogger)


class QuickUpdateRequest(BaseModel):
    url: str


@app.put("/api/blogger/{blogger_id}/url")
async def quick_update_blogger_url(blogger_id: str, req: QuickUpdateRequest):
    """快速更新博主的文章链接（识别文章元信息并更新）"""
    blogger = blogger_mgr.get_blogger(blogger_id)
    if not blogger:
        return {"success": False, "error": "博主不存在"}

    # 用 httpx 获取新文章的元信息
    info = await blogger_mgr.identify_from_url(req.url)
    if not info.get("success"):
        return {"success": False, "error": info.get("error", "无法识别文章")}

    # 更新博主的 source_url 和文章信息
    blogger["source_url"] = req.url
    blogger["article_title"] = info.get("article_title", "")
    blogger["article_time"] = info.get("article_time", "")
    from datetime import datetime
    blogger["updated_at"] = datetime.now().isoformat()
    blogger_mgr._save()

    return {
        "success": True,
        "message": f"「{blogger['name']}」文章链接已更新",
        "blogger": blogger,
    }


# ---------- WebSocket实时任务 ----------

async def _scrape_urls(ws: WebSocket, urls: list[str]) -> list[dict]:
    """阶段1: 抓取文章列表"""
    total = len(urls)
    articles = []

    await ws.send_json({"type": "phase", "phase": "scrape", "message": f"开始抓取 {total} 篇文章..."})

    for i, url in enumerate(urls, 1):
        url = url.strip()
        if not url:
            continue

        await ws.send_json({
            "type": "progress", "current": i, "total": total,
            "message": f"[{i}/{total}] 正在抓取: {url[:60]}...",
        })

        try:
            result = await scraper.fetch_article(url)
            if result.get("success"):
                articles.append(result)
                await ws.send_json({
                    "type": "article_done", "current": i, "total": total,
                    "title": result.get("title", "未知"),
                    "author": result.get("author", "未知"),
                    "status": "success",
                })
            else:
                await ws.send_json({
                    "type": "article_done", "current": i, "total": total,
                    "title": url[:50], "author": "", "status": "failed",
                    "error": result.get("error", "未知错误"),
                })
        except Exception as e:
            await ws.send_json({
                "type": "article_done", "current": i, "total": total,
                "title": url[:50], "author": "", "status": "failed", "error": str(e),
            })

        if i < total:
            await asyncio.sleep(config.scrape_delay)

    return articles


async def _resolve_blogger_urls(ws: WebSocket, blogger_ids: list[str], mode: str = "latest_n", count: int = 5, period: str | None = None) -> list[str]:
    """将选中的博主ID解析为文章URL列表（并发获取，信号量限流）"""
    all_urls = []
    total = len(blogger_ids)

    await ws.send_json({"type": "phase", "phase": "resolve", "message": f"正在获取 {total} 位博主的最新文章..."})

    # 准备有效博主列表
    bloggers_to_fetch = []
    for bid in blogger_ids:
        blogger = blogger_mgr.get_blogger(bid)
        if not blogger:
            await ws.send_json({"type": "log", "level": "warning", "message": f"博主 {bid} 不存在，跳过"})
            continue
        bloggers_to_fetch.append(blogger)

    if not bloggers_to_fetch:
        return all_urls

    # 并发获取，信号量控制最多3个同时请求避免限频
    sem = asyncio.Semaphore(3)
    results = [None] * len(bloggers_to_fetch)

    async def fetch_one(idx, blogger):
        async with sem:
            name = blogger.get("name", "未知")
            await ws.send_json({
                "type": "log", "level": "info",
                "message": f"[{idx+1}/{total}] 正在获取「{name}」的最新文章...",
            })
            result = await blogger_mgr.fetch_recent_articles(blogger, count=count, mode=mode, period=period)
            results[idx] = (blogger, result)

    # 并发执行
    tasks = [fetch_one(i, b) for i, b in enumerate(bloggers_to_fetch)]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 收集结果
    for item in results:
        if not item:
            continue
        blogger, result = item
        name = blogger.get("name", "未知")
        if isinstance(result, dict) and result.get("success") and result.get("articles"):
            urls = [a["url"] for a in result["articles"] if a.get("url")]
            all_urls.extend(urls)
            titles = ", ".join(a.get("title", "")[:20] for a in result["articles"][:3] if a.get("title"))
            await ws.send_json({
                "type": "log", "level": "success",
                "message": f"「{name}」找到 {len(urls)} 篇: {titles}",
            })
        else:
            err = result.get("error", "未知") if isinstance(result, dict) else str(result)
            await ws.send_json({
                "type": "log", "level": "warning",
                "message": f"「{name}」获取失败: {err}",
            })

    return all_urls


@app.websocket("/ws/task")
async def websocket_task(ws: WebSocket):
    """WebSocket任务: 收集 → 生成报告 → 预览/下载 → 可选发送邮件"""
    await ws.accept()

    try:
        # --- 接收初始请求 ---
        data = await ws.receive_json()
        mode = data.get("mode", "urls")  # "urls" 或 "bloggers"

        # --- 确定要抓取的URL列表 ---
        urls = []
        if mode == "bloggers":
            blogger_ids = data.get("blogger_ids", [])
            extra_urls = data.get("extra_urls", [])
            scrape_mode = data.get("scrape_mode", "latest_n")
            scrape_count = data.get("scrape_count", 5)
            scrape_period = data.get("scrape_period", None)
            if not blogger_ids and not extra_urls:
                await ws.send_json({"type": "error", "message": "请选择至少一个博主或输入文章链接"})
                return
            if blogger_ids:
                urls = await _resolve_blogger_urls(ws, blogger_ids, mode=scrape_mode, count=scrape_count, period=scrape_period)
            # 合并手动补充的链接
            for u in extra_urls:
                u = u.strip()
                if u and u not in urls:
                    urls.append(u)
            if not urls:
                await ws.send_json({"type": "error", "message": "未能获取到任何文章链接"})
                return
            await ws.send_json({
                "type": "log", "level": "info",
                "message": f"共获取到 {len(urls)} 篇文章链接，开始抓取内容...",
            })
        else:
            urls = data.get("urls", [])
            if not urls:
                await ws.send_json({"type": "error", "message": "请输入至少一个链接"})
                return

        # --- 阶段1: 抓取文章内容 ---
        articles = await _scrape_urls(ws, urls)

        if not articles:
            await ws.send_json({"type": "error", "message": "所有文章抓取失败，请检查链接"})
            return

        # --- 通知前端: 文章收集完成，等待用户选择 ---
        await ws.send_json({
            "type": "articles_collected",
            "count": len(articles),
            "total": len(urls),
            "message": f"成功抓取 {len(articles)}/{len(urls)} 篇文章，请选择输出方式",
        })

        # --- 等待用户选择: 仅汇总 or AI分析（可指定多个投资视角）---
        choice = await ws.receive_json()
        do_analyze = choice.get("analyze", False)
        # personas: 用户勾选的视角 ID 列表；若 AI 分析但未指定，则使用全部视角
        personas = choice.get("personas") or []

        # --- 阶段2: 生成报告 ---
        analyzer = ArticleAnalyzer(config)
        if do_analyze:
            if personas:
                msg = f"正在以 {len(personas)} 个视角并行分析..."
            else:
                msg = "正在进行AI智能分析..."
            await ws.send_json({"type": "phase", "phase": "analyze", "message": msg})
            if personas:
                analysis = await analyzer.analyze_with_personas(articles, personas)
            else:
                analysis = await analyzer.analyze(articles)
        else:
            await ws.send_json({"type": "phase", "phase": "analyze", "message": "正在生成文章汇总..."})
            from datetime import datetime
            today = datetime.now().strftime("%Y年%m月%d日")
            analysis = analyzer._fallback_report(articles, today)

        if not analysis.get("success"):
            await ws.send_json({"type": "error", "message": f"生成报告失败: {analysis.get('error')}"})
            return

        report = analysis["report"]

        # 发送报告预览（前端可预览 + 下载）
        await ws.send_json({"type": "report", "report": report})

        # --- 阶段3: 等待用户后续操作（发送邮件 or 结束） ---
        # 持续监听，用户可以多次发送邮件或选择结束
        while True:
            try:
                action_data = await asyncio.wait_for(ws.receive_json(), timeout=300)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "done", "message": "会话超时，报告已在页面中保留"})
                break

            action = action_data.get("action", "")

            if action == "send_email":
                emails = action_data.get("emails", [])
                if not emails:
                    await ws.send_json({"type": "email_error", "message": "请输入至少一个收件邮箱"})
                    continue

                sender = EmailSender(config.email)
                success_list = []
                fail_list = []

                for email_addr in emails:
                    email_addr = email_addr.strip()
                    if not email_addr:
                        continue
                    await ws.send_json({"type": "phase", "phase": "email", "message": f"正在发送邮件到 {email_addr}..."})
                    email_result = sender.send(email_addr, report, len(articles))
                    if email_result["success"]:
                        success_list.append(email_addr)
                    else:
                        fail_list.append(f"{email_addr}: {email_result['error']}")

                if success_list and not fail_list:
                    await ws.send_json({"type": "email_sent", "message": f"邮件已发送到: {', '.join(success_list)}"})
                elif success_list and fail_list:
                    await ws.send_json({"type": "email_sent", "message": f"部分发送成功: {', '.join(success_list)}；失败: {'; '.join(fail_list)}"})
                else:
                    await ws.send_json({"type": "email_error", "message": f"发送失败: {'; '.join(fail_list)}"})

            elif action == "finish":
                await ws.send_json({"type": "done", "message": "任务完成"})
                break

    except WebSocketDisconnect:
        logger.info("客户端断开连接")
    except Exception as e:
        logger.error(f"任务执行异常: {e}", exc_info=True)
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ---------- 启动 ----------

@app.on_event("startup")
async def _startup():
    """启动时预加载数据。"""
    init_db()
    asyncio.create_task(StockService.preload_stock_list())

    async def _preload_fund():
        """后台预加载基金 ETF 数据。"""
        try:
            from market.fund import _get_etf_df
            await _get_etf_df()
            logger.info("基金 ETF 数据预加载完成")
        except Exception as e:
            logger.warning(f"基金数据预加载失败（首次访问时重试）: {e}")

    asyncio.create_task(_preload_fund())


@app.on_event("shutdown")
async def _shutdown():
    """关闭时清理数据库连接。"""
    close_db()
    logger.info("数据库连接已关闭")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
