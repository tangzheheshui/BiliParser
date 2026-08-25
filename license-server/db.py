"""SQLite 访问层：license_codes（激活码三态）+ activate_logs（激活审计）。

表结构按 docs/requirements/服务器需求文档.md v2.2 第 7 节。
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS license_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sn TEXT UNIQUE NOT NULL,            -- 激活码，统一大写
    status TEXT NOT NULL DEFAULT 'unshipped',  -- unshipped / shipped / activated
    shipped_at TEXT,                    -- 发货（被取走）时间；退回时清空
    bound_mac TEXT,                     -- 规范化后 MAC；NULL = 未激活
    remark TEXT DEFAULT '',             -- 备注（批次等）
    expires_at TEXT,                    -- 预留，NULL = 永久（本期恒为 NULL）
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    activated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_codes_status ON license_codes(status, id);

CREATE TABLE IF NOT EXISTS activate_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sn TEXT,                            -- 上送的序列号（可为空）
    mac TEXT,                           -- 上送的 MAC
    result_code INTEGER,                -- 本次返回业务码
    ip TEXT,                            -- 来源 IP
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接并确保表结构存在。row_factory 让调用方按列名取值。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
