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

CREATE TABLE IF NOT EXISTS user_secrets (
    license_id INTEGER PRIMARY KEY REFERENCES licenses(id),
    sessdata_enc TEXT,             -- 用户的 B 站 SESSDATA（SERVER_SECRET 派生密钥加密）
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
    license_id INTEGER NOT NULL REFERENCES licenses(id),
    id TEXT NOT NULL,              -- p<hex 时间戳>，同 web 工作台
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    PRIMARY KEY (license_id, id)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接并确保表结构存在。row_factory 让调用方按列名取值。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
