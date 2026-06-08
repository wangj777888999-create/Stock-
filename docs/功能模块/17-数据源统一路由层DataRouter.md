# 数据源统一路由层 DataRouter

> 日期:2026-06-07
> 范围:`services/data_router.py`(新建)、`services/providers.py`(新建)、`routers/health.py`(新建)、`signal_service.py` / `stock_service.py` / `cockpit_service.py` 接入
> 状态:Phase 1+2 完成,4 个核心契约已接入

## 一、为什么做这件事

之前每条数据接口的"多源降级"逻辑是各自手写的:
- 资金流:`get_money_flow` 里写 race + try/except
- 概念榜:`get_concept_ranking` 里写 for-loop 降级
- 涨跌家数:`cockpit_service` 里写两套(东财失败再试新浪)

后果:
1. 每加一个备选源,要改 3+ 个文件
2. 没有统一的 EWMA 排序——每个 race 都是"按声明顺序"试,不会自动学习哪个源在当前环境最快
3. 不可观测——网络环境变了,不知道哪个源在挂

## 二、新架构

```
┌─────── 业务层 ─────────────────────────────┐
│ get_money_flow / get_concept_ranking / ... │
│ 只声明"我要什么数据",不指定源              │
└──────────────┬─────────────────────────────┘
               │
               ▼
┌─────── 源路由层 DataRouter ────────────────┐
│  - register_contract("concept_rank")        │
│  - register_provider(contract, id, fn)      │
│  - fetch(contract, **params)                │
│    ├─ 缓存查询(L1+L2)                       │
│    ├─ 按 EWMA 历史延迟排序所有源             │
│    ├─ 并发 race,首个有效结果胜出             │
│    ├─ 自动更新统计                           │
│    └─ 全败 → stale 缓存兜底                  │
└──────────────┬─────────────────────────────┘
               │
               ▼
┌─────── 源适配层 services/providers.py ─────┐
│ em_push2 / sina_class / sina_spot /        │
│ akshare / em_direct / ths_summary / ...    │
└─────────────────────────────────────────────┘
```

## 三、已接入的 9 个契约(Phase 1-4 累计)

| 契约 | 业务接口 | 注册的源 | 当前胜出源 |
|---|---|---|---|
| `concept_rank` | `get_concept_ranking` | em_push2(主) + sina_class(备) | sina_class ⚡ |
| `industry_rank` | `get_industry_ranking` | sina(主) + ths_summary(备) | 看环境(EWMA 自动学) |
| `market_breadth` | `cockpit.get_sentiment` | em_legu(主) + sina_spot(备) | sina_spot |
| `money_flow_individual` | `stock.get_money_flow` | em_direct + akshare + sina_today | sina_today(当东财不通) |
| **`stock_quote`** | `stock.get_realtime_quote` | tencent + sina_hq(让步 800ms) | tencent ⚡(字段全) |
| **`stock_news`** | `stock.get_news` | em(单源) | em |
| **`stock_kline_a`** | `stock.get_kline`(A股) | tencent + akshare | tencent |
| **`hot_stocks`** | `signal.get_hot_stocks` | em_emappdata(单源) | em_emappdata |
| **`stock_announcement`** | `stock.get_announcements` | cninfo(主) | cninfo |

## 四、核心特性

### 1. EWMA 自适应排序

每次源调用记 `EWMA(latency)`:成功记真实延迟,失败记 `timeout × 3` 罚值。
下次 race 时按 EWMA 排序,**让 event loop 先调度最快的源**。

效果:你切换 VPN 后,系统在几次调用内自动适应,**不需要手动改顺序**。

### 2. Stale-while-revalidate 兜底

`router.fetch(cache_key=...)` 自带:
- 命中新鲜缓存 → 秒回
- 缓存过期但 7 天内有过成功 → 返回旧值 + `stale: true` 标记
- 真的全失败 → 返回明确错误

### 3. 可观察性

新增 `GET /api/health/sources` 端点,返回每个 contract 下每个源的:
- EWMA 延迟(ms)
- 总调用 / 成功次数 / 成功率
- 最近一次错误信息

面试时直接打开这个接口看实时数据,**等价于给系统装了仪表盘**。

## 五、实测验证

### A. 启动日志
```
DataRouter providers ready: concept_rank, industry_rank, market_breadth, money_flow_individual
```

### B. 4 个端点 HTTP 测试(全部 200)
```bash
GET /api/signal/concept_rank?limit=5   → 5 条数据,source=sina_class,440ms
GET /api/signal/industry_rank?limit=5  → 5 条数据,source=ths_summary,69ms
GET /api/stock/flow/600519             → 茅台资金,source=sina_today,110ms
GET /api/cockpit/sentiment             → 涨跌家数全有,source=sina_spot
GET /api/health/sources                → 完整 EWMA + 成功率快照
```

### C. EWMA 自动学习验证
- 首次调用 em_direct 资金流(失败)→ 记 EWMA=21000ms(惩罚)
- 首次调用 sina_today(成功 110ms)→ 记 EWMA=110ms
- 二次调用 → sina_today 排第一,em_direct 排最后,**race 立刻命中**

## 六、文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `services/data_router.py` | 新 | DataRouter 核心:contract 注册、fetch、EWMA、stale、snapshot |
| `services/providers.py` | 新 | 所有源适配器集中注册 |
| `routers/health.py` | 新 | `/api/health/sources` 健康面板 |
| `signal_service.py` | 改 | `get_concept_ranking` / `get_industry_ranking` 改走 router |
| `stock_service.py` | 改 | `get_money_flow` 改走 router,旧实现保留为 `_DEPRECATED` |
| `cockpit_service.py` | 改 | 涨跌家数降级走 router |
| `app.py` | 改 | 注册 providers + health 路由 |

## 七、Phase 3-4 新增能力

### handicap_ms 让步机制(质量优先)

race 框架只看延迟,不知道"哪个源数据更全"。Phase 3 引入 `handicap_ms` 解决:

```python
router.register_provider("stock_quote", "tencent",  _quote_tencent, weight=10.0)
# sina_hq 字段少(无 PE/市值),让步 800ms 给腾讯先机
router.register_provider("stock_quote", "sina_hq",  _quote_sina,   weight=5.0, handicap_ms=800)
```

效果:
- 腾讯正常时:300ms 返回字段完整数据,sina 还没启动 → 腾讯赢
- 腾讯挂时:800ms 后 sina 启动接管 → 数据少但可用
- EWMA 持续学习 + 静态质量保障

### Phase 5 后续扩展候选

- `hk_kline` / `us_kline`(港股/美股 K线)
- `stock_holders`(十大流通股东)
- `stock_visits`(机构调研)
- `etf_quote` / `etf_kline`(基金)
- `crypto_*`(加密货币)

每个接入只需:
1. 在 `providers.py` 加 `register_contract` + `register_provider`(20 行)
2. 业务层把 try/except 改成 `router.fetch(...)`(5 行)

## 八、面试讲法

> "我系统里多个数据源比如东财、新浪、同花顺,经常会因为网络环境/反爬变化导致某个源不可达。
> 我没在每个业务函数里写 try/except 降级,而是抽象出一个 **DataRouter 数据契约层**——
> 业务声明'我要什么数据',router 内部做多源并发 race、EWMA 自动学习哪个源在当前环境最快、
> 缓存 + stale-while-revalidate 兜底、统一统计。配套一个 `/api/health/sources` 健康面板,
> 可以实时看到每个源的延迟和成功率。
> 这个设计让系统的可靠性和可观察性都上了一个台阶,业务代码也变干净了——
> 加新源只改一处,不再到处改 try/except。"

这是一段非常加分的工程能力展示。
