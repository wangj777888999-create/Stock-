"""文章内容分析模块 - 整合多篇文章并生成投资分析报告"""

import asyncio
import json
import logging
from datetime import datetime

from agents import PERSONAS, Persona
from config import AppConfig
from http_client import get_async_proxy_client

logger = logging.getLogger(__name__)


class ArticleAnalyzer:
    """股票博主文章分析器"""

    def __init__(self, config: AppConfig):
        self.config = config

    def _build_articles_text(self, articles: list[dict]) -> str:
        """将多篇文章拼接为分析用文本"""
        parts = []
        for i, article in enumerate(articles, 1):
            parts.append(
                f"【文章{i}】\n"
                f"标题: {article.get('title', '未知')}\n"
                f"作者: {article.get('author', '未知')}\n"
                f"时间: {article.get('publish_time', '未知')}\n"
                f"内容:\n{article.get('content', '无内容')}\n"
            )
        return "\n---\n".join(parts)

    async def analyze(self, articles: list[dict]) -> dict:
        """
        分析多篇股票博主文章，生成综合报告

        Args:
            articles: 文章列表，每篇包含 title/author/content 等字段

        Returns:
            dict: {"success": bool, "report": str, "error": str|None}
        """
        if not articles:
            return {"success": False, "report": "", "error": "没有可分析的文章"}

        articles_text = self._build_articles_text(articles)
        today = datetime.now().strftime("%Y年%m月%d日")

        prompt = f"""你是一位专业的股票投资分析助手。以下是我关注的多位股票博主在 {today} 前后发布的文章内容。

请帮我完成以下分析，用中文输出：

## 分析要求

1. **市场总览**: 综合所有博主观点，总结当前市场整体情绪和趋势
2. **热门板块/个股**: 提取被多位博主共同提及或重点推荐的板块和个股
3. **操作建议汇总**: 整理各博主的具体操作建议（买入/卖出/持有/观望）
4. **风险提示**: 汇总博主们提到的风险点和注意事项
5. **观点分歧**: 如果博主间存在不同看法，列出分歧点
6. **今日要点**: 用3-5个要点总结最重要的信息

请以结构化的 Markdown 格式输出报告。

---

以下是收集到的文章内容:

{articles_text}"""

        # 如果配置了AI API，调用API分析
        if self.config.ai.api_key:
            return await self._call_ai(
                prompt,
                system="你是专业的股票投资分析助手，擅长整合多方信息并给出结构化的分析报告。",
            )

        # 未配置AI时，返回简单的文本拼接报告
        return self._fallback_report(articles, today)

    async def analyze_with_personas(
        self, articles: list[dict], persona_ids: list[str]
    ) -> dict:
        """用多个投资人格视角并行分析文章，最终聚合成一份多视角报告。

        Args:
            articles: 文章列表
            persona_ids: 选中的人格 ID 列表（来自 agents.PERSONAS 的 key）

        Returns:
            {"success": bool, "report": str, "error": str|None}
        """
        if not articles:
            return {"success": False, "report": "", "error": "没有可分析的文章"}
        if not persona_ids:
            return {"success": False, "report": "", "error": "未选择任何分析视角"}
        if not self.config.ai.api_key:
            today = datetime.now().strftime("%Y年%m月%d日")
            return self._fallback_report(articles, today)

        # 过滤出有效 persona
        personas: list[Persona] = [PERSONAS[pid] for pid in persona_ids if pid in PERSONAS]
        if not personas:
            return {"success": False, "report": "", "error": "选中的视角均无效"}

        articles_text = self._build_articles_text(articles)
        today = datetime.now().strftime("%Y年%m月%d日")

        # 并行调用多个 persona
        tasks = [self._run_persona(p, articles_text, today) for p in personas]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        # 将异常转为错误结构，避免单个 persona 失败导致全部丢失
        results = []
        for i, r in enumerate(raw_results):
            if isinstance(r, Exception):
                logger.error(f"Persona {personas[i].name} 分析失败: {r}")
                results.append({"success": False, "report": "", "error": str(r)})
            else:
                results.append(r)

        # 拼装为单份多视角报告
        return self._assemble_report(personas, results, today, len(articles))

    async def _run_persona(
        self, persona: Persona, articles_text: str, today: str
    ) -> dict:
        """单个 persona 的 LLM 调用，失败时返回错误结构而不抛出。"""
        prompt = persona.user_template.format(articles_text=articles_text, today=today)
        return await self._call_ai(prompt, system=persona.system_prompt)

    def _assemble_report(
        self,
        personas: list[Persona],
        results: list[dict],
        today: str,
        article_count: int,
    ) -> dict:
        """把多个 persona 的输出拼成一份 Markdown 多视角报告。"""
        lines = [
            f"# 多视角投资分析报告 ({today})",
            "",
            f"基于 **{article_count}** 篇博主文章，从 **{len(personas)}** 个视角独立分析。",
            "",
        ]
        any_success = False
        errors: list[str] = []
        for persona, result in zip(personas, results):
            lines.append("---")
            lines.append("")
            lines.append(f"## {persona.icon} {persona.name}视角")
            lines.append(f"> {persona.tagline}")
            lines.append("")
            if result.get("success"):
                any_success = True
                lines.append(result["report"].strip())
            else:
                err = result.get("error") or "未知错误"
                errors.append(f"{persona.name}: {err}")
                lines.append(f"⚠️ 该视角分析失败：{err}")
            lines.append("")

        report = "\n".join(lines)
        if not any_success:
            return {
                "success": False,
                "report": report,
                "error": "所有视角均失败：" + " | ".join(errors),
            }
        # 即使部分视角失败也返回成功（报告里会标注失败的）
        return {"success": True, "report": report, "error": None}

    async def _call_ai(self, prompt: str, system: str) -> dict:
        """调用AI API进行分析（system prompt 由调用方决定）。"""
        try:
            client = get_async_proxy_client()
            response = await client.post(
                f"{self.config.ai.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.ai.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.ai.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                return {"success": False, "report": "", "error": "AI返回空结果（配额耗尽或内容被过滤）"}
            report = choices[0].get("message", {}).get("content", "")
            if not report:
                return {"success": False, "report": "", "error": "AI返回内容为空"}
            return {"success": True, "report": report, "error": None}

        except Exception as e:
            logger.error(f"AI分析失败: {e}")
            return {"success": False, "report": "", "error": f"AI分析失败: {str(e)}"}

    def _fallback_report(self, articles: list[dict], today: str) -> dict:
        """未配置AI时的降级报告 - 纯文本整合"""
        lines = [
            f"# 股票博主文章汇总 ({today})",
            "",
            f"共收集 **{len(articles)}** 篇文章",
            "",
            "---",
            "",
        ]
        for i, article in enumerate(articles, 1):
            lines.extend([
                f"## {i}. {article.get('title', '未知标题')}",
                f"**作者**: {article.get('author', '未知')} | **时间**: {article.get('publish_time', '未知')}",
                "",
                article.get("content", "无内容"),
                "",
                "---",
                "",
            ])

        lines.append("\n> 提示: 配置AI API密钥后可获得智能分析报告，而非简单汇总。")
        return {"success": True, "report": "\n".join(lines), "error": None}

    async def extract_mentions(self, articles: list[dict]) -> dict:
        """扫描文章中提及的股票，输出候选列表（不做荐股判断）。

        Returns:
            {"success": bool, "mentions": [{"stock_code": str, "stock_name": str,
              "context": str, "confidence": str, "article_url": str}], "error": str|None}
        """
        if not articles:
            return {"success": True, "mentions": [], "error": None}
        if not self.config.ai.api_key:
            return {"success": True, "mentions": [], "error": None}

        articles_text = self._build_articles_text(articles)

        prompt = f"""请扫描以下股票博主文章，列出所有被提及的 A 股/港股/美股的股票。

规则：
1. 只提取有明确股票名称或代码的提及（如"贵州茅台""600519""茅台"）
2. 对于隐喻/暗语/不确定的提及也列出，但 confidence 标为 "low"
3. 每条记录附上原文上下文（50 字以内），方便人工判断
4. 不要做是否为"荐股"的判断，只做"提到了什么股票"的扫描

输出严格的 JSON（不要 markdown 代码块）：
{{"mentions": [{{"stock_code": "", "stock_name": "贵州茅台", "context": "原文片段", "confidence": "high", "article_url": ""}}]}}

confidence 含义：
- "high": 明确提到了股票名称或代码
- "medium": 可推断但有歧义（如"白酒龙头"大概率是茅台）
- "low": 隐喻/暗语/不确定

文章内容：

{articles_text}"""

        system = "你是股票文本扫描助手，任务是从文章中提取所有被提及的股票名称。只做扫描，不做荐股判断。输出严格 JSON。"

        raw = ""
        try:
            client = get_async_proxy_client()
            response = await client.post(
                f"{self.config.ai.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.ai.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.ai.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2048,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                return {"success": False, "mentions": [], "error": "AI返回空结果"}
            raw = choices[0].get("message", {}).get("content", "")
            if not raw:
                return {"success": False, "mentions": [], "error": "AI返回内容为空"}

            # 解析 JSON（兼容 markdown 代码块包裹的情况）
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            parsed = json.loads(text)
            mentions = parsed.get("mentions", [])

            # 补充 article_url（AI 可能遗漏）
            for m in mentions:
                if not m.get("article_url"):
                    m["article_url"] = ""

            return {"success": True, "mentions": mentions, "error": None}

        except json.JSONDecodeError:
            logger.warning(f"extract_mentions JSON 解析失败: {raw[:200]}")
            return {"success": False, "mentions": [], "error": "AI 返回格式异常"}
        except Exception as e:
            logger.error(f"extract_mentions 失败: {e}")
            return {"success": False, "mentions": [], "error": str(e)}
