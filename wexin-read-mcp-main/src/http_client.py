"""共享 HTTP 客户端 — 连接池、代理绕过、自动重试、浏览器 UA。

设计要点：
- requests Session 配置了 Retry 适配器(网络层瞬时错误自动重试,连接池死连接自动换新)
- 默认带浏览器 UA(反爬常见门槛)
- patch_requests 改为「线程局部」实现:不再用全局锁串行所有 AKShare 调用,
  改为给当前调用线程独立 patch、调用完恢复。多线程下互不干扰、可真正并发。
- 国内通路 trust_env=False(屏蔽系统代理/VPN),国外 AI 通路 trust_env=True。
"""

import contextlib
import threading

import httpx
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 浏览器 UA(东财/同花顺等反爬必备)──────────────────────────────────────
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


def _build_session() -> requests.Session:
    """构造一个带 Retry / 浏览器 UA / 不走系统代理的 requests Session。"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update(_DEFAULT_HEADERS)
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,           # 退避:0.4, 0.8, 1.6 秒
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20, pool_block=False)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# ── requests Session(供 AKShare patch 使用,以及直接 GET 国内 API)─────
session = _build_session()


# ── 线程局部 patch:替代全局锁,允许 AKShare 并发 ─────────────────────────
_thread_local = threading.local()


def _is_patched() -> bool:
    return getattr(_thread_local, "patched", False)


@contextlib.contextmanager
def _patch_requests_ctx():
    """线程局部猴补丁:把 requests.get/post/Session 替换为我们的稳健 Session。

    设计为可重入(嵌套调用安全),不引入全局锁,所以多个线程可并发使用 AKShare。
    """
    if _is_patched():
        # 已被外层 patch 过,内层直接放行,避免重复保存/恢复
        yield
        return

    import requests as _requests

    orig_get = _requests.get
    orig_post = _requests.post
    orig_Session = _requests.Session

    class _NoProxyRetrySession(_requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False
            self.headers.update(_DEFAULT_HEADERS)
            retry = Retry(
                total=2, connect=2, read=2, backoff_factor=0.4,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST", "HEAD"],
                raise_on_status=False,
            )
            ad = HTTPAdapter(max_retries=retry, pool_maxsize=10, pool_block=False)
            self.mount("https://", ad)
            self.mount("http://", ad)

    _requests.get = session.get
    _requests.post = session.post
    _requests.Session = _NoProxyRetrySession
    _thread_local.patched = True
    try:
        yield
    finally:
        _requests.get = orig_get
        _requests.post = orig_post
        _requests.Session = orig_Session
        _thread_local.patched = False


def patch_requests(func, **kwargs):
    """在「无代理 + 浏览器 UA + 自动重试」上下文里调用 AKShare 函数。

    与旧版相比:不再使用全局锁,改用线程局部 patch — 多线程下可真正并发,
    不会再因为「一个慢请求卡住,后面所有 AKShare 排队等」。
    """
    with _patch_requests_ctx():
        return func(**kwargs)


# ── httpx AsyncClient(国内通路,不走代理)──────────────────────────────
_async_client: httpx.AsyncClient | None = None
_async_client_lock = threading.Lock()


def get_async_client() -> httpx.AsyncClient:
    """国内数据源用:不走系统代理,默认带浏览器 UA。"""
    global _async_client
    if _async_client is None:
        with _async_client_lock:
            if _async_client is None:
                _async_client = httpx.AsyncClient(
                    trust_env=False,
                    timeout=httpx.Timeout(10.0, connect=5.0),
                    follow_redirects=True,
                    headers=_DEFAULT_HEADERS,
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                )
    return _async_client


# ── httpx AsyncClient(国外通路,走系统代理,供 AI API 使用)────────────
_async_proxy_client: httpx.AsyncClient | None = None
_async_proxy_client_lock = threading.Lock()


def get_async_proxy_client() -> httpx.AsyncClient:
    """国外 AI API 用:走系统代理(HTTP_PROXY / HTTPS_PROXY)。"""
    global _async_proxy_client
    if _async_proxy_client is None:
        with _async_proxy_client_lock:
            if _async_proxy_client is None:
                _async_proxy_client = httpx.AsyncClient(
                    trust_env=True,
                    timeout=httpx.Timeout(120.0, connect=10.0),
                    follow_redirects=True,
                )
    return _async_proxy_client
