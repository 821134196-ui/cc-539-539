import { getDb, cid, withTx } from '../db.js';
import { normalizePlate, maskPlate } from './plate.js';
import { nowIso, parseTime, minutesBetween } from './time.js';
import { calcCharge, REASON_TEXTS } from './billing.js';
import { GRACE_MINUTES_EARLY, PRICING } from '../config.js';
import { captureMaskedPhoto } from '../adapters/camera.js';
import { commandGateOpen, recordGateDenied } from '../adapters/gate.js';
import { pay as payAdapter } from '../adapters/payment.js';
import { getInvitation, getInvitationByCode } from './invitations.js';
import { AppError } from './errors.js';

// ---------- 查询辅助 ----------
export function getEvents(invitationId, db = getDb()) {
  const rows = db.prepare('SELECT * FROM events WHERE invitation_id = ? ORDER BY happened_at').all(invitationId);
  return {
    entry: rows.find((r) => r.event_type === 'entry') || null,
    exit: rows.find((r) => r.event_type === 'exit') || null,
  };
}

// 门岗核验：按码或车牌查询，返回能不能进/出的完整判断（不落库）
export function verifyAtGate({ code, plate, gateId = 'GATE-01', at = nowIso() }, db = getDb()) {
  let invitation = null;
  let samePlateOthers = [];
  if (code) {
    invitation = getInvitationByCode(code, db);
    if (!invitation) throw new AppError('CODE_NOT_FOUND', '通行码不存在，请核对后重试');
    samePlateOthers = db.prepare(
      `SELECT * FROM invitations WHERE plate = ? AND id <> ? ORDER BY planned_arrival DESC`)
      .all(invitation.plate, invitation.id);
  } else if (plate) {
    const list = db.prepare(
      `SELECT i.*, u.name AS unit_name, u.code AS unit_code
       FROM invitations i JOIN units u ON u.id = i.unit_id
       WHERE i.plate = ? ORDER BY i.planned_arrival DESC`).all(normalizePlate(plate));
    if (!list.length) throw new AppError('PLATE_NO_INVITATION', '该车牌没有可匹配的访客邀请');
    invitation = list[0];
    samePlateOthers = list.slice(1);
  } else {
    throw new AppError('MISSING_QUERY', '请输入通行码或车牌');
  }

  const { entry, exit } = getEvents(invitation.id, db);
  const t = parseTime(at);
  const assessment = {
    code: 'OK',
    message: '在批准时段内，可正常入场',
    earlyMinutes: 0,
  };

  if (invitation.status === 'cancelled') {
    assessment.code = 'CANCELLED'; assessment.message = '邀请已被被访单位取消';
  } else if (entry) {
    assessment.code = 'CODE_REUSED';
    assessment.message = '该邀请已完成一次有效入场（一邀一入），通行码转发无效，禁止重复入场';
  } else if (t > parseTime(invitation.planned_departure)) {
    assessment.code = 'WINDOW_EXPIRED';
    assessment.message = '已晚于批准离场时间，邀请时段失效，需异常放行';
  } else if (t < parseTime(invitation.planned_arrival)) {
    const early = minutesBetween(t.toISOString(), invitation.planned_arrival);
    assessment.earlyMinutes = early;
    if (early <= GRACE_MINUTES_EARLY) {
      assessment.code = 'EARLY_WITHIN_GRACE';
      assessment.message = `提前 ${early} 分钟到达，在 ${GRACE_MINUTES_EARLY} 分钟免费等待宽限内，可入场`;
    } else {
      assessment.code = 'EARLY_BEYOND_GRACE';
      assessment.message = `提前 ${early} 分钟到达，超出 ${GRACE_MINUTES_EARLY} 分钟宽限，需异常放行审批`;
    }
  }

  return { invitation, samePlateOthers, entry, exit, gateId, at, assessment, maskedPlate: maskPlate(invitation.plate) };
}

// ---------- 入场 ----------
export function admitEntry({ code, observedPlate, gateId = 'GATE-01', at = nowIso(),
                            eventId = cid('ev_'), source = 'online', note = '' }, db = getDb()) {
  const v = verifyAtGate({ code, gateId, at }, db);
  const inv = v.invitation;

  if (v.entry) {
    recordException({
      plate: inv.plate, invitationId: inv.id, reasonCode: 'CODE_REUSED',
      direction: 'entry', gateId, requestedBy: '门岗系统', amountCents: 0,
      note: `通行码 ${inv.code} 被再次使用，来源：${source}；系统自动拦截，一邀一入`,
      forceStatus: 'rejected',
    }, db);
    recordGateDenied({ gateId, plate: inv.plate, context: '拒绝放行：通行码重复使用（一邀一入）', db });
    throw new AppError('CODE_REUSED', v.assessment.message, { invitation: inv });
  }
  if (inv.status === 'cancelled') {
    recordException({
      plate: inv.plate, invitationId: inv.id, reasonCode: 'INV_CANCELLED',
      direction: 'entry', gateId, requestedBy: '门岗系统', note: `持已取消邀请的码 ${inv.code} 入场，已拦截`,
      forceStatus: 'rejected',
    }, db);
    throw new AppError('INV_CANCELLED', '邀请已取消，禁止入场');
  }
  const observed = normalizePlate(observedPlate || inv.plate);
  if (observed !== normalizePlate(inv.plate)) {
    recordException({
      plate: observed, invitationId: inv.id, reasonCode: 'PLATE_MISMATCH',
      direction: 'entry', gateId, requestedBy: '门岗系统',
      note: `门岗识别 ${observed} 与邀请登记 ${inv.plate} 不符，疑似车牌录错，已拦截，请更正车牌或走异常放行`,
      forceStatus: 'pending',
    }, db);
    throw new AppError('PLATE_MISMATCH',
      `车牌不符：门岗识别 ${observed}，邀请登记 ${inv.plate}（疑似录错），请被访单位更正或申请异常放行`);
  }
  if (v.assessment.code === 'EARLY_BEYOND_GRACE' || v.assessment.code === 'WINDOW_EXPIRED') {
    throw new AppError(v.assessment.code, v.assessment.message + '，请提交异常放行申请');
  }

  return doInsertEntry({ inv, gateId, at, eventId, source, note }, db);
}

function doInsertEntry({ inv, gateId, at, eventId, source, note }, db) {
  // 幂等：同一 event_id 重放直接返回旧记录
  const dup = db.prepare('SELECT * FROM events WHERE id = ?').get(eventId);
  if (dup) return { duplicated: true, event: dup, invitation: inv, gate: { opened: true, reused: true } };

  const photo = captureMaskedPhoto({ plate: inv.plate, gateId, atIso: at });
  withTx(db, () => {
    db.prepare(`INSERT INTO events (id, invitation_id, plate, event_type, happened_at, recorded_at, source, gate_id, photo_path, note)
                VALUES (?,?,?, 'entry', ?, ?, ?, ?, ?, ?)`)
      .run(eventId, inv.id, inv.plate, parseTime(at).toISOString(), nowIso(), source, gateId, photo, note);
    db.prepare("UPDATE invitations SET status = 'used', updated_at = ? WHERE id = ?").run(nowIso(), inv.id);
  });

  const gate = commandGateOpen({ gateId, plate: inv.plate, context: `放行入场：邀请 ${inv.code}`, db });
  return { duplicated: false, event: db.prepare('SELECT * FROM events WHERE id = ?').get(eventId), invitation: inv, gate, photo };
}

// ---------- 出场回执与计费 ----------
export function buildExitReceipt(invitationId, at = nowIso(), db = getDb()) {
  const inv = getInvitation(invitationId, db);
  if (!inv) throw new AppError('INV_NOT_FOUND', '邀请不存在');
  const { entry, exit } = getEvents(invitationId, db);
  if (!entry) throw new AppError('NO_ENTRY', '没有入场记录，不能直接出场，请走异常放行');
  if (exit) {
    const old = db.prepare('SELECT * FROM exit_receipts WHERE invitation_id = ?').get(invitationId);
    return { duplicated: true, receipt: old, detail: old ? JSON.parse(old.calc_detail) : null, invitation: inv };
  }
  const detail = calcCharge({
    enteredAt: entry.happened_at,
    exitedAt: parseTime(at).toISOString(),
    plannedArrival: inv.planned_arrival,
    plannedDeparture: inv.planned_departure,
  });
  return { duplicated: false, detail, invitation: inv, entry };
}

// 出场：计算并落回执（幂等）。超时则待支付；不超时直接免费放行开闸。
export function processExit({ code, plate, gateId = 'GATE-01', at = nowIso(),
                              requestId = cid('req_'), source = 'online' }, db = getDb()) {
  let inv;
  if (code) {
    inv = getInvitationByCode(code, db);
    if (!inv) throw new AppError('CODE_NOT_FOUND', '通行码不存在');
  } else {
    const rows = db.prepare(
      `SELECT i.*, u.name AS unit_name, u.code AS unit_code FROM invitations i
       JOIN units u ON u.id = i.unit_id WHERE i.plate = ? ORDER BY planned_arrival DESC`).all(normalizePlate(plate));
    if (!rows.length) throw new AppError('PLATE_NO_INVITATION', '该车牌没有邀请记录');
    const inPark = rows.find((r) => {
      const e = getEvents(r.id, db);
      return e.entry && !e.exit;
    });
    if (!inPark) throw new AppError('NOT_IN_PARK', '该车牌当前没有在园车辆记录');
    inv = inPark;
  }

  const built = buildExitReceipt(inv.id, at, db);
  // 重试：已有出场与回执 —— 原样返回，绝不重复计费
  if (built.duplicated) {
    const receipt = built.receipt;
    const gate = receipt && receipt.status === 'free_gate'
      ? commandGateOpen({ gateId, plate: inv.plate, context: `重试开闸（免费）：${receipt.id}`, db })
      : { opened: Boolean(receipt && receipt.status === 'paid'), reused: true, reason: receipt?.status === 'pending' ? '回执待支付，重试不产生新费用' : null };
    return { duplicated: true, receipt, detail: built.detail, invitation: inv, gate };
  }

  const { detail } = built;
  const photo = captureMaskedPhoto({ plate: inv.plate, gateId, atIso: at });
  const receiptId = cid('rc_');
  const exitEventId = cid('ev_');

  withTx(db, () => {
    db.prepare(`INSERT INTO events (id, invitation_id, plate, event_type, happened_at, recorded_at, source, gate_id, photo_path, note)
                VALUES (?,?,?, 'exit', ?, ?, ?, ?, ?, ?)`)
      .run(exitEventId, inv.id, inv.plate, detail.exitedAt, nowIso(), source, gateId, photo,
        detail.overnight ? '跨夜停留出场' : '');
    db.prepare(`INSERT INTO exit_receipts
      (id, request_id, invitation_id, event_id, entered_at, exited_at, approved_end,
       free_minutes, overtime_minutes, amount_cents, calc_detail, status, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)`)
      .run(receiptId, requestId, inv.id, exitEventId, detail.enteredAt, detail.exitedAt,
        detail.plannedDeparture, detail.freeMinutes, detail.overtimeMinutes, detail.amountCents,
        JSON.stringify(detail), nowIso());
  });

  let receipt = db.prepare('SELECT * FROM exit_receipts WHERE id = ?').get(receiptId);

  // 不超时：免费放行并开闸
  if (detail.amountCents === 0) {
    db.prepare("UPDATE exit_receipts SET status = 'free_gate' WHERE id = ?").run(receiptId);
    const gate = commandGateOpen({ gateId, plate: inv.plate, context: `免费放行出场：回执 ${receiptId}`, db });
    receipt = db.prepare('SELECT * FROM exit_receipts WHERE id = ?').get(receiptId);
    return { duplicated: false, receipt, detail, invitation: inv, gate };
  }

  return { duplicated: false, receipt, detail, invitation: inv, gate: { opened: false, reason: '存在超时费用，支付后开闸' } };
}

// 支付（幂等）+ 开闸；支付后闸机未开时可凭同一回执重试，不重复收费
export function payAndOpen({ receiptId, requestId = cid('payreq_'), gateId = 'GATE-01' }, db = getDb()) {
  const receipt = db.prepare('SELECT * FROM exit_receipts WHERE id = ?').get(receiptId);
  if (!receipt) throw new AppError('RECEIPT_NOT_FOUND', '收费回执不存在');

  if (receipt.status === 'paid') {
    // 已支付：只重试开闸（处置“支付后闸机未开”）
    const gate = commandGateOpen({ gateId, plate: null, context: `支付成功后重试开闸：${receiptId}`, db });
    return { retried: true, receipt, payment: null, gate };
  }
  if (receipt.status === 'free_gate') {
    const gate = commandGateOpen({ gateId, plate: null, context: `免费回执重试开闸：${receiptId}`, db });
    return { retried: true, receipt, payment: null, gate };
  }

  const payment = payAdapter({ receiptId, amountCents: receipt.amount_cents, requestId, db });
  db.prepare("UPDATE exit_receipts SET status = 'paid' WHERE id = ?").run(receiptId);
  const gate = commandGateOpen({ gateId, plate: null, context: `收费 ${receipt.amount_cents} 分后开闸：支付 ${payment.providerRef}`, db });
  return {
    retried: false,
    receipt: db.prepare('SELECT * FROM exit_receipts WHERE id = ?').get(receiptId),
    payment, gate,
  };
}

// ---------- 异常放行 ----------
export function recordException({ plate = null, invitationId = null, reasonCode, direction,
                                  gateId = 'GATE-01', requestedBy, note = '', amountCents = 0,
                                  forceStatus = null }, db = getDb()) {
  if (!REASON_TEXTS[reasonCode]) throw new AppError('BAD_REASON', '未知异常理由');
  const id = cid('ex_');
  const status = forceStatus || 'pending';
  db.prepare(`INSERT INTO exception_releases
    (id, plate, invitation_id, reason_code, reason_text, direction, gate_id, requested_by, status, amount_cents, created_at, review_note, note)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`)
    .run(id, plate ? normalizePlate(plate) : null, invitationId, reasonCode, REASON_TEXTS[reasonCode],
      direction, gateId, requestedBy, status, amountCents, nowIso(),
      status === 'rejected' ? note : null, note);
  return db.prepare('SELECT * FROM exception_releases WHERE id = ?').get(id);
}

// 园区管理员复核：通过后按方向执行放行（免费放行金额归零）
export function reviewException(exceptionId, adminId, approve, reviewNote = '', db = getDb()) {
  const admin = db.prepare('SELECT * FROM admins WHERE id = ?').get(adminId);
  if (!admin) throw new AppError('ADMIN_NOT_FOUND', '复核人必须是园区管理员');
  const ex = db.prepare('SELECT * FROM exception_releases WHERE id = ?').get(exceptionId);
  if (!ex) throw new AppError('EX_NOT_FOUND', '异常放行记录不存在');
  if (ex.status !== 'pending') throw new AppError('EX_REVIEWED', `该异常已复核：${ex.status}`);

  if (!approve) {
    db.prepare("UPDATE exception_releases SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE id = ?")
      .run(adminId, nowIso(), reviewNote || '管理员驳回', ex.id);
    return db.prepare('SELECT * FROM exception_releases WHERE id = ?').get(ex.id);
  }

  const inv = ex.invitation_id ? getInvitation(ex.invitation_id, db) : null;
  const { entry, exit } = inv ? getEvents(inv.id, db) : { entry: null, exit: null };
  let receipt = null;

  withTx(db, () => {
    if (ex.direction === 'entry' && inv && !entry) {
      // 批准入场异常（车牌录错/提前超宽限/时段失效）：补入场事件并开闸
      const evId = cid('ev_');
      const photo = captureMaskedPhoto({ plate: ex.plate || inv.plate, gateId: ex.gate_id, atIso: nowIso() });
      db.prepare(`INSERT INTO events (id, invitation_id, plate, event_type, happened_at, recorded_at, source, gate_id, photo_path, note)
                  VALUES (?,?,?, 'entry', ?, ?, 'online', ?, ?, ?)`)
        .run(evId, inv.id, inv.plate, nowIso(), nowIso(), ex.gate_id, photo,
          `异常入场：${REASON_TEXTS[ex.reason_code]}`);
      db.prepare("UPDATE invitations SET status = 'used', updated_at = ? WHERE id = ?").run(nowIso(), inv.id);
    } else if (ex.direction === 'exit' && inv && entry && !exit) {
      // 免费放行复核通过：按零元落出场回执，开闸
      const detail = calcCharge({
        enteredAt: entry.happened_at, exitedAt: nowIso(),
        plannedArrival: inv.planned_arrival, plannedDeparture: inv.planned_departure,
      });
      detail.waiver = true;
      detail.waiverReason = REASON_TEXTS[ex.reason_code];
      const evId = cid('ev_');
      const photo = captureMaskedPhoto({ plate: inv.plate, gateId: ex.gate_id, atIso: nowIso() });
      db.prepare(`INSERT INTO events (id, invitation_id, plate, event_type, happened_at, recorded_at, source, gate_id, photo_path, note)
                  VALUES (?,?,?, 'exit', ?, ?, 'online', ?, ?, ?)`)
        .run(evId, inv.id, inv.plate, nowIso(), nowIso(), ex.gate_id, photo, `异常免费出场：${REASON_TEXTS[ex.reason_code]}`);
      db.prepare(`INSERT INTO exit_receipts
        (id, request_id, invitation_id, event_id, entered_at, exited_at, approved_end,
         free_minutes, overtime_minutes, amount_cents, calc_detail, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?, 'free_gate', ?)`)
        .run(cid('rc_'), `exception-${ex.id}`, inv.id, evId, detail.enteredAt, detail.exitedAt,
          detail.plannedDeparture, detail.totalMinutes, detail.overtimeMinutes, 0,
          JSON.stringify(detail), nowIso());
      receipt = db.prepare('SELECT * FROM exit_receipts WHERE event_id = ?').get(evId);
    }
    db.prepare("UPDATE exception_releases SET status = 'approved', reviewed_by = ?, reviewed_at = ?, review_note = ?, amount_cents = ? WHERE id = ?")
      .run(adminId, nowIso(), reviewNote || '管理员核准免费放行', 0, ex.id);
  });

  const gate = commandGateOpen({
    gateId: ex.gate_id || 'GATE-01',
    plate: ex.plate || inv?.plate || null,
    context: `异常放行经园区管理员 ${admin.name} 复核通过：${REASON_TEXTS[ex.reason_code]}`,
    db,
  });

  return {
    exception: db.prepare('SELECT * FROM exception_releases WHERE id = ?').get(ex.id),
    receipt, gate,
  };
}

// ---------- 离线队列与重放 ----------
export function queueOfflineEvent(payload, db = getDb()) {
  // 门岗离线：事件先落本地队列（event_id 预先分配，重放天然去重）
  const eventId = payload.eventId || cid('ev_');
  const id = cid('oq_');
  db.prepare(`INSERT INTO offline_queue (id, event_id, payload, status, created_at)
              VALUES (?, ?, ?, 'queued', ?)`)
    .run(id, eventId, JSON.stringify({ ...payload, eventId }), nowIso());
  return { id, eventId };
}

export function replayOffline(db = getDb()) {
  const queued = db.prepare("SELECT * FROM offline_queue WHERE status = 'queued' ORDER BY created_at").all();
  const results = [];
  for (const item of queued) {
    const p = JSON.parse(item.payload);
    try {
      let out;
      if (p.kind === 'entry') {
        out = admitEntry({
          code: p.code, observedPlate: p.observedPlate, gateId: p.gateId,
          at: p.at, eventId: item.event_id, source: 'offline_replay', note: '离线恢复后补录',
        }, db);
      } else if (p.kind === 'exit') {
        out = processExit({
          code: p.code, plate: p.plate, gateId: p.gateId, at: p.at,
          requestId: p.requestId || `offline-${item.event_id}`, source: 'offline_replay',
        }, db);
      } else {
        throw new Error('未知离线事件类型');
      }
      db.prepare("UPDATE offline_queue SET status = 'replayed', replayed_at = ? WHERE id = ?").run(nowIso(), item.id);
      results.push({ id: item.id, ok: true, duplicated: Boolean(out.duplicated) });
    } catch (err) {
      // 重放失败（邀请已用/已取消/车牌不符）：拦截并留处置记录，绝不重复入场
      db.prepare("UPDATE offline_queue SET status = 'failed', replayed_at = ? WHERE id = ?").run(nowIso(), item.id);
      recordException({
        plate: p.observedPlate || p.plate || null,
        reasonCode: 'OFFLINE_REPLAY_REJECTED',
        direction: p.kind === 'exit' ? 'exit' : 'entry',
        gateId: p.gateId, requestedBy: '离线重放任务',
        note: `离线事件 ${item.event_id} 重放被拦截：${err.message}`,
        forceStatus: 'rejected',
      }, db);
      results.push({ id: item.id, ok: false, error: err.message, code: err.code });
    }
  }
  return results;
}

// ---------- 列表 ----------
export function listInPark(db = getDb()) {
  return db.prepare(`
    SELECT i.id, i.code, i.plate, i.building, i.contact_name, i.contact_phone,
           u.name AS unit_name, i.planned_arrival, i.planned_departure,
           e.happened_at AS entered_at, e.gate_id, e.photo_path
    FROM events e
    JOIN invitations i ON i.id = e.invitation_id
    JOIN units u ON u.id = i.unit_id
    WHERE e.event_type = 'entry'
      AND NOT EXISTS (SELECT 1 FROM events x WHERE x.invitation_id = e.invitation_id AND x.event_type = 'exit')
    ORDER BY e.happened_at`).all();
}

export function listReceipts(db = getDb()) {
  return db.prepare(`
    SELECT r.*, i.plate, i.code AS code_hint, i.building, u.name AS unit_name
    FROM exit_receipts r
    JOIN invitations i ON i.id = r.invitation_id
    JOIN units u ON u.id = i.unit_id
    ORDER BY r.created_at DESC`).all();
}

export function listExceptions(status = null, db = getDb()) {
  const sql = `SELECT ex.*, a.name AS reviewed_by_name FROM exception_releases ex
               LEFT JOIN admins a ON a.id = ex.reviewed_by`;
  return status
    ? db.prepare(`${sql} WHERE ex.status = ? ORDER BY ex.created_at DESC`).all(status)
    : db.prepare(`${sql} ORDER BY ex.created_at DESC`).all();
}

export function listPayments(db = getDb()) {
  return db.prepare(`
    SELECT p.*, i.plate FROM payments p
    JOIN exit_receipts r ON r.id = p.receipt_id
    JOIN invitations i ON i.id = r.invitation_id
    ORDER BY p.created_at DESC`).all();
}

export function listGateLogs(limit = 30, db = getDb()) {
  return db.prepare('SELECT * FROM gate_logs ORDER BY created_at DESC LIMIT ?').all(limit);
}

export function listOfflineQueue(db = getDb()) {
  return db.prepare('SELECT * FROM offline_queue ORDER BY created_at DESC').all();
}

export { PRICING, REASON_TEXTS };
