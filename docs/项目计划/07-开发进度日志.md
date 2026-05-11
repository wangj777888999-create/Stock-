# progress.md — 会话日志

## 2026-04-21 会话1: 性能测试与初始优化

### 完成事项

1. **股票服务基线测试** — 8 个接口全部测试，识别 search_stock 8.4s 瓶颈
2. **search_stock 优化** — 预加载股票列表到内存，0.005s，提升 1690x
3. **微信爬虫修复** — 禁用 Cookie 失效时的降级路径，防止返回旧数据
4. **标题提取增强** — og:title → title 标签 → JS 变量三重兜底
5. **登录授权失效提醒** — 新增 API 端点 + 前端红色警告 banner

### 测试数据

| 接口 | 优化前 | 优化后 |
|------|--------|--------|
| search_stock | 8.441s | 0.005s |
| get_realtime_quote | 0.076s | — |
| get_kline | 0.190s | — |
| get_news | 0.070s | — |

| 并发数 | 吞吐量 |
|--------|--------|
| 5 | 1178/s |
| 10 | 2022/s |
| 20 | 3191/s |

---

## 2026-04-21 会话2: 三个问题修复 + 深度排查

### 问题反馈

用户反馈三个问题：
1. 微信公众号接口反应速度很慢
2. 登录完成后顶部提示没有自动消失
3. 股痴流沙河最新文章"如图"抓取不到

### 修复过程

**问题1 — 速度优化**
- `app.py` `_resolve_blogger_urls()` 从串行 for 循环改为 `asyncio.gather` + `Semaphore(3)`
- `blogger.py` `refresh_all()` 增加 `Semaphore(2)` 限流

**问题2 — 登录 banner 自动消失**
- 在 `handleMpLoginMessage` success、`checkMpLoginStatus` else、`autoRefreshBloggers` else 三个入口点添加 `loginWarningBanner.remove()`

**问题3 — 深度排查过程**

1. 先测试 scraper 能否抓取该 URL → 成功（标题"如图"，内容为一张图片）
2. 问题定位到**文章列表获取阶段**，不是抓取阶段
3. 查看博主数据 → `refresh_error: "Cookie已失效"` → 但实际测试凭证有效 (ret=0)
4. 调用 `appmsg type=9` → 返回 5 篇文章，"如图"不在其中
5. 尝试 `type=""` → ret=200002 参数无效
6. 遍历 type=1,2,3,5,9,10 → 只有 type=9 有效，其余 200002
7. 尝试 `appmsgpublish` 接口 → **发现"如图"！** type=10002（图片消息）
8. 确认根因：`appmsg` 只返回 type=9（图文），type=10002（图片消息）需要 `appmsgpublish`
9. 新增 `_mp_list_published()` 方法
10. 测试验证 → 所有 6 个博主刷新成功

### 踩坑记录

| 错误操作 | 后果 | 修正 |
|---------|------|------|
| `appmsg` 的 `type` 设为 `""` | ret=200002，完全无法获取文章 | 恢复 `type="9"` |
| 只看 `appmsg` 接口 | 遗漏 type=10002 的图片消息 | 新增 `appmsgpublish` 作为主数据源 |
| `refresh_all` 成功时未清除 `refresh_error` | 旧错误信息残留 | 成功时 `pop("refresh_error")` |

---

## 2026-04-28 会话3: 灵活爬取 + 问财集成

### 完成事项

#### 1. 灵活爬取模式（3 种）

- `latest`: 每个博主只取最新 1 篇
- `latest_n`: 每个博主取最近 N 篇（默认 5）
- `period`: 时间段筛选（今天/3天/一周/一月）

**实现**:
- `blogger.py`: 新增 `_filter_by_mode()` 静态方法，`fetch_recent_articles()` 签名扩展
- `app.py`: `_resolve_blogger_urls()` 透传参数，WebSocket 解析 `scrape_mode/scrape_count/scrape_period`
- 前端: 任务启动区增加模式单选按钮 + 数字输入/下拉选择

#### 2. IWencaiService 问财服务

新建 `src/iwencai_service.py`，6 个方法 + 1 个预留钩子:

| 方法 | 查询 | 数据源 |
|------|------|--------|
| `query()` | 自然语言选股 | pywencai |
| `get_sectors()` | 行业板块列表 | AKShare `stock_board_industry_name_ths` |
| `get_sector_stocks()` | 行业成分股 | pywencai |
| `get_stock_visits()` | 个股机构调研 | pywencai (dict 格式) |
| `get_visits_search()` | 全市场调研扫描 | pywencai + 去重 |
| `query_for_article()` | 博主观点交叉验证（预留） | 组合调用 |

新增 5 条路由: `POST /api/iwencai/query`, `GET /api/iwencai/sectors`, `GET /api/iwencai/sector/{name}`, `GET /api/iwencai/visits/{symbol}`, `POST /api/iwencai/visits/search`

#### 3. 前端条件选股视图

- 侧边栏新增加"条件选股"导航项
- 3 个 tab: 条件选股（自然语言 + 结构化筛选）、板块扫描（行业标签云 + 成分股）、机构调研（全市场扫描 + 个股详情）
- 个股详情区新增"机构调研"卡片
- 切换到板块/调研 tab 自动加载数据

#### 4. Bug 修复

| Bug | 修复 |
|------|------|
| Tab 切换无反应 | CSS class + JS 统一用 `classList.toggle('active')` |
| 板块热力图显示个股 | 改用 AKShare 行业列表 + 标签云 UI |
| 机构调研数据全重复 | 全市场查询改为"近一月机构调研家数大于5家" + `_dedup_visits()` 去重 |
| 个股调研查询失败 | 兼容 pywencai dict 返回，提取"近半年机构调研明细" |

### 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/blogger.py` | 新增 `_filter_by_mode()`，`fetch_recent_articles()` 签名扩展 |
| `src/app.py` | `_resolve_blogger_urls()` 透传参数，新增 5 条问财路由 |
| `src/iwencai_service.py` | **新建**，完整问财服务（含 `_dedup_visits` 去重） |
| `src/templates/index.html` | 爬取模式 UI + 条件选股完整视图 + 机构调研卡片 |
| `docs/superpowers/specs/2026-04-28-flexible-scraping-modes-design.md` | 设计文档 |
| `docs/superpowers/plans/2026-04-28-flexible-scraping-wencai.md` | 实现计划 |

### 验证结果

```
条件选股: success=True, total=3 (深高速 PE=19.98)
板块列表: success=True, count=90
成分股: success=True (半导体行业, 5 只含实时行情)
全市场调研: success=True, total=96, 唯一率=96/96
个股调研: success=True (平安银行, 3 条调研记录)
```
