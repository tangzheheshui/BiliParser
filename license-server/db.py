"""SQLite 访问层：licenses + usage 两张表。"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    note TEXT DEFAULT '',
    device_fingerprint TEXT,
    activated_at TEXT,
    expires_at TEXT,               -- ISO 时间；NULL = 永久
    is_active INTEGER DEFAULT 1,
    daily_quota INTEGER DEFAULT 50,
    unbind_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage (
    license_id INTEGER NOT NULL REFERENCES licenses(id),
    day TEXT NOT NULL,             -- 本地日期 YYYY-MM-DD
    count INTEGER DEFAULT 0,
    PRIMARY KEY (license_id, day)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接并确保表结构存在。row_factory 让调用方按列名取值。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
