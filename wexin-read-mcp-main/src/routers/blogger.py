"""博主管理 + WebSocket 任务路由"""
import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from playwright.async_api import async_playwright

from analyzer import ArticleAnalyzer
from emailer import EmailSender
from state import config, scraper, blogger_mgr, CONFIG_FILE

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------- 数据模型 ----------

class IdentifyRequest(BaseModel):
    url: str


class QuickUpdateRequest(BaseModel):
    url: str


# ---------- 辅助函数 ----------

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


# ---------- 博主管理API ----------

@router.post("/api/blogger/identify")
async def identify_blogger(req: IdentifyRequest):
    """通过URL识别博主"""
    return await blogger_mgr.identify_from_url(req.url)


@router.post("/api/blogger/add")
async def add_blogger(info: dict):
    """保存博主"""
    return blogger_mgr.add_blogger(info)


@router.get("/api/blogger/list")
async def list_bloggers():
    return {"bloggers": blogger_mgr.list_bloggers()}


@router.delete("/api/blogger/{blogger_id}")
async def remove_blogger(blogger_id: str):
    return blogger_mgr.remove_blogger(blogger_id)


@router.post("/api/blogger/refresh")
async def refresh_all_bloggers():
    """批量刷新所有博主的文章状态（获取最新文章标题/日期）"""
    return await blogger_mgr.refresh_all()


@router.post("/api/blogger/{blogger_id}/articles")
async def fetch_blogger_articles(blogger_id: str):
    """获取某个博主的最近文章"""
    blogger = blogger_mgr.get_blogger(blogger_id)
    if not blogger:
        return {"success": False, "error": "博主不存在"}
    return await blogger_mgr.fetch_recent_articles(blogger)


@router.put("/api/blogger/{blogger_id}/url")
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


# ---------- WebSocket: 扫码登录 ----------

@router.websocket("/ws/mp-login")
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


# ---------- WebSocket: 实时任务 ----------

@router.websocket("/ws/task")
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

        # --- 阶段1.5: AI 股票提及扫描（不阻塞主流程） ---
        mentions = []
        try:
            analyzer = ArticleAnalyzer(config)
            scan_result = await analyzer.extract_mentions(articles)
            if scan_result.get("success"):
                mentions = scan_result.get("mentions", [])
                logger.info(f"股票提及扫描完成: 发现 {len(mentions)} 条")
        except Exception as e:
            logger.warning(f"股票提及扫描失败（不影响主流程）: {e}")

        # --- 通知前端: 文章收集完成，等待用户选择 ---
        await ws.send_json({
            "type": "articles_collected",
            "count": len(articles),
            "total": len(urls),
            "message": f"成功抓取 {len(articles)}/{len(urls)} 篇文章，请选择输出方式",
            "mentions": mentions,
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
