"""Shared application state — singleton instances for config, scraper, blogger_mgr.

这些实例在 app.py 中被 import，然后 load_saved_config() 在模块级修改 config。
router 文件通过 `from state import config / blogger_mgr / scraper / CONFIG_FILE` 访问。
"""

from pathlib import Path
from config import AppConfig
from scraper import WeixinScraper
from blogger import BloggerManager

config = AppConfig.from_env()
scraper = WeixinScraper()
blogger_mgr = BloggerManager(scraper, config)
CONFIG_FILE = Path(__file__).parent.parent / "user_config.json"
