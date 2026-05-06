"""SQLite 数据库连接管理 — 单连接，WAL 模式，启动时自动建表。"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger("database")

_db: sqlite3.Connection | None = None
_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    """获取全局单例数据库连接。首次调用前必须先调用 init_db()。"""
    global _db
    if _db is None:
        raise RuntimeError("数据库未初始化，请先调用 init_db()")
    return _db


def init_db(db_path: str | None = None) -> None:
    """初始化数据库：创建连接、开启 WAL、建表、清理过期缓存。

    多次调用安全——仅首次生效。
    """
    global _db
    with _lock:
        if _db is not None:
            return

        if db_path is None:
            db_path = Path(__file__).parent.parent / "data.db"

        _db = sqlite3.connect(str(db_path), check_same_thread=False)
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA busy_timeout=5000")

        _db.executescript("""
            CREATE TABLE IF NOT EXISTS cache (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                expires_at  REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);

            CREATE TABLE IF NOT EXISTS watchlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                name        TEXT,
                added_at    TEXT NOT NULL DEFAULT (datetime('now')),
                note        TEXT DEFAULT '',
                sort_order  INTEGER DEFAULT 0,
                UNIQUE(symbol, market)
            );

            CREATE TABLE IF NOT EXISTS portfolios (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS positions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id  INTEGER NOT NULL REFERENCES portfolios(id),
                symbol        TEXT NOT NULL,
                market        TEXT NOT NULL,
                name          TEXT,
                shares        REAL NOT NULL,
                buy_price     REAL NOT NULL,
                buy_date      TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(portfolio_id, symbol, market)
            );

            CREATE TABLE IF NOT EXISTS backtests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                strategy    TEXT NOT NULL,
                params      TEXT NOT NULL,
                start_date  TEXT NOT NULL,
                end_date    TEXT NOT NULL,
                result      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # 清理过期缓存
        deleted = _db.execute(
            "DELETE FROM cache WHERE expires_at < ?", (time.time(),)
        ).rowcount
        if deleted:
            logger.info(f"清理过期缓存: {deleted} 条")

        _db.commit()
        logger.info("数据库初始化完成")


def close_db() -> None:
    """优雅关闭数据库连接并置空全局引用。"""
    global _db
    with _lock:
        if _db is not None:
            _db.close()
            _db = None
            logger.info("数据库连接已关闭")
