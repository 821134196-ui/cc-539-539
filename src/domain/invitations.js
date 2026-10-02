import { getDb, cid } from '../db.js';
import { normalizePlate, isValidPlate } from './plate.js';
import { parseTime, nowIso, minutesBetween } from './time.js';
import { AppError } from './errors.js';

function genCode(db) {
  for (let i = 0; i < 20; i++) {
    const code = Math.random().toString(36).slice(2, 8).toUpperCase();
    if (!/[0O1I]/.test(code) && !db.prepare('SELECT 1 FROM invitations WHERE code = ?').get(code)) return code;
  }
  throw new AppError('CODE_GEN_FAILED', '通行码生成失败，请重试');
}

// 被访单位申请临时通行
// 允许同一车牌存在多份邀请（同车牌多份邀请），返回时标注。
export function createInvitation(input, db = getDb()) {
  const unitId = String(input.unitId || '');
  const unit = db.prepare('SELECT * FROM units WHERE id = ?').get(unitId);
  if (!unit) throw new AppError('UNIT_NOT_FOUND', '被访单位不存在');

  const plate = normalizePlate(input.plate);
  if (!isValidPlate(plate)) throw new AppError('INVALID_PLATE', '车牌格式不正确，示例：京A12345');

  let arrival, departure;
  try {
    arrival = parseTime(input.plannedArrival).toISOString();
    departure = parseTime(input.plannedDeparture).toISOString();
  } catch {
    throw new AppError('INVALID_TIME', '预计到离时间格式不正确');
  }
  if (minutesBetween(arrival, departure) <= 0) {
    throw new AppError('INVALID_RANGE', '预计离场时间必须晚于到达时间');
  }
  for (const field of ['contactName', 'contactPhone', 'building']) {
    if (!String(input[field] || '').trim()) {
      throw new AppError('MISSING_FIELD', `缺少必填项：${field === 'contactName' ? '联系人' : field === 'contactPhone' ? '联系电话' : '访问楼栋'}`);
    }
  }

  const id = cid('inv_');
  const code = genCode(db);
  const now = nowIso();
  db.prepare(`INSERT INTO invitations
    (id, code, unit_id, plate, contact_name, contact_phone, building, planned_arrival, planned_departure, status, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?, 'valid', ?, ?)`)
    .run(id, code, unitId, plate,
      String(input.contactName).trim(), String(input.contactPhone).trim(), String(input.building).trim(),
      arrival, departure, now, now);

  db.prepare('INSERT INTO audit_log (id, actor_unit, action, target, detail, created_at) VALUES (?,?,?,?,?,?)')
    .run(cid('al_'), unitId, 'invitation-create', id, `车牌 ${plate} / 通行码 ${code} / 楼栋 ${input.building}`, now);

  return getInvitation(id, db);
}

export function getInvitation(id, db = getDb()) {
  return db.prepare(`SELECT i.*, u.name AS unit_name, u.code AS unit_code
                     FROM invitations i JOIN units u ON u.id = i.unit_id WHERE i.id = ?`).get(id);
}

export function getInvitationByCode(code, db = getDb()) {
  return db.prepare(`SELECT i.*, u.name AS unit_name, u.code AS unit_code
                     FROM invitations i JOIN units u ON u.id = i.unit_id WHERE i.code = ?`)
    .get(String(code || '').toUpperCase().trim());
}

// 同车牌的全部邀请（门岗按车牌核验时展示，处理“同车牌多份邀请”）
export function findInvitationsByPlate(plate, db = getDb()) {
  return db.prepare(`SELECT i.*, u.name AS unit_name, u.code AS unit_code
                     FROM invitations i JOIN units u ON u.id = i.unit_id
                     WHERE i.plate = ? ORDER BY i.planned_arrival DESC`).all(normalizePlate(plate));
}

export function listInvitations({ unitId = null, includeAll = false } = {}, db = getDb()) {
  // 单位隔离：普通单位只看本单位；园区管理员(includeAll)可看全部
  if (includeAll) {
    return db.prepare(`SELECT i.*, u.name AS unit_name, u.code AS unit_code
                       FROM invitations i JOIN units u ON u.id = i.unit_id
                       ORDER BY i.created_at DESC`).all();
  }
  if (!unitId) throw new AppError('FORBIDDEN', '需要指定单位');
  return db.prepare(`SELECT i.*, u.name AS unit_name, u.code AS unit_code
                     FROM invitations i JOIN units u ON u.id = i.unit_id
                     WHERE i.unit_id = ? ORDER BY i.created_at DESC`).all(unitId);
}

function assertOwns(id, unitId, db) {
  const inv = getInvitation(id, db);
  if (!inv) throw new AppError('INV_NOT_FOUND', '邀请不存在');
  if (inv.unit_id !== unitId) {
    db.prepare('INSERT INTO audit_log (id, actor_unit, action, target, detail, created_at) VALUES (?,?,?,?,?,?)')
      .run(cid('al_'), unitId, 'invitation-modify-denied', id,
        `单位 ${unitId} 试图修改外单位邀请（归属 ${inv.unit_id}），已拒绝`, nowIso());
    throw new AppError('FORBIDDEN_UNIT', '单位隔离：被访单位不能替其他单位修改邀请');
  }
  return inv;
}

// 只能改自己单位的邀请；已使用/已取消不可再改时段
export function updateInvitation(id, unitId, patch, db = getDb()) {
  const inv = assertOwns(id, unitId, db);
  if (inv.status !== 'valid') throw new AppError('INV_LOCKED', `邀请已${inv.status === 'used' ? '使用' : '取消'}，不可修改`);

  const fields = [];
  const vals = [];
  if (patch.plate !== undefined) {
    // 车牌录错：被访单位可在入场前更正车牌，留下审计
    const plate = normalizePlate(patch.plate);
    if (!isValidPlate(plate)) throw new AppError('INVALID_PLATE', '车牌格式不正确，示例：京A12345');
    fields.push('plate = ?'); vals.push(plate);
  }
  if (patch.plannedArrival || patch.plannedDeparture) {
    const a = parseTime(patch.plannedArrival || inv.planned_arrival).toISOString();
    const d = parseTime(patch.plannedDeparture || inv.planned_departure).toISOString();
    if (new Date(d) <= new Date(a)) throw new AppError('INVALID_RANGE', '预计离场时间必须晚于到达时间');
    fields.push('planned_arrival = ?', 'planned_departure = ?'); vals.push(a, d);
  }
  for (const f of ['contactName', 'contactPhone', 'building']) {
    const col = f.replace(/([A-Z])/g, '_$1').toLowerCase();
    if (patch[f] !== undefined) {
      if (!String(patch[f]).trim()) throw new AppError('MISSING_FIELD', '联系信息不可为空');
      fields.push(`${col} = ?`); vals.push(String(patch[f]).trim());
    }
  }
  if (!fields.length) return inv;
  fields.push('updated_at = ?'); vals.push(nowIso());
  vals.push(id);
  db.prepare(`UPDATE invitations SET ${fields.join(', ')} WHERE id = ?`).run(...vals);
  db.prepare('INSERT INTO audit_log (id, actor_unit, action, target, detail, created_at) VALUES (?,?,?,?,?,?)')
    .run(cid('al_'), unitId, 'invitation-update', id, '入场前更正/变更邀请信息', nowIso());
  return getInvitation(id, db);
}

export function cancelInvitation(id, unitId, db = getDb()) {
  const inv = assertOwns(id, unitId, db);
  if (inv.status === 'used') throw new AppError('INV_LOCKED', '车辆已入场，不能取消邀请，请走异常出场流程');
  db.prepare("UPDATE invitations SET status = 'cancelled', updated_at = ? WHERE id = ?").run(nowIso(), id);
  db.prepare('INSERT INTO audit_log (id, actor_unit, action, target, detail, created_at) VALUES (?,?,?,?,?,?)')
    .run(cid('al_'), unitId, 'invitation-cancel', id, '被访单位取消邀请', nowIso());
  return getInvitation(id, db);
}

export function listUnits(db = getDb()) {
  return db.prepare('SELECT * FROM units ORDER BY code').all();
}
