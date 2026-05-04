"""博主识别与文章获取模块"""

import asyncio
import re
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

BLOGGERS_FILE = Path(__file__).parent.parent / "bloggers.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class BloggerManager:
    """博主管理器"""

    def __init__(self, scraper, config=None):
        self.scraper = scraper
        self.config = config
        self.bloggers: list[dict] = []
        self._load()

    # ==================== 持久化 ====================

    def _load(self):
        if BLOGGERS_FILE.exists():
            try:
                self.bloggers = json.loads(BLOGGERS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.bloggers = []

    def _save(self):
        BLOGGERS_FILE.write_text(
            json.dumps(self.bloggers, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ==================== httpx 页面解析工具 ====================

    @staticmethod
    def _extract_blogger_info(html: str, url: str) -> dict:
        """从 httpx 获取的原始 HTML 中提取博主信息"""
        info = {"name": "未知", "biz": "", "user_name": "", "avatar": "", "source_url": url}

        m = re.search(r"bizuin:\s*JsDecode\('([^']+)'\)", html)
        if m:
            info["biz"] = m.group(1)
        else:
            m = re.search(r'var\s+biz\s*=\s*"([^"]+)"', html)
            if m:
                info["biz"] = m.group(1)
            else:
                m = re.search(r"__biz=([A-Za-z0-9=]+)", url)
                if m:
                    info["biz"] = m.group(1)

        m = re.search(r'js_wx_follow_nickname[^>]*>([^<]+)<', html)
        if m and m.group(1).strip():
            info["name"] = m.group(1).strip()
        else:
            m = re.search(r'var\s+nickname\s*=\s*htmlDecode\("([^"]+)"\)', html)
            if m:
                info["name"] = m.group(1)

        m = re.search(r"user_name:\s*JsDecode\('([^']+)'\)", html)
        if m:
            info["user_name"] = m.group(1)
        else:
            m = re.search(r'user_name\s*=\s*"(gh_[a-f0-9]+)"', html)
            if m:
                info["user_name"] = m.group(1)

        m = re.search(r"hd_head_img:\s*JsDecode\('([^']+)'\)", html)
        if m:
            info["avatar"] = m.group(1)
        else:
            m = re.search(r"ori_head_img_url:\s*JsDecode\('([^']+)'\)", html)
            if m:
                info["avatar"] = m.group(1)

        return info

    @staticmethod
    def _extract_article_meta(html: str) -> dict:
        """从 httpx 获取的原始 HTML 中提取文章元信息（标题、日期）"""
        meta = {"title": "", "publish_time": ""}

        # 方法1: og:title
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
        if m:
            meta["title"] = m.group(1)

        # 方法2: 兜底 title 标签
        if not meta["title"]:
            m = re.search(r'<title[^>]*>([^<]+)</title>', html)
            if m:
                meta["title"] = m.group(1).strip()

        # 方法3: window.__RENDER_DATA__ 或其他 JS 变量中的标题
        if not meta["title"]:
            m = re.search(r'"title"\s*:\s*"([^"]{2,100})"', html)
            if m:
                meta["title"] = m.group(1)

        # 发布时间在 JS 变量中: var ct = "1713250000"  (unix timestamp)
        m = re.search(r"var\s+ct\s*=\s*\"(\d+)\"", html)
        if m:
            try:
                ts = int(m.group(1))
                meta["publish_time"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        # 兜底: 在页面中搜索 create_time
        if not meta["publish_time"]:
            m = re.search(r"create_time\s*=\s*\"(\d+)\"", html)
            if m:
                try:
                    ts = int(m.group(1))
                    meta["publish_time"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

        return meta

    @staticmethod
    def _filter_by_mode(articles, mode, count, period):
        if mode == "latest":
            return articles[:1]
        elif mode == "latest_n":
            return articles[:count]
        elif mode == "period":
            now = datetime.now()
            delta_map = {
                "today": timedelta(days=0),
                "last_3_days": timedelta(days=3),
                "last_week": timedelta(days=7),
                "last_month": timedelta(days=30),
            }
            since = now - delta_map.get(period, timedelta(days=7))
            filtered = []
            for a in articles:
                date_str = a.get("date", "")
                if date_str:
                    try:
                        d = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                        if d >= since:
                            filtered.append(a)
                    except ValueError:
                        filtered.append(a)
                else:
                    filtered.append(a)
            return filtered
        return articles[:count]

    # ==================== 博主识别 ====================

    async def identify_from_url(self, url: str) -> dict:
        """通过微信文章 URL 识别博主身份（httpx，不触发反爬）"""
        try:
            async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20) as client:
                resp = await client.get(url)
                html = resp.text

            if "环境异常" in html or "请在微信客户端打开链接" in html:
                return {"success": False, "error": "页面需要微信环境验证，请稍后重试"}

            info = self._extract_blogger_info(html, url)
            meta = self._extract_article_meta(html)

            if not info.get("name") or info["name"] == "未知":
                return {"success": False, "error": "无法识别博主信息，请确认链接是否为微信公众号文章"}

            # 附带文章元信息
            info["article_title"] = meta["title"]
            info["article_time"] = meta["publish_time"]

            return {"success": True, **info, "error": None}

        except Exception as e:
            logger.error(f"识别博主失败: {e}")
            return {"success": False, "error": f"请求失败: {str(e)}"}

    # ==================== 博主 CRUD ====================

    def list_bloggers(self) -> list[dict]:
        return self.bloggers

    def add_blogger(self, info: dict) -> dict:
        """添加博主（按 biz 去重，已存在则自动更新 source_url）"""
        biz = info.get("biz", "")

        for b in self.bloggers:
            if biz and b["biz"] == biz:
                # 已存在 → 更新信息和文章链接
                b.update({
                    "name": info.get("name", b["name"]),
                    "avatar": info.get("avatar", b.get("avatar", "")),
                    "user_name": info.get("user_name", b.get("user_name", "")),
                    "source_url": info.get("source_url", b.get("source_url", "")),
                    "article_title": info.get("article_title", ""),
                    "article_time": info.get("article_time", ""),
                    "updated_at": datetime.now().isoformat(),
                })
                self._save()
                return {
                    "success": True,
                    "message": f"博主「{b['name']}」已存在，文章链接已更新为最新",
                    "blogger": b,
                }

        blogger = {
            "id": f"b_{len(self.bloggers) + 1}_{int(datetime.now().timestamp())}",
            "name": info.get("name", "未知"),
            "biz": biz,
            "user_name": info.get("user_name", ""),
            "avatar": info.get("avatar", ""),
            "source_url": info.get("source_url", ""),
            "article_title": info.get("article_title", ""),
            "article_time": info.get("article_time", ""),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self.bloggers.append(blogger)
        self._save()
        return {"success": True, "message": f"博主「{blogger['name']}」已添加", "blogger": blogger}

    def remove_blogger(self, blogger_id: str) -> dict:
        before = len(self.bloggers)
        self.bloggers = [b for b in self.bloggers if b["id"] != blogger_id]
        if len(self.bloggers) < before:
            self._save()
            return {"success": True}
        return {"success": False, "error": "博主不存在"}

    def get_blogger(self, blogger_id: str) -> dict | None:
        for b in self.bloggers:
            if b["id"] == blogger_id:
                return b
        return None

    # ==================== 自动刷新（进入博主管理页时触发） ====================

    async def refresh_all(self) -> dict:
        """
        并发刷新所有博主的文章状态（信号量限流避免微信限频）。
        通过 fetch_recent_articles() 获取每个博主的最新文章列表。
        """
        if not self.bloggers:
            return {"success": True, "bloggers": []}

        # 信号量限制最多2个并发请求，避免微信 200013 限频
        sem = asyncio.Semaphore(2)

        async def _limited_refresh(blogger):
            async with sem:
                return await self._refresh_one_via_api(blogger)

        tasks = [_limited_refresh(b) for b in self.bloggers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        updated = []
        changed = False
        for b, result in zip(self.bloggers, results):
            if isinstance(result, dict) and result.get("success"):
                articles = result.get("articles", [])
                if articles:
                    newest = articles[0]
                    b["article_title"] = newest.get("title", "")
                    b["article_time"] = newest.get("date", "")
                    if newest.get("url"):
                        b["source_url"] = newest["url"]
                b["refresh_status"] = "ok"
                b.pop("refresh_error", None)  # 清除旧错误
                b["updated_at"] = datetime.now().isoformat()
                changed = True
            else:
                b["refresh_status"] = "error"
                err_msg = result.get("error") if isinstance(result, dict) else str(result)
                b["refresh_error"] = err_msg[:100] if err_msg else "未知错误"
            updated.append(b)

        if changed:
            self._save()

        # 检查是否有登录失效的博主
        login_expired_count = 0
        for b in updated:
            if b.get("refresh_status") == "error" and "Cookie已失效" in b.get("refresh_error", ""):
                login_expired_count += 1

        return {
            "success": True,
            "bloggers": updated,
            "login_required": login_expired_count > 0,
            "login_expired_count": login_expired_count,
        }

    async def _refresh_one_via_api(self, blogger: dict) -> dict:
        """刷新单个博主：通过 API 获取最新文章列表"""
        result = await self.fetch_recent_articles(blogger, count=1, mode="latest")
        return result

    # ==================== 获取文章列表（任务执行时调用） ====================

    def _get_wechat_cookie(self) -> str:
        """获取配置中的微信读者端 cookie"""
        if self.config and hasattr(self.config, 'wechat'):
            return self.config.wechat.cookie or ""
        return ""

    def _get_mp_credentials(self) -> tuple[str, str]:
        """获取公众号后台 cookie 和 token"""
        if self.config and hasattr(self.config, 'wechat'):
            return (self.config.wechat.mp_cookie or "", self.config.wechat.mp_token or "")
        return ("", "")

    async def fetch_recent_articles(self, blogger: dict, count: int = 5, mode: str = "latest_n", period: str | None = None) -> dict:
        """获取博主最新文章列表 — 优先公众号后台API → 读者端Cookie → 降级到已有链接"""
        name = blogger.get("name", "")
        biz = blogger.get("biz", "")

        # ========== 优先路径1: 公众号后台 API (方案A) ==========
        mp_cookie, mp_token = self._get_mp_credentials()
        if mp_cookie and mp_token:
            mp_result = await self._fetch_via_mp_backend(blogger, mp_cookie, mp_token, count)
            if mp_result["success"] and mp_result["articles"]:
                # 更新博主的最新文章信息
                newest = mp_result["articles"][0]
                blogger["article_title"] = newest.get("title", "")
                blogger["article_time"] = newest.get("date", "")
                if newest.get("url"):
                    blogger["source_url"] = newest["url"]
                blogger["updated_at"] = datetime.now().isoformat()
                self._save()
                mp_result["articles"] = self._filter_by_mode(mp_result["articles"], mode, count, period)
                return mp_result
            # token/cookie 失效时记录，继续降级
            err = mp_result.get("error", "")
            if "token" in err.lower() or "登录" in err or "invalid" in err.lower():
                logger.warning(f"公众号后台凭证已失效: {err}")

        # ========== 优先路径2: 读者端 Cookie (方案B) ==========
        cookie = self._get_wechat_cookie()
        if cookie and biz:
            api_result = await self._fetch_via_getmsg(biz, cookie, count)
            if api_result["success"] and api_result["articles"]:
                newest = api_result["articles"][0]
                blogger["article_title"] = newest.get("title", "")
                blogger["article_time"] = newest.get("date", "")
                if newest.get("url"):
                    blogger["source_url"] = newest["url"]
                blogger["updated_at"] = datetime.now().isoformat()
                self._save()
                api_result["articles"] = self._filter_by_mode(api_result["articles"], mode, count, period)
                return api_result
            if "cookie" in api_result.get("error", "").lower() or "session" in api_result.get("error", "").lower():
                logger.warning(f"微信Cookie已失效: {api_result['error']}")

        # ========== 降级路径已禁用 ==========
        # ❌ 不再返回旧数据，必须通过 Cookie/API 获取最新文章
        source_url = blogger.get("source_url", "")
        if not source_url:
            return {"success": False, "articles": [], "error": "该博主没有关联文章链接，且未配置公众号后台凭证"}

        # Cookie 失效时直接报错，不返回旧数据
        return {
            "success": False,
            "articles": [],
            "error": f"Cookie已失效，无法获取「{blogger.get('name', '该博主')}」的最新文章，请重新配置 Cookie"
        }

    # ==================== 方案A: 公众号后台 API ====================

    async def _fetch_via_mp_backend(self, blogger: dict, mp_cookie: str, mp_token: str, count: int = 5) -> dict:
        """通过 mp.weixin.qq.com 后台 API 获取文章列表"""
        name = blogger.get("name", "")
        fakeid = blogger.get("fakeid", "")

        headers = {
            **_HEADERS,
            "Cookie": mp_cookie,
            "Referer": "https://mp.weixin.qq.com/",
        }

        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
                # 如果没有 fakeid，先通过名称搜索获取
                if not fakeid and name:
                    search_result = await self._mp_search_biz(client, name, mp_token)
                    if not search_result["success"]:
                        return search_result
                    fakeid = search_result["fakeid"]
                    # 持久化 fakeid 到博主数据
                    blogger["fakeid"] = fakeid
                    self._save()

                if not fakeid:
                    return {"success": False, "articles": [], "error": "未找到该公众号的 fakeid"}

                # 优先使用 appmsgpublish（包含所有类型: 图文/图片/视频等）
                pub_result = await self._mp_list_published(client, fakeid, mp_token, count)
                if pub_result["success"] and pub_result["articles"]:
                    return pub_result

                # 降级到传统 appmsg 接口（仅图文 type=9）
                return await self._mp_list_articles(client, fakeid, mp_token, count)

        except Exception as e:
            logger.error(f"公众号后台API调用失败: {e}")
            return {"success": False, "articles": [], "error": str(e)}

    async def _mp_search_biz(self, client: httpx.AsyncClient, query: str, token: str) -> dict:
        """搜索公众号，获取 fakeid"""
        url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
        params = {
            "action": "search_biz",
            "begin": "0",
            "count": "5",
            "query": query,
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }
        try:
            resp = await client.get(url, params=params)
            data = resp.json()

            if data.get("base_resp", {}).get("ret") == 200013:
                return {"success": False, "fakeid": "", "error": "操作频率过快，请稍后再试"}
            if data.get("base_resp", {}).get("ret") != 0:
                ret = data.get("base_resp", {}).get("ret", -1)
                errmsg = data.get("base_resp", {}).get("err_msg", "")
                if ret == -1 or "invalid" in errmsg.lower():
                    return {"success": False, "fakeid": "", "error": f"Token/Cookie已失效(ret={ret})，请重新登录公众号后台获取"}
                return {"success": False, "fakeid": "", "error": f"搜索失败: ret={ret}, {errmsg}"}

            biz_list = data.get("list", [])
            if not biz_list:
                return {"success": False, "fakeid": "", "error": f"未搜索到公众号「{query}」"}

            # 优先精确匹配
            for item in biz_list:
                if item.get("nickname", "") == query:
                    return {"success": True, "fakeid": item["fakeid"]}

            # 否则取第一个
            return {"success": True, "fakeid": biz_list[0]["fakeid"]}

        except json.JSONDecodeError:
            return {"success": False, "fakeid": "", "error": "返回数据非JSON，Cookie可能已失效"}

    async def _mp_list_published(self, client: httpx.AsyncClient, fakeid: str, token: str, count: int = 5) -> dict:
        """通过 appmsgpublish 获取已发布内容（包含图文、图片、视频等所有类型）"""
        url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
        params = {
            "sub": "list",
            "search_field": "null",
            "begin": "0",
            "count": str(min(count, 20)),
            "fakeid": fakeid,
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }
        try:
            resp = await client.get(url, params=params)
            data = resp.json()

            base_resp = data.get("base_resp", {})
            ret = base_resp.get("ret", -1)

            if ret == 200013:
                return {"success": False, "articles": [], "error": "操作频率过快，请稍后再试"}
            if ret != 0:
                errmsg = base_resp.get("err_msg", "")
                if ret == -1 or "invalid" in errmsg.lower():
                    return {"success": False, "articles": [], "error": f"Token/Cookie已失效(ret={ret})，请重新获取"}
                return {"success": False, "articles": [], "error": f"获取发布列表失败: ret={ret}, {errmsg}"}

            # publish_page 可能是 JSON 字符串，需要二次解析
            publish_page = data.get("publish_page", {})
            if isinstance(publish_page, str):
                try:
                    publish_page = json.loads(publish_page)
                except json.JSONDecodeError:
                    return {"success": False, "articles": [], "error": "publish_page 解析失败"}

            articles = []
            for item in publish_page.get("publish_list", []):
                # publish_info 也可能是 JSON 字符串
                pub_info = item.get("publish_info", {})
                if isinstance(pub_info, str):
                    try:
                        pub_info = json.loads(pub_info)
                    except json.JSONDecodeError:
                        continue

                # 发布时间: 优先 sent_info.time
                sent_info = pub_info.get("sent_info", {})
                pub_time = sent_info.get("time", 0)

                # 遍历该次发布中的所有文章（可能多图文）
                for appmsg in pub_info.get("appmsg_info", []):
                    title = appmsg.get("title", "")
                    content_url = appmsg.get("content_url", "").replace("&amp;", "&").replace("\\/", "/")
                    cover = appmsg.get("cover_url", "") or appmsg.get("cover", "")
                    digest = appmsg.get("digest", "")
                    is_deleted = appmsg.get("is_deleted", False)

                    if is_deleted or not content_url:
                        continue

                    date_str = ""
                    if pub_time:
                        try:
                            date_str = datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            pass

                    articles.append({
                        "title": title or "(无标题)",
                        "url": content_url,
                        "date": date_str,
                        "cover": cover,
                        "digest": digest,
                    })

            if articles:
                return {"success": True, "articles": articles[:count], "error": None, "source": "mp_publish"}
            return {"success": False, "articles": [], "error": "发布列表中无文章"}

        except json.JSONDecodeError:
            return {"success": False, "articles": [], "error": "返回数据非JSON，Cookie可能已失效"}

    async def _mp_list_articles(self, client: httpx.AsyncClient, fakeid: str, token: str, count: int = 5) -> dict:
        """通过 fakeid 获取公众号的文章列表"""
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        params = {
            "action": "list_ex",
            "begin": "0",
            "count": str(min(count, 20)),
            "fakeid": fakeid,
            "type": "9",
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }
        try:
            resp = await client.get(url, params=params)
            data = resp.json()

            base_resp = data.get("base_resp", {})
            ret = base_resp.get("ret", -1)

            if ret == 200013:
                return {"success": False, "articles": [], "error": "操作频率过快，请稍后再试"}
            if ret != 0:
                errmsg = base_resp.get("err_msg", "")
                if ret == -1 or "invalid" in errmsg.lower():
                    return {"success": False, "articles": [], "error": f"Token/Cookie已失效(ret={ret})，请重新获取"}
                return {"success": False, "articles": [], "error": f"获取文章失败: ret={ret}, {errmsg}"}

            app_msg_list = data.get("app_msg_list", [])
            articles = []
            for item in app_msg_list:
                title = item.get("title", "")
                link = item.get("link", "")
                cover = item.get("cover", "")
                digest = item.get("digest", "")
                update_time = item.get("update_time", 0)

                date_str = ""
                if update_time:
                    try:
                        date_str = datetime.fromtimestamp(int(update_time)).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                if link:
                    articles.append({
                        "title": title or "(无标题)",
                        "url": link,
                        "date": date_str,
                        "cover": cover,
                        "digest": digest,
                    })

            if articles:
                return {"success": True, "articles": articles[:count], "error": None, "source": "mp_backend"}
            return {"success": False, "articles": [], "error": "该公众号暂无文章"}

        except json.JSONDecodeError:
            return {"success": False, "articles": [], "error": "返回数据非JSON，Cookie可能已失效"}

    async def _fetch_via_getmsg(self, biz: str, cookie: str, count: int = 5) -> dict:
        """通过微信 profile_ext getmsg API 获取公众号历史文章"""
        url = "https://mp.weixin.qq.com/mp/profile_ext"
        params = {
            "action": "getmsg",
            "__biz": biz,
            "f": "json",
            "offset": "0",
            "count": str(min(count, 10)),
            "is_ok": "1",
            "scene": "124",
            "uin": "",
            "key": "",
        }
        headers = {
            **_HEADERS,
            "Cookie": cookie,
            "Referer": f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={biz}&scene=124",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
                resp = await client.get(url, params=params)

            data = resp.json()
            ret = data.get("ret", -1)

            if ret != 0:
                errmsg = data.get("errmsg", "未知错误")
                if ret == -3 or "session" in errmsg.lower():
                    return {"success": False, "articles": [], "error": f"Cookie已过期(ret={ret})，请重新获取"}
                return {"success": False, "articles": [], "error": f"API错误: ret={ret}, {errmsg}"}

            # 解析文章列表
            msg_list_str = data.get("general_msg_list", "")
            if not msg_list_str:
                return {"success": False, "articles": [], "error": "返回数据中无文章列表"}

            msg_list = json.loads(msg_list_str)
            articles = []

            for msg in msg_list.get("list", []):
                comm = msg.get("comm_msg_info", {})
                msg_type = comm.get("type", 0)

                # 发布时间（所有消息类型通用）
                send_time = comm.get("datetime", 0)
                date_str = ""
                if send_time:
                    try:
                        date_str = datetime.fromtimestamp(send_time).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                info = msg.get("app_msg_ext_info", {})
                if info:
                    # 标准图文消息
                    title = info.get("title", "")
                    content_url = info.get("content_url", "").replace("&amp;", "&")
                    cover = info.get("cover", "")
                    digest = info.get("digest", "")

                    if title and content_url:
                        articles.append({
                            "title": title,
                            "url": content_url,
                            "date": date_str,
                            "cover": cover,
                            "digest": digest,
                        })

                    # 处理多图文（同一条消息中的子文章）
                    for sub in info.get("multi_app_msg_item_list", []):
                        sub_title = sub.get("title", "")
                        sub_url = sub.get("content_url", "").replace("&amp;", "&")
                        if sub_title and sub_url:
                            articles.append({
                                "title": sub_title,
                                "url": sub_url,
                                "date": date_str,
                                "cover": sub.get("cover", ""),
                                "digest": sub.get("digest", ""),
                            })
                else:
                    # 非图文消息（图片、视频、文字等）
                    # 尝试从 comm_msg_info 中提取可用信息
                    content = comm.get("content", "")
                    # 图片消息 type=3, 视频消息 type=62, 文字消息 type=1
                    if msg_type == 1 and content:
                        # 纯文字消息，以内容前20字作为标题
                        articles.append({
                            "title": content[:20].strip(),
                            "url": "",
                            "date": date_str,
                            "cover": "",
                            "digest": content[:100],
                        })
                    elif msg_type in (3, 62):
                        # 图片或视频消息，记录但可能无法抓取
                        label = "图片消息" if msg_type == 3 else "视频消息"
                        logger.debug(f"跳过{label}: {date_str}")

            if articles:
                return {"success": True, "articles": articles[:count], "error": None}
            return {"success": False, "articles": [], "error": "解析到0篇文章"}

        except json.JSONDecodeError:
            return {"success": False, "articles": [], "error": "返回数据非JSON，可能Cookie已失效"}
        except Exception as e:
            logger.error(f"getmsg API 调用失败: {e}")
            return {"success": False, "articles": [], "error": str(e)}
