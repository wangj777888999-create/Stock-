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
# 序列化所有写操作，防止调度线程与请求线程并发写入同一连接
write_lock = threading.Lock()


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
        _db.row_factory = sqlite3.Row
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

            CREATE TABLE IF NOT EXISTS trade_journal (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                entry_date  TEXT,
                exit_date   TEXT,
                entry_price REAL,
                exit_price  REAL,
                quantity    REAL,
                reason      TEXT,
                reflection  TEXT,
                tags        TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sim_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                price       REAL NOT NULL,
                quantity    REAL NOT NULL,
                fee         REAL DEFAULT 0,
                trade_date  TEXT NOT NULL,
                status      TEXT DEFAULT 'open',
                closed_at   TEXT,
                close_price REAL,
                pnl         REAL,
                note        TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS blogger_calls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                blogger_id  TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                call_type   TEXT,
                call_date   TEXT NOT NULL,
                call_price  REAL,
                target_price REAL,
                article_url TEXT,
                notes       TEXT,
                verified    INTEGER DEFAULT 0,
                verified_at TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS real_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                price       REAL NOT NULL,
                quantity    REAL NOT NULL,
                fee         REAL DEFAULT 0,
                trade_date  TEXT NOT NULL,
                source      TEXT DEFAULT 'manual',
                note        TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recommendation_scores (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id INTEGER NOT NULL REFERENCES blogger_calls(id),
                check_date        TEXT NOT NULL,
                current_price     REAL,
                return_pct        REAL,
                max_gain_pct      REAL,
                max_drawdown_pct  REAL,
                holding_days      INTEGER
            );

            CREATE TABLE IF NOT EXISTS scraped_articles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                blogger_id      TEXT NOT NULL,
                title           TEXT NOT NULL,
                url             TEXT NOT NULL UNIQUE,
                author          TEXT,
                publish_time    TEXT,
                content         TEXT,
                cover_url       TEXT,
                ai_mentions     TEXT,
                scrape_date     TEXT NOT NULL DEFAULT (date('now')),
                processed       INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS chart_drawings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL DEFAULT 'a',
                period      TEXT NOT NULL DEFAULT 'day',
                type        TEXT NOT NULL,
                data        TEXT NOT NULL,
                color       TEXT DEFAULT '#2563EB',
                label       TEXT DEFAULT '',
                visible     INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_drawings_symbol ON chart_drawings(symbol, market, period);

            CREATE TABLE IF NOT EXISTS stock_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL DEFAULT 'a',
                title       TEXT DEFAULT '',
                content     TEXT NOT NULL DEFAULT '',
                tags        TEXT DEFAULT '',
                note_date   TEXT DEFAULT (date('now')),
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_notes_symbol ON stock_notes(symbol, market);

            CREATE TABLE IF NOT EXISTS custom_pattern_rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                rule_type   TEXT    NOT NULL,
                params      TEXT    NOT NULL DEFAULT '{}',
                color       TEXT    DEFAULT '#22c55e',
                position    TEXT    DEFAULT 'belowBar',
                enabled     INTEGER DEFAULT 1,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS roles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                avatar_color    TEXT DEFAULT '#2563EB',
                initial_capital REAL NOT NULL DEFAULT 100000.0,
                notes           TEXT DEFAULT '',
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS industry_reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                industry    TEXT NOT NULL,
                purpose     TEXT DEFAULT 'investment',
                report_text TEXT NOT NULL DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            );

        """)

        # executescript() 会提交事务并可能重置 session-level PRAGMA，
        # 因此在它完成后再启用外键约束，确保约束对后续所有操作生效。
        _db.execute("PRAGMA foreign_keys=ON")

        # 清理过期缓存
        deleted = _db.execute(
            "DELETE FROM cache WHERE expires_at < ?", (time.time(),)
        ).rowcount
        if deleted:
            logger.info(f"清理过期缓存: {deleted} 条")

        _migrate()
        _db.commit()
        logger.info("数据库初始化完成")


def _migrate():
    """增量迁移：添加新列。忽略 'duplicate column name' 错误。"""
    # 新表迁移：CREATE TABLE IF NOT EXISTS 对已有表无副作用，适合存量数据库升级
    new_tables = [
        """CREATE TABLE IF NOT EXISTS flow_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            sort_order  INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS flow_category_stocks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES flow_categories(id) ON DELETE CASCADE,
            symbol      TEXT NOT NULL,
            name        TEXT,
            added_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(category_id, symbol)
        )""",
        """CREATE TABLE IF NOT EXISTS review_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            name       TEXT,
            note       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    ]
    for sql in new_tables:
        try:
            _db.execute(sql)
        except sqlite3.OperationalError as e:
            logger.error(f"数据库迁移失败(新表): {e}")
            raise

    migrations = [
        "ALTER TABLE watchlist ADD COLUMN tags TEXT DEFAULT ''",
        "ALTER TABLE watchlist ADD COLUMN alert_price REAL",
        "ALTER TABLE watchlist ADD COLUMN target_price REAL",
        "ALTER TABLE blogger_calls ADD COLUMN ai_reason TEXT",
        "ALTER TABLE blogger_calls ADD COLUMN status TEXT DEFAULT 'pending'",
        "ALTER TABLE blogger_calls ADD COLUMN user_confirmed INTEGER DEFAULT 0",
        # 多角色系统
        "ALTER TABLE sim_trades ADD COLUMN role_id INTEGER REFERENCES roles(id)",
    ]
    for sql in migrations:
        try:
            _db.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.error(f"数据库迁移失败: {sql} — {e}")
                raise

    # 角色数据迁移：创建默认角色并挂入旧交易
    existing = _db.execute("SELECT id FROM roles LIMIT 1").fetchone()
    if not existing:
        cur = _db.execute(
            "INSERT INTO roles (name, initial_capital, notes) VALUES (?, ?, ?)",
            ("默认账户", 100000.0, "从旧版单账户模拟交易迁移"),
        )
        default_id = cur.lastrowid
        _db.execute(
            "UPDATE sim_trades SET role_id = ? WHERE role_id IS NULL",
            (default_id,),
        )
        logger.info(f"角色迁移完成：创建默认账户 id={default_id}")


def close_db() -> None:
    """优雅关闭数据库连接并置空全局引用。"""
    global _db
    with _lock:
        if _db is not None:
            _db.close()
            _db = None
            logger.info("数据库连接已关闭")
