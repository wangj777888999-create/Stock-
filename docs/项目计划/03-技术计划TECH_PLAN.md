# 股票信息平台 - 整体技术规划与改进方案

| 项目 | 内容 |
|------|------|
| 项目名称 | 股票信息平台 (wexin-read-mcp-main) |
| 文档版本 | v1.0 |
| 创建日期 | 2026-04-21 |
| 修改日期 | 2026-04-21 |
| 状态 | 待评审 |

---

## 一、项目现状总览

### 1.1 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                         app.py (~750行)                      │
│     混合职责: WebSocket / API路由 / 配置管理 / 扫码登录       │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌─────────────┐ ┌──────────┐ ┌──────────┐
│stock_service│ │ blogger   │ │ scraper  │
│  (~420行)    │ │ (~560行)  │ │ (~220行) │
│  股票数据    │ │ 博主管理  │ │ 爬虫     │
└─────────────┘ └──────────┘ └──────────┘
        │             │             │
        ▼             ▼             ▼
┌─────────────┐ ┌──────────┐ ┌──────────┐
│ AKShare API │ │ 微信API   │ │Playwright│
│  腾讯/东方  │ │ mp后台   │ │ 浏览器   │
└─────────────┘ └──────────┘ └──────────┘
```

### 1.2 代码规模统计

| 模块 | 文件大小 | 行数 | 职责 |
|------|----------|------|------|
| app.py | 29KB | ~750 | Web服务主入口 |
| blogger.py | 22KB | ~560 | 博主管理与文章获取 |
| stock_service.py | 16KB | ~420 | A股数据服务 |
| scraper.py | 9KB | ~220 | Playwright爬虫 |
| analyzer.py | 4.8KB | ~150 | AI分析报告生成 |
| emailer.py | 3.5KB | ~100 | 邮件发送 |
| config.py | 1.8KB | ~60 | 配置管理 |
| parser.py | 2.3KB | ~75 | HTML解析器 |
| server.py | 2.2KB | ~70 | MCP服务器入口 |
| stock_utils.py | 2.8KB | ~108 | 工具函数 |

**总计**: ~2523 行 Python 代码

---

## 二、问题分析

### 2.1 安全性问题 (🔴 严重)

| 问题 | 严重程度 | 位置 | 描述 |
|------|----------|------|------|
| 敏感信息明文存储 | 🔴 严重 | user_config.json | 邮箱密码、微信Cookie明文存储 |
| 缺少 .gitignore | 🔴 严重 | 项目根目录 | 可能提交敏感配置到git |
| 无权限控制 | 🟡 中等 | API层 | 所有API无认证机制 |
| SQL/注入风险 | 🟢 低 | 各模块 | 使用参数化查询，风险较低 |

**敏感信息示例**:
```json
// user_config.json 当前存储方式
{
  "email": {
    "sender_password": "LGhACJUp2WMsn7Hf"  // 明文！
  },
  "wechat": {
    "mp_cookie": "ua_id=...",  // 明文！
    "mp_token": "1988759670"
  }
}
```

### 2.2 模块耦合问题 (🟡 中等)

| 问题 | 位置 | 描述 |
|------|------|------|
| app.py 职责过重 | app.py | 混合了 WebSocket / API / 配置 / 扫码登录 |
| blogger.py 过大 | blogger.py | 混合了 CRUD / 文章获取 / HTML解析 |
| 直接依赖 | 多个模块 | scraper 被 blogger 直接实例化 |
| 配置传递 | app.py:31-32 | 全局状态直接传递 |

**耦合示例**:
```python
# app.py 中直接创建依赖
scraper = WeixinScraper()  # 硬编码依赖
config = AppConfig.from_env()
blogger_mgr = BloggerManager(scraper, config)  # 直接注入
stock_service = StockService()
```

### 2.3 并发性能问题 (🟡 中等)

| 问题 | 位置 | 描述 |
|------|------|------|
| httpx 无连接复用 | stock_service.py | 每次请求创建新连接 |
| 浏览器实例无复用 | scraper.py | 每次抓取创建新页面 |
| 缓存无持久化 | stock_utils.py | TTLCache 仅内存，服务重启丢失 |
| 并发控制硬编码 | config.py:39 | max_concurrent_scrape=3 写死 |

**性能测试结果**:

| 接口 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| search_stock | 8.441秒 | 0.005秒 | 1690x |
| get_realtime_quote | 0.076秒 | 0.076秒 | - |
| 20并发吞吐量 | - | 3191/秒 | - |

### 2.4 内容安全问题 (🟡 中等)

| 问题 | 位置 | 描述 |
|------|------|------|
| 外部内容直接执行 | 多处 | 网页内容未做安全过滤 |
| 模板注入风险 | analyzer.py:47 | AI prompt 拼接用户内容 |
| XSS 风险 | analyzer.py | 文章内容直接渲染 |

**风险示例**:
```python
# analyzer.py - 直接拼接用户内容到 prompt
prompt = f"""你是一位专业的股票投资分析助手。以下是我关注的多位股票博主在 {today} 前后发布的文章内容。

{articles_text}  # 用户内容直接插入
...
"""
```

### 2.5 存储架构问题 (🟡 中等)

| 问题 | 位置 | 描述 |
|------|------|------|
| JSON 文件存储 | bloggers.json | 无事务，无索引，量大会慢 |
| 配置分散 | user_config.json | 邮件/AI/微信混在一起 |
| 无数据库 | 整体架构 | 缺少结构化存储 |
| 无迁移机制 | 配置管理 | 配置变更无版本管理 |

---

## 三、改进方案

### 3.1 安全性改进

#### 方案 S1: 敏感信息保护 (🔴 最高优先级)

**目标**: 消除敏感信息明文存储风险

**实施方案**:

1. **创建 `.gitignore`**:
```gitignore
# 敏感配置
user_config.json
*.local.json
.env
.env.*
config/secrets.*

# Python
__pycache__/
*.py[cod]
*.so

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store
Thumbs.db
```

2. **环境变量配置方案**:
```python
# config.py 新增
import os

@dataclass
class AppConfig:
    # ... 原有字段 ...

    @classmethod
    def from_env(cls) -> "AppConfig":
        # 优先从环境变量读取敏感信息
        email_password = os.getenv("SENDER_PASSWORD") or os.getenv("EMAIL_PASSWORD", "")
        wx_cookie = os.getenv("WECHAT_COOKIE") or ""
        wx_mp_cookie = os.getenv("WECHAT_MP_COOKIE") or ""
        wx_mp_token = os.getenv("WECHAT_MP_TOKEN") or ""

        return cls(...)
```

3. **配置加密存储** (可选):
```python
# 使用 cryptography库的Fernet对称加密
from cryptography.fernet import Fernet

class SecureConfig:
    KEY_FILE = Path(__file__).parent.parent / ".key"

    @classmethod
    def load_encrypted(cls, path: Path) -> dict:
        key = cls._load_key()
        f = Fernet(key)
        with open(path, 'rb') as fp:
            return json.loads(f.decrypt(fp.read()))
```

#### 方案 S2: API 认证机制 (🟡 中等)

**目标**: 防止未授权访问

**实施方案**:
```python
# 新建 middleware/auth.py
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(key: str = Security(api_key_header)):
    if key != os.getenv("API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return key

# 使用方式
@app.get("/api/stock/search", dependencies=[Security(verify_api_key)])
async def api_stock_search(keyword: str):
    ...
```

---

### 3.2 模块解耦改进

#### 方案 M1: 拆分 app.py (🟡 中等优先级)

**目标**: 将 app.py 按职责拆分为独立路由模块

**目标架构**:
```
src/
├── app.py                    # 主入口，仅组装路由
├── config.py                 # 配置管理
├── router/
│   ├── __init__.py
│   ├── blogger.py            # 博主管理 API
│   ├── stock.py              # 股票查询 API
│   ├── config.py             # 配置管理 API
│   └── task.py               # WebSocket 任务 API
├── service/
│   ├── __init__.py
│   ├── blogger_service.py    # 博主业务逻辑
│   ├── article_fetcher.py    # 文章获取（从blogger.py拆分）
│   └── stock_service.py      # 股票服务（保留）
├── parser/
│   ├── __init__.py
│   ├── blogger_parser.py     # HTML解析（从blogger.py拆分）
│   └── weixin_parser.py      # 微信解析（从parser.py拆分）
├── scraper/
│   ├── __init__.py
│   └── weixin_scraper.py     # 爬虫（保留）
└── model/
    ├── __init__.py
    ├── blogger.py            # 博主数据模型
    └── stock.py              # 股票数据模型
```

**拆分步骤**:
1. 创建 `router/` 目录
2. 从 app.py 提取博主相关路由 → `router/blogger.py`
3. 从 app.py 提取股票相关路由 → `router/stock.py`
4. 从 app.py 提取配置相关路由 → `router/config.py`
5. 从 app.py 提取 WebSocket 任务 → `router/task.py`
6. 修改 app.py 为纯组装文件

#### 方案 M2: 依赖注入重构 (🟡 中等优先级)

**目标**: 解除硬编码依赖，便于测试

**实施方案**:
```python
# 新建 di.py - 依赖注入容器
from functools import lru_cache
from playwright.async_api import async_playwright

@lru_cache()
def get_playwright():
    return async_playwright()

@lru_cache()
def get_browser():
    pw = get_playwright()
    return pw.chromium.launch(headless=True)

@lru_cache()
def get_scraper():
    return WeixinScraper(get_browser)

@lru_cache()
def get_stock_service():
    return StockService()

@lru_cache()
def get_blogger_manager():
    return BloggerManager(get_scraper(), get_config())
```

---

### 3.3 并发性能改进

#### 方案 P1: 股票服务连接池 (🟡 中等优先级)

**目标**: 复用 httpx 连接，减少连接开销

**实施方案**:
```python
# stock_service.py 新增
class StockService:
    _http_client: httpx.AsyncClient | None = None

    @classmethod
    def get_http_client(cls) -> httpx.AsyncClient:
        if cls._http_client is None:
            cls._http_client = httpx.AsyncClient(
                timeout=30,
                proxies={"http": None, "https": None},
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
            )
        return cls._http_client

    @classmethod
    async def close_http_client(cls):
        if cls._http_client:
            await cls._http_client.aclose()
            cls._http_client = None

    async def get_realtime_quote(self, symbol: str) -> dict:
        client = self.get_http_client()
        # 使用复用连接
        r = await client.get(url, timeout=10)
```

#### 方案 P2: 浏览器实例复用 (🟡 中等优先级)

**目标**: 减少浏览器启动开销

**实施方案**:
```python
# scraper.py 优化
class WeixinScraper:
    _browser: Browser | None = None
    _context: BrowserContext | None = None

    async def get_browser(self):
        if self._browser is None:
            pw = await async_playwright().start()
            self._browser = await pw.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="..."
            )
        return self._browser, self._context

    async def fetch_article(self, url: str) -> dict:
        browser, context = await self.get_browser()
        page = await context.new_page()
        # ... 使用页面
```

#### 方案 P3: 缓存持久化 (🟡 中等优先级)

**目标**: 服务重启后缓存不丢失

**实施方案**:
```python
# stock_utils.py 新增
import json
from pathlib import Path

class PersistentTTLCache:
    CACHE_FILE = Path(__file__).parent.parent / ".cache" / "stock_cache.json"

    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        if self.CACHE_FILE.exists():
            try:
                data = json.loads(self.CACHE_FILE.read_text())
                self._store = {
                    k: (v, float(exp)) for k, (v, exp) in data.items()
                    if time.time() < float(exp)
                }
            except Exception:
                pass

    def _save_to_disk(self):
        self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: (v, exp) for k, (v, exp) in self._store.items()}
        self.CACHE_FILE.write_text(json.dumps(data))
```

---

### 3.4 内容安全改进

#### 方案 C1: AI Prompt 注入防护 (🟡 中等优先级)

**目标**: 防止用户内容干扰 AI 判断

**实施方案**:
```python
# analyzer.py 优化
import html

def _sanitize_for_prompt(text: str) -> str:
    """清理用户内容，防止 prompt 注入"""
    # HTML 转义
    text = html.escape(text)
    # 移除特殊标记
    text = text.replace("```", "").replace("---", "")
    # 限制长度
    max_len = 50000
    if len(text) > max_len:
        text = text[:max_len] + "\n...[内容已截断]"
    return text

async def analyze(self, articles: list[dict]) -> dict:
    articles_text = self._build_articles_text(articles)
    # 安全处理
    articles_text = _sanitize_for_prompt(articles_text)
    # ...
```

#### 方案 C2: 文章内容安全过滤 (🟡 中等优先级)

**目标**: 防止 XSS 和恶意内容

**实施方案**:
```python
# parser.py 新增
import re

class ContentSanitizer:
    DANGEROUS_PATTERNS = [
        r'<script[^>]*>.*?</script>',
        r'javascript:',
        r'on\w+\s*=',
    ]

    @classmethod
    def sanitize(cls, content: str) -> str:
        """清理潜在恶意内容"""
        for pattern in cls.DANGEROUS_PATTERNS:
            content = re.sub(pattern, '', content, flags=re.IGNORECASE | re.DOTALL)
        return content

    @classmethod
    def extract_text_safe(cls, html: str) -> str:
        """安全提取文本"""
        # 清理后再解析
        cleaned = cls.sanitize(html)
        soup = BeautifulSoup(cleaned, 'html.parser')
        return soup.get_text()
```

---

### 3.5 存储架构改进

#### 方案 D1: 引入 SQLite 数据库 (🟡 中等优先级)

**目标**: 结构化存储，支持索引和事务

**实施方案**:

1. **创建数据库模型**:
```python
# database.py
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "stock_platform.db"

@contextmanager
def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bloggers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                biz TEXT,
                user_name TEXT,
                avatar TEXT,
                source_url TEXT,
                article_title TEXT,
                article_time TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blogger_id TEXT,
                title TEXT,
                url TEXT UNIQUE,
                publish_time TEXT,
                content TEXT,
                fetched_at TEXT,
                FOREIGN KEY (blogger_id) REFERENCES bloggers(id)
            );

            CREATE INDEX IF NOT EXISTS idx_blogger_biz ON bloggers(biz);
            CREATE INDEX IF NOT EXISTS idx_article_url ON articles(url);
        """)
```

2. **重构 BloggerManager 使用数据库**:
```python
# blogger.py 改造
class BloggerManager:
    def __init__(self, scraper, config=None):
        self.scraper = scraper
        self.config = config
        init_db()  # 初始化数据库
        self._load()

    def _load(self):
        with get_db() as conn:
            cursor = conn.execute("SELECT * FROM bloggers")
            self.bloggers = [dict(row) for row in cursor]

    def _save(self):
        with get_db() as conn:
            for b in self.bloggers:
                conn.execute("""
                    INSERT OR REPLACE INTO bloggers
                    (id, name, biz, user_name, avatar, source_url, article_title, article_time, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (...))
            conn.commit()
```

#### 方案 D2: 配置分层管理 (🟡 低优先级)

**目标**: 分离不同类型的配置

**实施方案**:
```
config/
├── default.json          # 默认配置（可提交git）
├── production.json        # 生产环境覆盖
├── local.json.example     # 本地配置示例（不提交）
└── secrets.json.enc       # 加密敏感配置
```

```python
# config_loader.py
def load_config() -> AppConfig:
    # 1. 加载默认
    config = load_json("config/default.json")

    # 2. 环境变量覆盖
    env_override(config)

    # 3. 本地文件覆盖（不提交）
    if Path("config/local.json").exists():
        config = merge(config, load_json("config/local.json"))

    return AppConfig(**config)
```

---

## 四、实施计划

### 4.1 分阶段实施

| 阶段 | 内容 | 优先级 | 工作量 | 风险 |
|------|------|--------|--------|------|
| **Phase 0** | 紧急：添加 .gitignore，修复敏感信息 | 🔴 必须 | 0.5天 | 无 |
| **Phase 1** | 安全：环境变量配置方案 | 🔴 必须 | 1天 | 低 |
| **Phase 2** | 性能：连接池 + 缓存持久化 | 🟡 重要 | ✅ 已完成 | — |
| **Phase 3** | 解耦：拆分 app.py | 🟡 重要 | 3天 | 中 |
| **Phase 4** | 内容安全：输入过滤 | 🟡 中等 | 1天 | 低 |
| **Phase 5** | 存储：引入 SQLite | 🟢 可选 | 2天 | 中 |
| **Phase 6** | 认证：API Key 机制 | 🟢 可选 | 1天 | 低 |

### 4.2 Phase 0 详细方案 (立即执行)

#### Step 1: 创建 .gitignore

```gitignore
# 敏感配置
user_config.json
*.local.json
.env
.env.*
config/secrets.*
.cached_*

# Python
__pycache__/
*.py[cod]
*.so
.venv/
venv/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Playwright
.pytest_cache/
playwright/.cache/

# 项目特定
data/
*.db
*.db-journal
```

#### Step 2: 确保 user_config.json 在 .gitignore 中

```bash
# 在项目根目录执行
echo "user_config.json" >> /Users/wangjun/Desktop/股票信息/wexin-read-mcp-main/.gitignore
```

### 4.3 Phase 1 详细方案

#### 修改 config.py - 添加环境变量支持

```python
# config.py 新增环境变量读取
import os

@dataclass
class AppConfig:
    email: EmailConfig = field(default_factory=EmailConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    max_concurrent_scrape: int = 3
    scrape_delay: float = 2.0

    @classmethod
    def from_env(cls) -> "AppConfig":
        # 优先从环境变量读取
        email_password = os.getenv("SENDER_PASSWORD") or os.getenv("EMAIL_PASSWORD", "")
        wx_cookie = os.getenv("WECHAT_COOKIE", "")
        wx_mp_cookie = os.getenv("WECHAT_MP_COOKIE", "")
        wx_mp_token = os.getenv("WECHAT_MP_TOKEN", "")
        ai_key = os.getenv("AI_API_KEY", "")

        return cls(
            email=EmailConfig(
                smtp_server=os.getenv("SMTP_SERVER", ""),
                smtp_port=int(os.getenv("SMTP_PORT", "465")),
                sender_email=os.getenv("SENDER_EMAIL", ""),
                sender_password=email_password,
                use_ssl=os.getenv("SMTP_USE_SSL", "true").lower() == "true",
            ),
            ai=AIConfig(
                api_key=ai_key,
                base_url=os.getenv("AI_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("AI_MODEL", "gpt-4o-mini"),
            ),
            wechat=WeChatConfig(
                cookie=wx_cookie,
                mp_cookie=wx_mp_cookie,
                mp_token=wx_mp_token,
            ),
        )
```

### 4.4 Phase 2 详细方案 ✅ 已完成

#### 2.1 HTTP 连接池（已完成）

**实际方案**：使用 `requests.Session` 替代原计划的 `httpx.AsyncClient`。
原因：项目使用同步 `requests` 调用 + `asyncio.to_thread` 包装，无需 httpx。

新建 `src/http_client.py`，提供全局连接池：

```python
import requests

session = requests.Session()
session.trust_env = False  # 不读系统代理环境变量（VPN/Clash/Surge 不影响）
session.proxies = {"http": None, "https": None}  # 双保险

def patch_requests(func, **kwargs):
    """临时替换 requests.get/post 为走连接池的版本，用于 AKShare。"""
    import requests as _requests
    orig_get, orig_post = _requests.get, _requests.post
    _requests.get, _requests.post = session.get, session.post
    try:
        return func(**kwargs)
    finally:
        _requests.get, _requests.post = orig_get, orig_post
```

**改动文件**：
- 新建 `src/http_client.py` — 共享连接池 + 代理绕过
- `stock_service.py` — 删除 19 行重复代理代码，改为导入
- `iwencai_service.py` — 删除 14 行重复代理代码，改为导入
- `market/futures.py` — 删除 14 行重复代理代码，改为导入

#### 2.2 缓存持久化（已完成，早于本计划）

实际在 Phase 5（SQLite 引入）时一并完成。`stock_utils.py` 已使用 SQLite `cache` 表实现持久化缓存，包含：
- `cache_get()` — 从 SQLite 读取，过期自动删除
- `cache_set()` — 写入 SQLite，支持 DataFrame 序列化
- `_CacheCompat` — 兼容旧 `TTLCache` API

---

## 五、未来建设路线图

### 5.1 短期 (1-2个月)

| 功能 | 描述 | 优先级 |
|------|------|--------|
| .gitignore | 保护敏感文件不提交 | 🔴 |
| 环境变量配置 | 敏感信息不存储文件 | 🔴 |
| 连接池 | 减少连接开销 | ✅ 已完成 |
| 缓存持久化 | 重启不丢失缓存 | ✅ 已完成 |
| 内容过滤 | XSS防护 | 🟡 |

### 5.2 中期 (3-6个月)

| 功能 | 描述 | 优先级 |
|------|------|--------|
| app.py 拆分 | 模块解耦 | 🟡 |
| SQLite 引入 | 结构化存储 | 🟢 |
| API 认证 | 接口安全 | 🟢 |
| 单元测试 | 质量保证 | 🟢 |
| 监控告警 | 生产可用 | 🟢 |

### 5.3 长期 (6个月+)

| 功能 | 描述 | 优先级 |
|------|------|--------|
| 前端界面 | 用户友好 | 🟢 |
| 多用户支持 | 团队协作 | 🟢 |
| 数据分析 | 投资决策支持 | 🟢 |
| 移动端 | 随时查看 | 🟢 |

---

## 六、风险评估

| 风险 | 影响 | 概率 | 应对 |
|------|------|------|------|
| 配置迁移失败 | 高 | 低 | 保留回滚方案 |
| 拆分引入bug | 中 | 中 | 充分测试 |
| 性能回退 | 中 | 低 | 基准测试 |
| 依赖冲突 | 低 | 低 | 虚拟环境隔离 |

---

## 七、验收标准

### Phase 0-1 验收

- [ ] `.gitignore` 包含 `user_config.json`
- [ ] 敏感配置可通过环境变量读取
- [ ] 不启动报错，现有功能正常

### Phase 2 验收

- [x] 全局 Session 统一管理，trust_env=False 绕过 VPN 代理
- [x] stock_service / iwencai_service / futures 三处重复代码消除
- [x] 缓存通过 SQLite 持久化，重启后数据保留
- [x] 接口功能正常，腾讯行情 API 请求成功

### Phase 3 验收

- [ ] app.py 行数减少 50%+
- [ ] 模块间依赖清晰
- [ ] 单元测试覆盖 80%+

---

*文档结束*
