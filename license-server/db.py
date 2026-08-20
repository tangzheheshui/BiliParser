"""SQLite 访问层：licenses / usage / user_secrets / prompts / web_sessions。"""

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
    daily_quota INTEGER DEFAULT 100,
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
    provider TEXT,                 -- 买家的 AI 提供商：zhipu / deepseek
    api_key_enc TEXT,              -- 买家的 API key（加密）
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
    license_id INTEGER NOT NULL REFERENCES licenses(id),
    id TEXT NOT NULL,              -- p<hex 时间戳>，同 web 工作台
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    PRIMARY KEY (license_id, id)
);

CREATE TABLE IF NOT EXISTS web_sessions (
    license_id INTEGER NOT NULL REFERENCES licenses(id),
    sid TEXT NOT NULL,             -- 网页会话 id（登录时发，限并发防共享）
    created_at TEXT,
    last_seen TEXT,
    PRIMARY KEY (license_id, sid)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接并确保表结构存在。row_factory 让调用方按列名取值。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 迁移：老库的 user_secrets 无 provider/api_key_enc 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(user_secrets)")]
    if "provider" not in cols:
        conn.execute("ALTER TABLE user_secrets ADD COLUMN provider TEXT")
    if "api_key_enc" not in cols:
        conn.execute("ALTER TABLE user_secrets ADD COLUMN api_key_enc TEXT")
    return conn
