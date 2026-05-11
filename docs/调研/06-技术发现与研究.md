# findings.md — 技术发现与研究

## 1. 微信 MP 后台 API 内容类型体系

### 发现日期: 2026-04-21

微信公众号后台有两套文章获取接口，覆盖范围不同：

| 接口 | 端点 | 能获取的类型 | 备注 |
|------|------|------------|------|
| `appmsg` | `/cgi-bin/appmsg?action=list_ex&type=9` | 仅 type=9（图文消息） | 传统接口，稳定 |
| `appmsgpublish` | `/cgi-bin/appmsgpublish?sub=list` | 所有类型（9, 10002...） | 新接口，响应格式嵌套 |

### 内容类型枚举

| type 值 | 含义 | appmsg 可获取 | appmsgpublish 可获取 |
|---------|------|--------------|---------------------|
| 9 | 图文消息（传统文章） | ✓ | ✓ |
| 10002 | 图片消息（新版发布） | ✗ | ✓ |

### appmsgpublish 响应格式（关键）

```
响应结构（三层嵌套 JSON 字符串）:
{
  "base_resp": {"ret": 0},
  "publish_page": "<JSON字符串>" ← 第1层: 需要 json.loads()
}

publish_page 解析后:
{
  "total_count": 1115,
  "publish_list": [
    {
      "publish_type": 1,
      "publish_info": "<JSON字符串>" ← 第2层: 需要 json.loads()
    }
  ]
}

publish_info 解析后:
{
  "type": 10002,           ← 内容类型
  "msgid": 2247488273,
  "sent_info": {"time": 0},  ← 图片消息 time 可能为 0
  "appmsg_info": [         ← 文章列表
    {
      "title": "如图",
      "content_url": "https://mp.weixin.qq.com/s/xxx",
      "is_deleted": false,
      "cover_url": "...",
      "digest": ""
    }
  ]
}
```

### appmsg 的 type 参数限制

- `type=9` 有效（图文消息）
- `type=""` **无效**，返回 ret=200002
- `type=2,3,5,10` 等均返回 200002
- 该接口只支持 `type=9`

---

## 2. 股票搜索性能瓶颈

### 发现日期: 2026-04-21

**现象**: `search_stock` 每次调用耗时 8.4 秒

**根因**: 每次搜索都调用 `ak.stock_info_a_code_name()` 获取全市场 5000+ 股票列表

**代码位置**: `stock_service.py:136`

**优化方案**: 服务启动时一次性预加载到内存，后续搜索仅做字符串匹配

**效果**: 8.4s → 0.005s（1690x），并发 3191 req/s

---

## 3. 微信 Cookie 降级路径的数据准确性风险

### 发现日期: 2026-04-21

**问题**: Cookie 失效时降级到 source_url（旧文章 URL）抓取 HTML 提取元信息，返回的是旧数据而非最新文章，且不报错。

**修复策略**: 禁用降级路径。Cookie 失效时直接返回错误，强制要求重新配置凭证。

---

## 4. Config dataclass 的 hasattr 陷阱

### 发现日期: 2026-04-21

**现象**: `hasattr(config, 'wechat')` 返回 False，但 `config.wechat` 可以正常访问

**原因**: 测试时传入的 Config 对象不是正确的 dataclass 实例。

**教训**: 测试时应使用真实的 `AppConfig` 实例。

---

## 5. 微信并发请求限频规律

### 发现日期: 2026-04-21

- `ret=200013` — 操作频率过快（请求被拒但凭证仍有效）
- 安全并发数: 2-3 个（`Semaphore(2)` 或 `Semaphore(3)`）
- 请求间隔 0.3-1 秒可有效避免限频

---

## 6. Playwright 反检测配置

### 发现日期: 2026-04-21

**生效配置**:
```python
browser = await pw.chromium.launch(
    headless=True,
    args=["--disable-blink-features=AutomationControlled"],
)
context = await browser.new_context(
    user_agent="Chrome/125.0.0.0 Safari/537.36",
)
await context.add_init_script(
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
)
```

**关键**: 必须同时设置 `--disable-blink-features=AutomationControlled` 和隐藏 `navigator.webdriver`。

---

## 7. pywencai 问财查询特性

### 发现日期: 2026-04-28

### 7.1 只能做个股筛选，不能返回板块指数

| 查询类型 | pywencai 返回 | 说明 |
|---------|--------------|------|
| "行业板块涨幅排名" | 个股 DataFrame | 返回各行业的成分股，非板块指数 |
| "概念板块指数" | 个股 DataFrame | 同上 |
| query_type='board' | dict（非 tabular） | 不支持板块查询 |
| query_type='stock' | DataFrame | 唯一可用的查询类型 |

**结论**: 板块热力图不能依赖 pywencai，改用了 AKShare 的 `stock_board_industry_name_ths` 获取行业列表。

### 7.2 个股调研查询返回 dict

查询 "000001 机构调研" 等具体股票时，pywencai 返回 dict 而非 DataFrame。dict 中含 `近半年机构调研明细` 列表，包含：股票简称、调研公告日期、调研地点、调研日期。

**结论**: `get_stock_visits()` 需要兼容 dict 返回格式。

### 7.3 全市场调研数据严重重复

"近一月有机构调研" 查询返回 10 行可能全是同一只股票（每行对应一家机构的调研记录）。去重后可能只剩 1-2 只股票。

**解决方案**:
- 全市场查询改为"近一月机构调研家数大于5家" — 返回 96+ 只不重复股票
- 后端新增 `_dedup_visits()` 按股票代码去重

### 7.4 性能基准

| 查询类型 | 耗时 | 去重后数据量 |
|---------|------|------------|
| 问财条件选股 | 1-2.6s | 50-100 条 |
| 行业列表 (AKShare) | <1s | 90 个行业 |
| 行业成分股查询 | 1-2s | 50-100 条 |
| 全市场调研扫描 | 2-4s | 96 条（去重后） |
| 个股调研 | 1-2s | 3-10 条 |

---

## 8. CSS/JS Tab 切换冲突

### 发现日期: 2026-04-28

**现象**: 条件选股视图的 tab 切换完全没有响应

**根因**: CSS 用 `.wencai-panel { display: none }` 隐藏面板，而 JS 用 `panel.style.display = ''` 试图显示。但 `style.display = ''` 只是清除 inline style，CSS 规则 `display: none` 仍生效。

**修复**: 统一用 CSS class 控制 —— `panel.classList.toggle('active')`，通过 `.wencai-panel.active { display: block }` 覆盖基础规则。同时移除 HTML 中的 `style="display:none"` 避免 inline style 冲突。

### 教训

在同一个元素上混用 CSS class 和 inline style 控制 display 是常见的 bug 来源。应统一使用一种机制。本项目主视图使用 `.view.active` 已是最佳实践，条件选股视图未遵循此模式。

---

## 9. 东方财富 API 网络不可达

### 发现日期: 2026-04-28

`push2.eastmoney.com` 在当前网络环境下连接超时，`stock_board_concept_spot_em` 和 `stock_board_industry_spot_em` 均不可用。`_patch_requests` 绕过代理无效。

**影响**: 板块实时行情数据无法从东方财富获取，依赖同花顺和问财数据源。
