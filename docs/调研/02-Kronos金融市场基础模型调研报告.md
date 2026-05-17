# 02 - Kronos 金融市场基础模型调研报告

> 调研日期：2026-05-17
> 来源：[GitHub shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | 论文 arXiv:2508.02739 | AAAI 2026

---

## 项目定位

**全球首个专为金融 K 线设计的开源基础模型**，覆盖 45+ 全球交易所数据，被 AAAI 2026 录用。GitHub 已获 25.2k Stars / 4.4k Forks，是当前量化领域最受关注的开源项目之一。

核心思路：将 K 线序列视为"市场语言"，用类 GPT 的自回归 Transformer 进行建模与预测，而非套用通用时序预测框架。

---

## 核心架构：两阶段框架

```
原始 OHLCV 数据
  → [阶段1] 专用分词器 (Tokenizer)
      将连续多维 K 线数据量化为层级化离散 Token
      （降低计算复杂度 60-70%）
  → [阶段2] 自回归 Transformer (Decoder-only)
      因果掩码 + 大规模预训练
      → 统一处理多种量化任务
```

关键设计点：

- **离散化分词**：分词器将 OHLCV 连续值映射为离散 token，类似 BPE 对文字的处理，使模型可以用语言模型范式训练。
- **因果掩码**：确保预测只依赖历史数据，无未来信息泄露。
- **上下文长度**：512 Token（mini 版 2048），约等价于数天分钟级行情窗口。
- **多任务统一**：同一套模型支持价格预测、波动率预测、合成 K 线生成等多种任务。

---

## 模型家族

| 模型 | 参数量 | 上下文长度 | 定位 | 开放状态 |
|------|--------|-----------|------|----------|
| Kronos-mini | 4.1M | 2048 | 边缘部署 / 原型验证 | ✅ MIT 开源 |
| Kronos-small | 24.7M | 512 | 单资产预测 | ✅ MIT 开源 |
| Kronos-base | 102.3M | 512 | 组合管理 | ✅ MIT 开源 |
| Kronos-large | 499.2M | 512 | 机构级应用 | ❌ 受限（需机构合作） |

---

## 训练数据

- **规模**：12 亿+ 条 K 线记录
- **来源**：45+ 全球交易所，多市场、多周期联合预训练
- **细调支持**：提供 A 股细调脚本（基于 Qlib 集成），适合本地化迁移

---

## 性能指标

| 任务 | 指标 | 结果 |
|------|------|------|
| 价格序列预测 RankIC | vs 最佳 TSFM | +93% |
| 价格序列预测 RankIC | vs 最佳非预训练基线 | +87% |
| 波动率预测 MAE | 绝对降低 | -9% |
| 合成 K 线生成保真度 | 生成质量提升 | +22% |
| 加密货币小时级方向准确率 | 第三方评测 | 58–65% |
| 细调后精度提升（目标资产）| 迁移效果 | +15~25% |

---

## 核心应用场景

| 场景 | 描述 |
|------|------|
| 加密货币交易 | 用 400 分钟历史数据预测未来 120 分钟走势 |
| 统计套利 | 捕捉相关资产协整关系，优于线性模型 |
| 风险管理 | 多情景 VaR 计算（温度采样生成多条价格路径） |
| 高频交易 | Kronos-mini 可达 <100ms 延迟（co-located 部署） |
| 市场监控 | Token embedding 自然聚类为市场状态，可用于异常检测 |

---

## 快速接入

**环境要求**：Python 3.10+，16GB RAM，可选 CUDA 11.8+

```bash
git clone https://github.com/shiyu-coder/Kronos.git
pip install -r requirements.txt
```

**基础用法**：

```python
from kronos import KronosPredictor

# 从 HuggingFace Hub 加载模型
predictor = KronosPredictor.from_pretrained("shiyu-coder/Kronos-base")

# 批量预测（支持多序列并行推理）
forecasts = predictor.predict_batch(
    historical_ohlcv,      # 历史 OHLCV 数据
    future_timestamps,     # 待预测时间戳
    temperature=0.8,       # 控制预测多样性
    top_p=0.9              # 核采样参数
)
```

**细调流程**：提供完整 fine-tuning 脚本（以 A 股 + Qlib 为示例），支持 torchrun 多 GPU 训练。

---

## 与 StockPulse 的整合价值

| 现有模块 | 可整合方向 |
|----------|-----------|
| `stock_service.py`（A 股行情） | 接入 Kronos-base + Qlib 细调，生成 A 股 K 线预测信号 |
| `analyzer.py`（AI 文章分析） | 结合 Kronos 价格预测，为 AI 多视角分析补充量化维度 |
| `services/indicators.py`（RSI/MACD 等） | Kronos 预测结果作为额外 alpha 因子，与传统指标联合使用 |
| `watchlist` / 模拟交易 | Kronos 预测信号驱动自动开仓建议或回测验证 |

**整合优先级建议**：先用 Kronos-small 在现有 watchlist 标的上做离线预测验证，确认信号质量后再接入实盘推荐流程。

---

## 风险与局限

- **large 模型未开源**：499.2M 参数的最强版本仅限机构合作，开源版本上限为 base（102.3M）。
- **方向准确率上限**：65% 方向准确率在噪声较大的市场中仍需配合止损策略。
- **超参敏感性**：temperature / top_p 对预测分布影响较大，需针对目标市场调参。
- **细调数据需求**：虽声称只需 90% 更少标注数据，但 A 股细调仍需足量高质量历史数据。
- **延迟风险**：base/small 版本在 CPU 环境下推理延迟较高，不适合高频场景。

---

## 许可证 & 引用

- **许可证**：MIT（Kronos-large 受限）
- **论文**：arXiv:2508.02739，AAAI 2026
- **HuggingFace**：`shiyu-coder/Kronos-{mini,small,base}`

---

## 参考资料

- [GitHub - shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos)
- [Kronos Live Demo (BTC/USDT)](https://shiyu-coder.github.io/Kronos-demo/)
- [论文摘要 - NASA ADS](https://ui.adsabs.harvard.edu/abs/2025arXiv250802739S/abstract)
- [BrightCoding 技术解析](https://www.blog.brightcoding.dev/2026/04/10/kronos-the-revolutionary-ai-model-for-financial-markets)
- [Quant Science 项目介绍](https://x.com/quantscience_/status/2046620289799233936)
