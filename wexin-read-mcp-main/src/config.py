"""应用配置

支持从环境变量读取敏感信息，环境变量优先级高于配置文件。
敏感字段包括：邮箱密码、AI API密钥、微信Cookie/Token

环境变量列表:
  - SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD, SMTP_USE_SSL
  - AI_API_KEY, AI_BASE_URL, AI_MODEL
  - WECHAT_COOKIE, WECHAT_MP_COOKIE, WECHAT_MP_TOKEN
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def _get_env(key: str, default: str = "") -> str:
    """获取环境变量，支持空字符串返回默认值"""
    return os.getenv(key, default) or default


def _get_env_int(key: str, default: int) -> int:
    """获取整数环境变量"""
    val = os.getenv(key)
    if val:
        try:
            return int(val)
        except ValueError:
            logger.warning(f"环境变量 {key} 不是有效的整数: {val}")
    return default


def _get_env_bool(key: str, default: bool) -> bool:
    """获取布尔环境变量"""
    val = os.getenv(key)
    if val:
        return val.lower() in ("true", "1", "yes", "on")
    return default


@dataclass
class EmailConfig:
    """邮箱SMTP配置"""
    smtp_server: str = ""
    smtp_port: int = 465
    sender_email: str = ""
    sender_password: str = ""  # 授权码，非登录密码
    use_ssl: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        """从环境变量加载邮箱配置"""
        password = _get_env("SENDER_PASSWORD") or _get_env("EMAIL_PASSWORD", "")
        return cls(
            smtp_server=_get_env("SMTP_SERVER", ""),
            smtp_port=_get_env_int("SMTP_PORT", 465),
            sender_email=_get_env("SENDER_EMAIL", ""),
            sender_password=password,
            use_ssl=_get_env_bool("SMTP_USE_SSL", True),
        )


@dataclass
class AIConfig:
    """AI分析配置"""
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    @classmethod
    def from_env(cls) -> "AIConfig":
        """从环境变量加载AI配置"""
        return cls(
            api_key=_get_env("AI_API_KEY", ""),
            base_url=_get_env("AI_BASE_URL", "https://api.openai.com/v1"),
            model=_get_env("AI_MODEL", "gpt-4o-mini"),
        )


@dataclass
class WeChatConfig:
    """微信Cookie配置 — 用于获取公众号历史文章"""
    cookie: str = ""  # 完整 Cookie 字符串（读者端，方案B备用）
    mp_cookie: str = ""  # 公众号后台 mp.weixin.qq.com 的 Cookie
    mp_token: str = ""  # 公众号后台 URL 中的 token 参数

    @classmethod
    def from_env(cls) -> "WeChatConfig":
        """从环境变量加载微信配置"""
        return cls(
            cookie=_get_env("WECHAT_COOKIE", ""),
            mp_cookie=_get_env("WECHAT_MP_COOKIE", ""),
            mp_token=_get_env("WECHAT_MP_TOKEN", ""),
        )


@dataclass
class AppConfig:
    """应用全局配置"""
    email: EmailConfig = field(default_factory=EmailConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    max_concurrent_scrape: int = 3
    scrape_delay: float = 2.0  # 每次抓取间隔(秒)

    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量加载全部配置（优先级最高）"""
        return cls(
            email=EmailConfig.from_env(),
            ai=AIConfig.from_env(),
            wechat=WeChatConfig.from_env(),
            max_concurrent_scrape=_get_env_int("MAX_CONCURRENT_SCRAPE", 3),
            scrape_delay=float(_get_env("SCRAPE_DELAY", "2.0")),
        )

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "AppConfig":
        """
        加载配置的统一入口。

        加载顺序（后者优先）：
        1. 环境变量（最高优先级）
        2. 配置文件（如果指定）

        Args:
            config_path: 可选的配置文件路径

        Returns:
            AppConfig 实例
        """
        # 环境变量配置始终生效
        config = cls.from_env()

        # 如果提供了配置文件路径且文件存在，从文件加载并覆盖
        if config_path:
            from pathlib import Path
            import json

            path = Path(config_path)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))

                    # 合并配置（环境变量已设置的值不会被覆盖）
                    if not config.email.smtp_server and "smtp_server" in data.get("email", {}):
                        config.email.smtp_server = data["email"]["smtp_server"]
                    if not config.email.sender_email and "sender_email" in data.get("email", {}):
                        config.email.sender_email = data["email"]["sender_email"]
                    # 不从文件加载密码，只从环境变量

                    if not config.ai.api_key and "api_key" in data.get("ai", {}):
                        config.ai.api_key = data["ai"]["api_key"]

                    # 微信配置同样优先从环境变量
                    logger.info(f"已从配置文件加载非敏感配置: {config_path}")
                except Exception as e:
                    logger.warning(f"加载配置文件失败: {e}")

        return config
