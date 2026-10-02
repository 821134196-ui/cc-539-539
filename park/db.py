"""SQLite 数据层。

保存邀请、进出事件（核验/放行/结算）、离线重放队列、
异常放行复核、支付流水和闸机指令。单文件数据库，默认 data/park.db。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS units (
    code        TEXT PRIMARY KEY,          -- 单位编号，如 A / B
    name        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invitations (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,  -- 通行码（一次性）
    plate           TEXT NOT NULL,
    unit_code       TEXT NOT NULL,
    building        TEXT NOT NULL,
    contact         TEXT NOT NULL,
    enter_at        TEXT NOT NULL,         -- 批准入场
    leave_at        TEXT NOT NULL,         -- 批准离场
    status          TEXT NOT NULL DEFAULT 'active',
        -- active 有效 / entered 已入场 / exited 已出场 / revoked 已撤销 /
        -- exception 异常放行
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    revoke_reason   TEXT,
    revoke_at       TEXT,
    revoke_by       TEXT,
    FOREIGN KEY(unit_code) REFERENCES units(code)
);
CREATE INDEX IF NOT EXISTS idx_inv_plate ON invitations(plate);
CREATE INDEX IF NOT EXISTS idx_inv_status ON invitations(status);

-- 进出事件流水：一次核验/放行/结算都在这里留痕
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    inv_id      INTEGER,
    code        TEXT,                      -- 通行码（冗余，码失效后仍可查）
    plate       TEXT,
    unit_code   TEXT,
    kind        TEXT NOT NULL,
        -- deliver 邀请交付 / verify 核验 / gate_enter 放行入场 / settle 结算 / pay 支付 /
        -- gate_exit 放行出场 / exception 异常放行 / revoke 撤销 /
        -- offline_queue 离线暂存 / replay 重放 / admin_review 管理员复核 /
        -- gate_retry 开闸重试
    result      TEXT NOT NULL,             -- ok / denied / warning / pending
    reason      TEXT NOT NULL DEFAULT '',  -- 完整文字理由（不使用代码缩写）
    detail_json TEXT NOT NULL DEFAULT '',
    operator    TEXT NOT NULL DEFAULT '',
    photo_path  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_inv ON events(inv_id);
CREATE INDEX IF NOT EXISTS idx_ev_plate ON events(plate);
CREATE INDEX IF NOT EXISTS idx_ev_kind ON events(kind);

-- 实际进出记录（一辆车一次在园对应一行）
CREATE TABLE IF NOT EXISTS visits (
    id              INTEGER PRIMARY KEY,
    inv_id          INTEGER NOT NULL UNIQUE,  -- 一个邀请只能对应一次有效入场
    code            TEXT NOT NULL,
    plate           TEXT NOT NULL,
    unit_code       TEXT NOT NULL,
    actual_enter    TEXT NOT NULL,
    actual_exit     TEXT,
    settle_at       TEXT,                   -- 结算时间（支付后闸机重试也以它计费）
    amount          REAL NOT NULL DEFAULT 0,
    gate_enter_cmd  TEXT,
    gate_exit_cmd   TEXT,
    exception       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(inv_id) REFERENCES invitations(id)
);
CREATE INDEX IF NOT EXISTS idx_visit_open ON visits(actual_exit);

-- 支付流水（以业务幂等键去重：exit receipt 重试不重复收费）
CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY,
    idem_key        TEXT NOT NULL UNIQUE,
    inv_id          INTEGER NOT NULL,
    visit_id        INTEGER NOT NULL,
    amount          REAL NOT NULL,
    status          TEXT NOT NULL,          -- success / refunded
    channel         TEXT NOT NULL DEFAULT '模拟支付',
    trade_no        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- 异常免费放行：必须园区管理员复核
CREATE TABLE IF NOT EXISTS exception_reviews (
    id          INTEGER PRIMARY KEY,
    visit_id    INTEGER NOT NULL,
    inv_id      INTEGER NOT NULL,
    plate       TEXT NOT NULL,
    reason      TEXT NOT NULL,             -- 完整文字
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
    reviewer    TEXT,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT ''
);

-- 门禁离线时的核验暂存队列，恢复后逐条重放
CREATE TABLE IF NOT EXISTS offline_queue (
    id          INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL,
    queued_at   TEXT NOT NULL,
    replayed_at TEXT,
    result      TEXT NOT NULL DEFAULT 'pending'  -- pending/done/failed
);

-- 闸机指令（含离线未达与卡阻重试记录）
CREATE TABLE IF NOT EXISTS gate_commands (
    id          INTEGER PRIMARY KEY,
    visit_id    INTEGER,
    direction   TEXT NOT NULL,             -- enter / exit
    status      TEXT NOT NULL,             -- sent 已下发 / failed 卡阻未开 / offline 离线未达 / retried 重试成功
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gate_visit ON gate_commands(visit_id);

-- 系统可变状态（门禁在线标志等）
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Web 服务器多线程共享一条连接，调用侧以 RLock 串行化所有请求
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def tx(conn: sqlite3.Connection):
    """显式事务（SQLite 默认 autocommit 被我们关掉以外的保险）。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
