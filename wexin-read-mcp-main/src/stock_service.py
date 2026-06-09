"""
股票数据服务层 — facade（向后兼容的导入入口）。

实现已按职责拆到 `stock/` 子包（Mixin 模式）：
- stock.quote      搜索 + 实时行情 + 股票列表预加载
- stock.kline      前复权 K 线 + 技术指标
- stock.fundamental 公司简介 / 财务 / 公告 / 股东
- stock.flow       资金流向 + 个股新闻

对外导入路径保持不变：`from stock_service import StockService`。
扩展方式：在对应 Mixin 下新增方法，然后在 app.py 添加路由即可。
"""

from __future__ import annotations

from stock.quote import QuoteMixin
from stock.kline import KlineMixin
from stock.fundamental import FundamentalMixin
from stock.flow import FlowMixin


class StockService(QuoteMixin, KlineMixin, FundamentalMixin, FlowMixin):
    """A 股/港股/美股数据服务（聚合各功能 Mixin）。

    类级缓存 `_stock_list_cache`/`_stock_list_loaded` 单一来源于 QuoteMixin，
    MRO 下 preload/_refresh/search 共享同一份。
    """
    pass


# 兼容历史内部符号引用（grep 确认目前外部仅用 StockService；
# providers 已直连 quote_parser，不再依赖此处）。保留命名空间避免破坏潜在引用。
from services.quote_parser import _QT_URL, _parse_tencent_quote  # noqa: E402,F401
