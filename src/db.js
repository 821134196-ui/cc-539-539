import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { DATA_DIR, PHOTO_DIR, DB_PATH } from './config.js';

let dbInstance = null;

export function openDb(dbPath = DB_PATH) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.mkdirSync(PHOTO_DIR, { recursive: true });
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA foreign_keys = ON;');
  migrate(db);
  return db;
}

export function getDb(dbPath = DB_PATH) {
  if (!dbInstance) dbInstance = openDb(dbPath);
  return dbInstance;
}

export function closeDb() {
  if (dbInstance) {
    dbInstance.close();
    dbInstance = null;
  }
}

// node:sqlite 没有 better-sqlite3 的 transaction()，自行包一层
export function withTx(db, fn) {
  db.exec('BEGIN');
  try {
    const result = fn();
    db.exec('COMMIT');
    return result;
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
}

function migrate(db) {
  db.exec(`
  CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE
  );

  -- 园区管理员：用于异常免费放行复核
  CREATE TABLE IF NOT EXISTS admins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS invitations (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,                 -- 通行码（6 位，不区分大小写）
    unit_id TEXT NOT NULL REFERENCES units(id),
    plate TEXT NOT NULL,                       -- 规范化大写车牌
    contact_name TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    building TEXT NOT NULL,                    -- 访问楼栋
    planned_arrival TEXT NOT NULL,             -- ISO 时间
    planned_departure TEXT NOT NULL,           -- ISO 时间
    status TEXT NOT NULL DEFAULT 'valid',      -- valid | used | cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (planned_departure > planned_arrival)
  );
  CREATE INDEX IF NOT EXISTS idx_invitations_plate ON invitations(plate);
  CREATE INDEX IF NOT EXISTS idx_invitations_unit ON invitations(unit_id);

  -- 进出事件：一个邀请只能对应一次有效入场（部分唯一索引保证）
  CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,                      -- 幂等键，离线重放靠它去重
    invitation_id TEXT NOT NULL REFERENCES invitations(id),
    plate TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('entry','exit')),
    happened_at TEXT NOT NULL,                -- 实际发生时间（门岗设备时间）
    recorded_at TEXT NOT NULL,                -- 服务端落库时间
    source TEXT NOT NULL DEFAULT 'online',    -- online | offline_replay
    gate_id TEXT,
    photo_path TEXT,                           -- 本地遮罩照片
    note TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_events_plate ON events(plate);
  CREATE INDEX IF NOT EXISTS idx_events_invitation ON events(invitation_id);
  -- 一张邀请至多一条入场、一条出场
  CREATE UNIQUE INDEX IF NOT EXISTS uniq_event_entry ON events(invitation_id) WHERE event_type = 'entry';
  CREATE UNIQUE INDEX IF NOT EXISTS uniq_event_exit ON events(invitation_id) WHERE event_type = 'exit';

  -- 出场回执：重试靠 request_id 幂等，绝不重复收费
  CREATE TABLE IF NOT EXISTS exit_receipts (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    invitation_id TEXT NOT NULL REFERENCES invitations(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    entered_at TEXT NOT NULL,
    exited_at TEXT NOT NULL,
    approved_end TEXT NOT NULL,
    free_minutes INTEGER NOT NULL,
    overtime_minutes INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    calc_detail TEXT NOT NULL,                -- JSON 拆分
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | paid | free_gate
    created_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES exit_receipts(id),
    request_id TEXT NOT NULL UNIQUE,          -- 支付请求幂等（支付后闸机未开重试）
    amount_cents INTEGER NOT NULL,
    provider_ref TEXT,
    status TEXT NOT NULL,                     -- success | failed
    created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_payments_receipt ON payments(receipt_id);

  -- 异常放行（含异常免费放行，需园区管理员复核）
  CREATE TABLE IF NOT EXISTS exception_releases (
    id TEXT PRIMARY KEY,
    plate TEXT,
    invitation_id TEXT REFERENCES invitations(id),
    reason_code TEXT NOT NULL,                -- 机读码
    reason_text TEXT NOT NULL,                -- 完整文字理由
    direction TEXT NOT NULL CHECK (direction IN ('entry','exit')),
    gate_id TEXT,
    requested_by TEXT NOT NULL,               -- 门岗/被访单位
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    reviewed_by TEXT REFERENCES admins(id),
    reviewed_at TEXT,
    review_note TEXT,
    note TEXT,                                -- 门岗现场说明
    amount_cents INTEGER NOT NULL DEFAULT 0,  -- free=0；异常放行也可能照章收费
    created_at TEXT NOT NULL
  );

  -- 闸机动作日志（模拟适配器）
  CREATE TABLE IF NOT EXISTS gate_logs (
    id TEXT PRIMARY KEY,
    gate_id TEXT NOT NULL,
    action TEXT NOT NULL,                     -- open | open_failed
    plate TEXT,
    context TEXT,                             -- 人工可读上下文
    created_at TEXT NOT NULL
  );

  -- 离线事件队列：门禁离线时先落本地，恢复后重放
  CREATE TABLE IF NOT EXISTS offline_queue (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,                  -- 与 events.id 相同，重放去重
    payload TEXT NOT NULL,                   -- JSON
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | replayed | failed
    created_at TEXT NOT NULL,
    replayed_at TEXT
  );

  -- 单位变更邀请的审计（单位隔离）
  CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    actor_unit TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
  );
  `);

  const row = db.prepare("SELECT COUNT(*) AS n FROM units").get();
  if (row.n === 0) seedBase(db);
}

function seedBase(db) {
  const now = new Date().toISOString();
  db.prepare("INSERT INTO units (id, name, code) VALUES (?, ?, ?)")
    .run('u-alpha', '阿尔法科技有限公司', 'ALPHA');
  db.prepare("INSERT INTO units (id, name, code) VALUES (?, ?, ?)")
    .run('u-beta', '贝塔设计工作室', 'BETA');
  db.prepare("INSERT INTO admins (id, name) VALUES (?, ?)")
    .run('admin-1', '园区管理员·王主任');
  db.prepare("INSERT INTO audit_log (id, actor_unit, action, target, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .run(cid(), null, 'system-init', null, '基础数据初始化', now);
}

export function cid(prefix = '') {
  return prefix + (globalThis.crypto
    ? crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
}
