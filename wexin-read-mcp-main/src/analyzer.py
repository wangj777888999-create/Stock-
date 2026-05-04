"""文章内容分析模块 - 整合多篇文章并生成投资分析报告"""

import asyncio
import httpx
import logging
from datetime import datetime

from agents import PERSONAS, Persona
from config import AppConfig

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
        results = await asyncio.gather(*tasks, return_exceptions=False)

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
        """调用AI API进行分析（system prompt 由调用方决定）"""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
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
                )
                response.raise_for_status()
                data = response.json()
                report = data["choices"][0]["message"]["content"]
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
