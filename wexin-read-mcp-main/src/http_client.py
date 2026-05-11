"""
共享 HTTP 客户端 — 全局连接池 + 代理绕过。

所有对外 HTTP 请求（腾讯行情、AKShare、新浪等）统一走此模块：
- requests.Session 自带连接池，复用 TCP 连接
- trust_env=False 忽略系统代理环境变量（VPN/Clash/Surge 不影响）
- proxies=None 双保险，明确禁用代理

使用方式：
    from http_client import session, patch_requests

    # 直接请求
    resp = session.get(url, timeout=10)

    # 调用 AKShare（自动绕代理）
    df = patch_requests(ak.some_function, arg1=val1)
"""

import threading
import requests

session = requests.Session()
session.trust_env = False  # 不读 HTTP_PROXY / HTTPS_PROXY / NO_PROXY 等环境变量
session.proxies = {"http": None, "https": None}  # 双保险，明确禁用代理

_patch_lock = threading.Lock()


def patch_requests(func, **kwargs):
    """在绕过代理的环境下调用函数（主要用于 AKShare）。

    同时 patch requests.get/post 和 requests.Session 类，
    覆盖内部新建 Session 的调用路径（如东方财富成分股接口）。
    """
    import requests as _requests

    with _patch_lock:
        orig_get = _requests.get
        orig_post = _requests.post
        orig_Session = _requests.Session

        class _NoProxySession(_requests.Session):
            def __init__(self):
                super().__init__()
                self.trust_env = False
                self.proxies = {"http": None, "https": None}

        _requests.get = session.get
        _requests.post = session.post
        _requests.Session = _NoProxySession
        try:
            return func(**kwargs)
        finally:
            _requests.get = orig_get
            _requests.post = orig_post
            _requests.Session = orig_Session
