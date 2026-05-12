"""
共享 HTTP 客户端 — 全局连接池 + 代理绕过。

- requests.Session：供 AKShare（patch_requests）使用，trust_env=False 忽略系统代理
- httpx.AsyncClient：供直接 async 请求使用，无 requests proxies=None 的 20s 延迟 bug
"""

import threading
import requests
import httpx

# ── requests Session（供 AKShare patch 使用）──────────────────────────────
# 注意：不设 proxies={"http": None}，否则 macOS 上每次请求会有 ~20s 延迟。
# trust_env=False 已足够屏蔽系统代理。
session = requests.Session()
session.trust_env = False

_patch_lock = threading.Lock()


def patch_requests(func, **kwargs):
    """在绕过代理的环境下调用 AKShare 函数。

    patch requests.get/post 和 requests.Session，覆盖内部新建 Session 的路径。
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

        _requests.get = session.get
        _requests.post = session.post
        _requests.Session = _NoProxySession
        try:
            return func(**kwargs)
        finally:
            _requests.get = orig_get
            _requests.post = orig_post
            _requests.Session = orig_Session


# ── httpx AsyncClient（供直接 async HTTP 请求使用）────────────────────────
_async_client: httpx.AsyncClient | None = None
_async_client_lock = threading.Lock()


def get_async_client() -> httpx.AsyncClient:
    """返回全局共享的 httpx.AsyncClient 单例。首次调用时初始化。"""
    global _async_client
    if _async_client is None:
        with _async_client_lock:
            if _async_client is None:
                _async_client = httpx.AsyncClient(
                    trust_env=False,
                    timeout=httpx.Timeout(10.0),
                    follow_redirects=True,
                )
    return _async_client
