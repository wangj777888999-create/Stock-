"""Playwright浏览器爬虫 — 带重试与反检测"""

import asyncio
import logging

from playwright.async_api import async_playwright, Browser, BrowserContext

try:
    from .parser import WeixinParser
except ImportError:
    from parser import WeixinParser

logger = logging.getLogger(__name__)

# 最大重试次数
_MAX_RETRIES = 3
# 每次重试间隔（秒）
_RETRY_DELAY = 2


class WeixinScraper:
    """微信文章爬虫"""

    def __init__(self):
        self.parser = WeixinParser()
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def initialize(self):
        """初始化浏览器（若已初始化则跳过）"""
        if self.browser:
            return
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            proxy={"server": "direct"},  # 绕过系统代理，避免 VPN 干扰国内站点访问
            java_script_enabled=True,
        )
        # 隐藏自动化标志
        await self.context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

    async def fetch_article(self, url: str) -> dict:
        """
        抓取微信文章内容，失败自动重试（最多 _MAX_RETRIES 次）。
        """
        last_error = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            result = await self._try_fetch(url)
            if result.get("success"):
                return result

            last_error = result.get("error", "未知错误")
            # 仅可重试的错误才重试（超时、网络错误）
            if "Timeout" in last_error or "net::" in last_error or "ERR_" in last_error:
                logger.warning(
                    f"抓取失败(第{attempt}次), 将重试: {last_error[:80]}"
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY * attempt)
                continue
            # 其它错误（如页面内容解析问题）直接返回
            break

        return {"success": False, "error": last_error}

    async def _try_fetch(self, url: str) -> dict:
        """单次抓取尝试"""
        try:
            await self.initialize()
            page = await self.context.new_page()
            try:
                # domcontentloaded 比 networkidle 更快更稳定
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # 检测微信反爬拦截
                blocked = await page.evaluate(
                    "() => document.body.innerText.includes('环境异常')"
                )
                if blocked:
                    return {"success": False, "error": "微信环境异常验证，请稍后重试"}

                # 等待正文容器出现（微信 JS 渲染需要时间）
                try:
                    await page.wait_for_selector(
                        "#js_content", state="attached", timeout=15000
                    )
                except Exception:
                    pass  # 部分文章可能没有此元素，继续尝试解析

                # 等待标题元素渲染（JS 动态创建，晚于 DOM 加载）
                try:
                    await page.wait_for_selector(
                        "#js_text_title, #activity-name", timeout=8000
                    )
                except Exception:
                    # 标题元素超时也不阻塞，后面会用 og:title 兜底
                    await page.wait_for_timeout(2000)

                # 额外等待确保 JS 渲染完成
                await page.wait_for_timeout(2000)

                # 优先通过 JS 直接从 DOM 提取结构化数据（最稳定）
                extracted = await page.evaluate("""() => {
                    // 标题: 新版 #js_text_title，旧版 #activity-name
                    const titleEl = document.getElementById('js_text_title')
                                 || document.getElementById('activity-name');
                    const title = titleEl ? titleEl.textContent.trim() : '';

                    // 作者/公众号名
                    const nickEl = document.querySelector('.wx_follow_nickname')
                                || document.getElementById('js_name');
                    const author = nickEl ? nickEl.textContent.trim() : '';

                    // 发布时间
                    const timeEl = document.getElementById('publish_time');
                    const publishTime = timeEl ? timeEl.textContent.trim() : '';

                    // 正文内容（保留图片，按原文图文顺序输出）
                    const contentEl = document.getElementById('js_content');
                    let content = '';
                    const images = [];
                    if (contentEl) {
                        const clone = contentEl.cloneNode(true);
                        clone.querySelectorAll('script,style').forEach(e => e.remove());
                        // 递归遍历节点，文本和图片按顺序输出
                        function walk(node) {
                            for (const child of node.childNodes) {
                                if (child.nodeType === 3) { // 文本节点
                                    const t = child.textContent.replace(/ {2,}/g, ' ');
                                    if (t.trim()) content += t;
                                } else if (child.nodeType === 1) { // 元素节点
                                    const tag = child.tagName.toLowerCase();
                                    if (tag === 'img') {
                                        const src = child.getAttribute('data-src') || child.getAttribute('src') || '';
                                        if (src && !src.startsWith('data:')) {
                                            content += '\\n\\n![img](' + src + ')\\n\\n';
                                            images.push(src);
                                        }
                                    } else if (tag === 'br') {
                                        content += '\\n';
                                    } else if (['p','div','section','h1','h2','h3','h4','blockquote'].includes(tag)) {
                                        content += '\\n';
                                        walk(child);
                                        content += '\\n';
                                    } else {
                                        walk(child);
                                    }
                                }
                            }
                        }
                        walk(clone);
                        content = content.replace(/\\n{3,}/g, '\\n\\n').trim();
                    }

                    // 封面图
                    const ogImg = document.querySelector("meta[property='og:image']");
                    const coverUrl = ogImg ? ogImg.getAttribute('content') : '';

                    // og:title 兜底
                    const ogTitle = document.querySelector("meta[property='og:title']");
                    const ogTitleText = ogTitle ? ogTitle.getAttribute('content') : '';

                    return {
                        title: title || ogTitleText || '',
                        author,
                        publishTime,
                        content,
                        coverUrl,
                        images,
                    };
                }""")

                title = extracted.get("title", "")
                author = extracted.get("author", "")
                content = extracted.get("content", "")

                # 如果 #js_content 内容为空，尝试从 og:description 或 digest 获取摘要
                if not content:
                    ogDesc = await page.evaluate(
                        "() => document.querySelector(\"meta[property='og:description']\")?.getAttribute('content') || ''"
                    )
                    if ogDesc:
                        content = f"[图文内容摘要]\n{ogDesc}"
                    else:
                        # 检查页面是否有任何文本内容
                        pageText = await page.evaluate(
                            "() => document.body?.innerText?.slice(0, 500) || ''"
                        )
                        if pageText and len(pageText) > 50:
                            content = f"[页面内容]\n{pageText}"
                        else:
                            return {"success": False, "error": "未找到正文内容，页面可能未完全加载"}

                return {
                    "success": True,
                    "title": title or "未知标题",
                    "author": author or "未知作者",
                    "publish_time": extracted.get("publishTime", ""),
                    "content": content,
                    "images": extracted.get("images", []),
                    "cover_url": extracted.get("coverUrl", ""),
                    "error": None,
                }
            finally:
                await page.close()

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def cleanup(self):
        """清理资源"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.context = None
        self.browser = None
        self.playwright = None
