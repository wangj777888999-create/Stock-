"""配置管理路由"""
import json
from fastapi import APIRouter
from pydantic import BaseModel
from state import config, CONFIG_FILE, blogger_mgr

router = APIRouter()


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


# ---------- 配置API ----------

@router.post("/api/config")
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


@router.get("/api/config")
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
        "mp_token": "******" if config.wechat.mp_token else "",
    }


@router.post("/api/config/test-cookie")
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


@router.post("/api/config/test-mp")
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
        import logging
        logging.getLogger(__name__).error(f"测试公众号凭证异常: {e}", exc_info=True)
        return {"success": False, "status": "错误", "message": "测试失败，请检查网络连接后重试"}


@router.get("/api/config/mp-login-status")
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
        import logging
        logging.getLogger(__name__).error(f"检测登录状态异常: {e}", exc_info=True)
        return {"logged_in": False, "status": "错误", "message": "检测失败，请检查网络连接后重试"}
